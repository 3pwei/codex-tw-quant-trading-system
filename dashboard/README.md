# TMF Dashboard

`/` 是動態策略回測頁，`/live/` 是 TMF 即時 1 分 K。兩頁共用 FastAPI
提供的 ORB／BNF 策略分析核心，避免即時訊號與回測規則不同步。

回測頁會先呼叫 `/api/backtest/options` 取得 SQLite 可用交易日，再呼叫
`/api/backtest` 執行所選策略與日期。日期區間由前後端共同限制為最多 31 個
日曆日；回測只使用已收盤 1 分 K。每筆交易皆顯示進場、出場、停損、停利、
成本、MFE、MAE 與權益回撤。

本機啟動：

```bash
cd dashboard
npm ci
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 npm run dev
```

正式 Lightsail 部署由 Caddy 讓前端與 FastAPI 共用 `tmf.milespapa.com`，不需
設定 `NEXT_PUBLIC_MARKET_API_URL`。GitHub Pages 必須用 Repository Variable
`MARKET_API_URL` 指向可公開連線的 HTTPS FastAPI；Pages 本身不能執行回測後端。

回測結果不代表未來績效，也不構成投資建議。
