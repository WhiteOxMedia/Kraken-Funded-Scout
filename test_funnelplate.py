"""Test THE FUNNEL PLATE (Sep 23) — the console's live rotation view:
every candidate the scanner read this rotation, its quick/brain read,
the bar it faced, and which gate buried it (or FIRED).

Graded here:
  1. the row builder (pure, sliced from app.py): merge + newest-first,
     the quick dim below 45, the bar from 'bar' or 'floor', the brain
     shown only when consulted, FIRED in green, tide-out/death-line in
     red, the 14-row cap, the honest total
  2. the render wiring: reads BOTH ledgers, scopes by the scanner's own
     rot key + the MST date, snapshots only kind='fired', the empty-
     rotation message, the expander title, sits ABOVE the burial strip
  3. the one-brain rule: the plate renders — it never evaluates

Run: .venv\\Scripts\\python.exe -X utf8 test_funnelplate.py
"""
import sys
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

src = (BASE / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")

# ── 1. the pure row builder, sliced and exec'd ──────────────────────
m = re.search(r"def _funnel_plate_rows\(rot_key: str, sup_rows: list, "
              r"fire_rows: list\):.*?(?=\ndef )", src, re.S)
check("1.0 the builder exists as a pure function", m is not None)
builder_src = m.group(0) if m else ""
G = {"_asset": lambda p: str(p).split("/")[0]}
try:
    exec(compile(builder_src, "builder", "exec"), G)
    rows_fn = G["_funnel_plate_rows"]
    HAVE = True
except Exception as exc:
    HAVE = False
    print(f"  (builder exec failed: {exc})")

if HAVE:
    sup = [
        {"ts": "2026-09-23T15:45:46", "pair": "UNI/USD", "gate": "tide-out",
         "quick": 54.6, "floor": 88.0, "brain": 61.0,
         "detail": "the OUT tide's price"},
        {"ts": "2026-09-23T15:47:10", "pair": "ARB/USD", "gate": "stale-move",
         "quick": 53.5, "brain": 53.5, "detail": "4h high 4h ago"},
        {"ts": "2026-09-23T15:50:18", "pair": "S/USD", "gate": "too-thin",
         "quick": 28.6, "detail": "book too thin"},
    ]
    fired = [
        {"ts": "2026-09-23T15:52:00", "pair": "KAITO/USD", "gate": "fired",
         "quick": 93.0, "bar": 89.0, "brain": 91.0, "kind": "fired",
         "detail": "champion"},
    ]
    total, html = rows_fn("12-16", sup, fired)
    check("1.1 the total counts every candidate (buried + fired)",
          total == 4, total)
    check("1.2 newest first across BOTH ledgers",
          "15:52" in html[0] and "15:45" in html[-1],
          [h[60:80] for h in html])
    check("1.3 FIRED renders green",
          "#4be38a" in html[0] and "FIRED" in html[0])
    check("1.4 tide-out renders red",
          "#ff6b5e" in html[-1])
    check("1.5 the brain read shows when consulted",
          "brain 61" in "".join(html) and "brain 54" in "".join(html))
    check("1.6 no brain line when never consulted",
          "brain" not in html[1], html[1][200:280])
    check("1.7 the bar comes from 'bar' or falls back to 'floor'",
          "vs bar 89" in html[0] and "vs bar 88" in html[-1])
    check("1.8 a sub-45 quick dims (noise floor, not a near-miss)",
          "class='dim'>quick 29" in html[1])
    check("1.9 a 45+ quick renders bright (a near-miss reads loud)",
          "class='dim'>quick 54" not in html[-1])
    big = [{"ts": f"2026-09-23T15:{i:02d}:00", "pair": f"P{i}/USD",
            "gate": "g", "quick": 50.0, "detail": "x"} for i in range(20)]
    total_b, html_b = rows_fn("12-16", big, [])
    check("1.10 the plate caps the render at 14 but keeps the honest total",
          total_b == 20 and len(html_b) == 14, (total_b, len(html_b)))
    t0, h0 = rows_fn("12-16", [], [])
    check("1.11 an empty rotation is (0, []) — the caller speaks",
          t0 == 0 and h0 == [])
    t1, h1 = rows_fn("12-16", [dict(sup[0])], [dict(fired[0])])
    check("1.12 a duplicate pair still shows both records (each read counts)",
          t1 == 2 and len(h1) == 2)

# ── 2. the render wiring ────────────────────────────────────────────
check("2.1 the renderer reads BOTH ledgers",
      '"popup_suppressions.jsonl"' in src and '"signal_snapshots.jsonl"' in src)
check("2.2 the rotation scope uses the scanner's own key",
      "rotation_key_for_hour(now.hour)" in src)
check("2.3 snapshots only count kind='fired' (burials live in the other ledger)",
      '{"fired"}' in src)
check("2.4 the MST date guards against yesterday's same-rot rows",
      ".date() != today" in src)
check("2.5 the empty rotation speaks (never a silent hole)",
      "nothing buried and nothing fired yet" in src)
check("2.6 the expander names the rotation and counts the candidates",
      "every candidate read this rotation" in src and "candidate read" in src)
check("2.7 the plate sits ABOVE the burial strip (rotation view, then history)",
      src.index("_render_funnel_plate()") < src.index("_render_suppression_strip()"))
check("2.8 the fresh stamp is on the plate",
      re.search(r"def _render_funnel_plate.*?as of \{now", src, re.S) is not None)

# ── 3. the one-brain rule ───────────────────────────────────────────
check("3.1 the builder never evaluates strategy (it renders rows only)",
      "strategy" not in builder_src and "deep_read" not in builder_src
      and "import background_scanner" not in builder_src)
check("3.2 the renderer imports the scanner ONLY for the rot key",
      re.search(r"def _render_funnel_plate\(.*?import background_scanner as "
                r"_bs1\s*\n\s*now = _now_mst\(\)", src, re.S) is not None)
check("3.3 no st.\\* widget calls inside the builder (pure, drive-testable)",
      "st." not in builder_src)

fails = [r for r in results if not r[1]]
(BASE / "_funnelplate_check.txt").write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
