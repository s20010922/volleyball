# TCJH Volleyball — 排球活動報名系統

給校內排球社／班級使用的活動場次管理系統：公告、場次建立、報名、投票喬時間、歷史封存，後端是一個單一 Flask API，前端另外部署在 `/var/www/volleyball`（靜態網頁，不含在本 repo）。

## 系統架構

```
前端 (靜態網頁, /var/www/volleyball)
   │  fetch /api
   ▼
Nginx (tcjhvolley.felidae.tw) ──proxy_pass /api──► Gunicorn (127.0.0.1:5000, 2 workers)
                                                          │
                                                       app.py (Flask)
                                                          │
                                                      SQLite (instance/volleyball.db)

crontab (每天 03:00, volleyball 帳號)
  └─ deploy/backup.sh：備份 db + .env → /var/lib/volleyball/backups/ → rclone copy 到 Google Drive
```

| 元件 | 說明 |
|------|------|
| Flask 3 + Flask-SQLAlchemy | 單檔 API 伺服器（`app.py`） |
| Gunicorn (2 workers, --preload) | WSGI 伺服器 |
| Nginx | 反向代理 `/api`，其餘路徑回傳靜態前端檔案 |
| SQLite | 資料庫，儲存於 `/opt/volleyball/instance/volleyball.db`（不進版控） |
| crontab + rclone | 每日 03:00 備份資料庫與 .env 到 Google Drive，保留 30 天 |

### 技術棧

- **語言／框架**：Python 3、Flask 3 + Flask-CORS（單檔 `app.py`，只有一個 `/api` 路由）
- **資料庫**：SQLite + Flask-SQLAlchemy ORM
- **前端**：純靜態網頁（HTML/JS，另外部署於 `/var/www/volleyball`，不含在本 repo）
- **部署**：Gunicorn（2 workers）＋ Nginx 反向代理，systemd 常駐
- **備份**：crontab 每日 03:00 + rclone 上傳到 Google Drive

## 資料模型

- **Config**：目前公告文字（單筆）。
- **Event**：進行中場次（`active` 開放報名／`vote` 投票喬時間）。
- **Registration**：某場次的報名紀錄（姓名、攜帶球數、備註）。
- **Vote**：`vote` 狀態場次的時間投票紀錄。
- **Archive**：已結束場次的封存（含最終報名名單字串）。

## API

只有一個路徑 `/api`，用 `type` 欄位區分動作。

### `GET /api`

回傳目前公告、所有場次（進行中＋封存）、全部投票與報名紀錄。每次呼叫都會先跑兩個自動維護：

1. **`auto_process_finished_votes`**：投票時間到了的 `vote` 場次，依最高票數決定最終時間並轉為 `active`；若沒人投票則直接封存。
2. **`auto_archive_events`**：場次時間已過的 `active`／`vote` 場次自動搬進 `Archive`，並清空該場次的報名與投票紀錄。

### `POST /api`（依 `type` 分派）

| `type` | 需要管理密碼 | 說明 |
|--------|:---:|------|
| `verify` | ✓ | 驗證管理密碼是否正確 |
| `updateAnnouncement` | ✓ | 更新公告文字 |
| `createEvent` | ✓ | 新增場次（`active` 或 `vote`） |
| `convertToActive` | ✓ | 投票場次手動轉為開放報名，可同時改標題／日期 |
| `archiveEvent` | ✓ | 手動將場次標記為封存 |
| `deleteEvent` | ✓ | 刪除場次（含歷史封存）及其報名／投票紀錄 |
| `register` | — | 一般使用者報名，額滿或重複報名會擋下 |
| `cancelRegistration` | — | 依姓名取消報名 |
| `vote` | — | 對 `vote` 場次投票，每人每場只能投一次 |
| `cancelVote` | — | 依姓名取消投票 |

管理動作用 `adminPassword` 欄位帶密碼，伺服器用 `werkzeug` 雜湊比對（見 `verify_admin`）。

## 部署

服務以獨立的系統帳號 `volleyball` 執行，與同主機上的其他服務（例如跑在 `felidae` 帳號的 ISIRL）隔開：

| 路徑 | 擁有者 | 權限 | 內容 |
|------|--------|------|------|
| `/opt/volleyball/` | 部署者 | 755 | 程式碼與 venv，服務帳號只能讀 |
| `/opt/volleyball/instance/` | volleyball | 700 | SQLite 資料庫（服務唯一可寫的目錄） |
| `/opt/volleyball/.env` | volleyball | 600 | `ADMIN_PWD` |
| `/var/lib/volleyball/` | volleyball | 700 | 本機備份、專用 rclone 設定（只有 `[gdrive]`） |
| `/var/log/volleyball/` | volleyball | 750 | 備份 log |

systemd 另外加了 `MemoryMax=300M`、`ProtectSystem=strict`、`ProtectHome=true`：服務看不到 `/home`，也寫不到資料庫目錄以外的地方。

```bash
# 1. 在 repo 目錄建立 .env（填入 ADMIN_PWD，未設定時程式會拒絕啟動）
cp .env.example .env && vim .env

# 2. 一次建好帳號、目錄、venv、systemd 服務與備份排程（可重複執行，不會覆蓋既有資料）
sudo bash deploy/setup.sh

# 3. 重新授權 Google Drive（存到 volleyball 專用的 rclone 設定）
sudo -u volleyball rclone config reconnect gdrive: --config /var/lib/volleyball/rclone.conf

# 4. 前端靜態檔放到 /var/www/volleyball，並設定 Nginx（含 Certbot 簽的 SSL 憑證，需自行申請）
sudo certbot certonly --nginx -d tcjhvolley.felidae.tw
sudo cp deploy/nginx.conf /etc/nginx/sites-available/volleyball
sudo ln -s /etc/nginx/sites-available/volleyball /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 5. fail2ban：管理密碼 10 分鐘內錯 5 次就封鎖該 IP（需在 nginx reload 之後，log 檔才存在）
sudo install -m 644 deploy/fail2ban-filter.conf /etc/fail2ban/filter.d/volleyball-admin.conf
sudo install -m 644 deploy/fail2ban-jail.conf /etc/fail2ban/jail.d/volleyball.local
sudo fail2ban-client reload
```

Nginx 另外做了：`POST /api` 每個 IP 每分鐘 20 次（突發 20 次）的頻率限制，超過回 429；
共用 `snippets/isirl-security-headers.conf` 的安全標頭（HSTS、X-Frame-Options 等）；`server_tokens off` 隱藏版本號。

資料庫在 app 載入時會自動 `create_all`（已存在的表不會動）；想從舊系統匯入資料見下方。

### 常用維運指令

```bash
sudo systemctl restart volleyball   # 重啟服務
sudo journalctl -u volleyball -f    # 查看即時 log

# 更新程式碼（/opt/volleyball 屬於部署者，不需 sudo）
cd /opt/volleyball && git pull && sudo systemctl restart volleyball

# 手動備份資料庫（同每日排程，會一併上傳 Google Drive）
sudo -u volleyball /opt/volleyball/deploy/backup.sh
```

### 資料庫備份

`/etc/cron.d/volleyball` 每天 03:00 以 `volleyball` 帳號執行 `deploy/backup.sh`，log 在 `/var/log/volleyball/backup.log`。

腳本用 `sqlite3 .backup` 取得一致的快照（Gunicorn 寫入中也安全），連同 `.env` 存到 `/var/lib/volleyball/backups/`，
再用 `rclone copy` 上傳到 `gdrive:TCJH_Volleyball_Backup/daily/`。本機與雲端都只保留 30 天。

> 舊主機留在 `gdrive:TCJH_Volleyball_Backup/` 根目錄的歷史備份不會被自動刪除。

## 從舊系統（CSV／試算表）匯入資料

`import_csv.py` 可以把舊資料一次性匯入資料庫，執行前把對應 CSV 放在專案根目錄：

- `Config.csv` — 公告文字
- `Events.csv` — 進行中場次
- `Archive.csv` — 歷史封存場次
- `Registrations.csv` — 報名紀錄
- `Vote.csv` — 投票紀錄

```bash
python import_csv.py
```

欄位比對不分大小寫、忽略前後空白，且已存在的 `event_id` 不會重複匯入。

## 環境變數

| 變數 | 說明 |
|------|------|
| `ADMIN_PWD` | 後台管理密碼（明文設定，程式啟動時會雜湊後存記憶體比對，不會存進資料庫） |

## 已知限制

- CORS 只允許 `https://tcjhvolley.felidae.tw`；若前端改放其他網域，需修改 `app.py` 的 `CORS(...)`。
- 前端網頁本身不在這個 repo 裡（部署在 `/var/www/volleyball`，由 Nginx 直接讀取靜態檔案）。
- `instance/volleyball.db` 目前只有每日排程備份到 Google Drive，沒有納入本 repo（也不應該，內含使用者真實報名資料）。
