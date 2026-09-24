"""Test the manual RUN BRACKET click — does it run the full tournament,
error out, or bounce somewhere?"""
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(BASE / "app.py"), default_timeout=300)
    at.run()
    lines.append(f"boot exception: {at.exception}")

    # go to TOURNAMENT page first
    nav = next((b for b in at.button if b.key == "flow_TOURNAMENT"), None)
    lines.append(f"TOURNAMENT nav button: {nav is not None}")
    if nav:
        nav.click()
        at.run()

    # click RUN BRACKET
    btn = next((b for b in at.button if b.key == "run_tourney"), None)
    lines.append(f"RUN BRACKET button found: {btn is not None}")
    if btn:
        btn.click()
        at.run()
        lines.append(f"after click exception: {at.exception}")
        body = " ".join((m.value or "") for m in at.markdown)
        for probe in ["The Pick", "Champion", "won the bracket", "best pick", "FIELD", "matches played"]:
            lines.append(f"  '{probe}': {'FOUND' if probe in body else 'missing'}")
        # did the flow advance hijack us? check what page rendered
        for probe in ["PICK A COIN AND HIT DEEP SCAN", "best pick —", "DEEP SCAN"]:
            lines.append(f"  qc-page marker '{probe}': {'FOUND (ADVANCED TO QC!)' if probe in body else 'no'}")
except Exception as exc:
    lines.append(f"TEST ERROR: {exc}")

out = BASE / "bracket_test.txt"
scratch_space.report_temp('bracket_test.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
