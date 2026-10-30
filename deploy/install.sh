#!/usr/bin/env bash
# 在 Oracle Cloud（Ubuntu 22.04 / 24.04）上一鍵安裝。用法：
#   git clone https://github.com/Russell0123/jvs.git && cd jvs && sudo bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/jvs
APP_USER=jvs
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "請用 sudo 執行"; exit 1; fi

echo "==> 安裝系統套件"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git sqlite3 rsync >/dev/null

echo "==> 建立執行帳號 $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> 複製程式到 $APP_DIR"
mkdir -p "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  # data/ 整個不動：資料庫、備份、上傳的圖片都在裡面（--delete 會把 repo 裡沒有的檔案刪掉）
  rsync -a --delete --exclude .venv --exclude .env --exclude /data/ \
    "$SRC_DIR"/ "$APP_DIR"/ 2>/dev/null || cp -r "$SRC_DIR"/. "$APP_DIR"/
fi
mkdir -p "$APP_DIR/data/backups"

echo "==> 建立 Python 虛擬環境"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  NEED_ENV=1
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> 安裝 systemd 服務"
cp "$APP_DIR/deploy/jvs.service" /etc/systemd/system/jvs.service
cp "$APP_DIR/deploy/jvs-backup.service" "$APP_DIR/deploy/jvs-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jvs-backup.timer >/dev/null

if [[ "${NEED_ENV:-0}" == 1 ]]; then
  echo
  echo "✅ 安裝完成，但還沒填設定。請執行："
  echo "   sudo nano $APP_DIR/.env      # 填 DISCORD_TOKEN、CHANNEL_IDS、CWA_API_KEY"
  echo "   sudo systemctl enable --now jvs"
else
  systemctl enable jvs >/dev/null
  systemctl restart jvs
  echo "✅ 已更新並重新啟動。"
fi
echo "看紀錄：journalctl -u jvs -f"
