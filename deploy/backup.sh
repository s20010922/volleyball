#!/usr/bin/env bash
# Daily backup of TCJH Volleyball database and .env to Google Drive.
# Runs as the volleyball user from /etc/cron.d/volleyball (see deploy/volleyball.cron).
set -euo pipefail

PROJECT_DIR=/opt/volleyball
LOCAL_DIR=/var/lib/volleyball/backups
# volleyball 帳號專用的 rclone 設定，只含 gdrive 這組授權
export RCLONE_CONFIG=/var/lib/volleyball/rclone.conf
# 用 daily/ 子資料夾，避免下面的 rclone delete 動到舊主機留下的歷史備份
REMOTE=gdrive:TCJH_Volleyball_Backup/daily
KEEP_DAYS=30

STAMP=$(date +%F_%H%M)
mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"

# sqlite3 .backup takes a consistent snapshot even while gunicorn is writing
sqlite3 "$PROJECT_DIR/instance/volleyball.db" ".backup '$LOCAL_DIR/volleyball_$STAMP.db'"
chmod 600 "$LOCAL_DIR/volleyball_$STAMP.db"
install -m 600 "$PROJECT_DIR/.env" "$LOCAL_DIR/volleyball_$STAMP.env"

rclone copy "$LOCAL_DIR" "$REMOTE" --include "volleyball_$STAMP.*"

find "$LOCAL_DIR" -name 'volleyball_*' -mtime +"$KEEP_DAYS" -delete
rclone delete "$REMOTE" --min-age "${KEEP_DAYS}d" --include 'volleyball_*'

echo "[$(date '+%F %T')] backup ok: volleyball_$STAMP.db / .env -> $REMOTE"
