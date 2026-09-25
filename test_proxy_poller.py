"""test_proxy_poller.py — THE POLLER'S PARSE CONTRACT (Sep 25).

Grades pool/proxy_poller.py against the API shapes the LIVE proxy
actually serves (discovered by the live smoke, which caught the
original draft iterating /2/summary's 'workers' COUNT as a list —
a bug that meant the poller could never parse a single worker):

  1. parse_workers joins /1/miners (identities: user =
     'wallet.worker_name', columns self-described by 'format') with
     /1/workers (per-rig lifetime share counters + hashrate)
  2. the join keys work for both rig_id and worker-name shapes
  3. broken/empty docs degrade to empty lists, never exceptions
  4. fetch fail-soft: a dead proxy returns None, never raises
  5. the MinerYield wiring is intact (fail-soft import + call sites)
  6. hermetic: the live jackpot.db is byte-identical throughout

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import hashlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


SANDBOX = tempfile.mkdtemp(prefix="proxy_parse_drive_")
sys.path.insert(0, os.path.join(HERE, "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = __import__("pathlib").Path(
    os.path.join(SANDBOX, "jackpot.db"))
db.init_db()

import proxy_poller as pp                              # noqa: E402

LIVE_DB = os.path.join(HERE, "pool", "jackpot.db")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_live_before = (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) if os.path.exists(LIVE_DB) else None

# fixtures shaped EXACTLY like the live proxy's answers (observed
# against xmrig-proxy 6.26.0 on this box)
MINERS_DOC = {
    "format": ["id", "ip", "tx", "rx", "state", "diff", "user",
               "password", "rig_id", "agent"],
    "miners": [
        [364, "127.0.0.1", 521829, 75715, 2, 36220,
         "4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kr"
         "adKAqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms.Birdsnbubbles",
         "x", "Birdsnbubbles", "XMRig/6.26.0"],
        [365, "127.0.0.2", 10, 10, 2, 100,
         "4" + "b" * 94 + ".rig-two",
         "x", "rig-two", "XMRig/6.26.0"],
    ],
}
WORKERS_DOC = {
    "hashrate": {"total": [2.35, 1.1]},
    "mode": "rig_id",
    "workers": [
        ["Birdsnbubbles", "127.0.0.1", 1, 16704, 0, 138, 665261958,
         1790354581367, 2.35, 1.1, 1.18, 1.51, 1.41],
        ["rig-two", "127.0.0.2", 1, 5, 1, 2, 1000, 100,
         0.0, 0.0, 0.0, 0.0, 0.0],
    ],
}

print("== 1. THE JOIN: IDENTITIES + LIFETIME COUNTERS ==")
rows = pp.parse_workers(MINERS_DOC, WORKERS_DOC)
check(1.1, len(rows) == 2, f"both miners parsed ({len(rows)} rows)")
b = next(r for r in rows if r["worker_name"] == "Birdsnbubbles")
check(1.2, b["wallet_address"].startswith("4AoPP7sz")
      and b["shares_valid"] == 16704 and b["shares_invalid"] == 138,
      "the 'user' column splits wallet.worker_name; the rig row's "
      "LIFETIME accepted/invalid counters land as-is (deltas are the "
      "DB layer's job)")
check(1.3, b["hashrate"] == 2.35,
      "hashrate = the max bucket (the 10s live window)")
two = next(r for r in rows if r["worker_name"] == "rig-two")
check(1.4, two["wallet_address"] == "4" + "b" * 94
      and two["shares_valid"] == 5 and two["shares_invalid"] == 2,
      "the join falls back to worker-name keying when rig_id differs")

print("== 2. BROKEN DOCS DEGRADE, NEVER RAISE ==")
check(2.1, pp.parse_workers({}, {}) == [],
      "empty docs → empty list")
check(2.2, pp.parse_workers({"format": [], "miners": [None, [], [1, 2]]},
                            {"workers": ["x", 5, None]}) == [],
      "malformed rows are skipped, not crashed on")
check(2.3, pp.parse_workers(MINERS_DOC, None)[0]["shares_valid"] == 0,
      "a missing workers doc still yields identities (zeroed stats)")

print("== 3. FETCH FAIL-SOFT ==")
pp.PROXY_API = "http://127.0.0.1:1"       # nothing listens on port 1
check(3.1, pp.fetch_miners() is None and pp.fetch_workers() is None
      and pp.fetch_proxy_stats() is None,
      "a dead proxy returns None from every fetch — the poller loop "
      "logs and keeps breathing")

print("== 4. THE MINERYIELD WIRING IS INTACT ==")
with open(os.path.join(HERE, "pool", "proxy_poller.py"),
          encoding="utf-8") as f:
    src = f.read()
check(4.1, "import miner_yield" in src and "miner_yield = None" in src
      and "note_workers(workers)" in src and "flush_daily()" in src,
      "the yield observer still rides update_workers, fail-soft")

print("== 5. HERMETIC ==")
if _live_before:
    check(5.1, (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before,
          "the live jackpot.db is sha256+mtime identical throughout")
else:
    check(5.1, not os.path.exists(LIVE_DB),
          "no live jackpot.db — nothing to protect, nothing touched")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
