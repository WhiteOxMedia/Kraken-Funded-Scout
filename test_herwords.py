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

# isolate: the voice log lives in a mkdtemp sandbox (testleaks triage,
# Sep 23) — the old repo-dir scratch file is retired; suite-per-process
# bounds the window and the live scanner's own pointer was bound at its
# import, long before this suite ran.
import tempfile as _tempfile_hw  # noqa: E402
_HW_SANDBOX = Path(_tempfile_hw.mkdtemp(prefix="_sbx_herwords_"))
bs.VOICE_LOG_FILE = str(_HW_SANDBOX / "test_voice_log.jsonl")  # leak-audit: ok (suite-long redirect into mkdtemp; handed back at suite end)
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

# 3. cap works (only last N kept) — write PAST the cap so the trim must
#    fire regardless of the constant's value (the Sep 4 cap raise 150→400
#    left this writing only 202 lines: below the cap, nothing to trim)
for i in range(bs.VOICE_LOG_MAX + 50):
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
      # (Sep 30: the call block re-indented 8 -> 4 spaces in the Sep-21
      # status-line re-fit — the adjacency itself never moved)
      '_render_light_panel(f"{interval // 60} min")\n    _render_her_words()' in app_src)
check("latest line in the title", "HER WORDS" in app_src and "tap to read all" in app_src)

# cleanup: hand the voice log back to production + scrub the sandbox
import os
# clock-audit: ok — SANDBOXED (deliberate hand-back to production after the sandbox; leak-audit documented) (clockbombs census 4)
bs.VOICE_LOG_FILE = str(Path("voice_log.jsonl"))  # leak-audit: ok (the deliberate hand-back to production)
import shutil as _shutil_hw
_shutil_hw.rmtree(_HW_SANDBOX, ignore_errors=True)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_herwords_check.txt", "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
