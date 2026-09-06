# 故障復原手冊

本手冊適用於 FastAPI／Market API 重啟、整台 Lightsail 重啟、SQLite 損壞或部署後
需要回復資料。平台目前是單機 SQLite 架構，RPO 取決於最後一份已驗證備份，RTO
取決於映像重建與資料復原時間。

## 一般服務重啟

在 `/opt/tw-quant/repo` 執行：

```bash
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml restart market-api
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml ps
```

重啟後確認：

1. `market-api` 為 `healthy`，gateway 為 running。
2. `/healthz` 回傳 HTTP 200 且本文完全等於 `ok`。
3. 管理員 `/settings/` 的 Paper Recovery 為 healthy。
4. 重啟前持倉、已實現損益、當日交易次數與 Kill Switch 狀態一致。
5. 相同 Idempotency-Key 重送只回傳原委託。

## 整台主機重啟

執行 `sudo reboot` 前記錄目前 commit、容器狀態及最新備份。主機恢復後確認 Docker
已啟動，因服務採 `restart: unless-stopped`，容器應自動恢復。依照上述五項逐一驗收，
並額外確認 Caddy TLS 與 Shioaji Session 已重新連線。

## SQLite 備份驗證

每次部署會使用 SQLite Online Backup 建立
`/data/live_market.backup-<commit前12碼>.sqlite3`，且只有
`PRAGMA integrity_check` 等於 `ok` 才繼續部署。也可手動建立：

```bash
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml exec -T market-api \
  python -m tw_quant sqlite-backup \
  --source /data/live_market.sqlite3 \
  --destination /data/live_market.manual-backup.sqlite3
```

## SQLite 復原

復原會覆寫正式資料，必須先停止 Market API。復原工具會先建立一份
`pre-restore-<UTC時間>` 回滾副本，並驗證備份與復原結果。

```bash
cd /opt/tw-quant/repo
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml stop market-api
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml run --rm --no-deps market-api \
  python -m tw_quant sqlite-restore \
  --backup /data/live_market.backup-<commit>.sqlite3 \
  --target /data/live_market.sqlite3
sudo docker compose --env-file /opt/tw-quant/config/compose.env \
  -f deploy/lightsail/docker-compose.yml up -d market-api gateway
```

復原後按照一般重啟驗收。若 Recovery Lock 顯示事件鏈不一致，保留資料與備份，禁止
新增曝險，不可刪除事件或直接解除自動 Kill Switch。

## 回滾判定

遇到以下任一情況停止交易並回滾：容器無法 healthy、公開 `/healthz` 非 `200 + ok`、
Paper Recovery degraded、SQLite integrity check 失敗、持倉／損益不一致，或行情 Queue
持續累積。回滾程式版本不等於回滾資料；除非確認 schema／資料損壞，優先只回滾
commit，避免把部署後的合法 Paper 事件一起清除。
