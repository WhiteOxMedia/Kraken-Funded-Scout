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
app.ASSETS = list(_orig_assets) + ["TESTA", "TESTB", "TESTC", "TESTD"]
PACE = {"TESTA/USD": 0.8, "TESTB/USD": 1.2, "TESTC/USD": 1.0, "TESTD/USD": 0.5}
bs._hours_to_scalp = lambda pair, entry: PACE.get(pair)
bs._max_resolve_hours = lambda: 6.0

class FakeEx:
    def __init__(self, prices):
        self.prices = prices
    def fetch_ticker(self, pair):
        return {"last": self.prices[pair]}

now = datetime.now(MST)
rows = [
    # pair, conf, entry, ts, live
    ("TESTA/USD", 80, 1.00, now,                      1.005),  # best pace, same sweep as TESTB
    ("TESTB/USD", 70, 1.00, now - timedelta(seconds=60), 1.010),  # same sweep, slower -> collapses
    ("TESTC/USD", 65, 1.00, now - timedelta(minutes=20), 0.995),  # different sweep -> own move
    ("TESTD/USD", 55, 1.00, now,                      1.000),  # below floor -> excluded
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

    check("synthetic signals considered (live pool adds more)", total >= 4, f"total={total}")
    assets = [c["asset"] for c in ranked]
    check("headline = TESTA (fastest $20)", assets and assets[0] == "TESTA", f"{assets}")
    check("TESTB collapsed into TESTA's sweep", "TESTB" not in assets, f"{assets}")
    check("TESTC survives as its own move", "TESTC" in assets, f"{assets}")
    check("TESTD excluded (55% < 60 floor)", "TESTD" not in assets, f"{assets}")
    ta = next(c for c in ranked if c["asset"] == "TESTA")
    check("TESTA cluster size = 2 (the wave)", ta.get("cluster") == 2, f"{ta.get('cluster')}")
    check("TESTA pace carried", abs(ta["pace"] - 0.8) < 0.01, f"{ta['pace']}")

    # chase filter: TESTC ran +3% since its signal -> excluded
    app._scalp_headline.clear()   # ttl cache would serve the first call's data
    ex2 = FakeEx({p: (1.03 if p == "TESTC/USD" else live) for p, _, _, _, live in rows})
    ranked2, _ = app._scalp_headline(ex2, "12-16", "weekday")
    check("ran +3% since signal -> dropped", "TESTC" not in [c["asset"] for c in ranked2])
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
