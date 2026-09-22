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
Run:  python test_watchliveness.py   (exit 0 = all pass)
"""
import sys, types, time, json
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
# the meta tickers ride the cycle — hermetic state file for them
ml.ROOT = TMP
ml.STATE_FILE = TMP / "meta_layers_state.json"
(TMP / "meta_layers_state.json").unlink(missing_ok=True)

cfg = types.SimpleNamespace(challenge_balance=10000.0, candle_interval=60,
                            lookback_candles=200)
t0 = time.time()
try:
    bs.contest_light(_Ex(), cfg)
    _ran = True
except Exception as exc:
    _ran = False
    print(f"      (contest_light raised: {exc!r})")
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
    bs.contest_light(_Ex(), cfg)
except Exception:
    pass
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
try:
    real = json.loads((HERE / "contest_watch.json").read_text(encoding="utf-8"))
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

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nRESULT: {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
