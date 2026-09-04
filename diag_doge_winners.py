"""DOGE 17:00 audit, part 2: the scanner crowns champions in the Neon
winners table even when the desktop popup is suppressed — that's the
persistent record of what fired. Pulls today's winners (all, plus DOGE)
with the confidence the scanner actually saw."""
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
out = []

try:
    import db
    db.init_db()
    rows = db.get_tournament_winners(limit=30)
    out.append(f"winners rows: {len(rows)}")
    for w in rows:
        t = w.timestamp
        if t is not None:
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC"))
            t = t.astimezone(MST)
        ts = t.strftime("%m-%d %H:%M:%S") if t else "?"
        out.append(f"{ts}  {w.pair:<11} {w.action:<4} conf={w.confidence} "
                   f"scan={w.scan_type} regime={w.regime or ''} "
                   f"reasons={str(w.reasons or '')[:110]}")
except Exception as exc:
    out.append(f"winners read failed: {exc}")

txt = "\n".join(out)
BASE.joinpath("doge_audit2.txt").write_text(txt, encoding="utf-8")
print("DONE -> doge_audit2.txt")
