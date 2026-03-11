# Frontend on Vercel, Backend on Ubuntu

本方案適用於：
- 前端：部署到 Vercel
- 後端：部署在 Ubuntu
- 前端仍然使用相對路徑 `/api/*`
- 由 Vercel rewrite 把 `/api/*` 代理到 Ubuntu 後端

## 1. 先處理 Ubuntu 後端

要求：
- 後端必須可從公網訪問
- 建議使用 `https://`
- 對外至少要能訪問：
  - `/api/health`
  - `/api/latest`
  - `/api/stats`
  - `/api/tx/...`
  - `/api/stream`

例如：
- `https://api.your-domain.com`

## 2. 修改前端 Vercel rewrite

打開：
- `src/frontend/vercel.json`

把這個值：
- `https://your-ubuntu-backend.example.com`

替換成你真實的 Ubuntu 後端地址，例如：
- `https://api.your-domain.com`

改完後：
- `https://your-vercel-site.vercel.app/api/*`
- 會由 Vercel 轉發到：
- `https://api.your-domain.com/api/*`

這樣瀏覽器看起來仍然是同源 `/api`，前端代碼不用改。

## 3. 用 Vercel 網站部署，不用 CLI

1. 把代碼推到 GitHub / GitLab / Bitbucket
2. 登錄 Vercel Dashboard
3. 點 `New Project`
4. `Import Git Repository`
5. 選你的倉庫
6. 把 `Root Directory` 設成：
   - `src/frontend`
7. Framework Preset 選：
   - `Vite`
8. Build Command 使用：
   - `npm run build`
9. Output Directory 使用：
   - `dist`
10. 點 Deploy

## 4. 部署後效果

- 頁面：
  - `https://your-project.vercel.app/`
- 前端資源：
  - 由 Vercel 提供
- API：
  - `https://your-project.vercel.app/api/*`
  - 實際被 rewrite 到 Ubuntu 後端

## 5. 優點

- 前端部署簡單
- 不需要改現有前端 `fetch("/api/...")` 代碼
- 不需要在瀏覽器處理跨域 CORS
- Vercel 只負責靜態頁面與入口代理

## 6. 注意

- Ubuntu 後端若是 `http://`，則不建議這樣公開使用
- 建議 Ubuntu 後端自己也配置 `https`
- SSE `/api/stream` 也會走同一條 Vercel rewrite
