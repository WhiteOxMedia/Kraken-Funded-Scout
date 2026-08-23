"""AppTest smoke: app boots, dopamine plates render."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(BASE / "app.py"), default_timeout=120)
    at.run()
    lines.append(f"exception: {at.exception}")
    body = " ".join((m.value or "") for m in at.markdown)
    for probe in ["KRAKEN SCOUT", "TODAY", "NOW:", "Win Feed", "Today's Score", "New here"]:
        lines.append(f"{probe}: {'FOUND' if probe in body else 'missing'}")
except Exception as exc:
    lines.append(f"APPTEST FAILED: {exc}")

out = BASE / "apptest_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
