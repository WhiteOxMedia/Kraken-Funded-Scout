"""diag_prefsmirror.py — THE PREFS-MIRROR GATE (Sep 13).

WHY THIS EXISTS: her show-sims and verdict choices mirror into
console_prefs.json so they survive CLOSING THE TAB. The mirror rests on
four structural links, each of which can silently rot under an edit to
app.py: the boot-restore must re-seed session_state from the file (a
fresh boot otherwise snaps back to the widget default), every mirrored
choice must have a _prefs_save write site (a dropped write strands her
choice in the session), the widget must carry its stable key, and
_prefs_path must read the SCOUT_PREFS_FILE override with _prefs_load
failing open (the sandbox every prefs pin depends on; a raising loader
blocks the console on its own prefs). The behavioral loop is pinned by
the booted-AppTest pins; THIS gate pins the STRUCTURE statically so a
future edit that severs any link fails the battery in one entry.

Static, AST-only — no streamlit import, no app import, no execution.
Run: .venv\\Scripts\\python.exe -X utf8 diag_prefsmirror.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app.py"

# the two prefs-backed choices and their widget keys (the _cb suffix is
# the mirror widgets' naming convention; the verdict mirror seeds the
# SEMANTIC key, the sims mirror seeds the widget key — the gate accepts
# either, the boot-restore is what must exist)
MIRRORS = {"show_flip_sims": "show_flip_sims_cb",
           "verdict_filter": "verdict_filter_cb"}
WIDGET_NAMES = {"selectbox", "checkbox", "multiselect", "radio",
                "toggle", "text_input", "number_input"}


def _sess_key(node):
    """A session_state subscript key: session_state["k"] -> "k"."""
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "session_state"):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return None


def _dict_literal_keys(node):
    """Literal string keys of a Dict literal ({**x, "k": v} -> {"k"})."""
    if not isinstance(node, ast.Dict):
        return set()
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _is_prefs_save_call(node):
    """_prefs_save(...) as a bare name or attribute (module-level
    helpers and nested callbacks both call it bare in app.py)."""
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == "_prefs_save")
            or (isinstance(f, ast.Attribute) and f.attr == "_prefs_save"))


def _analyze_tree(tree):
    """The four structural links over one parsed module tree. Returns
    (tier1, info): tier-1 findings sever a mirror link and fail the
    battery; info carries the counted evidence."""
    finds = []
    info = {"restores": 0, "saves": 0, "keyed": 0, "widgets": 0,
            "env_override": False, "load_fail_open": False}

    membership_keys = set()   # keys guarded `if "k" not in session_state`
    assigned_keys = set()     # keys written session_state["k"] = ...
    pref_reads = set()        # names read via _prefs_load().get("k")
    widget_keys = set()       # every keyed widget call
    save_keys = set()         # every literal key written via _prefs_save
    fn_nodes = {}             # def name -> node (for the helper pins)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            fn_nodes[node.name] = node
        # `if "k" not in st.session_state:` — the init-on-missing guard
        if (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.NotIn)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and node.comparators
                and isinstance(node.comparators[0], ast.Attribute)
                and node.comparators[0].attr == "session_state"):
            membership_keys.add(node.left.value)
        # st.session_state["k"] = ...
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            for t in targets:
                k = _sess_key(t)
                if k:
                    assigned_keys.add(k)
        # restored = _prefs_load().get("k")
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "_prefs_load"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            pref_reads.add(node.args[0].value)
        # st.<widget>(..., key="...")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in WIDGET_NAMES \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "st":
            info["widgets"] += 1
            for kw in node.keywords:
                if kw.arg == "key" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    widget_keys.add(kw.value.value)
                    info["keyed"] += 1
        # _prefs_save({**_prefs_load(), "pref": ...})
        if isinstance(node, ast.Call) and _is_prefs_save_call(node):
            for a in node.args:
                save_keys |= _dict_literal_keys(a)
            for kw in node.keywords:
                save_keys |= _dict_literal_keys(kw.value)

    # LINK 1 (boot-restore): each mirror re-seeds session_state from
    # the file behind an init-on-missing guard — the semantic key or
    # the widget key may be the guarded one, but a _prefs_load read of
    # the pref must exist to restore from
    for pref, key in MIRRORS.items():
        guarded = (pref in membership_keys) or (key in membership_keys) \
            or (key in assigned_keys and pref in assigned_keys)
        if not (guarded and pref in pref_reads):
            finds.append(("tier1", "restore", pref,
                          f"no init-on-missing boot-restore for '{pref}' "
                          "(guard + _prefs_load read) — a fresh boot "
                          "snaps back to the widget default"))
    info["restores"] = sum(
        1 for pref, key in MIRRORS.items()
        if ((pref in membership_keys) or (key in membership_keys)
            or (key in assigned_keys and pref in assigned_keys))
        and pref in pref_reads)

    # LINK 2 (tab-survives write): every mirrored pref name has a
    # _prefs_save call whose dict literal carries the key
    for pref in MIRRORS:
        if pref not in save_keys:
            finds.append(("tier1", "save", pref,
                          f"no _prefs_save call writes '{pref}' — her "
                          "choice dies with the session"))
    info["saves"] = sum(1 for k in MIRRORS if k in save_keys)

    # LINK 3 (stable key): the mirrored widgets themselves carry theirs
    for pref, key in MIRRORS.items():
        if key not in widget_keys:
            finds.append(("tier1", "key", key,
                          f"no st widget call carries key='{key}' — the "
                          f"{pref} mirror has no widget to mirror"))

    # LINK 4a: _prefs_path reads the SCOUT_PREFS_FILE override — the
    # sandbox every prefs pin (and this analyzer's own testability)
    # depends on; an env-blind reader means tests touch production
    fn = fn_nodes.get("_prefs_path")
    if fn is None:
        finds.append(("tier1", "helper", "_prefs_path",
                      "the prefs path helper is gone"))
    else:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "environ"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "SCOUT_PREFS_FILE"):
                info["env_override"] = True
        if not info["env_override"]:
            finds.append(("tier1", "helper", "_prefs_path",
                          "no longer reads SCOUT_PREFS_FILE — the test "
                          "sandbox override is severed"))

    # LINK 4b: _prefs_load fails OPEN (absent or corrupt file reads as
    # defaults) — the console must never block on its own prefs
    fn = fn_nodes.get("_prefs_load")
    if fn is None:
        finds.append(("tier1", "helper", "_prefs_load",
                      "the prefs loader is gone"))
    else:
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) \
                            and isinstance(sub.value, ast.Dict):
                        info["load_fail_open"] = True
        if not info["load_fail_open"]:
            finds.append(("tier1", "helper", "_prefs_load",
                          "no fail-open return in its except path — a "
                          "corrupt prefs file would raise into the UI"))

    return finds, info


def analyze(path=None):
    """Gate app.py (or an explicit file). Returns (tier1, info)."""
    p = Path(path) if path else TARGET
    tree = ast.parse(p.read_text(encoding="utf-8"))
    return _analyze_tree(tree)


# ── selftest fixtures: synthetic modules in app.py's real idioms ────
GOOD = '''
import streamlit as st
def _prefs_path():
    import os
    env = os.environ.get("SCOUT_PREFS_FILE")
    return (Path(env) if env else Path(__file__).parent / "p.json")
def _prefs_load():
    try:
        return json.loads(_prefs_path().read_text()) or {}
    except Exception:
        return {}
def _prefs_save(prefs):
    _prefs_path().write_text(json.dumps(prefs))
def render():
    if "show_flip_sims_cb" not in st.session_state:
        restored = _prefs_load().get("show_flip_sims")
        st.session_state["show_flip_sims_cb"] = bool(restored)
        st.session_state["show_flip_sims"] = bool(restored)
    def _sims_save():
        val = bool(st.session_state["show_flip_sims_cb"])
        st.session_state["show_flip_sims"] = val
        _prefs_save({**_prefs_load(), "show_flip_sims": val})
    if "verdict_filter" not in st.session_state:
        restored = _prefs_load().get("verdict_filter")
        st.session_state["verdict_filter"] = restored
    def _vf_save():
        val = st.session_state.get("verdict_filter_cb")
        st.session_state["verdict_filter"] = val
        _prefs_save({**_prefs_load(), "verdict_filter": val})
    st.checkbox("show simulated worked examples",
                key="show_flip_sims_cb", on_change=_sims_save)
    st.selectbox("verdict", ["PAYING", "COSTING", "pending"],
                 key="verdict_filter_cb", on_change=_vf_save)
'''

# sever exactly one link per variant
BAD_RESTORE = GOOD.replace('''    if "verdict_filter" not in st.session_state:
        restored = _prefs_load().get("verdict_filter")
        st.session_state["verdict_filter"] = restored
''', '')
BAD_SAVE = GOOD.replace('''    def _vf_save():
        val = st.session_state.get("verdict_filter_cb")
        st.session_state["verdict_filter"] = val
        _prefs_save({**_prefs_load(), "verdict_filter": val})
''', '')
BAD_KEY = GOOD.replace('key="show_flip_sims_cb", on_change=_sims_save)',
                       'on_change=_sims_save)')
BAD_ENV = GOOD.replace('os.environ.get("SCOUT_PREFS_FILE")',
                       'os.environ.get("OTHER_VAR")')
BAD_RAISE = GOOD.replace('        return {}', '        raise')


def selftest():
    """Every rule fires on its severed variant; GOOD stays clean."""
    try:
        bads = []
        for name, src, tag in (("restore", BAD_RESTORE, "verdict_filter"),
                               ("save", BAD_SAVE, "verdict_filter"),
                               ("key", BAD_KEY, "show_flip_sims_cb"),
                               ("env", BAD_ENV, "_prefs_path"),
                               ("raise", BAD_RAISE, "_prefs_load")):
            finds, _ = _analyze_tree(ast.parse(src))
            if not any(tag == f[2] or tag in f[2] for f in finds):
                bads.append(f"{name} variant not caught")
        good_finds, good_info = _analyze_tree(ast.parse(GOOD))
        if good_finds:
            bads.append(f"clean fixture flagged: {good_finds}")
        if good_info["restores"] != 2 or good_info["saves"] != 2 \
                or good_info["keyed"] != 2 \
                or good_info.get("env_override") is not True \
                or good_info.get("load_fail_open") is not True:
            bads.append(f"clean fixture info wrong: {good_info}")
        detail = "; ".join(bads) if bads else \
            "all 5 severed variants caught, clean fixture passes"
        return detail, not bads
    except Exception as exc:
        return f"selftest raised: {exc!r}", False


def main():
    finds, info = analyze()
    print("=== PREFS-MIRROR GATE — app.py structural links ===")
    print(f"  boot-restores present: {info['restores']}/2")
    print(f"  _prefs_save pref-key sites: {info['saves']}/2")
    print(f"  keyed st widgets: {info['keyed']}/{info['widgets']}")
    print(f"  SCOUT_PREFS_FILE override wired: {info['env_override']}")
    print(f"  _prefs_load fails open: {info['load_fail_open']}")
    for kind, tag, name, why in finds:
        print(f"  [{kind}] {tag}:{name} — {why}")
    if not finds:
        print("  CLEAN: the prefs mirror's four structural links hold")
    print()
    return 1 if finds else 0


if __name__ == "__main__":
    sys.exit(main())
