# TMF Level 2 系統化量化交易平台

[![CI](https://github.com/3pwei/codex-tw-quant-trading-system/actions/workflows/ci.yml/badge.svg)](https://github.com/3pwei/codex-tw-quant-trading-system/actions/workflows/ci.yml)

以微型臺指期貨（TMF）為核心的研究與 Paper Trading 平台，整合 Shioaji 即時行情、歷史回測、動態回放、多週期策略、事件驅動模擬成交、帳戶風控及監控。正式環境部署於 AWS Lightsail，使用 Cloudflare Access 保護入口。

> 本專案僅供研究與工程驗證，不構成投資建議。Production 預設不會向外部券商送出
> 真實委託。只有另行啟用、通過 readiness review 且由操作人員限時 ARM 的 Manual
> Live Canary 可送出一口 allowlisted 委託；Strategy、Paper 與 Live Shadow 永遠不能
> 抵達這條路徑。Strategy Auto Live 程式邊界已加入，但預設
> `LIVE_AUTO_ENABLED=false`，必須另行完成 server preflight 與限時人工 ARM；一般部署、
> 重啟與 read-only 模式仍拒絕所有 broker write。

## 核心能力

| 領域 | 已完成 |
|---|---|
| 行情 | Shioaji quote-only、Mock Replay、Tick callback → Queue → Worker、1 分 K 聚合、WebSocket |
| 週期 | `1m`、`5m`、`10m`、`15m`、`30m`、`1h`、`1d`、`1w`，共用同一份 1 分 K 資料 |
| 策略 | 14 套基本策略（含三種共用 Dow Channel 結構的進場邏輯）、多週期 Setup／Entry／Exit／Risk、ALL／ANY、三層組合策略引用 |
| 版本 | 不可變版本、參數快照、名稱唯一、封存、引用保護及回測追溯 |
| 執行 | Backtest／Replay／Paper 共用事件語意；Live foundation 提供 durable outbox、callback audit、Recovery Lock、三方對帳、Execution Worker 與 Live Shadow |
| 執行目標 | `owner_user_id → ExecutionTarget → BrokerAccountRef` durable ownership；opaque target ID、exact lookup、無 fallback |
| 風控 | Paper 與 Live policy 分離；Live Shadow 依 broker truth 做 account／owner portfolio limits、quote／session／expiry／capability gates |
| 平台 | Cloudflare OTP、FastAPI RBAC、申請與審核、Rate Limit、Request Size Limit、稽核紀錄 |
| 穩定性 | 重啟復原、SQLite verified backup、Queue／WebSocket／DB／主機監控、五種服務狀態 |
| UI | `/trade/` 整合 Observe／Paper／Live Shadow／Live Auto；真實路徑明確標示 REAL MONEY 且預設停用 |
| 部署 | Docker、Caddy、AWS Lightsail、GitHub Actions、Python 套件鎖定 |

Level 2 工程能力已實作；每個正式候選版本仍須依 [Level 2 完成標準](docs/level2-definition-of-done.md) 留存四小時 soak 與人工驗收證據。Live execution foundation 已具備 simulation、production read-only 與 Manual Live Canary 組裝邊界。Canary 是預設停用、單一 owner／account／contract／一口且人工 ARM 的受限能力，不代表 Strategy Auto Live 或一般實盤能力。

## 系統架構

```mermaid
flowchart TD
    A["Shioaji quote-only 或 Replay"] --> B["MarketDataProvider"]
    B --> C["Tick Queue 與 K 棒 Worker"]
    C --> D["SQLite 1 分 K 與事件紀錄"]
    D --> E["Strategy、Risk、Execution"]
    E --> F["Backtest、Replay、Paper"]
    F --> G["FastAPI REST 與 WebSocket"]
    G --> H["Next.js 交易工作台"]
    E --> I["Durable Live Persistence Boundary"]
    I --> J["Execution Target<br/>BrokerAccountRef"]
    J --> K["Broker Registry"]
    K --> L["Broker Account Runtime"]
    L --> M["BrokerPort · Registry Locked"]
    M --> N["Shioaji Production Read-Only<br/>Login · CA · Broker Truth"]
    M -.-> O["Future Adapter"]
```

- 行情 Provider 與 Broker／Order Executor 是獨立邊界；production 行情只讀取
  `MARKET_SJ_*` quote credentials，`SJ_API_KEY`、`SJ_SECRET_KEY` 與 CA 只能屬於
  無 public port 的 execution container。
- Tick callback 只做正規化與非阻塞入 Queue，不寫 DB、不算指標、不推送前端。
- Live、Replay、Backtest 共用 `KBar` 與策略；Backtest、Replay、Paper 共用事件、風控及成本模型。
- 行情資料全平台共用；策略、版本、回測與 Paper 資料依 `owner_user_id` 隔離。
- Live 下單基礎先持久化 order／outbox，再由 Recovery Lock 控制 dispatch；callback 只觸發 audit 與券商狀態 refresh。
- execution connection、Recovery、health 與 secret resolution 均使用
  `broker_name + account_id`；相同帳號字串在不同券商不會形成同一個執行身分。
- `ExecutionTarget` 是 broker-neutral 的 durable ownership/routing metadata；只保存不透明
  `secret_ref`，不解析或持有任何 API key、密碼或 CA material。`active` 也不代表 ARMED
  或允許實盤。
- 每筆 Live order 與 outbox 都持久化 `BrokerAccountRef` target；restart 後依原 target
  精確解析，未知、locked 或 unavailable target 一律拒絕，不會 fallback 至其他券商。
- `BrokerRegistry` 以 O(1) lookup 解析長生命週期 account runtime；每個 registration
  分別宣告 `BrokerCapabilities` 與 `BrokerInstrumentMapper`，Strategy 與 Risk
  不依賴 adapter 類別或券商能力。
- `ExecutionSupervisor` 分別管理每個 `BrokerAccountWorker`；啟動先鎖定、連線後立即
  對帳，只有券商 orders、fills、positions 全部一致才進入 `READY_READ_ONLY`。
- Callback 只是低延遲通知：先保存稽核證據，再 refresh broker truth；45 秒週期 snapshot
  負責 missed/out-of-order callback 的 eventual recovery，同帳戶禁止重疊對帳。
- Shioaji production client 與 simulation client 是不同 class 與 lifecycle；read-only
  SDK I/O 由單一 async lock 序列化後送入 worker thread，health 只讀 cached state。
- production callback 只保留 allowlisted routing 欄位並投入 bounded queue；滿載時丟棄、
  記錄 degraded metrics，不能寫 DB 或直接改變 Position。

## Systematic Paper Trading

`/trade/` 將交易工作區明確分成三種模式：`OBSERVE` 只看 closed-bar 策略訊號，
`MANUAL PAPER` 保留手動模擬委託，`PAPER AUTO` 則以不可變策略快照、帳戶風控與
明確 ARM 控制自動模擬進出。Pause 只停止新進場，既有部位仍接受 canonical strategy
exit 與 server-owned Stop Loss／Take Profit；Stop 不會偷偷強制平倉，既有部位繼續由
保護價管理至 flat，使用者也可手動緊急平倉。

```mermaid
flowchart TD
    A["Closed Market Bar"] --> B["Strategy Runtime"]
    B --> C["Paper Auto Controller"]
    C --> D["Account Risk"]
    D --> E["Simulated Broker"]
    E --> F["Paper Position"]
```

**Automated Paper Trading ≠ Live Trading。** Public Application 仍為 Shioaji quote-only，
不載入 execution CA。獨立 execution worker 可選擇建立 production read-only client，
但 Registry、admission gate 與 client write methods 都維持 locked；即使 Runtime 顯示
`PAPER AUTO · ARMED`，所有委託也只會進入平台的 Simulated Broker。

## Live Shadow

`live_shadow` Runtime 保存 immutable strategy snapshot 與明確的 opaque execution target。
每個 closed-bar decision 只在本機記憶體與 SQLite 上完成：reconciled broker positions、
working orders、owner 跨券商曝險、server-owned Live limits、真實 BidAsk、合約規格與
`BrokerCapabilities` 共同決定 marketable-limit IOC request 是否「would submit」。

Live Shadow 與 Live Trading 是不同 code path。`ShadowExecutionService` 只接受
`ShadowExecutionStore`，composition 不注入 `LiveOrderManager`、`BrokerPort` 或 live
outbox。結果依 decision、broker/account 與 policy version 冪等保存；UI 只顯示遮罩帳號，
也沒有 ARM／BUY／SELL／CANCEL／FLATTEN Live 控制。Production 預設
`LIVE_SHADOW_ENABLED=false`，即使明確啟用，real submit/cancel 與 live outbox 仍為零。

人工驗收見 [Live Shadow Acceptance](docs/live-shadow-acceptance.md)。

Manual Live Canary 上線前必須逐項通過
[Production Canary Acceptance](docs/live-production-canary-acceptance.md)；異常停機與回退依
[Canary Rollback](docs/live-canary-rollback.md)。部署與 CI 不會自動完成這項人工授權。

Dow Channel 策略共用同一套 confirmed pivot、ATR、HH／HL、LH／LL 與平行軌道偵測；`Dow Channel Pullback` 在邊界測試後收回時順勢進場，`Dow Channel Reversal` 在反向突破趨勢軌道時反向進場，`Dow Channel Momentum` 則沿既有趨勢突破外側軌道。既有 key `linear_channel_breakout` 保留為 Momentum 的 canonical key，確保歷史回測、參數快照及組合策略引用持續有效。

`live_auto` 沿用既有 Live Risk、Execution Policy、`LiveOrderManager`、durable outbox、
Recovery Lock、BrokerRegistry 與 Position Guardian。Runtime 預設 PAUSED／DISARMED；
restart 或 reconnect 不會 ARM。第一版固定單 broker／account／contract／runtime、quantity 1，
禁止 pyramiding 與 fallback；所有 exit 與 protection 只由 Guardian 協調。詳見
[Strategy Auto Live Runbook](docs/strategy-auto-live-runbook.md)。

主要程式位置：

| 路徑 | 職責 |
|---|---|
| `tw_quant/market_data/` | Provider 介面與 Shioaji／Replay Adapter |
| `tw_quant/market/` | Tick、KBar、交易時段與多週期聚合 |
| `tw_quant/strategy/` | 基本策略、參數與組合策略 |
| `tw_quant/events/` | 事件契約、虛擬時鐘與確定性事件迴圈 |
| `tw_quant/risk/` | 策略與帳戶風控 |
| `tw_quant/execution/` | 模擬成交、部位及損益帳本 |
| `tw_quant/broker/` | Broker 契約、訂單生命週期、durable outbox、callback audit、三方對帳與 Execution Worker |
| `tw_quant/live/` | FastAPI、WebSocket、監控與 SQLite Repository |
| `tw_quant/paper/`、`tw_quant/replay/` | Paper 與 Replay 交易 Session |
| `dashboard/app/` | Next.js 操作介面 |
| `deploy/lightsail/` | Caddy、Docker Compose 與部署腳本 |

## AI-native 開發流程

這個專案採用 **AI-assisted、human-governed** 的開發方式。我負責產品需求、架構決策、風險邊界、驗收標準及上線決策；Codex 協作完成程式、測試、文件與問題診斷。所有修改必須通過 PR 與自動化品質門檻，才允許部署。

```mermaid
flowchart TD
    A["需求、風險與驗收標準（我）"] --> B["架構設計與任務拆分（我與 Codex）"]
    B --> C["實作、測試與文件（Codex 協作）"]
    C --> D["PR 與 CI 品質門檻"]
    D --> E["功能驗收與上線決策（我）"]
    E --> F["Lightsail 部署與正式監控"]
    F --> A
```

每次迭代都保留需求脈絡、PR、測試結果、部署紀錄及正式環境回饋，讓 AI 產出的變更可審查、可重現、可回滾，而不是直接將生成程式碼送進正式環境。

## 主要頁面

| 路徑 | 功能 |
|---|---|
| `/` | 系統、行情與策略總覽 |
| `/trade/` | 即時行情與 Paper Trading 工作台 |
| `/backtest/` | 最長 31 天的歷史回測與可縮放交易圖表 |
| `/replay/` | 動態歷史行情與隔離模擬交易 |
| `/history/` | 回測執行紀錄、按需載入的交易圖表、績效明細與批次刪除 |
| `/strategies/` | 基本策略參數管理 |
| `/composite-strategies/` | 多週期組合策略與版本管理 |
| `/settings/` | 管理員監控與系統狀態 |
| `/admin/users/` | 帳號申請、角色及交易模式管理 |

舊 `/live/` 與 `/paper/` 會轉址至 `/trade/`，避免建立重複 WebSocket 連線。

## 快速啟動

需求：Python 3.10–3.12、Node.js 22。Python 依賴以 `uv.lock` 固定版本與雜湊。

```bash
git clone https://github.com/3pwei/codex-tw-quant-trading-system.git
cd codex-tw-quant-trading-system

python -m venv .venv
source .venv/bin/activate
python -m pip install "uv==0.11.33"
uv sync --locked --extra server --extra test

cp .env.example .env
uv run --locked --extra server uvicorn tw_quant.live.api:create_app \
  --factory --host 0.0.0.0 --port 8000 --env-file .env
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 與 `Copy-Item .env.example .env`。

另一個終端啟動 Dashboard：

```bash
cd dashboard
npm ci
NEXT_PUBLIC_MARKET_API_URL=http://localhost:8000 npm run dev
```

開啟 <http://localhost:3000/>；FastAPI 文件位於 <http://localhost:8000/docs>。

Mock 模式預設重播 `data/mock_tmf_ticks.csv`，不需要券商憑證。

### Shioaji 行情

開發環境加入 Shioaji 時保留測試依賴：

```bash
uv sync --locked --extra server --extra test --extra shioaji
```

`.env` 至少設定：

```dotenv
MARKET_DATA_PROVIDER=shioaji
MARKET_CONTRACT=TMFR1
MARKET_HISTORY_DAYS=30
MARKET_HISTORY_LIMIT=50000
MARKET_SJ_API_KEY=your-market-data-key
MARKET_SJ_SECRET_KEY=your-market-data-secret
MARKET_SJ_PRODUCTION=true
```

服務只載入歷史與即時行情，不載入 CA、不啟用外部下單。個人 Shioaji 行情不代表具有多人展示或轉發授權。

## 安全與權限

正式環境使用兩層控制：

1. Cloudflare Access 以 One-time PIN 驗證 Email。
2. FastAPI 以 `app_users`、角色、權限及帳號狀態決定功能存取。

Cloudflare Policy：

- Action：`Allow`
- Include：`Everyone`
- Require：`Login Methods = One-time PIN`

任何 Email 都能驗證身分，但只有 `app_users` 中 `active` 的帳號能使用平台。未開通者可送出申請，由管理員在 `/admin/users/` 核准或拒絕。

Production 必要設定：

```dotenv
PLATFORM_ENVIRONMENT=production
MARKET_ACCESS_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=team.cloudflareaccess.com
CF_ACCESS_AUD=replace-with-application-audience-tag
PLATFORM_AUTHORIZATION_MODE=enforced
PLATFORM_BOOTSTRAP_ADMIN_EMAILS=owner@example.com

RATE_LIMIT_ACCESS_REQUESTS_PER_HOUR=5
RATE_LIMIT_BACKTESTS_PER_MINUTE=10
RATE_LIMIT_REPLAY_PREPARES_PER_MINUTE=10
RATE_LIMIT_ORDERS_PER_MINUTE=30
API_MAX_REQUEST_BODY_BYTES=262144
```

`API_MAX_REQUEST_BODY_BYTES` 必須同時設定於 `market.env` 與 `gateway.env`。Production 若缺少 Cloudflare、enforced authorization 或 Bootstrap Admin，服務會拒絕啟動。

角色：

- `researcher`：行情、策略與回測。
- `trader`：研究功能及自己的 Paper Trading。
- `admin`：帳號、設定、監控與稽核；必須明確切換為 `paper` 才能模擬下單。

## 部署與復原

正式環境使用 Caddy + FastAPI + Next.js static dashboard，資料保存在 Docker named volume `tw-quant-lightsail_market-data` 的 `/data`。

部署有兩種觸發方式：

- PR 合併後，`master` CI 成功即自動執行 Lightsail deployment。
- `workflow_dispatch` 可指定一個屬於 `master` 的完整 commit SHA 手動部署。

部署流程會重新執行 Python 測試、Dashboard lint/build、SQLite Online Backup、容器健康檢查，以及公開 `/healthz` 的 `200 + ok` 驗證。Cloudflare 必須為 `/healthz` 設定精確的 Bypass policy。

詳細程序：

- [Level 2 正式運行與監控](docs/level2-operations.md)
- [部署驗收清單](docs/deployment-acceptance-checklist.md)
- [故障復原手冊](docs/disaster-recovery.md)
- [Paper Trading 操作手冊](docs/paper-trading-guide.md)
- [Automated Paper Trading 四小時驗收](docs/automated-paper-trading-acceptance.md)
- [Replay Trading 操作手冊](docs/replay-trading-guide.md)
- [帳戶風控](docs/account-risk.md)
- [事件引擎](docs/event-engine.md)
- [程式架構與依賴規則](docs/architecture.md)
- [訂單生命週期與 Live 安全規則](docs/order-lifecycle.md)
- [Live Execution Security Boundary](docs/live-execution-security-boundary.md)
- [Multi-Broker Execution Architecture](docs/architecture.md#multi-broker-execution-architecture)
- [Live Read-Only Recovery 驗收與 Soak](docs/live-read-only-acceptance.md)

## API 概覽

| 類別 | 主要端點 |
|---|---|
| 身分 | `GET /api/me`、`POST /api/access-requests` |
| 行情 | `GET /api/health`、`GET /api/kbars`、`WS /ws/market/{symbol}` |
| 策略 | `/api/strategies`、`/api/composite-strategies`、`/api/strategy-signals`、`/api/trading-runtimes`（Observe／armed Paper Auto entry 與 managed exit） |
| 回測 | `/api/backtest`、`/api/backtest-runs`；新結果包含向後相容的 `visualization.schema_version=1` 診斷資料，History detail 不重複傳送完整 points，chart endpoint 才按範圍載入；執行紀錄可逐筆勾選或批次刪除 |
| 回放 | `/api/replay/prepare`、`/api/replay/sessions/{session_id}` |
| Paper | `/api/paper/account`、`/api/paper/orders`、`/api/paper/fills`、`/api/paper/kill-switch` |
| 管理 | `/api/admin/users`、`/api/admin/access-requests`、`/api/admin/health`、`/api/admin/audit` |

## CLI 與測試

```bash
# 完整 Python 測試
uv run --locked --extra server --extra test python -m unittest discover -s tests -v

# 短版 Level 2 soak
uv run --locked --extra server --extra test python -m tw_quant level2-soak \
  --duration-seconds 60 \
  --tick-interval-seconds 0.1 \
  --output output/level2-soak.json

# 前端驗證
cd dashboard
npm ci
npm run lint
npm run build
```

其他 CLI：`demo`、`backtest`、`futures-night`、`sqlite-backup`、`sqlite-restore`。

CI 會驗證 Python 3.10／3.12、Dashboard、Docker Compose、FastAPI + Shioaji image、Caddy 及 gateway health route。`pyproject.toml` 或套件版本變更後必須更新 `uv.lock`；`uv sync --locked` 會在鎖檔不一致時失敗。

TMF 研究預設成本：契約乘數每點 NT$10、每邊手續費 NT$10、交易稅率 `0.00002`、每邊滑價 1 點。這些是可調整的研究假設，不是券商報價或成交保證。

## 已知限制與 Roadmap

目前限制：單一 TMF 商品、單機 SQLite、不含完整委託簿與實盤部分成交流程，也不處理漲跌停／暫緩撮合。Multi-Broker routing、Shioaji production read-only adapter 與 per-account periodic reconciliation 已完成，但沒有第二家 production adapter；尚未具備 CA／金鑰輪替、原生保護委託／OCO、人工 mismatch／UNKNOWN 處理介面及完整營運解鎖流程。

下一階段優先順序：

1. 接入合法授權的歷史資料，建立 Parquet 資料層與資料品質報告。
2. 加入多標的、風險預算、walk-forward 與樣本外驗證。
3. 增加外部告警與長時間正式環境監控證據。
4. 實盤擴大前評估 PostgreSQL，完成 CA 安全輪替、保護委託、UNKNOWN／mismatch 人工覆核及法規／授權確認。
5. 資料品質與研究流程成熟後，再評估 Regime Detection、Feature Store 與 ML 策略。

## Manual Live Order Canary

Manual Live Canary 是唯一可抵達真實券商 write API 的路徑，但 Production 預設仍為
`LIVE_CANARY_ENABLED=false`。它只允許一個 server-side allowlisted owner、一個
opaque execution target、一個商品／契約、單筆一口，以及經驗證的
`MarketableLimitIOCPolicy`。策略 runtime、Paper Auto 與 `live_shadow` 無法取得
`LiveExecutionSink`。

真實動作需要部署設定與 5–15 分鐘、重啟即清除的人工 ARM。Public API 先在同一
SQLite transaction 建立 canonical Live order 與 outbox；隔離的 execution-worker
commit 後才 claim，並在 SDK call 前重新檢查 ARM、Recovery、broker/CA readiness 與
kill switch。Ambiguous submit/cancel 進入 `UNKNOWN` 且永不自動 retry。合併或部署
不代表 Canary 已啟用，也不得取代第一次人工 readiness review。操作方式見
`docs/live-canary-runbook.md`。

## Live Position Guardian

`LIVE_POSITION_GUARDIAN_ENABLED=false` is the production default. When explicitly
enabled together with Manual Live Canary, the dedicated execution process rebuilds
durable managed positions only from reconciled broker fills and positions. Stop-loss
and take-profit levels use the actual average fill price; partial fills protect only
the broker-confirmed filled quantity. Strategy pause or process loss does not transfer
this responsibility back to Strategy Runtime.

All Guardian exits are platform-created, durable, reduce-only orders. Emergency
`FLATTEN` first halts entry, cancels platform-owned working entry orders, reads
reconciled broker truth, reserves a reduce-only close, reconciles, verifies flat, and
remains locked for operator review. It never guesses while disconnected and never
retries an `UNKNOWN` exit. This is **platform-managed protection**, not a broker-native
stop/OCO: protection depends on the execution worker, market-data quote persistence,
network connectivity, and broker availability.
