#!/usr/bin/env bash
# 一次性建立排球系統的獨立執行環境，從 repo 目錄執行：
#   sudo bash deploy/setup.sh
# 可重複執行：已存在的帳號、程式碼、venv、.env、rclone 設定都不會被覆蓋。
# Nginx 與 SSL 憑證不在這裡處理（需要前端檔案與網域憑證，見 README）。
set -euo pipefail

SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)
APP_DIR=/opt/volleyball
DATA_DIR=/var/lib/volleyball
LOG_DIR=/var/log/volleyball
DEPLOY_USER=${SUDO_USER:?請用 sudo 執行}
RCLONE_SRC=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)/.config/rclone/rclone.conf

[ "$(id -u)" -eq 0 ] || { echo "請用 sudo 執行"; exit 1; }

# 1. 系統帳號：不能登入，home 設在資料目錄（rclone 快取用）
if ! id volleyball &>/dev/null; then
    useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin volleyball
    echo "建立帳號 volleyball"
fi

# 2. 程式碼：屬於部署者，服務帳號只能讀不能改
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 "$APP_DIR"
if [ ! -f "$APP_DIR/app.py" ]; then
    tar -C "$SRC_DIR" --exclude=./venv --exclude=./.env --exclude=./instance \
        --exclude=./__pycache__ --exclude=./logs -cf - . \
        | sudo -u "$DEPLOY_USER" tar -C "$APP_DIR" -xf -
    # tar 會把目錄權限改回來源目錄的 775，這裡收回群組寫入權限
    chmod -R g-w,o-w "$APP_DIR"
    echo "複製程式碼到 $APP_DIR"
fi
if [ ! -x "$APP_DIR/venv/bin/gunicorn" ]; then
    sudo -u "$DEPLOY_USER" python3 -m venv "$APP_DIR/venv"
    sudo -u "$DEPLOY_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    echo "建立 venv"
fi

# 3. 只有服務帳號能碰的東西：資料庫、.env、備份、log
install -d -o volleyball -g volleyball -m 700 "$APP_DIR/instance" "$DATA_DIR" "$DATA_DIR/backups"
install -d -o volleyball -g volleyball -m 750 "$LOG_DIR"
if [ ! -f "$APP_DIR/.env" ]; then
    [ -f "$SRC_DIR/.env" ] || { echo "找不到 $SRC_DIR/.env，請先從 .env.example 建立"; exit 1; }
    install -o volleyball -g volleyball -m 600 "$SRC_DIR/.env" "$APP_DIR/.env"
    echo "安裝 .env"
fi

# 4. rclone：只把 [gdrive] 這組授權複製給 volleyball，看不到其他服務的雲端授權
if [ ! -f "$DATA_DIR/rclone.conf" ] && [ -f "$RCLONE_SRC" ]; then
    awk '/^\[/{p=($0=="[gdrive]")} p' "$RCLONE_SRC" > "$DATA_DIR/rclone.conf"
    chown volleyball:volleyball "$DATA_DIR/rclone.conf"
    chmod 600 "$DATA_DIR/rclone.conf"
    echo "複製 rclone [gdrive] 設定"
fi

# 5. systemd 服務與備份排程
install -m 644 "$APP_DIR/deploy/volleyball.service" /etc/systemd/system/volleyball.service
install -m 644 "$APP_DIR/deploy/volleyball.cron" /etc/cron.d/volleyball
systemctl daemon-reload
systemctl enable --now volleyball
systemctl restart volleyball

for _ in 1 2 3 4 5; do
    if curl -fs http://127.0.0.1:5000/api >/dev/null; then
        echo "volleyball 服務啟動成功：http://127.0.0.1:5000/api"
        exit 0
    fi
    sleep 1
done
echo "服務沒有回應，請看 log：journalctl -u volleyball -n 50"
exit 1
