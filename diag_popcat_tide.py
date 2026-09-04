"""Reconstruct the fleet tide at ~09:00 Sep 4 (the POPCAT 08:57 crown
window) from the persisted ohlcv cache — the same data _fleet_tide reads.
Also dump the console_state worth-floor settings as they stand now."""
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
out = []

# 1. the fleet tide at the 08-12 window: pairs above their EMA21
try:
    import pandas as pd
    from indicators import ema as _ema
    cc = sqlite3.connect(BASE / "ohlcv_cache.db")
    rows = cc.execute(
        "SELECT pair, ts, close FROM ohlcv WHERE ts < '2026-09-04T09:10' "
        "ORDER BY pair, ts").fetchall()
    cc.close()
    series = {}
    for p, ts, c in rows:
        series.setdefault(p, []).append((str(ts), c))
    # Sep 4 2026 09:10 MDT = 15:10 UTC = epoch ms 1788534600000
    CUT_MS = 1788534600000
    above = total = 0
    detail = []
    for p, closes in series.items():
        cl = [c for ts, c in sorted(closes)
              if ts.isdigit() and int(ts) <= CUT_MS][-40:]
        if len(cl) < 25:
            continue
        total += 1
        e21 = float(_ema(pd.Series(cl), 21).iloc[-1])
        up = cl[-1] > e21
        above += 1 if up else 0
        detail.append((p, up, cl[-1], e21))
    pct = 100.0 * above / total if total else 0
    state = "RISING" if pct >= 60 else ("OUT" if pct < 35 else "FLAT")
    out.append(f"fleet tide at ~09:00 Sep 4 (cached candles): "
               f"{above}/{total} above EMA21 = {pct:.0f}% -> {state}")
    if "POPCAT/USD" in {p for p, *_ in detail}:
        for p, up, c, e in detail:
            if "POPCAT" in p:
                out.append(f"POPCAT at ~09:00: close {c:.5f} vs EMA21 {e:.5f} "
                           f"-> {'ABOVE (uptrend)' if up else 'below (downtrend)'}")
except Exception as exc:
    out.append(f"tide reconstruction failed: {exc}")

# 2. the worth-floor settings now (what the gate would read)
out.append("")
out.append("=== console_state.json (worth floor + goal toggles) ===")
try:
    doc = json.loads((BASE / "console_state.json").read_text(encoding="utf-8"))
    for k in ("worth_floor_frac", "worth_floor_auto", "watch_on", "goal_on",
              "voice_on", "tier"):
        out.append(f"{k} = {doc.get(k, '(unset)')}")
except Exception as exc:
    out.append(f"read error: {exc}")

txt = "\n".join(out)
BASE.joinpath("popcat_tide.txt").write_text(txt, encoding="utf-8")
print("DONE -> popcat_tide.txt")
