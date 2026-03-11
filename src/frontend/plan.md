# Frontend 當前實作說明

## 1. 技術棧
- `React 18`
- `TypeScript`
- `Vite`
- `react-router-dom`
- 原生 `fetch`
- 原生 `CSS`
- `EventSource`（SSE）

## 2. 當前頁面結構

### Dashboard
- 路由：`/`
- 文件位置：`src/frontend/src/App.tsx`
- 內容：
- Hero 標題區
- 搜索框
- 4 張可點擊統計卡（每張同時顯示總數與 `LayerZero / Wormhole` 拆分數；點擊後切換對應類別 top 50）
- 協議篩選標簽（`All / LayerZero / Wormhole`）
- latest 列表
- 起始區塊說明（取自後端 `.env` 配置值）
- 交互：
- 調用 `/api/latest`
- `/api/latest?category=...&protocol=...` 支援依統計類別與協議切換 top 50
- 調用 `/api/stats` 取得全局統計與按協議拆分統計
- 調用 `/api/search`
- 連接 `/api/stream?category=...&protocol=...`
  - 支援手動 Refresh
  - 新交易進場動畫

### Tx Detail
- 路由：`/tx/:canonicalId`
- 文件位置：`src/frontend/src/App.tsx`
- 內容：
  - 返回 Dashboard
  - 單筆路由摘要
  - Tx Decode（顯示 raw data 與 decoded JSON，可多條）
  - 風險報告窗
  - timeline
  - from/to/timeline `tx_hash` explorer 超連結
  - timeline / decode 由上到下按事件流程順序展示
- 交互：
  - 調用 `/api/tx/{canonicalId}`
  - 支援手動 Refresh

## 3. 代碼分布
- `src/frontend/src/App.tsx`
  - 目前包含：
    - 路由定義
    - DashboardPage
    - TxDetailPage
    - 顯示輔助函數
- `src/frontend/src/api.ts`
  - `fetchLatest`
  - `fetchTx`
  - `searchTx`
- `src/frontend/src/types.ts`
  - `XChainTxSummary`
  - `TimelineItem`
  - `RiskReport`
  - `TxDetail`
  - `StreamLatestEvent`
- `src/frontend/src/styles.css`
  - 主題變量
  - panel / list / timeline 樣式
  - 手機與窄屏響應式規則

## 4. 當前已落地能力
- latest 列表展示
- `canonicalId / txHash` 搜索入口
- tx detail 展示
- 風險狀態與 AI 摘要展示
- timeline 展示
- SSE 實時刷新
- SSE 斷線後自動重連
- 新增交易高亮動畫
- Dashboard 主標題目前使用 `跨鏈交易監測平台`，鏈與協議範圍放在副標題
- Dashboard 會顯示 Ethereum 與 TARGET_CHAIN 的配置起始區塊（不是當前游標）
- 統計卡支持切換 `Total / Executed / In Progress / Need Attention` 對應 top 50 列表
- 統計卡會同步顯示 `LayerZero / Wormhole` 各自數量
- latest 列表可依協議標簽只顯示 `LayerZero` 或 `Wormhole`

## 5. 當前限制
- 前端搜索輸入框文案仍寫 `txHash / canonicalId / address`，但後端目前只對 `txHash / canonicalId` 建了索引。
- `chainLabel()` 目前只對 `1` 和 `42161` 做友好名稱映射。
- latency 雖然在後端 detail 回應裡存在，但頁面尚未渲染。
- 元件尚未拆分，主要邏輯集中在 `App.tsx`。

## 6. 若後續要重構
1. 先抽出共用元件：
   - `StatusPill`
   - `ProtocolBadge`
   - `TxCard`
   - `TimelineList`
   - `RiskPanel`
2. 再抽出工具與映射：
   - `chainLabel`
   - `statusTone`
   - `riskTone`
3. 最後再考慮加入：
   - 篩選器
   - 分頁
   - 更多鏈名稱映射
