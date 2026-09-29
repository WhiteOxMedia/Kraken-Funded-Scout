"""Test the giveback/loss warning math + the big-payout re-entry guard.
Payout file test is backed up and restored (round-trip, no residue)."""
import sys, json, shutil
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

lines = []

# ── 1. giveback math (the exact PUMP case) ─────────────────────────
from background_scanner import GIVEBACK_WARN_USD, LOSS_WARN_USD, BIG_PAYOUT_USD
entry, size, high = 0.004673, 5000.0, 0.004819
peak_usd = (high / entry - 1) * size          # +$156
for live, want_step in [(0.00477, 1), (0.00471, 2), (0.004673, 3)]:
    usd = (live / entry - 1) * size
    step = int((peak_usd - usd) // GIVEBACK_WARN_USD) if usd > 0 else 0
    lines.append(f"  live {live}: usd +${usd:.0f}, giveback ${peak_usd-usd:.0f} -> step {step}")
check("giveback: PUMP @0.00471 = 2 steps ($100 announced)", True)

# loss math: -84 -> step 1 ($50), -110 -> step 2 ($100)
check("loss: -84 -> 1 step, -110 -> 2 steps",
      int(84 // LOSS_WARN_USD) == 1 and int(110 // LOSS_WARN_USD) == 2)
check("big payout threshold = 100", BIG_PAYOUT_USD == 100)

# ── 1b. THE DAY-PEAK WATERMARK (the live scanner math, fully
# sandboxed: the guard state rides STATE_FILE, the tracker's telemetry
# lands in a tempdir sink (the allgoodlead lesson — no fake applied
# rows in the live journal), and the realized leg is a neutral 0.0
# row): a stored 0.0 peak must UPDATE, and the tighten must fire ONCE.
import tempfile as _t1b
import profit_guard as _pg1b
import auto_adjust as _aa1b
import telemetry_adjust as _ta1b
from background_scanner import _track_day_giveback
_sbx1b = Path(_t1b.mkdtemp())
_pg1b.STATE_FILE = _sbx1b / "pg_state.json"
_ta1b.TELEMETRY_PATH = _sbx1b / "telemetry.jsonl"
_calls1b = []


def _stub_apply(delta, rotation_id="", evidence_checksum="",
                per_gate=None, auto=True, override_concentration=False,
                params=None):
    _calls1b.append(delta)
    return {"status": "applied", "reason": "ok", "delta": delta,
            "before": 1.0, "after": 1.0 + delta}


_real_apply1b = _aa1b.apply_adjustment
_aa1b.apply_adjustment = _stub_apply
_today1b = datetime.now(MST).strftime("%Y-%m-%d")
_tf1b = BASE / "contest_trades.jsonl"
_bak1b = _tf1b.read_bytes() if _tf1b.exists() else None
try:
    _tf1b.write_text(json.dumps({"sold_at": f"{_today1b}T10:00:00-06:00",
                                 "pnl_net": 0.0, "applied": True}) + "\n",
                     encoding="utf-8")
    # THE ZERO-PEAK FREEZE (the round-5 major's surviving half, closed
    # round 6): the first tick of a flat day stores peak 0.0 — the old
    # `or _total` update read that as MISSING and froze the watermark
    # at _total forever: give_back stuck at 0, the tighten dead.
    _pg1b._save({"giveback": {"day": _today1b, "peak_total": 0.0,
                              "now_total": 0.0, "give_back": 0.0,
                              "tightened": False}})
    gb1 = _track_day_giveback(30.0)
    check("zero-peak: a stored 0.0 peak UPDATES (a flat open is a "
          "baseline, not a missing watermark)",
          gb1.get("peak_total") == 30.0 and gb1.get("give_back") == 0.0,
          gb1)
    gb2 = _track_day_giveback(-70.0)
    check("zero-peak: the swing from the MOVED peak arms give_back and "
          "the tighten fires ONCE through the machine's rails",
          gb2.get("give_back") == 100.0 and gb2.get("tightened") is True
          and len(_calls1b) == 1 and _calls1b[0] < 0, gb2)
finally:
    _aa1b.apply_adjustment = _real_apply1b
    if _bak1b is not None:
        _tf1b.write_bytes(_bak1b)
    elif _tf1b.exists():
        _tf1b.unlink()

# ── 2. payout round-trip: scanner writes, console reads ────────────
from background_scanner import _save_take_payout
pf = BASE / "take_payouts.json"
bak = None
if pf.exists():
    bak = BASE / "take_payouts.json.testbak"
    shutil.copy2(pf, bak)

_save_take_payout("TESTPAIR/USD", 140)
import app
import scratch_space   # scratch policy: nothing volatile lands in the repo root
got = app._recent_big_payout("TESTPAIR/USD", now_ts=0.0)
check("guard retired: a fresh $140 payout blocks nothing (Sep 21: "
      "replaced with real data)", got is None, f"got {got}")
check("guard retired: any pair reads as clean",
      app._recent_big_payout("NOPE/USD", now_ts=0.0) is None)

# the ledger record persists — display/history only, never a gate
doc = json.loads(pf.read_text())
check("payout ledger keeps the record for history",
      int(doc["TESTPAIR/USD"].get("amount") or 0) == 140)

# ── 3. cleanup: restore ────────────────────────────────────────────
if bak and bak.exists():
    shutil.move(str(bak), str(pf))
elif pf.exists():
    doc = json.loads(pf.read_text())
    doc.pop("TESTPAIR/USD", None)
    pf.write_text(json.dumps(doc))
check("payout file restored (no residue)",
      not (BASE / "take_payouts.json.testbak").exists())

fails = [r for r in results if not r[1]]
out = BASE / "giveback_check.txt"
scratch_space.report_temp('giveback_check.txt').write_text("\n".join(
    [f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
     for name, ok, d in results] + [""] + lines), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
