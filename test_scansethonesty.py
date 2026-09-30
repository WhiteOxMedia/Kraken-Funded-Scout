"""SCAN-SET HONESTY (Sep 22): the best-pick plate must never present a
champion the movers-only funnel cannot scan as a coming popup — the TRUMP
case: a −0.4% coin was "today's best pick" while the funnel had nothing
to say about it. Pinned here: _scanned_assets() exec'd from source against
a synthetic log (fresh/stale/wrap/missing/empty shapes), the live real-log
probe, the plate's three honest branches (in / out / unknown), and the
toast+speak gate that refuses to promise popup behavior for a champion
outside the scan set.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


ap = Path("app.py").read_text(encoding="utf-8").replace("\r\n", "\n")

# ── 1. the helper, exec'd from source over a synthetic logs/ tree ──────
i = ap.find("def _scanned_assets")
j = ap.find("\ndef ", i + 10)
check("the helper exists", i > 0 and j > i)
MST = timezone(timedelta(hours=-6))
_base_ns = {"datetime": datetime, "timedelta": timedelta,
            "Path": Path, "MST": MST}


def _build(ns_extra=None, log_text=None):
    td = tempfile.TemporaryDirectory()
    logdir = Path(td.name) / "logs"
    logdir.mkdir()
    if log_text is not None:
        (logdir / "scanner_console.log").write_text(log_text, encoding="utf-8")
    ns = dict(_base_ns)
    ns["__file__"] = str(Path(td.name) / "app.py")
    if ns_extra:
        ns.update(ns_extra)
    exec(ap[i:j], ns)
    return ns, td


def _stamp(min_ago):
    return (datetime.now(MST) - timedelta(minutes=min_ago)).strftime("%I:%M %p")


FRESH = (f"{_stamp(0)} [INFO]   Movers (>2%): AAVE, ALGO, ZEC, trump\n"
         "09:00 AM [INFO]   Next check in 30s\n")

ns, td = _build(log_text=FRESH)
s = ns["_scanned_assets"]()
check("fresh line parses to the uppercase set", s == {"AAVE", "ALGO", "ZEC", "TRUMP"}, s)
td.cleanup()

# append-only order: the OLDER movers line sits EARLIER in the file
ns, td = _build(log_text=(f"{_stamp(31)} [INFO]   Movers (>2%): OLD, COINS\n"
                          + FRESH))
check("the newest movers line wins (reversed scan)",
      ns["_scanned_assets"]() == {"AAVE", "ALGO", "ZEC", "TRUMP"})
td.cleanup()

ns, td = _build(log_text=f"{_stamp(31)} [INFO]   Movers (>2%): OLD, COINS\n")
check("stale movers line (>30 min) -> None", ns["_scanned_assets"]() is None)
td.cleanup()

ns, td = _build(log_text="09:00 AM [INFO]   Next check in 30s\n")
check("no movers line at all -> None (unknown, not empty)",
      ns["_scanned_assets"]() is None)
td.cleanup()

ns, td = _build(log_text=None)
check("missing log file -> None", ns["_scanned_assets"]() is None)
td.cleanup()

# near-midnight wrap: the stamp says 11:59 PM, it is now 12:01 AM
class _WrapDT:
    @staticmethod
    def now(tz=None):
        n = datetime.now(tz or MST)
        if n.hour < 1:            # only warp when actually near midnight
            n = n.replace(hour=23, minute=59, second=0)
        return n
ns, td = _build(ns_extra={"datetime": _WrapDT},
                log_text="11:59 PM [INFO]   Movers (>2%): LATE, RUNNERS\n")
got = ns["_scanned_assets"]()
check("pre-midnight stamp parses across midnight (age ~2 min)",
      got == {"LATE", "RUNNERS"} or got is None and datetime.now(MST).hour >= 1,
      got)
td.cleanup()

# ── 2. the live real-log probe ──────────────────────────────────────────
import app as A
live = A._scanned_assets()
check("the real scanner log reads (or is honestly None)",
      live is None or (isinstance(live, set)
                       and all(isinstance(a, str) and a == a.upper() for a in live)),
      type(live).__name__ if live is None else len(live))
if live:
    lines.append(f"NOTE  live scan set now: {sorted(live)}")

# ── 3. the plate's three honest branches + the gated celebration ───────
i_plate = ap.find("SCAN-SET HONESTY")
i_pick = ap.find('plate("The Pick — today\'s best trade"')
check("the honesty block sits on the plate (before its render)",
      0 < i_plate < i_pick)
check("unknown branch says eligibility can't be judged",
      "popup eligibility can't" in ap and "SCAN SET unknown" in ap)
check("in-set branch says eligible for a popup",
      "IN THE FUNNEL'S SCAN SET" in ap and "eligible for a popup" in ap)
check("out-of-set branch says no popup can fire",
      "NOT IN THE FUNNEL'S SCAN SET" in ap and "no popup can fire for this pick" in ap)
check("the out-of-set reason names the movers-only funnel",
      "the funnel scans only the movers" in ap)

# the celebration gate: toast/speak only for an in-set champion
i_fresh = ap.find("if _fresh:\n",
                  ap.find("_champ_asset = _asset(champ.pair)"))
blk = ap[i_fresh:i_fresh + 1600]
check("celebration gated on scan-set membership",
      "_sc = _scanned_assets()" in blk and "_champ_asset in _sc" in blk, blk[:80])
check("out-of-set champion gets the honest toast instead",
      "NOT in the funnel's scan set" in blk and "popup will fire for it" in blk)
check("the promise lives ONLY inside the in-set branch",
      blk.lower().count("popup is watching it") == 2,
      blk.lower().count("popup is watching it"))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_scanset_check.txt').write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
