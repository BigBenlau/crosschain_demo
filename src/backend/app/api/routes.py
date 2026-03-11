"""API 路由實作。

本檔負責：
- 提供 search/latest/detail/stream API
- 將資料庫模型轉為 API 回應模型
- 提供最小 SSE 實時推送能力
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ByProtocolStats,
    DecodedLogItem,
    GlobalStatsResponse,
    LatestResponse,
    ProtocolStats,
    RiskReportItem,
    SearchResponse,
    StreamLatestEvent,
    TimelineItem,
    XChainTxDetail,
    XChainTxSummary,
)
from app.db import SessionLocal, get_db
from app.indexer import indexer_service
from app.models import RiskReport, SearchIndex, XChainTimelineEvent, XChainTx

router = APIRouter(prefix="/api", tags=["xchain"])

STAGE_DISPLAY_ORDER = {
    "SENT": 0,
    "VERIFIED": 1,
    "EXECUTED": 2,
    "FAILED": 2,
    "STUCK": 3,
}


def _to_iso(value: datetime | None) -> str | None:
    """將 datetime 轉為 ISO 字串。"""
    return value.isoformat() if value else None


def _timeline_sort_key(item: XChainTimelineEvent, src_chain_id: int | None) -> tuple[int, int, int, int, int]:
    """將 timeline/decode 事件排序為由早到晚、由上到下的閱讀順序。"""
    stage_rank = STAGE_DISPLAY_ORDER.get(item.stage, 99)
    chain_rank = 0 if src_chain_id is not None and item.chain_id == src_chain_id else 1
    block_number = int(item.block_number) if item.block_number is not None else 2**63 - 1
    log_index = int(item.log_index) if item.log_index is not None else 2**31 - 1
    stable_id = int(item.id) if item.id is not None else 2**31 - 1
    return (stage_rank, chain_rank, block_number, log_index, stable_id)


def _to_summary(tx: XChainTx) -> XChainTxSummary:
    """將交易主表模型轉為摘要模型。"""
    return XChainTxSummary(
        canonicalId=tx.canonical_id,
        protocol=tx.protocol,
        status=tx.status,
        srcChainId=tx.src_chain_id,
        srcTxHash=tx.src_tx_hash,
        dstChainId=tx.dst_chain_id,
        dstTxHash=tx.dst_tx_hash,
        updatedAt=_to_iso(tx.updated_at),
    )


def _to_risk_report(report: RiskReport | None) -> RiskReportItem | None:
    """將風險報告模型轉為輸出模型。"""
    if report is None:
        return None

    factors: list[str] = []
    if report.risk_factors_json:
        try:
            parsed = json.loads(report.risk_factors_json)
            if isinstance(parsed, list):
                factors = [str(item) for item in parsed]
        except ValueError:
            factors = []

    return RiskReportItem(
        verdict=report.verdict,
        score=report.risk_score,
        factors=factors,
        summary=report.analysis_summary,
        analyzedAt=_to_iso(report.analyzed_at),
    )


def _cursor_to_offset(cursor: str | None) -> int:
    """將游標字串轉為 offset。"""
    if cursor is None:
        return 0
    return int(cursor) if cursor.isdigit() else 0


def _build_latest_query(
    protocol: str | None,
    status: str | None,
    src_chain: int | None,
    dst_chain: int | None,
    category: str | None,
):
    """建立 latest 列表查詢。"""
    stmt = select(XChainTx)
    filters = []
    normalized_protocol = protocol.strip().lower() if protocol else None
    if normalized_protocol and normalized_protocol != "all":
        filters.append(XChainTx.protocol == normalized_protocol)
    if status:
        filters.append(XChainTx.status == status)
    if category:
        normalized = category.strip().lower()
        if normalized == "executed":
            filters.append(XChainTx.status == "EXECUTED")
        elif normalized == "in_progress":
            filters.append(XChainTx.status.in_(("SENT", "VERIFIED")))
        elif normalized == "attention":
            filters.append(XChainTx.status.in_(("FAILED", "STUCK")))
    if src_chain is not None:
        filters.append(XChainTx.src_chain_id == src_chain)
    if dst_chain is not None:
        filters.append(XChainTx.dst_chain_id == dst_chain)
    if filters:
        stmt = stmt.where(and_(*filters))
    block_sort = func.coalesce(XChainTx.ethereum_block_number, -1)
    log_sort = func.coalesce(XChainTx.ethereum_log_index, -1)
    return stmt.order_by(desc(block_sort), desc(log_sort), desc(XChainTx.canonical_id))


@router.get("/latest", response_model=LatestResponse)
def latest(
    protocol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    srcChain: int | None = Query(default=None),
    dstChain: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> LatestResponse:
    """回傳最新交易流（支援簡易游標分頁）。"""
    offset = _cursor_to_offset(cursor)
    stmt = _build_latest_query(protocol, status, srcChain, dstChain, category).offset(offset).limit(limit + 1)
    rows = db.execute(stmt).scalars().all()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(offset + limit) if has_more else None
    return LatestResponse(items=[_to_summary(item) for item in items], nextCursor=next_cursor)


@router.get("/stats", response_model=GlobalStatsResponse)
def global_stats(db: Session = Depends(get_db)) -> GlobalStatsResponse:
    """回傳 xchain_txs 的全局統計值。"""
    stmt = select(
        func.count().label("total"),
        func.sum(case((XChainTx.status == "EXECUTED", 1), else_=0)).label("executed"),
        func.sum(case((XChainTx.status.in_(("SENT", "VERIFIED")), 1), else_=0)).label("risk_pending"),
        func.sum(case((XChainTx.status.in_(("FAILED", "STUCK")), 1), else_=0)).label("attention"),
    )
    row = db.execute(stmt).one()
    protocol_stmt = select(
        XChainTx.protocol.label("protocol"),
        func.count().label("total"),
        func.sum(case((XChainTx.status == "EXECUTED", 1), else_=0)).label("executed"),
        func.sum(case((XChainTx.status.in_(("SENT", "VERIFIED")), 1), else_=0)).label("risk_pending"),
        func.sum(case((XChainTx.status.in_(("FAILED", "STUCK")), 1), else_=0)).label("attention"),
    ).group_by(XChainTx.protocol)
    protocol_rows = db.execute(protocol_stmt).all()
    by_protocol = {
        "layerzero": ProtocolStats(total=0, executed=0, riskPending=0, attention=0),
        "wormhole": ProtocolStats(total=0, executed=0, riskPending=0, attention=0),
    }
    for protocol_row in protocol_rows:
        protocol = str(protocol_row.protocol or "").lower()
        if protocol not in by_protocol:
            continue
        by_protocol[protocol] = ProtocolStats(
            total=int(protocol_row.total or 0),
            executed=int(protocol_row.executed or 0),
            riskPending=int(protocol_row.risk_pending or 0),
            attention=int(protocol_row.attention or 0),
        )

    return GlobalStatsResponse(
        total=int(row.total or 0),
        executed=int(row.executed or 0),
        riskPending=int(row.risk_pending or 0),
        attention=int(row.attention or 0),
        byProtocol=ByProtocolStats(
            layerzero=by_protocol["layerzero"],
            wormhole=by_protocol["wormhole"],
        ),
    )


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """依 txHash / canonicalId / address 搜索交易。"""
    query = q.strip()
    lowered = query.lower()

    sub_stmt = select(SearchIndex.canonical_id).where(
        or_(SearchIndex.key_value == query, SearchIndex.key_value == lowered)
    )
    canonical_ids = [item[0] for item in db.execute(sub_stmt).all()]
    if not canonical_ids:
        canonical_ids = [query]

    tx_stmt = (
        select(XChainTx)
        .where(XChainTx.canonical_id.in_(canonical_ids))
        .order_by(desc(XChainTx.updated_at), desc(XChainTx.canonical_id))
        .limit(limit)
    )
    items = db.execute(tx_stmt).scalars().all()

    count_stmt = select(func.count()).select_from(XChainTx).where(XChainTx.canonical_id.in_(canonical_ids))
    total = int(db.execute(count_stmt).scalar() or 0)
    return SearchResponse(items=[_to_summary(item) for item in items], total=total, nextCursor=None)


@router.get("/tx/{canonical_id}", response_model=XChainTxDetail)
def tx_detail(canonical_id: str, db: Session = Depends(get_db)) -> XChainTxDetail:
    """回傳單筆交易詳情。"""
    tx = db.get(XChainTx, canonical_id)
    if tx is None:
        empty = XChainTxSummary(
            canonicalId=canonical_id,
            protocol="unknown",
            status="UNKNOWN",
            srcChainId=None,
            srcTxHash=None,
            dstChainId=None,
            dstTxHash=None,
            updatedAt=None,
        )
        return XChainTxDetail(tx=empty, timeline=[], decodedLogs=[], latency={}, failure=None, riskReport=None)

    timeline_stmt = (
        select(XChainTimelineEvent)
        .where(XChainTimelineEvent.canonical_id == canonical_id)
        .order_by(XChainTimelineEvent.id.asc())
    )
    timeline_rows = db.execute(timeline_stmt).scalars().all()
    ordered_timeline_rows = sorted(timeline_rows, key=lambda item: _timeline_sort_key(item, tx.src_chain_id))
    timeline = [
        TimelineItem(
            stage=item.stage,
            chainId=item.chain_id,
            txHash=item.tx_hash,
            blockNumber=item.block_number,
            eventName=item.event_name,
            eventTs=_to_iso(item.event_ts),
            evidence=item.evidence_json,
        )
        for item in ordered_timeline_rows
    ]
    decoded_logs = [
        DecodedLogItem(
            stage=item.stage,
            chainId=item.chain_id,
            txHash=item.tx_hash,
            blockNumber=item.block_number,
            logIndex=item.log_index,
            eventName=item.event_name,
            rawData=item.evidence_json,
            decodedJson=item.decoded_json,
        )
        for item in ordered_timeline_rows
        if item.evidence_json or item.decoded_json
    ]

    risk_stmt = (
        select(RiskReport)
        .where(RiskReport.canonical_id == canonical_id)
        .order_by(desc(RiskReport.analyzed_at), desc(RiskReport.id))
    )
    risk_row = db.execute(risk_stmt).scalars().first()

    latency = {
        "total": tx.latency_ms_total,
        "verify": tx.latency_ms_verify,
        "execute": tx.latency_ms_execute,
    }
    return XChainTxDetail(
        tx=_to_summary(tx),
        timeline=timeline,
        decodedLogs=decoded_logs,
        latency=latency,
        failure=tx.failure_category,
        riskReport=_to_risk_report(risk_row),
    )


async def _stream_generator(category: str | None, protocol: str | None) -> AsyncIterator[bytes]:
    """SSE 生成器：按 indexer cycle 推送最新列表與增量 canonical id。"""
    last_cycle_seq = -1
    while True:
        snapshot = indexer_service.snapshot()
        if snapshot.last_cycle_seq != last_cycle_seq:
            with SessionLocal() as db:
                stmt = _build_latest_query(protocol, None, None, None, category).limit(50)
                rows = db.execute(stmt).scalars().all()
                items = [_to_summary(row) for row in rows]
                available_ids = {item.canonicalId for item in items}
                inserted_ids = [canonical_id for canonical_id in snapshot.last_changed_ids if canonical_id in available_ids]
                payload = StreamLatestEvent(
                    event="indexer_cycle",
                    cycleSeq=snapshot.last_cycle_seq,
                    insertedCanonicalIds=inserted_ids,
                    items=items,
                )
                message = f"data: {json.dumps(payload.model_dump(), ensure_ascii=False)}\n\n"
                yield message.encode("utf-8")
                last_cycle_seq = snapshot.last_cycle_seq

        await asyncio.sleep(1)


@router.get("/stream")
async def stream(
    category: str | None = Query(default=None),
    protocol: str | None = Query(default=None),
) -> StreamingResponse:
    """SSE 實時推送端點。"""
    return StreamingResponse(_stream_generator(category, protocol), media_type="text/event-stream")
