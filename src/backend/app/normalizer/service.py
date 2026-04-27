"""跨鏈交易正規化服務。

本檔負責：
- 將 `raw_logs` 映射為統一交易主表 `xchain_txs`
- 建立時間線事件 `xchain_timeline_events`
- 建立搜尋索引 `search_index`
- 根據超時規則標記 `STUCK` 及 failure category
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import NormalizationTask, RawLog, RiskReport, SearchIndex, XChainTimelineEvent, XChainTx
from app.registry import get_protocol_registry


def _status_rank(status: str) -> int:
    """定義狀態進階順序，用於合併多事件狀態。"""
    rank = {"SENT": 1, "VERIFIED": 2, "EXECUTED": 3}
    return rank.get(status, 0)


class NormalizerService:
    """最小正規化服務，將原始日志聚合為統一交易視圖。"""

    def normalize_changed(
        self,
        db: Session,
        changed_canonical_ids: set[str] | list[str] | None,
        dual_chain_pair: tuple[int, int] | None = None,
    ) -> list[str]:
        """按本輪受影響 canonical id 增量重建交易視圖。"""
        if dual_chain_pair is None:
            return []

        protocol_maps = self._build_protocol_maps()
        changed_ids: set[str] = set(changed_canonical_ids or [])

        # 首輪部署後，舊 raw_logs 仍然沒有 canonical_id。
        # 先回填再增量重建，避免後續只拿到半邊事件而破壞收斂結果。
        changed_ids.update(self._backfill_raw_log_canonical_ids(db))
        self.enqueue_canonical_ids(db, changed_ids)
        db.flush()

        rebuilt_ids: set[str] = set()
        pending_ids = self._load_pending_canonical_ids(db)
        for canonical_id in pending_ids:
            if self._rebuild_canonical(db, canonical_id, dual_chain_pair, protocol_maps):
                rebuilt_ids.add(canonical_id)
            self._clear_pending_canonical_id(db, canonical_id)

        db.flush()
        rebuilt_ids.update(self._mark_stuck_transactions(db))
        db.commit()
        return list(rebuilt_ids)

    def _build_protocol_maps(self) -> dict[str, dict[str, str]]:
        """構建協議 topic0 -> stage 的快速映射。"""
        return {item.key: item.stage_by_topic for item in get_protocol_registry(settings)}

    def enqueue_canonical_ids(self, db: Session, canonical_ids: set[str] | list[str]) -> None:
        """將待重建 canonical id 持久化到待處理隊列。"""
        for canonical_id in canonical_ids:
            if not canonical_id:
                continue
            task = db.get(NormalizationTask, canonical_id)
            if task is None:
                db.add(NormalizationTask(canonical_id=canonical_id))

    def _load_pending_canonical_ids(self, db: Session) -> list[str]:
        """載入所有待重建 canonical id。"""
        stmt = select(NormalizationTask.canonical_id).order_by(NormalizationTask.updated_at.asc())
        return [row[0] for row in db.execute(stmt).all()]

    def _clear_pending_canonical_id(self, db: Session, canonical_id: str) -> None:
        """在單個 canonical id 重建成功後移除其待處理標記。"""
        db.execute(delete(NormalizationTask).where(NormalizationTask.canonical_id == canonical_id))

    def build_canonical_id_for_values(
        self,
        protocol: str,
        chain_id: int,
        tx_hash: str,
        log_index: int,
        decoded: dict[str, Any] | None,
    ) -> str:
        """從原始欄位生成 canonical id，供 indexer 與 normalizer 共用。"""
        if protocol == "layerzero":
            layerzero_id = self._build_layerzero_canonical(decoded)
            if layerzero_id is not None:
                return layerzero_id
        if protocol == "wormhole":
            wormhole_id = self._build_wormhole_canonical(decoded)
            if wormhole_id is not None:
                return wormhole_id

        if protocol == "layerzero":
            return f"lz:{chain_id}:{tx_hash}:{log_index}"
        if protocol == "wormhole":
            return f"wormhole:{chain_id}:{tx_hash}:{log_index}"
        return f"{protocol}:{chain_id}:{tx_hash}:{log_index}"

    def _backfill_raw_log_canonical_ids(self, db: Session) -> set[str]:
        """為舊資料回填 raw_logs.canonical_id。"""
        stmt = select(RawLog).where(RawLog.canonical_id.is_(None)).order_by(RawLog.id.asc())
        rows = db.execute(stmt).scalars().all()
        changed_ids: set[str] = set()
        for raw in rows:
            decoded = self._load_decoded(raw.decoded_json)
            raw.canonical_id = self.build_canonical_id_for_values(
                protocol=raw.protocol,
                chain_id=raw.chain_id,
                tx_hash=raw.tx_hash,
                log_index=raw.log_index,
                decoded=decoded,
            )
            changed_ids.add(raw.canonical_id)
        return changed_ids

    def _resolve_stage(self, protocol_maps: dict[str, dict[str, str]], protocol: str, topic0: str | None) -> str | None:
        """由協議與 topic0 解析統一階段。"""
        if topic0 is None:
            return None
        stage_map = protocol_maps.get(protocol, {})
        return stage_map.get(topic0.lower())

    def _load_decoded(self, decoded_json: str | None) -> dict[str, Any] | None:
        """讀取 `raw_logs.decoded_json` 並轉為 dict。"""
        if not decoded_json:
            return None
        try:
            parsed = json.loads(decoded_json)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _build_layerzero_canonical(self, decoded: dict[str, Any] | None) -> str | None:
        """按 LayerZero hint 生成 canonical id。"""
        if not decoded:
            return None
        hint = decoded.get("canonical_hint")
        if not isinstance(hint, dict):
            return None

        src_eid = hint.get("src_eid")
        sender = hint.get("sender")
        nonce = hint.get("nonce")
        if isinstance(src_eid, int) and isinstance(nonce, int) and isinstance(sender, str) and sender.startswith("0x"):
            return f"lz:{src_eid}:{sender.lower()}:{nonce}"

        guid = hint.get("guid")
        if isinstance(guid, str) and guid.startswith("0x") and len(guid) == 66:
            return f"lz:{guid.lower()}"
        return None

    def _build_wormhole_canonical(self, decoded: dict[str, Any] | None) -> str | None:
        """按 Wormhole hint 生成 canonical id。"""
        if not decoded:
            return None
        hint = decoded.get("canonical_hint")
        if not isinstance(hint, dict):
            return None

        emitter_chain_id = hint.get("emitter_chain_id")
        emitter_address = hint.get("emitter_address")
        sequence = hint.get("sequence")
        if (
            isinstance(emitter_chain_id, int)
            and isinstance(sequence, int)
            and isinstance(emitter_address, str)
            and emitter_address.startswith("0x")
        ):
            return f"wormhole:{emitter_chain_id}:{emitter_address.lower()}:{sequence}"
        return None

    def _build_staged_entry(
        self,
        raw: RawLog,
        protocol_maps: dict[str, dict[str, str]],
    ) -> tuple[RawLog, str, str, dict[str, Any] | None] | None:
        """構建單筆可用事件（raw, stage, canonical_id）。"""
        stage = self._resolve_stage(protocol_maps, raw.protocol, raw.topic0)
        if stage is None:
            return None
        decoded = self._load_decoded(raw.decoded_json)
        canonical_id = raw.canonical_id or self.build_canonical_id_for_values(
            protocol=raw.protocol,
            chain_id=raw.chain_id,
            tx_hash=raw.tx_hash,
            log_index=raw.log_index,
            decoded=decoded,
        )
        return (raw, stage, canonical_id, decoded)

    def _rebuild_canonical(
        self,
        db: Session,
        canonical_id: str,
        dual_chain_pair: tuple[int, int],
        protocol_maps: dict[str, dict[str, str]],
    ) -> bool:
        """按單個 canonical id 從頭重建主表、timeline 與 search 索引。"""
        stmt = (
            select(RawLog)
            .where(RawLog.canonical_id == canonical_id, RawLog.removed.is_(False))
            .order_by(RawLog.block_number.asc(), RawLog.id.asc())
        )
        logs = db.execute(stmt).scalars().all()

        staged_entries: list[tuple[RawLog, str, str, dict[str, Any] | None]] = []
        chain_sides: dict[str, dict[str, int | bool | None]] = {}
        for raw in logs:
            entry = self._build_staged_entry(raw, protocol_maps)
            if entry is None:
                continue
            staged_entries.append(entry)
            self._accumulate_chain_sides(chain_sides, canonical_id, raw, entry[1], dual_chain_pair, entry[3])

        eligible_ids = self._eligible_canonical_ids(chain_sides, dual_chain_pair)
        if canonical_id not in eligible_ids or not staged_entries:
            return self._prune_canonical_transaction(db, canonical_id)

        tx = db.get(XChainTx, canonical_id)
        if tx is None:
            tx = XChainTx(canonical_id=canonical_id, protocol=staged_entries[0][0].protocol, status="SENT")
            db.add(tx)
            db.flush()

        self._reset_tx(tx, protocol=staged_entries[0][0].protocol)
        db.execute(delete(XChainTimelineEvent).where(XChainTimelineEvent.canonical_id == canonical_id))
        db.execute(delete(SearchIndex).where(SearchIndex.canonical_id == canonical_id))

        seen_search_keys: set[tuple[str, str, str]] = set()
        for raw, stage, _staged_canonical_id, _decoded in staged_entries:
            self._apply_stage(tx, raw, stage)
            self._upsert_timeline(db, raw, canonical_id, stage)
            self._upsert_search_index(db, raw, canonical_id, seen_search_keys)

        return True

    def _reset_tx(self, tx: XChainTx, protocol: str) -> None:
        """清空交易主表的派生欄位，便於從 raw logs 完整重建。"""
        tx.protocol = protocol
        tx.src_chain_id = None
        tx.src_tx_hash = None
        tx.src_timestamp = None
        tx.ethereum_block_number = None
        tx.ethereum_log_index = None
        tx.dst_chain_id = None
        tx.dst_tx_hash = None
        tx.dst_timestamp = None
        tx.status = "SENT"
        tx.failure_category = None
        tx.latency_ms_total = None
        tx.latency_ms_verify = None
        tx.latency_ms_execute = None

    def _accumulate_chain_sides(
        self,
        chain_sides: dict[str, dict[str, int | bool | None]],
        canonical_id: str,
        raw: RawLog,
        stage: str,
        dual_chain_pair: tuple[int, int],
        decoded: dict[str, Any] | None,
    ) -> None:
        """累積每個 canonical_id 的方向與雙邊鏈上證據資訊。"""
        sides = chain_sides.get(canonical_id)
        if sides is None:
            sides = {
                "src": None,
                "dst": None,
                "conflict": False,
                "has_src_evidence": False,
                "has_dst_evidence": False,
            }
            chain_sides[canonical_id] = sides

        direction = self._resolve_direction(raw, stage, dual_chain_pair, decoded)
        if direction is None:
            return
        src_chain_id, dst_chain_id = direction

        if sides["src"] is None:
            sides["src"] = src_chain_id
        elif sides["src"] != src_chain_id:
            sides["conflict"] = True

        if sides["dst"] is None:
            sides["dst"] = dst_chain_id
        elif sides["dst"] != dst_chain_id:
            sides["conflict"] = True

        if stage == "SENT" and raw.chain_id == src_chain_id:
            sides["has_src_evidence"] = True
        if stage in {"VERIFIED", "EXECUTED", "FAILED"} and raw.chain_id == dst_chain_id:
            sides["has_dst_evidence"] = True

    def _eligible_canonical_ids(
        self,
        chain_sides: dict[str, dict[str, int | bool | None]],
        dual_chain_pair: tuple[int, int],
    ) -> set[str]:
        """計算可物化為交易主表的 canonical id 集合。"""
        eth_chain_id, target_chain_id = dual_chain_pair
        output: set[str] = set()
        for canonical_id, sides in chain_sides.items():
            if bool(sides.get("conflict")):
                continue
            if not bool(sides.get("has_src_evidence")):
                continue
            src = sides.get("src")
            dst = sides.get("dst")
            if src is None or dst is None:
                continue
            if (src == eth_chain_id and dst == target_chain_id) or (src == target_chain_id and dst == eth_chain_id):
                output.add(canonical_id)
        return output

    def _resolve_direction(
        self,
        raw: RawLog,
        stage: str,
        dual_chain_pair: tuple[int, int],
        decoded: dict[str, Any] | None,
    ) -> tuple[int, int] | None:
        """按協議解碼結果解析單筆事件的跨鏈方向。"""
        if decoded is None:
            return None
        if raw.protocol == "layerzero":
            return self._resolve_layerzero_direction(raw, stage, dual_chain_pair, decoded)
        if raw.protocol == "wormhole":
            return self._resolve_wormhole_direction(raw, stage, dual_chain_pair, decoded)
        return None

    def _resolve_layerzero_direction(
        self,
        raw: RawLog,
        stage: str,
        dual_chain_pair: tuple[int, int],
        decoded: dict[str, Any],
    ) -> tuple[int, int] | None:
        """以 LayerZero EID（src/dst）校驗事件方向。"""
        eth_chain_id, target_chain_id = dual_chain_pair
        eth_eid = settings.layerzero_ethereum_eid
        target_eid = settings.layerzero_target_eid

        if stage == "SENT":
            direction = decoded.get("direction")
            if not isinstance(direction, dict):
                return None
            src_eid = direction.get("src_eid")
            dst_eid = direction.get("dst_eid")
            if not isinstance(src_eid, int) or not isinstance(dst_eid, int):
                return None

            if raw.chain_id == eth_chain_id and src_eid == eth_eid and dst_eid == target_eid:
                return (eth_chain_id, target_chain_id)
            if raw.chain_id == target_chain_id and src_eid == target_eid and dst_eid == eth_eid:
                return (target_chain_id, eth_chain_id)
            return None

        if stage in {"VERIFIED", "EXECUTED", "FAILED"}:
            hint = decoded.get("canonical_hint")
            if not isinstance(hint, dict):
                return None
            src_eid = hint.get("src_eid")
            if not isinstance(src_eid, int):
                return None

            if raw.chain_id == target_chain_id and src_eid == eth_eid:
                return (eth_chain_id, target_chain_id)
            if raw.chain_id == eth_chain_id and src_eid == target_eid:
                return (target_chain_id, eth_chain_id)
        return None

    def _resolve_wormhole_direction(
        self,
        raw: RawLog,
        stage: str,
        dual_chain_pair: tuple[int, int],
        decoded: dict[str, Any],
    ) -> tuple[int, int] | None:
        """以 Wormhole chain id（toChain/emitterChainId）校驗事件方向。"""
        eth_chain_id, target_chain_id = dual_chain_pair
        eth_wormhole_chain_id = settings.wormhole_ethereum_chain_id
        target_wormhole_chain_id = settings.wormhole_target_chain_id

        direction = decoded.get("direction")
        if not isinstance(direction, dict):
            return None

        if stage == "SENT":
            dst_chain = direction.get("dst_wormhole_chain_id")
            if not isinstance(dst_chain, int):
                return None

            if raw.chain_id == eth_chain_id and dst_chain == target_wormhole_chain_id:
                return (eth_chain_id, target_chain_id)
            if raw.chain_id == target_chain_id and dst_chain == eth_wormhole_chain_id:
                return (target_chain_id, eth_chain_id)
            return None

        if stage in {"EXECUTED", "VERIFIED", "FAILED"}:
            src_chain = direction.get("src_wormhole_chain_id")
            if not isinstance(src_chain, int):
                return None

            if raw.chain_id == target_chain_id and src_chain == eth_wormhole_chain_id:
                return (eth_chain_id, target_chain_id)
            if raw.chain_id == eth_chain_id and src_chain == target_wormhole_chain_id:
                return (target_chain_id, eth_chain_id)
        return None

    def _apply_stage(self, tx: XChainTx, raw: RawLog, stage: str) -> bool:
        """根據事件階段更新主交易表。"""
        changed = False
        changed = self._apply_ethereum_order_key(tx, raw) or changed
        if stage == "SENT":
            if tx.src_chain_id is None:
                tx.src_chain_id = raw.chain_id
                changed = True
            if tx.src_tx_hash is None:
                tx.src_tx_hash = raw.tx_hash
                changed = True
            if raw.block_timestamp is not None and (tx.src_timestamp is None or raw.block_timestamp < tx.src_timestamp):
                tx.src_timestamp = raw.block_timestamp
                changed = True
        if stage in {"VERIFIED", "EXECUTED", "FAILED"}:
            if tx.dst_chain_id is None:
                tx.dst_chain_id = raw.chain_id
                changed = True
            if tx.dst_tx_hash is None:
                tx.dst_tx_hash = raw.tx_hash
                changed = True
            if raw.block_timestamp is not None and (tx.dst_timestamp is None or raw.block_timestamp < tx.dst_timestamp):
                tx.dst_timestamp = raw.block_timestamp
                changed = True

        new_status = self._merge_status(tx.status, stage)
        if new_status != tx.status:
            tx.status = new_status
            changed = True

        new_failure_category = self._failure_category_for_stage(stage)
        if tx.failure_category != new_failure_category:
            tx.failure_category = new_failure_category
            changed = True

        return changed

    def _apply_ethereum_order_key(self, tx: XChainTx, raw: RawLog) -> bool:
        """保存 Ethereum 主網側最早事件的位置，供 latest 排序使用。"""
        if raw.chain_id != 1:
            return False

        current_block = tx.ethereum_block_number
        current_log_index = tx.ethereum_log_index
        if current_block is None:
            tx.ethereum_block_number = raw.block_number
            tx.ethereum_log_index = raw.log_index
            return True

        if raw.block_number < current_block:
            tx.ethereum_block_number = raw.block_number
            tx.ethereum_log_index = raw.log_index
            return True

        if raw.block_number == current_block and current_log_index is not None and raw.log_index < current_log_index:
            tx.ethereum_log_index = raw.log_index
            return True

        if raw.block_number == current_block and current_log_index is None:
            tx.ethereum_log_index = raw.log_index
            return True

        return False

    def _merge_status(self, current: str, incoming: str) -> str:
        """合併原狀態與新事件狀態。"""
        if current == "FAILED" or incoming == "FAILED":
            return "FAILED"
        if current == "STUCK" and incoming == "STUCK":
            return "STUCK"
        return incoming if _status_rank(incoming) >= _status_rank(current) else current

    def _failure_category_for_stage(self, stage: str) -> str | None:
        """根據最新狀態清理或設置 failure category。"""
        if stage == "FAILED":
            return "FAILED_DEST_EXECUTION"
        return None

    def _upsert_timeline(self, db: Session, raw: RawLog, canonical_id: str, stage: str) -> bool:
        """插入時間線事件（若已存在則跳過）。"""
        stmt = select(XChainTimelineEvent).where(
            and_(
                XChainTimelineEvent.canonical_id == canonical_id,
                XChainTimelineEvent.chain_id == raw.chain_id,
                XChainTimelineEvent.tx_hash == raw.tx_hash,
                XChainTimelineEvent.log_index == raw.log_index,
                XChainTimelineEvent.stage == stage,
            )
        )
        existing = db.execute(stmt).scalar_one_or_none()
        if existing:
            changed = False
            if existing.event_ts is None and raw.block_timestamp is not None:
                existing.event_ts = raw.block_timestamp
                changed = True
            if existing.evidence_json != raw.data:
                existing.evidence_json = raw.data
                changed = True
            if existing.decoded_json != raw.decoded_json:
                existing.decoded_json = raw.decoded_json
                changed = True
            return changed

        db.add(
            XChainTimelineEvent(
                canonical_id=canonical_id,
                stage=stage,
                chain_id=raw.chain_id,
                tx_hash=raw.tx_hash,
                block_number=raw.block_number,
                log_index=raw.log_index,
                event_name=f"{raw.protocol}:{stage.lower()}",
                event_ts=raw.block_timestamp,
                evidence_json=raw.data,
                decoded_json=raw.decoded_json,
            )
        )
        return True

    def _build_search_entries(self, raw: RawLog, canonical_id: str) -> list[tuple[str, str, str]]:
        """組裝 search index 所需的 key 資料。"""
        return [
            ("canonicalId", canonical_id, "onchain_derived"),
            ("txHash", raw.tx_hash.lower(), "onchain"),
        ]

    def _upsert_search_index(
        self,
        db: Session,
        raw: RawLog,
        canonical_id: str,
        seen_search_keys: set[tuple[str, str, str]],
    ) -> bool:
        """為 canonical id 與 tx hash 建立搜尋索引。"""
        changed = False
        entries = self._build_search_entries(raw, canonical_id)
        for key_type, key_value, source in entries:
            key = (key_type, key_value, canonical_id)
            if key in seen_search_keys:
                continue

            stmt = select(SearchIndex).where(
                and_(
                    SearchIndex.key_type == key_type,
                    SearchIndex.key_value == key_value,
                    SearchIndex.canonical_id == canonical_id,
                )
            )
            exists = db.execute(stmt).scalar_one_or_none()
            if exists:
                seen_search_keys.add(key)
                continue

            db.add(
                SearchIndex(
                    key_type=key_type,
                    key_value=key_value,
                    canonical_id=canonical_id,
                    source=source,
                )
            )
            seen_search_keys.add(key)
            changed = True
        return changed

    def _mark_stuck_transactions(self, db: Session) -> list[str]:
        """將超時未推進的交易標記為 STUCK。"""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=settings.stuck_timeout_minutes)
        stmt = select(XChainTx).where(
            and_(
                XChainTx.status.in_(["SENT", "VERIFIED"]),
                XChainTx.updated_at <= threshold,
            )
        )
        rows = db.execute(stmt).scalars().all()
        changed: list[str] = []
        for tx in rows:
            prev_status = tx.status
            if prev_status == "STUCK":
                continue
            tx.status = "STUCK"
            tx.failure_category = "STUCK_NO_VERIFY" if prev_status == "SENT" else "STUCK_NEED_EXECUTION"
            changed.append(tx.canonical_id)
        return changed

    def _prune_canonical_transaction(self, db: Session, canonical_id: str) -> bool:
        """刪除單個已失去有效鏈上證據的交易及其關聯資料。"""
        tx = db.get(XChainTx, canonical_id)
        has_tx = tx is not None
        db.execute(delete(RiskReport).where(RiskReport.canonical_id == canonical_id))
        db.execute(delete(XChainTimelineEvent).where(XChainTimelineEvent.canonical_id == canonical_id))
        db.execute(delete(SearchIndex).where(SearchIndex.canonical_id == canonical_id))
        if tx is not None:
            db.delete(tx)
        return has_tx


normalizer_service = NormalizerService()
