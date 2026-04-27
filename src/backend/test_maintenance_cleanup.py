import gzip
import json
import os
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

def _set_test_env() -> None:
    defaults = {
        "APP_NAME": "xchain-demo-test",
        "APP_ENV": "test",
        "APP_PORT": "8000",
        "TARGET_CHAIN": "arbitrum",
        "API_KEY": "test",
        "ETH_RPC_URL": "http://localhost:8545",
        "TARGET_CHAIN_RPC_URL": "http://localhost:8546",
        "TARGET_CHAIN_EXPLORER_BASE_URL": "https://arbiscan.io",
        "ETH_START_BLOCK": "1",
        "TARGET_CHAIN_START_BLOCK": "1",
        "ETH_FINALITY_DEPTH": "12",
        "TARGET_CHAIN_FINALITY_DEPTH": "12",
        "INDEXER_POLL_SECONDS": "30",
        "INDEXER_CHUNK_SIZE": "2000",
        "STUCK_TIMEOUT_MINUTES": "99999",
        "MAINTENANCE_ENABLED": "true",
        "MAINTENANCE_POLL_SECONDS": "3600",
        "MAINTENANCE_REMOVED_RETENTION_DAYS": "7",
        "MAINTENANCE_EXECUTED_RETENTION_DAYS": "60",
        "MAINTENANCE_FAILED_ARCHIVE_RETENTION_DAYS": "60",
        "MAINTENANCE_ARCHIVE_DIR": "./test-archives",
        "MAINTENANCE_VACUUM_INTERVAL_HOURS": "168",
        "MAINTENANCE_VACUUM_MIN_DELETED_ROWS": "10000",
        "LAYERZERO_TOPIC0S": "",
        "WORMHOLE_TOPIC0S": "",
        "LAYERZERO_SENT_TOPICS": "lz_sent",
        "LAYERZERO_VERIFIED_TOPICS": "lz_verified",
        "LAYERZERO_EXECUTED_TOPICS": "lz_executed",
        "LAYERZERO_FAILED_TOPICS": "lz_failed",
        "WORMHOLE_SENT_TOPICS": "wh_sent",
        "WORMHOLE_EXECUTED_TOPICS": "wh_executed",
        "LAYERZERO_ETHEREUM_ENDPOINTS": "0x1111111111111111111111111111111111111111",
        "LAYERZERO_TARGET_ENDPOINTS": "0x1111111111111111111111111111111111111111",
        "WORMHOLE_ETHEREUM_CORE_CONTRACTS": "0x2222222222222222222222222222222222222222",
        "WORMHOLE_TARGET_CORE_CONTRACTS": "0x3333333333333333333333333333333333333333",
        "WORMHOLE_ETHEREUM_TOKEN_BRIDGES": "0x4444444444444444444444444444444444444444",
        "WORMHOLE_TARGET_TOKEN_BRIDGES": "0x5555555555555555555555555555555555555555",
        "LAYERZERO_ETHEREUM_EID": "30101",
        "LAYERZERO_TARGET_EID": "30110",
        "WORMHOLE_ETHEREUM_CHAIN_ID": "2",
        "WORMHOLE_TARGET_CHAIN_ID": "23",
        "AI_API_KEY": "",
        "AI_BASE_URL": "https://example.com",
        "AI_MODEL": "test-model",
        "AI_TIMEOUT_SECONDS": "30",
        "AI_BATCH_SIZE": "1",
        "AI_BATCH_MAX_SIZE": "1",
        "AI_MAX_PROMPT_CHARS": "10000",
        "AI_MAX_OUTPUT_TOKENS": "512",
        "AI_TEMPERATURE": "0",
        "DB_PATH": "sqlite:///:memory:",
    }
    for key, value in defaults.items():
        os.environ[key] = value


_set_test_env()
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings  # noqa: E402
from app.models import Base, MaintenanceState, RawLog, RiskReport, XChainTimelineEvent, XChainTx  # noqa: E402
from app.maintenance.service import maintenance_service  # noqa: E402


ARCHIVE_DIR = Path(settings.maintenance_archive_dir)


class MaintenanceCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        if ARCHIVE_DIR.exists():
            shutil.rmtree(ARCHIVE_DIR)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        if ARCHIVE_DIR.exists():
            shutil.rmtree(ARCHIVE_DIR)

    def _add_tx(self, canonical_id: str, status: str, updated_at: datetime) -> XChainTx:
        tx = XChainTx(
            canonical_id=canonical_id,
            protocol="layerzero",
            status=status,
            src_chain_id=1,
            src_tx_hash=f"{canonical_id}-src",
            dst_chain_id=42161 if status in {"EXECUTED", "FAILED"} else None,
            dst_tx_hash=f"{canonical_id}-dst" if status in {"EXECUTED", "FAILED"} else None,
        )
        tx.created_at = updated_at
        tx.updated_at = updated_at
        self.db.add(tx)
        self.db.flush()
        return tx

    def _add_raw_log(
        self,
        *,
        canonical_id: str | None,
        tx_hash: str,
        removed: bool,
        updated_at: datetime,
        data: str = "{}",
    ) -> RawLog:
        row = RawLog(
            protocol="layerzero",
            chain_id=1,
            block_number=100,
            canonical_id=canonical_id,
            tx_hash=tx_hash,
            log_index=1 if removed else abs(hash(tx_hash)) % 100000,
            topic0="lz_sent",
            data=data,
            decoded_json='{"sample":true}',
            removed=removed,
        )
        row.created_at = updated_at
        row.updated_at = updated_at
        self.db.add(row)
        self.db.flush()
        return row

    def test_cleanup_deletes_removed_and_old_executed_logs(self) -> None:
        now = datetime(2026, 4, 27, tzinfo=timezone.utc)
        old_removed = now - timedelta(days=8)
        old_executed = now - timedelta(days=61)
        recent_removed = now - timedelta(days=2)

        self._add_raw_log(canonical_id=None, tx_hash="0xremoved-old", removed=True, updated_at=old_removed)
        self._add_raw_log(canonical_id=None, tx_hash="0xremoved-recent", removed=True, updated_at=recent_removed)

        self._add_tx("exec-old", "EXECUTED", old_executed)
        self._add_raw_log(canonical_id="exec-old", tx_hash="0xexec-old", removed=False, updated_at=old_executed)

        self._add_tx("stuck-old", "STUCK", old_executed)
        self._add_raw_log(canonical_id="stuck-old", tx_hash="0xstuck-old", removed=False, updated_at=old_executed)

        self.db.commit()
        summary = maintenance_service._run_cleanup_cycle(self.db, now)

        self.assertEqual(summary.deleted_removed_logs, 1)
        self.assertEqual(summary.deleted_executed_logs, 1)
        remaining_hashes = {row.tx_hash for row in self.db.execute(select(RawLog)).scalars().all()}
        self.assertIn("0xremoved-recent", remaining_hashes)
        self.assertIn("0xstuck-old", remaining_hashes)
        self.assertNotIn("0xremoved-old", remaining_hashes)
        self.assertNotIn("0xexec-old", remaining_hashes)

    def test_failed_transactions_are_archived_but_not_deleted(self) -> None:
        now = datetime(2026, 4, 27, tzinfo=timezone.utc)
        old_failed = now - timedelta(days=61)

        self._add_tx("failed-old", "FAILED", old_failed)
        self._add_raw_log(canonical_id="failed-old", tx_hash="0xfailed-old", removed=False, updated_at=old_failed)
        self.db.add(
            XChainTimelineEvent(
                canonical_id="failed-old",
                stage="FAILED",
                chain_id=42161,
                tx_hash="0xfailed-old",
                block_number=100,
                log_index=1,
                event_name="layerzero:failed",
                evidence_json='{"failed":true}',
                decoded_json='{"decoded":true}',
            )
        )
        self.db.add(RiskReport(canonical_id="failed-old", verdict="HIGH_RISK", risk_score=10))
        self.db.commit()

        summary = maintenance_service._run_cleanup_cycle(self.db, now)

        self.assertEqual(summary.archived_failed_txs, 1)
        archive_files = list((ARCHIVE_DIR / "failed").glob("*.json.gz"))
        self.assertEqual(len(archive_files), 1)
        with gzip.open(archive_files[0], "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["canonicalId"], "failed-old")
        self.assertEqual(payload["tx"]["status"], "FAILED")
        self.assertEqual(payload["rawLogs"][0]["txHash"], "0xfailed-old")
        remaining_hashes = {row.tx_hash for row in self.db.execute(select(RawLog)).scalars().all()}
        self.assertIn("0xfailed-old", remaining_hashes)

    def test_vacuum_threshold_state_is_recorded(self) -> None:
        now = datetime(2026, 4, 27, tzinfo=timezone.utc)
        old_removed = now - timedelta(days=8)

        self._add_raw_log(canonical_id=None, tx_hash="0xremoved-threshold", removed=True, updated_at=old_removed)
        self.db.add(MaintenanceState(state_key="cleanup.deleted_rows_since_vacuum", state_value="9999"))
        self.db.commit()

        summary = maintenance_service._run_cleanup_cycle(self.db, now)

        self.assertTrue(summary.should_vacuum)
        self.assertEqual(summary.vacuum_reason, "deleted_rows_since_vacuum=10000")


if __name__ == "__main__":
    unittest.main()
