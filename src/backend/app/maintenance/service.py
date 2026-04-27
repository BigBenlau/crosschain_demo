"""背景清理與維護服務。"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, run_sqlite_vacuum
from app.logging_utils import build_backend_file_logger
from app.models import MaintenanceState, RawLog, RiskReport, XChainTimelineEvent, XChainTx


logger = build_backend_file_logger("xchain.maintenance", "maintenance.log")

STATE_LAST_VACUUM_AT = "cleanup.last_vacuum_at"
STATE_DELETED_ROWS_SINCE_VACUUM = "cleanup.deleted_rows_since_vacuum"


@dataclass(frozen=True)
class MaintenanceSummary:
    deleted_removed_logs: int
    deleted_executed_logs: int
    archived_failed_txs: int
    should_vacuum: bool
    vacuum_reason: str | None

    @property
    def deleted_rows_total(self) -> int:
        return self.deleted_removed_logs + self.deleted_executed_logs


@dataclass(frozen=True)
class MaintenanceSnapshot:
    running: bool
    last_error: str | None
    last_run_at: str | None
    last_deleted_removed_logs: int
    last_deleted_executed_logs: int
    last_archived_failed_txs: int
    last_vacuum_at: str | None
    last_vacuum_reason: str | None
    archive_dir: str
    poll_seconds: int


class MaintenanceService:
    """週期性清理 raw_logs 並執行維護任務。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_error: str | None = None
        self._last_run_at: str | None = None
        self._last_deleted_removed_logs = 0
        self._last_deleted_executed_logs = 0
        self._last_archived_failed_txs = 0
        self._last_vacuum_at: str | None = None
        self._last_vacuum_reason: str | None = None

    def start(self) -> None:
        """啟動背景清理執行緒。"""
        if not settings.maintenance_enabled:
            logger.info("Maintenance disabled by config")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="xchain-maintenance", daemon=True)
        self._thread.start()
        logger.info(
            "Maintenance worker started: poll_seconds=%s archive_dir=%s",
            settings.maintenance_poll_seconds,
            settings.maintenance_archive_dir,
        )

    def stop(self) -> None:
        """停止背景清理執行緒。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Maintenance worker stopped")

    def snapshot(self) -> MaintenanceSnapshot:
        """輸出當前維護任務快照。"""
        with self._lock:
            return MaintenanceSnapshot(
                running=self._running,
                last_error=self._last_error,
                last_run_at=self._last_run_at,
                last_deleted_removed_logs=self._last_deleted_removed_logs,
                last_deleted_executed_logs=self._last_deleted_executed_logs,
                last_archived_failed_txs=self._last_archived_failed_txs,
                last_vacuum_at=self._last_vacuum_at,
                last_vacuum_reason=self._last_vacuum_reason,
                archive_dir=settings.maintenance_archive_dir,
                poll_seconds=settings.maintenance_poll_seconds,
            )

    def run_once(self) -> None:
        """執行單輪清理。"""
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            summary = self._run_cleanup_cycle(db, now)

        vacuum_ran = False
        vacuum_at: str | None = None
        if summary.should_vacuum:
            run_sqlite_vacuum()
            vacuum_ran = True
            vacuum_at = now.isoformat()
            with SessionLocal() as db:
                self._set_state(db, STATE_LAST_VACUUM_AT, vacuum_at)
                self._set_state(db, STATE_DELETED_ROWS_SINCE_VACUUM, "0")
                db.commit()

        with self._lock:
            self._last_error = None
            self._last_run_at = now.isoformat()
            self._last_deleted_removed_logs = summary.deleted_removed_logs
            self._last_deleted_executed_logs = summary.deleted_executed_logs
            self._last_archived_failed_txs = summary.archived_failed_txs
            if vacuum_ran:
                self._last_vacuum_at = vacuum_at
            self._last_vacuum_reason = summary.vacuum_reason if vacuum_ran else None

        logger.info(
            "Maintenance cycle done: removed_deleted=%s executed_deleted=%s failed_archived=%s vacuum=%s reason=%s",
            summary.deleted_removed_logs,
            summary.deleted_executed_logs,
            summary.archived_failed_txs,
            vacuum_ran,
            summary.vacuum_reason,
        )

    def _run_loop(self) -> None:
        """背景主循環。"""
        with self._lock:
            self._running = True
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_once()
                except Exception as exc:
                    logger.exception("Maintenance cycle failed: %s", exc)
                    with self._lock:
                        self._last_error = str(exc)
                self._stop_event.wait(settings.maintenance_poll_seconds)
        finally:
            with self._lock:
                self._running = False

    def _run_cleanup_cycle(self, db: Session, now: datetime) -> MaintenanceSummary:
        """執行單輪 DB 清理邏輯。"""
        deleted_removed_logs = self._delete_removed_raw_logs(db, now)
        deleted_executed_logs = self._delete_executed_raw_logs(db, now)
        archived_failed_txs = self._archive_failed_transactions(db, now)

        deleted_rows_total = deleted_removed_logs + deleted_executed_logs
        should_vacuum, vacuum_reason = self._record_vacuum_state(db, now, deleted_rows_total)
        db.commit()
        return MaintenanceSummary(
            deleted_removed_logs=deleted_removed_logs,
            deleted_executed_logs=deleted_executed_logs,
            archived_failed_txs=archived_failed_txs,
            should_vacuum=should_vacuum,
            vacuum_reason=vacuum_reason,
        )

    def _delete_removed_raw_logs(self, db: Session, now: datetime) -> int:
        """刪除已 removed 且超過保留期的 raw logs。"""
        threshold = now - timedelta(days=settings.maintenance_removed_retention_days)
        stmt = select(RawLog.id).where(
            RawLog.removed.is_(True),
            func.coalesce(RawLog.updated_at, RawLog.created_at) <= threshold,
        )
        ids = [row[0] for row in db.execute(stmt).all()]
        if not ids:
            return 0
        db.execute(delete(RawLog).where(RawLog.id.in_(ids)))
        return len(ids)

    def _delete_executed_raw_logs(self, db: Session, now: datetime) -> int:
        """刪除已 EXECUTED 且超過保留期的交易對應 raw logs。"""
        threshold = now - timedelta(days=settings.maintenance_executed_retention_days)
        stmt = (
            select(RawLog.id)
            .join(XChainTx, RawLog.canonical_id == XChainTx.canonical_id)
            .where(
                XChainTx.status == "EXECUTED",
                XChainTx.updated_at <= threshold,
            )
        )
        ids = [row[0] for row in db.execute(stmt).all()]
        if not ids:
            return 0
        db.execute(delete(RawLog).where(RawLog.id.in_(ids)))
        return len(ids)

    def _archive_failed_transactions(self, db: Session, now: datetime) -> int:
        """將 오래된 FAILED 交易導出為壓縮歸檔文件，但不刪資料。"""
        threshold = now - timedelta(days=settings.maintenance_failed_archive_retention_days)
        stmt = select(XChainTx).where(XChainTx.status == "FAILED", XChainTx.updated_at <= threshold)
        txs = db.execute(stmt).scalars().all()
        archived = 0
        for tx in txs:
            archive_path = self._archive_path_for_canonical(tx.canonical_id)
            if archive_path.exists():
                continue
            payload = self._build_failed_archive_payload(db, tx, now)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(archive_path, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            archived += 1
        return archived

    def _build_failed_archive_payload(self, db: Session, tx: XChainTx, now: datetime) -> dict:
        """構建 FAILED 交易歸檔內容。"""
        raw_logs = db.execute(
            select(RawLog).where(RawLog.canonical_id == tx.canonical_id).order_by(RawLog.block_number.asc(), RawLog.id.asc())
        ).scalars().all()
        timeline_rows = db.execute(
            select(XChainTimelineEvent)
            .where(XChainTimelineEvent.canonical_id == tx.canonical_id)
            .order_by(XChainTimelineEvent.id.asc())
        ).scalars().all()
        risk_rows = db.execute(
            select(RiskReport)
            .where(RiskReport.canonical_id == tx.canonical_id)
            .order_by(RiskReport.analyzed_at.asc(), RiskReport.id.asc())
        ).scalars().all()
        return {
            "archivedAt": now.isoformat(),
            "canonicalId": tx.canonical_id,
            "tx": {
                "protocol": tx.protocol,
                "status": tx.status,
                "failureCategory": tx.failure_category,
                "srcChainId": tx.src_chain_id,
                "dstChainId": tx.dst_chain_id,
                "srcTxHash": tx.src_tx_hash,
                "dstTxHash": tx.dst_tx_hash,
                "createdAt": tx.created_at.isoformat() if tx.created_at else None,
                "updatedAt": tx.updated_at.isoformat() if tx.updated_at else None,
            },
            "rawLogs": [
                {
                    "id": row.id,
                    "protocol": row.protocol,
                    "chainId": row.chain_id,
                    "blockNumber": row.block_number,
                    "canonicalId": row.canonical_id,
                    "txHash": row.tx_hash,
                    "logIndex": row.log_index,
                    "blockTimestamp": row.block_timestamp.isoformat() if row.block_timestamp else None,
                    "topic0": row.topic0,
                    "data": row.data,
                    "decodedJson": row.decoded_json,
                    "removed": row.removed,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                    "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in raw_logs
            ],
            "timeline": [
                {
                    "id": row.id,
                    "stage": row.stage,
                    "chainId": row.chain_id,
                    "txHash": row.tx_hash,
                    "blockNumber": row.block_number,
                    "logIndex": row.log_index,
                    "eventName": row.event_name,
                    "eventTs": row.event_ts.isoformat() if row.event_ts else None,
                    "evidenceJson": row.evidence_json,
                    "decodedJson": row.decoded_json,
                }
                for row in timeline_rows
            ],
            "riskReports": [
                {
                    "id": row.id,
                    "verdict": row.verdict,
                    "riskScore": row.risk_score,
                    "riskFactorsJson": row.risk_factors_json,
                    "analysisSummary": row.analysis_summary,
                    "aiModel": row.ai_model,
                    "promptVersion": row.prompt_version,
                    "analyzedAt": row.analyzed_at.isoformat() if row.analyzed_at else None,
                }
                for row in risk_rows
            ],
        }

    def _record_vacuum_state(self, db: Session, now: datetime, deleted_rows: int) -> tuple[bool, str | None]:
        """記錄 VACUUM 狀態，並判定本輪是否需要執行。"""
        accumulated = self._get_state_int(db, STATE_DELETED_ROWS_SINCE_VACUUM) + deleted_rows
        self._set_state(db, STATE_DELETED_ROWS_SINCE_VACUUM, str(accumulated))

        last_vacuum_at = self._get_state_datetime(db, STATE_LAST_VACUUM_AT)
        interval_elapsed = (
            last_vacuum_at is None
            or now - last_vacuum_at >= timedelta(hours=settings.maintenance_vacuum_interval_hours)
        )
        threshold_reached = accumulated >= settings.maintenance_vacuum_min_deleted_rows

        if threshold_reached:
            return (True, f"deleted_rows_since_vacuum={accumulated}")
        if interval_elapsed and accumulated > 0:
            return (True, f"interval_hours={settings.maintenance_vacuum_interval_hours}")
        return (False, None)

    def _archive_path_for_canonical(self, canonical_id: str) -> Path:
        digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()
        archive_root = Path(settings.maintenance_archive_dir)
        return archive_root / "failed" / f"{digest}.json.gz"

    def _get_state(self, db: Session, key: str) -> str | None:
        row = db.get(MaintenanceState, key)
        return row.state_value if row is not None else None

    def _set_state(self, db: Session, key: str, value: str) -> None:
        row = db.get(MaintenanceState, key)
        if row is None:
            db.add(MaintenanceState(state_key=key, state_value=value))
            return
        row.state_value = value
        row.updated_at = datetime.now(timezone.utc)

    def _get_state_int(self, db: Session, key: str) -> int:
        raw = self._get_state(db, key)
        return int(raw) if raw and raw.isdigit() else 0

    def _get_state_datetime(self, db: Session, key: str) -> datetime | None:
        raw = self._get_state(db, key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None


maintenance_service = MaintenanceService()
