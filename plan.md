# Cross-Protocol Cross-Chain Explorer Demo 當前實作快照

## 1. 專案定位
- 本項目目前是 `雙鏈 + 雙協議` 的 MVP。
- 後端使用 `FastAPI + SQLAlchemy + SQLite`。
- 前端使用 `React 18 + TypeScript + Vite`。
- 數據來源嚴格限制為 `EVM RPC`，不接入協議官方 API。
- 目前實作協議只有：
  - `LayerZero V2`
  - `Wormhole Token Bridge`

## 2. 當前可用能力
- 最新交易流：`GET /api/latest`
- 搜索：`GET /api/search`
- 單筆詳情：`GET /api/tx/{canonicalId}`
- 實時推送：`GET /api/stream`（SSE）
- 健康檢查：`GET /api/health`
- 風險分析：規則判定 + 可選批量 AI 評審
- AI 默認配置走智譜免費模型 `glm-4.7-flash`
- AI 風險分數範圍 `0-100`，越高越安全

## 3. 當前監控範圍
- 固定一條源鏈：`Ethereum`
- 固定一條目標鏈：`TARGET_CHAIN`
- `TARGET_CHAIN` 由 `.env` 提供鏈名稱與 RPC URL
- 目標鏈 `chain_id` 由 `TARGET_CHAIN_RPC_URL` 動態透過 `eth_chainId` 取得

## 4. 當前資料流
1. `src/backend/app/main.py` 啟動應用，先建表，再啟動背景 indexer。
2. `src/backend/app/indexer/service.py` 依鏈與協議掃描 logs，寫入 `raw_logs` 與 `indexer_cursors`。
3. `src/backend/app/decoder/service.py` 解析 LayerZero / Wormhole 事件，產出 `decoded_json`。
4. `src/backend/app/normalizer/service.py` 按受影響 canonical id 增量重建 `xchain_txs`、`xchain_timeline_events`、`search_index`。
5. `src/backend/app/risk/service.py` 對本輪變更交易產出 `risk_reports`。
6. `src/backend/app/maintenance/service.py` 週期性清理 `raw_logs`、歸檔 `FAILED`、並按策略執行 SQLite `VACUUM`。
7. `src/backend/app/api/routes.py` 對外提供查詢與 SSE。

## 5. 目錄與模組
- `src/backend/app/main.py`
  - FastAPI 入口
  - lifespan 內初始化資料庫與 indexer
- `src/backend/app/config.py`
  - 環境變數配置中心
- `src/backend/app/db.py`
  - SQLAlchemy engine / session / init_db
- `src/backend/app/models/`
  - ORM schema
- `src/backend/app/registry/chains.py`
  - `ethereum + target_chain` 雙鏈註冊表
- `src/backend/app/registry/protocols.py`
  - LayerZero / Wormhole 的 topic 與地址白名單
- `src/backend/app/indexer/service.py`
  - RPC 掃描、游標推進、raw log upsert
- `src/backend/app/decoder/service.py`
  - 協議事件 ABI 解碼
- `src/backend/app/normalizer/service.py`
  - canonical id、方向校驗、狀態合併、STUCK 標記、reorg 後資料清理
- `src/backend/app/risk/service.py`
  - 規則風險分析、待檢查池、背景 worker、批量 AI 評審、逐筆解析與回退
- `src/backend/app/api/`
  - response schema 與 API 路由
- `src/frontend/src/App.tsx`
  - Dashboard 與 Tx Detail 主要交互
- `src/frontend/src/api.ts`
  - 前端 API 請求封裝
- `src/frontend/src/types.ts`
  - 前端資料契約型別
- `src/frontend/src/styles.css`
  - 頁面樣式與響應式規則

## 6. 當前資料模型
- `xchain_txs`
  - 統一跨鏈交易主表
- `xchain_timeline_events`
  - 時間線事件
- `raw_logs`
  - 從 RPC 拉回的原始事件
- `indexer_cursors`
  - 每鏈每協議的掃描進度
- `search_index`
  - 搜索鍵到 `canonical_id` 的映射
- `risk_reports`
  - 風險分析結果

## 7. 當前 canonical id 規則
- LayerZero
  - 優先：`lz:{srcEid}:{sender}:{nonce}`
  - 次選：`lz:{guid}`
  - 解碼失敗 fallback：`lz:{chain_id}:{tx_hash}:{log_index}`
- Wormhole
  - 優先：`wormhole:{emitterChainId}:{emitterAddress}:{sequence}`
  - 解碼失敗 fallback：`wormhole:{chain_id}:{tx_hash}:{log_index}`

## 8. 當前狀態映射
- LayerZero
  - `PacketSent -> SENT`
  - `PacketVerified -> VERIFIED`
  - `PacketDelivered -> EXECUTED`
  - `LzReceiveAlert -> FAILED`
- Wormhole
  - `LogMessagePublished -> SENT`
  - `TransferRedeemed -> EXECUTED`
- 通用
  - 超時未推進會被標記為 `STUCK`

## 9. 當前查詢能力
- 已實作索引：
  - `canonicalId`
  - `txHash`
- 尚未實作索引：
  - `address`
- 因此 API 雖允許輸入任意 `q`，但可靠命中的只有 `canonicalId / txHash`

## 10. 當前前端實作狀態
- 已有兩個頁面：
  - Dashboard
  - Tx Detail
- Dashboard 能力：
  - latest 列表
  - 搜索
  - 手動刷新
  - SSE 實時刷新
  - 新增交易進場動畫
- Tx Detail 能力：
  - 協議、鏈路、tx hash、失敗類型
  - timeline
  - risk report
- 目前未拆分為多個 React 子元件，主要邏輯仍集中在 `App.tsx`

## 11. 當前已知限制
- 僅支援 `Ethereum + TARGET_CHAIN`
- 僅支援 `LayerZero / Wormhole`
- `event_ts` 尚未從區塊時間回填
- `latency_ms_*` 欄位已存在，但目前未實際計算
- 搜索索引尚未覆蓋 address
- 前端 `chainLabel()` 目前只對 `1` 與 `42161` 做友好名稱映射
- `.env.example` 內協議地址示例只適用於 `Ethereum <-> Arbitrum`
- AI 風險評審已改為異步背景 worker，但尚未拆成外部任務隊列

## 12. 文檔同步規則
- 調整 ORM schema 時，必須同步更新：
  - `src/backend/docs/db_schema.md`
- 調整模組職責或資料流時，必須同步更新：
  - `src/backend/docs/app.md`
- 調整前端頁面結構或交互時，必須同步更新：
  - `src/frontend/plan.md`
