export type XChainTxSummary = {
  canonicalId: string;
  protocol: string;
  status: string;
  srcChainId: number | null;
  srcTxHash: string | null;
  dstChainId: number | null;
  dstTxHash: string | null;
  updatedAt: string | null;
};

export type TimelineItem = {
  stage: string;
  chainId: number | null;
  txHash: string | null;
  blockNumber: number | null;
  eventName: string | null;
  eventTs: string | null;
  evidence: string | null;
};

export type RiskReport = {
  verdict: string;
  score: number;
  factors: string[];
  summary: string | null;
  analyzedAt: string | null;
};

export type GlobalStats = {
  total: number;
  executed: number;
  riskPending: number;
  attention: number;
};

export type TxDetail = {
  tx: XChainTxSummary;
  timeline: TimelineItem[];
  latency: Record<string, number | null>;
  failure: string | null;
  riskReport: RiskReport | null;
};

export type StreamLatestEvent = {
  event: string;
  cycleSeq: number;
  insertedCanonicalIds: string[];
  items: XChainTxSummary[];
};
