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
import scratch_space   # scratch policy: nothing volatile lands in the repo root
urllib.request.urlopen = fake_urlopen

# pretend we have a topic
bs._push_topic = lambda: "test-topic"
# THE CHARTER LEG IS ANOTHER URLLIB CONSUMER (Sep 29): _charter_push's
# route posts its own JSON through urllib.request.urlopen, which the
# fake below would capture and double every count — environment-
# dependently (the pool route only HTTPs when reachable). The filter
# is this suite's subject and the charter emission mechanics have
# their own drive (test_charter_signal_push); neutralize the leg here
# so the counts measure the ntfy wire and the retry queue only.
bs._charter_push = lambda event: True

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

import time as _time

# 7. BUY retries: the in-doubt queue carries BOTH call classes now
# (Sep 29: a missed BUY is the missed entry — the one call the desk
# can least afford to never hear). One second attempt ~60s later,
# armed whether or not ntfy accepted attempt 1.
sent.clear()
bs._push_retry_q.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Buy FIL! 91 percent sure! The recovery leg is on!")
check("a BUY arms exactly ONE retry in the queue",
      len(bs._push_retry_q) == 1, len(bs._push_retry_q))
_r = bs._push_retry_q[0]
check("the BUY retry rides ~60s out at HIGH priority (a second shot, "
      "not a second campaign)",
      59.0 <= _r["due"] - _r["why"] <= 61.0 and _r["prio"] == "high", _r)
sent.clear()
bs._push_retry_q.clear()
bs._push_state = {"text": "", "ts": 0.0}
bs._push_to_phone("Get out now! Sell ARB! It gave back too much!")
check("a GET OUT still arms its retry at URGENT (the Sep 21 contract "
      "intact, one queue, both classes)",
      len(bs._push_retry_q) == 1 and bs._push_retry_q[0]["prio"] == "urgent")
# the processor fires a DUE retry through the same wire
sent.clear()
bs._push_retry_q.clear()
bs._push_retry_q.append({"text": "Buy FIL! 91 percent sure!", "prio": "high",
                         "due": 0.0, "why": 0.0})   # long overdue
bs._process_push_retries()
check("a due retry FIRES through the wire and drains the queue",
      len(sent) == 1 and len(bs._push_retry_q) == 0, (len(sent), sent_texts()))
check("the fired retry keeps the call's priority on the wire",
      sent and sent[0].headers.get("Priority") == "high")
# the 8-deep cap bounds a pathological burst
bs._push_retry_q.clear()
for _i in range(12):
    bs._push_retry_q.append({"text": f"x{_i}", "prio": "high",
                             "due": _time.time() + 3600.0, "why": _time.time()})
bs._process_push_retries()   # nothing due; the cap trims the armory
check("the queue cap holds at 8 (a burst can't grow the armory unbounded)",
      len(bs._push_retry_q) == 8, len(bs._push_retry_q))
bs._push_retry_q.clear()

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

# 11. THE URGENCY-CLASS MATRIX (Sep 22): every spelling the user named.
# Word-boundary regexes — case never matters, quote style never matters,
# punctuation never matters. Buy class pushes HIGH; sell class URGENT.
_MATRIX_BUY = [
    'Buy "X" now! 91 percent sure!',        # Buy "x" now (double quotes)
    "Buy PUMP! 91 percent sure!",           # the real fire line
    "buy now — the rotation turned!",       # buy now
    'BUY "X" NOW — new champion!',          # BUY "X" NOW
    "New pick! Buy ARB!",                   # the new-pick shape
    "GO! Buy DOGE!",                        # the go! prefix
]
_MATRIX_SELL = [
    "Get out now! Sell ARB! It gave back too much!",
    "GET OUT NOW — Sell PUMP!\nWHY: trend broke",   # the full-reasons body
    "get out — the trend broke!",
    'SELL "X" NOW! It reversed!',
    "Sell 'x' now!",                        # Sell 'x' now (single quotes)
    "Sell now — take the money!",
    "GET OUT",                              # bare, uppercase
    "get out",                              # bare, lowercase
    "STOPPED OUT — sell KAITO!",
    "Time to sell!",                        # word at line END (the old
    #                                         'sell ' substring missed this)
]
for _txt in _MATRIX_BUY:
    sent.clear()
    bs._push_state = {"text": "", "ts": 0.0}
    bs._push_to_phone(_txt)
    check(f"BUY class pushes HIGH: {_txt[:30]!r}",
          len(sent) == 1 and sent[0].headers.get("Priority") == "high",
          f"n={len(sent)}")
for _txt in _MATRIX_SELL:
    sent.clear()
    bs._push_state = {"text": "", "ts": 0.0}
    bs._push_to_phone(_txt)
    check(f"SELL class pushes URGENT: {_txt[:30]!r}",
          len(sent) == 1 and sent[0].headers.get("Priority") == "urgent",
          f"n={len(sent)}")

# 12. THE WARNING GUARD (Sep 22): a line that STARTS with 'warning' is a
#     console status call — its text NEVER pushes, even when it contains
#     'get out' or 'sell'. The 06:41 loss warning ('Warning! PUMP down 11
#     dollars! Get out or hold!') pushed URGENT through exactly that hole.
_MATRIX_WARN = [
    "Warning! PUMP down 11 dollars! Get out or hold!",
    "Warning! ARB gave back 60 percent! Take it or ride!",
    "Warning! The tide is going out! All ships sink together!",
    "warning! lowercase start is guarded too!",
]
for _txt in _MATRIX_WARN:
    sent.clear()
    bs._push_state = {"text": "", "ts": 0.0}
    bs._push_to_phone(_txt)
    check(f"WARNING never pushes: {_txt[:38]!r}", len(sent) == 0)

# 13. word boundaries keep chatter out — 'buyer'/'resell' contain the
#     stems but are not calls
for _txt in ("the buyer stepped in", "we resell nothing",
             "Take it! ARB! Plus 20 dollars!",
             "ARB, plus 19 dollars, about 40 minutes left, stay close"):
    sent.clear()
    bs._push_state = {"text": "", "ts": 0.0}
    bs._push_to_phone(_txt)
    check(f"chatter never pushes: {_txt[:36]!r}", len(sent) == 0)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_pushfilter_check.txt'), "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
