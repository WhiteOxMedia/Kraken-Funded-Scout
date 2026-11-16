import sys
"""Test the GET OUT persistence + I RE-ENTERED form fixes (Aug 30)."""
from pathlib import Path

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


bs_src = open("background_scanner.py", encoding="utf-8").read()
app_src = open("app.py", encoding="utf-8").read()

# 1. Scanner exit behavior — UPDATED (Sep 21): GET OUT auto-stops the
#    watch (auto-close removes the row) — the row NEVER silently lingers.
check("scanner: GET OUT auto-closes the trade (pnl_net_usd)",
      "pnl_net_usd(" in bs_src.split("AUTO-CLOSE AT FIRE TIME")[1][:4000])
# The window here used to be a fixed 8000-character budget off the
# marker; the auto-close grew past it (Nov 7) and this check went red
# while the behavior it guards was intact — a budget is not a pin. The
# section is now bounded by the handler's own end, and the removal is
# pinned to the receipt gate that protects a refused close: a tombstone
# BESIDE `if _journaled:` instead of inside it is the leak class (the
# Nov 6 market-guard refusal keeps the row only because the gate is
# what grants the tombstone).
_autoclose = bs_src.split("AUTO-CLOSE AT FIRE TIME")[1].split(
    "exit condition cleared")[0]
check("scanner: GET OUT removes the watch row itself (auto-stop; the "
      "close tombstones None — merge-save deletes it, a pop would "
      "resurrect the closed row)",
      "watch[pair] = None" in _autoclose)
_jgate = _autoclose.find("if _journaled:")
_tomb = _autoclose.find("watch[pair] = None")
def _indent_at(src, pos):
    """The indentation of the LINE holding offset pos (pos can be mid-line)."""
    _ls = src.rfind("\n", 0, pos) + 1
    _le = src.find("\n", pos)
    _line = src[_ls:_le if _le != -1 else len(src)]
    return len(_line) - len(_line.lstrip())


_tomb_ind = _indent_at(_autoclose, _tomb)
_gate_ind = _indent_at(_autoclose, _jgate)
check("scanner: the tombstone sits INSIDE the receipt gate (indented under "
      "`if _journaled:`), so a refused close keeps its watch row",
      _jgate != -1 and _tomb > _jgate and _tomb_ind > _gate_ind,
      f"gate@{_jgate} ind {_gate_ind}, tombstone@{_tomb} ind {_tomb_ind}")
check("scanner: only a written ledger receipt flips _journaled (the "
      "market-guard refusal, the missing-numbers skip and the exception "
      "arm all leave it False)",
      _autoclose.count("_journaled = True") == 1 and
      _autoclose.find("_fx.write(") < _autoclose.find("_journaled = True"))
check("scanner: each refused close names its reason and keeps the row",
      all(_k in _autoclose for _k in (
          "not a real USD market — not booked",
          "close failed — watch row kept for SOLD",
          "skipped — missing entry/size/live",
          "GET OUT kept the")))
check("scanner: exit voice gated to 60s repeat",
      "_last_exit_voice.get(pair, 0)) >= 60" in bs_src)
check("scanner: exit-voice re-arm when condition clears",
      "_last_exit_voice.pop(pair, None)" in bs_src)
check("scanner: close failure keeps the row for the SOLD override",
      "the SOLD button remains the way out" in bs_src)

# 2. Console never drops a position on exit
light_section = app_src.split("def _light_check")[1].split("def _render_light_panel")[0]
check("console: no contest_watch.pop on exit (the SOLD tombstone "
      "writes None — the merge-save deletes it)",
      "contest_watch.pop(p, None)" not in light_section
      and "_cw_sold[_sp] = None" in app_src)
check("console: SOLD is the only clear",
      "flow_exit_at" in app_src.split("def render_auto_watch")[1].split("def _time_to_goal")[0])

# 3. The manual entry control — RETIRED (Sep 21, user rule: popups
#    auto-track at fire time; nothing to fix by hand). The form is gone.
check("entry control: RETIRED (the form is gone)",
      "def _render_entry_control" not in app_src)
check("entry control: no MY ENTRY references remain",
      "MY ENTRY" not in app_src)

# 4. ONE BRAIN (Sep 22, user: "the console doesn't need its own exit
#    evaluator anymore"): the light's console-side evaluator is retired —
#    GET OUT verdicts come only from the scanner's exit watch (its own
#    popup + push). The ghost-SELL class is dead at the source.
check("one brain: the light's console evaluator is retired",
      "_run_exit_logic(kraken, pair, cfg, sb)" not in light_section)
check("one brain: the red SELL NOW toast is gone",
      "SELL NOW:" not in light_section)
check("one brain: the global SELL banner is gone (no exit_alerts anywhere)",
      "exit_alerts" not in app_src)
check("one brain: the light still does the pure-price work (GOAL CALL)",
      "THE GOAL CALL" in light_section and "take_level" in light_section)
check("one brain: the light feeds the phone-free rows (no alerts dict)",
      "alerts[pair]" not in light_section)
check("one brain: manual Exit Check page survives as a diagnostic",
      "def render_exit_check" in app_src)

# 5. THE SECOND-BRAIN TRIPWIRE (Sep 22, user rule: fail if any console
#    code path ever again evaluates strategy signals FOR AN EXIT VERDICT
#    outside the manual Exit Check page). Display reads stay legal — the
#    tournament ranks candidates, quick-check renders a read — but an
#    eval that shares a function with an exit verdict, a SELL/GET OUT
#    delivery, or the light is the ghost-SELL class come back.
import re as _re
import scratch_space   # scratch policy: nothing volatile lands in the repo root


def _strip_docs_comments(src):
    src = _re.sub(r'""".*?"""', ' ', src, flags=_re.S)
    src = _re.sub(r"'''.*?'''", ' ', src, flags=_re.S)
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))


_top = [(m.start(), m.group(1)) for m in
        _re.finditer(r"^def ([A-Za-z_][A-Za-z0-9_]*)\(", app_src, _re.M)]
_spans = []
for _i, (_pos, _nm) in enumerate(_top):
    _end = _top[_i + 1][0] if _i + 1 < len(_top) else len(app_src)
    _spans.append((_nm, _strip_docs_comments(app_src[_pos:_end])))

_EVAL = _re.compile(r"evaluate_strategy\(|=\s*_eval\(")
_EXIT_MARKS = ("should_exit", '"exit"', "GET OUT", "SELL NOW")
# The manual diagnostic pages.  Exit Check computes a verdict on demand; DEEP
# SCAN (render_quick_check) renders a read and its help copy mentions GET OUT,
# which the mark scan cannot tell from a delivery.  The eval+mark matrix is a
# heuristic for "the console computes exits", so the page is exempt - and the
# shape it would take is pinned directly below instead.
_ALLOWED_EVAL_EXIT = {"render_exit_check", "render_quick_check"}

for _nm, _body in _spans:
    _has_eval = bool(_EVAL.search(_body))
    _has_exit = any(_m in _body for _m in _EXIT_MARKS)
    if _nm in _ALLOWED_EVAL_EXIT:
        continue
    if _nm == "_run_exit_logic":
        # the quarantined corpse: pinned separately below (zero callers)
        continue
    check(f"second-brain tripwire: {_nm}",
          not (_has_eval and _has_exit),
          f"eval={_has_eval} exit-marks={_has_exit}")

# the exemption above is earned: the deep scan may evaluate a strategy, it may
# never compute an exit verdict of its own
_qc_body = dict(_spans).get("render_quick_check", "")
check("second-brain tripwire: the deep scan computes no exit",
      not any(_m in _qc_body for _m in ("should_exit(", "should_exit =")),
      "the console would be deciding an exit again")

_light_src = _strip_docs_comments(
    app_src.split("def _light_check")[1].split("def render_auto_watch")[0])
check("second-brain tripwire: the light path runs no eval at all",
      not _EVAL.search(_light_src))
check("second-brain tripwire: _run_exit_logic has zero callers",
      not [n for n, b in _spans if n != "_run_exit_logic"
           and "_run_exit_logic(" in b],
      "a re-mounted corpse is the same regression by another door")
check("second-brain tripwire: the light's rows are born verdict-free",
      '"exit": False' in _light_src,
      "rows may hold state; they may never hold a verdict")

# 6. THE MIRROR TRIPWIRE (Sep 22, user rule: the scanner must compute its
#    own exits): background_scanner never READS a console-written verdict.
#    The one-brain rule killed the console computing exits; this kills the
#    mirror image — the scanner delegating its exit decision to something
#    the console (or any console code path) wrote. Reads that stay legal:
#    user DIALS (inputs a human turns) and scanner/diag-computed state.
#    Provenance, verified Sep 22: the six console_state keys are dials;
#    surething_goal_tilt.json is written by the DIAG, not the console;
#    evaluate_strategy( at the exit site is the scanner computing its own
#    verdict from price data — the behavior this tripwire PROTECTS.
bs_stripped = _strip_docs_comments(bs_src)
_bs_lines = bs_stripped.splitlines()
_cs_sites = [i for i, ln in enumerate(_bs_lines)
             if "console_state.json" in ln]
check("mirror tripwire: the scanner reads console_state at all (the "
      "surface exists to be pinned — re-verify provenance if this "
      "count moves)",
      len(_cs_sites) == 5, f"{len(_cs_sites)} sites")
_cs_keys = set()
for _i in _cs_sites:
    for _ln in _bs_lines[_i:_i + 12]:
        _cs_keys |= set(_re.findall(r'\.get\(\s*["\']([A-Za-z_]+)["\']', _ln))
_MIRROR_DIALS = {"tier", "worth_floor_frac", "worth_floor_auto",
                 "popup_cap", "climb_strictness", "voice_volume"}
_stray = _cs_keys - _MIRROR_DIALS
check("mirror tripwire: every console_state key the scanner reads is a "
      "user dial (a new key is a new decision — pin it consciously)",
      not _stray, f"stray: {sorted(_stray)}")
check("mirror tripwire: no import of the console module",
      # a real statement, not a substring: "from entry_timing import apply"
      # read as "import app" and turned this tripwire red on prose
      not _re.search(r"^\s*(?:from\s+app\s+import\b|import\s+app\b)",
                     bs_stripped, _re.M))
_MIRROR_ARTIFACTS = [a for a in ("exit_alerts", "flow_exit_at",
                                 "_run_exit_logic(") if a in bs_stripped]
check("mirror tripwire: no retired console-verdict artifact anywhere",
      not _MIRROR_ARTIFACTS, f"found: {_MIRROR_ARTIFACTS}")
_MIRROR_VERDICT_KEYS = _re.findall(
    r'\.get\(\s*["\'](exit|sell|get_out|exit_alert)["\']', bs_stripped)
check("mirror tripwire: no verdict-shaped read from any shared store "
      "(watch rows, console_state, her words)",
      not _MIRROR_VERDICT_KEYS, f"found: {_MIRROR_VERDICT_KEYS}")
# (CodeRabbit, Sep 22 — merged Sep 24): the census above only saw .get();
# a scanner change reading store["exit"] as a SUBSCRIPT sailed through
# with the read-site count unchanged. Both shapes are the same crime —
# a verdict read off a store the console wrote — so the tripwire now
# hunts the indexed form too. Proven live: the scanner carries neither
# shape today (the 6.10 census would have caught it at either key).
_MIRROR_VERDICT_SUBS = _re.findall(
    r'\[\s*["\'](exit|sell|get_out|exit_alert)["\']\s*\]', bs_stripped)
check("mirror tripwire: no verdict-shaped SUBSCRIPT read from any "
      "shared store (store[\"exit\"] evaded the .get()-only census)",
      not _MIRROR_VERDICT_SUBS, f"found: {_MIRROR_VERDICT_SUBS}")

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_persist_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed


if __name__ == "__main__":
    sys.exit(main())
