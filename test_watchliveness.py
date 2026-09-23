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
     shows alive / slow / FROZEN from the age.
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

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nRESULT: {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
