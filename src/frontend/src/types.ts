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

export type DecodedLogItem = {
  stage: string;
  chainId: number | null;
  txHash: string | null;
  blockNumber: number | null;
  logIndex: number | null;
  eventName: string | null;
  rawData: string | null;
  decodedJson: string | null;
};

export type RiskReport = {
  verdict: string;
  score: number;
  factors: string[];
  summary: string | null;
  analyzedAt: string | null;
  observations?: string[] | null;
  aiModel?: string | null;
};

export type GlobalStats = {
  total: number;
  executed: number;
  riskPending: number;
  attention: number;
  byProtocol: Record<string, ProtocolStats>;
};

export type ProtocolStats = {
  total: number;
  executed: number;
  riskPending: number;
  attention: number;
};

export type TxDetail = {
  tx: XChainTxSummary;
  timeline: TimelineItem[];
  decodedLogs: DecodedLogItem[];
  latency: Record<string, number | null>;
  failure: string | null;
  ruleReport: RiskReport | null;
  riskReport: RiskReport | null;
};

export type StreamLatestEvent = {
  event: string;
  cycleSeq: number;
  insertedCanonicalIds: string[];
  items: XChainTxSummary[];
};
