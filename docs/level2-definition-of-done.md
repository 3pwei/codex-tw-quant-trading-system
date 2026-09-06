# Level 2 完成標準

本平台的 Level 2 定義為：事件驅動的 Backtest／Replay／Paper Trading 共用領域事件與
風控語意，行情 callback 不阻塞，Queue 可控，服務可重啟復原，並具備可操作的監控、
部署與故障復原流程。這不代表已具備實盤下單或 HFT 能力。

## 必要能力

| 領域 | 完成標準 | 證據 |
|---|---|---|
| 行情 | Tick callback 只正規化與入 Queue；Worker 聚合 K 棒 | soak JSON、Queue 指標 |
| 事件 | 時間＋FIFO 決定順序，event ID／委託 key 冪等 | 自動化測試 |
| 回測／回放 | 與 Paper 共用事件、策略與風控語意 | 架構測試與操作驗收 |
| Paper | 帳號隔離、風控、持倉、成交、reduce-only | API 測試與雙帳號驗收 |
| 復原 | 重啟恢復持倉、損益、Kill Switch、冪等狀態 | 重啟與備份復原紀錄 |
| 穩定性 | 行情中斷禁止新倉，不補送失敗委託 | 斷線／重連測試 |
| 監控 | 行情、Queue、WS、DB、Paper、主機指標與五種狀態 | `/settings/` 截圖／健康資料 |
| 部署 | CI gate、SQLite verified backup、容器與公開健康檢查 | Actions run URL |

## 效能基準

- Tick callback 最大 10 ms；callback 不寫 SQLite、不計算指標、不推 WebSocket。
- 驗收結束 Queue 必須歸零、不得 dropped tick，高水位不得超過 500。
- Tick Worker 平均小於 50 ms、單筆最大低於 1 秒。
- SQLite 平均寫入小於 20 ms、單次最大低於 500 ms。
- Provider → API 行情延遲不超過 1 秒；正式環境另記錄實際 P95。
- Paper 委託 CI 基準：200 筆平均小於 100 ms、單筆最大低於 1 秒。

上述是退化防線，不是券商 SLA 或 HFT 延遲承諾。正式環境若硬體基準需要調整，必須
在驗收紀錄寫明機型、負載、原門檻與調整理由。

## 驗收層級

1. 每次 CI 執行完整單元／整合測試與短版 synthetic soak。
2. 發版候選版本執行至少 4 小時 synthetic soak：

   ```bash
   python -m tw_quant level2-soak \
     --duration-seconds 14400 \
     --tick-interval-seconds 0.1 \
     --output output/level2-soak.json
   ```

3. 正式站在實際交易時段連續觀察至少 4 小時，記錄開始／結束健康資料、實際行情
   延遲、Queue 高水位、斷線次數與主機資源。
4. 依部署清單人工完成手機網路切換、WebSocket 重連、Market API／主機重啟、雙帳號
   隔離、Kill Switch 與 SQLite 備份復原。

只有四層全部有證據且沒有未處理的 Severity 1／2 問題，才可標記 Level 2 正式完成。
短版 CI soak 通過不等於已完成四小時正式站驗收。
