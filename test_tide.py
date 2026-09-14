"""Test the tide: classification thresholds + a live breadth reading.

Sep 8 (CodeRabbit): the FLAT-sizing checks no longer stop at source
tokens — a runtime harness EXECUTES the real `_sure_thing_scan` body
against fakes and grades what the code DOES: the armed-announce dedup,
the vetoed coil's memory (the re-announce loop fix), the FLAT half-size
on a real fire, the fresh fire-time tide read, and the climb-tide stamp
in the fired snapshot."""
import sys
import time as _time
import types
from datetime import datetime as _dt, timedelta as _td
from pathlib import Path
from zoneinfo import ZoneInfo as _ZI

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from background_scanner import _tide_class, _fleet_tide, TIDE_RISING_MIN, TIDE_OUT_MAX
import background_scanner as bs

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# thresholds
check("75% -> RISING", _tide_class(75.0) == "RISING")
check("74.9% -> FLAT", _tide_class(74.9) == "FLAT")
check("50% -> FLAT", _tide_class(50.0) == "FLAT")
check("35.1% -> FLAT", _tide_class(35.1) == "FLAT")
check("35% -> OUT", _tide_class(35.0) == "OUT")
check("10% -> OUT", _tide_class(10.0) == "OUT")
check("bands are sane", TIDE_RISING_MIN == 75.0 and TIDE_OUT_MAX == 35.0)
# the Sep 11 retune (diag_tideretune.py): the RISING bar moved 70 -> 75
# on the recalibrated spot-native store; OUT stayed 35 (no FLAT/OUT seam).

# live reading from the real engine cache
pct, state = _fleet_tide(max_age_s=0)
if pct is None:
    check("live tide readable (cache may be empty in test env)", True, "returned None (fail-open)")
else:
    check("live breadth in 0-100", 0.0 <= pct <= 100.0, f"{pct}")
    check("live state is a real state", state in ("RISING", "FLAT", "OUT"), state)

# ── THE FLAT SIZING FACTOR (Sep 7: the stamped week spoke — FLAT 22%
#    win vs RISING 57%; the user picked 0.50×) ──────────────────────────
bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8").replace("\r\n", "\n")
ap_src = (BASE / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
check("the factor exists at 0.50", "TIDE_FLAT_SIZE_FACTOR = 0.50" in bs_src)
_st_fn = bs_src[bs_src.find("def _sure_thing_scan"):bs_src.find("# ── ONE VOICE ENGINE")]
check("the SURE THING sizes FLAT too (Sep 7: the same factor, no exception)",
      "_st_tide_mult = TIDE_FLAT_SIZE_FACTOR" in _st_fn
      and "_spend = _spend * _st_tide_mult" in _st_fn
      and "FLAT TIDE SIZING" in _st_fn)
check("the Sure Thing popup names the FLAT factor (line + voice)",
      "TIDE SIZES IT: FLAT" in _st_fn
      and "flat tide — half size!" in _st_fn
      and "× {_st_tide_mult:.2f} FLAT tide" in _st_fn)
check("the Sure Thing reads the tide fresh at fire time (not the funnel cache)",
      "_st_tide_pct, _st_tide_state = _fleet_tide()" in _st_fn)
check("the champion sizes FLAT down (after the guard + brain, before the worth floor)",
      "if is_buy and (_df_state or \"\") == \"FLAT\":" in bs_src
      and "_spend = _spend * _tide_size_mult" in bs_src
      and bs_src.find("FLAT TIDE SIZING") < bs_src.find("if is_buy and not _fast_route:"))
check("the mover sizes FLAT down",
      "(_tsw or \"\") == \"FLAT\"" in bs_src
      and "_m_spend = _m_spend * TIDE_FLAT_SIZE_FACTOR" in bs_src)
check("the QC mirrors the alert (never disagrees)",
      "_bs_tsf.TIDE_FLAT_SIZE_FACTOR" in ap_src
      and "(_tide_state or \"\") == \"FLAT\"" in ap_src)
check("the popup + voice name the discount",
      "TIDE SIZES IT: FLAT" in bs_src and "Flat tide — half size!" in bs_src)

# ── RUNTIME: the fire path EXECUTES (CodeRabbit, Sep 8) ────────────────
# The scan body lives 4-space indented inside the big scan() loop and
# imports sure_thing / contest_account lazily at CALL time. The harness
# strips one indent level, exec()s it once, and runs the returned
# _sure_thing_scan(exchange) against fakes. The fake imports are wired
# through a custom __builtins__ mapping (a plain dict keeps names
# __import__ looks up working) — no sys.modules mutation to restore.
_MST = _ZI("America/Denver")
_scan_body = "\n".join((l[4:] if l.startswith("    ") else l)
                       for l in _st_fn.splitlines())
_fake_goal = 1.5   # what the fake sure_thing module reports as the fire's goal
_fake_live_goal = 2.4   # what the fake market-goal machinery picks for this coin
check("the runtime slice is the scan function",
      _scan_body.lstrip().startswith("def _sure_thing_scan"))


class _DeepRead:
    def __init__(self, action, confidence):
        self.action, self.confidence = action, confidence


class _Ex:
    def fetch_ohlcv(self, pair, timeframe="5m", limit=720):
        return [[0, 1, 1, 1, 1, 10]] * 50


_ARMED = {"armed": True, "fire": False, "zone_high": 1.0000, "tests": 6,
          "detail": "the coil", "score": 70.0, "trigger": "crack",
          "est_min": 45, "vol_spike": 2.2, "trigger_px": 1.005,
          "coil_range_pct": 1.1, "squeeze_pct": 8.0, "atr_ratio": 1.0,
          "bb_width_pctile": 4.0, "pullback_trend": 1, "micro_spikes": 2,
          "buy_dominance": 60.0, "hist_hit_rate": 55.0}


_fake_goal = 1.5   # what the fake sure_thing module reports as the fire's goal


def _make_harness(deep, tide_state="RISING"):
    """One poll harness: the REAL _sure_thing_scan body + fakes.
    Returns (scan_fn, captures, tide_box, armed, result_box)."""
    cap = {"notify": [], "speak": [], "snaps": [], "sups": [], "rot": [],
           "tide_reads": 0, "learn_bar": (None, "cold"),
           "seam_rel": (False, "the journal shows no tide-out burials")}
    armed, tide_box, result_box = {}, {"state": tide_state}, {"r": dict(_ARMED)}

    fake_st = types.ModuleType("sure_thing")
    fake_st.SURE_THING_WINDOW_MIN = 60
    fake_st.SURE_THING_GOAL_PCT = 1.5
    fake_st.PRE_BREAKOUT_THRESHOLD = 65
    fake_st.analyze = lambda rows: dict(result_box["r"])
    fake_ca = types.ModuleType("contest_account")
    fake_ca.load_state = lambda: {"current_balance": 10000.0,
                                  "start_balance": 10000.0}
    fake_ca.window_drawdown_pct = lambda pair: 2.0
    fake_ca.max_entry_usd = lambda bal, wdd, start: 1000.0

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sure_thing":
            return fake_st
        if name == "contest_account":
            return fake_ca
        if name == "hindsight_scorer":
            return types.SimpleNamespace(brain_size_mult=lambda score: 0.50)
        return real_import(name, globals, locals, fromlist, level)

    real_import = __import__
    import builtins as _real_builtins
    _bi = {k: getattr(_real_builtins, k) for k in dir(_real_builtins)
           if not k.startswith("_")}
    _bi["__import__"] = _fake_import

    ns = {
        "__builtins__": _bi,
        "notify_big": lambda t, m, spoken="": cap["notify"].append((t, m, spoken)),
        "rotation_gate": lambda asset: (True, "", None, True),
        "lone_wolf_discount": lambda ex, pair: (0, ""),
        "_fleet_tide": lambda max_age_s=300: (
            cap.__setitem__("tide_reads", cap["tide_reads"] + 1)
            or (37.0, tide_box["state"])),
        "_popup_cap": lambda: 3,
        "_recent_fired_popups": lambda: 0,
        "knife_reversed_1h": lambda ex, pair, cfg: (False, None),
        "cfg": types.SimpleNamespace(trading_pairs=[]),
        "_deep_read": lambda ex, pair, cfg: (deep, "hold"),
        "_log_suppression": lambda pair, kind, why, **kw:
            cap["sups"].append((pair, kind)),
        "_climb_out_check": lambda *a, **k: {
            "ok": True, "output_block": "[climb]",
            "features": {"climb_room_pct": 2.0}},
        "_now_mst": lambda: _dt.now(_MST),
        "_fmt_mst": lambda d: d.strftime("%H:%M"),
        "timedelta": _td,
        "rotation_key_for_hour": lambda h: "08-12",
        "TIDE_FLAT_SIZE_FACTOR": bs.TIDE_FLAT_SIZE_FACTOR,
        "LONE_WOLF_MOVE_MIN": bs.LONE_WOLF_MOVE_MIN,
        "_tide_learn_bar": lambda: cap["learn_bar"],
        "tide_personal_bar": bs.tide_personal_bar,   # the REAL rebalanced helper (Sep 10)
        "_seam_releases_tide_out": lambda quick, deep, pair="", deep_fetcher=None:
            cap["seam_rel"],
        "_sure_thing_goal": lambda ex, pair: _fake_live_goal,
        "_danger_zone_panel": lambda *a, **k: "[panel]",
        "rotation_mark_fired": lambda asset: cap["rot"].append(asset),
        "_st_armed": armed,
        "_climb_features_stamped": bs._climb_features_stamped,
        "_log_signal_snapshot": cap["snaps"].append,
        "logger": bs.logger,
        "time": _time,
        "_voice_enabled": lambda: True,
        "_speak_server": lambda text, rate=2: cap["speak"].append(text),
        "all_pairs": ["XMR/USD"],
        "exchange": _Ex(),
    }
    exec(compile(_scan_body, "background_scanner.py", "exec"), ns)
    return ns["_sure_thing_scan"], cap, tide_box, armed, result_box


# scenario 1: the armed dedup + the vetoed coil's memory (the re-announce
# loop CodeRabbit caught — now graded on behavior, poll after poll)
scan1, cap1, _, armed1, box1 = _make_harness(_DeepRead("sell", 70), "RISING")
_fx = _Ex()
scan1(_fx)
check("runtime: an armed coil is SILENT (user rule Sep 8: the BUY is the first alert)",
      len(cap1["speak"]) == 0 and "XMR" in armed1,
      (len(cap1["speak"]), list(armed1)))
scan1(_fx)
check("runtime: the SAME zone never re-announces (poll 2 still silent)",
      len(cap1["speak"]) == 0, len(cap1["speak"]))
box1["r"] = dict(_ARMED, fire=True)   # the coil cracks; the deep scan says no
scan1(_fx)
check("runtime: the deep veto suppresses (no popup)", len(cap1["notify"]) == 0)
check("runtime: the veto is logged with its reason",
      any(kind == "sure-thing-deep-veto" for _, kind in cap1["sups"]), cap1["sups"])
check("runtime: the vetoed coil KEEPS its armed memory (the re-announce fix)",
      "XMR" in armed1, list(armed1))
scan1(_fx)
check("runtime: the poll after a veto: still silent, the veto logged again",
      len(cap1["speak"]) == 0 and len(cap1["sups"]) == 2,
      (len(cap1["speak"]), len(cap1["sups"])))

# scenario 2: the FLAT fire — half size, named in the popup, stamped in the snapshot
scan2, cap2, _, armed2, box2 = _make_harness(_DeepRead("buy", 60), "FLAT")
box2["r"] = dict(_ARMED, fire=True, trigger="pre-breakout")  # the coil, before the crack
scan2(_Ex())
check("runtime: the FLAT fire pops", len(cap2["notify"]) == 1)
check("runtime: the popup IS the Breakout Alert (title, line, voice, countdown)",
      bool(cap2["notify"])
      and cap2["notify"][0][0].startswith("\U0001f9f2 BREAKOUT ALERT")
      and "~45 MIN" in cap2["notify"][0][0]
      and cap2["notify"][0][1].startswith("BREAKOUT ALERT — BUY XMR — COUNTDOWN ~45 MIN")
      and cap2["notify"][0][2].startswith("Breakout alert!")
      and "countdown starts now" in cap2["notify"][0][2],
      cap2["notify"][0][0][:70] if cap2["notify"] else None)
_sn = cap2["snaps"]
check("runtime: the fired snapshot is written (route + kind + trigger)",
      len(_sn) == 1 and _sn[0]["route"] == "sure-thing"
      and _sn[0]["kind"] == "fired" and _sn[0]["trigger"] == "pre-breakout",
      _sn[0].get("route") if _sn else None)
check("runtime: FLAT sizes the fire to HALF (cap $1000 x 0.50 brain x 0.50 tide = $250)",
      bool(_sn) and abs(_sn[0]["spend"] - 250.0) < 1e-9,
      _sn[0]["spend"] if _sn else None)
check("runtime: the popup NAMES the FLAT factor (line + voice)",
      bool(cap2["notify"]) and "TIDE SIZES IT: FLAT" in cap2["notify"][0][1]
      and "flat tide" in cap2["notify"][0][2].lower(),
      cap2["notify"][0][1][:60] if cap2["notify"] else None)
check("runtime: the snapshot's climb features carry the tide stamp",
      bool(_sn) and _sn[0]["climb"]["climb_tide"] == "FLAT",
      _sn[0]["climb"] if _sn else None)
check("runtime: the snapshot stamps est_min (the countdown data)",
      bool(_sn) and _sn[0]["est_min"] == 45,
      _sn[0].get("est_min") if _sn else None)
check("runtime: the snapshot stamps the per-fire goal — the LIVE market pick, not the engine constant",
      bool(_sn) and abs(_sn[0]["goal_pct"] - _fake_live_goal) < 1e-9
      and abs(_sn[0]["goal_pct"] - _fake_goal) > 1e-9,
      _sn[0].get("goal_pct") if _sn else None)
check("runtime: the tide is read FRESH at fire time (not one funnel read)",
      cap2["tide_reads"] >= 2, cap2["tide_reads"])
check("runtime: the rotation marks fired and the armed watch clears",
      cap2["rot"] == ["XMR"] and "XMR" not in armed2,
      (cap2["rot"], list(armed2)))

# scenario 4: the OUT tide is a PRICE now (Sep 10) — the breakout
# watch pays the learned bar or the journal release, and a buried coil
# leaves a LOG (the old silent continue hid coil deaths)
scan4, cap4, _, armed4, box4 = _make_harness(_DeepRead("buy", 60), "OUT")
cap4["learn_bar"] = (92.0, "learned OUT bar 92 (payers 11.0% over 474h)")
box4["r"] = dict(_ARMED, fire=True)
scan4(_Ex())
check("runtime: an OUT-tide fire below the learned bar is suppressed — and LOGGED",
      len(cap4["notify"]) == 0 and any(k == "tide-out" for _, k in cap4["sups"]),
      (len(cap4["notify"]), cap4["sups"]))
cap4["seam_rel"] = (True, "the discipline journal opened the tide gate")
scan4(_Ex())
check("runtime: the journal release pops the OUT-tide fire",
      len(cap4["notify"]) == 1 and cap4["rot"] == ["XMR"],
      (len(cap4["notify"]), cap4["rot"]))
check("runtime: the OUT popup is the Breakout Alert — NOW",
      bool(cap4["notify"])
      and "BREAKOUT ALERT — BUY XMR — NOW" in cap4["notify"][0][1],
      cap4["notify"][0][1][:60] if cap4["notify"] else None)

# scenario 3: the RISING fire — full size, no FLAT line
scan3, cap3, _, _, box3 = _make_harness(_DeepRead("buy", 60), "RISING")
box3["r"] = dict(_ARMED, fire=True)
scan3(_Ex())
check("runtime: RISING pays FULL size ($500 — no tide factor)",
      bool(cap3["snaps"]) and abs(cap3["snaps"][0]["spend"] - 500.0) < 1e-9,
      cap3["snaps"][0]["spend"] if cap3["snaps"] else None)
check("runtime: no FLAT line on a RISING fire",
      bool(cap3["notify"]) and "TIDE SIZES IT: FLAT" not in cap3["notify"][0][1])
check("runtime: the crack popup is the Breakout Alert — NOW (no countdown)",
      bool(cap3["notify"])
      and cap3["notify"][0][0].startswith("\U0001f9f2 BREAKOUT ALERT")
      and "BREAKOUT ALERT — BUY XMR — NOW" in cap3["notify"][0][1]
      and cap3["notify"][0][2].startswith("Breakout alert!"),
      cap3["notify"][0][1][:60] if cap3["notify"] else None)

fails = [r for r in results if not r[1]]
out = BASE / "tide_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results) +
    (f"\n\nlive tide now: {pct and f'{pct:.0f}%'} {state or '(no data)'}"), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
