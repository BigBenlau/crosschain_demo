import type { TxDetail, XChainTxSummary } from "./types";

export async function fetchLatest(): Promise<XChainTxSummary[]> {
  const resp = await fetch("/api/latest?limit=50");
  if (!resp.ok) {
    throw new Error(`latest request failed: ${resp.status}`);
  }
  const data = await resp.json();
  return data.items ?? [];
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
