#!/usr/bin/env bash
# Cài gost proxy (HTTP + SOCKS5, bắt buộc user/pass) chạy 24/7 bằng systemd.
# Dùng: sudo bash vps-install.sh [PORT]   (mặc định PORT=8080)
set -euo pipefail
PORT="${1:-8080}"
if [ "$(id -u)" -ne 0 ]; then echo "Chạy bằng sudo/root nhé"; exit 1; fi
if ! command -v systemctl >/dev/null 2>&1; then echo "VPS không có systemd — báo lại để đổi cách chạy"; exit 1; fi

ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) ARCHTAG=amd64 ;;
  aarch64|arm64) ARCHTAG=arm64 ;;
  *) echo "Arch chưa hỗ trợ: $ARCH"; exit 1 ;;
esac

echo "[1/4] Tải gost..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ASSET_URL=$(curl -fsS -m 25 https://api.github.com/repos/go-gost/gost/releases/latest 2>/dev/null \
  | grep -o '"browser_download_url": *"[^"]*_linux_'"$ARCHTAG"'\.tar\.gz"' | cut -d'"' -f4 | head -1) || true
if [ -z "${ASSET_URL:-}" ]; then
  ASSET_URL="https://github.com/go-gost/gost/releases/download/v3.3.3/gost_3.3.3_linux_${ARCHTAG}.tar.gz"
fi
curl -fsSL -m 300 "$ASSET_URL" -o "$TMP/gost.tgz"
tar -xzf "$TMP/gost.tgz" -C "$TMP" gost
install -m 0755 "$TMP/gost" /usr/local/bin/gost

echo "[2/4] Sinh user/pass (giữ nguyên nếu đã có)..."
mkdir -p /etc/gost-proxy
if [ ! -s /etc/gost-proxy/env ]; then
  PASS=$(openssl rand -hex 12)
  printf 'GOST_AUTH=boss:%s\nGOST_PORT=%s\n' "$PASS" "$PORT" > /etc/gost-proxy/env
fi
chmod 600 /etc/gost-proxy/env
AUTH=$(grep '^GOST_AUTH=' /etc/gost-proxy/env | cut -d= -f2-)
PASS="${AUTH#*:}"

echo "[3/4] Tạo service systemd..."
cat > /etc/systemd/system/gost-proxy.service <<'UNIT'
[Unit]
Description=gost proxy (HTTP+SOCKS5, auth)
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/gost-proxy/env
ExecStart=/usr/local/bin/gost -L "auto://${GOST_AUTH}@0.0.0.0:${GOST_PORT}"
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now gost-proxy >/dev/null
sleep 1
if systemctl is-active --quiet gost-proxy; then
  echo "gost-proxy: đang chạy"
else
  echo "gost-proxy lỗi:"; journalctl -u gost-proxy -n 20 --no-pager; exit 1
fi

echo "[4/4] Firewall..."
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "${PORT}/tcp" >/dev/null && echo "ufw: đã mở ${PORT}/tcp"
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || true
  command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true
fi

IP=$(curl -fsS -m 10 https://api.ipify.org || hostname -I | awk '{print $1}')
echo "==================================================="
echo " PROXY ĐÃ CHẠY"
echo "   Endpoint : ${IP}:${PORT}"
echo "   User     : boss"
echo "   Pass     : ${PASS}"
echo "   BP Proxy Switcher: Type HTTP (hoặc SOCKS5), Host ${IP}, Port ${PORT}"
echo "   Test     : curl -x http://boss:${PASS}@${IP}:${PORT} https://api.ipify.org"
echo "   Đổi pass : sửa /etc/gost-proxy/env rồi systemctl restart gost-proxy"
echo "   Oracle Cloud: mở ingress TCP ${PORT} trong VCN Security List (nếu chưa mở)"
echo "==================================================="
