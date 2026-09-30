"""test_pricealert.py — THE PRICE TRIPWIRE, PINNED (Sep 29).

ops/price_alert.py: one pair, one line, one ping on the phone the
moment the level is touched — the recovery-leg tripwire, riding the
SAME private ntfy pipe as the GET OUTs (push_topic.txt), one-shot with
the one-retry lesson (a 200 is not a delivery receipt), and honest on
a dead feed (a failed fetch NEVER fires — a stale price is a lie with
decimals). FIL's line was 1.085 with the tape at 1.063: the desk wants
the ping the moment the leg starts, not a restart-day maybe.

Pins, hermetic (the ntfy send, the feed, the lock paths and the sleep
are rebound; the loop is driven by a fake clock and a scripted tape
that can inject feed failures and send failures):

  the trigger arithmetic (at-the-line counts, both senses);
  the fire text names the pair, the line, the desk command;
  the wire format is the scanner's push shape, same topic file;
  the dead-feed rails: no fire on a failed fetch, the ~15-min
  degradation note exactly ONCE, recovery still trips;
  the trip: attempt 1 + attempt 2/2 sixty seconds later, the fired
  record, and a re-boot REFUSES to re-arm (discharged);
  the failed-send rails: the alert stays ARMED and retries; the cap
  gives up LOUDLY (exit 3) instead of silently;
  the lock: a live-python double is refused, a dead lock is retaken;
  read-only on the firm: no watch/account/bridge path, no order call.
"""
import sys
import json
import tempfile
import time as _time_mod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "ops"))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import price_alert as pa   # the ops watcher

# ── 1. the trigger arithmetic ─────────────────────────────────────────
check("at-the-line counts ('at 1.085' means 1.085 pings)",
      pa.tripped(1.085, 1.085, None) is True)
check("above the line trips", pa.tripped(1.09, 1.085, None) is True)
check("below the line does not (the leg has not started)",
      pa.tripped(1.063, 1.085, None) is False)
check("the below sense trips at/below its line",
      pa.tripped(150.0, None, 150.0) is True
      and pa.tripped(149.0, None, 150.0) is True
      and pa.tripped(151.0, None, 150.0) is False)
txt = pa.fire_text("FIL/USD", 1.087, 1.085, None)
check("the fire text names the pair, the crossed price, the line and "
      "the desk command that prices the next move",
      "FIL/USD" in txt and "1.087" in txt and "1.085" in txt
      and "recovery_scenarios.py" in txt, txt)
src = (Path(__file__).parent / "ops" / "price_alert.py").read_text(encoding="utf-8")
check("the push rides the SAME topic file and ntfy.sh wire shape the "
      "GET OUTs use (title, priority scheme, tags)",
      "push_topic.txt" in src and "https://ntfy.sh/" in src
      and '"Title": "Kraken Scout"' in src and '"Priority": prio' in src)

# ── 2. the hermetic loop world ────────────────────────────────────────
SBX = Path(tempfile.mkdtemp())
pa.LOCK_FILE = SBX / "price_alert.lock"
pa.STATE_FILE = SBX / "price_alert_state.json"
pa.TOPIC_FILE = SBX / "push_topic.txt"
pa.TOPIC_FILE.write_text("scout_test_topic", encoding="utf-8")

T = {"t": 1000.0}
slept = []
attempts = []          # every send attempt, successes and failures
_real_sleep, _real_time = _time_mod.sleep, _time_mod.time
_time_mod.sleep = lambda s: (slept.append(s), T.__setitem__("t", T["t"] + s))
_time_mod.time = lambda: T["t"]


def _scripted(tape):
    """A scripted last-price tape; 'FAIL' injects a fetch failure; an
    exhausted tape repeats its last entry."""
    it = iter(tape)

    def _fetch(pair):
        try:
            v = next(it)
        except StopIteration:
            v = tape[-1]
        return None if v == "FAIL" else v
    return _fetch


def _sender(outcomes):
    """A scripted ntfy pipe: pops ok/fail outcomes, records attempts."""
    it = iter(outcomes)

    def _send(text, prio, tag):
        try:
            ok = next(it)
        except StopIteration:
            ok = True
        attempts.append({"text": text, "prio": prio, "tag": tag, "ok": ok,
                         "t": T["t"]})
        return ok
    return _send


def _reset_state():
    """A fresh armed world — tolerant: a loud give-up never wrote one."""
    try:
        (pa.STATE_FILE).unlink()
    except FileNotFoundError:
        pass


try:
    # ── 3. the happy trip: fire, attempt 2/2, discharge ───────────────
    pa.send_ntfy = _sender([True, True])
    pa.fetch_price = _scripted([1.063, 1.070, 1.085])   # cycle 3 touches the line
    rc = pa._run_locked("FIL/USD", 1.085, None, 30.0)
    check("the trip exits clean", rc == 0, rc)
    check("the trip pushed attempt 1 and armed attempt 2/2 (the GET "
          "OUT lesson: a 200 is not a delivery receipt)",
          len(attempts) == 2 and "attempt 2/2" in attempts[1]["text"],
          attempts)
    check("the attempt 2/2 fires ~60s after the first (not the poll "
          "interval)",
          attempts[1]["t"] - attempts[0]["t"] == 60.0,
          attempts[1]["t"] - attempts[0]["t"])
    check("the ping is HIGH priority with the chart tag (a buy-side "
          "call, not an urgent exit)",
          attempts[0]["prio"] == "high"
          and attempts[0]["tag"] == "chart_with_upwards_trend",
          attempts[0])
    st = json.loads(pa.STATE_FILE.read_text(encoding="utf-8"))
    check("the fired record lands: discharged, the price and the line "
          "named",
          st.get("armed") is False and st.get("fired_price") == 1.085
          and st.get("fired_line") == 1.085 and st.get("fired_at"), st)
    check("the poll breathed before the trip (two 30s cycles, then the "
          "trip cycle — the state file shows the watcher breathing)",
          slept[:2] == [30.0, 30.0] and st.get("cycles") == 3, slept[:3])

    # ── 4. the discharge: a re-boot refuses to re-arm ─────────────────
    attempts.clear(); slept.clear()
    rc = pa._run_locked("FIL/USD", 1.085, None, 30.0)
    check("a discharged alert REFUSES to re-arm (one alert, one job — "
          "no second watcher after the first fired)",
          rc == 0 and not attempts and not slept, (rc, attempts))

    # ── 5. the dead feed: a failed fetch NEVER fires ──────────────────
    _reset_state()
    attempts.clear(); slept.clear()
    pa.send_ntfy = _sender([True, True, True])
    # 30 dead cycles at 30s = the ~15-min degradation note, exactly once;
    # cycle 32 recovers straight into the line
    pa.fetch_price = _scripted(["FAIL"] * 31 + [1.085])
    rc = pa._run_locked("FIL/USD", 1.085, None, 30.0)
    check("the dead-feed run still trips once the feed recovers", rc == 0, rc)
    check("a failed fetch NEVER fired (the lie-with-decimals rail): the "
          "first thing on the wire after 31 dead cycles is the LOW "
          "degradation note, never a trip ping",
          attempts[0]["prio"] == "low"
          and "still armed" in attempts[0]["text"], attempts[0])
    check("the degradation note sent exactly ONCE across 31 dead cycles "
          "(once per run, not a nag)",
          sum(1 for a in attempts if a["prio"] == "low") == 1, attempts)
    check("the recovered tape trips with the full ceremony (high ping "
          "+ attempt 2/2)",
          attempts[-2]["prio"] == "high"
          and "attempt 2/2" in attempts[-1]["text"], attempts[-2:])

    # ── 6. a failed send keeps the alert ARMED and retries ────────────
    _reset_state()
    attempts.clear(); slept.clear()
    pa.fetch_price = _scripted([1.09])   # trips on every cycle
    pa.send_ntfy = _sender([False, False, True, True])
    rc = pa._run_locked("FIL/USD", 1.085, None, 30.0)
    check("two failed sends left the alert ARMED and the third cycle's "
          "send delivered (a dead pipe must not silently swallow the trip)",
          rc == 0 and [a["ok"] for a in attempts[:3]] == [False, False, True],
          [a["ok"] for a in attempts])
    check("the delivery landed before the discharge (the fired record "
          "follows the ACCEPTED send, never a failed one)",
          rc == 0 and json.loads(
              pa.STATE_FILE.read_text(encoding="utf-8")).get("armed") is False,
          rc)
    check("the retry sleeps the poll interval between failed sends "
          "(not a tight loop against ntfy)",
          slept.count(30.0) >= 2, slept)

    # ── 7. the cap: a dead pipe gives up LOUDLY, never silently ───────
    _reset_state()
    attempts.clear()
    _real_cap = pa.SEND_RETRY_CAP
    pa.SEND_RETRY_CAP = 2            # tighten the cap so the drive stays fast
    try:
        pa.send_ntfy = _sender([False, False])
        rc = pa._run_locked("FIL/USD", 1.085, None, 30.0)
    finally:
        pa.SEND_RETRY_CAP = _real_cap
    check("after the cap the watcher exits 3 with the level and the "
          "price in the log (the alert never reached the pipe — named, "
          "not swallowed)",
          rc == 3 and len(attempts) == 2, (rc, attempts))

    # ── 8. the lock: no double watcher, dead locks are retaken ────────
    _reset_state()   # (section 7's give-up already left none — that IS the pin)
    import os
    pa.LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")   # THIS drive: a live python
    check("a LIVE python watcher is refused (a double is two phones "
          "buzzing at the same moment)",
          pa.claim_lock() is False)
    pa.LOCK_FILE.write_text("0", encoding="utf-8")                # dead/absent pid
    check("a DEAD lock is retaken (the recycled-pid trap closed)",
          pa.claim_lock() is True)
    pa.release_lock()
    check("the lock releases clean", not pa.LOCK_FILE.exists())
    check("no line armed exits with usage code 2 (not a silent 0)",
          pa.run("FIL/USD", None, None, 30.0) == 2)

    # ── 9. read-only on the firm ──────────────────────────────────────
    check("the watcher touches no watch file, no account state, no "
          "bridge DB, and no order endpoint",
          "contest_watch" not in src and "contest_account" not in src
          and "signal_bridge" not in src and "place_market" not in src)
    check("its own runtime files are the lock and the state json, "
          "gitignored neighbours, nothing else",
          "price_alert.lock" in src and "price_alert_state.json" in src)
finally:
    _time_mod.sleep = _real_sleep
    _time_mod.time = _real_time
    for f in (pa.LOCK_FILE, pa.STATE_FILE):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

fails = [r for r in lines if r.startswith("FAIL")]
import scratch_space   # scratch policy: the check report routes through the router
out = scratch_space.report_temp("pricealert_check.txt")
out.write_text("\n".join(lines) + f"\n\nRESULT: {PASS} passed, {FAIL} failed\n",
               encoding="utf-8")
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if fails else 0)
