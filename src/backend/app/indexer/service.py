"""最小可用鏈上索引器服務。

本檔負責：
- 透過 Node RPC 讀取區塊高度與事件 logs
- 按鏈/協議執行 backfill 與增量掃描（輪詢）
- 寫入 `raw_logs` 與 `indexer_cursors`
- 提供索引器運行狀態快照給健康檢查
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.decoder import decode_log
from app.db import SessionLocal
from app.models import IndexerCursor, RawLog
from app.normalizer import normalizer_service
from app.registry import get_chain_registry, get_protocol_registry
from app.risk import risk_service


def _build_indexer_logger() -> logging.Logger:
    """建立 indexer 專用 logger，並將日誌寫入 backend/logs/indexer.log。"""
    logger = logging.getLogger("xchain.indexer")
    if logger.handlers:
        return logger

    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "indexer.log"

    handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = _build_indexer_logger()


def _hex_to_int(value: Any) -> int | None:
    """將 RPC 回傳的數值（hex/int）轉為 Python int。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return None


def _to_hex(value: int) -> str:
    """將區塊高度等整數轉為 RPC 需要的 hex 字串。"""
    return hex(value)


def _format_block_tag(value: Any) -> str:
    """格式化區塊標籤，若為 hex 則同時輸出十進位值。"""
    if isinstance(value, str) and value.startswith("0x"):
        parsed = _hex_to_int(value)
        if parsed is not None:
            return f"{value}({parsed})"
    return str(value)


def _topic_to_address(topic: Any) -> str | None:
    """將 topic[1] 轉為 address（取 bytes32 末 20 bytes）。"""
    if not isinstance(topic, str):
        return None
    normalized = topic[2:] if topic.startswith("0x") else topic
    if len(normalized) != 64:
        return None
    return f"0x{normalized[-40:].lower()}"


def _summarize_rpc_params(method: str, params: list[Any]) -> str:
    """輸出 RPC 參數摘要，避免直接打印完整 payload。"""
    if method == "eth_getLogs" and params and isinstance(params[0], dict):
        log_filter = params[0]
        topics = log_filter.get("topics") or []
        topic_count = len(topics[0]) if topics and isinstance(topics[0], list) else len(topics)
        address_filter = log_filter.get("address")
        address_count = 0
        if isinstance(address_filter, str):
            address_count = 1
        elif isinstance(address_filter, list):
            address_count = len(address_filter)
        return (
            f"from={_format_block_tag(log_filter.get('fromBlock'))} "
            f"to={_format_block_tag(log_filter.get('toBlock'))} "
            f"topic_count={topic_count} "
            f"address_count={address_count}"
        )
    if method in {"eth_blockNumber", "eth_chainId"}:
        return "no_params"
    return f"param_count={len(params)}"


def _summarize_rpc_result(method: str, result: Any) -> str:
    """輸出 RPC 回應摘要。"""
    if method == "eth_getLogs":
        return f"log_count={len(result) if isinstance(result, list) else 0}"
    if isinstance(result, str):
        return f"value={result}"
    return f"type={type(result).__name__}"


def _rpc_call(rpc_url: str, method: str, params: list[Any], chain_key: str) -> Any:
    """執行單次 JSON-RPC 呼叫，並在錯誤時拋出可讀例外。"""
    started_at = time.time()
    endpoint = urlparse(rpc_url).netloc or "unknown"
    logger.info(
        "RPC start: chain=%s endpoint=%s method=%s %s",
        chain_key,
        endpoint,
        method,
        _summarize_rpc_params(method, params),
    )

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logger.error(
            "RPC done: chain=%s endpoint=%s method=%s status=error elapsed_ms=%s error=%s",
            chain_key,
            endpoint,
            method,
            int((time.time() - started_at) * 1000),
            exc,
        )
        raise RuntimeError(f"rpc request failed: {exc}") from exc

    if result.get("error"):
        logger.error(
            "RPC done: chain=%s endpoint=%s method=%s status=rpc_error elapsed_ms=%s error=%s",
            chain_key,
            endpoint,
            method,
            int((time.time() - started_at) * 1000),
            result.get("error"),
        )
        raise RuntimeError(f"rpc error: {result['error']}")

    result_value = result.get("result")
    logger.info(
        "RPC done: chain=%s endpoint=%s method=%s status=ok elapsed_ms=%s %s",
        chain_key,
        endpoint,
        method,
        int((time.time() - started_at) * 1000),
        _summarize_rpc_result(method, result_value),
    )
    return result_value


def _get_safe_head(rpc_url: str, finality_depth: int, chain_key: str) -> int:
    """取得安全區塊高度（latest - finality_depth）。"""
    latest_hex = _rpc_call(rpc_url, "eth_blockNumber", [], chain_key=chain_key)
    latest = _hex_to_int(latest_hex) or 0
    return max(0, latest - finality_depth)


def _get_logs(
    rpc_url: str,
    from_block: int,
    to_block: int,
    topics: list[str],
    chain_key: str,
    addresses: list[str] | None = None,
) -> list[dict]:
    """依區塊範圍與 topic 條件抓取鏈上 logs。"""
    if not topics:
        return []
    log_filter = {
        "fromBlock": _to_hex(from_block),
        "toBlock": _to_hex(to_block),
        "topics": [topics],
    }
    if addresses:
        log_filter["address"] = addresses[0] if len(addresses) == 1 else addresses
    return _rpc_call(rpc_url, "eth_getLogs", [log_filter], chain_key=chain_key) or []


@dataclass
class IndexerSnapshot:
    """索引器健康快照，用於健康檢查輸出。"""

    running: bool
    last_error: str | None
    last_indexed_block_by_chain: dict[str, int]
    poll_seconds: int
    last_cycle_seq: int
    last_changed_ids: list[str]
    last_changed_count: int
    last_risk_updated_count: int


class IndexerService:
    """最小可用索引器服務。

    生命週期：
    - `start()` 啟動背景輪詢
    - `stop()` 停止背景輪詢
    - `run_once()` 執行一輪雙鏈雙協議掃描
    """

    def __init__(self) -> None:
        """初始化索引器狀態與執行緒控制元件。"""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._last_error: str | None = None
        self._last_indexed_block_by_chain: dict[str, int] = {}
        self._last_cycle_seq = 0
        self._last_changed_ids: list[str] = []
        self._last_risk_updated_ids: list[str] = []
        logger.info(
            "Indexer initialized: poll_seconds=%s, chunk_size=%s",
            settings.indexer_poll_seconds,
            settings.indexer_chunk_size,
        )

    def start(self) -> None:
        """啟動背景索引執行緒（若已啟動則略過）。"""
        if self._thread and self._thread.is_alive():
            logger.info("Indexer start skipped: thread already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="xchain-indexer", daemon=True)
        self._thread.start()
        logger.info("Indexer thread started")

    def stop(self) -> None:
        """停止背景索引執行緒。"""
        logger.info("Indexer stopping")
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Indexer stopped")

    def snapshot(self) -> IndexerSnapshot:
        """回傳目前索引器狀態快照。"""
        with self._lock:
            return IndexerSnapshot(
                running=self._running,
                last_error=self._last_error,
                last_indexed_block_by_chain=dict(self._last_indexed_block_by_chain),
                poll_seconds=settings.indexer_poll_seconds,
                last_cycle_seq=self._last_cycle_seq,
                last_changed_ids=list(self._last_changed_ids),
                last_changed_count=len(self._last_changed_ids),
                last_risk_updated_count=len(self._last_risk_updated_ids),
            )

    def _run_loop(self) -> None:
        """背景輪詢主循環：按固定間隔執行掃描。"""
        with self._lock:
            self._running = True

        logger.info("Indexer run loop entered")
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_once()
                    with self._lock:
                        self._last_error = None
                except Exception as exc:
                    logger.exception("Indexer run_once failed: %s", exc)
                    with self._lock:
                        self._last_error = str(exc)
                self._stop_event.wait(settings.indexer_poll_seconds)
        finally:
            with self._lock:
                self._running = False
            logger.info("Indexer run loop exited")

    def run_once(self) -> None:
        """執行一輪全鏈掃描：逐鏈計算 safe head，逐協議拉取 logs。"""
        # 階段 1：初始化本輪上下文與統計計數器。
        # - 載入鏈註冊表（Ethereum + TARGET_CHAIN）
        # - 載入協議註冊表（LayerZero + Wormhole）
        # - 解析雙邊鏈配對，用於後續 normalize 僅保留雙邊交易
        cycle_started = time.time()
        chains = get_chain_registry(settings)
        protocols = get_protocol_registry(settings)
        dual_chain_pair = self._resolve_dual_chain_pair(chains)
        total_chunks = 0
        total_rpc_logs = 0
        total_upserted = 0

        with SessionLocal() as db:
            # 階段 2：逐鏈掃描。
            # 每條鏈先算 safe head，再按協議拉 logs 並寫入 raw_logs。
            for chain in chains:
                # 2.1 鏈級前置檢查：若 RPC 未配置則跳過，避免阻塞全輪。
                if not chain.rpc_url:
                    logger.warning("Skip chain=%s because rpc_url is empty", chain.key)
                    continue

                # 2.2 計算本鏈安全高度（latest - finality_depth），只掃安全區塊。
                safe_head = _get_safe_head(chain.rpc_url, chain.finality_depth, chain_key=chain.key)
                logger.info(
                    "Chain snapshot: chain=%s chain_id=%s safe_head=%s start_block=%s",
                    chain.key,
                    chain.chain_id,
                    safe_head,
                    chain.start_block,
                )

                # 2.3 逐協議掃描：
                # - 使用 topic + address 白名單過濾 RPC logs
                # - Wormhole SENT 事件再套 sender 白名單
                # - 返回 chunk/rpc_logs/upserted 三項統計
                for protocol in protocols:
                    chunks, rpc_logs, upserted = self._scan_protocol(
                        db,
                        chain.chain_id,
                        chain.key,
                        chain.rpc_url,
                        chain.start_block,
                        safe_head,
                        protocol.key,
                        protocol.topics(),
                        protocol.addresses_for_chain(chain.key),
                        protocol.topics_by_stage("SENT"),
                        protocol.sent_sender_filter_for_chain(chain.key),
                    )
                    total_chunks += chunks
                    total_rpc_logs += rpc_logs
                    total_upserted += upserted

                # 2.4 更新健康狀態快照：記錄本鏈最後索引到的 safe head。
                with self._lock:
                    self._last_indexed_block_by_chain[chain.key] = safe_head

            # 階段 3：正規化與風險分析。
            # - normalize：將 raw_logs 聚合為 xchain_txs / timeline / search_index
            # - risk：僅對本輪變更交易執行風險評估
            changed_ids = normalizer_service.normalize_all(db, dual_chain_pair=dual_chain_pair)
            risk_updated = risk_service.analyze_transactions(db, changed_ids)

            # 階段 4：回寫本輪結果快照，供 /api/health 與觀察使用。
            with self._lock:
                self._last_cycle_seq += 1
                self._last_changed_ids = changed_ids
                self._last_risk_updated_ids = risk_updated

            # 階段 5：輸出本輪摘要日志（耗時 + 各項統計）。
            logger.info(
                "Cycle done: chains=%s protocols=%s chunks=%s rpc_logs=%s upserted=%s changed=%s risk_updated=%s elapsed_ms=%s",
                len(chains),
                len(protocols),
                total_chunks,
                total_rpc_logs,
                total_upserted,
                len(changed_ids),
                len(risk_updated),
                int((time.time() - cycle_started) * 1000),
            )

    def _resolve_dual_chain_pair(self, chains: list[Any]) -> tuple[int, int] | None:
        """從當前鏈註冊表解析 (ethereum_chain_id, target_chain_id) 配對。"""
        ethereum_chain_id: int | None = None
        target_chain_id: int | None = None

        for chain in chains:
            if getattr(chain, "key", "").lower() == "ethereum":
                ethereum_chain_id = int(chain.chain_id)
            else:
                target_chain_id = int(chain.chain_id)

        if ethereum_chain_id is None or target_chain_id is None:
            logger.warning("Dual chain pair unresolved: ethereum=%s target=%s", ethereum_chain_id, target_chain_id)
            return None

        return (ethereum_chain_id, target_chain_id)

    def _scan_protocol(
        self,
        db: Session,
        chain_id: int,
        chain_key: str,
        rpc_url: str,
        start_block: int,
        safe_head: int,
        protocol_key: str,
        topics: list[str],
        contract_addresses: list[str],
        sent_topics: list[str],
        sent_sender_filter: list[str],
    ) -> tuple[int, int, int]:
        """掃描單鏈單協議的區塊範圍，並持續推進游標。"""
        # 階段 1：輸入校驗。
        # - 沒有 topic 無法構建事件過濾條件
        # - 沒有協議地址白名單則跳過，避免抓到非目標合約噪音
        if not topics:
            logger.warning("Skip scan: chain=%s protocol=%s has no topics configured", chain_key, protocol_key)
            return (0, 0, 0)
        if not contract_addresses:
            logger.warning("Skip scan: chain=%s protocol=%s has no contract addresses configured", chain_key, protocol_key)
            return (0, 0, 0)

        # 階段 2：定位掃描起點（cursor 續跑）。
        # - 首次掃描用 start_block
        # - 後續掃描從上次 to_block + 1 開始
        cursor = db.get(IndexerCursor, (chain_id, protocol_key))
        next_block = start_block if cursor is None or cursor.to_block is None else cursor.to_block + 1

        # 階段 3：若當前起點已超過 safe head，代表本輪無需掃描。
        if next_block > safe_head:
            return (0, 0, 0)

        # 階段 4：初始化本協議本鏈統計與掃描參數。
        chunk_size = max(1, settings.indexer_chunk_size)
        scanned_chunks = 0
        total_rpc_logs = 0
        total_upserted = 0
        range_from = next_block
        range_to = next_block
        sent_topic_set = {item.lower() for item in sent_topics}
        sent_sender_filter_set = {item.lower() for item in sent_sender_filter}

        # 階段 5：Wormhole SENT sender 過濾提示。
        # 若已配置 SENT topic 但 sender 白名單為空，SENT 事件會被全部跳過。
        if protocol_key == "wormhole" and sent_topic_set and not sent_sender_filter_set:
            logger.warning("Wormhole SENT sender filter empty: chain=%s", chain_key)

        # 階段 6：分塊掃描區塊區間。
        # 每個 chunk 會：
        # - 以 topic + address 查 RPC logs
        # - 執行協議級過濾與 upsert raw_logs
        # - 推進 cursor 並提交，確保中斷可恢復
        while next_block <= safe_head:
            end_block = min(next_block + chunk_size - 1, safe_head)
            logs = _get_logs(
                rpc_url,
                next_block,
                end_block,
                topics,
                chain_key=chain_key,
                addresses=contract_addresses,
            )
            upserted = self._upsert_raw_logs(
                db,
                protocol_key,
                chain_id,
                logs,
                sent_topic_set=sent_topic_set,
                sent_sender_filter_set=sent_sender_filter_set,
            )
            scanned_chunks += 1
            total_rpc_logs += len(logs)
            total_upserted += upserted
            range_to = end_block

            if cursor is None:
                cursor = IndexerCursor(chain_id=chain_id, protocol=protocol_key)

            cursor.from_block = next_block
            cursor.to_block = end_block
            db.add(cursor)
            db.commit()
            next_block = end_block + 1

        # 階段 7：輸出本協議本鏈掃描摘要。
        logger.info(
            "Protocol scan: chain=%s chain_id=%s protocol=%s block_range=%s-%s chunks=%s rpc_logs=%s upserted=%s",
            chain_key,
            chain_id,
            protocol_key,
            range_from,
            range_to,
            scanned_chunks,
            total_rpc_logs,
            total_upserted,
        )
        return (scanned_chunks, total_rpc_logs, total_upserted)

    def _upsert_raw_logs(
        self,
        db: Session,
        protocol_key: str,
        chain_id: int,
        logs: list[dict],
        sent_topic_set: set[str],
        sent_sender_filter_set: set[str],
    ) -> int:
        """將 RPC logs 寫入 `raw_logs`，同位置資料以更新方式去重。"""
        upserted_count = 0
        for entry in logs:
            tx_hash = (entry.get("transactionHash") or "").lower()
            log_index = _hex_to_int(entry.get("logIndex"))
            block_number = _hex_to_int(entry.get("blockNumber"))
            topics = entry.get("topics") or []
            topic0_raw = (topics or [None])[0]
            topic0 = topic0_raw.lower() if isinstance(topic0_raw, str) else topic0_raw
            removed = bool(entry.get("removed", False))

            if self._should_skip_wormhole_sent_log(protocol_key, topic0, topics, sent_topic_set, sent_sender_filter_set):
                continue

            decoded_payload = decode_log(
                protocol=protocol_key,
                chain_id=chain_id,
                topic0=topic0,
                topics=topics,
                data_hex=entry.get("data"),
            )
            decoded_json = json.dumps(decoded_payload, ensure_ascii=False) if decoded_payload else None

            if not tx_hash or log_index is None or block_number is None:
                continue

            stmt = select(RawLog).where(
                RawLog.chain_id == chain_id, RawLog.tx_hash == tx_hash, RawLog.log_index == log_index
            )
            existing = db.execute(stmt).scalar_one_or_none()

            if existing:
                existing.protocol = protocol_key
                existing.block_number = block_number
                existing.topic0 = topic0
                existing.data = entry.get("data")
                existing.decoded_json = decoded_json
                existing.removed = removed
                upserted_count += 1
            else:
                db.add(
                    RawLog(
                        protocol=protocol_key,
                        chain_id=chain_id,
                        block_number=block_number,
                        tx_hash=tx_hash,
                        log_index=log_index,
                        topic0=topic0,
                        data=entry.get("data"),
                        decoded_json=decoded_json,
                        removed=removed,
                    )
                )
                upserted_count += 1
        return upserted_count

    def _should_skip_wormhole_sent_log(
        self,
        protocol_key: str,
        topic0: str | None,
        topics: list[Any],
        sent_topic_set: set[str],
        sent_sender_filter_set: set[str],
    ) -> bool:
        """判斷是否跳過非 TokenBridge sender 的 Wormhole SENT 事件。"""
        if protocol_key != "wormhole":
            return False
        if topic0 is None or topic0 not in sent_topic_set:
            return False
        if not sent_sender_filter_set:
            return True

        sender_topic = topics[1] if len(topics) > 1 else None
        sender_address = _topic_to_address(sender_topic)
        if sender_address is None:
            return True
        return sender_address.lower() not in sent_sender_filter_set


indexer_service = IndexerService()
