"""Run _scalp_headline against the REAL live pool — what the user sees."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

import ccxt
import app
from background_scanner import rotation_key_for_hour, _schedule_for_dow

now = datetime.now(MST)
ex = ccxt.kraken()
ranked, total = app._scalp_headline(ex, rotation_key_for_hour(now.hour), _schedule_for_dow(now.weekday()))

lines = [f"live headline run: {now.strftime('%a %I:%M %p')} Denver",
         f"signals in last 45 min: {total}  ->  distinct moves after clustering: {len(ranked)}", ""]
for i, c in enumerate(ranked[:6]):
    tag = "HEADLINE" if i == 0 else f"runner {i}"
    lines.append(f"{tag:<9} {c['asset']:<10} $20 in ~{c['pace']:.1f}h  "
                 f"since-signal +{c['ran']:.1f}%  setup {c['conf']:.0f}%  "
                 f"book ${c['vol']:,.0f}  (cluster of {c.get('cluster', 1)})")

out = BASE / "headline_live.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
