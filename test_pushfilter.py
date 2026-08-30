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

# 3. TAKE IT pushes (URGENT — it's the sell/take action)
sent.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Take it! ARB! Plus 20 dollars!")
check("take it pushes urgent", len(sent) == 1 and sent[0].headers.get("Priority") == "urgent")

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

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_pushfilter_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
