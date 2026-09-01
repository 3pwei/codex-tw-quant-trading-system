# 台股分鐘線當沖量化回測 MVP

這是一套可直接執行、可逐步擴充的 Python 回測與行情 Dashboard 骨架。既有回測流程保持不變，新增的即時模組聚焦「永豐 Shioaji、微型臺指期貨 TMF、Tick 聚合 1 分 K」。目前只有行情，沒有任何下單端點或下單程式碼。

> 本專案僅供研究與工程驗證，不構成投資建議。合成示範資料不能用來判斷策略獲利能力。

## 已完成

- 讀取與驗證台股 1 分 K CSV，統一使用 `Asia/Taipei`
- 開盤區間突破（Opening Range Breakout, ORB）示範策略
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

## 架構

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
| `tw_quant/strategies.py` | ORB 策略；可換成自己的策略類別 |
| `tw_quant/engine.py` | 事件式撮合、部位、風控與交易紀錄 |
| `tw_quant/costs.py` | 台股交易成本與滑價 |
| `tw_quant/metrics.py` | 勝率、淨利、PF、回撤與日頻 Sharpe |
| `tw_quant/report.py` | 儲存 CSV、JSON 與權益曲線圖 |
| `tw_quant/live/feed.py` | Shioaji quote-only adapter 與 Mock Replay feed |
| `tw_quant/live/aggregator.py` | Tick 去重、亂序政策、缺漏分鐘與即時 1 分 K |
| `tw_quant/live/storage.py` | SQLite repository；介面可替換 PostgreSQL |
| `tw_quant/live/api.py` | FastAPI REST、WebSocket 與健康檢查 |
| `dashboard/app/live/` | 即時 K 線、成交量、狀態與自動重連前端 |

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
MARKET_MODE=mock uvicorn tw_quant.live.api:app --host 0.0.0.0 --port 8000 --env-file .env
```

另一個終端啟動 Dashboard：

```bash
cd dashboard
npm ci
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 npm run dev
```

Windows PowerShell 可先執行 `$env:NEXT_PUBLIC_MARKET_API_URL="http://localhost:8000"`，再執行 `npm run dev`。

開啟：

- 回測 Dashboard：<http://localhost:3000/>
- 即時 1 分 K：<http://localhost:3000/live/>
- API 文件：<http://localhost:8000/docs>

Mock 會重播 `data/mock_tmf_ticks.csv`。同一分鐘包含多筆 Tick，圖表應以 `series.update()` 反覆更新同一根形成中 K 棒，跨分鐘才新增 K 棒；15:02 沒有成交，會補成零量 K 棒。

### Shioaji 正式行情模式

`.env` 至少設定：

```dotenv
MARKET_MODE=shioaji
MARKET_CONTRACT=TMFR1
SJ_API_KEY=your-real-key
SJ_SEC_KEY=your-real-secret
SJ_PRODUCTION=true
```

再啟動同一個 FastAPI 指令。行情服務只呼叫登入、契約解析、Tick callback 與 quote subscribe；不啟用 CA、不提供下單 API。正式 key 建議只授予 Market/Data 權限並限制來源 IP。

### API

- `GET /api/health`
- `GET /api/kbars?symbol=TMF&interval=1m&limit=500`
- `WS /ws/market/TMF`

WebSocket 的 K 棒訊息包含 symbol、實際契約、交易所／接收時間、延遲、OHLCV、forming/closed、日夜盤、交易日與行情連線狀態。獨立 heartbeat 即使無成交也會持續推送。

### Docker 與本機部署

```bash
docker compose up --build -d
curl http://localhost:8000/api/health
```

FastAPI、Shioaji callback 與 WebSocket 必須部署在可常駐執行 Python 的主機；GitHub Actions 只負責測試與建置，不能作為盤中行情 daemon。部署主機需掛載 `output/` 保存 SQLite，或日後將 `BarRepository` 換成 PostgreSQL。

Dashboard 可由 `.github/workflows/pages.yml` 部署至 GitHub Pages。先部署 HTTPS/WSS 後端，再在 Repository → Settings → Secrets and variables → Actions 建立變數：

```text
MARKET_API_URL=https://your-market-api.example.com
```

接著在 Settings → Pages 將 Source 設為 GitHub Actions；合併 `master` 或手動執行 Pages workflow。公開 Dashboard 預期網址為 <https://3pwei.github.io/codex-tw-quant-trading-system/>，即時頁為 `/live/`。HTTPS Pages 不能連接不安全的 `http://`／`ws://` 後端。

## AWS Lightsail 公開部署

正式部署目標是東京區域的 AWS Lightsail Linux/Ubuntu x86 主機，建議至少
2 GB RAM。GitHub 保存程式碼並執行測試；Lightsail 只負責常駐執行。公開流量
透過 Caddy 進入同一個 HTTPS 網址：

```text
瀏覽器 ─HTTPS/WSS→ Caddy
                     ├─ /、/live → 靜態 Dashboard
                     ├─ /api     → FastAPI REST
                     └─ /ws      → FastAPI WebSocket
                                      └→ Shioaji Tick → Queue → Worker → SQLite Volume
```

前端在沒有設定 `NEXT_PUBLIC_MARKET_API_URL` 時會使用目前網頁的 origin，因此
正式網站不會退回 `localhost`。GitHub Pages 模式仍可透過 Repository Variable
指定獨立 API 網址，既有 Pages 功能不受影響。

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

先以 `MARKET_MODE=mock` 驗證。`gateway.env` 的密碼雜湊可在主機產生：

```bash
docker run --rm caddy:2.10-alpine \
  caddy hash-password --plaintext 'replace-with-a-long-random-password'
```

將輸出以單引號包住後填入 `DASHBOARD_PASSWORD_HASH`，避免雜湊中的 `$` 被解讀。
設定完成後啟動：

```bash
sudo /opt/tw-quant/repo/deploy/lightsail/deploy.sh "$(git -C /opt/tw-quant/repo rev-parse HEAD)"
curl https://tmf.example.com/healthz
```

瀏覽器開啟 `https://tmf.example.com/live/`，登入 Caddy Basic Authentication 後，
應看到 Mock 形成中的 1 分 K。切換 Shioaji 前，將 `market.env` 改成：

```dotenv
MARKET_MODE=shioaji
MARKET_CONTRACT=TMFR1
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

### 4. 備份、更新與故障處理

- SQLite 位於 Docker named volume `tw-quant-lightsail_market-data`，容器更新不會刪除。
- 部署前應建立 Lightsail snapshot；若有真實下單需求，資料庫應升級 PostgreSQL。
- 部署會短暫中斷行情，僅在休市時手動執行。
- 重啟後 Worker 會讀取形成中 K 棒及 Tick 去重資料；恢復 Shioaji 後仍須檢查缺漏行情。
- `/healthz` 只代表 HTTPS gateway 存活；`/api/health` 才包含 Shioaji 連線、最後 Tick 與延遲。
- 若未來加入下單，必須先完成模擬交易、固定 IP 白名單、CA 安全保存、訂單冪等、持倉核對、最大虧損與 Kill Switch；目前版本仍完全不能下單。

### 常見問題

- `歷史 K 棒為空`：新 SQLite 首次啟動還沒有資料；先讓 Replay 或 Shioaji 收到 Tick。
- `Dashboard 顯示重新連線`：確認後端 URL、CORS 的 `MARKET_ALLOWED_ORIGINS`、TLS 憑證和 `/api/health`。
- `沒有 Tick 但仍顯示連線`：這是預期行為；無成交不等於斷線，heartbeat 才是連線判斷依據。
- `重啟後 Mock 不再更新`：既有 SQLite 已記錄相同 replay Tick；測試新一輪可刪除測試用 DB，正式資料庫不要任意刪除。
- `換月`：訂閱 `TMFR1`，健康檢查與 K 棒訊息的 `contract` 顯示實際契約；換月前應人工核對流動性與切換時間。

## 立即執行示範

```bash
python -m tw_quant demo
```

這會在 `output/demo/` 產生：

- `demo_2330_1m.csv`：合成資料，不是真實 2330 行情
- `trades.csv`：逐筆交易與成本
- `equity.csv`：權益與回撤
- `summary.json`：績效摘要
- `equity.png`：權益曲線

## 微型臺指期貨 5 分 K 夜盤

`tw_quant/futures.py` 支援讀取期交所近 30 個交易日逐筆成交 CSV，篩選
微型臺指期貨（`TMF`）指定月份與夜盤時段，並聚合為 5 分 K。

2026/8/24 夜盤使用 `TMF 202609`，時段為 2026/8/24 15:00 至
2026/8/25 05:00。期貨損益依契約乘數每點新臺幣 10 元計算；券商手續費、
交易稅與滑價均為可調參數。官方逐筆檔的「成交數量(B+S)」為買賣雙方合計，
聚合成交量時會除以 2。

本次固定參數回測輸出在 `output/tmf_20260824_night/`。單一夜盤結果只能驗證
資料與撮合流程，不能推論策略的長期績效。

取得期交所逐筆 CSV 後，可用 CLI 重跑任何指定夜盤：

```bash
python -m tw_quant futures-night \
  --csv Daily_2026_08_25.csv \
  --product TMF \
  --contract-month 202609 \
  --session-start "2026-08-24 15:00" \
  --session-end "2026-08-25 05:00" \
  --last-entry "2026-08-25 04:30" \
  --interval 5min \
  --output output/tmf_20260824_night
```

輸出包含 `bars.csv`、`trades.csv`、`equity.csv` 與 `summary.json`。

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
