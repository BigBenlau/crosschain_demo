"""協議註冊表定義。

本檔負責：
- 定義協議配置資料結構 `ProtocolConfig`
- 從配置讀取 LayerZero / Wormhole 的 topic0 與階段映射
- 從配置讀取各鏈協議合約地址與 Wormhole sender 過濾清單
"""

from dataclasses import dataclass

from app.config import Settings


LAYERZERO_LEGACY_TOPIC_STAGE_MAP = {
    "0x1ab700d4ced0c005b164c0f789fd09fcbb0156d4c2041b8a3bfbcd961cd1567f": "SENT",
    "0x0d87345f3d1c929caba93e1c3821b54ff3512e12b66aa3cfe54b6bcbc17e59b4": "VERIFIED",
    "0x3cd5e48f9730b129dc7550f0fcea9c767b7be37837cd10e55eb35f734f4bca04": "EXECUTED",
    "0x7edfa10fe10193301ad8a8bea7e968c7bcabcc64981f368e3aeada40ce26ae2c": "FAILED",
}

WORMHOLE_LEGACY_TOPIC_STAGE_MAP = {
    "0x6eb224fb001ed210e379b335e35efe88672a8ce935d981a6896b27ffdf52a3b2": "SENT",
    "0xcaf280c8cfeba144da67230d9b009c8f868a75bac9a528fa0474be1ba317c169": "EXECUTED",
}


@dataclass(frozen=True)
class ProtocolConfig:
    """單協議索引配置。"""

    key: str
    stage_by_topic: dict[str, str]
    addresses_by_chain_key: dict[str, list[str]]
    sent_sender_filter_by_chain_key: dict[str, list[str]]

    def topics(self) -> list[str]:
        """輸出協議下所有需要監控的 topic。"""
        return list(self.stage_by_topic.keys())

    def topics_by_stage(self, stage: str) -> list[str]:
        """輸出指定 stage 對應的 topic0 列表。"""
        target_stage = stage.upper()
        return [topic for topic, mapped in self.stage_by_topic.items() if mapped == target_stage]

    def addresses_for_chain(self, chain_key: str) -> list[str]:
        """輸出指定鏈應用的合約地址白名單。"""
        return self.addresses_by_chain_key.get(chain_key.lower(), [])

    def sent_sender_filter_for_chain(self, chain_key: str) -> list[str]:
        """輸出指定鏈在 SENT 事件上的 sender 白名單（僅 Wormhole 使用）。"""
        return self.sent_sender_filter_by_chain_key.get(chain_key.lower(), [])


def _build_stage_map(*items: tuple[str, list[str]]) -> dict[str, str]:
    """由 stage + topics 列表構建 topic 到 stage 的映射。"""
    output: dict[str, str] = {}
    for stage, topics in items:
        for topic in topics:
            output[topic.lower()] = stage
    return output


def _build_stage_map_from_known_topics(topics: list[str], known_stage_map: dict[str, str]) -> dict[str, str]:
    """按已知 topic 簽名回填 stage 映射，用於兼容舊版配置。"""
    output: dict[str, str] = {}
    for topic in topics:
        normalized = topic.lower()
        stage = known_stage_map.get(normalized)
        if stage is None:
            continue
        output[normalized] = stage
    return output


def _normalize_addresses(addresses: list[str]) -> list[str]:
    """將地址列表規範為小寫 0x40 格式並去重。"""
    output: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        value = raw.strip().lower()
        if not value.startswith("0x") or len(value) != 42:
            continue
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _merge_address_lists(*address_lists: list[str]) -> list[str]:
    """合併多組地址並保持去重後順序。"""
    merged: list[str] = []
    seen: set[str] = set()
    for addresses in address_lists:
        for address in addresses:
            if address in seen:
                continue
            seen.add(address)
            merged.append(address)
    return merged


def get_protocol_registry(settings: Settings) -> list[ProtocolConfig]:
    """生成雙協議註冊表。"""
    layerzero_sent = settings.parse_csv(settings.layerzero_sent_topics)
    layerzero_verified = settings.parse_csv(settings.layerzero_verified_topics)
    layerzero_executed = settings.parse_csv(settings.layerzero_executed_topics)
    layerzero_failed = settings.parse_csv(settings.layerzero_failed_topics)
    wormhole_sent = settings.parse_csv(settings.wormhole_sent_topics)
    wormhole_executed = settings.parse_csv(settings.wormhole_executed_topics)
    target_chain_key = settings.target_chain.lower()

    # 兼容舊配置：若未配置 stage topics，按已知簽名回填真實 stage。
    if not any([layerzero_sent, layerzero_verified, layerzero_executed, layerzero_failed]):
        legacy_stage_map = _build_stage_map_from_known_topics(
            settings.parse_csv(settings.layerzero_topic0s),
            LAYERZERO_LEGACY_TOPIC_STAGE_MAP,
        )
        layerzero_sent = [topic for topic, stage in legacy_stage_map.items() if stage == "SENT"]
        layerzero_verified = [topic for topic, stage in legacy_stage_map.items() if stage == "VERIFIED"]
        layerzero_executed = [topic for topic, stage in legacy_stage_map.items() if stage == "EXECUTED"]
        layerzero_failed = [topic for topic, stage in legacy_stage_map.items() if stage == "FAILED"]
    if not any([wormhole_sent, wormhole_executed]):
        legacy_stage_map = _build_stage_map_from_known_topics(
            settings.parse_csv(settings.wormhole_topic0s),
            WORMHOLE_LEGACY_TOPIC_STAGE_MAP,
        )
        wormhole_sent = [topic for topic, stage in legacy_stage_map.items() if stage == "SENT"]
        wormhole_executed = [topic for topic, stage in legacy_stage_map.items() if stage == "EXECUTED"]

    layerzero_addresses_by_chain = {
        "ethereum": _normalize_addresses(settings.parse_csv(settings.layerzero_ethereum_endpoints)),
        target_chain_key: _normalize_addresses(settings.parse_csv(settings.layerzero_target_endpoints)),
    }
    wormhole_ethereum_core_addresses = _normalize_addresses(settings.parse_csv(settings.wormhole_ethereum_core_contracts))
    wormhole_target_core_addresses = _normalize_addresses(settings.parse_csv(settings.wormhole_target_core_contracts))
    wormhole_ethereum_token_bridge_addresses = _normalize_addresses(
        settings.parse_csv(settings.wormhole_ethereum_token_bridges)
    )
    wormhole_target_token_bridge_addresses = _normalize_addresses(
        settings.parse_csv(settings.wormhole_target_token_bridges)
    )
    wormhole_addresses_by_chain = {
        "ethereum": _merge_address_lists(wormhole_ethereum_core_addresses, wormhole_ethereum_token_bridge_addresses),
        target_chain_key: _merge_address_lists(wormhole_target_core_addresses, wormhole_target_token_bridge_addresses),
    }
    wormhole_sent_sender_filter_by_chain = {
        "ethereum": wormhole_ethereum_token_bridge_addresses,
        target_chain_key: wormhole_target_token_bridge_addresses,
    }

    return [
        ProtocolConfig(
            key="layerzero",
            stage_by_topic=_build_stage_map(
                ("SENT", layerzero_sent),
                ("VERIFIED", layerzero_verified),
                ("EXECUTED", layerzero_executed),
                ("FAILED", layerzero_failed),
            ),
            addresses_by_chain_key=layerzero_addresses_by_chain,
            sent_sender_filter_by_chain_key={},
        ),
        ProtocolConfig(
            key="wormhole",
            stage_by_topic=_build_stage_map(
                ("SENT", wormhole_sent),
                ("EXECUTED", wormhole_executed),
            ),
            addresses_by_chain_key=wormhole_addresses_by_chain,
            sent_sender_filter_by_chain_key=wormhole_sent_sender_filter_by_chain,
        ),
    ]
