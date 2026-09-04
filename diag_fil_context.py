"""The context FIL's alert SHOULD have carried: tide + book size."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

import ccxt
ex = ccxt.kraken()
live = float(ex.fetch_ticker("FIL/USD")["last"])
entry, size = 0.717, 5000.0
usd = (live / entry - 1.0) * size

from background_scanner import _fleet_tide
from execution_tiers import liquidity_snapshot

lines = [f"now: {now.strftime('%a %I:%M %p')}"]
lines.append(f"FIL live {live:.4f} | entry {entry} -> ${usd:+,.0f} ({(live/entry-1)*100:+.2f}%)")

tp, st = _fleet_tide(max_age_s=0)
if st:
    hint = {"RISING": "the market carries greens", "FLAT": "pickier, smaller",
            "OUT": "ALL SHIPS SINK TOGETHER"}[st]
    lines.append(f"TIDE: {st} ({tp:.0f}% of the fleet up) — {hint}")

snap = liquidity_snapshot(ex, "FIL/USD", size)
if snap.get("ok"):
    depth = float(snap["book"]["ask_tob"])
    carry = depth * 0.20
    if size <= carry:
        lines.append(f"BOOK: ${depth:,.0f} deep — carries all ${size:,.0f} (all-in was fine)")
    else:
        fit = [c * size for c in (0.25, 0.50, 0.75, 1.00) if c * size <= carry]
        spend = max(fit) if fit else 0
        if spend:
            lines.append(f"BOOK: ${depth:,.0f} deep — carries only ~${carry:,.0f} "
                         f"(should have been {spend/size:.0%} = ${spend:,.0f})")
        else:
            lines.append(f"BOOK: ${depth:,.0f} deep — TOO THIN for contest size")

print("\n".join(lines))
(BASE / "fil_context.txt").write_text("\n".join(lines), encoding="utf-8")
