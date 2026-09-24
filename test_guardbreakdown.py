"""test_guardbreakdown.py — the guard's why, on the page.

USER RULE (Sep 23): "Show each watched pair's live GuardFactor breakdown
on the console — env, discipline, MLT, vol, memory — so I can see why
the net is wide or tight."

CONTRACT:
  1. The scanner stamps the FINALIZED breakdown onto every watch row it
     checks (`guard_brk`: the post-RCL/SAL/momentum factor, the seven
     named parts, the meta extras, a ts) — on HOLD too: the breakdown
     is a heartbeat, not an event. A pair whose ticker is down gets NO
     stamp (real silence stays silent, same rule as checked_ts).
  2. The stamp is the scanner's OWN computation — the page renders the
     why and never recomputes it (the one-brain rule). No profit_guard
     import may exist in the console's breakdown renderer.
  3. The WATCHING plate renders every part honestly: wide/normal/tight
     with the band colors, em-dash for an unreadable part, the carried
     provenance when the live loop is unreadable, and the meta extras
     (RCL drift, SAL, momentum, end-game) only when they actually bite.
  4. MY TRADES carries the same factor as a per-line badge with the EG /
     MOM flags, straight from the row — silent (fixed rules) says so.
  5. THE 24-HOUR RING (Sep 24, user: "a 24-hour guard-factor sparkline
     per pair — see how the net has moved across rotations"): the
     scanner samples the finalized factor onto the row's bounded ring
     (72 samples max, one per 20-min throttle, >24h pruned, unreadable
     records nothing); the plate draws the polyline with the REAL
     sample extremes and the last sample's age. One dot or a stale
     ring draws NOTHING — a one-point sparkline is a lie about a trend.
Run:  python test_guardbreakdown.py   (exit 0 = all pass)
"""
import sys, types, time, json, os
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).parent
TMP = HERE / "test_tmp_guardbrk"
TMP.mkdir(exist_ok=True)

P, F = [], []


def check(n, ok, extra=""):
    (P if ok else F).append(n)
    print(("  PASS " if ok else "  FAIL ") + str(n) + (f"  [{extra}]" if extra else ""))


print("== 1. the scanner stamps the finalized breakdown on every pair it checks ==")
import background_scanner as bs
import meta_layers as ml

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
        return [[0, 100.0, 100.3, 99.8, 100.2, 10.0]] * 60


bs._load_contest_watch = lambda: json.loads(json.dumps(WATCH))
bs._save_contest_watch = lambda w: _saved.update(json.loads(json.dumps(w)))
bs._voice_enabled = lambda: False
bs._speak_server = lambda *a, **k: None
try:
    bs._fleet_tide = lambda max_age_s=300: (55.0, "FLAT")
except Exception:
    pass
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
row = (_saved.get("FAKE/USD") or {}).get("guard_brk")
check(1.2, isinstance(row, dict) and t0 - 5 <= float(row.get("ts") or 0) <= time.time() + 5,
      "FAKE's row carries a fresh guard_brk stamp (written on HOLD, like checked_ts)")
gf = row.get("gf") if row else None
check(1.3, isinstance(gf, (int, float)) and 0.4 <= gf <= 2.0,
      f"the stamped factor is the finalized clamp range (gf={gf})")
parts = row.get("parts") or {}
check(1.4, set(parts) == {"env", "disc_mod", "mlt_corr", "vol_mod",
                          "asset_mem", "funding_mod", "decay_mod"},
      f"all seven named parts ride the stamp ({sorted(parts)})")
check(1.5, all(v is None or isinstance(v, (int, float)) for v in parts.values()),
      "every part is a number or an honest None — never a string")
check(1.6, isinstance(row.get("readable"), bool)
      and (row.get("carried_from") is None or isinstance(row.get("carried_from"), str)),
      "readable is a bool, carried_from names the day or is None")
check(1.7, (row.get("rcl") in (None, "UNKNOWN", "NORMAL", "MILD DRIFT", "MAJOR DRIFT"))
      and (row.get("rcl_mult") is None or isinstance(row.get("rcl_mult"), (int, float)))
      and (row.get("sal_mult") is None or isinstance(row.get("sal_mult"), (int, float))),
      f"the meta verdict and multipliers ride the stamp (rcl={row.get('rcl')})")
check(1.8, isinstance(row.get("momentum"), bool) and isinstance(row.get("endgame"), bool),
      "momentum and end-game flags are bools")
check(1.9, not (_saved.get("DEAD/USD") or {}).get("guard_brk"),
      "the dead-ticker pair got NO breakdown — real silence stays silent")

ring = row.get("ring") if row else None
check(1.10, isinstance(ring, list) and len(ring) == 1
      and isinstance(ring[0], (list, tuple)) and len(ring[0]) == 2
      and t0 - 5 <= float(ring[0][0]) <= time.time() + 5
      and isinstance(ring[0][1], (int, float)),
      "the first check seeds the ring with one (ts, factor) sample")
check(1.11, not ((_saved.get("DEAD/USD") or {}).get("guard_brk") or {}).get("ring"),
      "the dead-ticker pair never rings — real silence stays silent")

print("== 1b. the ring is bounded, throttled, and pruned (the scanner owns it) ==")
# the meta-layers sandbox again: every repeat cycle stays hermetic
ml.ROOT = TMP
ml.STATE_FILE = TMP / "meta_layers_state.json"
(TMP / "meta_layers_state.json").unlink(missing_ok=True)


def _reseed():
    """The stub loader hands back WATCH — copy the saved row in so the
    next cycle sees the previous stamp (two checks in a row)."""
    WATCH["FAKE/USD"]["guard_brk"] = json.loads(
        json.dumps(_saved["FAKE/USD"]["guard_brk"]))


_reseed()
t1 = time.time()
bs.contest_light(_Ex(), cfg)
_row2 = _saved["FAKE/USD"]["guard_brk"]
check(1.12, len(_row2.get("ring") or []) == 1,
      "a check inside the 20-min throttle adds NO sample (the ring is not a firehose)")
check(1.13, t1 - 5 <= float(_row2.get("ts") or 0) <= time.time() + 5,
      "the head stamp stays fresh every check — only the ring is throttled")

_reseed()
WATCH["FAKE/USD"]["guard_brk"]["ring"][-1][0] -= 21 * 60
t2 = time.time()
bs.contest_light(_Ex(), cfg)
_ring2 = _saved["FAKE/USD"]["guard_brk"].get("ring") or []
check(1.14, len(_ring2) == 2 and t2 - 5 <= float(_ring2[-1][0]) <= time.time() + 5,
      "past the throttle window the check appends exactly one sample")

WATCH["FAKE/USD"]["guard_brk"]["ring"] = json.loads(json.dumps(_ring2))
WATCH["FAKE/USD"]["guard_brk"]["ring"][0][0] -= 25 * 3600   # past the window
WATCH["FAKE/USD"]["guard_brk"]["ring"][1][0] -= 21 * 60      # throttle eligible
bs.contest_light(_Ex(), cfg)
_ring3 = _saved["FAKE/USD"]["guard_brk"].get("ring") or []
check(1.15, len(_ring3) == 2
      and all(time.time() - float(s[0]) <= 24 * 3600 + 5 for s in _ring3),
      "a sample older than 24h is pruned, never drawn")
ml.ROOT, ml.STATE_FILE = _ml_root0, _ml_state0   # sandbox out

print("== 2. the WATCHING plate renders the why (the REAL helper) ==")
app = (HERE / "app.py").read_text(encoding="utf-8")
_i = app.find("def _guard_breakdown_rows")
_j = app.find("def _render_light_panel")
check(2.0, 0 < _i < _j, "the breakdown renderer is extractable")
_NS = {"__name__": "guardbrk", "time": time}   # the renderer ages the ring
exec(app[_i:_j], _NS)
_gb_rows = _NS["_guard_breakdown_rows"]


def _row(gf=1.05, readable=True, carried=None, parts=None, **extra):
    d = {"ts": time.time(), "gf": gf, "readable": readable, "carried_from": carried,
         "parts": parts or {"env": 1.0, "disc_mod": 1.0, "mlt_corr": 0.0,
                            "vol_mod": 1.0, "asset_mem": 1.0,
                            "funding_mod": 1.0, "decay_mod": 1.0}}
    d.update(extra)
    return d


html = "\n".join(_gb_rows({"guard_brk": _row(gf=2.0)}, 3.4))
check(2.1, "LET IT RUN" in html and "#4be38a" in html and "×2.00" in html,
      "a wide factor reads LET IT RUN in green")
html = "\n".join(_gb_rows({"guard_brk": _row(gf=1.05)}, 1.0))
check(2.2, "normal trailing" in html and "#ffc36b" in html,
      "the neutral band reads normal trailing in amber")
html = "\n".join(_gb_rows({"guard_brk": _row(gf=0.7)}, -1.2))
check(2.3, "TIGHTEN" in html and "#ff6b5e" in html,
      "a tight factor reads TIGHTEN in red")
html = "\n".join(_gb_rows({"guard_brk": _row()}, 1.0))
check(2.4, all(w in html for w in ("env", "disc", "mlt", "vol", "mem", "fund", "decay")),
      "all seven part labels render — env, discipline, MLT, vol, memory, funding, decay")
html = "\n".join(_gb_rows({"guard_brk": _row(parts={"env": None, "disc_mod": None,
                                                    "mlt_corr": None, "vol_mod": None,
                                                    "asset_mem": None, "funding_mod": None,
                                                    "decay_mod": None})}, 1.0))
check(2.5, html.count("—") >= 7, "an unreadable part renders as an em-dash, never a fake number")
html = "\n".join(_gb_rows({"guard_brk": _row(parts={"env": 1.0, "disc_mod": 1.0,
                                                    "mlt_corr": 0.12, "vol_mod": 1.0,
                                                    "asset_mem": 1.0, "funding_mod": 1.0,
                                                    "decay_mod": 1.0})}, 1.0))
check(2.6, "mlt +0.12" in html, "a positive MLT renders with its sign (the loosen correction)")
html = "\n".join(_gb_rows({"guard_brk": _row(parts={"env": 1.0, "disc_mod": 1.0,
                                                    "mlt_corr": -0.30, "vol_mod": 1.0,
                                                    "asset_mem": 1.0, "funding_mod": 1.0,
                                                    "decay_mod": 1.0})}, 1.0))
check(2.7, "mlt -0.30" in html, "a negative MLT renders with its sign (the tighten correction)")
html = "\n".join(_gb_rows({"guard_brk": _row(readable=False, carried="2026-09-21")}, 1.0))
check(2.8, "carried from 2026-09-21" in html,
      "a carried factor names its day — the page never dresses a carried net as live")
check(2.9, "fixed rules" in "\n".join(_gb_rows({}, 1.0))
      and "fixed rules" in "\n".join(_gb_rows({"guard_brk": {"ts": time.time(), "gf": 0}}, 1.0)),
      "no stamp (or a zeroed one) says the committed fixed rules stand")
html = "\n".join(_gb_rows({"guard_brk": _row(rcl="MAJOR DRIFT", rcl_mult=0.75,
                                             sal_mult=0.85, momentum=True, endgame=True)}, 1.0))
check(2.10, all(w in html for w in ("RCL MAJOR DRIFT ×0.75", "SAL ×0.85",
                                    "MOMENTUM +15%", "END-GAME")),
      "the meta extras render only when they actually bite")
html = "\n".join(_gb_rows({"guard_brk": _row(rcl="NORMAL", rcl_mult=1.0,
                                             sal_mult=1.0, momentum=False, endgame=False)}, 1.0))
check(2.11, "RCL" not in html and "SAL" not in html and "END-GAME" not in html,
      "a NORMAL pass renders no meta noise — quiet when quiet")

print("== 2b. the sparkline draws the ring's shape (never rebuilds it) ==")
_tn = time.time()
html = "\n".join(_gb_rows({"guard_brk": _row(gf=1.6, ring=[[_tn - 5 * 3600, 1.0],
                                                           [_tn, 1.6]])}, 1.6))
check(2.12, "<polyline" in html and "net, 24h" in html and "1.00–1.60" in html,
      "a moving ring draws the polyline with the REAL sample extremes")
html = "\n".join(_gb_rows({"guard_brk": _row(gf=1.0, ring=[[_tn - 7200, 1.0],
                                                           [_tn - 3600, 1.0],
                                                           [_tn, 1.0]])}, 1.0))
check(2.13, "<polyline" in html and "1.00–1.00" in html,
      "a flat ring still draws — padded geometry, REAL extremes printed")
html = "\n".join(_gb_rows({"guard_brk": _row(ring=[[_tn, 1.2]])}, 1.2))
check(2.14, "<polyline" not in html,
      "one sample is no trend — no sparkline until there are two in-window points")
html = "\n".join(_gb_rows({"guard_brk": _row(ring=[[_tn - 30 * 3600, 1.0],
                                                   [_tn - 25 * 3600, 1.4]])}, 1.2))
check(2.15, "<polyline" not in html,
      "a fully stale ring (every sample past 24h) draws nothing")
html = "\n".join(_gb_rows({"guard_brk": _row(ring=[[_tn, 1.0], ["x", 1.2], [1.3]])}, 1.1))
check(2.16, "<polyline" not in html,
      "malformed ring entries are ignored, never drawn as fake points")

print("== 3. the wiring: page renders, never recomputes ==")
_k = app.find("def _render_light_panel")
_m = app.find("THE SEMANTIC-OPTIONS IDIOM", _k)
light = app[_k:_m]
check(3.1, "_guard_breakdown_rows(w, pct)" in app and app.count("_guard_breakdown_rows(w, pct)") == 1,
      "the WATCHING plate renders the breakdown exactly once per watch card")
check(3.2, "net ×" in light and "{_gb_txt}" in light and 'rec.get("guard_brk")' in light,
      "MY TRADES badges the factor straight from the row's stamp")
check(3.3, "profit_guard" not in app[_i:_j] and "guard_factor" not in app[_i:_j],
      "the breakdown renderer imports nothing from the guard — the one-brain rule holds")
check(3.4, light.count("guard_factor") == 0,
      "the MY TRADES badge never recomputes the factor either")
check(3.5, '"guard_brk"' in (HERE / "background_scanner.py").read_text(encoding="utf-8"),
      "the stamp writer exists in the scanner — the row is the only channel")
check(3.6, '"ring"' in (HERE / "background_scanner.py").read_text(encoding="utf-8")
      and "ring" in app[_i:_j],
      "the ring is written by the scanner and drawn by the plate — same channel as the stamp")

print(f"\n{len(P)} passed, {len(F)} failed")
import shutil
shutil.rmtree(TMP, ignore_errors=True)   # hygiene: the drive leaves no strays
sys.exit(1 if F else 0)
