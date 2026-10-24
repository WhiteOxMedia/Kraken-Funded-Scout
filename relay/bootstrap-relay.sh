#!/usr/bin/env bash
# bootstrap-relay.sh - turn a fresh Ubuntu VPS into the public front for the pool.
#
# WHY THIS EXISTS
#   Cloudflare cannot proxy raw stratum: the free plan proxies HTTP(S) only, and
#   Spectrum (arbitrary TCP) is an Enterprise add-on. So `mine.xmrjackpot.com`
#   can only be a grey-cloud record pointing at a real IP. Today that IP is the
#   house connection, which publishes it. This script makes the VPS that IP:
#
#     miner -> VPS:3333 (haproxy) -> wg0 -> pool PC:3333 (p2pool)
#
#   The pool PC dials OUT to the VPS over WireGuard, so:
#     - the house IP never appears in DNS,
#     - the router's 3333 forward can be closed entirely,
#     - miners keep using mine.xmrjackpot.com:3333 with no client change.
#
# USAGE
#   sudo bash bootstrap-relay.sh                    # first run, generates the home peer
#   sudo bash bootstrap-relay.sh --home-pubkey KEY  # re-run with a peer you already have
#   SSH_PORT=2222 sudo bash bootstrap-relay.sh      # if sshd is not on 22
#
# Idempotent: re-running rewrites the same files and restarts both services.
# Nothing here touches DNS; the record is moved separately, after the relay is
# proven reachable (see README.md).
set -euo pipefail

WG_IF="wg0"
WG_PORT="${WG_PORT:-51820}"
WG_NET="10.66.66"
VPS_ADDR="${WG_NET}.1"
HOME_ADDR="${WG_NET}.2"
STRATUM_PORT="${STRATUM_PORT:-3333}"
ORIGIN_PORT="${ORIGIN_PORT:-3333}"
SSH_PORT="${SSH_PORT:-22}"
HOME_PUBKEY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --home-pubkey) HOME_PUBKEY="${2:-}"; shift 2 ;;
    --home-pubkey=*) HOME_PUBKEY="${1#*=}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run me as root (sudo bash $0)" >&2; exit 1; }
command -v apt-get >/dev/null || { echo "this script targets Debian/Ubuntu (no apt-get found)" >&2; exit 1; }

say() { printf '\n== %s\n' "$*"; }

say "installing wireguard + haproxy"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard haproxy curl >/dev/null

say "keys"
install -d -m 700 /etc/wireguard
if [ ! -s /etc/wireguard/server.key ]; then
  umask 077
  wg genkey > /etc/wireguard/server.key
fi
wg pubkey < /etc/wireguard/server.key > /etc/wireguard/server.pub
SERVER_PUB="$(cat /etc/wireguard/server.pub)"

if [ -z "$HOME_PUBKEY" ]; then
  if [ -s /etc/wireguard/home.key ] && [ -s /etc/wireguard/home.pub ]; then
    HOME_PRIV="$(cat /etc/wireguard/home.key)"
    HOME_PUBKEY="$(cat /etc/wireguard/home.pub)"
  else
    umask 077
    wg genkey > /etc/wireguard/home.key
    wg pubkey < /etc/wireguard/home.key > /etc/wireguard/home.pub
    HOME_PRIV="$(cat /etc/wireguard/home.key)"
    HOME_PUBKEY="$(cat /etc/wireguard/home.pub)"
  fi
else
  HOME_PRIV=""
fi

say "wireguard ${WG_IF} (${VPS_ADDR}/24, udp ${WG_PORT})"
cat > "/etc/wireguard/${WG_IF}.conf" <<EOF
# Managed by bootstrap-relay.sh - the pool PC dials in; no Endpoint here,
# because the home side is the one behind NAT and always initiates.
[Interface]
Address = ${VPS_ADDR}/24
ListenPort = ${WG_PORT}
PrivateKey = $(cat /etc/wireguard/server.key)

[Peer]
# pool PC (p2pool stratum on ${ORIGIN_PORT})
PublicKey = ${HOME_PUBKEY}
AllowedIPs = ${HOME_ADDR}/32
EOF
chmod 600 "/etc/wireguard/${WG_IF}.conf"
systemctl enable --now "wg-quick@${WG_IF}" >/dev/null
systemctl restart "wg-quick@${WG_IF}"

say "haproxy ${STRATUM_PORT} -> ${HOME_ADDR}:${ORIGIN_PORT}"
# Stratum connections are long-lived and often idle for minutes between shares,
# so every timeout is hours, not seconds: the stock 30s/50s defaults would drop
# miners mid-session, which looks like random disconnects in xmrig.
cat > /etc/haproxy/haproxy.cfg <<EOF
# Managed by bootstrap-relay.sh - raw TCP pass-through, no protocol inspection.
global
    # runtime socket: 'show stat' tells an operator (and relay/monitor-relay.py)
    # whether the backend check is passing, which no outside probe can infer.
    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners
    stats timeout 30s
    log /dev/log local0
    maxconn 4096
    daemon

defaults
    mode tcp
    log global
    option tcplog
    option dontlognull
    timeout connect 10s
    timeout client  4h
    timeout server  4h
    timeout tunnel  4h

frontend stratum_in
    bind *:${STRATUM_PORT}
    default_backend p2pool

backend p2pool
    server pool-pc ${HOME_ADDR}:${ORIGIN_PORT} check inter 10s fall 3 rise 2
EOF
haproxy -c -f /etc/haproxy/haproxy.cfg
systemctl enable haproxy >/dev/null
systemctl restart haproxy

say "firewall"
if command -v ufw >/dev/null; then
  ufw allow "${SSH_PORT}/tcp" >/dev/null
  ufw allow "${STRATUM_PORT}/tcp" >/dev/null
  ufw allow "${WG_PORT}/udp" >/dev/null
  ufw --force enable >/dev/null
  ufw status | sed -n '1,12p'
else
  echo "ufw not installed; open ${SSH_PORT}/tcp, ${STRATUM_PORT}/tcp and ${WG_PORT}/udp yourself"
fi

PUBLIC_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || echo '<VPS_PUBLIC_IP>')"

say "relay is up"
printf '  public ip    : %s\n  stratum      : %s/tcp -> %s:%s\n  wireguard    : udp/%s, peer %s\n' \
  "$PUBLIC_IP" "$STRATUM_PORT" "$HOME_ADDR" "$ORIGIN_PORT" "$WG_PORT" "$HOME_ADDR"

cat <<EOF

------------------------------------------------------------------
HOME PEER CONFIG (WireGuard for Windows on the pool PC)
Paste this into WireGuard -> Add Tunnel -> Add empty tunnel.
Keep AllowedIPs at ${VPS_ADDR}/32: that routes ONLY relay traffic
through the VPS, so the site, the Cloudflare tunnel and normal
internet keep going out the house connection.
------------------------------------------------------------------
[Interface]
PrivateKey = ${HOME_PRIV:-<your existing home peer private key>}
Address = ${HOME_ADDR}/32

[Peer]
PublicKey = ${SERVER_PUB}
Endpoint = ${PUBLIC_IP}:${WG_PORT}
AllowedIPs = ${VPS_ADDR}/32
PersistentKeepalive = 25
------------------------------------------------------------------

NEXT
 1. On the pool PC: import that config, activate it, then confirm the
    tunnel:  ping ${VPS_ADDR}
 2. Allow the relay through Windows Firewall (inbound TCP ${ORIGIN_PORT}
    from ${VPS_ADDR} only):
      New-NetFirewallRule -DisplayName "p2pool stratum via relay" ^
        -Direction Inbound -Protocol TCP -LocalPort ${ORIGIN_PORT} ^
        -RemoteAddress ${VPS_ADDR} -Action Allow
 3. Prove it from OUTSIDE the house network (phone hotspot):
      python relay/verify-stratum.py ${PUBLIC_IP} ${STRATUM_PORT}
    Only when that prints PASS, move the DNS record (see relay/README.md).
EOF
