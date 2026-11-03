"""test_kraken_seam_live.py — THE OPERATIONAL SEAM PROBE (Sep 26).

The hermetic drives (integration 8b/8c, the unit drive's 4b) prove the
LOGIC of the two credential seams — pool/kraken_referral's
kraken_credentials() and src/kraken_client's tiered accessor — against
fakes. This probe proves the OPERATIONAL fact on whatever THIS machine
has actually provisioned: both seams resolve the SAME tier and the SAME
identity from the real OS environment, the real pool settings, and the
real OS keyring — no stubs, no sandbox, no monkeypatching.

  1. both seams resolve a COMPLETE pair (one resolving while the other
     does not is the divergence this probe exists to catch)
  2. the SERVING tier is identical — read from the client's own tier
     report (check_credentials), so the seam's public reporting is what
     gets pinned, not a copy of its internals
  3. the key tails are identical (same resolved identity)
  4. the SECRETS are identical — compared for equality, NEVER printed
     (a tail alone could mask a same-tail swap)
  5. the SEEN-TAIL tripwire: when the machine IS provisioned and the
     resolved tail differs from the last SEEN tail, the probe says so
     LOUDLY and fails — a legitimate key rotation is a deliberate
     operator act (update SEEN_KEY_TAIL below); an unexplained change
     is a credential incident and must never be silent. This constant
     is operator-maintained, deliberately NOT a hard pin on the key.

SKIP CONTRACT: when NOTHING is provisioned (no complete pair in any
tier) the probe exits 0 with a SKIP line — a bare checkout has nothing
to compare, and the fleet must not classify honest absence as failure.
The hermetic drives own the logic contract either way.

ZERO MUTATION: every tier is read exactly as the seam reads it — an
env lookup, one settings SELECT through the real database layer (safe
against the running pool server), one keyring get. No network, no
writes, no fixture residue. Prints "RESULT: N passed, M failed".
"""
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The last key tail KNOWN to be provisioned on this box (setup_kraken's
# keyring pair). Operator-maintained: rotate this constant WITH the key.
SEEN_KEY_TAIL = "kcWK"

sys.path.insert(0, str(HERE / "pool"))

import os
# XMR_PRICE_LIVE=0 keeps the rate reader on the fallback, off the
# network (this drive reaches it transitively through a gateway
# import below - the top-level pin test_rate_pin_fleet.py enforces).
os.environ["XMR_PRICE_LIVE"] = "0"
import kraken_referral as kr                       # noqa: E402

sys.path.insert(0, str(HERE / "src"))
import kraken_client as kc                         # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


# ── resolve BOTH seams against the REAL machine ──────────────────────
# THE SETTINGS-TIER PREREQUISITE: both seams resolve through tiered
# lookups (env -> pool settings -> keyring), and the settings tier reads
# the live pool/jackpot.db — operator state the checkout does not carry.
# On a box whose DB is absent/uninitialized the tier lookup crashes
# (`no such table: settings`) before the probe's own "nothing "
# "provisioned" skip contract can run, so when the settings DB is not
# a database the probe says so honestly and yields to the hermetic
# drives (8b/8c), exactly as the no-pair skip below does.
def _settings_ready():
    if not (HERE / "pool" / "jackpot.db").exists():
        return False
    try:
        import sqlite3
        _c = sqlite3.connect(
            f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}?mode=ro",
            uri=True)
        try:
            _t = {r[0] for r in _c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            _c.close()
        return "settings" in _t
    except sqlite3.Error:
        return False


if not _settings_ready():
    print("SKIP: the pool settings tier is unreadable in this checkout "
          "(no initialized pool/jackpot.db), so the operational seam "
          "cannot be resolved here — the hermetic drives (8b/8c) own "
          "the logic contract")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

pk, ps, ptier = kr.kraken_credentials()
ck, cs = kc._get_kraken_credentials()

print("== 1. WHAT THIS MACHINE RESOLVES (live, no stubs) ==")
print(f"  pool/kraken_referral: tier={ptier!r} "
      f"key_tail={'…' + pk[-4:] if pk else None}")
print(f"  src/kraken_client:    "
      f"key_tail={'…' + ck[-4:] if ck else None}")

if not (pk and ps) and not (ck and cs):
    print("SKIP: nothing provisioned on this machine (no complete pair "
          "in env, settings, or keyring) — the operational seam has "
          "nothing to compare; the hermetic drives (8b/8c) own the "
          "logic contract")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

# the client's OWN tier report is the second seam's public answer
buf = io.StringIO()
with redirect_stdout(buf):
    kc_ok = kc.check_credentials()
kc_report = buf.getvalue()
m = re.search(r"tier: (env|settings|keyring)", kc_report)
kc_tier = m.group(1) if m else ""
for line in kc_report.strip().splitlines():
    print(f"  client report: {line.strip()}")

print("== 2. THE TWO SEAMS AGREE ==")
check(1, bool(pk and ps) and bool(ck and cs) and kc_ok,
      "BOTH seams resolve a complete pair from the real machine")
check(2, ptier == kc_tier and kc_tier != "",
      f"the SERVING tier is identical: {ptier!r} == {kc_tier!r}")
check(3, (pk or "")[-4:] == (ck or "")[-4:],
      f"the resolved key tail is identical: …{(pk or '')[-4:]}")
check(4, ps == cs,
      "the resolved SECRETS are identical (compared, never printed — "
      "a tail alone could mask a same-tail swap)")

print("== 3. THE SEEN-TAIL TRIPWIRE ==")
tail = (pk or "")[-4:]
if tail == SEEN_KEY_TAIL:
    check(5, True, f"the resolved tail …{tail} matches the last SEEN "
                   f"tail (no unexplained identity change)")
else:
    print(f"AUDIT: the seam now resolves …{tail} but the last SEEN "
          f"tail is …{SEEN_KEY_TAIL} (tier={ptier!r}). If you rotated "
          f"the key, update SEEN_KEY_TAIL in this probe. If you did "
          f"NOT rotate anything, treat this as a credential incident.")
    check(5, False, f"resolved tail …{tail} != SEEN …{SEEN_KEY_TAIL}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
