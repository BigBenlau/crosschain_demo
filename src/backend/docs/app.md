# `app` 目錄實作說明

## 目的
- 本文件描述 `src/backend/app` 各模組的職責、資料流與相依關係。
- 目標是讓新加入的開發者可快速定位功能與修改點。

## 目錄總覽
- `main.py`：FastAPI 入口、生命週期管理（啟動 DB、啟動/停止 indexer）、`/api/health`
- `config.py`：環境變數配置中心（鏈/RPC/indexer/topic/AI/DB）
- `db.py`：SQLAlchemy engine/session 與 `init_db()`
- `models/`：ORM schema（交易、時間線、raw logs、cursor、search、risk）
- `registry/`：鏈與協議註冊表（由 config 組裝）
- `indexer/`：鏈上 RPC 掃描器（backfill + tailing）
- `decoder/`：協議事件 ABI/data 解碼與 canonical 配對欄位提取
- `normalizer/`：`raw_logs -> xchain_txs/timeline/search_index` 正規化
- `risk/`：規則風險分析 + 可選 AI 補充
- `api/`：`latest/search/tx/stream` 路由與回應模型

## 主要資料流（執行順序）
1. `main.py` 啟動，先 `init_db()` 建表，再啟動 `indexer_service`
2. `indexer/service.py` 每輪：
   - 讀取 `registry/chains.py`、`registry/protocols.py`
   - RPC 拉取 logs，透過 `decoder/service.py` 解析，再寫入 `raw_logs`
   - 更新 `indexer_cursors`
3. 同一輪內呼叫 `normalizer/service.py`：
   - 將 `raw_logs` 聚合為 `xchain_txs`
   - 建立 `xchain_timeline_events` 與 `search_index`
   - 套用 `STUCK` 規則
4. 接著呼叫 `risk/service.py`：
   - 先規則判定風險
   - 若有 `AI_API_KEY`，補充 AI 摘要
   - 寫入 `risk_reports`
5. `api/routes.py` 對外提供查詢與 SSE 推送

## 模組分工細節

### `config.py`
- 所有運行參數都從這裡輸出 `settings`
- 新增配置時，需同步：
  - `src/backend/.env.example`
  - 涉及模組的使用邏輯

### `models/tables.py`
- 是資料結構單一來源（schema source of truth）
- 修改任何 table/column/index/constraint 時，必須同步更新：
  - `src/backend/docs/db_schema.md`

### `registry/chains.py`
- 固定包含兩條鏈：
  - `ethereum`（`chain_id=1`）
  - `TARGET_CHAIN`（來自環境配置）

### `registry/protocols.py`
- 提供 LayerZero / Wormhole topic 配置
- 支援階段化 topic：
  - `SENT/VERIFIED/EXECUTED/FAILED`
- 若未提供階段化配置，會回退到舊版 `*_TOPIC0S`

### `indexer/service.py`
- 負責鏈上讀取，嚴格不使用協議 offchain 資料源
- 核心能力：
  - safe head 計算（`latest - finality_depth`）
  - chunk 掃描
  - raw log 去重 upsert（`chain_id + tx_hash + log_index`）
  - 寫入 `decoded_json`（協議事件 decode 結果）
  - cursor 前進與健康快照

### `decoder/service.py`
- 使用 ABI decode 解讀 LayerZero / Wormhole 事件 data
- 輸出 `canonical_hint`（如 `guid`、`src_eid/sender/nonce`、`emitter_chain_id/emitter_address/sequence`）
- 用於後續 normalizer 生成跨鏈可配對 `canonical_id`
- 事件簽名與 decode type 配置抽離到：
  - `src/backend/app/decoder/abis/layerzero_events.json`
  - `src/backend/app/decoder/abis/wormhole_events.json`

### `normalizer/service.py`
- 將協議事件映射為統一狀態：
  - `SENT/VERIFIED/EXECUTED/FAILED/STUCK`
- canonical id 生成策略：
  - LayerZero 優先 `lz:{srcEid}:{sender}:{nonce}`，若欄位不足才回退 `lz:{guid}`
  - Wormhole 使用 `wormhole:{emitterChainId}:{emitterAddress}:{sequence}`
  - 若解碼不足則回退 `protocol:chain_id:tx_hash:log_index`
- 僅將 `Ethereum + TARGET_CHAIN` 雙邊交易寫入 `xchain_txs`，單邊或非目標鏈對事件不會進入主表。

### `risk/service.py`
- 規則引擎優先，AI 為可選補充
- 主要輸出：
  - `verdict`（`SAFE/WARNING/HIGH_RISK/UNKNOWN`）
  - `risk_score`（0-100）
  - `risk_factors_json`、`analysis_summary`

### `api/routes.py`
- `GET /api/latest`
- `GET /api/search`
- `GET /api/tx/{canonicalId}`
- `GET /api/stream`（SSE；每次 indexer cycle 完成後推送 `items + insertedCanonicalIds`）
- API model 由 `api/schemas.py` 管理

`/api/stream` 事件格式（`StreamLatestEvent`）：
- `event`：固定 `indexer_cycle`
- `cycleSeq`：索引器輪次序號
- `insertedCanonicalIds`：本輪變更且目前仍在 latest 列表中的交易 id
- `items`：最新交易摘要列表（供前端直接渲染）

## 常見修改場景
- 新增協議事件：
  1. 更新 `.env` topic 配置
  2. 更新 `registry/protocols.py` 映射
  3. 更新 `normalizer/service.py` 狀態映射規則
- 調整風險策略：
  1. 修改 `risk/service.py` 規則判定
  2. 視需要調整 API 輸出模型 `api/schemas.py`
- 新增查詢欄位：
  1. 擴充 `models/tables.py`
  2. 同步 `db_schema.md`
  3. 更新 `api/routes.py` 與前端取值

## 新人閱讀順序（建議）
1. `main.py`
2. `config.py`
3. `indexer/service.py`
4. `normalizer/service.py`
5. `risk/service.py`
6. `api/routes.py`
7. `models/tables.py` + `docs/db_schema.md`
