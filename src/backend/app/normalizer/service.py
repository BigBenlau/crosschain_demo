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

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RawLog, RiskReport, SearchIndex, XChainTimelineEvent, XChainTx
from app.registry import get_protocol_registry


def _status_rank(status: str) -> int:
    """定義狀態進階順序，用於合併多事件狀態。"""
    rank = {"SENT": 1, "VERIFIED": 2, "EXECUTED": 3}
    return rank.get(status, 0)


class NormalizerService:
    """最小正規化服務，將原始日志聚合為統一交易視圖。"""

    def normalize_all(self, db: Session, dual_chain_pair: tuple[int, int] | None = None) -> list[str]:
        """執行全量正規化，回傳本輪變更的 canonical id 列表。"""
        # 階段 0：前置條件。
        # 僅在已解析出雙邊鏈配對（Ethereum + TARGET_CHAIN）時執行正規化；
        # 若缺少配對資訊，直接返回空集合，避免誤寫入單邊交易。
        if dual_chain_pair is None:
            return []

        # 階段 1：初始化本輪上下文。
        # - protocol_maps：topic0 -> stage 的快速映射
        # - changed_ids：收集本輪有更新的 canonical id
        # - seen_search_keys：同一輪內 search_index 去重
        protocol_maps = {item.key: item.stage_by_topic for item in get_protocol_registry(settings)}
        changed_ids: set[str] = set()
        seen_search_keys: set[tuple[str, str, str]] = set()

        # 階段 2：掃描所有有效 raw logs，先整理為「可用事件」清單。
        # 這一階段只做解析與歸類，不直接寫 xchain_txs，便於後續先做雙邊過濾。
        stmt = select(RawLog).where(RawLog.removed.is_(False)).order_by(RawLog.block_number.asc(), RawLog.id.asc())
        logs = db.execute(stmt).scalars().all()
        staged_entries: list[tuple[RawLog, str, str, dict[str, Any] | None]] = []
        chain_sides: dict[str, dict[str, int | bool | None]] = {}
        for raw in logs:
            entry = self._build_staged_entry(raw, protocol_maps)
            if entry is None:
                continue
            staged_entries.append(entry)
            self._accumulate_chain_sides(chain_sides, entry[2], raw, entry[1], dual_chain_pair, entry[3])

        # 階段 3：計算符合雙邊條件的 canonical id（縮小處理場景）。
        # 只保留：
        # - 同一 canonical id 同時出現 src/dst
        # - 方向可校驗且無衝突
        # - 鏈對為 Ethereum <-> TARGET_CHAIN
        eligible_ids = self._eligible_canonical_ids(chain_sides, dual_chain_pair)
        changed_ids.update(self._prune_ineligible_transactions(db, eligible_ids))
        if not eligible_ids:
            changed_stuck = self._mark_stuck_transactions(db)
            changed_ids.update(changed_stuck)
            db.commit()
            return list(changed_ids)

        # 階段 4：僅針對 eligible canonical id 寫主表、時間線、搜索索引。
        # 這是核心收斂步驟，避免單邊或錯向資料進入 xchain_txs。
        for raw, stage, canonical_id, _decoded in staged_entries:
            if canonical_id not in eligible_ids:
                continue
            tx = db.get(XChainTx, canonical_id)
            if tx is None:
                tx = XChainTx(canonical_id=canonical_id, protocol=raw.protocol, status="SENT")
                db.add(tx)
                db.flush()

            changed = self._apply_stage(tx, raw, stage)
            changed = self._upsert_timeline(db, raw, canonical_id, stage) or changed
            changed = self._upsert_search_index(db, raw, canonical_id, seen_search_keys) or changed
            if changed:
                changed_ids.add(canonical_id)

        # 階段 5：收尾規則與提交。
        # 補標記超時未推進交易為 STUCK，再統一 commit。
        db.flush()
        changed_stuck = self._mark_stuck_transactions(db)
        changed_ids.update(changed_stuck)
        db.commit()
        return list(changed_ids)

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

    def _build_canonical_id(self, raw: RawLog, decoded: dict[str, Any] | None) -> str:
        """生成 canonical id：優先使用解碼結果，失敗時回退 fallback。"""
        if raw.protocol == "layerzero":
            layerzero_id = self._build_layerzero_canonical(decoded)
            if layerzero_id is not None:
                return layerzero_id
        if raw.protocol == "wormhole":
            wormhole_id = self._build_wormhole_canonical(decoded)
            if wormhole_id is not None:
                return wormhole_id

        return self._build_fallback_canonical(raw)

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

    def _build_fallback_canonical(self, raw: RawLog) -> str:
        """使用舊版 fallback 規則生成 canonical id。"""
        if raw.protocol == "layerzero":
            return f"lz:{raw.chain_id}:{raw.tx_hash}:{raw.log_index}"
        if raw.protocol == "wormhole":
            return f"wormhole:{raw.chain_id}:{raw.tx_hash}:{raw.log_index}"
        return f"{raw.protocol}:{raw.chain_id}:{raw.tx_hash}:{raw.log_index}"

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
        canonical_id = self._build_canonical_id(raw, decoded)
        return (raw, stage, canonical_id, decoded)

    def _accumulate_chain_sides(
        self,
        chain_sides: dict[str, dict[str, int | bool | None]],
        canonical_id: str,
        raw: RawLog,
        stage: str,
        dual_chain_pair: tuple[int, int],
        decoded: dict[str, Any] | None,
    ) -> None:
        """累積每個 canonical_id 的 src/dst 鏈資訊。"""
        sides = chain_sides.get(canonical_id)
        if sides is None:
            sides = {"src": None, "dst": None, "conflict": False}
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

    def _eligible_canonical_ids(
        self,
        chain_sides: dict[str, dict[str, int | bool | None]],
        dual_chain_pair: tuple[int, int],
    ) -> set[str]:
        """計算符合 Ethereum + TARGET_CHAIN 雙邊配對的 canonical id 集合。"""
        eth_chain_id, target_chain_id = dual_chain_pair
        output: set[str] = set()
        for canonical_id, sides in chain_sides.items():
            if bool(sides.get("conflict")):
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

            # 先用記憶體集合去重，避免同一輪 session 內重複 add 造成 unique 衝突。
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
                or_(XChainTx.updated_at <= threshold, XChainTx.created_at <= threshold),
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

    def _prune_ineligible_transactions(self, db: Session, eligible_ids: set[str]) -> list[str]:
        """刪除已失去有效鏈上證據的交易及其關聯資料。"""
        stmt = select(XChainTx.canonical_id)
        if eligible_ids:
            stmt = stmt.where(XChainTx.canonical_id.not_in(eligible_ids))
        existing_ids = [row[0] for row in db.execute(stmt).all()]
        if not existing_ids:
            return []

        db.execute(delete(RiskReport).where(RiskReport.canonical_id.in_(existing_ids)))
        db.execute(delete(XChainTimelineEvent).where(XChainTimelineEvent.canonical_id.in_(existing_ids)))
        db.execute(delete(SearchIndex).where(SearchIndex.canonical_id.in_(existing_ids)))
        db.execute(delete(XChainTx).where(XChainTx.canonical_id.in_(existing_ids)))
        return existing_ids


normalizer_service = NormalizerService()
