"""Headless drive of the REAL _process_daily_canary state machine.

Frozen clock (datetime.datetime patched inside bs.MST's zone), stubbed
sender (no real ntfy traffic), backed-up state file. Scenarios:
  1. pre-window silence       5. backoff expiry re-fires
  2. 09:00 fire + state       6. >=21:00 silence (no all-night nagging)
  3. idempotent same-day      7. failed send NOT marked sent -> retries
  4. backoff suppresses <5min re-tries
"""
import datetime as _dt
import json
import os
import sys
import time

import background_scanner as bs

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + name)


CN_REAL = bs._PUSH_CANARY_JSON
# HERMETIC: drive a TEMP state file — the live push_canary.json belongs
# to the running scanner, and a live stamp for the fake-day dates would
# suppress the scenarios (the Sep 21 lesson: the live 18:41 canary
# collided with the frozen 2026-09-21 clock and failed the suite)
import shutil
import tempfile
_tmpdir = tempfile.mkdtemp(prefix="canary_test_")
CN = os.path.join(_tmpdir, "push_canary.json")
# the pointer install is guarded at L48 (inside the big try whose finally
# restores CN_REAL); the sandbox-by-construction value needs no sentinel here

_FAKE = _dt.datetime(2026, 9, 21, 8, 59, 0)


class _Frozen(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FAKE.replace(tzinfo=tz) if tz else _FAKE


_bs_dt = bs.datetime
_orig_send = bs._send_ntfy
try:
    bs._PUSH_CANARY_JSON = CN   # GUARDED SWAP (testleaks, Sep 23)
    # 1. pre-window: 08:59 — silent, no state written
    sent = []
    bs.datetime = _Frozen          # install the frozen clock
    bs._send_ntfy = lambda *a, **k: sent.append(a) or True
    bs._process_daily_canary()
    check("pre-window (08:59) silent", not sent and not os.path.exists(CN))

    # 2. 09:00 — fires, low priority, state marked sent for today
    _FAKE = _dt.datetime(2026, 9, 21, 9, 0, 0)
    bs._process_daily_canary()
    check("09:00 fires once", len(sent) == 1)
    check("canary priority is low", bool(sent) and sent[0][1] == "low")
    st = json.load(open(CN))
    check("state marks today sent", st.get("last_sent_day") == "2026-09-21")

    # 3. idempotent same day
    bs._process_daily_canary()
    check("same-day re-tick silent", len(sent) == 1)

    # 4. new day, tried <5 min ago -> suppressed
    _FAKE = _dt.datetime(2026, 9, 22, 9, 0, 0)
    json.dump({"day": "2026-09-22", "last_try_ts": time.time()},
              open(CN, "w"))
    sent.clear()
    bs._process_daily_canary()
    check("backoff suppresses <5min re-try", not sent)

    # 5. backoff expiry -> fires, marks the new day sent
    json.dump({"day": "2026-09-22", "last_try_ts": time.time() - 301},
              open(CN, "w"))
    bs._process_daily_canary()
    check("backoff expiry re-fires", len(sent) == 1)
    st = json.load(open(CN))
    check("new day marked sent", st.get("last_sent_day") == "2026-09-22")

    # 6. late evening — no nagging
    _FAKE = _dt.datetime(2026, 9, 23, 21, 0, 0)
    sent.clear()
    bs._process_daily_canary()
    check(">=21:00 silent (no all-night nagging)", not sent)

    # 7. failed send is NOT marked sent -> will retry next window
    _FAKE = _dt.datetime(2026, 9, 23, 10, 0, 0)
    json.dump({"day": "2026-09-23", "last_try_ts": time.time() - 301},
              open(CN, "w"))
    bs._send_ntfy = lambda *a, **k: False
    bs._process_daily_canary()
    st = json.load(open(CN))
    check("failed send not marked sent", st.get("last_sent_day") != "2026-09-23")
    check("failed send logged a try (backoff armed)", st.get("day") == "2026-09-23")
finally:
    bs.datetime = _bs_dt
    bs._send_ntfy = _orig_send
    bs._PUSH_CANARY_JSON = CN_REAL   # the live pointer back
    shutil.rmtree(_tmpdir, ignore_errors=True)

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
