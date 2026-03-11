import type { GlobalStats, TxDetail, XChainTxSummary } from "./types";

export type LatestCategory = "total" | "executed" | "in_progress" | "attention";
export type ProtocolFilter = "all" | "layerzero" | "wormhole";

export async function fetchLatest(
  category: LatestCategory = "total",
  protocol: ProtocolFilter = "all",
): Promise<XChainTxSummary[]> {
  const params = new URLSearchParams({ limit: "50" });
  if (category !== "total") {
    params.set("category", category);
  }
  if (protocol !== "all") {
    params.set("protocol", protocol);
  }
  const resp = await fetch(`/api/latest?${params.toString()}`);
  if (!resp.ok) {
    throw new Error(`latest request failed: ${resp.status}`);
  }
  const data = await resp.json();
  return data.items ?? [];
}

export async function fetchGlobalStats(): Promise<GlobalStats> {
  const resp = await fetch("/api/stats");
  if (!resp.ok) {
    throw new Error(`stats request failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchTx(canonicalId: string): Promise<TxDetail> {
  const resp = await fetch(`/api/tx/${encodeURIComponent(canonicalId)}`);
  if (!resp.ok) {
    throw new Error(`tx request failed: ${resp.status}`);
  }
  return resp.json();
}

export async function searchTx(query: string): Promise<XChainTxSummary[]> {
  const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=20`);
  if (!resp.ok) {
    throw new Error(`search request failed: ${resp.status}`);
  }
  const data = await resp.json();
  return data.items ?? [];
}
