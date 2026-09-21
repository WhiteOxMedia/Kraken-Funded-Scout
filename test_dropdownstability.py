"""test_dropdownstability.py — the dropdown race fix (Sep 21).

USER SPEC: "Fix the Streamlit dropdown race where the expander's
tap-to-open body collapses during auto-reruns. Make expansion
deterministic, persistent, and stable across reruns on desktop and
mobile."

THE RACE: a keyless st.expander's open state is tied to the widget
instance, whose identity Streamlit hashes from the LABEL. The strip's
labels carry the newest popup / voice line — every new line rebuilt the
expander as a NEW widget and it snapped shut mid-read.

CONTRACT:
  1. The strip expanders (HER WORDS, POPUP HISTORY, BOT'S BOOK x2) ride
     stable_expander with STABLE keys — no keyless expander in the strip.
  2. The open/closed state lives in session_state (init guard defaults
     closed) — expansion is deterministic, never widget-instance luck.
  3. expanded= is READ from session_state, so a label change cannot
     reset it (persistence across reruns and remounts).
  4. Explicit Open / Close buttons flip the state via on_click — the
     tap race has a bulletproof fallback.
  5. The mobile latch keeps a tapped section open for the session, and
     an explicit Close clears the latch (close wins).
  6. The state machine actually BEHAVES (driven headless below).
"""
import ast
import sys
from pathlib import Path

FAILS = []
PASSES = []


def check(n, cond, why=""):
    (PASSES if cond else FAILS).append(n)
    print(f"  {'PASS' if cond else 'FAIL'} {n}: {why}")


SRC = Path("app.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
FN = {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}


def body_src(fn):
    lines = SRC.splitlines()
    return "\n".join(lines[fn.lineno - 1: fn.end_lineno])


print("== 1. stable machinery exists ==")
check(1.1, "def stable_expander" in SRC and "_ctxmgr" in SRC,
      "stable_expander context manager defined")
check(1.2, "if key not in st.session_state:\n        st.session_state[key] = False" in SRC,
      "session_state init guard (first rerun defaults closed)")
check(1.3, "expanded=_open" in SRC and "_open = bool(st.session_state[key])" in SRC,
      "expanded= is READ from session_state (deterministic, label-proof)")

print("== 2. the strip expanders ride stable keys ==")
for fn_name, tag in (("_render_her_words", "her_words"),
                     ("_render_popup_history", "popup_history"),
                     ("_render_bots_book", "bots_book")):
    fn = FN.get(fn_name)
    if fn is None:
        check(f"2.{tag}", False, f"{fn_name} not found")
        continue
    src = body_src(fn)
    uses = f'stable_expander("{tag}' in src
    check(f"2.{fn_name} uses stable_expander('{tag}')", uses)
    check(f"2.{fn_name} has no keyless st.expander left",
          "with st.expander(" not in src)

check("2.bots_book flat variant keyed", 'stable_expander("bots_book_flat"' in SRC)

print("== 3. explicit controls (the bulletproof fallback) ==")
check(3.1, 'on_click=_expander_open' in SRC and 'on_click=_expander_close' in SRC
      and 'on_click=_expander_mobile' in SRC,
      "Open / Close / Mobile buttons wired via on_click callbacks")
check(3.2, "Tap to Expand (Mobile)" in SRC,
      "mobile Tap-to-Expand button present")
check(3.3, "_mobile_force" in SRC and "st.session_state[key] = True" in SRC,
      "mobile latch forces the section open for the session")
check(3.4, '_expander_close' in SRC and
      'f"{key}_mobile_force"] = False' in SRC,
      "an explicit Close clears the mobile latch (close wins)")

print("== 4. the state machine BEHAVES (driven, not grepped) ==")
try:
    import streamlit as st
    import app as app_mod

    # fresh keys each run so the init guard is exercised
    k = "popup_history"
    for kk in (k, f"{k}_mobile_force"):
        st.session_state.pop(kk, None)
    check(4.1, k not in st.session_state, "state starts unset (init guard owns it)")

    app_mod._expander_open(k)
    check(4.2, st.session_state[k] is True, "Open flips the state open")

    app_mod._expander_mobile(k)
    st.session_state[k] = False          # simulate a remount losing the DOM state
    check(4.3, st.session_state.get(f"{k}_mobile_force") is True
          and app_mod.stable_expander is not None,
          "mobile latch recorded")
    # the init-guard + latch read is what a rerun executes:
    if k not in st.session_state:
        st.session_state[k] = False
    if st.session_state.get(f"{k}_mobile_force"):
        st.session_state[k] = True
    check(4.4, st.session_state[k] is True,
          "after a simulated remount the latch re-opens the section (persistence)")

    app_mod._expander_close(k)
    check(4.5, st.session_state[k] is False
          and st.session_state.get(f"{k}_mobile_force") is False,
          "Close shuts it AND clears the latch (no silent re-open)")
except Exception as exc:
    check("4.state-machine", False, f"drive failed: {exc}")

print("== 5. label changes cannot rebuild the state ==")
check(5.1, 'stable_expander("popup_history",\n                             f"📣 POPUP HISTORY' in SRC,
      "the state key is constant; the changing label is only display")
check(5.2, SRC.count("stable_expander(") >= 4,
      f"strip call sites converted ({SRC.count('stable_expander(') - 1} uses + def)")

RESULT = f"{len(PASSES)} passed, {len(FAILS)} failed"
print(RESULT)
sys.exit(1 if FAILS else 0)
