# Cross-Protocol Cross-Chain Explorer Demo 實作計劃（FastAPI + React）

## 摘要
- 目標：建立單一 Explorer，僅追蹤 `LayerZero V2` 與 `Wormhole Token Bridge` 兩種跨鏈協議，並對每筆跨鏈交易提供統一 `timeline/status/latency` 與 AI 安全分析結果。
- 技術選型：`Python + FastAPI`（後端）、`React + TypeScript + Vite`（前端）、`SQLite`（儲存）、背景 indexer（實時監控）。
- 數據來源策略：`全鏈上模式`，僅使用 Node RPC（`eth_getLogs`、`eth_getTransactionReceipt`、`eth_getBlockByNumber` 等）輸出數據，不使用任何協議 offchain 數據。
- 觀察鏈集合：`Ethereum + 另一條鏈（待定）`，第二條鏈名稱與 RPC 透過配置注入（`TARGET_CHAIN`、`TARGET_CHAIN_RPC_URL`）。
- 核心能力：
  - `實時監控`：持續監控新區塊並推送跨鏈交易狀態變化。
  - `安全分析`：對每筆跨鏈交易執行 AI 風險判定，輸出「是否安全」與「具體風險點」。

## 成功標準（驗收口徑）
- 僅支援 `LayerZero` 與 `Wormhole`，且兩協議事件都可被成功識別與標準化。
- 監控範圍為兩條鏈（`Ethereum + TARGET_CHAIN`），可從歷史區塊回填並切換到實時追蹤。
- latest stream 可在新跨鏈事件出現後 `<= 15 秒` 內更新（含後端入庫與前端展示）。
- 跨鏈交易資料來源僅限鏈上 RPC；不得接入協議官方 API、Indexer API、Explorer API 作為交易數據輸入。
- 每筆交易詳情頁都包含：
  - 統一時間線（來源事件、目的鏈事件）
  - 統一狀態（`SENT/VERIFIED/EXECUTED/FAILED/STUCK`）
  - AI 安全結論（`SAFE/WARNING/HIGH_RISK/UNKNOWN`）
  - 風險原因清單與建議動作
- 支援 `txHash`、`canonicalId`、`address` 檢索。

## 系統架構與模組
- `backend/app/main.py`：FastAPI app、路由掛載、背景任務啟動、健康檢查。
- `backend/app/config.py`：環境變數、鏈配置（含 `TARGET_CHAIN`）、AI 服務配置。
- `backend/app/db.py`：SQLite 連線與 schema 初始化。
- `backend/app/models/`：Pydantic API 模型與 SQLAlchemy ORM。
- `backend/app/registry/chains.py`：`Ethereum + TARGET_CHAIN` RPC、finality depth、鏈 metadata。
- `backend/app/registry/protocols.py`：僅 `LayerZero`、`Wormhole` 合約地址、ABI、event topic。
- `backend/app/indexer/`：回填與實時掃描、cursor 管理、reorg 防護、增量入庫。
- `backend/app/normalizer/`：事件標準化為 `XChainTx`、狀態機、canonical join。
- `backend/app/risk/`：
  - 規則引擎（rule-based pre-check）
  - AI 風險分析 client（調用 LLM）
  - 風險報告聚合器（輸出最終 verdict）
- `backend/app/api/`：`/search`、`/tx/{canonicalId}`、`/latest`、`/stream`、`/health`。
- `frontend/src/`：列表頁、詳情頁、時間線、風險報告卡片、實時流更新。

## 資料模型（決策完成版）
- `xchain_txs`
- 欄位：`canonical_id (PK)`、`protocol`、`src_chain_id`、`src_tx_hash`、`src_timestamp`、`dst_chain_id`、`dst_tx_hash`、`dst_timestamp`、`status`、`failure_category`、`latency_ms_total`、`latency_ms_verify`、`latency_ms_execute`、`created_at`、`updated_at`。
- `xchain_timeline_events`
- 欄位：`id`、`canonical_id (FK)`、`stage`、`chain_id`、`tx_hash`、`block_number`、`log_index`、`event_name`、`event_ts`、`evidence_json`。
- `raw_logs`
- 欄位：`id`、`protocol`、`chain_id`、`block_number`、`tx_hash`、`log_index`、`topic0`、`data`、`decoded_json`、`removed`。
- `indexer_cursors`
- 欄位：`chain_id`、`protocol`、`from_block`、`to_block`、`updated_at`。
- `search_index`
- 欄位：`key_type`(`txHash|canonicalId|address`)、`key_value`、`canonical_id`、`source`(`onchain|onchain_derived`)。
- `risk_reports`
- 欄位：`id`、`canonical_id (FK)`、`verdict`(`SAFE|WARNING|HIGH_RISK|UNKNOWN`)、`risk_score`(0-100)、`risk_factors_json`、`analysis_summary`、`ai_model`、`prompt_version`、`analyzed_at`。

## canonicalId 與狀態映射
- `LayerZero`：`lz:{guid}`；無 guid 時用 fallback `lz:{srcEid}:{sender}:{dstEid}:{receiver}:{nonce}`。
- `Wormhole`：`wormhole:{emitterChainId}:{emitterAddress}:{sequence}`。
- 狀態映射：
  - LayerZero：`PacketSent -> SENT`、`PacketVerified -> VERIFIED`、`PacketDelivered -> EXECUTED`。
  - Wormhole：`LogMessagePublished -> SENT`、`TransferRedeemed -> EXECUTED`。
  - 通用：超時規則將交易標記為 `STUCK`，鏈上可觀測失敗則標記 `FAILED`。

## 實時監控流程
- 啟動後先執行歷史回填：每鏈每協議從配置 `start_block` 掃到 `safe_head`。
- 回填完成後切入 tailing：每 `15s` 讀取新區塊並拉取關聯 logs。
- 僅允許 RPC 查詢：`eth_getLogs`、`eth_getTransactionReceipt`、`eth_getBlockByNumber`；不調用任何協議 offchain 端點。
- 去重鍵：`(chain_id, tx_hash, log_index)`；遇到 `removed=true` 做回滾。
- 每次新增或更新 `XChainTx` 後：
  - 寫入 DB
  - 觸發風險分析任務
  - 發送 stream 更新事件到前端（SSE 或 WebSocket）。

## 安全分析（AI）流程
- 觸發時機：
  - 新交易產生時立即分析一次。
  - 狀態從 `SENT/VERIFIED` 變化到 `EXECUTED/FAILED/STUCK` 時重新分析。
- 輸入特徵：
  - 僅使用鏈上與鏈上衍生特徵：協議類型、跨鏈方向、事件序列完整性、延遲、重試/回滾跡象、關聯地址行為特徵。
- 分析管線：
  - 先跑 rule-based pre-check（硬規則快速標記高風險）
  - 再調用 AI 模型生成風險解釋
  - 聚合為最終 verdict + score + risk factors
- 輸出格式：
  - `verdict`：`SAFE/WARNING/HIGH_RISK/UNKNOWN`
  - `risk_score`：0-100
  - `risk_factors`：可讀風險條目（例如「目的鏈未兌現超時」、「異常延遲」、「可疑地址關聯」）
  - `recommended_actions`：人工複核建議。

## 公開 API / 介面變更（重要）
- `GET /api/search?q=<string>&limit=<int=20>`
- 回傳 `SearchResponse { items, total, nextCursor? }`。
- `GET /api/tx/{canonicalId}`
- 回傳 `XChainTxDetail { tx, timeline[], latency, failure, riskReport }`。
- `GET /api/latest?protocol=&status=&srcChain=&dstChain=&cursor=&limit=50`
- 回傳 `LatestResponse { items, nextCursor? }`。
- `GET /api/stream`
- Server-Sent Events，推送交易新增/狀態變更/風險報告更新。
- `GET /api/health`
- 回傳 `HealthResponse { api, db, indexer, ai, lastIndexedBlockByChain }`。

## 前端實作範圍
- `頁面 1`：實時 latest stream（列表 + 協議/狀態/鏈過濾 + 搜索框）。
- `頁面 2`：交易詳情（統一 timeline、狀態、延遲分解、AI 風險報告）。
- `UI 元件`：`SearchBar`、`ProtocolBadge`、`StatusPill`、`Timeline`、`RiskBadge`、`RiskPanel`。
- `資料層`：React Query + SSE 訂閱；列表與詳情能即時刷新。
- `風險展示`：分開顯示 `AI Verdict`、`Risk Factors`、`Recommended Actions`。

## 實作里程碑（可直接執行）
1. 搭建 FastAPI + React + SQLite 基礎骨架與配置管理。
2. 完成雙鏈 registry（Ethereum + `TARGET_CHAIN`）與雙協議 registry（LayerZero、Wormhole）。
3. 完成 indexer（歷史回填 + 實時 tailing + cursor + reorg 回滾）。
4. 完成 normalizer（canonicalId、狀態機、timeline、latency）。
5. 完成風險分析引擎 v1（rule-based + AI 調用 + 報告落庫）。
6. 完成 API（`/search`、`/tx/{id}`、`/latest`、`/stream`、`/health`）。
7. 完成前端列表頁與詳情頁，接入實時流與風險報告展示。
8. 輸出 demo 操作說明與安全分析示例（安全、警告、高風險三類）。

## MVP 分步開發節奏（每次小步提交）
- Step 1：建立可啟動骨架（`src/backend`、`src/frontend`）與 `health` 檢查。
- Step 2：落地資料庫 schema（`xchain_txs/raw_logs/indexer_cursors/risk_reports`）與初始化流程。
- Step 3：接入 LayerZero/Wormhole 事件 topic 與最小 indexer（先 backfill 再 tailing）。
- Step 4：完成最小 normalizer（`canonicalId + status`）並可產生 latest stream。
- Step 5：接入最小 AI 風險分析（rule-based + LLM）並寫入 `risk_reports`。
- Step 6：完成最小前端（latest 列表 + tx 詳情 + risk panel + SSE 更新）。
- Step 7：整理 demo 指南與已知限制，按需求持續微調 `plan.md`。

## 風險與對策
- 歷史數據覆蓋不完整：為兩條鏈設定明確 `start_block`，並在 UI 顯示覆蓋時間窗口。
- 實時延遲偏高：縮小掃塊 chunk、分協議並行拉取、異步風險分析隊列化。
- AI 誤判/不穩定：引入 rule-based hard checks、固定 prompt version、保存分析輸入與輸出供審計。
- RPC 限流或短暫故障：retry/backoff + provider fallback，保障監控連續性。
- 鏈上可觀測性限制：部分中間態在鏈上不可見，UI 需標示「僅基於鏈上證據判定」。

## 明確假設與預設
- 僅支援兩協議：`LayerZero`、`Wormhole`。
- 僅支援兩條鏈：`Ethereum + TARGET_CHAIN`；`TARGET_CHAIN` 在啟動前確定。
- 兩條鏈的 `start_block` 必須由配置提供：`ETH_START_BLOCK`、`TARGET_CHAIN_START_BLOCK`。
- 協議事件 topic 由配置提供：`LAYERZERO_*_TOPICS`、`WORMHOLE_SENT_TOPICS`、`WORMHOLE_EXECUTED_TOPICS`。
- 僅支援 `local dev`，不包含雲端部署交付。
- 本期聚焦功能實現，不包含測試案例設計與實作。
- 不考慮協議版本更新與地址漂移，以既有歷史數據與實時增量數據展示為主。
- 交易數據只來自 Node RPC，不使用任何協議 offchain 數據源。
- DB 文檔同步規則：修改 `src/backend/app/models/tables.py` 時，必須同步更新 `src/backend/docs/db_schema.md`。
- Python 文檔規則：現有與後續新增的 Python 檔案，檔案開頭都需有中文簡介；若單檔超過 100 行，需為每個 `class` 與 `function` 補充中文注釋簡介。
