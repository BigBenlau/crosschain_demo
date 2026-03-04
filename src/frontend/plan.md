# Frontend 設計總結與重建計劃（MVP）

## 1. 技術棧
- 框架：`React 18` + `TypeScript`
- 構建工具：`Vite`
- 路由：`react-router-dom`（正式路由 `/`、`/tx/:canonicalId`）
- 樣式：原生 `CSS`（`styles.css`，不引入 UI 框架）
- 資料請求：瀏覽器 `fetch` API（封裝於 `api.ts`）
- 即時更新：`Server-Sent Events (EventSource)` 對接 `/api/stream`
- 型別管理：`types.ts`（前後端欄位語義對齊）

## 2. 功能需求與樣式要求
### 2.1 功能需求（MVP）
- 展示 `LayerZero / Wormhole` 跨鏈交易 latest stream。
- 支援搜索 `txHash / canonicalId / address`。
- 支援 Dashboard 列表點選進入獨立 Tx Detail 頁。
- Tx Detail 頁需展示 from/to chain 資訊、timeline、AI 風險狀態與文字報告窗。
- 支援 `Refresh`、`Reset`，並在 SSE 推送後自動刷新。
- 支援增量插入動畫：當後端完成一輪增量掃描並推送新增交易 id 時，Dashboard 對新交易卡片做進場動畫。
- 顯示核心統計：`Total / Executed / In Progress / Need Attention`。
- API 對齊：`/api/latest`、`/api/search`、`/api/tx/{canonicalId}`、`/api/stream`。
- `SSE /api/stream` 事件需包含 `items` 與 `insertedCanonicalIds`，前端據此做增量插入動畫。

### 2.2 樣式要求（現代化）
- 採深色現代 Dashboard 風格：漸層背景 + 玻璃卡片 + 柔和陰影。
- Dashboard 首屏結構：`Hero + Stats + Latest Stream`；Tx Detail 為獨立頁面。
- 狀態與風險顏色語義清晰且一致（成功/失敗/警告/資訊/中性）。
- 注重可讀性：hash 短格式、鏈名稱友好化、錯誤與空狀態可見。
- 響應式設計：桌面雙欄、窄屏單欄，保證核心資訊不截斷。

## 3. 目標與邊界
- 目標：提供現代化 Web Dashboard，展示 `LayerZero / Wormhole` 跨鏈活動與風險結果。
- 邊界：只做 MVP，優先可用性與可維護性，不做重框架改造。
- API 對齊：`/api/latest`、`/api/search`、`/api/tx/{canonicalId}`、`/api/stream`。

## 4. 已落地的設計結果
- 視覺：深色漸層背景、玻璃感卡片、狀態膠囊（Pill）與風險色階。
- 版型：`Dashboard（Hero + Stats + Latest）` + `Tx Detail（獨立頁）`。
- 互動：搜索、Reset、手動 Refresh、SSE 即時刷新、增量交易進場動畫、點選交易跳轉詳情。
- 指標：Total、Executed、In Progress、Need Attention 四張統計卡。

## 5. 代碼位置（重建時先看這些）
- `src/frontend/src/App.tsx`：頁面主流程與事件處理（搜索、列表、詳情、SSE）。
- `src/frontend/src/styles.css`：主題、卡片、網格、時間線、響應式規則。
- `src/frontend/src/api.ts`：前端 API 請求封裝。
- `src/frontend/src/types.ts`：`XChainTxSummary / TxDetail / RiskReport` 型別定義。

## 6. UI 組件拆分藍圖（下一步可重構）
- `App`（容器）
- `HeaderHero`（標題 + 搜索）
- `StatsBar`（4 張統計卡）
- `LatestPanel`（列表）
- `DetailPanel`（詳情）
- `StatusPill / ProtocolBadge / TimelineList / RiskPanel`（可重用子元件）

## 7. 設計規範（保持一致）
- 狀態顏色語義：
  - `EXECUTED` 綠、`FAILED` 紅、`STUCK` 橙、`VERIFIED` 藍、`SENT/UNKNOWN` 灰藍。
- 可讀性：
  - hash 使用短格式（頭尾保留）。
  - chain id 轉友好名稱（目前含 Ethereum / Arbitrum）。
- 響應式：
  - `<=1100px`：雙欄改單欄。
  - `<=700px`：搜索區換行，詳情區單欄。

## 8. 若要重建前端，建議順序
1. 先保留 `api.ts`、`types.ts`，避免資料契約變動。
2. 重建 `App.tsx` 骨架（Hero → Stats → Latest/Detail）。
3. 補 `StatusPill / ProtocolBadge`，再填交易列表與詳情內容。
4. 用 `styles.css` 先完成主題與卡片系統，再做細節。
5. 最後接回 SSE 與錯誤/空狀態處理。

## 9. 常見修改入口
- 增加篩選器：`App.tsx` 查詢條件 + `api.ts` query 參數。
- 增加鏈名：抽出 `chainLabel` mapping 到獨立常量檔。
- 增加分頁：對接 `nextCursor`，列表底部加 `Load More`。
- 增加主題：在 `styles.css` 擴充 CSS 變量，不動資料層。

## 10. 驗收基線（重建後至少滿足）
- `npm run build` 成功。
- 可完成：搜索 → 選交易 → 查看詳情/風險/時間線。
- SSE 更新時列表可刷新，不崩潰。
- 手機與窄屏可讀（核心資訊不截斷）。
