"""POPCAT verification (Sep 4): did the 08-12 'POPCAT paid its goal' claim
come from a REAL fired popup (voice log speaks every champion alert) or
from the discipline score's winners-table approximation? Pulls all three
records: discipline_state.json, voice_log POPCAT lines, winners POPCAT rows."""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
out = []

# 1. the discipline state: the 08-12 evaluation verbatim
out.append("=== discipline_state.json (the 08-12 evaluation) ===")
try:
    doc = json.loads((BASE / "discipline_state.json").read_text(encoding="utf-8"))
    for h in doc.get("history", []):
        if "08-12" in str(h.get("rot", "")):
            out.append(json.dumps(h, indent=1, default=str)[:2500])
    out.append("lessons: " + json.dumps(doc.get("lessons", [])[-3:], default=str))
except Exception as exc:
    out.append(f"read error: {exc}")

# 2. the voice log: every champion popup SPEAKS 'Buy X!'
out.append("")
out.append("=== voice_log POPCAT lines (all time) ===")
try:
    n = 0
    for line in (BASE / "voice_log.jsonl").read_text(encoding="utf-8").splitlines():
        if "POPCAT" in line:
            out.append(line[:200])
            n += 1
    if not n:
        out.append("(none — no popup ever SPOKE a POPCAT call)")
except Exception as exc:
    out.append(f"read error: {exc}")

# 3. the winners table: POPCAT rows today (the approximation's source)
out.append("")
out.append("=== winners table POPCAT today ===")
try:
    import db
    db.init_db()
    rows = [w for w in db.get_tournament_winners(limit=200)
            if "POPCAT" in (w.pair or "")]
    for w in rows:
        t = w.timestamp
        if t is not None:
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC"))
            t = t.astimezone(MST)
        out.append(f"{t.strftime('%m-%d %H:%M:%S') if t else '?'}  conf={w.confidence} "
                   f"scan={w.scan_type} regime={w.regime}")
except Exception as exc:
    out.append(f"read error: {exc}")

txt = "\n".join(out)
BASE.joinpath("popcat_verify.txt").write_text(txt, encoding="utf-8")
print("DONE -> popcat_verify.txt")
