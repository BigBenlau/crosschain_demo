import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { fetchGlobalStats, fetchLatest, fetchTx, searchTx } from "./api";
import type { LatestCategory, ProtocolFilter } from "./api";
import type {
  DecodedLogItem,
  GlobalStats,
  ProtocolStats,
  RiskReport,
  StreamLatestEvent,
  TxDetail,
  XChainTxSummary,
} from "./types";

function shortHash(value: string | null, head = 10, tail = 8): string {
  if (!value) {
    return "-";
  }
  if (value.length <= head + tail + 3) {
    return value;
  }
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
}

function chainLabel(chainId: number | null): string {
  if (chainId === null) {
    return "-";
  }
  if (chainId === 1) {
    return "Ethereum";
  }
  if (chainId === 42161) {
    return "Arbitrum";
  }
  return `Chain ${chainId}`;
}

function chainNameFromKey(chainKey: string): string {
  const normalized = chainKey.trim().toLowerCase();
  if (!normalized) {
    return "Target Chain";
  }
  if (normalized === "arbitrum") {
    return "Arbitrum";
  }
  if (normalized === "ethereum") {
    return "Ethereum";
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function statusTone(status: string): string {
  switch (status) {
    case "EXECUTED":
      return "is-success";
    case "FAILED":
      return "is-danger";
    case "STUCK":
      return "is-warning";
    case "VERIFIED":
      return "is-info";
    default:
      return "is-muted";
  }
}

function riskTone(verdict: string | null): string {
  switch (verdict) {
    case "SAFE":
      return "is-success";
    case "WARNING":
      return "is-warning";
    case "HIGH_RISK":
      return "is-danger";
    default:
      return "is-muted";
  }
}

function buildAiReportText(report: RiskReport | null): string {
  if (!report) {
    return "尚無 AI 分析結果。";
  }

  const lines = [
    `Status: ${report.verdict}`,
    `Score: ${report.score} / 100 (higher is safer)`,
    "Summary:",
    report.summary ?? "無摘要",
    "",
    "Risk Factors:",
    report.factors.length > 0 ? report.factors.map((item) => `- ${item}`).join("\n") : "- 無",
  ];
  return lines.join("\n");
}

function buildRuleReportText(report: RiskReport | null): string {
  if (!report) {
    return "尚無規則分析結果。";
  }

  const lines = [
    `Status: ${report.verdict}`,
    `Score: ${report.score} / 100 (higher is safer)`,
    "Summary:",
    report.summary ?? "無摘要",
    "",
    "Rule Observations:",
    report.observations && report.observations.length > 0 ? report.observations.map((item) => `- ${item}`).join("\n") : "- 無",
    "",
    "Rule Factors:",
    report.factors.length > 0 ? report.factors.map((item) => `- ${item}`).join("\n") : "- 無",
  ];
  return lines.join("\n");
}

function formatDecodedJson(value: string | null): string {
  if (!value) {
    return "無";
  }
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function buildTxExplorerUrl(txHash: string | null, chainId: number | null, targetChainExplorerBaseUrl: string): string | null {
  if (!txHash || !chainId) {
    return null;
  }
  const baseUrl = chainId === 1 ? "https://etherscan.io" : targetChainExplorerBaseUrl.trim();
  if (!baseUrl) {
    return null;
  }
  return `${baseUrl.replace(/\/$/, "")}/tx/${txHash}#eventlog`;
}

function renderTxHashLink(txHash: string | null, chainId: number | null, targetChainExplorerBaseUrl: string) {
  const href = buildTxExplorerUrl(txHash, chainId, targetChainExplorerBaseUrl);
  if (!href || !txHash) {
    return txHash ?? "-";
  }
  return (
    <a href={href} target="_blank" rel="noreferrer" className="tx-link">
      {txHash}
    </a>
  );
}

function categoryLabel(category: LatestCategory): string {
  switch (category) {
    case "executed":
      return "Executed Latest 50";
    case "in_progress":
      return "In Progress Latest 50";
    case "attention":
      return "Need Attention Latest 50";
    default:
      return "Latest 50";
  }
}

function protocolFilterLabel(protocol: ProtocolFilter): string {
  switch (protocol) {
    case "layerzero":
      return "LayerZero";
    case "wormhole":
      return "Wormhole";
    default:
      return "All Protocols";
  }
}

function protocolStats(stats: GlobalStats, protocol: "layerzero" | "wormhole"): ProtocolStats {
  return stats.byProtocol[protocol] ?? {
    total: 0,
    executed: 0,
    riskPending: 0,
    attention: 0,
  };
}

function statValueByCategory(stats: ProtocolStats, category: LatestCategory): number {
  switch (category) {
    case "executed":
      return stats.executed;
    case "in_progress":
      return stats.riskPending;
    case "attention":
      return stats.attention;
    default:
      return stats.total;
  }
}

function StatCard({
  title,
  value,
  layerzero,
  wormhole,
  active,
  onClick,
}: {
  title: string;
  value: number;
  layerzero: number;
  wormhole: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`stat-card ${active ? "is-active" : ""}`} onClick={onClick}>
      <span>{title}</span>
      <strong>{value}</strong>
      <div className="stat-breakdown">
        <div className="stat-breakdown-row">
          <span className="stat-breakdown-label">LayerZero</span>
          <span className="stat-breakdown-value">{layerzero}</span>
        </div>
        <div className="stat-breakdown-row">
          <span className="stat-breakdown-label">Wormhole</span>
          <span className="stat-breakdown-value">{wormhole}</span>
        </div>
      </div>
    </button>
  );
}

function DecodedLogCard({
  item,
  targetChainExplorerBaseUrl,
}: {
  item: DecodedLogItem;
  targetChainExplorerBaseUrl: string;
}) {
  return (
    <article className="decode-card">
      <div className="row-top">
        <span className={`pill ${statusTone(item.stage)}`}>{item.stage}</span>
        <span className="muted">{item.eventName ?? "Unknown Event"}</span>
      </div>
      <p className="muted">
        {chainLabel(item.chainId)} · block {item.blockNumber ?? "-"} · log {item.logIndex ?? "-"}
      </p>
      <p className="mono">
        Tx: {renderTxHashLink(item.txHash, item.chainId, targetChainExplorerBaseUrl)}
      </p>
      <div className="decode-grid">
        <div>
          <span className="muted">Raw Data</span>
          <pre className="decode-window mono">{item.rawData ?? "無"}</pre>
        </div>
        <div>
          <span className="muted">Decoded JSON</span>
          <pre className="decode-window mono">{formatDecodedJson(item.decodedJson)}</pre>
        </div>
      </div>
    </article>
  );
}

function DashboardPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<XChainTxSummary[]>([]);
  const [overview, setOverview] = useState<GlobalStats>({
    total: 0,
    executed: 0,
    riskPending: 0,
    attention: 0,
    byProtocol: {
      layerzero: { total: 0, executed: 0, riskPending: 0, attention: 0 },
      wormhole: { total: 0, executed: 0, riskPending: 0, attention: 0 },
    },
  });
  const [query, setQuery] = useState("");
  const [errorText, setErrorText] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [animatedIds, setAnimatedIds] = useState<string[]>([]);
  const [targetChainName, setTargetChainName] = useState("Target Chain");
  const [targetChainExplorerBaseUrl, setTargetChainExplorerBaseUrl] = useState("");
  const [ethStartBlock, setEthStartBlock] = useState<number | null>(null);
  const [targetStartBlock, setTargetStartBlock] = useState<number | null>(null);
  const [activeCategory, setActiveCategory] = useState<LatestCategory>("total");
  const [activeProtocol, setActiveProtocol] = useState<ProtocolFilter>("all");
  const [listMode, setListMode] = useState<"category" | "search">("category");

  useEffect(() => {
    void loadLatest("total");
  }, []);

  useEffect(() => {
    void loadOverview();
  }, []);

  useEffect(() => {
    void loadDashboardMeta();
  }, []);

  useEffect(() => {
    if (listMode !== "category") {
      return;
    }

    const animationTimers = new Set<number>();
    let source: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) {
        return;
      }

      const params = new URLSearchParams();
      if (activeCategory !== "total") {
        params.set("category", activeCategory);
      }
      if (activeProtocol !== "all") {
        params.set("protocol", activeProtocol);
      }
      const suffix = params.toString();
      source = new EventSource(suffix ? `/api/stream?${suffix}` : "/api/stream");
      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as StreamLatestEvent;
          if (!Array.isArray(payload.items)) {
            return;
          }

          setItems((previous) => {
            const previousIds = new Set(previous.map((item) => item.canonicalId));
            const inserted = (payload.insertedCanonicalIds || []).filter((id) => !previousIds.has(id));
            if (inserted.length > 0) {
              setAnimatedIds((current) => Array.from(new Set([...current, ...inserted])));
              const timer = window.setTimeout(() => {
                setAnimatedIds((current) => current.filter((itemId) => !inserted.includes(itemId)));
                animationTimers.delete(timer);
              }, 1600);
              animationTimers.add(timer);
            }
            return payload.items;
          });
          void loadOverview();
          setErrorText("");
        } catch (error) {
          setErrorText(String(error));
        }
      };
      source.onerror = () => {
        source?.close();
        if (reconnectTimer !== null) {
          window.clearTimeout(reconnectTimer);
        }
        reconnectTimer = window.setTimeout(() => {
          void loadLatest(activeCategory, activeProtocol);
          connect();
        }, 3000);
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      for (const timer of animationTimers) {
        window.clearTimeout(timer);
      }
    };
  }, [activeCategory, activeProtocol, listMode]);

  async function loadLatest(category: LatestCategory = activeCategory, protocol: ProtocolFilter = activeProtocol) {
    setLoadingList(true);
    try {
      const latest = await fetchLatest(category, protocol);
      setItems(latest);
      setActiveCategory(category);
      setActiveProtocol(protocol);
      setListMode("category");
      await loadOverview();
      setErrorText("");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setLoadingList(false);
    }
  }

  async function loadOverview() {
    const stats = await fetchGlobalStats();
    setOverview(stats);
  }

  async function loadSearchResults(searchTerm: string) {
    setLoadingList(true);
    try {
      const result = await searchTx(searchTerm);
      setItems(result);
      setListMode("search");
      setErrorText("");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setLoadingList(false);
    }
  }

  async function loadDashboardMeta() {
    try {
      const response = await fetch("/api/health");
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const chainKey = typeof payload?.targetChain === "string" ? payload.targetChain : "";
      const explorerBaseUrl = typeof payload?.targetChainExplorerBaseUrl === "string" ? payload.targetChainExplorerBaseUrl : "";
      const configuredStartBlock = payload?.configuredStartBlock ?? {};
      const ethereumStart = configuredStartBlock?.ethereum;
      const targetChainStart = configuredStartBlock?.targetChain;
      setTargetChainName(chainNameFromKey(chainKey));
      setTargetChainExplorerBaseUrl(explorerBaseUrl);
      setEthStartBlock(typeof ethereumStart === "number" ? ethereumStart : null);
      setTargetStartBlock(typeof targetChainStart === "number" ? targetChainStart : null);
    } catch {
      setTargetChainName("Target Chain");
      setTargetChainExplorerBaseUrl("");
      setEthStartBlock(null);
      setTargetStartBlock(null);
    }
  }

  async function onSearchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) {
      void loadLatest(activeCategory, activeProtocol);
      return;
    }
    void loadSearchResults(query.trim());
  }

  function openDetail(canonicalId: string) {
    navigate(`/tx/${encodeURIComponent(canonicalId)}`);
  }

  function onCategorySelect(category: LatestCategory) {
    setQuery("");
    void loadLatest(category, activeProtocol);
  }

  function onProtocolSelect(protocol: ProtocolFilter) {
    setQuery("");
    void loadLatest(activeCategory, protocol);
  }

  return (
    <div className="page">
      <div className="bg-glow bg-glow-a" />
      <div className="bg-glow bg-glow-b" />

      <header className="hero">
        <div>
          <p className="eyebrow">Crosschain Security Dashboard</p>
          <h1>跨鏈交易監測平台</h1>
          <p className="subtitle">
            實時查看 Ethereum ↔ {targetChainName} 的 LayerZero / Wormhole 跨鏈交易，點擊「查看細節」可進入安全分析詳情。
          </p>
          <p className="subtitle">
            數據統計起始區塊: Ethereum #{ethStartBlock ?? "-"} · {targetChainName} #{targetStartBlock ?? "-"}
          </p>
        </div>

        <form onSubmit={onSearchSubmit} className="search-form">
          <input
            placeholder="搜索 txHash / canonicalId / address"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit">Search</button>
        </form>
      </header>

      {errorText && <p className="error">{errorText}</p>}

      <section className="stats">
        <StatCard
          title="Total"
          value={overview.total}
          layerzero={protocolStats(overview, "layerzero").total}
          wormhole={protocolStats(overview, "wormhole").total}
          active={listMode === "category" && activeCategory === "total"}
          onClick={() => onCategorySelect("total")}
        />
        <StatCard
          title="Executed"
          value={overview.executed}
          layerzero={protocolStats(overview, "layerzero").executed}
          wormhole={protocolStats(overview, "wormhole").executed}
          active={listMode === "category" && activeCategory === "executed"}
          onClick={() => onCategorySelect("executed")}
        />
        <StatCard
          title="In Progress"
          value={overview.riskPending}
          layerzero={protocolStats(overview, "layerzero").riskPending}
          wormhole={protocolStats(overview, "wormhole").riskPending}
          active={listMode === "category" && activeCategory === "in_progress"}
          onClick={() => onCategorySelect("in_progress")}
        />
        <StatCard
          title="Need Attention"
          value={overview.attention}
          layerzero={protocolStats(overview, "layerzero").attention}
          wormhole={protocolStats(overview, "wormhole").attention}
          active={listMode === "category" && activeCategory === "attention"}
          onClick={() => onCategorySelect("attention")}
        />
      </section>

      <main className="dashboard-layout">
        <section className="panel">
          <div className="panel-header">
            <div className="panel-title-stack">
              <h2>{listMode === "search" ? "Search Results" : categoryLabel(activeCategory)}</h2>
              {listMode === "category" ? <p className="panel-subtitle">{protocolFilterLabel(activeProtocol)}</p> : null}
            </div>
            <button
              className="ghost"
              onClick={() =>
                listMode === "search" && query.trim()
                  ? void loadSearchResults(query.trim())
                  : void loadLatest(activeCategory, activeProtocol)
              }
            >
              {loadingList ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <div className="protocol-filters">
            <button
              type="button"
              className={`ghost protocol-filter ${activeProtocol === "all" ? "is-active" : ""}`}
              onClick={() => onProtocolSelect("all")}
            >
              All
            </button>
            <button
              type="button"
              className={`ghost protocol-filter ${activeProtocol === "layerzero" ? "is-active" : ""}`}
              onClick={() => onProtocolSelect("layerzero")}
            >
              LayerZero
            </button>
            <button
              type="button"
              className={`ghost protocol-filter ${activeProtocol === "wormhole" ? "is-active" : ""}`}
              onClick={() => onProtocolSelect("wormhole")}
            >
              Wormhole
            </button>
          </div>
          {items.length === 0 ? <p className="empty">暫無資料</p> : null}
          <ul className="tx-list">
            {items.map((item) => (
              <li key={item.canonicalId} className={animatedIds.includes(item.canonicalId) ? "is-new" : ""}>
                <div className="tx-main">
                  <div className="row-top">
                    <span className={`pill protocol ${item.protocol === "layerzero" ? "lz" : "wh"}`}>
                      {item.protocol.toUpperCase()}
                    </span>
                    <span className={`pill ${statusTone(item.status)}`}>{item.status}</span>
                  </div>
                  <div className="row-mid mono">{shortHash(item.canonicalId, 18, 12)}</div>
                  <div className="row-bottom">
                    <span>{chainLabel(item.srcChainId)}</span>
                    <span>→</span>
                    <span>{chainLabel(item.dstChainId)}</span>
                  </div>
                </div>
                <div className="row-actions">
                  <button type="button" onClick={() => openDetail(item.canonicalId)}>
                    查看細節
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}

function TxDetailPage() {
  const navigate = useNavigate();
  const { canonicalId: encodedCanonicalId } = useParams();
  const canonicalId = encodedCanonicalId ? decodeURIComponent(encodedCanonicalId) : "";

  const [detail, setDetail] = useState<TxDetail | null>(null);
  const [errorText, setErrorText] = useState("");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [targetChainExplorerBaseUrl, setTargetChainExplorerBaseUrl] = useState("");

  useEffect(() => {
    if (!canonicalId) {
      setDetail(null);
      return;
    }
    void loadDetail(canonicalId);
  }, [canonicalId]);

  useEffect(() => {
    void loadExplorerMeta();
  }, []);

  async function loadDetail(targetCanonicalId: string) {
    setLoadingDetail(true);
    try {
      const txDetail = await fetchTx(targetCanonicalId);
      setDetail(txDetail);
      setErrorText("");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setLoadingDetail(false);
    }
  }

  async function loadExplorerMeta() {
    try {
      const response = await fetch("/api/health");
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const explorerBaseUrl =
        typeof payload?.targetChainExplorerBaseUrl === "string" ? payload.targetChainExplorerBaseUrl : "";
      setTargetChainExplorerBaseUrl(explorerBaseUrl);
    } catch {
      setTargetChainExplorerBaseUrl("");
    }
  }

  return (
    <div className="page">
      <div className="bg-glow bg-glow-a" />
      <div className="bg-glow bg-glow-b" />

      <header className="detail-hero">
        <button type="button" className="ghost" onClick={() => navigate("/")}>
          ← Back Dashboard
        </button>
        <div className="detail-hero-title">
          <p className="eyebrow">Tx Detail</p>
          <h1 className="mono">{shortHash(canonicalId, 26, 16)}</h1>
        </div>
        <button type="button" className="ghost" onClick={() => void loadDetail(canonicalId)}>
          {loadingDetail ? "Refreshing..." : "Refresh"}
        </button>
      </header>

      {errorText && <p className="error">{errorText}</p>}

      <main className="detail-layout">
        <section className="panel">
          <div className="panel-header">
            <h2>Crosschain Route</h2>
            {detail ? <span className={`pill ${statusTone(detail.tx.status)}`}>{detail.tx.status}</span> : null}
          </div>

          {loadingDetail ? <p className="empty">載入中...</p> : null}
          {!loadingDetail && detail ? (
            <div className="detail-grid">
              <div>
                <span className="muted">Protocol</span>
                <p>{detail.tx.protocol.toUpperCase()}</p>
              </div>
              <div>
                <span className="muted">Failure</span>
                <p>{detail.failure ?? "N/A"}</p>
              </div>
              <div>
                <span className="muted">From Chain</span>
                <p>{chainLabel(detail.tx.srcChainId)}</p>
              </div>
              <div>
                <span className="muted">To Chain</span>
                <p>{chainLabel(detail.tx.dstChainId)}</p>
              </div>
              <div>
                <span className="muted">From Tx Hash</span>
                <p className="mono">
                  {renderTxHashLink(detail.tx.srcTxHash, detail.tx.srcChainId, targetChainExplorerBaseUrl)}
                </p>
              </div>
              <div>
                <span className="muted">To Tx Hash</span>
                <p className="mono">
                  {renderTxHashLink(detail.tx.dstTxHash, detail.tx.dstChainId, targetChainExplorerBaseUrl)}
                </p>
              </div>
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Tx Decode</h2>
          </div>
          {!detail || detail.decodedLogs.length === 0 ? (
            <p className="empty">暫無 decode 資料</p>
          ) : (
            <div className="decode-list">
              {detail.decodedLogs.map((item, index) => (
                <DecodedLogCard
                  key={`${item.txHash}-${item.logIndex}-${index}`}
                  item={item}
                  targetChainExplorerBaseUrl={targetChainExplorerBaseUrl}
                />
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>AI Security Analysis</h2>
            <span className={`pill ${riskTone(detail?.riskReport?.verdict ?? null)}`}>
              {detail?.riskReport?.verdict ?? "UNKNOWN"}
            </span>
          </div>
          <div className="analysis-sections">
            <div>
              <h3>Rule Analysis</h3>
              <div className="analysis-window mono">{buildRuleReportText(detail?.ruleReport ?? null)}</div>
            </div>
            <div>
              <h3>AI Analysis</h3>
              <div className="analysis-window mono">
                {detail?.riskReport?.aiModel ? buildAiReportText(detail.riskReport) : "尚無 AI 分析結果。"}
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Timeline</h2>
          </div>
          {!detail || detail.timeline.length === 0 ? (
            <p className="empty">暫無時間線資料</p>
          ) : (
            <ul className="timeline">
              {detail.timeline.map((item, index) => (
                <li key={`${item.txHash}-${index}`} className="timeline-item">
                  <div className="timeline-stage">
                    <span className={`pill ${statusTone(item.stage)}`}>{item.stage}</span>
                  </div>
                  <div className="timeline-body">
                    <p>{item.eventName ?? "Unknown Event"}</p>
                    <p className="mono">
                      Tx: {renderTxHashLink(item.txHash, item.chainId, targetChainExplorerBaseUrl)}
                    </p>
                    <p className="muted">
                      {chainLabel(item.chainId)} · block {item.blockNumber ?? "-"}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/tx/:canonicalId" element={<TxDetailPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
