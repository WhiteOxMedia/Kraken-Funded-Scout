"""GATE AUDIT (user question: 'user rules could be blocking good popups?')
Aggregates popup_suppressions.jsonl by gate: volume, how close candidates
scored to the floor, and how many scored ABOVE the floor (killed by the
gate hierarchy, not the score). Cross-references the discipline seam's
LEARNED system_burial weight (buried payers sting it up; buried duds ease
it down). Writes _gateaudit.txt.
Run: .venv\\Scripts\\python.exe -X utf8 diag_gateaudit.py
"""
import json
from collections import defaultdict
from pathlib import Path

lines = []


def out(s=""):
    print(s)
    lines.append(s)


rows = []
p = Path("popup_suppressions.jsonl")
if p.exists():
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

out(f"=== GATE AUDIT — {len(rows)} suppressions, "
    f"{rows[0]['ts'][:10] if rows else 'n/a'} → {rows[-1]['ts'][:10] if rows else 'n/a'} ===")
out("")

by_gate = defaultdict(list)
for r in rows:
    by_gate[r.get("gate", "?")].append(r)

out("── by gate ──")
out(f"{'gate':<22} {'count':>6} {'pairs':>6} {'max quick':>9} "
    f"{'quick≥floor':>11} {'≥floor-5':>8}")
for gate, rs in sorted(by_gate.items(), key=lambda kv: -len(kv[1])):
    quicks = [float(r.get("quick") or 0) for r in rs]
    floors = [float(r.get("floor") or 0) for r in rs]
    above = sum(1 for q, f in zip(quicks, floors) if f and q >= f)
    near = sum(1 for q, f in zip(quicks, floors) if f and q >= f - 5)
    pairs = len({r.get("pair") for r in rs})
    out(f"{gate:<22} {len(rs):>6} {pairs:>6} {max(quicks):>9.1f} "
        f"{above:>11} {near:>8}")
out("")

# the near-misses: scored at/above the floor but killed by the gate
out("── killed AT/ABOVE the floor (the gate, not the score, did it) ──")
for gate, rs in sorted(by_gate.items()):
    for r in rs:
        q, f = float(r.get("quick") or 0), float(r.get("floor") or 0)
        if f and q >= f:
            out(f"  {r['ts'][:16]}  {r.get('pair','?'):<16} {gate:<18} "
                f"quick {q:>5.1f} vs floor {f:.0f}   {r.get('tide','')}")
out("")

# within 5 of the floor — the near-miss band
out("── near-miss band (quick within 5 below floor) — latest 12 ──")
near_all = []
for gate, rs in sorted(by_gate.items()):
    for r in rs:
        q, f = float(r.get("quick") or 0), float(r.get("floor") or 0)
        if f and f - 5 <= q < f:
            near_all.append(r)
near_all.sort(key=lambda r: r["ts"], reverse=True)
for r in near_all[:12]:
    out(f"  {r['ts'][:16]}  {r.get('pair','?'):<16} {r.get('gate','?'):<18} "
        f"quick {float(r.get('quick') or 0):>5.1f} vs floor "
        f"{float(r.get('floor') or 0):.0f}")
out("")

# what the seam learned: buried payers push system_burial weight up
try:
    ds = json.loads(Path("discipline_state.json").read_text())
    w = ds.get("weights", {})
    out("── learned weights (discipline_state.json) ──")
    out(f"  {json.dumps(w)}")
    hist = ds.get("history", ds.get("lessons", []))
    if isinstance(hist, list):
        out(f"  lessons logged: {len(hist)}")
        for h in hist[-6:]:
            if isinstance(h, dict):
                out(f"    {str(h)[:150]}")
except Exception as exc:
    out(f"  (discipline_state: {exc})")

Path("_gateaudit.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n-> _gateaudit.txt")
