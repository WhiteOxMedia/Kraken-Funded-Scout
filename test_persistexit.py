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

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_persist_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
