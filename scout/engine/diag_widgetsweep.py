"""diag_widgetsweep.py — THE SNAP-BACK GATE (Sep 12).

The class: a stateful Streamlit widget (checkbox, selectbox, radio,
number_input, ...) with NO stable key snaps back to its default on
every rerun — her choice dies the moment any other element on the
page reruns the script. A keyed widget whose options are COUNTED
LABELS (labels that embed live numbers) snaps too when the counts
shift under it: the session stores the string, the string stops
existing. The blessed pattern (the verdict dropdown, the sims toggle,
now the ISO-week picker): a stable literal key + a semantic
session_state entry restored init-on-missing, counts rendered via
format_func or derived, never stored.

TIER 1 (fails the suite): a stateful widget without a literal-string
`key=`, or whose key is a non-literal expression (unstable across
reruns is the point of the audit — f'{prefix}_x' prefixes are the
project's blessed dynamic form and are allowed).
TIER 2 (reported, not gated): a keyed stateful widget with a DYNAMIC
`value=` — inert after first instantiation, but stale-hazard if the
session entry is ever cleared (widget churn recreates it with a value
computed from a changed world).

Selftest proves the machine: a known-bad no-key snippet is flagged,
the blessed prefix-key and literal-key shapes pass, tier 2 catches a
dynamic value=. Production CLEAN is the suite's verdict.
Writes _widgetsweep.txt. Run: .venv\\Scripts\\python.exe -X utf8 diag_widgetsweep.py
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app.py"
OUT = ROOT / "_widgetsweep.txt"

# the fleet's gate membership — the st-shadow convention verbatim:
# production modules are GATED (a finding fails), diagnostics, scratch
# (_-prefixed) and the test harness are swept but report-only
def _is_app_candidate(path: Path) -> bool:
    if path.name.startswith("_"):
        return False
    if path.name.startswith("test_") or path.name == "conftest.py":
        return False
    if path.name.startswith("diag_"):
        return False
    return True


def _pruned(rel):
    """True when a relative path sits under a pruned dir (caches,
    venvs, hidden dirs, logs)."""
    return any(part in {"__pycache__", ".venv", "venv", ".git",
                        "node_modules", "logs"} or part.startswith(".")
               for part in rel.parts[:-1])


def discover_modules(root: Path = ROOT):
    """Every project module binding `import streamlit as st` at module
    level — the exact shape this analyzer detects (st.<widget> calls
    on the literal name st). Deterministic order, venvs/caches/hidden
    dirs pruned, unparseable files skipped."""
    found = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if _pruned(rel):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for n in tree.body:
            if isinstance(n, ast.Import) and any(
                    a.name == "streamlit"
                    and (a.asname or a.name.split(".")[0]) == "st"
                    for a in n.names):
                found.append(p)
                break
    return found

# widgets whose value persists across reruns — the snap-back class
STATEFUL = {"checkbox", "selectbox", "multiselect", "radio", "slider",
            "select_slider", "text_input", "text_area", "number_input",
            "date_input", "time_input", "file_uploader", "color_picker",
            "toggle"}
# choice widgets whose stored value must be SEMANTIC — the stored
# option is what lands in session_state and what the code branches on,
# so a decorated/derived display string stored there poisons both
# (the entry form's glyph-prefixed radios were the last of the class;
# the semantic-options + format_func idiom is the fix)
CHOICE = {"selectbox", "multiselect", "radio", "select_slider"}
# text-entry widgets where value= is a PREFILL, not a stored choice —
# the widget owns its session entry after first render. A prefill
# derived from the world (default pair, saved text) is blessed; one
# that reads session_state fights the widget's own entry and stays
# tier 2.
PREFILL = {"text_input", "text_area", "number_input"}

lines = []


def say(s=""):
    lines.append(s)


def analyze(src=None, path=None):
    """Returns (tier1, tier2, n_stateful): tier1 = [(line, widget, why)],
    tier2 = [(line, widget, key, value_src)], n_stateful = total
    stateful widget calls audited. Pass path= (or src=) to analyze any
    module; the default is app.py — and any caller relying on the old
    TARGET default keeps its behavior."""
    if src is None:
        src = (path or TARGET).read_text(encoding="utf-8")
    tree = ast.parse(src)
    t1, t2, n = [], [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "st"
                and node.func.attr in STATEFUL):
            continue
        n += 1
        kw = {k.arg: k for k in node.keywords if k.arg}
        key_kw, val_kw = kw.get("key"), kw.get("value")
        if key_kw is None:
            t1.append((node.lineno, node.func.attr,
                       "no key — the choice snaps back on every rerun"))
            continue
        if not isinstance(key_kw.value, ast.Constant) \
                or not isinstance(key_kw.value.value, str):
            # a dynamic key defeats the audit's own stability rule…
            # EXCEPT the blessed f'{prefix}_name' form: exactly two
            # parts — a Name interpolation and a constant string, in
            # either order — stable per form instance, the entry-
            # control's shipped convention
            ok = False
            v = key_kw.value
            if isinstance(v, ast.JoinedStr) and len(v.values) == 2:
                a, b = v.values

                def _name_part(p):
                    return (isinstance(p, ast.FormattedValue)
                            and isinstance(p.value, ast.Name))

                def _const_part(p):
                    return (isinstance(p, ast.Constant)
                            and isinstance(p.value, str))
                ok = ((_name_part(a) and _const_part(b))
                      or (_const_part(a) and _name_part(b)))
            if not ok:
                t1.append((node.lineno, node.func.attr,
                           f"non-literal key ({ast.unparse(v)[:40]}) — "
                           "unstable identity"))
                continue
        if val_kw is not None \
                and not isinstance(val_kw.value, ast.Constant):
            # the PREFILL blessing: text-entry value= derived from the
            # world (no session_state read) is a prefill, not a stored
            # choice fighting its own widget entry
            _reads_session = any(
                isinstance(n, ast.Attribute) and n.attr == "session_state"
                for n in ast.walk(val_kw.value))
            if not (node.func.attr in PREFILL and not _reads_session):
                t2.append((node.lineno, node.func.attr,
                           ast.unparse(key_kw.value)[:30],
                           ast.unparse(val_kw.value)[:60]))

        # ── THE STORED-DISPLAY-STRING RULE (tier 1, Sep 13): a choice
        # widget whose OPTIONS are literal decorated strings stores the
        # decoration as the value — the entry form's glyph radios
        # ("◉ SAME TRADE…", branch read back with startswith) were the
        # class; semantic options + format_func keep display where it
        # belongs. A literal element starting with a non-ASCII char is
        # decoration; plain words and ASCII-prefixed content ("$ you
        # spent" is a LABEL, money strings are content) pass.
        if node.func.attr in CHOICE:
            opts_kw = kw.get("options")
            opts = opts_kw.value if opts_kw else (
                node.args[1] if node.func.attr == "radio"
                and len(node.args) > 1 else None)
            # st.radio(label, options, …): options is the SECOND
            # positional — the label is the first
            if isinstance(opts, ast.List):
                for el in opts.elts:
                    if (isinstance(el, ast.Constant)
                            and isinstance(el.value, str)
                            and el.value and ord(el.value[0]) > 127):
                        t1.append((node.lineno, node.func.attr,
                                   f"decorated option stored as the value "
                                   f"({el.value[:24]!r}…) — semantic "
                                   "options + format_func"))
    return t1, t2, n


def analyze_all(root: Path = ROOT):
    """THE FLEET SWEEP (Sep 13, the st-shadow convention): every module
    binding `import streamlit as st` — discovery, per-file analysis,
    and gate membership so diagnostics/sandboxes stay report-only
    while production findings fail. Returns
    [(path, tier1, tier2, n_stateful)] in discovery order."""
    fleet = []
    for p in discover_modules(root):
        t1, t2, n = analyze(path=p)
        fleet.append((p, t1, t2, n))
    return fleet


BAD_SNIPPET = '''
import streamlit as st
def bad_body(edges):
    pick = st.selectbox("ISO week", ["All"] + [f"{w} {e:+.2f}" for w, e in edges], index=0)
    show = st.checkbox("show", value=False)
    mode = st.radio("entry mode", ["◉ SAME TRADE — fix my numbers",
                                   "◯ NEW TRADE — fresh clock"], index=0, key="m")
'''

BLESSED_SNIPPET = '''
import streamlit as st
def blessed_body(prefix, edges):
    pick = st.selectbox("ISO week", ["All"], index=0, key="week_pick")
    amt = st.number_input("amt", value=None, key=f"{prefix}_amt")
    on = st.toggle("on", key="on_t", value=st.session_state.get("on_t", False))
    mode = st.radio("entry mode", ["same", "new"], index=0,
                    format_func=lambda m: "◉ SAME TRADE" if m == "same"
                    else "◯ NEW TRADE", key=f"{prefix}_mode")
    pair = st.text_input("coin", value=default_pair or "", key=f"{prefix}_mp")
'''


def selftest():
    """(detail, ok): the machine must flag the no-key class, the
    stored-display-string class, pass the blessed shapes (including a
    world-derived text prefill), and catch the dynamic value=."""
    ok = True
    det = []
    t1, t2, _ = analyze(BAD_SNIPPET)
    if {w for _, w, _ in t1} != {"selectbox", "checkbox", "radio"}:
        ok = False
        det.append(f"bad snippet not fully flagged: {t1}")
    if not any("semantic" in why for _, _, why in t1):
        ok = False
        det.append(f"stored-display rule missed the decorated radio: {t1}")
    t1b, t2b, nb = analyze(BLESSED_SNIPPET)
    if t1b:
        ok = False
        det.append(f"blessed shapes tier-1 flagged: {t1b}")
    if nb != 5:
        ok = False
        det.append(f"stateful count wrong: {nb}")
    if not t2b or not any(w == "toggle" and "on_t" in k
                          for _l, w, k, _v in t2b):
        ok = False
        det.append(f"tier 2 missed the dynamic value: {t2b}")
    if any(w != "toggle" for _l, w, _k, _v in t2b):
        ok = False
        det.append(f"the world-derived prefill was not blessed: {t2b}")
    return "; ".join(det) or ("selftest clean: no-key, stored-display, "
                              "prefill, and dynamic-value rules all live"), ok


def main():
    say("=== THE SNAP-BACK SWEEP — every stateful widget keyed, backed, "
        "and storing semantic values — FLEET-WIDE ===")
    say("")

    fleet = analyze_all()
    self_det, self_ok = selftest()
    say(f"selftest: {'PASS' if self_ok else 'FAIL'} — {self_det}")
    say(f"discovered: {len(fleet)} streamlit module(s): "
        + ", ".join(p.name for p, *_ in fleet))
    _n_total = sum(n for *_x, n in fleet)
    say(f"audited: {_n_total} stateful widget calls fleet-wide")
    say("")
    gate_t1, gate_t2 = [], []
    for p, t1, t2, n in fleet:
        gated = _is_app_candidate(p)
        tag = "GATE" if gated else "report-only"
        say(f"[{tag}] {p.name}: {n} stateful widget call(s), "
            f"{len(t1)} tier-1, {len(t2)} tier-2")
        if gated:
            gate_t1 += [(p.name, ln, w, why) for ln, w, why in t1]
            gate_t2 += [(p.name, ln, w, k, v) for ln, w, k, v in t2]
        else:
            for ln, w, why in t1:
                say(f"    (report-only) line {ln}: st.{w} — {why}")
    say("")
    if gate_t1:
        say(f"TIER 1 — {len(gate_t1)} SNAP-BACK FINDING(S) in gated "
            "modules:")
        for name, ln, w, why in gate_t1:
            say(f"  {name} line {ln}: st.{w} — {why}")
    else:
        say("TIER 1: none — every stateful widget in every gated module "
            "carries a stable key and stores semantic values")
    say("")
    if gate_t2:
        say(f"tier 2 (reported, not gated) — {len(gate_t2)} dynamic "
            "value= on keyed widgets not blessed as world-derived "
            "prefills (inert after first instantiation; stale-hazard "
            "only if the session entry is cleared):")
        for name, ln, w, k, v in gate_t2:
            say(f"  {name} line {ln}: st.{w} key={k} value={v}")
    else:
        say("tier 2: none")
    say("")
    say("the blessed pattern: stable literal key + semantic "
        "session_state")
    say("restored init-on-missing; counted labels rendered, never "
        "stored")
    say("(the verdict dropdown, the sims toggle, the ISO-week "
        "picker).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    _verdict = "ALL GREEN" if (self_ok and not gate_t1) \
        else "FINDINGS PRESENT"
    print(f"\n{_verdict}")
    return 0 if (self_ok and not gate_t1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
