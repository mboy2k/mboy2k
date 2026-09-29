#!/usr/bin/env bash
# Remote access for Hoplite PC: a real SSH door (user `boss`, full sudo) reachable
# over the owner's Tailscale network. Tailscale runs in userspace mode, so no TUN
# is needed and inbound TCP on port N is forwarded to localhost:N. Idempotent —
# safe to call on every boot and from the run-script watchdog.
set -u

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

# .env in the workspace carries project credentials (BROWSER_API_KEY, TS_AUTHKEY…);
# it is gitignored, so the key survives sandbox rebuilds without touching git.
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$WORKSPACE/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKSPACE/.env"
  set +a
fi

TS_BIN=/usr/local/bin/tailscale
TSD_BIN=/usr/local/bin/tailscaled
TS_SOCKET=/var/run/tailscale/tailscaled.sock
# Keep the node identity inside the workspace so the machine stays the same node
# (and stays logged in) when the sandbox is rebuilt around it.
TS_STATE="${TS_STATE:-$WORKSPACE/.hoplite/tailscale/tailscaled.state}"
LEGACY_TS_STATE=/var/lib/tailscale/tailscaled.state
STATE_DIR=/var/lib/hoplite-pc
# The sandbox runs with no_new_privs, so sudo can never escalate for a normal
# user; logging in as root is the only way to get full control of the machine.
SSH_USER="${PC_SSH_USER:-root}"
PC_HOSTNAME="${PC_HOSTNAME:-hoplite-pc}"

log() { [ "$QUIET" = 1 ] || printf 'machine-access: %s\n' "$*"; }

mkdir -p "$STATE_DIR" /var/lib/tailscale /var/run/tailscale /run/sshd
mkdir -p "$(dirname "$TS_STATE")"

# Carry over an identity created before the state moved into the workspace.
if [ ! -s "$TS_STATE" ] && [ -s "$LEGACY_TS_STATE" ]; then
  cp "$LEGACY_TS_STATE" "$TS_STATE"
  log "da chuyen state tailscale vao workspace"
fi

install_tailscale() {
  [ -x "$TS_BIN" ] && [ -x "$TSD_BIN" ] && return 0
  local file dir=/tmp/tailscale-unpack
  file=$(curl -fsS --max-time 30 https://pkgs.tailscale.com/stable/ 2>/dev/null \
    | grep -oE 'tailscale_[0-9.]+_amd64\.tgz' | head -1)
  [ -n "$file" ] || { log "khong doc duoc ban tailscale moi nhat"; return 1; }
  curl -fsSL --max-time 300 "https://pkgs.tailscale.com/stable/$file" -o /tmp/tailscale.tgz || return 1
  rm -rf "$dir"; mkdir -p "$dir"
  tar -xzf /tmp/tailscale.tgz -C "$dir" --strip-components=1 || return 1
  install -m 0755 "$dir/tailscale" "$TS_BIN" || return 1
  install -m 0755 "$dir/tailscaled" "$TSD_BIN" || return 1
  log "da cai tailscale ($file)"
}

ensure_user() {
  local home keydir
  if [ "$SSH_USER" != "root" ]; then
    if ! id "$SSH_USER" >/dev/null 2>&1; then
      useradd -m -s /bin/bash "$SSH_USER"
      log "da tao user $SSH_USER"
    fi
    if [ ! -f /etc/sudoers.d/hoplite-pc ]; then
      printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$SSH_USER" > /etc/sudoers.d/hoplite-pc
      chmod 440 /etc/sudoers.d/hoplite-pc
    fi
  fi

  # A fresh password each boot: it is shown in the desktop "Kết nối từ xa" app.
  if [ ! -s "$STATE_DIR/ssh-password" ]; then
    umask 077
    python3 -c 'import secrets; print(secrets.token_hex(8))' > "$STATE_DIR/ssh-password"
  fi
  chpasswd <<<"$SSH_USER:$(cat "$STATE_DIR/ssh-password")"

  home=$(getent passwd "$SSH_USER" | cut -d: -f6)
  keydir="$home/.ssh"
  install -d -m 700 -o "$SSH_USER" -g "$SSH_USER" "$keydir"
  : >"$keydir/authorized_keys"
  [ -s "$STATE_DIR/authorized_keys" ] && cat "$STATE_DIR/authorized_keys" >>"$keydir/authorized_keys"
  [ -n "${BOSS_SSH_PUBKEY:-}" ] && printf '%s\n' "$BOSS_SSH_PUBKEY" >>"$keydir/authorized_keys"
  chown "$SSH_USER:$SSH_USER" "$keydir/authorized_keys"
  chmod 600 "$keydir/authorized_keys"
}

ensure_sshd() {
  cat >/etc/ssh/sshd_config.d/60-hoplite-pc.conf <<'EOF'
Port 22
PermitRootLogin yes
PubkeyAuthentication yes
PasswordAuthentication yes
KbdInteractiveAuthentication no
EOF
  ssh-keygen -A >/dev/null 2>&1
  if ! /usr/sbin/sshd -t 2>/tmp/sshd-config.err; then
    log "cau hinh sshd loi: $(cat /tmp/sshd-config.err)"
    return 1
  fi
  if (exec 3<>/dev/tcp/127.0.0.1/22) 2>/dev/null; then
    # Already listening: make it pick up the config we just wrote.
    [ -s /run/sshd.pid ] && kill -HUP "$(cat /run/sshd.pid)" 2>/dev/null
    return 0
  fi
  nohup /usr/sbin/sshd -E /var/log/sshd.log >/dev/null 2>&1 &
  sleep 1
  log "da bat sshd"
}

ensure_tailscaled() {
  # Match the process name exactly: a -f pattern would also match this script's
  # own command line when it is called with those arguments.
  if pgrep -x tailscaled >/dev/null 2>&1; then
    return 0
  fi
  [ -x "$TSD_BIN" ] || return 1
  # Only inbound forwarding is wanted; the optional socks5/HTTP proxy listeners
  # just add extra open ports the preview tries to expose.
  nohup "$TSD_BIN" --tun=userspace-networking --socket="$TS_SOCKET" --state="$TS_STATE" \
    >>/var/log/tailscaled.log 2>&1 &
  for _ in $(seq 1 40); do
    [ -S "$TS_SOCKET" ] && { log "da bat tailscaled"; return 0; }
    sleep 0.5
  done
  log "tailscaled khong len duoc"
  return 1
}

# TS_AUTHKEY (reusable auth key) makes the machine rejoin the tailnet by itself
# after every sandbox rebuild; without it the desktop shows the login URL instead.
ensure_tailnet() {
  local key="${TS_AUTHKEY:-${TAILSCALE_AUTHKEY:-}}"
  if [ -n "$key" ]; then
    if "$TS_BIN" --socket="$TS_SOCKET" up --authkey="$key" --hostname="$PC_HOSTNAME" \
        --accept-dns=false >/dev/null 2>&1; then
      log "da vao tailnet voi ten $PC_HOSTNAME"
      return 0
    fi
    # A bad or expired key must not block the interactive route below.
    log "auth key khong dung duoc, chuyen sang dang nhap thu cong"
  fi

  # Keep an interactive login waiting so the desktop app can show a fresh URL.
  # Only start a new flow when none is pending.
  local state authurl
  state=$("$TS_BIN" --socket="$TS_SOCKET" status --json 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("BackendState",""), d.get("AuthURL",""))' 2>/dev/null)
  case "$state" in
    Running*) return 0 ;;
  esac
  authurl=${state#* }
  if [ -z "$authurl" ] && ! pgrep -f "$TS_BIN .* up --hostname=$PC_HOSTNAME" >/dev/null 2>&1; then
    nohup "$TS_BIN" --socket="$TS_SOCKET" up --hostname="$PC_HOSTNAME" --accept-dns=false \
      >>/var/log/tailscale-login.log 2>&1 &
    log "dang cho dang nhap tailscale (xem app Ket noi tu xa)"
  fi
}

install_tailscale || true
ensure_user || true
ensure_sshd || true
ensure_tailscaled || true
ensure_tailnet || true
log "ssh + tailscale san sang"
