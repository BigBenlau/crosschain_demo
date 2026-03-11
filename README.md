# Crosschain MVP Demo

## 專案目標
- 僅支援 `LayerZero` 與 `Wormhole`
- 僅使用鏈上 RPC 數據（無協議 offchain API）
- 提供最小可用功能：`latest`、`search`、`tx detail`、`SSE stream`、風險分析

## 目錄結構
- `src/backend`：FastAPI + SQLite + Indexer + Normalizer + Risk
- `src/frontend`：React + Vite 最小展示介面
- `plan.md`：開發計劃與規範

## Backend 啟動
```bash
cd src/backend
cp .env.example .env
# 填入 ETH_RPC_URL、TARGET_CHAIN、TARGET_CHAIN_RPC_URL 等配置
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 由範本生成 `.env`
```bash
cd src/backend
python scripts/env_from_sample.py --force
```

可帶覆寫參數：
```bash
python scripts/env_from_sample.py --force \
  --set API_KEY=your_key \
  --set ETH_RPC_URL=https://rpc.ankr.com/eth/your_key \
  --set TARGET_CHAIN_RPC_URL=https://rpc.ankr.com/arbitrum/your_key
```

後端啟動後：
- Health: `http://127.0.0.1:8000/api/health`
- API docs: `http://127.0.0.1:8000/docs`

## Frontend 啟動
```bash
cd src/frontend
npm install
npm run dev
```

前端開啟：
- `http://127.0.0.1:5173`

## 必填配置（.env）
- `ETH_RPC_URL`
- `TARGET_CHAIN`
- `TARGET_CHAIN_RPC_URL`
- `TARGET_CHAIN_EXPLORER_BASE_URL`
- `ETH_START_BLOCK`
- `TARGET_CHAIN_START_BLOCK`
- `INDEXER_POLL_SECONDS=15`
- `LAYERZERO_*_TOPICS`
- `WORMHOLE_SENT_TOPICS`
- `WORMHOLE_EXECUTED_TOPICS`
- `LAYERZERO_*_ENDPOINTS`
- `WORMHOLE_*_CORE_CONTRACTS`
- `WORMHOLE_*_TOKEN_BRIDGES`

說明：
- `TARGET_CHAIN_ID` 不再需要手動配置，系統會從 `TARGET_CHAIN_RPC_URL` 呼叫 `eth_chainId` 自動取得。
- `TARGET_CHAIN_EXPLORER_BASE_URL` 用於前端 tx detail 跳轉目標鏈 explorer，鏈接格式固定為 `/tx/{tx_hash}#eventlog`。
- `.env.example` 內的協議地址示例只適用於 `Ethereum <-> Arbitrum` 主網組合；如果更換 `TARGET_CHAIN`，必須同步替換相關合約地址。

可選：
- `AI_API_KEY`
- `AI_BASE_URL`
- `AI_MODEL`
- `AI_BATCH_SIZE`
- `AI_BATCH_MAX_SIZE`
- `AI_MAX_PROMPT_CHARS`
- `AI_MAX_OUTPUT_TOKENS`
- `AI_TEMPERATURE`

AI 默認配置：
- 默認走智譜 OpenAI-compatible 接口
- 默認模型：`glm-4.7-flash`
- 默認批量分析：`5` 筆 / 批，最大 `10` 筆 / 批
- AI 評審由背景異步 worker 從待檢查池中批量取交易執行
- AI 分數為 `0-100`，分數越高表示越安全，分數越低表示風險越高

Topic 參考：
- `src/backend/docs/protocol_topics.md`
- `src/backend/docs/app.md`

## 已知限制（MVP）
- canonical id 目前採 fallback 策略（`chain_id + tx_hash + log_index`）。
- address 搜索需要鏈上可觀測資料，目前僅建立 canonicalId/txHash 索引。
- 目前僅使用 `eth_getLogs`，log 本身不含區塊 timestamp，因此 `event_ts` 不可直接由 log 得出。
- AI 風險評審目前是單進程背景 worker，尚未接入外部消息隊列。

## Dashboard 篩選
- Dashboard 的 `Total / Executed / In Progress / Need Attention` 統計卡可點擊。
- 每張統計卡除了總數，也會顯示 `LayerZero / Wormhole` 各自的數量拆分。
- 點擊後前端會顯示該類別對應的 top 50 交易。
- Dashboard 另提供協議篩選標簽，可切換：
  - `All`
  - `LayerZero`
  - `Wormhole`
- 後端對應接口：
  - `GET /api/stats`
  - `GET /api/latest?category=executed|in_progress|attention&protocol=layerzero|wormhole`
  - `GET /api/stream?category=executed|in_progress|attention&protocol=layerzero|wormhole`
- 類別口徑：
  - `Total`：全部交易
  - `Executed`：`status = EXECUTED`
  - `In Progress`：`status in (SENT, VERIFIED)`
  - `Need Attention`：`status in (FAILED, STUCK)`
- `/api/stats` 會同時返回：
  - 全局總數
  - `byProtocol.layerzero`
  - `byProtocol.wormhole`
