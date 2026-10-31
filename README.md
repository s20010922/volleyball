# TCJH Volleyball — 排球練習報名系統

土中排球練習的報名網站：https://tcjhvolley.felidae.tw

- **公告**：管理員發布的公告與守則
- **報名場次**：設定人數上限，報名時填姓名、攜帶球數、備註；額滿或重複報名會被擋下
- **投票喬時間**：列出幾個候選時間讓大家投票，截止後自動以最高票的時間開放報名
- **自動封存**：場次時間過了自動移到歷史紀錄，保留最終名單
- **管理員模式**：輸入管理密碼後可新增／刪除／封存場次、發布公告

## 架構

```
瀏覽器
  │ HTTPS
  ▼
Nginx (tcjhvolley.felidae.tw)
  ├─ /      → frontend/index.html（靜態檔）
  └─ /api   → Gunicorn 127.0.0.1:5000 → app.py (Flask) → SQLite instance/volleyball.db

cron 每天 03:00 → deploy/backup.sh → /var/lib/volleyball/backups → rclone → Google Drive
```

- 後端：Python 3、Flask 3、Flask-SQLAlchemy，單檔 `app.py`，只有一個 `/api` 路由
- 前端：單一 HTML 檔，純 JavaScript，不載入任何外部資源
- 資料庫：SQLite（不進版控）
- 部署：systemd + Gunicorn（2 workers）+ Nginx，以獨立系統帳號 `volleyball` 執行

## 專案結構

```
app.py                      Flask API
frontend/index.html         前端網頁
requirements.txt            Python 套件（固定版本）
.env.example                環境變數範本
deploy/
  setup.sh                  首次部署：建帳號、目錄、venv、systemd、備份排程
  volleyball.service        systemd 服務（含沙箱設定）
  nginx.conf                Nginx 站台設定（含頻率限制、安全標頭）
  backup.sh                 每日備份腳本
  volleyball.cron           備份排程（裝到 /etc/cron.d/volleyball）
  fail2ban-filter.conf      fail2ban：比對管理密碼錯誤
  fail2ban-jail.conf        fail2ban：封鎖規則
```

## 資料模型

| 資料表 | 內容 |
|--------|------|
| `Config` | 公告文字（單筆） |
| `Event` | 進行中的場次，`status` 為 `active`（開放報名）或 `vote`（投票中） |
| `Registration` | 報名紀錄：場次、姓名、攜帶球數、備註 |
| `Vote` | 投票紀錄：場次、姓名、選擇的時間 |
| `Archive` | 已結束的場次，含最終報名名單 |

資料表在 app 載入時自動建立（`db.create_all()`，已存在的表不會被動到）。

## API

只有 `/api` 一個路徑，`POST` 用 JSON body 的 `type` 欄位區分動作，必須帶 `Content-Type: application/json`。

**`GET /api`**：回傳公告、所有場次（含封存）、報名與投票紀錄。每次呼叫會先自動處理：
投票已截止的場次依最高票定案（沒人投票就直接封存），時間已過的場次移入 `Archive`。

**`POST /api`**

| `type` | 管理密碼 | 說明 |
|--------|:---:|------|
| `verify` | ✓ | 驗證管理密碼 |
| `updateAnnouncement` | ✓ | 更新公告 |
| `createEvent` | ✓ | 新增場次（`active` 或 `vote`） |
| `convertToActive` | ✓ | 投票場次手動轉為開放報名，可同時改標題／日期 |
| `archiveEvent` | ✓ | 手動封存場次 |
| `deleteEvent` | ✓ | 刪除場次及其報名／投票紀錄 |
| `register` | | 報名 |
| `cancelRegistration` | | 依姓名取消報名 |
| `vote` | | 投票（每人每場一次） |
| `cancelVote` | | 依姓名撤回投票 |

管理動作用 `adminPassword` 欄位帶密碼；密碼錯誤回 `403`。

## 環境變數

| 變數 | 說明 |
|------|------|
| `ADMIN_PWD` | 管理密碼。啟動時雜湊後存在記憶體比對，不會寫進資料庫；**未設定時程式拒絕啟動** |

## 主機上的配置

| 路徑 | 擁有者 | 權限 | 內容 |
|------|--------|------|------|
| `/opt/volleyball/` | 部署者 | 755 | 這個 repo 的 clone 與 venv；服務帳號只能讀 |
| `/opt/volleyball/instance/` | volleyball | 700 | SQLite 資料庫（服務唯一可寫的目錄） |
| `/opt/volleyball/.env` | volleyball | 600 | `ADMIN_PWD` |
| `/var/lib/volleyball/` | volleyball | 700 | 本機備份、專用 rclone 設定（只含 `[gdrive]`） |
| `/var/log/volleyball/` | volleyball | 750 | 備份 log |

## 首次部署

前提：DNS 已指向主機，已安裝 nginx、certbot、fail2ban、rclone、sqlite3、python3-venv。

```bash
# 1. clone 到 /opt/volleyball 並建立 .env（填入 ADMIN_PWD）
sudo install -d -o "$USER" -g "$USER" -m 755 /opt/volleyball
git clone https://github.com/s20010922/volleyball.git /opt/volleyball
cd /opt/volleyball && cp .env.example .env && vim .env

# 2. 建立帳號、目錄、venv、systemd 服務與備份排程（可重複執行，不會覆蓋既有資料）
sudo bash deploy/setup.sh

# 3. 授權 Google Drive 備份（headless 主機會請你在自己電腦跑 rclone authorize "drive"）
sudo -u volleyball rclone config reconnect gdrive: --config /var/lib/volleyball/rclone.conf

# 4. 申請憑證：先放一個只開 HTTP 的暫時站台讓 certbot 驗證
#    （直接啟用 deploy/nginx.conf 會因為憑證還不存在而讓 nginx -t 失敗）
printf 'server { listen 80; server_name tcjhvolley.felidae.tw; root /opt/volleyball/frontend; }\n' \
  | sudo tee /etc/nginx/sites-available/volleyball >/dev/null
sudo ln -s /etc/nginx/sites-available/volleyball /etc/nginx/sites-enabled/volleyball
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --nginx -d tcjhvolley.felidae.tw

# 5. 換上正式站台設定
sudo cp deploy/nginx.conf /etc/nginx/sites-available/volleyball
sudo nginx -t && sudo systemctl reload nginx

# 6. fail2ban（要在 nginx reload 之後，log 檔才存在）
sudo install -m 644 deploy/fail2ban-filter.conf /etc/fail2ban/filter.d/volleyball-admin.conf
sudo install -m 644 deploy/fail2ban-jail.conf /etc/fail2ban/jail.d/volleyball.local
sudo fail2ban-client reload
```

## 維運

### 更新程式

```bash
cd /opt/volleyball && git pull
sudo systemctl restart volleyball     # 只改前端時不需要重啟
```

改到 `deploy/` 裡的設定檔時，要另外重新安裝到系統位置（例如 `deploy/nginx.conf` → `/etc/nginx/sites-available/volleyball`）。

### 查看狀態與 log

```bash
systemctl status volleyball
sudo journalctl -u volleyball -f            # 服務 log
sudo tail /var/log/volleyball/backup.log    # 備份 log
```

### 備份與還原

每天 03:00 以 `volleyball` 帳號執行 `deploy/backup.sh`：用 `sqlite3 .backup` 取得一致的快照（服務寫入中也安全），
連同 `.env` 存到 `/var/lib/volleyball/backups/`，再上傳到 `gdrive:TCJH_Volleyball_Backup/daily/`。本機與雲端都保留 30 天。

```bash
# 手動備份
sudo -u volleyball /opt/volleyball/deploy/backup.sh

# 從備份還原（會先把目前的資料庫另存一份）
sudo systemctl stop volleyball
sudo cp -p /opt/volleyball/instance/volleyball.db /var/lib/volleyball/backups/volleyball_before_restore_$(date +%F_%H%M).db
sudo install -o volleyball -g volleyball -m 600 <備份檔.db> /opt/volleyball/instance/volleyball.db
sudo systemctl start volleyball
```

### 更改管理密碼

```bash
sudo sed -i 's/^ADMIN_PWD=.*/ADMIN_PWD=新密碼/' /opt/volleyball/.env
sudo systemctl restart volleyball      # 密碼只在啟動時讀取
```

### 解除 fail2ban 封鎖

```bash
sudo fail2ban-client status volleyball-admin
sudo fail2ban-client set volleyball-admin unbanip <IP>
```

## 安全措施

- **獨立帳號＋沙箱**：服務以 `volleyball` 帳號執行，`ProtectSystem=strict`、`ProtectHome`、`NoNewPrivileges`、`PrivateTmp`，
  只能寫 `instance/`；看不到 `/home`，也改不到自己的程式碼
- **資源上限**：`MemoryMax=300M`，避免影響同主機上的其他服務
- **管理密碼防暴力破解**：Nginx 對 `POST /api` 每個 IP 每分鐘 20 次（突發 20 次），超過回 `429`；
  fail2ban 在 10 分鐘內密碼錯 5 次時封鎖該 IP
- **HTTPS**：Let's Encrypt 憑證，HTTP 一律轉 HTTPS；安全標頭（HSTS、`X-Frame-Options`、`nosniff`、`Referrer-Policy`）
  共用 `/etc/nginx/snippets/isirl-security-headers.conf`；`server_tokens off`
- **防 XSS**：前端把使用者輸入的內容一律經過 `escapeHTML` 再顯示
- **不允許跨網域呼叫**：前端與 API 同網域，沒有 CORS 標頭，其他網站的 JavaScript 讀不到 API 回應

## 已知限制

- 取消報名、撤回投票只比對姓名，任何人輸入別人的姓名就能取消對方的報名
- 只有一組共用的管理密碼，沒有個別帳號
- SQLite 單機資料庫，備份只在每天 03:00 跑一次
