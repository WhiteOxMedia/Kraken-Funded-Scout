"""Test THE TOGGLE GUARD (Sep 21): the console's switches must never lose
their value= seeding from the persisted store (console_state.json).

The bug this guards (fixed 07:49 Sep 21 in 6878832, silently reverted by
the fragment-seconds commit c1f5900 at 08:11 the same day, re-restored
Sep 21): the old init-on-missing shape — `if "k" not in st.session_state:
st.session_state[k] = ...` before a keyed widget — seeded a SHADOW key the
widget never reads. Streamlit renders a keyed widget from its OWN
session_state entry (absent → False for a toggle), so every fresh browser
session drew the switches OFF; the very next save-on-change line then
wrote that False over the persisted ON. The 09:21 console load flipped
watch_on to false on disk with nobody touching it — and the AUTO-WATCH
branch going dark took 💬 HER WORDS and the ✔ SOLD buttons with it while
BCH/ARB sat open.

So the contract, enforced shape-by-shape on app.py source:
  1. every st.toggle with a key= carries value=bool(<persisted>...) —
     the widget seeds itself from the store at session start
  2. no code writes a toggle's OWN session key by hand (shadow or not —
     a write below the widget would fight the user mid-session)
  3. the init-on-missing shape is banned app-wide (it hid the bug once;
     the widget-policies warn is a Streamlit warning, not a gate)
  4. nothing hardcodes False for a persisted toggle key — an OFF default
     is a product decision, made in _load_console_state, not in source

Probes are string-level on app.py (the suites' convention — Streamlit
can't run headless here) and name the three keys explicitly so a renamed
toggle fails loudly instead of silently escaping the guard.

Run: python test_toggleguard.py   (exit 1 on any violation)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


app = Path("app.py").read_text(encoding="utf-8")

# ── the three persisted toggles (update here AND in TOGGLES below when
#    a console switch is added — a new one without value= must fail) ──
TOGGLES = [
    # (label in the UI, widget key, persisted store key)
    ("AUTO-WATCH", "watch_toggle", "watch_on"),
    ("GOAL CALL", "goal_toggle", "goal_on"),
    ("HER VOICE", "voice_toggle", "voice_on"),
]
_widget_keys = [k for _, k, _ in TOGGLES]
_persist_keys = [k for _, _, k in TOGGLES]


def _call(src: str, start: int) -> str:
    """The full st.toggle( ... ) call opening at `start`, parens matched —
    a widget call spans lines; the key=/value= live past the first."""
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    return src[start:start + 400]  # unbalanced — probe fails downstream


# ── 1. every toggle carries value=bool(<persisted>...) ────────────────
toggle_calls = []  # (key, call_source)
for m in re.finditer(r"st\.toggle\(", app):
    call = _call(app, m.start())
    km = re.search(r'key="([a-z_]+)"', call)
    toggle_calls.append((km.group(1) if km else None, call))

found_keys = sorted({k for k, _ in toggle_calls if k})
check("the three console toggles all exist in app.py",
      all(wk in found_keys for wk in _widget_keys),
      f"found keys: {found_keys}")

for label, wkey, pkey in TOGGLES:
    call = next((c for k, c in toggle_calls if k == wkey), None)
    if call is None:
        check(f"{label}: toggle present", False, f'key="{wkey}" not found')
        continue
    has_value = re.search(r"\bvalue\s*=", call) is not None
    seeds_from_store = re.search(
        rf"value\s*=\s*bool\(\s*_cs\.get\(\s*\"{pkey}\"", call) is not None
    check(f"{label}: st.toggle seeds value= from the persisted store "
          f"(console_state.json.{pkey})",
          has_value and seeds_from_store,
          "missing value=" if not has_value else
          "value= does not seed from the store (hardcoded or wrong key)")

# ── 2. nobody hand-writes a toggle's OWN session key ──────────────────
#    Streamlit widget-policies: the widget owns its key. A write below
#    the widget either echoes it (useless) or fights the user's clicks
#    mid-session. The 6777 echo wrote watch_on=False into session state
#    every off-rerun — the same snap the widget itself had just drawn.
for _, wkey, pkey in TOGGLES:
    writers = [ln for ln in re.findall(
        rf'st\.session_state\["{pkey}"\]\s*=\s*[^=\n]+', app)]
    check(f"{pkey}: no hand-written session_state writes of the toggle's "
          f"own key", not writers, writers)

# ── 3. the init-on-missing shape is banned app-wide ───────────────────
#    `if "k" not in st.session_state:` immediately before a keyed widget
#    seeds a shadow the widget never reads — THE snap-to-OFF shape.
#    Legit uses (first_run detection at 6145, one-time seeding of
#    NON-widget state like watch_interval_s at 6461) don't sit next to
#    a keyed widget and must keep passing.
shadow_pairs = []
for m in re.finditer(
        r'if\s+"([a-z_0-9]+)"\s+not\s+in\s+st\.session_state\s*:\s*\n'
        r'\s*st\.session_state\["\1"\]\s*=[^\n]+\n', app):
    tail = app[m.end():m.end() + 250]
    wm = re.search(r'(st\.\w+)\(\s*(?:[^)]*?)?key="([a-z_]+)"', tail)
    if wm and wm.group(2) == m.group(1):
        shadow_pairs.append((m.group(1), wm.group(1)))
check("no init-on-missing session seeding directly above a keyed widget "
      "(the snap-to-OFF shape, banned app-wide)",
      not shadow_pairs, shadow_pairs)

# ── 4. nothing hardcodes False for a persisted toggle key ─────────────
#    An OFF default is a product decision — it belongs in
#    _load_console_state, never as a literal in source.
hardcoded = []
for pkey in _persist_keys:
    for m in re.finditer(rf'{pkey}"\s*:\s*False', app):
        ln = app[:m.start()].count("\n") + 1
        hardcoded.append((pkey, ln))
check("no hardcoded 'False' seeds for the persisted toggle keys",
      not hardcoded, hardcoded)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_toggleguard_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
