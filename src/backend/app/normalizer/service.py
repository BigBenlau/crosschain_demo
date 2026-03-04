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

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RawLog, SearchIndex, XChainTimelineEvent, XChainTx
from app.registry import get_protocol_registry


def _status_rank(status: str) -> int:
    """定義狀態進階順序，用於合併多事件狀態。"""
    rank = {"SENT": 1, "VERIFIED": 2, "EXECUTED": 3}
    return rank.get(status, 0)


class NormalizerService:
    """最小正規化服務，將原始日志聚合為統一交易視圖。"""

    def normalize_all(self, db: Session, dual_chain_pair: tuple[int, int] | None = None) -> list[str]:
        """執行全量正規化，回傳本輪變更的 canonical id 列表。"""
        if dual_chain_pair is None:
            return []

        protocol_maps = {item.key: item.stage_by_topic for item in get_protocol_registry(settings)}
        changed_ids: set[str] = set()
        seen_search_keys: set[tuple[str, str, str]] = set()

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

        eligible_ids = self._eligible_canonical_ids(chain_sides, dual_chain_pair)
        if not eligible_ids:
            return []

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
        if stage == "SENT":
            if tx.src_chain_id is None:
                tx.src_chain_id = raw.chain_id
                changed = True
            if tx.src_tx_hash is None:
                tx.src_tx_hash = raw.tx_hash
                changed = True
        if stage in {"VERIFIED", "EXECUTED", "FAILED"}:
            if tx.dst_chain_id is None:
                tx.dst_chain_id = raw.chain_id
                changed = True
            if tx.dst_tx_hash is None:
                tx.dst_tx_hash = raw.tx_hash
                changed = True

        new_status = self._merge_status(tx.status, stage)
        if new_status != tx.status:
            tx.status = new_status
            changed = True

        if stage == "FAILED" and tx.failure_category != "FAILED_DEST_EXECUTION":
            tx.failure_category = "FAILED_DEST_EXECUTION"
            changed = True

        return changed

    def _merge_status(self, current: str, incoming: str) -> str:
        """合併原狀態與新事件狀態。"""
        if current == "FAILED" or incoming == "FAILED":
            return "FAILED"
        if current == "STUCK":
            return "STUCK"
        return incoming if _status_rank(incoming) >= _status_rank(current) else current

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
            return False

        db.add(
            XChainTimelineEvent(
                canonical_id=canonical_id,
                stage=stage,
                chain_id=raw.chain_id,
                tx_hash=raw.tx_hash,
                block_number=raw.block_number,
                log_index=raw.log_index,
                event_name=f"{raw.protocol}:{stage.lower()}",
                event_ts=None,
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
                XChainTx.created_at <= threshold,
            )
        )
        rows = db.execute(stmt).scalars().all()
        changed: list[str] = []
        for tx in rows:
            prev_status = tx.status
            tx.status = "STUCK"
            tx.failure_category = "STUCK_NO_VERIFY" if prev_status == "SENT" else "STUCK_NEED_EXECUTION"
            changed.append(tx.canonical_id)
        return changed


normalizer_service = NormalizerService()
