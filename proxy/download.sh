#!/usr/bin/env bash
# Tải gost + bore vào proxy/bin (dùng khi binary bị mất sau khi sandbox dựng lại).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p bin
GOST_URL=https://github.com/go-gost/gost/releases/download/v3.3.3/gost_3.3.3_linux_amd64.tar.gz
BORE_URL=https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-unknown-linux-musl.tar.gz
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -m 300 "$GOST_URL" -o "$tmp/gost.tgz"
curl -fsSL -m 300 "$BORE_URL" -o "$tmp/bore.tgz"
tar -xzf "$tmp/gost.tgz" -C "$tmp" gost
tar -xzf "$tmp/bore.tgz" -C "$tmp" bore
install -m 0755 "$tmp/gost" bin/gost
install -m 0755 "$tmp/bore" bin/bore
echo "[proxy] đã tải xong binary"
