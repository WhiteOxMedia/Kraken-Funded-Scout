#!/usr/bin/env bash
# witness-agent.sh - turn any Linux box on another network into a relay witness.
#
# Run it on the SECOND host (not the VPS, not the pool PC), as root:
#
#   scp relay/witness-agent.sh relay/ingress-check.py root@SECOND_HOST:/root/
#   ssh root@SECOND_HOST 'bash /root/witness-agent.sh'
#
# It installs one self-contained script and a cron entry that re-checks every five
# minutes, writing /var/lib/relay-witness.json. The pool PC then reads it with:
#
#   python relay/monitor-relay.py --witness-remote root@SECOND_HOST:/var/lib/relay-witness.json
#
# No daemon, no port, no firewall change: the witness only makes outbound
# connections, and the pool PC pulls the result over ssh.
#
# Why cron and not systemd: cron exists everywhere, has no unit-file syntax to get
# wrong, and a witness that stops updating is caught by the monitor's staleness
# check anyway. Re-running this script replaces the cron line rather than adding one.
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-mine.xmrjackpot.com}"
TARGET_PORT="${TARGET_PORT:-3333}"
OUT="${OUT:-/var/lib/relay-witness.json}"
BIN="/usr/local/bin/ingress-check.py"
SRC="$(cd "$(dirname "$0")" && pwd)/ingress-check.py"
INTERVAL="${INTERVAL:-*/5 * * * *}"

[ "$(id -u)" -eq 0 ] || { echo "run me as root" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required on the witness host" >&2; exit 1; }
command -v crontab >/dev/null || { echo "crontab is required (apt-get install cron)" >&2; exit 1; }

if [ -f "$SRC" ]; then
  install -m 755 "$SRC" "$BIN"
elif [ ! -f "$BIN" ]; then
  echo "copy ingress-check.py next to this script first" >&2
  exit 1
fi
install -d -m 755 "$(dirname "$OUT")"

echo "== first run"
python3 "$BIN" "$TARGET_HOST" "$TARGET_PORT" --witness "$OUT" || true
echo

# Replace any previous line for this script, then add the current one.
crontab -l 2>/dev/null | grep -v "$BIN" > /tmp/crontab.witness || true
echo "$INTERVAL python3 $BIN $TARGET_HOST $TARGET_PORT --witness $OUT >/dev/null 2>&1" >> /tmp/crontab.witness
crontab /tmp/crontab.witness
rm -f /tmp/crontab.witness

echo "== installed: $BIN, refreshes $OUT every check"
crontab -l | grep "$BIN"
echo
echo "== witness file"
cat "$OUT"
echo
echo "Next, on the pool PC:"
echo "  python relay/monitor-relay.py --witness-remote root@$(hostname -f 2>/dev/null || hostname):$OUT"
