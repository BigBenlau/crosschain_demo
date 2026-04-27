"""資料庫連線與初始化工具。

本檔負責：
- 建立 SQLAlchemy engine 與 session factory
- 提供 `init_db()` 建表初始化
- 提供 `get_db()` 作為 API 層資料庫依賴
"""

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base


is_sqlite = settings.db_path.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(settings.db_path, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _column_names(table_name: str) -> set[str]:
    inspector = inspect(engine)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = inspect(engine)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _ensure_schema_compat() -> None:
    with engine.begin() as conn:
        xchain_txs_columns = _column_names("xchain_txs")
        if "ethereum_block_number" not in xchain_txs_columns:
            conn.execute(text("ALTER TABLE xchain_txs ADD COLUMN ethereum_block_number BIGINT"))
        if "ethereum_log_index" not in xchain_txs_columns:
            conn.execute(text("ALTER TABLE xchain_txs ADD COLUMN ethereum_log_index INTEGER"))

        raw_log_columns = _column_names("raw_logs")
        if "canonical_id" not in raw_log_columns:
            conn.execute(text("ALTER TABLE raw_logs ADD COLUMN canonical_id VARCHAR(191)"))
        if "block_timestamp" not in raw_log_columns:
            conn.execute(text("ALTER TABLE raw_logs ADD COLUMN block_timestamp DATETIME"))
        if "created_at" not in raw_log_columns:
            conn.execute(text("ALTER TABLE raw_logs ADD COLUMN created_at DATETIME"))
        if "updated_at" not in raw_log_columns:
            conn.execute(text("ALTER TABLE raw_logs ADD COLUMN updated_at DATETIME"))
        conn.execute(
            text(
                "UPDATE raw_logs "
                "SET created_at = COALESCE(created_at, block_timestamp, CURRENT_TIMESTAMP), "
                "updated_at = COALESCE(updated_at, block_timestamp, created_at, CURRENT_TIMESTAMP)"
            )
        )

        timeline_columns = _column_names("xchain_timeline_events")
        if "decoded_json" not in timeline_columns:
            conn.execute(text("ALTER TABLE xchain_timeline_events ADD COLUMN decoded_json TEXT"))

        xchain_txs_indexes = _index_names("xchain_txs")
        if "ix_xchain_txs_src_timestamp" not in xchain_txs_indexes:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_xchain_txs_src_timestamp ON xchain_txs (src_timestamp)"))

        raw_logs_indexes = _index_names("raw_logs")
        if "ix_raw_logs_canonical_id" not in raw_logs_indexes:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_logs_canonical_id ON raw_logs (canonical_id)"))
        if "ix_raw_logs_block_timestamp" not in raw_logs_indexes:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_logs_block_timestamp ON raw_logs (block_timestamp)"))
        if "ix_raw_logs_updated_at" not in raw_logs_indexes:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_logs_updated_at ON raw_logs (updated_at)"))
        if "ix_xchain_txs_ethereum_block_number" not in xchain_txs_indexes:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_xchain_txs_ethereum_block_number ON xchain_txs (ethereum_block_number)")
            )
        if "ix_xchain_txs_ethereum_log_index" not in xchain_txs_indexes:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_xchain_txs_ethereum_log_index ON xchain_txs (ethereum_log_index)")
            )
        if "ix_xchain_txs_eth_order" not in xchain_txs_indexes:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_xchain_txs_eth_order "
                    "ON xchain_txs (ethereum_block_number, ethereum_log_index)"
                )
            )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_schema_compat()


def run_sqlite_vacuum() -> None:
    """對 SQLite 執行 VACUUM，非 SQLite 時直接跳過。"""
    if not is_sqlite:
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
