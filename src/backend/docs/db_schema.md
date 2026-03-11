# 資料庫結構說明

## 文檔範圍
- Schema 來源文件：`src/backend/app/models/tables.py`
- 資料庫：SQLite（透過 SQLAlchemy ORM）
- 本文檔描述每個資料表、每個欄位的數據含義與屬性（含目前代碼的實際寫入習慣）。

## 同步規則
- `tables.py` 是 schema 的唯一來源。
- 任何對 `src/backend/app/models/tables.py` 的表結構調整（table/column/type/index/constraint）都必須在同一個提交中同步更新此文件。

## 資料表清單
- `xchain_txs`
- `xchain_timeline_events`
- `raw_logs`
- `indexer_cursors`
- `search_index`
- `risk_reports`

## 目前「雙鏈完整跨鏈交易」判斷
- 主表為：`xchain_txs`。
- 目前代碼下，`xchain_txs` 僅保留同時具備來源與目的鏈，且鏈對為 `Ethereum(1) + TARGET_CHAIN` 的交易。
- 判斷條件（同一行）：
  - `src_chain_id IS NOT NULL`
  - `dst_chain_id IS NOT NULL`
- `src_chain_id/dst_chain_id` 需符合 `{1, target_chain_id}` 配對（方向可互換）。
- 單邊或非目標鏈對事件不會寫入 `xchain_txs`（僅保留在 `raw_logs` 作鏈上原始證據）。
- 若後續 reorg 使某筆交易失去有效雙邊證據，normalizer 會同步清理主表與關聯表資料。

## `xchain_txs`
用途：統一跨鏈交易主表（一筆 canonical 交易一行）。

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `canonical_id` | `String(191)` | 否 | `PRIMARY KEY` | 跨鏈交易的全局唯一 ID。 |
| `protocol` | `String(32)` | 否 | `INDEX` | 協議名稱（`layerzero` / `wormhole`）。 |
| `src_chain_id` | `Integer` | 是 | `INDEX` | 源鏈 chain id。 |
| `src_tx_hash` | `String(80)` | 是 | `INDEX` | 源鏈交易哈希。 |
| `src_timestamp` | `DateTime(timezone=True)` | 是 | `INDEX` | 源鏈發起事件時間；若未額外查 block RPC，通常為空。 |
| `ethereum_block_number` | `BIGINT` | 是 | `INDEX` | Ethereum 主網側最早事件的區塊高度，供 latest 排序使用。 |
| `ethereum_log_index` | `Integer` | 是 | `INDEX` | Ethereum 主網側最早事件的 log index，與 `ethereum_block_number` 組合排序。 |
| `dst_chain_id` | `Integer` | 是 | `INDEX` | 目的鏈 chain id。 |
| `dst_tx_hash` | `String(80)` | 是 | `INDEX` | 目的鏈交易哈希。 |
| `dst_timestamp` | `DateTime(timezone=True)` | 是 | - | 目的鏈事件時間。 |
| `status` | `String(32)` | 否 | `INDEX` | 統一狀態（`SENT/VERIFIED/EXECUTED/FAILED/STUCK`）。 |
| `failure_category` | `String(64)` | 是 | - | 失敗分類。 |
| `latency_ms_total` | `Integer` | 是 | - | 總延遲（毫秒）。 |
| `latency_ms_verify` | `Integer` | 是 | - | 驗證階段延遲（毫秒）。 |
| `latency_ms_execute` | `Integer` | 是 | - | 執行階段延遲（毫秒）。 |
| `created_at` | `DateTime(timezone=True)` | 否 | server default | 建立時間。 |
| `updated_at` | `DateTime(timezone=True)` | 否 | server default + onupdate | 更新時間。 |

## `xchain_timeline_events`
用途：每筆跨鏈交易的時間線事件證據。

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `id` | `Integer` | 否 | `PRIMARY KEY` | 時間線事件主鍵。 |
| `canonical_id` | `String(191)` | 否 | `FK -> xchain_txs.canonical_id`, `INDEX` | 關聯主交易 ID。 |
| `stage` | `String(32)` | 否 | `INDEX` | 時間線階段（`SENT/VERIFIED/EXECUTED/FAILED`）。 |
| `chain_id` | `Integer` | 是 | `INDEX` | 事件所在鏈。 |
| `tx_hash` | `String(80)` | 是 | `INDEX` | 事件所在交易哈希。 |
| `block_number` | `BIGINT` | 是 | `INDEX` | 區塊高度。 |
| `log_index` | `Integer` | 是 | - | 該交易中的 log 序號。 |
| `event_name` | `String(64)` | 是 | - | 原始事件名。 |
| `event_ts` | `DateTime(timezone=True)` | 是 | - | 事件時間；僅當系統額外取得區塊 timestamp 時可回填。 |
| `evidence_json` | `Text` | 是 | - | 事件證據（JSON 字串）。 |

## `raw_logs`
用途：保存從 RPC 抓取的原始鏈上 logs。

唯一約束：
- `uq_raw_logs_pos`：(`chain_id`, `tx_hash`, `log_index`)

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `id` | `Integer` | 否 | `PRIMARY KEY` | 原始 log 主鍵。 |
| `protocol` | `String(32)` | 否 | `INDEX` | 該 log 對應的協議。 |
| `chain_id` | `Integer` | 否 | `INDEX` | 發生鏈。 |
| `block_number` | `BIGINT` | 否 | `INDEX` | 區塊高度。 |
| `tx_hash` | `String(80)` | 否 | `INDEX` | 交易哈希。 |
| `log_index` | `Integer` | 否 | 唯一約束成員 | 交易中的 log 序號。 |
| `block_timestamp` | `DateTime(timezone=True)` | 是 | `INDEX` | 所在區塊時間；當前默認不主動請求 block RPC，因此通常為空。 |
| `topic0` | `String(80)` | 是 | `INDEX` | 事件 topic0。 |
| `data` | `Text` | 是 | - | 原始 data 欄位。 |
| `decoded_json` | `Text` | 是 | - | 解析後內容（JSON 字串）。 |
| `removed` | `Boolean` | 否 | default | 是否因 reorg 被移除。 |

## `indexer_cursors`
用途：記錄 indexer 在每條鏈、每個協議的掃描進度。

主鍵：
- 複合主鍵：(`chain_id`, `protocol`)

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `chain_id` | `Integer` | 否 | `PRIMARY KEY` | 掃描鏈 ID。 |
| `protocol` | `String(32)` | 否 | `PRIMARY KEY` | 協議名稱。 |
| `from_block` | `BIGINT` | 是 | - | 上次掃描起始區塊。 |
| `to_block` | `BIGINT` | 是 | - | 上次掃描結束區塊。 |
| `updated_at` | `DateTime(timezone=True)` | 否 | server default + onupdate | 游標更新時間。 |

## `search_index`
用途：支援查詢 key 到 `canonical_id` 的映射。

唯一約束：
- `uq_search_index`：(`key_type`, `key_value`, `canonical_id`)

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `id` | `Integer` | 否 | `PRIMARY KEY` | 搜尋索引主鍵。 |
| `key_type` | `String(32)` | 否 | `INDEX` | 查詢鍵類型（目前實際寫入：`txHash`、`canonicalId`）。 |
| `key_value` | `String(191)` | 否 | `INDEX` | 查詢鍵值。 |
| `canonical_id` | `String(191)` | 否 | `FK -> xchain_txs.canonical_id`, `INDEX` | 關聯主交易 ID。 |
| `source` | `String(32)` | 否 | `INDEX` | 來源（`onchain` / `onchain_derived`）。 |

目前實際寫入：
- `canonicalId`
- `txHash`

目前未寫入：
- `address`

## `risk_reports`
用途：保存每筆跨鏈交易的安全分析結果（規則 + AI）。

| 欄位 | 型別 | 可為空 | 主鍵/索引/約束 | 含義 |
|---|---|---|---|---|
| `id` | `Integer` | 否 | `PRIMARY KEY` | 風險報告主鍵。 |
| `canonical_id` | `String(191)` | 否 | `FK -> xchain_txs.canonical_id`, `INDEX` | 關聯主交易 ID。 |
| `verdict` | `String(32)` | 否 | `INDEX` | 最終判定（`SAFE/WARNING/HIGH_RISK/UNKNOWN`）。 |
| `risk_score` | `Integer` | 否 | - | 風險分數（0-100）。 |
| `risk_factors_json` | `Text` | 是 | - | 風險因子列表（JSON 字串）。 |
| `analysis_summary` | `Text` | 是 | - | 人類可讀摘要。 |
| `ai_model` | `String(128)` | 是 | - | 使用的模型標識。 |
| `prompt_version` | `String(64)` | 是 | - | Prompt 版本。 |
| `analyzed_at` | `DateTime(timezone=True)` | 否 | `INDEX`, server default | 分析時間。 |

## 常用查詢（實務）
- 查「雙邊都有」的交易（不限定鏈）：
```sql
SELECT *
FROM xchain_txs
WHERE src_chain_id IS NOT NULL
  AND dst_chain_id IS NOT NULL;
```

- 查「Ethereum + TARGET_CHAIN」雙邊交易（target 以實際 chain_id 替換）：
```sql
SELECT *
FROM xchain_txs
WHERE src_chain_id IS NOT NULL
  AND dst_chain_id IS NOT NULL
  AND (
    (src_chain_id = 1 AND dst_chain_id = :target_chain_id)
    OR
    (src_chain_id = :target_chain_id AND dst_chain_id = 1)
  );
```

## 實作備註
- `updated_at` 會作為 `STUCK` 超時判斷的主要時間基準。
- `risk_reports` 不是歷史版本表；目前代碼會更新同一筆交易的最新分析結果。
- `latency_ms_total`、`latency_ms_verify`、`latency_ms_execute` 欄位目前仍保留為空。
- `eth_getLogs` 的 log 本身不包含區塊 timestamp，因此若不額外調 `eth_getBlockByNumber`，`src_timestamp/event_ts/block_timestamp` 會保持為空。
- `latest` 目前按 `ethereum_block_number DESC, ethereum_log_index DESC` 排序；這表示按 Ethereum 主網側事件位置排序，而不是按 UTC 時間排序。
