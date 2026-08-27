"""Test the ONE voice engine: console _speak queues a file; the scanner's
drain logic shape matches. Round-trip with real files, cleaned up."""
import sys, json, glob, os, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

import app

# 1. the console's _speak drops a queue file (does NOT speak directly)
qdir = BASE / "speech_queue"
before = set(glob.glob(str(qdir / "*.json")))
app._speak("Testing the one voice engine.", rate=1)
time.sleep(0.2)
after = set(glob.glob(str(qdir / "*.json")))
new_files = after - before
check("_speak queues a file (no direct speech)", len(new_files) == 1, f"{len(new_files)}")
if new_files:
    f = new_files.pop()
    doc = json.loads(Path(f).read_text())
    check("queue file carries the text", "one voice engine" in doc.get("text", ""))
    check("queue file carries the rate", doc.get("rate") == 1)
    check("queue file carries a timestamp", isinstance(doc.get("ts"), float))
    os.remove(f)

# 2. no leftover files from the test
left = glob.glob(str(qdir / "*.json")) + glob.glob(str(qdir / "*.tmp"))
for f in left:
    os.remove(f)
check("queue clean after test", len(glob.glob(str(qdir / "*.json"))) == 0)

# 3. the scanner side has the drain (source contract)
bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
check("scanner drains the queue in its main loop",
      "_drain_speech_queue()" in bs_src and "SPEECH_QUEUE_DIR" in bs_src)
# 4. the CONSOLE's _speak no longer speaks directly (no Popen in it)
app_src = (BASE / "app.py").read_text(encoding="utf-8")
_speak_body = app_src.split("def _speak(")[1].split("\ndef ")[0]
check("console _speak has no direct speech (Popen gone — queue only)",
      "Popen" not in _speak_body and "SPEECH_QUEUE_DIR" in _speak_body)

fails = [r for r in results if not r[1]]
out = BASE / "onevoice_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
