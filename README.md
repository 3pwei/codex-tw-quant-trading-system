# 台股分鐘線當沖量化回測 MVP

這是一套可直接執行、可逐步擴充的 Python 回測骨架。第一版聚焦「單一台股、1 分 K、日內平倉」，先把資料驗證、策略訊號、下一根 K 棒撮合、交易成本、風險控制與績效報告做正確，再擴充多標的選股、模擬交易與券商實盤。

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

## 安裝

建議 Python 3.10 以上。

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

python -m pip install -e .
```

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
