"""協議事件解碼服務。

本檔負責：
- 對 LayerZero / Wormhole 的關鍵事件做 ABI data decode
- 從 decoded data 提取 canonical join 所需欄位（canonical_hint）
- 輸出 JSON 可序列化的解碼結果，供 `raw_logs.decoded_json` 存檔
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi.exceptions import DecodingError

# ABI 設定檔路徑
DECODER_ABI_DIR = Path(__file__).resolve().parent / "abis"

# LayerZero PacketV1Codec 偏移（來源：LayerZero V2 合約 PacketV1Codec）
LZ_PACKET_VERSION_OFFSET = 0
LZ_PACKET_NONCE_OFFSET = 1
LZ_PACKET_SRC_EID_OFFSET = 9
LZ_PACKET_SENDER_OFFSET = 13
LZ_PACKET_DST_EID_OFFSET = 45
LZ_PACKET_RECEIVER_OFFSET = 49
LZ_PACKET_GUID_OFFSET = 81
LZ_PACKET_MESSAGE_OFFSET = 113

# EVM chain_id -> Wormhole chain id（MVP 使用）
EVM_TO_WORMHOLE_CHAIN_ID: dict[int, int] = {
    1: 2,  # Ethereum
    10: 24,  # Optimism
    56: 4,  # BNB Chain
    137: 5,  # Polygon
    42161: 23,  # Arbitrum
    8453: 30,  # Base
}


def _load_event_specs(file_name: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """從 ABI JSON 載入事件配置，返回 by_topic 與 by_name 索引。"""
    file_path = DECODER_ABI_DIR / file_name
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    events = payload.get("events")
    if not isinstance(events, dict):
        raise ValueError(f"invalid decoder abi file: {file_path}")

    by_topic: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for event_name, config in events.items():
        if not isinstance(config, dict):
            continue
        topic0 = str(config.get("topic0", "")).lower()
        if not topic0:
            continue
        type_options_raw = config.get("data_types_options") or []
        type_options = [list(item) for item in type_options_raw if isinstance(item, list)]
        spec = {
            "event_name": event_name,
            "topic0": topic0,
            "data_types_options": type_options,
        }
        by_topic[topic0] = spec
        by_name[event_name] = spec
    return by_topic, by_name


LAYERZERO_EVENT_BY_TOPIC, LAYERZERO_EVENT_BY_NAME = _load_event_specs("layerzero_events.json")
WORMHOLE_EVENT_BY_TOPIC, WORMHOLE_EVENT_BY_NAME = _load_event_specs("wormhole_events.json")


def _hex_to_bytes(raw_value: str | None) -> bytes:
    """將 0x 字串轉為 bytes，空值時返回空 bytes。"""
    if not raw_value:
        return b""
    normalized = raw_value[2:] if raw_value.startswith("0x") else raw_value
    if len(normalized) % 2 == 1:
        normalized = f"0{normalized}"
    return bytes.fromhex(normalized)


def _normalize_address(value: Any) -> str | None:
    """將 address 值標準化為小寫 0x40 hex 字串。"""
    if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
        return value.lower()
    if isinstance(value, bytes) and len(value) == 20:
        return f"0x{value.hex()}"
    return None


def _bytes32_to_hex(value: bytes) -> str:
    """將 32 bytes 值轉為 0x hex 字串。"""
    return f"0x{value.hex()}"


def _bytes32_to_address(value: bytes) -> str | None:
    """將 bytes32 末 20 bytes 轉為 EVM address（若可表示）。"""
    if len(value) != 32:
        return None
    return f"0x{value[-20:].hex()}"


def _address_to_bytes32_hex(address: str | None) -> str | None:
    """將 EVM address 轉為 bytes32（左側補零）hex。"""
    if not address or not address.startswith("0x") or len(address) != 42:
        return None
    return f"0x{'0' * 24}{address[2:].lower()}"


def _topic_to_int(topic: str | None) -> int | None:
    """將 topic 值轉為整數。"""
    if not topic:
        return None
    normalized = topic[2:] if topic.startswith("0x") else topic
    return int(normalized, 16)


def _topic_to_bytes32(topic: str | None) -> str | None:
    """將 topic 標準化為 bytes32 hex 字串。"""
    if not topic:
        return None
    normalized = topic[2:] if topic.startswith("0x") else topic
    return f"0x{normalized.rjust(64, '0')[-64:]}".lower()


def _topic_to_address(topic: str | None) -> str | None:
    """將 topic 解析為 address（取末 20 bytes）。"""
    raw_bytes = _hex_to_bytes(topic)
    if len(raw_bytes) != 32:
        return None
    return f"0x{raw_bytes[-20:].hex()}"


def _decode_abi(data_hex: str | None, abi_types: list[str]) -> tuple[Any, ...] | None:
    """按 ABI type 列表解碼 data，失敗返回 None。"""
    try:
        return tuple(abi_decode(abi_types, _hex_to_bytes(data_hex)))
    except (DecodingError, ValueError):
        return None


def _decode_abi_by_options(data_hex: str | None, type_options: list[list[str]]) -> tuple[Any, ...] | None:
    """按多組 ABI type 嘗試解碼，直到成功。"""
    for abi_types in type_options:
        decoded = _decode_abi(data_hex, abi_types)
        if decoded is not None:
            return decoded
    return None


def _parse_lz_packet(encoded_packet: bytes) -> dict[str, Any]:
    """按 PacketV1Codec 偏移解析 LayerZero encoded packet。"""
    if len(encoded_packet) < LZ_PACKET_MESSAGE_OFFSET:
        return {"packet_hex": f"0x{encoded_packet.hex()}", "is_partial": True}

    sender_bytes32 = encoded_packet[LZ_PACKET_SENDER_OFFSET:LZ_PACKET_DST_EID_OFFSET]
    receiver_bytes32 = encoded_packet[LZ_PACKET_RECEIVER_OFFSET:LZ_PACKET_GUID_OFFSET]
    guid_bytes32 = encoded_packet[LZ_PACKET_GUID_OFFSET:LZ_PACKET_MESSAGE_OFFSET]

    return {
        "is_partial": False,
        "version": encoded_packet[LZ_PACKET_VERSION_OFFSET],
        "nonce": int.from_bytes(encoded_packet[LZ_PACKET_NONCE_OFFSET:LZ_PACKET_SRC_EID_OFFSET], "big"),
        "src_eid": int.from_bytes(encoded_packet[LZ_PACKET_SRC_EID_OFFSET:LZ_PACKET_SENDER_OFFSET], "big"),
        "sender_bytes32": _bytes32_to_hex(sender_bytes32),
        "sender_address": _bytes32_to_address(sender_bytes32),
        "dst_eid": int.from_bytes(encoded_packet[LZ_PACKET_DST_EID_OFFSET:LZ_PACKET_RECEIVER_OFFSET], "big"),
        "receiver_bytes32": _bytes32_to_hex(receiver_bytes32),
        "receiver_address": _bytes32_to_address(receiver_bytes32),
        "guid": _bytes32_to_hex(guid_bytes32),
        "message_hex": f"0x{encoded_packet[LZ_PACKET_MESSAGE_OFFSET:].hex()}",
    }


def _parse_wormhole_token_bridge_payload(payload: bytes) -> dict[str, Any]:
    """解讀 Wormhole TokenBridge payload 的關鍵欄位（MVP 子集）。"""
    if not payload:
        return {}

    payload_id = payload[0]
    output: dict[str, Any] = {"payload_id": payload_id}

    # Transfer (1) / TransferWithPayload (3) 都可在固定偏移取到 toChain。
    if payload_id in {1, 3} and len(payload) >= 101:
        output["amount"] = int.from_bytes(payload[1:33], "big")
        output["token_address"] = _bytes32_to_hex(payload[33:65])
        output["token_chain"] = int.from_bytes(payload[65:67], "big")
        output["to_address"] = _bytes32_to_hex(payload[67:99])
        output["to_chain"] = int.from_bytes(payload[99:101], "big")
    return output


def _decode_layerzero(topic0: str, topics: list[str], data_hex: str | None) -> dict[str, Any] | None:
    """解碼 LayerZero 事件，輸出 canonical join 所需欄位。"""
    event = LAYERZERO_EVENT_BY_TOPIC.get(topic0)
    if event is None:
        return None
    event_name = event["event_name"]

    if event_name == "PacketSent":
        decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
        if decoded is None:
            return None
        encoded_packet, options, send_library = decoded
        packet_fields = _parse_lz_packet(encoded_packet)
        guid = packet_fields.get("guid")
        src_eid = packet_fields.get("src_eid")
        sender = packet_fields.get("sender_address")
        nonce = packet_fields.get("nonce")
        canonical_hint = {
            "guid": guid.lower() if isinstance(guid, str) else None,
            "src_eid": src_eid,
            "sender": sender,
            "nonce": nonce,
            "dst_eid": packet_fields.get("dst_eid"),
            "receiver": packet_fields.get("receiver_address"),
        }
        return {
            "event_name": "PacketSent",
            "canonical_hint": canonical_hint,
            "direction": {
                "src_eid": packet_fields.get("src_eid"),
                "dst_eid": packet_fields.get("dst_eid"),
            },
            "decoded": {
                "packet": packet_fields,
                "options_hex": f"0x{options.hex()}",
                "send_library": _normalize_address(send_library),
            },
        }

    if event_name == "PacketVerified":
        decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
        if decoded is None:
            return None
        origin, receiver, payload_hash = decoded
        src_eid, sender_bytes32, nonce = origin
        return {
            "event_name": "PacketVerified",
            "canonical_hint": {
                "guid": None,
                "src_eid": int(src_eid),
                "sender": _bytes32_to_address(sender_bytes32),
                "nonce": int(nonce),
            },
            "decoded": {
                "origin": {
                    "src_eid": int(src_eid),
                    "sender_bytes32": _bytes32_to_hex(sender_bytes32),
                    "sender_address": _bytes32_to_address(sender_bytes32),
                    "nonce": int(nonce),
                },
                "receiver": _normalize_address(receiver),
                "payload_hash": _bytes32_to_hex(payload_hash),
            },
        }

    if event_name == "PacketDelivered":
        decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
        if decoded is None:
            return None
        origin, receiver = decoded
        src_eid, sender_bytes32, nonce = origin
        return {
            "event_name": "PacketDelivered",
            "canonical_hint": {
                "guid": None,
                "src_eid": int(src_eid),
                "sender": _bytes32_to_address(sender_bytes32),
                "nonce": int(nonce),
            },
            "decoded": {
                "origin": {
                    "src_eid": int(src_eid),
                    "sender_bytes32": _bytes32_to_hex(sender_bytes32),
                    "sender_address": _bytes32_to_address(sender_bytes32),
                    "nonce": int(nonce),
                },
                "receiver": _normalize_address(receiver),
            },
        }

    if event_name == "LzReceiveAlert":
        decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
        if decoded is None:
            return None
        origin, guid, gas_value, value_value, message, extra_data, reason = decoded
        src_eid, sender_bytes32, nonce = origin
        receiver = _topic_to_address(topics[1]) if len(topics) > 1 else None
        executor = _topic_to_address(topics[2]) if len(topics) > 2 else None
        return {
            "event_name": "LzReceiveAlert",
            "canonical_hint": {
                "guid": _bytes32_to_hex(guid),
                "src_eid": int(src_eid),
                "sender": _bytes32_to_address(sender_bytes32),
                "nonce": int(nonce),
            },
            "decoded": {
                "receiver": receiver,
                "executor": executor,
                "origin": {
                    "src_eid": int(src_eid),
                    "sender_bytes32": _bytes32_to_hex(sender_bytes32),
                    "sender_address": _bytes32_to_address(sender_bytes32),
                    "nonce": int(nonce),
                },
                "guid": _bytes32_to_hex(guid),
                "gas": int(gas_value),
                "value": int(value_value),
                "message_hex": f"0x{message.hex()}",
                "extra_data_hex": f"0x{extra_data.hex()}",
                "reason_hex": f"0x{reason.hex()}",
            },
        }

    return None


def _decode_wormhole(chain_id: int, topic0: str, topics: list[str], data_hex: str | None) -> dict[str, Any] | None:
    """解碼 Wormhole 事件，輸出 canonical join 所需欄位。"""
    event = WORMHOLE_EVENT_BY_TOPIC.get(topic0)
    if event is None:
        return None
    event_name = event["event_name"]

    if event_name == "LogMessagePublished":
        decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
        sender_address = _topic_to_address(topics[1]) if len(topics) > 1 else None
        sender_bytes32 = _topic_to_bytes32(topics[1]) if len(topics) > 1 else None

        if decoded is None:
            return None

        if len(decoded) == 5:
            sender_address = _normalize_address(decoded[0])
            sender_bytes32 = _address_to_bytes32_hex(sender_address)
            sequence, nonce, payload_bytes, consistency_level = decoded[1], decoded[2], decoded[3], decoded[4]
        else:
            sequence, nonce, payload_bytes, consistency_level = decoded

        emitter_chain_id = EVM_TO_WORMHOLE_CHAIN_ID.get(chain_id)
        payload_meta = _parse_wormhole_token_bridge_payload(payload_bytes)
        return {
            "event_name": "LogMessagePublished",
            "canonical_hint": {
                "emitter_chain_id": emitter_chain_id,
                "emitter_address": sender_bytes32,
                "sequence": int(sequence),
            },
            "direction": {
                "src_chain_id": chain_id,
                "dst_wormhole_chain_id": payload_meta.get("to_chain"),
            },
            "decoded": {
                "sender": sender_address,
                "sender_bytes32": sender_bytes32,
                "sequence": int(sequence),
                "nonce": int(nonce),
                "consistency_level": int(consistency_level),
                "payload_hex": f"0x{payload_bytes.hex()}",
                "payload_meta": payload_meta,
            },
        }

    if event_name == "TransferRedeemed":
        if len(topics) >= 4:
            emitter_chain_id = _topic_to_int(topics[1])
            emitter_address = _topic_to_bytes32(topics[2])
            sequence = _topic_to_int(topics[3])
        else:
            decoded = _decode_abi_by_options(data_hex, event["data_types_options"])
            if decoded is None:
                return None
            emitter_chain_id, emitter_address_bytes, sequence = decoded
            emitter_address = _bytes32_to_hex(emitter_address_bytes)

        return {
            "event_name": "TransferRedeemed",
            "canonical_hint": {
                "emitter_chain_id": int(emitter_chain_id) if emitter_chain_id is not None else None,
                "emitter_address": emitter_address,
                "sequence": int(sequence) if sequence is not None else None,
            },
            "direction": {
                "src_wormhole_chain_id": int(emitter_chain_id) if emitter_chain_id is not None else None,
                "dst_chain_id": chain_id,
            },
            "decoded": {
                "emitter_chain_id": int(emitter_chain_id) if emitter_chain_id is not None else None,
                "emitter_address": emitter_address,
                "sequence": int(sequence) if sequence is not None else None,
            },
        }

    return None


def _to_json_friendly(value: Any) -> Any:
    """將 nested 值轉為可 JSON 序列化結構。"""
    if isinstance(value, bytes):
        return f"0x{value.hex()}"
    if isinstance(value, list):
        return [_to_json_friendly(item) for item in value]
    if isinstance(value, tuple):
        return [_to_json_friendly(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_friendly(item) for key, item in value.items()}
    return value


def decode_log(protocol: str, chain_id: int, topic0: str | None, topics: list[str], data_hex: str | None) -> dict[str, Any] | None:
    """對單筆 raw log 進行協議級解碼。"""
    if topic0 is None:
        return None
    normalized_topic0 = topic0.lower()

    if protocol == "layerzero":
        decoded = _decode_layerzero(normalized_topic0, topics, data_hex)
    elif protocol == "wormhole":
        decoded = _decode_wormhole(chain_id, normalized_topic0, topics, data_hex)
    else:
        decoded = None

    if decoded is None:
        return None

    decoded["protocol"] = protocol
    decoded["chain_id"] = chain_id
    decoded["topic0"] = normalized_topic0
    return _to_json_friendly(decoded)
