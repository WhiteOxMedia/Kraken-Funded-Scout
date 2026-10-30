#!/usr/bin/env bash
# fix-ssh-access.sh - repair key-only login for the relay VPS, reporting only verdicts.
#
# Run this INSIDE the provider's VNC/console as root (it cannot be run over SSH
# when key auth is broken, which is the whole point).
#
# It answers one question per section and fixes what it can:
#   A  is this the instance that owns the relay IP?   (cannot be fixed - wrong VM)
#   B  are /root, /root/.ssh and authorized_keys owned+moded correctly?  (fixes)
#   C  does authorized_keys hold the expected key?    (reinstalls)
#   D  is PermitRootLogin blocking key auth?          (fixes via drop-in)
#   E  is PubkeyAuthentication or AuthorizedKeysFile off?  (fixes via drop-in)
#
# Output is PASS / FIXED / problems only. No private material, no raw config dumps.
# Safe to re-run; it converges and reloads ssh only when it changed something.
set -u

EXPECTED_IP="${EXPECTED_IP:-155.94.157.66}"
KEY_LINE='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIImFBZUsc6VoB43xxEkWmzDY1PvY0SqBbiC/pBvKV08B xmrjackpot-relay-admin'
EXPECTED_FP='SHA256:KIDMJ1BZ7Q/3p2peXK7z9BrugHJhVUzMIlFWyx7+0AQ'
DROPIN=/etc/ssh/sshd_config.d/99-relay-keylogin.conf
changed=0

[ "$(id -u)" -eq 0 ] || { echo "run me as root"; exit 1; }

# A - identity of the machine itself
if hostname -I 2>/dev/null | tr ' ' '\n' | grep -qx "$EXPECTED_IP"; then
  echo "A: PASS  this VM owns $EXPECTED_IP"
else
  echo "A: STOP  this VM does not own $EXPECTED_IP (any fixes here land on the wrong machine)"
  exit 1
fi

# B - ownership and modes (StrictModes silently ignores a loose authorized_keys)
b_bad=""
[ "$(stat -c '%U:%G %a' /root)" = "root:root 700" ] || b_bad="/root"
[ "$(stat -c '%U:%G %a' /root/.ssh 2>/dev/null)" = "root:root 700" ] || b_bad="$b_bad .ssh"
install -d -m 700 -o root -g root /root/.ssh
touch /root/.ssh/authorized_keys
chown root:root /root /root/.ssh /root/.ssh/authorized_keys
chmod 700 /root /root/.ssh
chmod 600 /root/.ssh/authorized_keys
if [ -n "$b_bad" ]; then echo "B: FIXED  ownership/modes corrected for:$b_bad"; changed=1; else echo "B: PASS"; fi

# C - the key itself
if ssh-keygen -lf /root/.ssh/authorized_keys 2>/dev/null | grep -q "${EXPECTED_FP#SHA256:}"; then
  echo "C: PASS  key fingerprint matches"
else
  printf '%s\n' "$KEY_LINE" > /root/.ssh/authorized_keys
  chown root:root /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  echo "C: FIXED reinstalled the relay admin key (fingerprint now $(ssh-keygen -lf /root/.ssh/authorized_keys | cut -d' ' -f2))"
  changed=1
fi

# D - root login must allow key auth
rootlogin="$(sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')"
case "$rootlogin" in
  no|forced-commands-only)
    printf 'PermitRootLogin prohibit-password\n' >> "$DROPIN"
    echo "D: FIXED  PermitRootLogin was '$rootlogin', set to prohibit-password via $DROPIN"
    changed=1 ;;
  *) echo "D: PASS  PermitRootLogin=$rootlogin" ;;
esac

# E - pubkey auth and where sshd looks for the file
pubkey="$(sshd -T 2>/dev/null | awk '/^pubkeyauthentication /{print $2}')"
if [ "$pubkey" = "no" ]; then
  printf 'PubkeyAuthentication yes\n' >> "$DROPIN"
  echo "E: FIXED  PubkeyAuthentication was off, re-enabled via $DROPIN"
  changed=1
else
  akf="$(sshd -T 2>/dev/null | awk '/^authorizedkeysfile /{print $2}')"
  case "$akf" in
    ""|.ssh/authorized_keys|"%h/.ssh/authorized_keys") echo "E: PASS  AuthorizedKeysFile=$akf" ;;
    *) echo "E: NOTE  AuthorizedKeysFile is '$akf' - the key must live there too"
       for f in $akf; do
         case "$f" in *%h*) p="${f//%h/\/root}" ;; *) p="/$f" ;; esac
         install -d -m 700 "$(dirname "$p")"
         grep -qxF "$KEY_LINE" "$p" 2>/dev/null || printf '%s\n' "$KEY_LINE" >> "$p"
         chmod 600 "$p"
       done
       echo "E: FIXED  key also installed at the configured AuthorizedKeysFile path"
       changed=1 ;;
  esac
fi

# allow/deny lists that exclude root are the one thing a drop-in cannot repair safely
if sshd -T 2>/dev/null | awk '/^(allowusers|denyusers|allowgroups|denygroups) /{print}' | grep -q .; then
  echo "F: NOTE  an AllowUsers/DenyUsers/AllowGroups list is active - if root is excluded, keys cannot work"
fi

if [ "$changed" -eq 1 ]; then
  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl restart ssh 2>/dev/null
    echo "== changed: sshd config valid, ssh reloaded"
  else
    echo "== ERROR sshd config invalid after edit - NOT reloading; remove $DROPIN and retry"
    exit 1
  fi
else
  echo "== nothing needed changing"
fi
echo "== done: retry key login from the workstation"
