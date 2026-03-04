import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { fetchLatest, fetchTx, searchTx } from "./api";
import type { RiskReport, StreamLatestEvent, TxDetail, XChainTxSummary } from "./types";

function shortHash(value: string | null, head = 10, tail = 8): string {
  if (!value) {
    return "-";
  }
  if (value.length <= head + tail + 3) {
    return value;
  }
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
}

function formatTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
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
    `Score: ${report.score}`,
    report.analyzedAt ? `Analyzed At: ${new Date(report.analyzedAt).toLocaleString()}` : "Analyzed At: -",
    "",
    "Summary:",
    report.summary ?? "無摘要",
    "",
    "Risk Factors:",
    report.factors.length > 0 ? report.factors.map((item) => `- ${item}`).join("\n") : "- 無",
  ];
  return lines.join("\n");
}

function DashboardPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<XChainTxSummary[]>([]);
  const [query, setQuery] = useState("");
  const [errorText, setErrorText] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [animatedIds, setAnimatedIds] = useState<string[]>([]);

  const overview = useMemo(() => {
    const total = items.length;
    const executed = items.filter((item) => item.status === "EXECUTED").length;
    const riskPending = items.filter((item) => item.status === "SENT" || item.status === "VERIFIED").length;
    const attention = items.filter((item) => item.status === "FAILED" || item.status === "STUCK").length;
    return { total, executed, riskPending, attention };
  }, [items]);

  useEffect(() => {
    void loadLatest();
  }, []);

  useEffect(() => {
    const animationTimers = new Set<number>();
    const source = new EventSource("/api/stream");
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
        setErrorText("");
      } catch (error) {
        setErrorText(String(error));
      }
    };
    source.onerror = () => {
      source.close();
      setTimeout(() => {
        void loadLatest();
      }, 3000);
    };
    return () => {
      source.close();
      for (const timer of animationTimers) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  async function loadLatest() {
    setLoadingList(true);
    try {
      const latest = await fetchLatest();
      setItems(latest);
      setErrorText("");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setLoadingList(false);
    }
  }

  async function onSearchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) {
      void loadLatest();
      return;
    }
    setLoadingList(true);
    try {
      const result = await searchTx(query.trim());
      setItems(result);
      setErrorText("");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setLoadingList(false);
    }
  }

  function clearSearch() {
    setQuery("");
    void loadLatest();
  }

  function openDetail(canonicalId: string) {
    navigate(`/tx/${encodeURIComponent(canonicalId)}`);
  }

  return (
    <div className="page">
      <div className="bg-glow bg-glow-a" />
      <div className="bg-glow bg-glow-b" />

      <header className="hero">
        <div>
          <p className="eyebrow">Crosschain Security Dashboard</p>
          <h1>LayerZero / Wormhole Explorer</h1>
          <p className="subtitle">實時查看 Ethereum ↔ Target Chain 雙向跨鏈交易，點擊可進入安全分析詳情。</p>
        </div>

        <form onSubmit={onSearchSubmit} className="search-form">
          <input
            placeholder="搜索 txHash / canonicalId / address"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit">Search</button>
          <button type="button" className="ghost" onClick={clearSearch}>
            Reset
          </button>
        </form>
      </header>

      {errorText && <p className="error">{errorText}</p>}

      <section className="stats">
        <article className="stat-card">
          <span>Total</span>
          <strong>{overview.total}</strong>
        </article>
        <article className="stat-card">
          <span>Executed</span>
          <strong>{overview.executed}</strong>
        </article>
        <article className="stat-card">
          <span>In Progress</span>
          <strong>{overview.riskPending}</strong>
        </article>
        <article className="stat-card">
          <span>Need Attention</span>
          <strong>{overview.attention}</strong>
        </article>
      </section>

      <main className="dashboard-layout">
        <section className="panel">
          <div className="panel-header">
            <h2>Latest Stream</h2>
            <button className="ghost" onClick={() => void loadLatest()}>
              {loadingList ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          {items.length === 0 ? <p className="empty">暫無資料</p> : null}
          <ul className="tx-list">
            {items.map((item) => (
              <li key={item.canonicalId} className={animatedIds.includes(item.canonicalId) ? "is-new" : ""}>
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
                  <span className="muted">{formatTime(item.updatedAt)}</span>
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

  useEffect(() => {
    if (!canonicalId) {
      setDetail(null);
      return;
    }
    void loadDetail(canonicalId);
  }, [canonicalId]);

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
                <p className="mono">{detail.tx.srcTxHash ?? "-"}</p>
              </div>
              <div>
                <span className="muted">To Tx Hash</span>
                <p className="mono">{detail.tx.dstTxHash ?? "-"}</p>
              </div>
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>AI Security Analysis</h2>
            <span className={`pill ${riskTone(detail?.riskReport?.verdict ?? null)}`}>
              {detail?.riskReport?.verdict ?? "UNKNOWN"}
            </span>
          </div>
          <div className="analysis-window mono">{buildAiReportText(detail?.riskReport ?? null)}</div>
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
                    <p className="mono">{shortHash(item.txHash, 20, 14)}</p>
                    <p className="muted">
                      {chainLabel(item.chainId)} · block {item.blockNumber ?? "-"} · {formatTime(item.eventTs)}
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
