#!/usr/bin/env bash
# 之後更新程式：在 clone 下來的資料夾執行 sudo bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
sudo bash deploy/install.sh
