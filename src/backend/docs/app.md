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
   - 將本輪變更交易送入待檢查池
   - 由背景 worker 按批次調用 AI 分析多筆交易
   - 寫入 `risk_reports`
5. `api/routes.py` 對外提供查詢與 SSE 推送

## 模組分工細節

### `config.py`
- 所有運行參數都從這裡輸出 `settings`
- 新增配置時，需同步：
  - `src/backend/.env.example`
  - 涉及模組的使用邏輯
- AI 相關配置目前包含：
  - 基礎調模：`AI_API_KEY/AI_BASE_URL/AI_MODEL/AI_TIMEOUT_SECONDS`
  - 批量控制：`AI_BATCH_SIZE/AI_BATCH_MAX_SIZE/AI_MAX_PROMPT_CHARS/AI_MAX_OUTPUT_TOKENS/AI_TEMPERATURE`

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
- 若未提供階段化配置，會按已知 topic 簽名回退到對應 stage
- Wormhole 地址白名單同時包含：
  - core contracts
  - token bridge contracts
- Wormhole 的 sender filter 僅用於 `SENT` 事件額外過濾非 bridge sender
- 若 Wormhole 的 `emitter_chain_id` / `src_wormhole_chain_id` / `dst_wormhole_chain_id` 不屬於 `Ethereum + TARGET_CHAIN`，會在 indexer 階段直接跳過，不寫入資料庫

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
  - LayerZero 優先 `lz:{srcEid}:{sender}:{nonce}`，其次 `lz:{guid}`
  - Wormhole 使用 `wormhole:{emitterChainId}:{emitterAddress}:{sequence}`
  - 若解碼不足則回退 `protocol:chain_id:tx_hash:log_index`
- 若來源鏈發起事件已能由 decode 明確確認為 `Ethereum <-> TARGET_CHAIN`，會先寫入 `xchain_txs` 並以 `SENT` 顯示為 in progress。
- 目的鏈事件到達後，會沿用同一 canonical id 合併到既有記錄並推進為 `VERIFIED / EXECUTED / FAILED`。
- 非目標鏈對、方向衝突或完全無法確認方向的事件不會進入主表。
- `eth_getLogs` 的返回不包含區塊 timestamp，因此不額外調 block RPC 時，`src_timestamp/event_ts` 通常為空。
- `latest` 會按 Ethereum 主網側最早事件的 `(block_number, log_index)` 倒序排序。
- 會將 `raw_logs.data` 與 `raw_logs.decoded_json` 同步到 `xchain_timeline_events`，供 detail 頁展示 decode 結果。
- 若 reorg 或方向校驗後交易失去有效雙邊證據，會清理對應：
  - `xchain_txs`
  - `xchain_timeline_events`
  - `search_index`
  - `risk_reports`
- `STUCK` 判定基準使用主交易最近一次更新時間，而非初次建立時間

### `risk/service.py`
- 規則引擎提供基線觀察與回退
- 內建待檢查池與背景 worker，避免阻塞 indexer 主循環
- 若配置 AI，會按批量方式評審多筆交易
- 默認 provider 配置對齊智譜 OpenAI-compatible 接口
- 默認模型為免費的 `glm-4.7-flash`
- 主要輸出：
  - `verdict`（`SAFE/WARNING/HIGH_RISK/UNKNOWN`）
  - `risk_score`（0-100，越高越安全）
  - `risk_factors_json`、`analysis_summary`
- 模型輸出要求：
  - 按 `TX-1 ... TX-N` 分段
  - 每段必須包含 `canonical_id`
  - 逐筆解析，單筆失敗單筆回退

`/api/health` 額外提供 `risk` 狀態：
- `running`
- `pendingCount`
- `lastError`
- `lastEnqueuedIds`
- `lastCompletedIds`

### `api/routes.py`
- `GET /api/latest`
- `GET /api/latest?category=executed|in_progress|attention&protocol=layerzero|wormhole`
- `GET /api/stats`
- `GET /api/search`
- `GET /api/tx/{canonicalId}`
- `GET /api/stream`（SSE；每次 indexer cycle 完成後推送 `items + insertedCanonicalIds`）
- `GET /api/stream?category=executed|in_progress|attention&protocol=layerzero|wormhole`
- API model 由 `api/schemas.py` 管理
- tx detail 會返回 `decodedLogs`，包含每個事件的 raw data 與 decoded JSON
- tx detail 的 `timeline/decodedLogs` 會按跨鏈流程順序展示，先發生的事件在上、後發生的事件在下
- `latest/stream` 目前按 Ethereum 主網側 `(block_number, log_index)` 倒序排序
- `stats` 會同時返回全局統計與 `byProtocol.layerzero / byProtocol.wormhole`
- `category` 過濾口徑：
  - `total` 或空值：不過濾
  - `executed`：`status = EXECUTED`
  - `in_progress`：`status in (SENT, VERIFIED)`
  - `attention`：`status in (FAILED, STUCK)`
- `protocol` 過濾口徑：
  - 空值或 `all`：不過濾
  - `layerzero`
  - `wormhole`
- `GET /api/search` 目前實際可穩定命中的 key 為：
  - `canonicalId`
  - `txHash`
- address 搜索尚未建立對應索引

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
