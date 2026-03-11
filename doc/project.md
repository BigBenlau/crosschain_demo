# 專案說明（當前實作版）

## 1. 當前範圍
- 本 demo 目前只實作兩個跨鏈協議：
  - `LayerZero V2`
  - `Wormhole Token Bridge`
- 監控鏈固定為：
  - `Ethereum`
  - `.env` 指定的 `TARGET_CHAIN`
- 數據來源只使用鏈上 RPC：
  - `eth_chainId`
  - `eth_blockNumber`
  - `eth_getLogs`

## 2. 專案目標
- 用單一 Explorer 介面展示兩協議跨鏈交易。
- 對每筆交易提供：
  - 統一狀態
  - 統一時間線
  - 失敗分類
  - 風險結論
- 支援：
  - latest 列表
  - `txHash` 搜索
  - `canonicalId` 搜索
  - 單筆詳情
  - SSE 實時刷新

## 3. 實際架構

### 後端
- `src/backend/app/main.py`
  - 啟動 API、DB、背景 indexer
- `src/backend/app/indexer/service.py`
  - 逐鏈逐協議掃描 logs
  - 寫入 `raw_logs`
  - 更新 `indexer_cursors`
- `src/backend/app/decoder/service.py`
  - 解 LayerZero / Wormhole 事件
  - 產出 `canonical_hint` 與方向資訊
- `src/backend/app/normalizer/service.py`
  - 將 raw logs 聚合成 `xchain_txs`
  - 建立 timeline 與 search index
  - 只保留 `Ethereum <-> TARGET_CHAIN` 的完整雙邊交易
  - 對失效 canonical id 做清理
- `src/backend/app/risk/service.py`
  - 先做規則評分
  - 再將交易放入待檢查池
  - 由背景 worker 按批次調用 AI 對多筆交易輸出風險結論
  - 默認對接智譜免費模型 `GLM-4.7-Flash`
  - 風險分數範圍 `0-100`，越高越安全
- `src/backend/app/api/routes.py`
  - 提供 `latest/search/detail/stream/stats`
  - `stats` 會返回全局統計與 `LayerZero / Wormhole` 拆分統計
  - `detail` 會返回 timeline 與 decode 明細

### 前端
- `src/frontend/src/App.tsx`
  - 包含 Dashboard 與 Tx Detail 路由頁
  - Dashboard 的統計卡會同時展示總數與 `LayerZero / Wormhole` 拆分數
  - Dashboard 支援協議標簽切換 latest top 50：`All / LayerZero / Wormhole`
  - Tx Detail 的 from/to/timeline `tx_hash` 可跳轉對應 explorer event log 頁
  - Tx Detail 會展示每個事件的 raw data 與 decoded JSON
- `src/frontend/src/api.ts`
  - 封裝 `fetchLatest / searchTx / fetchTx`
- `src/frontend/src/types.ts`
  - 對齊後端 response schema
- `src/frontend/src/styles.css`
  - 儀表板樣式、timeline、響應式規則

## 4. 當前協議映射

### LayerZero
- `PacketSent -> SENT`
- `PacketVerified -> VERIFIED`
- `PacketDelivered -> EXECUTED`
- `LzReceiveAlert -> FAILED`

### Wormhole
- `LogMessagePublished -> SENT`
- `TransferRedeemed -> EXECUTED`

## 5. 當前 canonical id
- LayerZero
  - 優先 `lz:{srcEid}:{sender}:{nonce}`
  - 次選 `lz:{guid}`
  - 失敗時 fallback 到 `lz:{chain_id}:{tx_hash}:{log_index}`
- Wormhole
  - `wormhole:{emitterChainId}:{emitterAddress}:{sequence}`
  - 失敗時 fallback 到 `wormhole:{chain_id}:{tx_hash}:{log_index}`

## 6. 當前資料表
- `xchain_txs`
- `xchain_timeline_events`
- `raw_logs`
- `indexer_cursors`
- `search_index`
- `risk_reports`

## 7. 當前限制
- 文檔與 UI 中提到的 `address` 搜索尚未真正落地索引。
- `latency_ms_total / latency_ms_verify / latency_ms_execute` 尚未計算。
- `event_ts` 尚未根據區塊時間回填。
- `.env.example` 內地址僅適配 `Ethereum <-> Arbitrum`。
- 前端鏈名映射目前只內建 `Ethereum / Arbitrum`。
- AI 評審已改為單進程背景 worker，但尚未使用外部消息隊列或分散式任務系統。
