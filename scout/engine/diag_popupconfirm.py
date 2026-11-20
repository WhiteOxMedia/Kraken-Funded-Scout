"""Popup confirmation study (user rule, Nov 20: track the floor win, fire
only on +$1 profit or the breakout).

Answers the questions the gate left open, from ledgers it already writes:
  1. does confirmation EARN its delay — fire rate, split by reason, and the
     arm-to-fire delay (signal_snapshots' champion-confirmed fire rows);
  2. how strict is the $1 bar per position size — $1 is +10% at the $10
     minimum and +0.02% at the $5,100 contest size, so the same rule is a
     different strategy per size (fire rows carry spend + pending_s);
  3. what the expired rows actually reached — the closest approach each
     row stamped while tracked (popup_suppressions' pending-expired rows
     carry peak_usd), so an expiry is "missed by $0.08" or "never moved",
     never a blind "did not confirm".

Post-expiry outcomes are the suppression backfill's job. This report never
invents a counterfactual it cannot price; it grades the gate's own record.

Run: python diag_popupconfirm.py  ->  scratch/_popupconfirm_diag.txt
"""
import json
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
import scratch_space   # scratch policy: nothing volatile lands in the repo root

DAYS = 30
lines = []


def out(s=""):
    print(s)
    lines.append(s)


def _rows(path, n=2000):
    try:
        raw = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows = []
    for line in raw[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _ts(rec):
    try:
        return datetime.fromisoformat(str(rec.get("ts") or "")).timestamp()
    except Exception:
        return None


def _pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(len(vals) * q))]


cutoff = (bs._now_mst() - timedelta(days=DAYS)).timestamp()
fired = [r for r in _rows(bs.SIGNAL_SNAPSHOTS_FILE)
         if r.get("kind") == "fired"
         and r.get("route") == "champion-confirmed"
         and (_ts(r) or 0) >= cutoff]
expired = [r for r in _rows(bs.SUPPRESSION_LOG_FILE)
           if r.get("gate") == "pending-expired" and (_ts(r) or 0) >= cutoff]
st = bs.popup_confirm_stats(DAYS)

_open_now = sum(1 for r in (bs._load_popup_pending() or {}).values()
                if float(r.get("expires_ts") or 0.0) > time.time())
_rate = (f"  ·  fire rate {st['fired'] / st['arms'] * 100:.0f}%"
         if st["arms"] else "")
out(f"POPUP CONFIRMATION STUDY — last {DAYS} days")
out("=" * 78)
out(f"armed {st['arms']} (fires {st['fired']}, expiries {st['expired']})"
    f"{_rate}  ·  open now {_open_now}")
if not st["arms"]:
    out("")
    out("No arms in the window yet. The gate writes one 'pending' snapshot at")
    out("the floor win and one 'fired' row at confirmation; run this again")
    out("once a few champions have gone through it.")

# ── 1. does confirmation earn its delay? ────────────────────────────────
out("")
out("TIME TO CONFIRM")
out("-" * 78)


def _delay(rows_):
    vals = []
    for r in rows_:
        try:
            vals.append(float(r.get("pending_s") or 0.0) / 60.0)
        except (TypeError, ValueError):
            continue
    return vals


def _is_break(r):
    return str(r.get("confirm") or "").lower().startswith("breakout")


for label, rows_ in (("on the $1 target", [r for r in fired if not _is_break(r)]),
                     ("on the ceiling", [r for r in fired if _is_break(r)]),
                     ("all fires", fired)):
    vals = _delay(rows_)
    if not vals:
        out(f"  {label:<18} none")
        continue
    out(f"  {label:<18} n={len(vals):<3} median {statistics.median(vals):5.1f} min"
        f"   p90 {_pct(vals, 0.9):5.1f} min")

# ── 2. the $1 bar by size ──────────────────────────────────────────────
out("")
out("THE $1 BAR BY SIZE (what the rule asks of that spend)")
out("-" * 78)
BUCKETS = [("$10-99", 10, 100), ("$100-999", 100, 1000),
           ("$1k-2.9k", 1000, 3000), ("$3k+", 3000, float("inf"))]
def _spend(rec):
    try:
        return float(rec.get("spend") or 0.0)
    except (TypeError, ValueError):
        return 0.0


for name, lo, hi in BUCKETS:
    rows_ = [r for r in fired + expired if lo <= _spend(r) < hi]
    if not rows_:
        out(f"  {name:<10} none")
        continue
    asks = [bs.POPUP_CONFIRM_PROFIT_USD / _spend(r) * 100.0
            for r in rows_ if _spend(r) > 0]
    delays = _delay(rows_)
    out(f"  {name:<10} n={len(rows_):<3} median ask {statistics.median(asks):6.2f}%"
        + (f"   median delay {statistics.median(delays):5.1f} min"
           if delays else ""))

# ── 3. what the expired rows reached ───────────────────────────────────
out("")
out("WHAT THE EXPIRED ROWS REACHED (closest approach, never a fire)")
out("-" * 78)
peaks, unrecorded = [], 0
for r in expired:
    try:
        p = float(r.get("peak_usd"))
    except (TypeError, ValueError):
        unrecorded += 1
        continue
    peaks.append(max(0.0, p))
if peaks:
    bar = bs.POPUP_CONFIRM_PROFIT_USD
    near = [p for p in peaks if p >= bar * 0.5]
    out(f"  n={len(peaks):<3} median peak +${statistics.median(peaks):.2f}"
        f"   p90 +${_pct(peaks, 0.9):.2f}")
    out(f"  reached at least half the bar: {len(near)}/{len(peaks)}"
        f" ({len(near) / len(peaks) * 100:.0f}%)")
else:
    out("  no expiry carried a peak yet")
if unrecorded:
    out(f"  {unrecorded} expiry row(s) predate the peak stamp")

# ── classes: what else fires, and why it is not this gate ──────────────
out("")
out("OTHER POPUP CLASSES (never counted as confirmed)")
out("-" * 78)
sure = [r for r in _rows(bs.SIGNAL_SNAPSHOTS_FILE)
        if r.get("kind") == "fired" and r.get("route") == "sure-thing"
        and (_ts(r) or 0) >= cutoff]
pre = sum(1 for r in sure if r.get("trigger") == "pre-breakout")
out(f"  Sure Thing fires {len(sure)} (pre-breakout {pre}, crack {len(sure) - pre})")
out("  pre-breakout pops enter BEFORE the crack by design: a different class,")
out("  labelled PRE-BREAKOUT in the popup and 'pre-breakout' in the snapshot.")

out("")
out("NOTES")
out("-" * 78)
out("  · arms = champion-confirmed fires + 'pending-expired' suppressions")
out("    (every arm ends one way); open rows ride popup_pending.json.")
out("  · post-expiry outcomes are graded by the suppression backfill, not")
out("    here — this study never asserts would-have-won.")
out("  · the rule stays $1 until the delay-by-size table says otherwise:")
out("    at the contest size the bar is nearly free, at the minimum it is")
out("    +10%, so a percentage floor is a decision to make from these rows.")

Path(scratch_space.report_temp("_popupconfirm_diag.txt")).write_text(
    "\n".join(lines) + "\n", encoding="utf-8")
