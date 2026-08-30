"""Test the her-words log: write path (scanner) + read path (console)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs

# isolate: scratch log file
bs.VOICE_LOG_FILE = str(Path(__file__).parent / "_test_voice_log.jsonl")
try:
    import os
    os.unlink(bs.VOICE_LOG_FILE)
except FileNotFoundError:
    pass

# 1. logging a line writes valid JSON with ts + text
bs._log_her_line("Get out now! Sell ARB!")
data = json.loads(open(bs.VOICE_LOG_FILE, encoding="utf-8").read().strip())
check("line logged with text", data.get("text") == "Get out now! Sell ARB!", data)
check("line logged with timestamp", bool(data.get("ts")))

# 2. VOICE_PREFIX is stripped from the log
bs._log_her_line(bs.VOICE_PREFIX + "Buy PUMP!")
lines_read = open(bs.VOICE_LOG_FILE, encoding="utf-8").read().splitlines()
last = json.loads(lines_read[-1])
check("mic-check prefix stripped", last["text"] == "Buy PUMP!", last["text"])

# 3. cap works (only last N kept)
for i in range(200):
    bs._log_her_line(f"filler {i}")
lines_read = [l for l in open(bs.VOICE_LOG_FILE, encoding="utf-8").read().splitlines() if l.strip()]
check("log capped at VOICE_LOG_MAX", len(lines_read) == bs.VOICE_LOG_MAX, f"{len(lines_read)}")

# 4. empty lines not logged
n_before = len(lines_read)
bs._log_her_line(bs.VOICE_PREFIX)
lines_after = [l for l in open(bs.VOICE_LOG_FILE, encoding="utf-8").read().splitlines() if l.strip()]
check("prefix-only line not logged", len(lines_after) == n_before)

# 5. source contracts
bs_src = open("background_scanner.py", encoding="utf-8").read()
app_src = open("app.py", encoding="utf-8").read()
check("_speak_server hooks the log", "_log_her_line(text)" in bs_src)
check("console renders the feed", "_render_her_words" in app_src)
check("feed placed with the light panel",
      "_render_light_panel(interval)\n        _render_her_words()" in app_src)
check("latest line in the title", "HER WORDS" in app_src and "tap to read all" in app_src)

# cleanup
import os
os.unlink(bs.VOICE_LOG_FILE)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_herwords_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
