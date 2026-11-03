"""CACHE-STALENESS SWEEP v2 — the sims-toggle class, fleet-wide (Sep 13).

The disease: st.cache_data freezes a function's OUTPUT behind its
hashed args. Anything the body reads that is NOT an argument —
st.session_state first among equals — is frozen for the whole ttl.
That is exactly how the trail's sims toggle went dead on the MASTER
ledger chart: the plate was cached, the toggle lived in
session_state, and run 1's pre-flip HTML kept being served. The heal
threaded the choice in as a parameter: sims_hidden is part of the
CACHE KEY now (_tidebar_verdict, the proven exemplar).

THE SAFE PATTERN: every off-arg input becomes an explicit parameter,
threaded from the call site — then the cache key carries it and a
change recomputes instead of serving stale output.

v2 sweeps the CONSOLE'S WHOLE IMPORT CLOSURE, not just app.py. The
class does not respect module boundaries: a cached plate in app.py
that calls db.helper() freezes whatever helper reads just as dead —
the staleness lives in the PLATE, wherever the read happens. Discovery
is pure AST (nothing is ever imported), following Import / ImportFrom
nodes — including function-local imports, which is how the console
pulls db, signal_bridge, strategy and contest_account — to every local
module (a name is local iff <name>.py or <name>/__init__.py exists).

The call graph is module-merged:
  - Name calls resolve to same-module defs first, then to names the
    module pulled in with `from mod import name` (cross-module edge).
  - Attribute calls alias.attr(...) resolve through `import mod` /
    `import mod as alias` bindings (cross-module edge).
  - Unresolved calls are ignored, exactly as v1 ignored them.

Flags, TRANSITIVELY through the merged graph (fragments in ANY module
are exempt — a fragment re-executes on its interval):
  TIER 1  st.session_state reachable from a cached body — fails.
  TIER 2  wall-clock / randomness reads (datetime.now/utcnow/today,
          time.time, random.*) — frozen "as of" stamps; cosmetic,
          reported, not gated.

Read-only. Writes _cachesweep.txt.
Run: .venv/Scripts/python.exe -X utf8 diag_cachesweep.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENTRY = "app"
OUT = ROOT / "_cachesweep.txt"

CACHE_DECORATORS = {"cache_data", "cache_resource"}
TIME_ATTRS = {"now", "utcnow", "today", "time", "perf_counter",
              "monotonic"}
TIME_NAMES = {"datetime", "date", "time", "random"}

# ── THE FORGIVEN REGISTRY (Sep 13, fleet pass) ──────────────────
# The fleet's cross-module graph surfaced tier-2 reads v1 could never
# see. Each is classified ONCE, here, with its reason — and every
# forgiveness prints in the report, so nothing hides silently. Only
# (callee, attr) pairs verified in context belong; anything new still
# reports and still names itself.
#   expiry math — the helper is its own mtime/scale-in cache: the read
#                 IS the invalidation logic, always fresh.
#   ts field    — stamps data the plate just fetched (provenance, not
#                 staleness).
#   dead field  — lands in record fields no cached plate consumes.
FORGIVEN_TIER2 = {
    ("volatility_engine.load_rotation_thresholds", "time.time"): (
        "expiry math",
        "the read drives _THRESH_CACHE mtime invalidation — the "
        "helper IS a cache; its clock must be live"),
    ("execution_tiers.measure_book", "time.time"): (
        "ts field",
        "stamps the book snapshot fetched in the same call — "
        "provenance on live data, not a frozen stamp"),
    ("execution_tiers._now", "datetime.now"): (
        "expiry math",
        "scale-in slice expiry (until/count comparisons) and the "
        "snapshot's own age/rem fields — cache-management reads"),
    ("contest_account.load_state", "datetime.now"): (
        "dead field",
        "the clock lands in fallback records' contest_id/updated_at "
        "fields, which no cached plate reads"),
}


def _local_module(name):
    """Path of a local module, or None for stdlib/site-packages."""
    p = ROOT / f"{name}.py"
    if p.exists():
        return p
    pkg = ROOT / name / "__init__.py"
    if pkg.exists():
        return pkg
    return None


def discover(entry=ENTRY):
    """The console's import closure as {module: Path} — pure AST on
    files, nothing imported, cycles safe (a seen-set)."""
    seen, stack = {}, [entry]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        path = _local_module(mod)
        if path is None:
            continue
        seen[mod] = path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
        except SyntaxError:
            continue          # unparseable local module: report, don't die
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    top = a.name.split(".")[0]
                    if _local_module(top):
                        stack.append(top)
            elif isinstance(n, ast.ImportFrom):
                if n.module and n.level == 0:
                    top = n.module.split(".")[0]
                    if _local_module(top):
                        stack.append(top)
    return seen


def _deco_pair(dec):
    f = dec.func if isinstance(dec, ast.Call) else dec
    if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
        return (f.value.id, f.attr)
    return None


def _is_cache_decorator(dec):
    return _deco_pair(dec) == ("st", "cache_data") or \
        _deco_pair(dec) == ("st", "cache_resource")


def _is_fragment_decorator(dec):
    return _deco_pair(dec) == ("st", "fragment")


def _session_state_uses(fn):
    """Line numbers of every st.session_state access in fn's OWN body
    (nested defs are separate scopes with their own wiring)."""
    out = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.Lambda)):
            continue          # another scope's territory
        if (isinstance(n, ast.Attribute) and n.attr == "session_state"
                and isinstance(n.value, ast.Name) and n.value.id == "st"):
            out.append(n.lineno)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _time_uses(fn):
    """(what, lineno) for wall-clock/random reads in fn's own body."""
    out = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.Lambda)):
            continue
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            a = n.func.attr
            # the receiver may be a chain: datetime.datetime.now(),
            # dt.datetime.utcnow() — collect every name down the chain
            v, names = n.func.value, []
            while isinstance(v, ast.Attribute):
                names.append(v.attr)
                v = v.value
            if isinstance(v, ast.Name):
                names.append(v.id)
            if (any(x in TIME_NAMES for x in names)
                    and a in TIME_ATTRS):
                out.append((f"{'.'.join(reversed(names))}.{a}()",
                            n.lineno))
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            if n.value.id == "random" and n.attr not in ("seed",):
                out.append((f"random.{n.attr}", n.lineno))
        stack.extend(ast.iter_child_nodes(n))
    return out


def analyze_modules(sources):
    """sources: {module_name: source_text}. Returns
    (tier1, tier2, fns, info):
      tier1/tier2 — (cached_qname, detail, lineno) tuples, qnames
                    module-qualified ('app._scalp_headline').
      fns         — {qualified_name: FunctionDef} across ALL modules.
      info        — fleet metadata: per-module fn/cached/fragment/
                    cross-edge counts + the module list.
    """
    fns = {}            # qname -> node
    cached, fragments = [], set()
    calls = {}          # qname -> set of target qnames
    from_names = {}     # per module: local name -> ("mod", orig_name)
    mod_alias = {}      # per module: alias -> module name
    per_mod = {}
    trees = {}          # mod -> parsed tree (phase 2 needs ALL defs)

    # ── PHASE 1: parse every module, collect every def first. Edges
    # are built only in phase 2 — building them per-module inline made
    # every forward cross-edge silently vanish (app before db meant
    # db.helper was "not a function" when app's edges resolved).
    for mod, src in sources.items():
        try:
            tree = ast.parse(src, filename=f"{mod}.py")
        except SyntaxError:
            per_mod[mod] = {"fns": 0, "cached": 0, "fragments": 0,
                            "cross_edges": 0, "error": "unparseable"}
            continue
        trees[mod] = tree
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mod_alias.setdefault(
                        (mod, a.asname or a.name.split(".")[0]),
                        a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if n_ok := (node.module or ""):
                    for a in node.names:
                        from_names.setdefault(
                            (mod, a.asname or a.name), (n_ok, a.name))
            elif isinstance(node, ast.FunctionDef):
                qn = f"{mod}.{node.name}"
                while qn in fns:            # nested name collision
                    qn += "~"
                fns[qn] = node
                decs = node.decorator_list
                if any(_is_cache_decorator(d) for d in decs):
                    cached.append(qn)
                if any(_is_fragment_decorator(d) for d in decs):
                    fragments.add(qn)

    for mod, tree in trees.items():
        fmods = [qn for qn in fns
                 if qn.split(".")[0] == mod and qn.count(".") == 1]
        fcached = [q for q in cached if q in fmods]
        ffrags = {q for q in fragments if q in fmods}
        per_mod[mod] = {"fns": len(fmods), "cached": len(fcached),
                        "fragments": len(ffrags), "cross_edges": 0}

        for qn in fmods:
            fn = fns[qn]
            edges = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                if isinstance(n.func, ast.Name):
                    nm = n.func.id
                    if f"{mod}.{nm}" in fns:
                        edges.add(f"{mod}.{nm}")       # same-module def
                    elif (mod, nm) in from_names:
                        fm, orig = from_names[(mod, nm)]
                        if f"{fm}.{orig}" in fns:
                            edges.add(f"{fm}.{orig}")  # from-import edge
                elif isinstance(n.func, ast.Attribute) and \
                        isinstance(n.func.value, ast.Name):
                    alias = n.func.value.id
                    target_mod = mod_alias.get((mod, alias))
                    if target_mod:
                        tq = f"{target_mod}.{n.func.attr}"
                        if tq in fns:
                            edges.add(tq)              # alias.attr edge
            calls[qn] = edges
            x = len([e for e in edges if not e.startswith(mod + ".")])
            per_mod[mod]["cross_edges"] += x

    # modules with zero fns still need a calls entry for reachability
    for qn in fns:
        calls.setdefault(qn, set())

    def reachable(qn):
        seen, stack = set(), [c for c in calls.get(qn, ())
                              if c not in fragments]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            stack.extend(x for x in calls.get(c, ())
                         if x not in fragments and x not in seen)
        return seen

    tier1, tier2 = [], []
    forgiven = []
    for qn in sorted(cached):
        for r in sorted(reachable(qn)):
            for ln in _session_state_uses(fns[r]):
                tier1.append((qn, f"reaches session_state via {r}()",
                              ln))
            for what, ln in _time_uses(fns[r]):
                # `what` looks like 'datetime.datetime.now()' — the
                # registry keys on 'datetime.now' / 'time.time'
                base = what[:-2] if what.endswith("()") else what
                leaf = base.split(".")[-1]
                head = base.rsplit(".", 1)[0] if "." in base else base
                rule = FORGIVEN_TIER2.get(
                    (r, f"{head}.{leaf}" if head else leaf))
                if rule is not None:
                    forgiven.append((qn, r, what, ln, rule))
                else:
                    tier2.append((qn, f"reaches {what} via {r}()", ln))

    # ── TIER 3 (Sep 13, later): cached→cached call edges — the NESTED
    #    STALENESS CHAIN. The inner cache holds its own ttl, so the
    #    outer plate can serve data up to ttl_outer + ttl_inner old
    #    while telling one staleness story; the plate clocks pin made
    #    every stamp fresh, but composition itself was invisible to the
    #    sweep. Direct edges only: chains through non-cached glue are
    #    already covered leg-by-leg (A→glue→B yields the same pair at
    #    each cached level as A→B would). Report-only — composition is
    #    legal; the hazard must be VISIBLE, the ttl ladder readable.
    cached_set = set(cached)
    nested = sorted({(qn, c) for qn in cached
                     for c in calls.get(qn, ()) if c in cached_set})

    info = {"modules": sorted(per_mod), "per_mod": per_mod,
            "entry": None, "fns_total": len(fns),
            "cached_total": len(cached), "forgiven": forgiven,
            "nested": nested}
    return tier1, tier2, fns, info


def analyze_text(src):
    """Single-module convenience: one source under the name 'app'.
    Kept so one-module snippets (selftests, quick probes) work."""
    t1, t2, fns, _info = analyze_modules({"app": src})
    return t1, t2, fns


def analyze(entry=ENTRY):
    mods = discover(entry)
    sources = {}
    for m, path in mods.items():
        try:
            sources[m] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    t1, t2, fns, info = analyze_modules(sources)
    info["entry"] = entry
    return t1, t2, fns, info


TWO_MODULE_SNIPPET = {
    "app": '''
import streamlit as st
import datetime
from db import helper as db_helper   # from-import: a cross edge
import db as dbmod                   # alias: an attribute cross edge

@st.cache_data(ttl=60)
def plate(secret):
    a = db_helper(secret)            # TIER 1 via db.helper
    b = dbmod.helper2()              # TIER 1 via db.helper2 (attr edge)
    return f"{a}:{b}"

@st.cache_data(ttl=60)
def clean(rows):
    return "".join(sorted(rows))     # every input is an argument
''',
    "db": '''
import streamlit as st

def helper(secret):
    return st.session_state["mode"]  # frozen for plate's ttl

@st.cache_data(ttl=5)
def helper2():
    import datetime
    x = st.session_state["tick"]     # frozen for plate's ttl
    return f"{x}:{datetime.datetime.now()}"   # tier 2 path
# cached itself (Sep 13, tier 3): plate's call to it is a NESTED
# STALENESS CHAIN — two ttls deep, visible in the report

@st.fragment(run_every=30_000)
def live():
    return st.session_state["m"]     # fragment: fresh, EXCLUDED
''',
}


def selftest():
    """The two-module fleet: both cross-module paths (from-import and
    alias attribute) must flag on the cached plate; the fragment stays
    exempt; the clean arg-only body stays quiet; the wall-clock path
    through the alias edge reports as tier 2; and the cached→cached
    edge (plate→helper2, two ttls deep) reports as tier 3."""
    t1, t2, fns, info = analyze_modules(TWO_MODULE_SNIPPET)
    detail = ([f"tier1: {qn} {why} (line {ln})" for qn, why, ln in t1]
              + [f"tier2: {qn} {why} (line {ln})" for qn, why, ln in t2]
              + [f"tier3: {a} -> {b}" for a, b in info["nested"]]
              + [f"fleet: {info['modules']} fns={info['fns_total']}"])
    ok = (
        len(t1) == 2
        and all(qn == "app.plate" for qn, _, _ in t1)
        and any("db.helper()" in why for _, why, _ in t1)
        and any("db.helper2()" in why for _, why, _ in t1)
        and not any("live" in qn for qn, _, _ in t1)
        and not any("clean" in qn for qn, _, _ in t1)
        and len(t2) == 1
        and any("now()" in why and "db.helper2" in why
                for _, why, _ in t2)
        and ("app.plate", "db.helper2") in info["nested"]
        and all(a == "app.plate" for a, _b in info["nested"])
        and set(info["modules"]) == {"app", "db"}
        and info["per_mod"]["app"]["cross_edges"] >= 2
    )
    return detail, ok


def main():
    t1, t2, fns, info = analyze()
    nested = info["nested"]
    st_detail, st_ok = selftest()

    lines = ["CACHE-STALENESS SWEEP v2 — the console's import closure "
             "(the sims-toggle class, fleet-wide)",
             f"modules discovered from '{ENTRY}': {len(info['modules'])} "
             f"(fns {info['fns_total']}, cached "
             f"{info['cached_total']})", ""]
    lines.append("FLEET (module: fns / cached / fragments / "
                 "cross-module edges):")
    for m in info["modules"]:
        pm = info["per_mod"][m]
        err = f"  [!] {pm.get('error')}" if pm.get("error") else ""
        lines.append(f"  {m}: {pm['fns']} / {pm['cached']} / "
                     f"{pm['fragments']} / {pm['cross_edges']}{err}")
    lines.append("")
    lines.append("SELFTEST (the two-module hermetic fleet):")
    lines.extend("  " + s for s in st_detail)
    lines.append("")
    if t1:
        lines.append(f"TIER 1 — session_state reachable from cached "
                     f"bodies, across modules ({len(t1)}):")
        for qn, why, ln in sorted(t1, key=lambda f: f[0]):
            lines.append(f"  {qn} <- {why} (line {ln})")
    else:
        lines.append("TIER 1: none — no cached body in the fleet "
                     "freezes session_state, transitively across "
                     "module boundaries")
    if t2:
        lines.append(f"TIER 2 — frozen wall-clock/random stamps "
                     f"({len(t2)}):")
        for qn, why, ln in sorted(t2, key=lambda f: f[0]):
            lines.append(f"  {qn} <- {why} (line {ln})")
    else:
        lines.append("TIER 2: none")
    if nested:
        lines.append(f"TIER 3 — NESTED STALENESS CHAINS, cached calling "
                     f"cached ({len(nested)}) — report-only: the inner "
                     f"ttl stacks on the outer's, so read the ladder "
                     f"before trusting a plate's freshness story:")
        for a, b in nested:
            lines.append(f"  {a} -> {b}")
    else:
        lines.append("TIER 3: none — no cached body calls another "
                     "cached body")
    if info["forgiven"]:
        lines.append("")
        lines.append(f"FORGIVEN tier-2 reads — classified in "
                     f"FORGIVEN_TIER2 with reasons ({len(info['forgiven'])}):")
        for qn, r, what, ln, rule in sorted(info["forgiven"]):
            kind, why = rule
            lines.append(f"  {qn} <- {what} via {r}() (line {ln}) "
                         f"[{kind}: {why}]")
    lines.append("")
    verdict = ("FLEET CLEAN — every cached plate's inputs ride its "
               "cache key, across every module the console imports"
               if not t1 else
               f"{len({q for q, _, _ in t1})} cached fn(s) freeze "
               f"session_state — thread them in as parameters "
               f"(the _tidebar_verdict sims_hidden pattern)")
    lines.append(f"VERDICT: {verdict}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if (st_ok and not t1) else 1


if __name__ == "__main__":
    sys.exit(main())
