"""test_watchliveness.py — the exit watch's heartbeat, made visible.

USER RULE (Sep 22): "Make the scanner's exit-watch liveness visible on
the console — last check time per watched pair — so a silent watcher is
noticeable instead of invisible."

CONTRACT:
  1. The scanner stamps `checked_ts` (epoch seconds) on EVERY watch row
     it actually checks each light cycle — even when every gate says
     HOLD — and persists it through the watch file. Absence of an exit
     must never be confusable with absence of a check.
  2. A pair whose ticker fetch fails gets NO stamp: per-pair silence is
     real silence, and the console shows it growing.
  3. The console surfaces the age in three honest states: fresh (green,
     <= 180s), SILENT (red, > 180s — "the scanner may be down"), and
     not-checked-yet (amber, missing stamp or absurd age — a stamp is
     never trusted past a day).
  4. MY TRADES carries the same heartbeat on the same line as P&L.
  5. THE SCANNER PULSE (Sep 22, the whole machine): one beat per
     COMPLETED light cycle — counter + timestamp in scanner_pulse.json —
     beats even with NO pairs watched; a crashed cycle (the loop's try
     swallows the raise) freezes the counter, and the auto-watch plate
     shows alive / slow / FROZEN from the age. The beat lives in the
     loop's try, AFTER the contest_light call — and NEVER inside
     contest_light's own body: an in-body beat advances the counter
     before the pass completes, so an exception raised after it (in the
     all-good voice, the exit evaluator, the cooldown review) is
     swallowed by the loop's except and the pulse reads alive through a
     crashed cycle.
  6. SCAN-AWARE PULSE (Sep 22, the boot-night incident): the full
     tournament runs inside the same loop pass and held the pulse ~9
     minutes — the console cried FROZEN for a working machine. A busy
     beat (phase=scan) refreshes the timestamp WITHOUT advancing the
     counter; the tournament beats busy at entry/mid-fetch/tail and
     clears to idle in a finally on EVERY path; the console reads a
     fresh-ish scan phase as alive-scan up to a 15-minute grace, past
     it FROZEN (a scanner dead mid-scan is still caught), and the
     per-pair stamps keep their hard 180s SILENT bound — no scan grace.
  7. THE SLOW-CYCLE WATCH (Sep 23): a light cycle still running past
     180s announces ONCE per episode, log-only (warnings never reach
     the phone) — distinguishing a SLOW SCAN (phase=scan, busy beats
     running) from a CRASHED CYCLE (no scan, the except swallowed the
     raise). The light block's finally resets the episode so the next
     slow episode announces again; the duration clock starts at boot.
Run:  python test_watchliveness.py   (exit 0 = all pass)
"""
import sys, types, time, json, os
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).parent
TMP = HERE / "test_tmp_liveness"
TMP.mkdir(exist_ok=True)

P, F = [], []


def check(n, ok, extra=""):
    (P if ok else F).append(n)
    print(("  PASS " if ok else "  FAIL ") + str(n) + (f"  [{extra}]" if extra else ""))


print("== 1. the scanner stamps every pair it actually checks ==")
import background_scanner as bs
import meta_layers as ml

# hermetic state: the watch lives in a dict, saves are captured
WATCH = {
    "FAKE/USD": {"entry": 100.0, "high": 100.5, "size": 500.0,
                 "goal_pct": 2.0, "window_min": 120,
                 "added": datetime.now(timezone.utc).astimezone().isoformat(),
                 "source": "champion", "why": ["BUY 70% (champion)"]},
    "DEAD/USD": {"entry": 50.0, "high": 50.0, "size": 500.0,
                 "goal_pct": 2.0, "window_min": 120,
                 "added": datetime.now(timezone.utc).astimezone().isoformat(),
                 "source": "champion", "why": ["BUY 70% (champion)"]},
}
_saved = {}


class _Ex:
    """FAKE answers; DEAD's ticker is down (the per-pair silence case)."""
    def fetch_ticker(self, pair):
        if pair.startswith("DEAD"):
            raise RuntimeError("ticker down")
        return {"last": 100.2}

    def fetch_ohlcv(self, pair, timeframe="1h", limit=200):
        # a flat series -> hold verdict, no exit path fires
        return [[0, 100.0, 100.3, 99.8, 100.2, 10.0]] * 60


bs._load_contest_watch = lambda: json.loads(json.dumps(WATCH))
bs._save_contest_watch = lambda w: _saved.update(json.loads(json.dumps(w)))
bs._voice_enabled = lambda: False
bs._speak_server = lambda *a, **k: None
try:
    bs._fleet_tide = lambda max_age_s=300: (55.0, "FLAT")
except Exception:
    pass
# the meta tickers ride the cycle — hermetic state file for them:
# each cycle's swap lives INSIDE its try and the finally restores the
# production paths (diag_testleaks class 2: a module-global path swap
# without an enclosing try/finally restore leaves the leak open)
_ml_root0, _ml_state0 = ml.ROOT, ml.STATE_FILE
cfg = types.SimpleNamespace(challenge_balance=10000.0, candle_interval=60,
                            lookback_candles=200)
t0 = time.time()
try:
    ml.ROOT = TMP
    ml.STATE_FILE = TMP / "meta_layers_state.json"
    (TMP / "meta_layers_state.json").unlink(missing_ok=True)
    bs.contest_light(_Ex(), cfg)
    _ran = True
except Exception as exc:
    _ran = False
    print(f"      (contest_light raised: {exc!r})")
finally:
    ml.ROOT = _ml_root0
    ml.STATE_FILE = _ml_state0
check(1.1, _ran, "the real contest_light cycle runs on the stub exchange")
saved_rows = _saved or {}
check(1.2, "FAKE/USD" in saved_rows and "DEAD/USD" in saved_rows,
      f"the cycle persisted the watch file ({sorted(saved_rows)})")
_ts = float(saved_rows.get("FAKE/USD", {}).get("checked_ts") or 0)
check(1.3, t0 - 5 <= _ts <= time.time() + 5,
      f"FAKE stamped at check time (age {time.time() - _ts:.1f}s)")
check(1.4, not saved_rows.get("DEAD/USD", {}).get("checked_ts"),
      "the dead-ticker pair got NO stamp — real silence stays silent")
check(1.5, saved_rows.get("FAKE/USD", {}).get("high") == 100.5,
      "the normal trackers still work beside the stamp (high held)")
check(1.6, "checked_ts" not in saved_rows.get("DEAD/USD", {}) or
      float(saved_rows["DEAD/USD"].get("checked_ts") or 0) == 0,
      "no zero-stamp forgery for the silent pair")

print("== 2. the second cycle refreshes the stamp (a heartbeat, not a birth) ==")
WATCH2 = dict(WATCH)
WATCH2["FAKE/USD"] = dict(WATCH["FAKE/USD"])
WATCH2["FAKE/USD"]["checked_ts"] = t0 - 600     # ten minutes stale
bs._load_contest_watch = lambda: json.loads(json.dumps(WATCH2))
_saved2 = {}
bs._save_contest_watch = lambda w: _saved2.update(json.loads(json.dumps(w)))
try:
    ml.ROOT = TMP
    ml.STATE_FILE = TMP / "meta_layers_state.json"
    bs.contest_light(_Ex(), cfg)
except Exception:
    pass
finally:
    ml.ROOT = _ml_root0
    ml.STATE_FILE = _ml_state0
_ts2 = float(_saved2.get("FAKE/USD", {}).get("checked_ts") or 0)
check(2.1, _ts2 >= time.time() - 5,
      "a stale stamp is overwritten the moment the watcher checks again")

print("== 3. the console reads the heartbeat (census) ==")
app = (HERE / "app.py").read_text(encoding="utf-8")
check(3.1, 'w.get("checked_ts")' in app,
      "the WATCHING plate reads the stamp from the watch row")
check(3.2, 'rec.get("checked_ts")' in app,
      "MY TRADES reads the stamp from the persisted row")
check(3.3, app.count("checked_ts") >= 2 and "86400" in app,
      "the absurd-age guard exists (a stamp is never trusted past a day)")
check(3.4, "SILENT, the scanner may be down" in app and "#ff6b5e;'>SILENT " in app,
      "the red SILENT wording is on both plates, not implied")
check(3.5, "not checked yet" in app and "not checked" in app,
      "missing stamps say so instead of pretending freshness")
check(3.6, "_age <= 180" in app, "the 3-minute freshness bound pins both plates")
check(3.7, "checked {" in app or "checked " in app,
      "the age renders in seconds/minutes, human units")
src_bs = (HERE / "background_scanner.py").read_text(encoding="utf-8")
check(3.8, src_bs.count('w["checked_ts"] = time.time()') == 1,
      "exactly one stamp site in the scanner (after the ticker + entry gates)")

print("== 4. LIVE probe: the real watch file's heartbeat right now ==")
# (CodeRabbit minor) a fresh checkout has no runtime watch file — the
# probe is operational, not hermetic, and must not fail the drive for
# an environmental absence. The hermetic sections stay authoritative.
_RealWatch = HERE / "contest_watch.json"
if not _RealWatch.exists():
    check(4.1, True, "skip: no runtime watch file yet (fresh checkout) — "
          "the hermetic sections above are the contract")
else:
    try:
        real = json.loads(_RealWatch.read_text(encoding="utf-8"))
        for pair, row in real.items():
            ts = float(row.get("checked_ts") or 0)
            age = time.time() - ts if ts else None
            state = ("NEVER" if age is None else
                     f"{age:.0f}s" if age <= 180 else
                     f"SILENT {age / 60:.0f}m")
            print(f"      {pair}: {state}")
        check(4.1, True, "read-only probe (the running scanner stamps on its next cycle)")
    except Exception as exc:
        check(4.1, False, f"probe failed: {exc!r}")

print("== 5. the scanner pulse — the whole-machine heartbeat ==")
_pulse_file = TMP / "scanner_pulse.json"
_pulse_orig = bs.PULSE_JSON             # guarded swap (diag_testleaks): every
_p1 = _p2 = None                        # file-touching beat runs while the
try:                                    # production path is swapped out; the
    bs.PULSE_JSON = str(_pulse_file)    # finally restores it BEFORE any code
    _pulse_file.unlink(missing_ok=True) # that could raise past the section
    bs._pulse_beat()
    bs._pulse_beat()
    _p1 = json.loads(_pulse_file.read_text(encoding="utf-8"))
    _p1["operator_key"] = "keep-me"
    _pulse_file.write_text(json.dumps(_p1), encoding="utf-8")
    bs._pulse_beat()
    _p2 = json.loads(_pulse_file.read_text(encoding="utf-8"))
finally:
    bs.PULSE_JSON = _pulse_orig
check(5.1, (_p1 or {}).get("cycles") == 2,
      f"two beats -> the counter advanced by exactly 2 (got {(_p1 or {}).get('cycles')})")
check(5.2, time.time() - float((_p1 or {}).get("last_cycle_ts") or 0) < 5,
      "last_cycle_ts is the completion moment (fresh)")
check(5.3, int((_p1 or {}).get("pid") or 0) == os.getpid(),
      "the beating pid is stamped (which scanner instance is alive)")
check(5.4, (_p2 or {}).get("operator_key") == "keep-me" and (_p2 or {}).get("cycles") == 3,
      "the beat MERGES — it never clobbers keys another writer added")
# the freeze contract: the beat lives INSIDE the loop's try, AFTER the
# contest_light call — a raise (or a dead thread) freezes the counter.
_call = src_bs.find("contest_light(exchange, cfg)",
                    src_bs.find("last_contest_light = 0"))
_window = src_bs[_call:src_bs.find("sure thing scan error", _call)]
_beat_at = _window.find("_pulse_beat()")
_except_at = _window.find("except Exception as exc:")
check(5.5, _window.count("_pulse_beat()") == 1,
      f"exactly one beat site in the loop (got {_window.count('_pulse_beat()')})")
check(5.6, 0 < _beat_at < _except_at,
      "the beat sits AFTER the contest_light call, INSIDE the try — "
      "a crashed cycle never advances the counter (freeze = visible)")
_defblk = src_bs[src_bs.find("def _pulse_beat"):src_bs.find("def _pulse_beat") + 900]
check(5.7, "Empty watch beats" in _defblk,
      "an empty watch still beats: the pulse is the cycle's, not the positions'")
# console census
check(5.8, "scanner_pulse.json" in app and app.count("_pulse_html()") >= 2,
      "the console reads the pulse and renders it on the light panel")
check(5.9, all(w in app for w in
               ("no heartbeat yet", "alive", "slow", "FROZEN", "cycle #")),
      "the three pulse states + the cycle counter are real render paths")
check(5.10, "def _scanner_pulse" in app and "86400" in app,
      "the pulse reader distrusts missing/absurd files (a stale file "
      "can't fake a live machine)")
# live probe (operational, read-only — the running scanner beats on its
# next restart; absence must not fail the drive)
_RealPulse = HERE / "scanner_pulse.json"
if _RealPulse.exists():
    try:
        _rp = json.loads(_RealPulse.read_text(encoding="utf-8"))
        _age = time.time() - float(_rp.get("last_cycle_ts") or 0)
        print(f"      live pulse: cycle #{_rp.get('cycles')} finished {_age:.0f}s ago")
    except Exception as exc:
        print(f"      live pulse unreadable: {exc!r}")
check(5.11, True, "live-pulse probe is read-only")

print("== 6. the pulse is scan-aware — a 9-minute tournament is alive, not FROZEN ==")
# busy-beat semantics (hermetic: same guarded swap as section 5)
_pulse_file6 = TMP / "scanner_pulse.json"
_pulse_orig6 = bs.PULSE_JSON
_b0 = _b1 = _b2 = None
try:
    bs.PULSE_JSON = str(_pulse_file6)
    _pulse_file6.unlink(missing_ok=True)
    bs._pulse_beat()                      # a completed cycle first
    _b0 = json.loads(_pulse_file6.read_text(encoding="utf-8"))
    time.sleep(1.1)                       # the timestamp must actually move
    bs._pulse_beat("busy")                # the tournament's keep-warm
    _b1 = json.loads(_pulse_file6.read_text(encoding="utf-8"))
    bs._pulse_beat()                      # the clear
    _b2 = json.loads(_pulse_file6.read_text(encoding="utf-8"))
finally:
    bs.PULSE_JSON = _pulse_orig6
check(6.1, (_b1 or {}).get("cycles") == (_b0 or {}).get("cycles"),
      f"a busy beat NEVER advances the counter (got {_b0 and _b0.get('cycles')} -> {_b1 and _b1.get('cycles')})")
check(6.2, (_b1 or {}).get("phase") == "scan"
      and float((_b1 or {}).get("last_cycle_ts") or 0) > float((_b0 or {}).get("last_cycle_ts") or 0),
      "a busy beat refreshes the timestamp and records phase=scan")
check(6.3, (_b2 or {}).get("cycles") == (_b0 or {}).get("cycles", 0) + 1
      and (_b2 or {}).get("phase") == "idle",
      "the clear advances the counter and returns the phase to idle")
check(6.4, int((_b1 or {}).get("pid") or 0) == os.getpid(),
      "the busy beat keeps the pid stamped")
# corrupt file + busy beat: the keep-warm degrades like every other beat
_b3 = None
try:
    bs.PULSE_JSON = str(_pulse_file6)
    _pulse_file6.write_text("{garbage", encoding="utf-8")
    bs._pulse_beat("busy")
    _b3 = json.loads(_pulse_file6.read_text(encoding="utf-8"))
finally:
    bs.PULSE_JSON = _pulse_orig6
check(6.5, (_b3 or {}).get("phase") == "scan" and "cycles" not in (_b3 or {}),
      "a corrupt file degrades to a phase-only busy beat (no crash, and no "
      "fabricated cycle count — busy never touches the counter)")
# wiring census: three busy sites inside the tournament gate, one clear in a finally
_w6 = src_bs[src_bs.find("if not no_buy and (now.timestamp() - last_full_scan)"):
             src_bs.find("# ── Sleep until next check")]
check(6.6, _w6.count('_pulse_beat("busy")') == 4,
      f"the tournament beats busy at entry, mid-fetch, post-bracket, and "
      f"tail (got {_w6.count('_pulse_beat(\"busy\")')}) — the scoring "
      "bracket is ~10 quiet minutes and must not outgrow the console's "
      "scan grace")
_fin = _w6.find("finally:")
check(6.7, 0 < _fin < _w6.find("except Exception as exc:\n                logger.error(\"Tournament failed") + 900
      and "_pulse_beat()" in _w6[_fin:_fin + 200]
      and '_pulse_beat("busy")' not in _w6[_fin:_fin + 200],
      "the clear lives in the tournament's finally — idle on EVERY path, success or crash")
_lw = src_bs[src_bs.find("contest_light(exchange, cfg)",
                          src_bs.find("last_contest_light = 0")):
             src_bs.find("sure thing scan error")]
check(6.8, "_pulse_beat()" in _lw and '_pulse_beat("busy")' not in _lw,
      "the per-cycle completed beat is untouched — cycles still count light cycles only")
# THE FREEZE CONTRACT'S NEGATIVE SPACE (Sep 23): the light's BODY sits
# BEFORE the call site, so every post-call window census above stays green
# while a beat moved INSIDE contest_light silently defeats the freeze —
# the counter advances mid-pass, the loop's except swallows the rest, and
# the console reads alive through a crashed cycle. Census the body itself:
_li = src_bs.find("def contest_light(exchange, cfg):")
_ln = src_bs.find("\ndef main():", _li)
_light = src_bs[_li:_ln if _ln > _li else len(src_bs)]
check(6.16, _light.count("_pulse_beat(") == 0,
      f"NO pulse beat inside contest_light's body (got "
      f"{_light.count('_pulse_beat(')}) — a beat there fires before the "
      "pass completes: the freeze contract (5.6) only holds while the "
      "beat follows the call site in the loop's try")
# THE TOURNAMENT'S NEGATIVE SPACE (Sep 23, pinned like 6.16): main's
# tournament block may beat busy only INSIDE the gate's try — the finally
# clears the phase on every path, so any beat outside the try is a beat
# an exception strand can never clear (the pulse would read 'scan' until
# the next hold instead of falling back to stamps).
_mi = src_bs.find("def main():")
_mj = src_bs.find("\ndef ", _mi)
_main = src_bs[_mi:_mj if _mj > _mi else len(src_bs)]
_ti = _main.find("if not no_buy and (now.timestamp() - last_full_scan) >= full_interval * 60:")
_fi = _main.find("finally:", _ti)
_tournament = _main[_ti:_fi if _fi > _ti else len(_main)]
_tbody = _tournament[_tournament.find("try:"):] if "try:" in _tournament else ""
check(6.17, 0 < _ti < _fi,
      "the tournament block and its finally are both found (census anchors intact)")
check(6.18, _tournament.count('_pulse_beat("busy")') == _tbody.count('_pulse_beat("busy")'),
      f"EVERY busy beat in the tournament block sits inside the gate's try "
      f"({_tbody.count('_pulse_beat(\"busy\")')}/{_tournament.count('_pulse_beat(\"busy\")')}) "
      "— a beat outside the try is a phase an exception can strand at scan "
      "until the next hold; the finally clears only what the try began")
check(6.19, _tbody.count('_pulse_beat("busy")') >= 2,
      f"the hold still beats busy through its long phases ({_tbody.count('_pulse_beat(\"busy\")')} in-body: "
      "hold entry, mid-fetch, bracket, tail) — the scan grace is earned by "
      "beats, not by the block's mere presence")
check(6.20, "_pulse_beat()" in _main[_fi:_fi + 400],
      "the clear-to-idle beat still lives in the finally (the counter's "
      "owner, never strandable)")
# console classification: the REAL defs exec'd against a real pulse file
# (the _scanner_pulse -> _fmt_pulse_age -> _pulse_html chain, one slice)
_i = app.find("def _scanner_pulse():")
_j = app.find("def _render_light_panel", _i)
check(6.9, 0 < _i < _j, "the console's pulse chain is extractable")
_NS6 = {"__name__": "watchliveness", "time": time, "PULSE_JSON": str(_pulse_file6)}
exec(app[_i:_j], _NS6)
_html = _NS6["_pulse_html"]


def _write_pulse(cycles, age_s, phase):
    _pulse_file6.write_text(json.dumps(
        {"cycles": cycles, "last_cycle_ts": time.time() - age_s,
         "pid": 1, "phase": phase}), encoding="utf-8")


_write_pulse(9, 20, "idle")
check(6.10, "alive" in _html() and "alive-scan" not in _html(),
      "a fresh idle pulse reads alive exactly as before (no behavior change)")
_write_pulse(9, 400, "scan")
check(6.11, "alive-scan" in _html() and "FROZEN" not in _html(),
      "4 minutes into a scan: alive-scan, not FROZEN — the boot-night false alarm dies")
_write_pulse(9, 400, "idle")
check(6.12, "FROZEN" in _html(),
      "the same 400s age with phase=idle still reads FROZEN — grace belongs to real scans only")
_write_pulse(9, 740, "scan")
check(6.13, "FROZEN" in _html(),
      "just past the 720s grace a scan reads FROZEN — a scanner dead "
      "MID-scan is still caught")
_write_pulse(9, 700, "scan")
check(6.14, "alive-scan" in _html(),
      "700s under the grace reads alive-scan (the boundary is the 720s "
      "constant — sized to the observed ~10-minute full scan)")
_pulse_file6.unlink(missing_ok=True)
check(6.15, "180" in app,
      "the per-pair SILENT bound stays 180s — pairs get no scan grace, the machine does")

print("== 7. the slow-cycle watch — one announcement, scan vs crash named ==")
# the REAL alarm + phase reader, called directly (module-level, like the beats)
from datetime import datetime as _dt
class _Capture:
    def __init__(self):
        self.records = []
    def warning(self, msg, *a):
        self.records.append(("WARNING", msg % a if a else msg))
    def error(self, msg, *a):
        self.records.append(("ERROR", msg % a if a else msg))
    def debug(self, msg, *a):
        pass
_orig_logger7 = bs.logger
_orig_pulse7 = bs.PULSE_JSON
_pf7 = TMP / "scanner_pulse.json"
try:
    bs.logger = _Capture()
    # phase reader: hermetic pulse swap
    bs.PULSE_JSON = str(_pf7)
    _pf7.write_text(json.dumps({"phase": "scan", "cycles": 5}), encoding="utf-8")
    check(7.1, bs._pulse_scanning() is True, "phase=scan reads True")
    _pf7.write_text(json.dumps({"phase": "idle", "cycles": 5}), encoding="utf-8")
    check(7.2, bs._pulse_scanning() is False, "phase=idle reads False")
    _pf7.unlink(missing_ok=True)
    check(7.3, bs._pulse_scanning() is False,
          "a missing pulse file claims nothing (fail-soft False)")
    bs.PULSE_JSON = _orig_pulse7
    # the alarm: fresh pass is silent, slow pass announces ONCE, per voice
    _now7 = time.time()
    _ep = {"start": 0.0, "announced": False, "scan": False}
    bs._cycle_slow_alarm(_ep, _now7 - 30, True)      # 30s in: under the 180s bar
    check(7.4, not bs.logger.records and _ep["start"] == 0.0,
          "a 30s pass is under the 3-minute bar — silent, no episode opened")
    bs._cycle_slow_alarm(_ep, _now7 - 200, True)     # 200s in, scanning
    check(7.5, len(bs.logger.records) == 1 and _ep["announced"] is True
          and abs(_ep["start"] - (_now7 - 200)) < 2,
          "past 180s the episode opens and announces exactly once "
          "(the episode clock is the pass completion, not the alarm call)")
    check(7.6, bs.logger.records[0][0] == "WARNING" and "SLOW CYCLE (scan)" in bs.logger.records[0][1],
          "a slow SCAN says so: WARNING, named as the tournament holding the cycle")
    bs._cycle_slow_alarm(_ep, _now7 - 400, True)     # later, same episode
    check(7.7, len(bs.logger.records) == 1,
          "announced once per episode — the follow-up call stays silent")
    _ep2 = {"start": 0.0, "announced": False, "scan": False}
    bs._cycle_slow_alarm(_ep2, _now7 - 250, False)   # slow and NOT scanning
    check(7.8, len(bs.logger.records) == 2 and bs.logger.records[1][0] == "ERROR"
          and "no scan" in bs.logger.records[1][1] and "crashed" in bs.logger.records[1][1].lower(),
          "a slow pass with NO busy beats is the crashed-cycle voice: ERROR, "
          "named as a swallowed raise, not a working tournament")
    # the finally reset: a completed pass re-arms the alarm for the NEXT episode
    _ep.update(start=0.0, announced=False, scan=False)
    bs._cycle_slow_alarm(_ep, _now7 - 190, True)
    check(7.9, len(bs.logger.records) == 3,
          "after the light block's reset the NEXT slow episode announces again")
    # wiring census: the call sits between the light block and the exit gate,
    # the completion timestamp rides the light try, the reset rides the finally
    _w7 = src_bs[src_bs.find("contest_light(exchange, cfg)",
                              src_bs.find("last_contest_light = 0")):
                 src_bs.find("(every 5 minutes on open positions)")]
    _call7 = _w7.find("_cycle_slow_alarm(_slow_episode")
    _fin7 = _w7.find("finally:")
    _exit7 = _w7.find("# ── Exit check")
    check(7.10, 0 < _fin7 < _call7 < _exit7,
          "the watch runs between light passes — after the light block's "
          "finally, before the exit gate")
    check(7.11, "last_cycle_done = time.time()" in _w7
          and _w7.find("last_cycle_done = time.time()") < _w7.find("except Exception as exc:"),
          "the completion timestamp is set INSIDE the light try — only a real "
          "completed pass moves the clock")
    _lf7 = _w7.find("finally:")
    check(7.12, 0 < _lf7 and "_slow_episode.update(start=0.0" in _w7[_lf7:_lf7 + 150],
          "the light block's finally resets the episode — announce-once, not announce-ever")
    check(7.13, "_SLOW_CYCLE_S = 180.0" in src_bs,
          "the 3-minute bar is one named constant")
    check(7.14, "send_alert" not in src_bs[src_bs.find("def _cycle_slow_alarm"):
                                            src_bs.find("def _day_review_spoken")],
          "the alarm is log-only — no push path (warnings never reach the phone)")
finally:
    bs.logger = _orig_logger7
    bs.PULSE_JSON = _orig_pulse7

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nRESULT: {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
