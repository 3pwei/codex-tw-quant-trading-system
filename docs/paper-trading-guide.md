# Paper Trading 操作手冊

Paper Trading 是模擬成交環境，不會送單到永豐或其他券商。行情來自目前設定的
Market Data Provider，成交、持倉與風控結果則寫入平台 SQLite。

## 使用前確認

1. 帳號狀態必須是 `active`。
2. 角色必須是 Trader，或是已明確開啟 Paper Trading 的 Admin。
3. `trading_mode` 必須是 `paper`。
4. Paper 頁不得顯示 `PROVIDER DISCONNECTED`、`MARKET STALE`、Recovery Lock。
5. Kill Switch 必須解除，最新報價必須仍在有效期限內。

## 建立模擬市價單

1. 進入 `/trade/`，在同一頁確認即時圖表、連線、報價新鮮度、最新價與持倉。
2. 選擇買進或賣出、口數，建立新倉時輸入停損價。
3. 送出後等待畫面顯示結果；不要因網路延遲改用不同頁面重複建立委託。
4. 同一次點擊會攜帶固定 `Idempotency-Key`。手機重送或快速重複點擊只會建立一筆
   委託與成交。
5. 在「委託紀錄」與「成交紀錄」核對狀態、拒絕原因與成交價。

## 平倉與 Kill Switch

- 持倉列的平倉操作會送出 `reduce_only` 委託，不得反向建立新部位。
- Kill Switch 啟用後，新倉會被拒絕，但最新報價有效時允許 reduce-only 平倉。
- 行情已過期時不使用舊價模擬成交；即使是平倉，也必須等行情恢復並重新操作。

## 行情中斷

當畫面顯示 `provider_disconnected` 或 `market_stale`：

- 系統在建立 Order Intent 前拒絕新倉，不會暫存委託。
- 委託、成交與既有持倉仍可查看。
- 行情恢復後不會補送中斷期間的請求，使用者必須重新確認價格再送單。
- 若畫面與操作結果不一致，停止重試並通知管理員查看 `/settings/`。

## 常見拒絕原因

| 原因 | 處理方式 |
|---|---|
| 帳號尚未啟用 Paper | 管理員至 `/admin/users/` 將 Trading Mode 設為 Paper |
| `kill_switch_active` | 確認風險後由本人解除；Recovery Lock 不可直接繞過 |
| 行情過期／Provider 中斷 | 等待行情恢復後重新確認價格 |
| 超過持倉、交易次數或風險限制 | 不要反覆重送，先檢查帳戶風控狀態 |
| 相同 Idempotency-Key | 系統回傳原委託，這是防止重複成交的正常行為 |

正式驗收時至少使用兩個 Trader 帳號交錯操作，確認彼此看不到對方的委託、成交與
持倉。
