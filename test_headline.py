"""Test _scalp_headline: scalp ranking, sweep clustering, filters.
Synthetic signals inserted then deleted; engine pace patched."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

import background_scanner as bs
import app

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# ── patches ────────────────────────────────────────────────────────
_orig_assets = app.ASSETS
_orig_pace, _orig_cap = bs._hours_to_scalp, bs._max_resolve_hours
app.ASSETS = list(_orig_assets) + ["TESTA", "TESTB", "TESTC", "TESTD", "TESTE"]
PACE = {"TESTA/USD": 0.8, "TESTB/USD": 1.2, "TESTC/USD": 1.0, "TESTD/USD": 0.5, "TESTE/USD": 1.4}
bs._hours_to_scalp = lambda pair, entry: PACE.get(pair)
bs._max_resolve_hours = lambda: 6.0

class FakeEx:
    def __init__(self, prices):
        self.prices = prices
    def fetch_ticker(self, pair):
        return {"last": self.prices[pair]}

# pin all synthetic timestamps to the 10-minute bucket grid so the sweep
# clustering is deterministic no matter when the test runs
_bucket_start = (datetime.now(MST).timestamp() // 600) * 600
def _ts(offset_sec: float) -> datetime:
    return datetime.fromtimestamp(_bucket_start + offset_sec, MST)

now = datetime.now(MST)
rows = [
    # pair, conf, entry, ts, live
    ("TESTA/USD", 80, 1.00, _ts(60),   1.005),  # best pace, same sweep as TESTB
    ("TESTB/USD", 70, 1.00, _ts(120),  1.010),  # same sweep, slower -> collapses
    ("TESTC/USD", 65, 1.00, _ts(-2100), 0.995), # TOO OLD (~35m > 30m) -> excluded
    ("TESTD/USD", 55, 1.00, _ts(60),   1.000),  # below floor -> excluded
    ("TESTE/USD", 68, 1.00, _ts(-300), 0.998),  # young, PREVIOUS sweep -> runner
]

DB = BASE / "signals.db"
conn = sqlite3.connect(DB)
for pair, conf, entry, ts, _ in rows:
    conn.execute(
        "INSERT INTO signals (signal_id, pair, action, confidence, timestamp, "
        "valid_until, entry_price, status, source) VALUES (?, ?, 'buy', ?, ?, ?, ?, 'rejected', 'scout_auto')",
        (f"synth_{pair.split('/')[0]}", pair, conf, ts.isoformat(),
         (ts + timedelta(minutes=30)).isoformat(), entry))
conn.commit()
conn.close()

try:
    ex = FakeEx({p: live for p, _, _, _, live in rows})
    ranked, total = app._scalp_headline(ex, "12-16", "weekday")

    check("synthetic signals considered (live pool adds more)", total >= 5, f"total={total}")
    assets = [c["asset"] for c in ranked]
    check("headline = TESTA (fastest $20)", assets and assets[0] == "TESTA", f"{assets}")
    check("TESTB collapsed into TESTA's sweep", "TESTB" not in assets, f"{assets}")
    check("TESTC excluded (~35 min old — past the 30-min cap)", "TESTC" not in assets, f"{assets}")
    check("TESTD excluded (55% < 60 floor)", "TESTD" not in assets, f"{assets}")
    check("TESTE survives as its own young move", "TESTE" in assets, f"{assets}")
    ta = next(c for c in ranked if c["asset"] == "TESTA")
    check("TESTA cluster size = 2 (the wave)", ta.get("cluster") == 2, f"{ta.get('cluster')}")
    check("TESTA pace carried", abs(ta["pace"] - 0.8) < 0.01, f"{ta['pace']}")
    check("TESTA age is young (< 10 min)", ta.get("age_min", 99) < 10, f"{ta.get('age_min')}")

    # chase is INFO now (calibration: chases hit at the same rate) — a ran
    # signal still shows on the board, with its ran % displayed on the card
    app._scalp_headline.clear()   # ttl cache would serve the first call's data
    ex2 = FakeEx({p: (1.03 if p == "TESTE/USD" else live) for p, _, _, _, live in rows})
    ranked2, _ = app._scalp_headline(ex2, "12-16", "weekday")
    te2 = next((c for c in ranked2 if c["asset"] == "TESTE"), None)
    check("ran +3% since signal -> still shown (warning, not a block)",
          te2 is not None and abs(te2["ran"] - 3.0) < 0.2,
          f"{te2 and te2['ran']}")
finally:
    # ── cleanup ────────────────────────────────────────────────────
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM signals WHERE signal_id LIKE 'synth_TEST%'")
    conn.commit()
    conn.close()
    app.ASSETS = _orig_assets
    bs._hours_to_scalp, bs._max_resolve_hours = _orig_pace, _orig_cap
    conn = sqlite3.connect(DB)
    left = conn.execute("SELECT COUNT(*) FROM signals WHERE signal_id LIKE 'synth_TEST%'").fetchone()[0]
    conn.close()
    check("synthetic rows cleaned", left == 0, f"{left} left")

fails = [r for r in results if not r[1]]
out = BASE / "headline_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
