"""API 回應模型定義。

本檔負責：
- 定義 search/latest/detail/health 的輸出結構
- 提供前後端穩定的資料契約
"""

from pydantic import BaseModel


class XChainTxSummary(BaseModel):
    """跨鏈交易摘要模型。"""

    canonicalId: str
    protocol: str
    status: str
    srcChainId: int | None
    srcTxHash: str | None
    dstChainId: int | None
    dstTxHash: str | None
    updatedAt: str | None


class TimelineItem(BaseModel):
    """交易時間線節點模型。"""

    stage: str
    chainId: int | None
    txHash: str | None
    blockNumber: int | None
    eventName: str | None
    eventTs: str | None
    evidence: str | None


class DecodedLogItem(BaseModel):
    """交易 decode 區塊模型。"""

    stage: str
    chainId: int | None
    txHash: str | None
    blockNumber: int | None
    logIndex: int | None
    eventName: str | None
    rawData: str | None
    decodedJson: str | None


class RiskReportItem(BaseModel):
    """風險報告輸出模型。"""

    verdict: str
    score: int
    factors: list[str]
    summary: str | None
    analyzedAt: str | None


class SearchResponse(BaseModel):
    """搜索 API 回應模型。"""

    items: list[XChainTxSummary]
    total: int
    nextCursor: str | None


class LatestResponse(BaseModel):
    """最新交易流 API 回應模型。"""

    items: list[XChainTxSummary]
    nextCursor: str | None


class ProtocolStats(BaseModel):
    """單協議統計輸出模型。"""

    total: int
    executed: int
    riskPending: int
    attention: int


class ByProtocolStats(BaseModel):
    """按協議拆分的統計輸出模型。"""

    layerzero: ProtocolStats
    wormhole: ProtocolStats


class GlobalStatsResponse(BaseModel):
    """全局統計 API 回應模型。"""

    total: int
    executed: int
    riskPending: int
    attention: int
    byProtocol: ByProtocolStats


class XChainTxDetail(BaseModel):
    """交易詳情 API 回應模型。"""

    tx: XChainTxSummary
    timeline: list[TimelineItem]
    decodedLogs: list[DecodedLogItem]
    latency: dict
    failure: str | None
    riskReport: RiskReportItem | None


class StreamLatestEvent(BaseModel):
    """SSE latest stream 事件模型。"""

    event: str
    cycleSeq: int
    insertedCanonicalIds: list[str]
    items: list[XChainTxSummary]
