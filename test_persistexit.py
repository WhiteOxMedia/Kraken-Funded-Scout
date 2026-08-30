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

# 1. Scanner never drops a position on exit
check("scanner: no watch.pop on exit",
      "watch.pop(p, None)" not in bs_src.split("def contest_light")[1].split("def ")[0])
check("scanner: exit voice gated to 60s repeat",
      "_last_exit_voice.get(pair, 0)) >= 60" in bs_src)
check("scanner: exit-voice re-arm when condition clears",
      "_last_exit_voice.pop(pair, None)" in bs_src)
check("scanner: 'stays tracked until SOLD' logged",
      "stays tracked until SOLD" in bs_src)

# 2. Console never drops a position on exit
light_section = app_src.split("def _light_check")[1].split("def _render_light_panel")[0]
check("console: no contest_watch.pop on exit",
      "contest_watch.pop(p, None)" not in light_section)
check("console: SOLD is the only clear",
      "flow_exit_at" in app_src.split("def render_auto_watch")[1].split("def _time_to_goal")[0])

# 3. I RE-ENTERED is a form (no reruns mid-typing)
reenter = app_src.split("def _render_reenter_control")[1].split("\ndef ")[0]
check("re-entered: wrapped in st.form", 'st.form("reenter_form"' in reenter)
check("re-entered: form submit button (not st.button)",
      "form_submit_button" in reenter and 'key="re_btn"' not in reenter)
check("re-entered: both inputs inside the form",
      reenter.index("st.form") < reenter.index("re_spent") < reenter.index("form_submit_button"))

# 4. The red SELL NOW toast still fires every cycle while exit is true
check("console: SELL NOW toast persists", "SELL NOW" in light_section)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_persist_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
