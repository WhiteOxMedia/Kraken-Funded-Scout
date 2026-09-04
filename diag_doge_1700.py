"""DOGE 17:00 rejection audit (Sep 4): which gate fired, with what floor?
Dumps every DOGE signal today + everything around 17:00, then computes
what the CURRENT rotation-aware floor stack says for 17:00 so the reject
can be compared against old-vs-new rules."""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
out = []

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row

out.append("=== DOGE signals today (all statuses) ===")
for r in conn.execute(
    "SELECT pair, action, confidence, source, status, timestamp, execution_reason, "
    "entry_price, tide_state, tide_pct FROM signals WHERE pair LIKE '%DOGE%' "
    "AND timestamp > '2026-09-04' ORDER BY rowid"
):
    out.append(f"{r['timestamp'][:19]} {r['pair']} {r['action']} conf={r['confidence']} "
               f"src={r['source']} status={r['status']} tide={r['tide_state']}/{r['tide_pct']} "
               f"entry={r['entry_price']} reason={str(r['execution_reason'] or '')[:90]}")

out.append("")
out.append("=== ALL signals 16:55-17:10 (context) ===")
for r in conn.execute(
    "SELECT pair, action, confidence, source, status, timestamp, execution_reason "
    "FROM signals WHERE timestamp > '2026-09-04T16:55' AND timestamp < '2026-09-04T17:11' "
    "ORDER BY rowid"
):
    out.append(f"{r['timestamp'][:19]} {r['pair']:<11} {r['action']} conf={r['confidence']:>4} "
               f"src={r['source']:<16} status={r['status']:<9} "
               f"reason={str(r['execution_reason'] or '')[:80]}")
conn.close()

# what the CURRENT floor stack says for 17:00 (16-20 rotation)
out.append("")
out.append("=== THE CURRENT FLOOR STACK AT 17:00 (16-20 rotation) ===")
try:
    import background_scanner as bs
    out.append(f"ROTATION_POPUP_FLOOR = {getattr(bs, 'ROTATION_POPUP_FLOOR', 'MISSING')}")
    out.append(f"POPUP_NEAR_MISS_PTS = {getattr(bs, 'POPUP_NEAR_MISS_PTS', 'MISSING')}")
    out.append(f"popup_floor_for_rotation(17) = {bs.popup_floor_for_rotation(17)}")
    try:
        _pct, _state = bs._fleet_tide()
        out.append(f"fleet tide now = {_state} ({_pct}%)")
    except Exception as exc:
        out.append(f"tide read failed: {exc}")
    out.append(f"popup_floor(tide={_state if '_state' in dir() else '?'}, lw=0) = "
               f"{bs.popup_floor(_state, 0)}")
    # the auto-trade rotation bar for the same hour
    try:
        name, interval, _conf, thr, _desc, no_buy, key = bs.get_time_block(17, 3)  # Thursday
        out.append(f"get_time_block(17, Thursday) = {name} bar={thr} interval={interval} "
                   f"no_buy={no_buy} rot={key}")
    except Exception as exc:
        out.append(f"time block read failed: {exc}")
except Exception as exc:
    out.append(f"floor stack read failed: {exc}")

txt = "\n".join(out)
BASE.joinpath("doge_audit.txt").write_text(txt, encoding="utf-8")
print("DONE -> doge_audit.txt")
