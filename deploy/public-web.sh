#!/usr/bin/env bash
# 把設定網頁開成公開網址（https + 帳密）。用法：sudo bash deploy/public-web.sh
# 會做：裝 Caddy（自動申請 https 憑證）、打開主機防火牆 80/443、問你帳號密碼寫進 .env、重啟 bot。
# 事先要做：Oracle 主控台的 Security List 開放 TCP 80、443（見 docs/SETUP.md）。
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "請用 sudo 執行"; exit 1; fi
ENV=/opt/jvs/.env

IP=$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)
HOST="${IP//./-}.sslip.io"
echo "==> 網址會是 https://$HOST"

echo "==> 安裝 Caddy"
apt-get update -qq && apt-get install -y -qq caddy >/dev/null
cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
    reverse_proxy 127.0.0.1:8080
}
CADDY

echo "==> 打開主機防火牆 80、443"
for port in 80 443; do
  iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport $port -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true

echo
read -rp "設定網頁的帳號（直接 Enter = admin）：" USER_IN
USER_IN=${USER_IN:-admin}
read -rp "設定網頁的密碼（會顯示在畫面上，打完按 Enter）：" PASS_IN
if [[ -z "$PASS_IN" ]]; then echo "密碼不能空白"; exit 1; fi
grep -q '^WEB_USER=' $ENV && sed -i "s|^WEB_USER=.*|WEB_USER=$USER_IN|" $ENV || echo "WEB_USER=$USER_IN" >> $ENV
grep -q '^WEB_PASSWORD=' $ENV && sed -i "s|^WEB_PASSWORD=.*|WEB_PASSWORD=$PASS_IN|" $ENV || echo "WEB_PASSWORD=$PASS_IN" >> $ENV

systemctl restart jvs
systemctl restart caddy
echo
echo "✅ 好了。等 1 分鐘讓它申請 https 憑證，然後打開：https://$HOST"
echo "   帳號：$USER_IN"
echo "   打不開的話：確認 Oracle 的 Security List 有開 80、443，再看 journalctl -u caddy -n 30"
