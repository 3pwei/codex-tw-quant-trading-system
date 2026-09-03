# TMF Dashboard

Dashboard 採功能導向 URL：

| URL | 功能 | 狀態 |
|---|---|---|
| `/` | 系統總覽、目前行情與策略狀態 | 可使用 |
| `/live/` | TMF 即時 1 分 K | 可使用 |
| `/backtest/` | 動態歷史回測 | 可使用 |
| `/replay/` | 動態歷史回放 | 路由已保留，功能建置中 |
| `/history/` | 回測／交易執行紀錄 | 路由已保留，功能建置中 |
| `/strategies/` | ORB／BNF 策略與風險規則 | 唯讀目錄可使用 |
| `/settings/` | 行情、商品與部署設定摘要 | 唯讀摘要可使用 |

`/live/`、未來 `/replay/` 與 `/backtest/` 共用
`tw_quant.strategy.analyze_strategies()`；資料來源先轉成標準 `KBar`，再進入
同一套 ORB／BNF、停損／停利與模擬成交流程，避免不同模式的規則漂移。

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
