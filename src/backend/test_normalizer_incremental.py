import json
import os
import sys
import unittest
from datetime import datetime, timezone
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
        "MAINTENANCE_ENABLED": "false",
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

from app.models import Base, RawLog, RiskReport, SearchIndex, XChainTimelineEvent, XChainTx  # noqa: E402
from app.normalizer.service import normalizer_service  # noqa: E402


SENDER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CANONICAL_ID = f"lz:30101:{SENDER}:7"


def _lz_decoded_sent() -> str:
    return json.dumps(
        {
            "canonical_hint": {
                "src_eid": 30101,
                "sender": SENDER,
                "nonce": 7,
                "dst_eid": 30110,
            },
            "direction": {
                "src_eid": 30101,
                "dst_eid": 30110,
            },
        }
    )


def _lz_decoded_executed() -> str:
    return json.dumps(
        {
            "canonical_hint": {
                "src_eid": 30101,
                "sender": SENDER,
                "nonce": 7,
            }
        }
    )


def _lz_decoded_verified() -> str:
    return json.dumps(
        {
            "canonical_hint": {
                "src_eid": 30101,
                "sender": SENDER,
                "nonce": 7,
            }
        }
    )


class NormalizerIncrementalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _add_sent_log(self, canonical_id: str | None = CANONICAL_ID, removed: bool = False) -> None:
        self.db.add(
            RawLog(
                protocol="layerzero",
                chain_id=1,
                block_number=100,
                canonical_id=canonical_id,
                tx_hash="0xsent",
                log_index=1,
                topic0="lz_sent",
                data='{"stage":"sent"}',
                decoded_json=_lz_decoded_sent(),
                removed=removed,
            )
        )

    def _add_executed_log(self, canonical_id: str | None = CANONICAL_ID, removed: bool = False) -> None:
        self.db.add(
            RawLog(
                protocol="layerzero",
                chain_id=42161,
                block_number=200,
                canonical_id=canonical_id,
                tx_hash="0xexecuted",
                log_index=2,
                topic0="lz_executed",
                data='{"stage":"executed"}',
                decoded_json=_lz_decoded_executed(),
                removed=removed,
            )
        )

    def _add_verified_log(self, canonical_id: str | None = CANONICAL_ID, removed: bool = False) -> None:
        self.db.add(
            RawLog(
                protocol="layerzero",
                chain_id=42161,
                block_number=150,
                canonical_id=canonical_id,
                tx_hash="0xverified",
                log_index=3,
                topic0="lz_verified",
                data='{"stage":"verified"}',
                decoded_json=_lz_decoded_verified(),
                removed=removed,
            )
        )

    def test_incremental_normalize_advances_existing_transaction(self) -> None:
        self._add_sent_log()
        self.db.commit()

        changed = normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))
        self.assertIn(CANONICAL_ID, changed)

        tx = self.db.get(XChainTx, CANONICAL_ID)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.status, "SENT")
        self.assertEqual(tx.src_tx_hash, "0xsent")
        self.assertIsNone(tx.dst_tx_hash)

        self._add_executed_log()
        self.db.commit()

        changed = normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))
        self.assertIn(CANONICAL_ID, changed)

        tx = self.db.get(XChainTx, CANONICAL_ID)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.status, "EXECUTED")
        self.assertEqual(tx.src_tx_hash, "0xsent")
        self.assertEqual(tx.dst_tx_hash, "0xexecuted")

        timeline_rows = self.db.execute(
            select(XChainTimelineEvent).where(XChainTimelineEvent.canonical_id == CANONICAL_ID)
        ).scalars().all()
        self.assertEqual(len(timeline_rows), 2)

        search_rows = self.db.execute(
            select(SearchIndex).where(SearchIndex.canonical_id == CANONICAL_ID)
        ).scalars().all()
        indexed_keys = {(row.key_type, row.key_value) for row in search_rows}
        self.assertEqual(
            indexed_keys,
            {
                ("canonicalId", CANONICAL_ID),
                ("txHash", "0xsent"),
                ("txHash", "0xexecuted"),
            },
        )

    def test_removed_source_log_prunes_transaction_and_related_rows(self) -> None:
        self._add_sent_log()
        self._add_executed_log()
        self.db.commit()
        normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))

        self.db.add(RiskReport(canonical_id=CANONICAL_ID, verdict="SAFE", risk_score=95))
        self.db.commit()

        sent_row = self.db.execute(select(RawLog).where(RawLog.tx_hash == "0xsent")).scalar_one()
        sent_row.removed = True
        self.db.commit()

        changed = normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))
        self.assertIn(CANONICAL_ID, changed)
        self.assertIsNone(self.db.get(XChainTx, CANONICAL_ID))
        self.assertEqual(
            self.db.execute(select(XChainTimelineEvent).where(XChainTimelineEvent.canonical_id == CANONICAL_ID))
            .scalars()
            .all(),
            [],
        )
        self.assertEqual(
            self.db.execute(select(SearchIndex).where(SearchIndex.canonical_id == CANONICAL_ID)).scalars().all(),
            [],
        )
        self.assertEqual(
            self.db.execute(select(RiskReport).where(RiskReport.canonical_id == CANONICAL_ID)).scalars().all(),
            [],
        )

    def test_backfill_missing_raw_log_canonical_id_preserves_existing_match(self) -> None:
        self._add_sent_log(canonical_id=None)
        self._add_executed_log(canonical_id=CANONICAL_ID)
        self.db.commit()

        changed = normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))
        self.assertIn(CANONICAL_ID, changed)

        backfilled_sent = self.db.execute(select(RawLog).where(RawLog.tx_hash == "0xsent")).scalar_one()
        self.assertEqual(backfilled_sent.canonical_id, CANONICAL_ID)

        tx = self.db.get(XChainTx, CANONICAL_ID)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.status, "EXECUTED")
        self.assertEqual(tx.src_tx_hash, "0xsent")
        self.assertEqual(tx.dst_tx_hash, "0xexecuted")

    def test_pending_normalization_queue_survives_without_in_memory_changed_ids(self) -> None:
        self._add_sent_log()
        normalizer_service.enqueue_canonical_ids(self.db, {CANONICAL_ID})
        self.db.commit()

        changed = normalizer_service.normalize_changed(self.db, set(), dual_chain_pair=(1, 42161))
        self.assertIn(CANONICAL_ID, changed)

        tx = self.db.get(XChainTx, CANONICAL_ID)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.status, "SENT")

    def test_recent_verified_progress_is_not_retroactively_marked_stuck(self) -> None:
        self._add_sent_log()
        self.db.commit()
        normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))

        tx = self.db.get(XChainTx, CANONICAL_ID)
        assert tx is not None
        tx.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        tx.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.db.commit()

        self._add_verified_log()
        self.db.commit()
        normalizer_service.normalize_changed(self.db, {CANONICAL_ID}, dual_chain_pair=(1, 42161))

        tx = self.db.get(XChainTx, CANONICAL_ID)
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.status, "VERIFIED")
        self.assertEqual(tx.failure_category, None)


if __name__ == "__main__":
    unittest.main()
