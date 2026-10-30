#!/usr/bin/env bash
# 設定網頁按「更新到最新版」→ bot 寫出 data/update-request → jvs-update.path 偵測到 → 用 root 跑這支。
# 流程：git pull（用 repo 擁有者的帳號）→ 基本檢查 → install.sh → 等服務起來；沒起來就退回上一版。
# 全程輸出寫到 data/update.log，結果寫到 data/update-status.json 給網頁看。
set -uo pipefail

APP_DIR=${JVS_APP_DIR:-/opt/jvs}          # 測試時可以換
WAIT=${JVS_HEALTH_WAIT:-10}
DATA="$APP_DIR/data"
LOG="$DATA/update.log"
STATUS="$DATA/update-status.json"
REQ="$DATA/update-request"

# install.sh 會覆蓋 /opt/jvs/deploy，先把自己複製出去再跑，避免跑到一半檔案被換掉
if [[ -z "${JVS_UPDATE_COPY:-}" ]]; then
  COPY=$(mktemp /tmp/jvs-web-update.XXXXXX)
  cp "$0" "$COPY"
  JVS_UPDATE_COPY="$COPY" exec bash "$COPY"
fi

trap 'rm -f "$REQ" "$JVS_UPDATE_COPY"' EXIT
mkdir -p "$DATA"
# log 只留最後 2000 行
[[ -f "$LOG" ]] && tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exec >>"$LOG" 2>&1
STARTED=$(date +%s)
echo
echo "===== $(date '+%F %T') 從設定網頁更新 ====="

# 狀態檔：state=running/done、ok、message、from、to、started、finished
status() {   # status <state> <ok:true/false> <message> [from] [to]
  python3 - "$STATUS" "$1" "$2" "$3" "${4:-}" "${5:-}" "$STARTED" <<'PY'
import json, os, sys, time
path, state, ok, msg, old, new, started = sys.argv[1:]
data = {"state": state, "ok": ok == "true", "message": msg, "from": old, "to": new,
        "started": int(started), "finished": int(time.time()) if state == "done" else None}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}

# 原始碼資料夾：install.sh 記在 VERSION.json 的 src；沒有就用 /home/ubuntu/jvs
SRC=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("src",""))' "$APP_DIR/VERSION.json" 2>/dev/null)
SRC=${SRC:-/home/ubuntu/jvs}
if [[ ! -d "$SRC/.git" ]]; then
  echo "找不到原始碼資料夾 $SRC"
  status done false "找不到原始碼資料夾 $SRC"
  exit 1
fi
OWNER=$(stat -c %U "$SRC")
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)
# 用 repo 擁有者的帳號和 HOME 跑 git（root 直接跑會遇到 safe.directory）
asowner() { runuser -u "$OWNER" -- env HOME="${OWNER_HOME:-/tmp}" "$@"; }
short() { echo "${1:0:7}"; }

status running true "更新中…"
OLD=$(asowner git -C "$SRC" rev-parse HEAD)
echo "目前版本 $(short "$OLD")，原始碼 $SRC（$OWNER）"

if ! asowner git -C "$SRC" pull --ff-only; then
  status done false "git pull 失敗，主機上的原始碼可能被改過，詳情看紀錄" "$(short "$OLD")"
  exit 1
fi
NEW=$(asowner git -C "$SRC" rev-parse HEAD)
if [[ "$OLD" == "$NEW" ]]; then
  echo "已經是最新版"
  status done true "已經是最新版" "$(short "$OLD")" "$(short "$NEW")"
  exit 0
fi
echo "新版本 $(short "$NEW")"

# 基本檢查：套件裝得起來、每個 .py 都能編譯、reminderbot 能 import。沒過就不安裝。
echo "==> 檢查新版"
"$APP_DIR/.venv/bin/pip" install -q -r "$SRC/requirements.txt"
if ! (cd "$SRC" && asowner env PYTHONDONTWRITEBYTECODE=1 "$APP_DIR/.venv/bin/python" - <<'PY'
import pathlib
for p in sorted(pathlib.Path("reminderbot").glob("*.py")):
    compile(p.read_text(encoding="utf-8"), str(p), "exec")
import reminderbot.core, reminderbot.web, reminderbot.bot, reminderbot.config  # noqa
print("檢查通過")
PY
); then
  asowner git -C "$SRC" reset -q --hard "$OLD"
  status done false "新版檢查沒過，沒有安裝，還在跑 $(short "$OLD")。詳情看紀錄" "$(short "$OLD")" "$(short "$NEW")"
  exit 1
fi

# 服務正常 = 跑著、而且設定網頁有回應
PORT=$(grep -E '^WEB_PORT=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' "')
healthy() {
  for _ in 1 2 3 4; do
    sleep "$WAIT"
    # 設定網頁有回應就算活著（舊版的 /healthz 要帳密，回 401 也算有回應）
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${PORT:-8080}/healthz")
    if systemctl is-active --quiet jvs && [[ "$code" == 200 || "$code" == 401 ]]; then
      return 0
    fi
  done
  return 1
}

echo "==> 安裝"
if bash "$SRC/deploy/install.sh" && healthy; then
  MSG=$(asowner git -C "$SRC" log -1 --format=%s)
  echo "✅ 更新完成：$(short "$NEW") $MSG"
  status done true "更新完成：$MSG" "$(short "$OLD")" "$(short "$NEW")"
  exit 0
fi

echo "❌ 新版沒有正常啟動，退回 $(short "$OLD")"
journalctl -u jvs -n 30 --no-pager || true
asowner git -C "$SRC" reset -q --hard "$OLD"
if bash "$SRC/deploy/install.sh" && healthy; then
  status done false "新版 $(short "$NEW") 有問題，已退回 $(short "$OLD")。詳情看紀錄" "$(short "$OLD")" "$(short "$NEW")"
else
  status done false "新版有問題，退回 $(short "$OLD") 也沒有正常啟動，要上主機看 journalctl -u jvs" "$(short "$OLD")" "$(short "$NEW")"
fi
exit 1
