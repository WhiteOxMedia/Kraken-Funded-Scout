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
check("scanner: GET OUT removes the watch row itself (auto-stop)",
      "watch.pop(pair, None)" in bs_src.split("AUTO-CLOSE AT FIRE TIME")[1][:8000])
check("scanner: exit voice gated to 60s repeat",
      "_last_exit_voice.get(pair, 0)) >= 60" in bs_src)
check("scanner: exit-voice re-arm when condition clears",
      "_last_exit_voice.pop(pair, None)" in bs_src)
check("scanner: close failure keeps the row for the SOLD override",
      "the SOLD button remains the way out" in bs_src)

# 2. Console never drops a position on exit
light_section = app_src.split("def _light_check")[1].split("def _render_light_panel")[0]
check("console: no contest_watch.pop on exit",
      "contest_watch.pop(p, None)" not in light_section)
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
_ALLOWED_EVAL_EXIT = {"render_exit_check"}   # the manual diagnostic page

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
      "import app" not in bs_stripped and "from app import" not in bs_stripped)
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
print("DONE")
