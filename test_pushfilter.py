"""Test the BUY NOW / SELL NOW-only phone push filter (Aug 30)."""
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

sent = []
orig_request = None


def fake_urlopen(req, timeout=None):
    sent.append(req)
    class _R:
        status = 200
    return _R()


import urllib.request
urllib.request.urlopen = fake_urlopen

# pretend we have a topic
bs._push_topic = lambda: "test-topic"

def sent_texts():
    out = []
    for r in sent:
        out.append(r.data.decode("utf-8"))
    return out


# 1. BUY lines push (HIGH)
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Buy PUMP! 92 percent sure! The twenty comes in 45 minutes!")
t = sent_texts()
check("buy line pushes", len(t) == 1, t)
check("buy priority high", sent[0].headers.get("Priority") == "high" if sent else False)

# 2. GET OUT pushes (URGENT)
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Get out now! Sell ARB! It gave back too much from the top!")
t = sent_texts()
check("get out pushes urgent", len(t) == 1 and sent[0].headers.get("Priority") == "urgent", t)

# 3. TAKE IT does NOT push (user rule, Aug 31: ".ntfy doesn't need the
# 'take it' alerts. Just 'buy now' and 'Get out now'" — the take-profit
# nudges repeat every minute during a run and spam the phone)
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Take it! ARB! Plus 20 dollars!")
check("take it NOT pushed (Aug 31 rule)", len(sent) == 0, sent_texts())
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Take it! 120 dollars up!")
check("take it (dollar form) NOT pushed", len(sent) == 0)

# 4. routine position update does NOT push
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("ARB, plus 19 dollars, about 40 minutes left, stay close")
check("routine update NOT pushed", len(sent) == 0)

# 5. tide warning does NOT push
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Warning! The tide is going out! All ships sink together!")
check("tide warning NOT pushed", len(sent) == 0)

# 6. trade done does NOT push
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Trade done! Let's find the next one!")
check("trade done NOT pushed", len(sent) == 0)

# 7. identical GET OUT repeats throttle to 5 min
import time as _time
sent.clear()
bs._push_state = {"text": "get out now! sell arb!", "ts": _time.time()}
bs._push_to_phone("Get out now! Sell ARB!")
check("identical repeat within 5min throttled", len(sent) == 0)
# but after 5+ min it pushes again
bs._push_state = {"text": "get out now! sell arb!", "ts": _time.time() - 301}
sent.clear()
bs._push_to_phone("Get out now! Sell ARB!")
check("identical after 5min pushes", len(sent) == 1)

# 8. different text pushes immediately even if recent
bs._push_state = {"text": "get out now! sell arb!", "ts": _time.time()}
sent.clear()
bs._push_to_phone("Buy NEAR! 100 percent sure!")
check("different text pushes immediately", len(sent) == 1)

# 9. THE SEND (Sep 21): urlopen is actually invoked — the regression that
#    built the request and dropped it is closed at the source
check("urlopen invoked with a 10s timeout",
      "urllib.request.urlopen(req, timeout=10)" in
      open("background_scanner.py", encoding="utf-8").read())
check("push success logged", "PUSHED (" in
      open("background_scanner.py", encoding="utf-8").read())
check("push failure logged (never silent)", "phone push failed" in
      open("background_scanner.py", encoding="utf-8").read())

# 10. the full-reasons GET OUT body pushes as one URGENT message
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("GET OUT NOW — Sell MOODENG!\nWHY: Profit guard — it gave back 60 percent of the peak!\n"
                  "POSITION: entered 0.1500 · now 0.1600 · +6.7% (+54 dollars) · held 42 min\n"
                  "GOAL was +1.5% — the watch auto-closed; the grade is journaled.")
t = sent_texts()
check("full-reasons GET OUT pushes urgent",
      len(t) == 1 and sent[0].headers.get("Priority") == "urgent", t)
check("the reasons body rides intact (multi-line)",
      len(t) == 1 and "WHY:" in t[0] and "POSITION:" in t[0] and "GOAL was" in t[0])

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_pushfilter_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
