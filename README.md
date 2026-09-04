# 台股分鐘線當沖量化回測 MVP

這是一套可直接執行、可逐步擴充的 Python 回測與行情 Dashboard 骨架。行情來源透過 provider-neutral 介面接入，目前提供永豐 Shioaji 與 Mock Replay Adapter；微型臺指期貨 TMF Tick 會聚合成共用 1 分 K。目前只有行情，沒有任何下單端點或下單程式碼。

> 本專案僅供研究與工程驗證，不構成投資建議。合成示範資料不能用來判斷策略獲利能力。

## 已完成

- 讀取與驗證台股 1 分 K CSV，統一使用 `Asia/Taipei`
- 開盤區間突破（Opening Range Breakout, ORB）策略
- BNF 均值回歸策略（20 期均線／標準差、Z-score 與 RSI 確認）
- 訊號於當根收盤產生，下一根開盤成交，避免未來函數
- 多方，以及可選擇啟用的空方回測
- 停損、停利、13:20 強制平倉（皆可調整）
- 同一根 K 棒同時觸發停損／停利時，保守採停損優先
- 台股手續費、最低手續費、當沖賣出交易稅與滑價
- 交易明細、權益曲線、JSON 摘要與 PNG 圖表
- 不依賴 `pytest` 的 `unittest` 測試
- Shioaji TMF Tick callback → Queue → Worker 的非阻塞行情管線
- 即時 1 分 K、SQLite、FastAPI REST/WebSocket 與獨立 heartbeat
- 無憑證可執行的 Mock/Replay 模式
- Next.js + TradingView Lightweight Charts 即時 K 線頁面
- ORB／BNF Multi-select 策略圖層與即時訊號標記
- 策略管理頁可調整 ORB／BNF 與停損停利參數，SQLite 持久化後供即時與回測共用
- 無程式碼多週期策略組合器：Setup／Entry／Exit／Risk、ALL／ANY 與版本追蹤

## 架構

行情與券商帳戶採用不同邊界。Strategy、K 棒聚合、Replay、Backtest、REST、
WebSocket 與 Dashboard 只依賴標準 `TickEvent`／`KBar`，不依賴 Shioaji SDK：

```text
Shioaji / Replay / future provider
  → MarketDataProvider
  → Tick Queue → 1m KBar → SQLite
  → Strategy / Backtest / REST / WebSocket / Dashboard

TradeSignal → Risk → ExecutionSimulator
                         └→ BrokerAccount / OrderExecutor（未來，現在 Disabled）
```

即使未來同一家券商同時提供行情與下單，也必須以兩個 Adapter、兩組設定與
獨立生命週期接入。個人 Shioaji 行情只適合私人測試；多人平台仍需另行確認行情
展示與轉發授權，本架構解耦不代表自動取得轉授權。

```text
CSV 1 分 K
  → 資料驗證與交易時段過濾
  → 策略產生 entry signal
  → 下一根 K 棒開盤撮合
  → 停損／停利／收盤前平倉
  → 手續費、交易稅與滑價
  → trades / equity / summary / chart
```

主要模組：

| 檔案 | 功能 |
|---|---|
| `tw_quant/data.py` | CSV 載入、時區與 OHLCV 品質檢查 |
| `tw_quant/strategy/definitions.py` | ORB 與 BNF 均值回歸策略定義 |
| `tw_quant/engine.py` | 事件式撮合、部位、風控與交易紀錄 |
| `tw_quant/costs.py` | 台股交易成本與滑價 |
| `tw_quant/metrics.py` | 勝率、淨利、PF、回撤與日頻 Sharpe |
| `tw_quant/report.py` | 儲存 CSV、JSON 與權益曲線圖 |
| `tw_quant/market/` | Live、Replay、CSV 共用的 Tick／KBar 與交易時段模型 |
| `tw_quant/strategy/engine.py` | 與資料來源無關的 ORB／BNF 策略分析器 |
| `tw_quant/strategy/parameters.py` | 共用參數規格、預設值與後端驗證 |
| `tw_quant/strategy/composite.py` | 多週期規則驗證、原子訊號組合與共用執行核心 |
| `tw_quant/risk/engine.py` | 共用停損、停利價格與觸發優先序 |
| `tw_quant/execution/simulator.py` | 下一根開盤、風險出場與時段平倉模擬 |
| `tw_quant/backtest/runner.py` | 成本、交易、權益與績效報表 |
| `tw_quant/market_data/ports.py` | 即時／歷史行情 Provider 介面與能力宣告 |
| `tw_quant/market_data/factory.py` | Provider 組裝點；FastAPI 不認識供應商實作 |
| `tw_quant/market_data/providers/` | Shioaji quote-only 與 Mock Replay Adapter |
| `tw_quant/broker/` | 獨立 Broker／Order 介面；目前只提供拒絕下單的 DisabledBroker |
| `tw_quant/live/feed.py` | 舊行情 import 相容層；新程式不應使用 |
| `tw_quant/live/aggregator.py` | Tick 去重、亂序政策、缺漏分鐘與即時 1 分 K |
| `tw_quant/live/storage.py` | SQLite repository；介面可替換 PostgreSQL |
| `tw_quant/live/api.py` | FastAPI REST、WebSocket 與健康檢查 |
| `tw_quant/live/models.py` | 舊匯入相容層；新程式不應使用 |
| `tw_quant/live/strategy_analysis.py` | 舊策略名稱相容層；新程式不應使用 |
| `tw_quant/live/backtest.py` | 舊回測名稱相容層；新程式不應使用 |
| `tw_quant/futures.py` | 期交所逐筆 CSV 匯入與標準 KBar 轉換 |
| `tw_quant/futures_costs.py` | 各 TMF 回測入口共用的契約乘數、手續費、稅與滑價 |
| `dashboard/app/live/` | 即時 K 線、成交量、狀態與自動重連前端 |
| `dashboard/app/strategies/` | 策略參數輸入、驗證訊息與儲存介面 |

## 安裝

建議 Python 3.10 以上。

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

python -m pip install -e ".[server,test]"
```

若要連接 Shioaji 正式行情，再安裝可選套件：

```bash
python -m pip install -e ".[server,shioaji]"
```

## TMF 即時 1 分 K

資料流刻意將券商 callback 保持最小：

```text
Shioaji Tick callback
  → 驗證、標準化、asyncio.Queue.put_nowait
  → 獨立 Worker
  → 去重／亂序處理／1 分 K 聚合
  → SQLite upsert
  → REST 歷史查詢 + WebSocket 增量推送
  → Next.js Lightweight Charts series.update()
```

K 棒使用 Tick 的交易所時間（`Asia/Taipei`）分桶，不使用瀏覽器時間。TMF 日盤設定為 08:45–13:45、夜盤為 15:00–次日 05:00；15:00 後的夜盤歸到下一交易日，跨午夜後維持同一交易日。週末會自動跳過；交易所特殊休市日仍應由部署端行事曆設定或在上線前驗證。

處理政策：

- 同一分鐘內亂序 Tick 仍會依最早／最晚交易所時間修正 Open／Close。
- 已關閉分鐘收到遲到 Tick 時不回寫歷史 K 棒，會計入 `late_ticks`。
- Tick 優先使用券商 sequence 去重；缺少 sequence 時使用契約、微秒時間、價格、單量與累積量雜湊。
- 無成交分鐘在下一筆 Tick 抵達時補成前收價 OHLC、成交量 0、`no_trade=true`。
- SQLite 同時保存形成中 K 棒與已處理 Tick ID，重啟後可續接且不重複累加。
- TMFR1 解析出的實際近月契約會放在訊息 `contract`；Tick 契約變更時視為換月並關閉舊契約 K 棒。
- 連線狀態取自獨立 heartbeat／Shioaji quote connection event，不會因為一段時間沒有成交就判斷斷線。

### 交易策略圖層

即時頁右上角的 Multi-select Dropdown 可以同時顯示或隱藏：

- `ORB 開盤突破`：每個日／夜盤以前 15 分鐘建立區間，收盤突破區間且
  當根量達前 5 根均量的 1.2 倍時確認訊號。
- `BNF 均值回歸`：20 期收盤均線與標準差；Z-score 向下穿越 `-2` 且
  RSI(14) 不高於 30 時做多，向上穿越 `+2` 且 RSI 不低於 70 時做空；
  回到 `±0.5Z` 內產生均值回歸出場訊號。

兩套策略只使用已收盤 K 棒確認，並在下一根 K 棒開盤建立訊號標記，
不使用形成中 K 棒偷看結果。風控預設為停損 0.6%、停利 1.2%；標記
只供研究與觀察，不會觸發模擬或真實訂單。

`/strategies/` 可以修改上述策略條件以及各策略自己的停損／停利。前端只負責
輸入；FastAPI 會依 `tw_quant/strategy/parameters.py` 再次驗證範圍與跨欄位規則，
通過後寫入行情服務使用的 SQLite。Live、Replay 與 Backtest 都會將同一份設定
傳入 `analyze_strategies()`，不會各自維護另一套參數。按「恢復預設」只會先更新
畫面，仍需按「儲存參數」才會套用。

### 多週期策略組合器

`/strategies/` 下方可用結構式編輯器建立自己的組合策略，不需要也不允許輸入
Python 程式碼。每個組合策略由四段構成：

- `Setup`：市場背景或高週期啟動條件；可留空。
- `Entry`：真正觸發進場的條件；至少一條。
- `Exit`：策略出場條件；可留空，仍會受風控與時段結束平倉。
- `Risk`：1 分 K 停損、停利與最長持有時間；必填。

Setup、Entry 與 Exit 都能加入多條 ORB／BNF 規則，個別選擇 `1m`、`5m`、
`10m`、`15m`、`30m`、`1h`、`1d` 或 `1w`，並設定 `ALL`／`ANY` 及條件確認
視窗。每個規則積木預設繼承策略管理頁上方已保存的原子策略參數，也可在積木內
個別覆寫（例如 Entry BNF 與 Exit BNF 使用不同門檻）；後端保存時會把完整解析後
的參數快照寫入組合策略版本，之後修改原子策略不會竄改既有版本。

儲存新策略會建立 v1；修改現有策略會新增 v2、v3，而不覆蓋舊版。策略管理頁
可展開完整版本紀錄；歷史版本只能複製成另一個全新策略，不能原地修改。歷史
回測頁會列出最新版本，執行結果同時記錄策略 ID 與版本。組合策略一律從原始已收盤
1 分 K 產生各週期訊號，再於下一根 1 分 K 開盤模擬成交；停損與停利也以
1 分 K 檢查，同根同時觸發時採停損優先。

策略管理清單的「刪除」採封存方式：策略會立即從管理清單與新回測選單移除，
但不會物理刪除 v1、v2 等既有版本，因此歷史結果仍可依原策略 ID／版本重現。
已封存策略不可再修改，以免同一個策略 ID 出現不連續或被覆寫的版本歷史。
封存庫預設收合，並支援勾選多筆永久刪除。永久刪除會移除整條版本鏈且無法
復原；後端只允許刪除已封存且沒有 `backtest_runs` 引用的策略，只要批次中有
一個策略被引用就會拒絕整批操作。SQLite 外鍵也使用 `ON DELETE RESTRICT` 作為
第二層保護。使用者在 `/backtest/` 主動執行的回測會保存至 `backtest_runs`；頁面
首次載入的預覽不保存，避免產生沒有決策價值的重複紀錄。

相關 API：

- `GET /api/composite-strategies`
- `POST /api/composite-strategies`
- `GET /api/composite-strategies/{id}/versions`
- `GET /api/composite-strategies/{id}?version=1`
- `PUT /api/composite-strategies/{id}`（建立新版本）
- `DELETE /api/composite-strategies/{id}`（封存，不刪除版本）
- `POST /api/composite-strategies/purge`（批次永久刪除未被引用的封存策略）
- `GET /api/composite-strategy-signals/{id}?version=1`
- `GET /api/composite-backtest?strategy_id={id}&version=1&start=2026-08-01&end=2026-08-31`

### 回測執行紀錄

`POST /api/backtest-runs` 是前端正式執行回測的統一入口，支援基本策略及組合
策略。每一筆紀錄保存策略／參數快照、績效摘要、交易明細及權益曲線；行情 K 棒
不重複寫入結果，仍由 `minute_bars` 管理。組合策略另外以 SQLite 外鍵固定引用
`strategy_id + strategy_version`，因此有回測紀錄的版本不能被永久刪除。

`/history/` 提供策略搜尋、基本／組合類型與有無交易篩選、績效摘要、逐筆交易
明細及單筆永久刪除。刪除組合策略回測會同步解除該策略版本的引用；若仍有其他
回測引用相同版本，該策略仍不能永久刪除。

相關 API：

- `POST /api/backtest-runs`（執行並保存）
- `GET /api/backtest-runs?limit=100&offset=0`
- `GET /api/backtest-runs/{run_id}`
- `DELETE /api/backtest-runs/{run_id}`（永久刪除並解除該筆策略版本引用）

目前仍是研究與行情觀察階段：組合策略可在 Live／Replay 資料上呼叫相同訊號
核心，也可執行歷史回測，但不會連接 Broker 下單。

### Mock／Replay 本機啟動

複製環境變數範本；範本只有假資料，請勿把真實憑證提交到 GitHub：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

啟動後端：

```bash
MARKET_DATA_PROVIDER=replay uvicorn tw_quant.live.api:app --host 0.0.0.0 --port 8000 --env-file .env
```

另一個終端啟動 Dashboard：

```bash
cd dashboard
npm ci
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 npm run dev
```

Windows PowerShell 可先執行 `$env:NEXT_PUBLIC_MARKET_API_URL="http://localhost:8000"`，再執行 `npm run dev`。

開啟：

- 系統總覽：<http://localhost:3000/>
- 回測 Dashboard：<http://localhost:3000/backtest/>
- 回測執行紀錄：<http://localhost:3000/history/>
- 即時 1 分 K：<http://localhost:3000/live/>
- API 文件：<http://localhost:8000/docs>

Mock 會重播 `data/mock_tmf_ticks.csv`。同一分鐘包含多筆 Tick，圖表應以 `series.update()` 反覆更新同一根形成中 K 棒，跨分鐘才新增 K 棒；15:02 沒有成交，會補成零量 K 棒。

### Shioaji 正式行情模式

`.env` 至少設定：

```dotenv
MARKET_DATA_PROVIDER=shioaji
MARKET_CONTRACT=TMFR1
MARKET_HISTORY_DAYS=30
MARKET_HISTORY_LIMIT=50000
SJ_API_KEY=your-real-key
SJ_SEC_KEY=your-real-secret
SJ_PRODUCTION=true
```

再啟動同一個 FastAPI 指令。啟動時會使用 Shioaji `kbars` 一次回補最近 30 日內
最多 50,000 根已收盤 1 分 K，之後只依靠 Tick callback 即時更新；不會輪詢歷史 API。
行情服務登入時停用 trade event subscription，只保留歷史／即時行情；不啟用 CA、
不提供下單 API。正式 key 建議只授予 Market/Data 權限並限制來源 IP。

### API

- `GET /api/health`
- `GET /api/me`（目前登入身分、角色、帳號／交易狀態與權限）
- `GET /api/admin/health`（管理員：Provider、Queue、重複／遲到 Tick 診斷）
- `GET|POST /api/admin/users`、`PUT /api/admin/users/{user_id}`（管理員）
- `GET /api/admin/audit`（管理員：帳號異動稽核）
- `GET /api/kbars?symbol=TMF&interval=5m&limit=500`
- `GET /api/strategy-signals?symbol=TMF&strategies=orb,bnf&interval=5m&limit=500`
- `GET /api/strategies`
- `PUT /api/strategies/{strategy}`，JSON：`{"parameters": {...}}`
- `GET /api/backtest/options?symbol=TMF`
- `GET /api/backtest?symbol=TMF&strategy=orb&interval=5m&start=2026-08-01&end=2026-08-31`
- `WS /ws/market/TMF?interval=5m`

`interval` 支援 `1m`、`5m`、`10m`、`15m`、`30m`、`1h`、`1d`、`1w`。
SQLite 仍只保存唯一一份 1 分 K；REST、WebSocket、策略訊號與回測會透過
`tw_quant.market.timeframes` 共用聚合器產生所選週期，不重複儲存行情，也不在
Live／Backtest 各寫一套。分鐘與小時 K 以日盤 08:45、夜盤 15:00 為分桶起點；
日 K 依期交所 `trading_date` 合併前一晚夜盤與當日日盤，週 K 依交易日週次聚合，
且合約換月時絕不合併不同契約。日／週 K 可顯示的長度取決於 1 分 K 實際保留範圍。
ORB 是日內開盤區間策略，因此日／週 K 不產生 ORB 訊號；BNF 在日／週 K 會依
契約連續累積指標視窗，仍使用相同的策略與風控函式。

回測 Dashboard 與即時頁共用 ORB／BNF 訊號分析器，只使用 SQLite 內的
1 分 K 作為來源。畫面可選 K 棒週期、策略與起訖交易日，單次最多 31 個日曆日；FastAPI 也會驗證
相同上限，不能只靠瀏覽器繞過。夜盤跨日依 `trading_date` 查詢，而非日曆時間。
既有 Lightsail 若仍使用舊版 `/opt/tw-quant/config/market.env`，需把
`MARKET_HISTORY_DAYS` 改為 `30`、`MARKET_HISTORY_LIMIT` 改為 `50000`，重啟後
才會嘗試回補一個月；實際可選日期仍以 Shioaji 回傳並成功寫入 SQLite 的範圍為準。

WebSocket 的 K 棒訊息包含 symbol、實際契約、交易所／接收時間、延遲、OHLCV、forming/closed、日夜盤、交易日與行情連線狀態。獨立 heartbeat 即使無成交也會持續推送。

### 共用策略資料流

Live、歷史回放與 CSV 的差異只存在資料來源；三者先轉為
`tw_quant.market.KBar`，再呼叫同一個 `analyze_strategies()`：

```text
Shioaji Live Feed ─┐
Replay Feed ───────┼─→ canonical KBar → Strategy Engine → Risk Engine
TAIFEX CSV Feed ───┘                                      ↓
                                      Execution Simulator → Backtest Result
```

目前 Live 是 quote-only，策略訊號只推送至 Dashboard，不會連到 Broker 下單。
未來若加入實盤，Broker adapter 必須與 `Execution Simulator` 分開，且必須先經過
獨立的下單風控與使用者授權。

### Docker 與本機部署

```bash
docker compose up --build -d
curl http://localhost:8000/api/health
```

FastAPI、Shioaji callback 與 WebSocket 必須部署在可常駐執行 Python 的主機；GitHub Actions 只負責測試與建置，不能作為盤中行情 daemon。部署主機需掛載 `output/` 保存 SQLite，或日後將 `BarRepository` 換成 PostgreSQL。

目前 GitHub Pages workflow 已停用；正式 Dashboard 與 FastAPI 統一由 Lightsail
上的 Caddy 提供。GitHub Actions 負責 CI 與核准後部署，不負責盤中常駐行情。

## AWS Lightsail 公開部署

正式部署目標是東京區域的 AWS Lightsail Linux/Ubuntu x86 主機，建議至少
2 GB RAM。GitHub 保存程式碼並執行測試；Lightsail 只負責常駐執行。公開流量
透過 Caddy 進入同一個 HTTPS 網址：

```text
瀏覽器 ─HTTPS/WSS→ Caddy
                     ├─ /、/live、/backtest 等 → 靜態 Dashboard
                     ├─ /api     → FastAPI REST
                     └─ /ws      → FastAPI WebSocket
                                      └→ Shioaji Tick → Queue → Worker → SQLite Volume
```

前端在沒有設定 `NEXT_PUBLIC_MARKET_API_URL` 時會使用目前網頁的 origin，因此
正式網站不會退回 `localhost`。GitHub Pages 模式仍可透過 Repository Variable
指定獨立 API 網址；正式 Lightsail 同源部署不需要設定此變數。

### 1. 建立 Lightsail 主機

1. 建立 AWS 帳號並啟用 MFA、帳單預算警示。
2. Lightsail 區域選 `Tokyo (ap-northeast-1)`。
3. Blueprint 選 Ubuntu 24.04 LTS、架構選 x86_64。
4. 建議方案為 2 GB RAM；不要選 ARM，Shioaji wheel 必須先驗證架構相容性。
5. 建立並附掛 Static IP；Lightsail 防火牆只開 TCP 80、443，以及初始維護用 TCP 22。
6. 將自己的 DNS `A` record 指向 Static IP。沒有網域時，可先用指向該 IP 的測試 DNS，但正式使用應購買並控制自己的網域。

HTTPS 是必要條件；不要用裸 IP、HTTP 或自簽憑證傳送網站密碼與行情連線。

### 2. 初始化主機

以 Lightsail SSH 連入 Ubuntu，取得 Repository 後執行：

```bash
git clone --branch master https://github.com/3pwei/codex-tw-quant-trading-system.git
cd codex-tw-quant-trading-system
sudo bash deploy/lightsail/bootstrap.sh
```

Bootstrap 只安裝 Docker、Git，並建立 `/opt/tw-quant/config/`。真實 Secret 保存在
Git checkout 外，權限為 `0600`。接著編輯：

```text
/opt/tw-quant/config/market.env
/opt/tw-quant/config/gateway.env
/opt/tw-quant/config/compose.env
```

先以 `MARKET_DATA_PROVIDER=replay` 驗證。既有 `MARKET_MODE=mock`／`shioaji`
仍可作為過渡相容設定，但新部署應使用 `MARKET_DATA_PROVIDER`。正式站不使用共用 Basic Auth 密碼，而是使用
Cloudflare Access 的核准 Email 與一次性驗證碼。先在 Cloudflare Zero Trust 建立：

1. `Access controls` → `Applications` → `Create new application`。
2. 類型選 `Self-hosted and private`，Public hostname 填 `tmf.example.com`。
3. 建立 `Allow` policy，Selector 選 `Emails`，只加入核准的完整 Email。
4. 啟用 One-time PIN 登入方式；不要用 `Emails ending in` 開放整個公共信箱網域。
5. 複製 Team domain（例如 `team.cloudflareaccess.com`）與應用程式的
   `Application Audience (AUD) Tag`。

將 `/opt/tw-quant/config/gateway.env` 設為：

```dotenv
MARKET_DOMAIN=tmf.example.com
ACME_EMAIL=owner@example.com
```

並在 `/opt/tw-quant/config/market.env` 設定：

```dotenv
MARKET_DATA_PROVIDER=replay
MARKET_ALLOWED_ORIGINS=https://tmf.example.com
MARKET_ACCESS_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=team.cloudflareaccess.com
CF_ACCESS_AUD=replace-with-application-audience-tag
PLATFORM_AUTHORIZATION_MODE=disabled
PLATFORM_BOOTSTRAP_ADMIN_EMAILS=owner@example.com
```

Access 會在 Cloudflare 邊緣驗證 Email，FastAPI 源站再驗證
`Cf-Access-Jwt-Assertion` 的簽章、issuer 與 audience。這可防止攻擊者用 Lightsail
IP 和偽造 Host header 繞過登入。缺少或無效的 assertion 會在源站 fail closed。

### 應用程式帳號與角色基礎

Cloudflare Access 負責確認 Email 身分；FastAPI 另以 SQLite 的 `app_users`、
`permissions`、`role_permissions` 與 `audit_events` 保存平台帳號、角色、交易狀態及
稽核紀錄。兩層不能互相取代：通過 Cloudflare 不代表已取得平台功能權限。

首次啟用時保持：

```dotenv
PLATFORM_AUTHORIZATION_MODE=disabled
PLATFORM_BOOTSTRAP_ADMIN_EMAILS=owner@example.com
```

這會建立指定的第一位管理員、提供 `GET /api/me`，但暫不封鎖既有頁面與 API。
登入 Dashboard 後，瀏覽器直接開啟 `https://tmf.example.com/api/me`，確認回傳
`registered: true`、`identity_bound: true`、`role: admin`。接著把伺服器設定改為：

```dotenv
PLATFORM_AUTHORIZATION_MODE=enforced
```

重新部署後，只有預先建立且為 `active` 的平台帳號可以使用系統。管理員可從
`/admin/users/` 新增核准 Email、設定 `researcher`／`trader`／`admin`、暫停或撤銷
帳號。設定頁、帳號管理、完整 Provider Health、OpenAPI 與文件頁均由 Caddy
forward-auth 與 FastAPI RBAC 雙重限制；前端隱藏選單不是安全邊界。未知 API route
在 enforced 模式下預設拒絕，WebSocket 也會在握手時驗證 `market.read`。

一般使用者的 `/api/health` 與 WebSocket heartbeat 不回傳 Provider 名稱、Queue、
丟棄／重複／遲到 Tick 等內部診斷；完整資訊只由 `/api/admin/health` 提供。
此設定不會自動建立其他 Email，Cloudflare Access policy 中的 Email 仍須與平台帳號
名單同步維護。

目前角色資料模型包含：

- `researcher`：行情、策略與回測研究。
- `trader`：研究功能，加上自己的 Broker、模擬交易；真實交易仍需另行啟用。
- `admin`：平台帳號、系統設定、Provider 診斷與稽核管理，不代替使用者下單。

帳號狀態為 `active`、`suspended`、`revoked`；交易模式獨立保存為 `disabled`、
`paper`、`live`。研究者與管理員不能設定為 `paper` 或 `live`。`audit_events` 僅提供
append 操作，Secret、API Key 與 Token 不得寫入事件內容。

先保持 DNS `DNS only`，讓 Caddy 取得源站 HTTPS 憑證，然後啟動：

```bash
sudo /opt/tw-quant/repo/deploy/lightsail/deploy.sh "$(git -C /opt/tw-quant/repo rev-parse HEAD)"
curl https://tmf.example.com/healthz
```

`/healthz` 成功後，將 Cloudflare DNS 紀錄切成 Proxied（橘雲），SSL/TLS mode 設為
`Full (strict)`。為讓 GitHub deployment health check 不需要使用者 Session，可另外建立
更精確的 Access application `tmf.example.com/healthz`，Policy action 選 `Bypass`、
Selector 選 `Everyone`；此路徑只回傳固定的 `ok`，不包含行情或系統狀態。

瀏覽器開啟 `https://tmf.example.com/live/`，使用核准 Email 收取一次性驗證碼後，
應看到 Mock 形成中的 1 分 K。Cloudflare Access Session 同時涵蓋 Dashboard、REST
與 WebSocket。切換 Shioaji 前，將 `market.env` 改成：

```dotenv
MARKET_DATA_PROVIDER=shioaji
MARKET_CONTRACT=TMFR1
MARKET_HISTORY_DAYS=30
MARKET_HISTORY_LIMIT=50000
SJ_API_KEY=replace-on-server
SJ_SEC_KEY=replace-on-server
SJ_PRODUCTION=true
```

第一階段的 API Key 只能授予 Market/Data 權限，不能授予 Trading 權限；本服務也
不載入 CA。設定永豐允許 IP 時使用 Lightsail Static IP。

### 3. GitHub Actions 部署

`.github/workflows/deploy-lightsail.yml` 是手動觸發的 production deployment。每次
部署會重新執行 Python 測試、前端 Lint 與 Build，通過後才透過 SSH 執行指定的
`master` commit。建議在 GitHub Environment `lightsail-production` 啟用 required
reviewer，避免盤中誤部署。

Environment Variables：

```text
LIGHTSAIL_HOST=<Static IP 或 DNS>
LIGHTSAIL_USER=ubuntu
PUBLIC_DASHBOARD_URL=https://tmf.example.com
```

Environment Secrets：

```text
LIGHTSAIL_SSH_PRIVATE_KEY=<僅部署使用的 SSH 私鑰>
LIGHTSAIL_SSH_HOST_KEY=<事先核對過的 known_hosts 完整一行>
```

Shioaji API Key、Secret、未來可能使用的 CA 憑證都不能放進 GitHub Actions。
GitHub workflow 只更新程式碼與容器，不能讀取 `/opt/tw-quant/config/market.env`。
Cloudflare Team domain 與 AUD tag 不是登入密碼，但仍應由伺服器設定管理，不要傳到前端。

### 4. 備份、更新與故障處理

- SQLite 位於 Docker named volume `tw-quant-lightsail_market-data`，容器更新不會刪除。
- 部署前應建立 Lightsail snapshot；若有真實下單需求，資料庫應升級 PostgreSQL。
- 部署會短暫中斷行情，僅在休市時手動執行。
- 重啟後 Worker 會讀取形成中 K 棒及 Tick 去重資料；恢復 Shioaji 後仍須檢查缺漏行情。
- `/healthz` 只代表 HTTPS gateway 存活；`/api/health` 才包含 Shioaji 連線、最後 Tick 與延遲。
- 若未來加入下單，必須先完成模擬交易、固定 IP 白名單、CA 安全保存、訂單冪等、持倉核對、最大虧損與 Kill Switch；目前版本仍完全不能下單。

### 常見問題

- `歷史 K 棒為空`：Shioaji 模式會在啟動時一次回補；查看 `/api/health` 的
  `history_bars_loaded` 與 `history_error`。Mock 模式則從 Replay Tick 累積。
- `Dashboard 顯示重新連線`：確認後端 URL、CORS 的 `MARKET_ALLOWED_ORIGINS`、TLS 憑證和 `/api/health`。
- `沒有 Tick 但仍顯示連線`：這是預期行為；無成交不等於斷線，heartbeat 才是連線判斷依據。
- `重啟後 Mock 不再更新`：既有 SQLite 已記錄相同 replay Tick；測試新一輪可刪除測試用 DB，正式資料庫不要任意刪除。
- `換月`：訂閱 `TMFR1`，健康檢查與 K 棒訊息的 `contract` 顯示實際契約；換月前應人工核對流動性與切換時間。

## 立即執行示範

```bash
python -m tw_quant demo
```

使用 BNF 均值回歸回測自己的台股 1 分 K：

```bash
python -m tw_quant backtest \
  --csv data/2330_1m.csv \
  --strategy bnf \
  --direction both \
  --bnf-window 20 \
  --bnf-entry-z 2.0 \
  --bnf-exit-z 0.5 \
  --bnf-rsi-period 14 \
  --output output/2330_bnf
```

均值回歸訊號在 K 棒收盤確認，下一根開盤成交；回到均值區、停損、
停利或收盤時間都可能觸發出場。同根同時碰到停損與停利時仍採停損優先。

這會在 `output/demo/` 產生：

- `demo_2330_1m.csv`：合成資料，不是真實 2330 行情
- `trades.csv`：逐筆交易與成本
- `equity.csv`：權益與回撤
- `summary.json`：績效摘要
- `equity.png`：權益曲線

## 期交所逐筆 CSV 離線回測

`tw_quant/futures.py` 只負責讀取期交所逐筆成交 CSV、篩選商品／契約及轉換
OHLCV。`futures-night` 會把資料聚合成 1 分 K，再轉成與即時系統相同的
`KBar` 模型，最後呼叫 `run_strategy_backtest()`。ORB、BNF、停損與停利
不在 CSV 匯入模組重複實作。

2026/8/24 夜盤使用 `TMF 202609`，時段為 2026/8/24 15:00 至
2026/8/25 05:00。期貨損益依契約乘數每點新臺幣 10 元計算；券商手續費、
交易稅與滑價均為可調參數。官方逐筆檔的「成交數量(B+S)」為買賣雙方合計，
聚合成交量時會除以 2。

取得期交所逐筆 CSV 後，可選擇與即時 Dashboard 相同的 `orb` 或 `bnf`：

```bash
python -m tw_quant futures-night \
  --csv Daily_2026_08_25.csv \
  --product TMF \
  --contract-month 202609 \
  --session-start "2026-08-24 15:00" \
  --session-end "2026-08-25 05:00" \
  --strategy orb \
  --output output/tmf_20260824_night
```

輸出包含 1 分 K `bars.csv`、`trades.csv`、`equity.csv` 與 `summary.json`。
策略規則若在 `tw_quant/strategy/engine.py` 修改，即時訊號、動態回測與
此離線 CSV 回測會一起更新。

## 使用自己的 1 分 K

CSV 欄位：

```csv
timestamp,symbol,open,high,low,close,volume
2026-08-24 09:00:00,2330,1180,1185,1175,1182,2500
2026-08-24 09:01:00,2330,1182,1184,1178,1180,1800
```

`timestamp` 若沒有時區，系統會視為台北時間。時間戳記假設代表該分鐘的起始時間。

```bash
python -m tw_quant backtest \
  --csv data/2330_1m.csv \
  --quantity 1000 \
  --direction long \
  --opening-minutes 15 \
  --stop-loss 0.006 \
  --take-profit 0.012 \
  --commission-discount 0.28 \
  --slippage-bps 2 \
  --output output/2330_orb
```

Windows PowerShell 可改成單行執行，或以反引號取代 `\` 換行。

## 2026 年預設交易成本

| 成本 | 預設值 | 說明 |
|---|---:|---|
| 券商手續費 | 每邊 0.1425% | 預設無折扣；請用 `--commission-discount` 填實際折扣 |
| 最低手續費 | 每筆 20 元 | 券商與交易管道可能不同，可調整 |
| 現股當沖賣出稅 | 0.15% | 股票賣出端；優惠期限目前至 2027-12-31 |
| 滑價 | 每邊 2 bps | 研究假設，不是法定費率 |

官方參考：[臺灣證券交易所—當日沖銷交易](https://www.twse.com.tw/zh/products/system/day-trading.html)、[臺灣證券交易所—集中市場交易制度](https://www.twse.com.tw/zh/products/system/trading.html)、[財政部—當沖降稅延長公告](https://www.mof.gov.tw/singlehtml/384fb3077bb349ea973e7fc6f13b6974?cntId=4493245d64e5422887a375921e889465)。

## 測試

```bash
python -m unittest discover -s tests -v
```

測試包含成本計算、OHLC 資料驗證、訊號下一根開盤成交，以及停損／停利同根觸發的保守處理。

## 回測解讀原則

1. 先看扣除全部成本後的 `net_profit`、`profit_factor` 與 `max_drawdown`，不要只看勝率。
2. 用至少跨越多空循環的資料，並將訓練期、驗證期、樣本外測試分開。
3. 分鐘 K 無法得知同一分鐘內先碰停損還是停利，本系統故意採最不利假設。
4. 低流動性股票需加入成交量限制、市場衝擊與漲跌停無法成交模型。
5. 空方回測前須確認標的可當沖、先賣後買資格、券源與券商規則。

## 第一版限制與下一階段

目前一次只處理一個 symbol，採固定股數，尚未模擬委託簿、部分成交、漲跌停、暫緩撮合、除權息與公司行動，也未連接券商。

建議依序擴充：

1. 接入合法授權的台股分鐘歷史資料，建立 Parquet 資料層與資料品質報告。
2. 加入多標的選股、風險預算、單日最大虧損與 walk-forward 驗證。
3. 串接券商行情做 paper trading，比較理論成交與真實模擬成交差異。
4. 最後才啟用實盤下單，加入 kill switch、冪等訂單、對帳、告警與人工覆核。
