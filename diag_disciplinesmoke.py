"""Smoke: run the discipline evaluation on the real data (public
candles, the winners table, the SOLD ledger) for today's closed
rotations. Writes _disciplinesmoke.txt."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import ccxt
import discipline as dc

MST = ZoneInfo("America/Denver")
ex = ccxt.kraken({"enableRateLimit": True})
lines = []


def out(s=""):
    print(s)
    lines.append(s)


now = datetime.now(MST)
for rot in ("00-04", "04-08", "08-12", "12-16"):
    if now.hour < int(rot.split("-")[0]) + 4 and now.hour >= int(rot.split("-")[0]):
        pass
    try:
        r = dc.evaluate_rotation(ex, rot, speak=False)
        out(f"=== {rot} ===")
        if r.get("score") is None:
            out(f"  error: {r.get('error')}")
            continue
        out(f"  score: {r['score']}   realized: ${r['realized_pnl']:+,.2f}   "
            f"left on the table: ${r['total_left']:,.2f}")
        out(f"  components: {r['usd_left']}")
        out(f"  popups: {[(p['pair'], 'paid' if p['paid'] else 'no', 'taken' if p['taken'] else 'MISSED') for p in r['popups']]}")
        out(f"  lesson: {r['lesson']}")
        out(f"  insight: {dc._insight(r, [], [])}")
    except Exception as exc:
        out(f"=== {rot} === FAILED: {exc}")

Path("_disciplinesmoke.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n-> _disciplinesmoke.txt")
