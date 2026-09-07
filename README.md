# TCJH Volleyball — 排球活動報名系統

給校內排球社／班級使用的活動場次管理系統：公告、場次建立、報名、投票喬時間、歷史封存，後端是一個單一 Flask API，前端另外部署在 `/var/www/volleyball`（靜態網頁，不含在本 repo）。

## 系統架構

```
前端 (靜態網頁, /var/www/volleyball)
   │  fetch /api
   ▼
Nginx (tcjhvolley.felidae.tw) ──proxy_pass /api──► Gunicorn (127.0.0.1:5000)
                                                          │
                                                       app.py (Flask)
                                                          │
                                                      SQLite (instance/volleyball.db)

crontab (每天 03:00)
  └─ 備份 volleyball.db → ~/db_backups/ → rclone sync 到 Google Drive
```

| 元件 | 說明 |
|------|------|
| Flask 3 + Flask-SQLAlchemy | 單檔 API 伺服器（`app.py`） |
| Gunicorn (4 workers) | WSGI 伺服器 |
| Nginx | 反向代理 `/api`，其餘路徑回傳靜態前端檔案 |
| SQLite | 資料庫，儲存於 `instance/volleyball.db`（不進版控） |
| crontab + rclone | 每日 03:00 備份資料庫並同步到 Google Drive |

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

```bash
# 1. 建立虛擬環境與安裝套件
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
vim .env   # 填入 ADMIN_PWD

# 3. 初始化資料庫（首次執行 app.py 會自動 create_all，
#    或想從舊系統匯入資料時用 import_csv.py，見下方）

# 4. 設定 systemd 服務
sudo cp deploy/volleyball.service /etc/systemd/system/
sudo systemctl enable volleyball
sudo systemctl start volleyball

# 5. 設定 Nginx（含 Certbot 簽的 SSL 憑證路徑，需自行申請）
sudo cp deploy/nginx.conf /etc/nginx/sites-available/volleyball
sudo ln -s /etc/nginx/sites-available/volleyball /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 常用維運指令

```bash
sudo systemctl restart volleyball   # 重啟服務
sudo journalctl -u volleyball -f    # 查看即時 log

# 手動備份資料庫
cp instance/volleyball.db instance/volleyball_backup_$(date +%F).db
```

### 資料庫備份

伺服器上的 crontab 每天 03:00 執行：

```
0 3 * * * rm -f ~/db_backups/*.db && cp /opt/volleyball/instance/volleyball.db ~/db_backups/volleyball_$(date +%F).db && rclone sync ~/db_backups/ gdrive:TCJH_Volleyball_Backup/
```

會覆蓋掉前一天本機的備份檔，同步後只在 Google Drive 保留歷史版本。

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

- 前端網頁本身不在這個 repo 裡（部署在 `/var/www/volleyball`，由 Nginx 直接讀取靜態檔案）。
- `instance/volleyball.db` 目前只有每日排程備份到 Google Drive，沒有納入本 repo（也不應該，內含使用者真實報名資料）。
