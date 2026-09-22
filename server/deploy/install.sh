#!/usr/bin/env bash
# =============================================================================
# TL2 Mikuro Lobby Lite: install as a systemd service (Linux, run as root).
#
#   sudo ./deploy/install.sh              # settings come from server/.env (copy .env.example)
#   sudo ./deploy/install.sh --uninstall
#
# Idempotent: run it again after editing .env or updating the code.
# Pure Python standard library, no pip packages. Needs Python >= 3.9.
# =============================================================================
set -euo pipefail

SVC="tl2-lobby-lite"
INSTALL_DIR="/opt/${SVC}"
RUN_USER="tl2lobby"
UNIT="/etc/systemd/system/${SVC}.service"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_SRC="${SRC_DIR}/.env"

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: sudo $0"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now "${SVC}" 2>/dev/null || true
  rm -f "${UNIT}"
  systemctl daemon-reload
  log "service removed. ${INSTALL_DIR} and user ${RUN_USER} are kept (remove them by hand if you like)."
  exit 0
fi

env_get() {   # value of KEY in .env (quotes and trailing comments stripped), empty if absent
  [[ -f "$ENV_SRC" ]] || return 0
  grep -E "^[[:space:]]*$1=" "$ENV_SRC" | tail -n1 | cut -d= -f2- \
    | sed -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
          -e "s/^['\"]//" -e "s/['\"]\$//" || true
}

[[ -f "$ENV_SRC" ]] || die "no ${ENV_SRC}. Copy .env.example to .env and set RELAY_IP first."

PORT="$(env_get LOBBY_PORT)";              PORT="${PORT:-4549}"
RELAY_PORT="$(env_get RELAY_PORT)";        RELAY_PORT="${RELAY_PORT:-$PORT}"
RANGE="$(env_get RELAY_PORT_RANGE)";       RANGE="${RANGE:-4550-4999}"
RELAY_IP="$(env_get RELAY_IP)";            RELAY_IP="${RELAY_IP:-auto}"

[[ "$RELAY_IP" == "auto" ]] && warn "RELAY_IP=auto advertises this machine's interface address. On a cloud VM behind NAT that is a private IP and players cannot reach it: set the public IP (or 'public')."

RANGE_LO=""; RANGE_HI=""
if [[ "$RANGE" =~ ^([0-9]+)-([0-9]+)$ ]]; then
  RANGE_LO="${BASH_REMATCH[1]}"; RANGE_HI="${BASH_REMATCH[2]}"
elif [[ "$RANGE" != "0" ]]; then
  die "RELAY_PORT_RANGE must be A-B or 0: $RANGE"
fi

# ---- python ----
if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq python3
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q python3
  else die "install python3 (>= 3.9) and run again"; fi
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "python3 >= 3.9 required"
PYBIN="$(command -v python3)"

# ---- user + files ----
id "${RUN_USER}" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "${RUN_USER}"
mkdir -p "${INSTALL_DIR}"
install -m 0644 "${SRC_DIR}"/lobby_server.py "${SRC_DIR}"/protocol.py "${SRC_DIR}"/pair_relay.py \
                "${SRC_DIR}"/login_hash.py "${INSTALL_DIR}/"
install -m 0600 -o "${RUN_USER}" -g "${RUN_USER}" "${ENV_SRC}" "${INSTALL_DIR}/.env"
chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
log "installed to ${INSTALL_DIR}"

# ---- systemd ----
cat > "${UNIT}" <<UNIT_EOF
[Unit]
Description=TL2 Mikuro Lobby Lite (Torchlight II lobby + relay)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUTF8=1
ExecStart=${PYBIN} ${INSTALL_DIR}/lobby_server.py --env ${INSTALL_DIR}/.env
Restart=on-failure
RestartSec=3
MemoryMax=512M
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# the server writes nothing to disk; logs go to the journal (journalctl -u ${SVC})
ReadOnlyPaths=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
UNIT_EOF

# ---- firewall ----
RULES=("${PORT}/tcp" "${RELAY_PORT}/udp")
[[ -n "$RANGE_LO" ]] && RULES+=("${RANGE_LO}:${RANGE_HI}/udp")
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  for r in "${RULES[@]}"; do ufw allow "${r}" >/dev/null && log "ufw allow ${r}"; done
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  for r in "${RULES[@]}"; do firewall-cmd --permanent --add-port="${r/:/-}" >/dev/null && log "firewalld allow ${r}"; done
  firewall-cmd --reload >/dev/null
elif command -v iptables >/dev/null 2>&1; then
  for r in "${RULES[@]}"; do
    p="${r%/*}"; proto="${r#*/}"
    iptables -C INPUT -p "$proto" --dport "$p" -j ACCEPT 2>/dev/null \
      || { iptables -I INPUT -p "$proto" --dport "$p" -j ACCEPT && log "iptables allow ${r}"; }
  done
  command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 \
    || warn "iptables rules are not persisted across reboots on this system; save them your usual way."
fi

systemctl daemon-reload
systemctl enable "${SVC}" >/dev/null 2>&1
systemctl restart "${SVC}"
sleep 1
systemctl is-active --quiet "${SVC}" || die "service failed to start: journalctl -u ${SVC} -n 50"

log "running. Logs: journalctl -u ${SVC} -f"
warn "Cloud VMs: also open TCP ${PORT}, UDP ${RELAY_PORT} and UDP ${RANGE} in the provider's security group / firewall."
