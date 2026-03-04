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
- `ETH_START_BLOCK`
- `TARGET_CHAIN_START_BLOCK`
- `INDEXER_POLL_SECONDS=15`
- `LAYERZERO_*_TOPICS`
- `WORMHOLE_SENT_TOPICS`
- `WORMHOLE_EXECUTED_TOPICS`

說明：
- `TARGET_CHAIN_ID` 不再需要手動配置，系統會從 `TARGET_CHAIN_RPC_URL` 呼叫 `eth_chainId` 自動取得。

可選：
- `AI_API_KEY`
- `AI_BASE_URL`
- `AI_MODEL`

Topic 參考：
- `src/backend/docs/protocol_topics.md`
- `src/backend/docs/app.md`

## 已知限制（MVP）
- canonical id 目前採 fallback 策略（`chain_id + tx_hash + log_index`）。
- address 搜索需要鏈上可觀測資料，目前僅建立 canonicalId/txHash 索引。
- `event_ts` 與 latency 分解尚未接入完整鏈上時間戳解析。
