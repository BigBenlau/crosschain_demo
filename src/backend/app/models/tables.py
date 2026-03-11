"""MVP 階段的資料表模型定義。

本檔負責：
- 定義所有 ORM table schema（交易、時間線、raw logs、游標、搜索、風險報告）
- 提供資料表主鍵、外鍵、索引、唯一約束

維護規則：
- 修改本檔任一 table/column/constraint 時，
  必須同步更新 `src/backend/docs/db_schema.md`。
"""

from datetime import datetime

from sqlalchemy import BIGINT, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class XChainTx(Base):
    """統一跨鏈交易主表。"""

    __tablename__ = "xchain_txs"

    canonical_id: Mapped[str] = mapped_column(String(191), primary_key=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    src_chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    src_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    src_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ethereum_block_number: Mapped[int | None] = mapped_column(BIGINT, nullable=True, index=True)
    ethereum_log_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    dst_chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    dst_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    dst_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms_verify: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms_execute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class XChainTimelineEvent(Base):
    """跨鏈交易時間線事件表。"""

    __tablename__ = "xchain_timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(
        String(191), ForeignKey("xchain_txs.canonical_id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    block_number: Mapped[int | None] = mapped_column(BIGINT, nullable=True, index=True)
    log_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RawLog(Base):
    """原始鏈上 log 存儲表（來自 RPC）。"""

    __tablename__ = "raw_logs"
    __table_args__ = (UniqueConstraint("chain_id", "tx_hash", "log_index", name="uq_raw_logs_pos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    block_number: Mapped[int] = mapped_column(BIGINT, nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    topic0: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    data: Mapped[str | None] = mapped_column(Text, nullable=True)
    decoded_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class IndexerCursor(Base):
    """索引器游標表（按鏈+協議記錄進度）。"""

    __tablename__ = "indexer_cursors"

    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    protocol: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_block: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    to_block: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SearchIndex(Base):
    """搜尋索引表（key 映射到 canonical_id）。"""

    __tablename__ = "search_index"
    __table_args__ = (UniqueConstraint("key_type", "key_value", "canonical_id", name="uq_search_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key_value: Mapped[str] = mapped_column(String(191), nullable=False, index=True)
    canonical_id: Mapped[str] = mapped_column(
        String(191), ForeignKey("xchain_txs.canonical_id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)


class RiskReport(Base):
    """安全風險分析結果表。"""

    __tablename__ = "risk_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(
        String(191), ForeignKey("xchain_txs.canonical_id"), nullable=False, index=True
    )
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_factors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
