"""Test the DEPTH-STABILITY FIX (Aug 31 — the gate that ate a 92% pick).
The old abs() form flagged books getting DEEPER as "unstable" (HYPE
$536→$12,865 — a 24x improvement, killed a 92% lone-wolf pick) and judged
sub-$100 front rows (CRV $49→$32 — one tiny order slot). The fix: only a
DRY of a MATERIAL row counts; the cumulative tob-drop check (already
directional) remains the real measure of a book evaporating."""
import sys
import time
from pathlib import Path

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


import execution_tiers as et
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# ── fake the book: two snapshots, no real API, no real 10s wait ────────
_real_sleep = time.sleep
time.sleep = lambda *_: None


def mkbook(level_usd, tob_usd=50_000.0, spread_bps=5.0):
    """A book whose front ask row carries level_usd and whose top-10
    cumulative depth is tob_usd (price 1.0 → size == usd)."""
    return {
        "pair": "TEST/USD", "ts": time.time(),
        "bids": [[0.999, tob_usd]],
        "asks": [[1.0, level_usd]],
        "best_bid": 0.999, "best_ask": 1.0, "mid": 0.9995,
        "spread_bps": spread_bps,
        "bid_tob": tob_usd, "ask_tob": tob_usd,
    }


def stability(b1, b2):
    _books = iter([b1, b2])
    et.measure_book = lambda exchange, pair: next(_books)
    return et.check_depth_stability(None, "TEST/USD", side="asks")


# ── 1. today's three rejections, re-judged ────────────────────────────
r = stability(mkbook(536), mkbook(12_865))
check("HYPE $536→$12,865 (24x DEEPER) is STABLE now — an improvement is "
      "never 'unstable' (was: rejected, killed the 92% pick)",
      r.get("stable") is True, r.get("reason"))

r = stability(mkbook(49), mkbook(32))
check("CRV $49→$32 is STABLE now — a sub-$500 front row is order-slot "
      "noise, not a book (was: rejected)",
      r.get("stable") is True, r.get("reason"))

r = stability(mkbook(5_419), mkbook(54))
check("XMR's shape $5,419→$54 still flags — a MATERIAL front order "
      "vanishing in 10s IS notable (by design)",
      r.get("stable") is False and "dried" in r.get("reason", ""),
      r.get("reason"))

# ── 2. the boundary math ──────────────────────────────────────────────
r = stability(mkbook(1_000), mkbook(850))
check("normal churn: $1,000→$850 (15% dry) is stable", r.get("stable") is True, r.get("reason"))
r = stability(mkbook(1_000), mkbook(700))
check("material dry 30% flags ($1,000→$700)", r.get("stable") is False, r.get("reason"))
r = stability(mkbook(1_000), mkbook(2_000))
check("material row DOUBLING is stable (deeper = better)",
      r.get("stable") is True, r.get("reason"))
r = stability(mkbook(499), mkbook(1))
check("tiny row drying 100% is stable (< $500 floor)",
      r.get("stable") is True, r.get("reason"))

# ── 3. the other two checks still work (directional, unchanged) ──────
r = stability(mkbook(1_000, spread_bps=5.0), mkbook(1_000, spread_bps=40.0))
check("spread blowout 5→40bps still flags",
      r.get("stable") is False and "spread" in r.get("reason", ""), r.get("reason"))
r = stability(mkbook(1_000, tob_usd=10_000), mkbook(1_000, tob_usd=2_000))
check("cumulative book drying 80% still flags (the REAL book measure)",
      r.get("stable") is False and "book depth" in r.get("reason", ""), r.get("reason"))

# ── 4. fail-open unchanged ────────────────────────────────────────────
et.measure_book = lambda exchange, pair: None
r = et.check_depth_stability(None, "TEST/USD")
check("book unavailable -> fail-open (stable)", r.get("stable") is True, r.get("reason"))

time.sleep = _real_sleep

# ── 5. source checks ──────────────────────────────────────────────────
src = open("execution_tiers.py", encoding="utf-8").read()
check("the symmetric abs() form is gone", "abs(d2 - d1)" not in src)
check("directional dry-only comparison present", "d2 < d1 * (1.0 - STABILITY_DEPTH_PCT" in src)
check("material-row floor constant present", "STABILITY_LEVEL_MIN_USD = 500.0" in src)
check("cumulative tob-drop check still directional (drops only)",
      "(tob1 - tob2) / tob1" in src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_depthcheck_check.txt'), "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
