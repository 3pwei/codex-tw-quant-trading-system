# Replay Trading 操作手冊

Replay Trading 用於在單一歷史盤次中練習判斷與模擬下單。它與即時 `/trade/`
的 Paper 帳戶完全分離，不會連接券商，也不會改動正式 Paper 的持倉、委託或成交。

## 建立 Session

1. 進入 `/replay/`，選擇交易日、日夜盤、K 棒週期及策略圖層。
2. 點擊「建立回放」。伺服器會固定歷史快照並建立使用者專屬 Replay Session。
3. 每個 Session 具有獨立虛擬時鐘與暫存事件庫；每位使用者最多保留最近 3 個。
4. Session 不跨服務重啟保存，重新部署或重啟後須重新建立。

## 模擬下單

1. 播放、逐根前進或拖曳到預定時間，確認目前 K 棒與虛擬時間。
2. 選擇買進／賣出、口數及停損價後送出。
3. 伺服器以目前 Replay 游標對應的收盤價處理，不接受瀏覽器指定成交價或時間。
4. 委託重用 Paper 的持倉上限、單筆風險、成本、滑價及 Idempotency 保護。
5. 成交會顯示在回放圖表，並可在持倉／委託／成交頁籤查詢。

## 時間倒退與重設

- 時間軸向前移動時，伺服器按順序處理歷史 K 棒並更新未實現損益。
- 回到較早的 K 棒時，系統會清空該 Session 的持倉、委託與成交，再將虛擬時鐘
  推進到新位置。這是為了避免保留未來成交造成 future leakage。
- 「重設 Replay 帳戶」會回到第一根 K 棒並清除本次練習紀錄。
- 上述操作只影響目前 Replay Session，正式 Paper 帳戶不受影響。

## 安全與隔離

- Session ID 必須同時屬於目前登入使用者；其他帳號查詢時一律視為不存在。
- Replay 使用獨立的 `PaperTradingService` 與 SQLite Repository，不註冊即時行情
  listener，也不共用正式 Paper event table。
- Replay API 沿用 `backtest.run` 權限，因此 Researcher 可練習歷史情境，但不會
  因此取得即時 Paper 或實盤下單權限。
