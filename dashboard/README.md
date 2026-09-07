# TMF Dashboard

Dashboard 採功能導向 URL：

| URL | 功能 | 狀態 |
|---|---|---|
| `/` | 系統總覽、目前行情與策略狀態 | 可使用 |
| `/trade/` | 交易工作台 | 即時多週期 K 線、Paper 委託、持倉／損益、Kill Switch 與成交紀錄 |
| `/backtest/` | 動態歷史回測 | 可使用 |
| `/replay/` | 動態歷史回放 | 單日盤次快照、策略訊號、虛擬時鐘與隔離的 Replay 模擬交易 |
| `/history/` | 回測／交易執行紀錄 | 查詢、篩選、明細與永久刪除 |
| `/strategies/` | 11 套基本策略與風險規則 | 可輸入並儲存參數 |
| `/composite-strategies/` | 組合策略清單、版本與封存管理 | 可新增、搜尋、編輯與回測 |
| `/composite-strategies/new/` | 新增組合策略 | 可從基本策略或歷史版本開始 |
| `/composite-strategies/editor/` | 組合策略編輯器 | 以 `strategy_id` 查詢參數指定策略 |
| `/settings/` | 行情、商品與部署設定摘要 | 唯讀摘要可使用 |

`/trade/`、`/replay/` 與 `/backtest/` 共用
`tw_quant.strategy.analyze_strategies()`；資料來源先轉成標準 `KBar`，再進入
同一套基本策略、停損／停利與模擬成交流程，避免不同模式的規則漂移。
策略管理頁透過 `GET /api/strategies` 載入欄位規格，並以
`PUT /api/strategies/{strategy}` 儲存到後端 SQLite；Live、Replay 與 Backtest
下一次分析時都會使用這份共用設定。

`/replay/` 建立快照時也會建立獨立 Replay Trading Session。下單價格與時間由
伺服器目前游標決定，持倉、委託及成交只保存在該 Session 的暫存事件庫；時間軸
倒退會清空 Replay 帳戶，且任何操作都不會寫入 `/trade/` 使用的正式 Paper 帳戶。

`/trade/` 是唯一的即時交易入口。圖表與 Paper 委託票共用同一條 WebSocket
行情，Paper 帳戶資料則每 5 秒更新；舊 `/live/`、`/paper/` 會自動導向工作台。
商品、週期與策略圖層由工作台的單一選擇狀態管理；Paper 資料更新後會在目前
圖表顯示成交點、未平倉部位均價及該部位最近一筆進場委託的停損線，不會另行
呼叫行情或 Paper API。
持倉、委託與成交集中在同一個頁籤區；手機版在畫面底部固定顯示快速買進／賣出
入口，點擊後開啟同一份委託表單的 Bottom Sheet。管理員帳號表在窄螢幕會轉為
可直接編輯的卡片版面，後端權限與 API 行為不變。

回測頁會先呼叫 `/api/backtest/options` 取得 SQLite 可用交易日，再呼叫
`/api/backtest` 執行所選策略與日期。日期區間由前後端共同限制為最多 31 個
日曆日；可選 `1m／5m／10m／15m／30m／1h／1d／1w`，所有週期皆由已收盤
1 分 K 經後端共用聚合器產生。每筆交易皆顯示進場、出場、停損、停利、
成本、MFE、MAE 與權益回撤。

本機啟動：

```bash
cd dashboard
npm ci
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 npm run dev
```

正式 Lightsail 部署由 Caddy 讓前端與 FastAPI 共用 `tmf.milespapa.com`，不需
設定 `NEXT_PUBLIC_MARKET_API_URL`。目前 GitHub Pages workflow 已停用，正式前後端
均由 Lightsail 提供。

回測結果不代表未來績效，也不構成投資建議。
