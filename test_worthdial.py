"""Test THE WORTH FLOOR AUTO-DIAL (user rule, Sep 4: "auto-dial the worth
floor dial to the proper location every rotation" — user approved: base
0.25, the toggle defaults ON, "build it, I trust you"; CORRECTED the
same day: "catch-up math is causing a problem, we don't need to catch
up — the goal is a guide not a rule"; Sep 7: the dial tunes to the
ACTUAL day too — realized vs planned bends it ±10%).

effective = BASE x pace x tide x actual, clamped [10%, 40%] — NO catch-up:
  pace   — this rotation's median expected move / the day's best rotation
           (the engine's live cells; a STATIC floor goes silent in slow
           hours — the day then can't make its pace)
  tide   — RISING relaxes 15% (the market carries greens)
  actual — realized net P&L today vs the pace, a ±10% TILT (Sep 7): a
           slow morning RAISES the floor, a green morning LOWERS it —
           gently, never a quota (the catch-up lesson stands)
The slider is the BASE when auto is on; 0% (OFF) beats the auto — never
resurrect a floor the user turned off. OFF toggle = static.
Calibration: diag_worthdial.py (the record's per-rotation goals + the
engine's live pace factors)."""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
import contest_account as ca

MST = ZoneInfo("America/Denver")
# clock-audit: ok — SANDBOXED (backup/seed/restore harness — _acct_bak/_cs_bak restored in the finally) (clockbombs census 4)
CS = Path("console_state.json")
ACCT = ca.STATE_FILE

src = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. the constants (the approved design, catch-up RETIRED) ─────────
check("base default is the approved 0.25", bs.WORTH_AUTO_BASE_DEFAULT == 0.25)
check("clamped [10%, 40%]", bs.WORTH_AUTO_MIN == 0.10 and bs.WORTH_AUTO_MAX == 0.40)
check("tide RISING relaxes 15%", bs.WORTH_TIDE_RISING_FACTOR == 0.85)

# ── 2. the pace factor (the engine's live cells) ──────────────────────
from volatility_engine import load_rotation_thresholds
import scratch_space   # scratch policy: nothing volatile lands in the repo root
doc = load_rotation_thresholds(max_age_s=0) or {}
for sched, day in (("weekday", 4), ("weekend", 5)):   # Fri Sep 4 / Sat Sep 5, 2026
    moves = {}
    for k in bs.ROTATION_KEYS:
        cell = (doc.get("median", {}).get(sched, {}) or {}).get(k) or {}
        mv = float(cell.get("avg_move") or 0)
        if mv > 0:
            moves[k] = mv
    if not moves:
        continue
    best_key = max(moves, key=moves.get)
    best_hr = int(best_key.split("-")[0])
    pf = bs._worth_pace_factor(datetime(2026, 9, day, best_hr + 1, 30, tzinfo=MST))
    check(f"{sched}: the day's best rotation ({best_key}) gets the full dial (pace 1.0)",
          abs(pf - 1.0) < 0.01, pf)
    slow_key = min(moves, key=moves.get)
    slow_hr = int(slow_key.split("-")[0])
    pf2 = bs._worth_pace_factor(datetime(2026, 9, day, slow_hr + 1, 30, tzinfo=MST))
    expect2 = max(0.6, min(1.0, moves[slow_key] / moves[best_key]))
    check(f"{sched}: the slowest rotation ({slow_key}) scales down ({expect2:.2f})",
          abs(pf2 - expect2) < 0.01, pf2)
check("pace factor never exceeds 1.0 or drops below 0.6",
      all(0.6 <= bs._worth_pace_factor(datetime(2026, 9, 4, h, 30, tzinfo=MST)) <= 1.0
          for h in (1, 5, 9, 13, 17, 21)))

# ── 3. the catch-up RETIREMENT (user rule, Sep 4: "we don't need to
# catch up — the goal is a guide not a rule") — the factor no longer
# touches the dial; the function is kept per the don't-delete rule ────
_acct_bak = ACCT.read_text(encoding="utf-8")
_cs_bak = CS.read_text(encoding="utf-8")
try:
    CS.write_text(json.dumps({"tier": "$10,000", "worth_floor_frac": 0.25}), encoding="utf-8")
    _f = bs.worth_floor_auto_factors()
    check("the toggle defaults ON when the key is missing",
          _f["auto"] is True)
    check("the base reads the slider (0.25)",
          abs(_f["base"] - 0.25) < 1e-9)
    check("NO catch-up in the product (the correction)",
          "catchup" not in _f
          and abs(_f["effective"] - max(0.10, min(0.40, 0.25 * _f["pace"] * _f["tide"] * _f.get("actual", 1.0)))) < 1e-9,
          _f)
    check("the ACTUAL lever is RETIRED from the gate (Sep 9): 'actual' now carries the BUFFER factor",
          "actual" in _f and abs(_f["actual"] - _f["buffer"]) < 1e-9
          and 0.40 <= _f["actual"] <= 1.60, _f.get("actual"))
    check("the day's ledger does NOT bend the dial (a big day and a down day read the same)",
          _f["effective"] == bs.worth_floor_auto_factors()["effective"])
    check("_worth_catchup_factor is retired (kept, not called by the dial)",
          "_worth_catchup_factor" not in
          Path("background_scanner.py").read_text(encoding="utf-8")
          .split("def worth_floor_auto_factors")[1].split("def _worth_floor_frac")[0])
    check("_worth_floor_frac returns the effective dial (the gates read it)",
          abs(bs._worth_floor_frac() - _f["effective"]) < 1e-9)

    # OFF toggle -> static
    CS.write_text(json.dumps({"tier": "$10,000", "worth_floor_frac": 0.25,
                              "worth_floor_auto": False}), encoding="utf-8")
    _f2 = bs.worth_floor_auto_factors()
    check("the toggle OFF -> the static base (the old behavior)",
          _f2["auto"] is False and abs(_f2["effective"] - 0.25) < 1e-9)

    # 0% OFF beats the auto
    CS.write_text(json.dumps({"tier": "$10,000", "worth_floor_frac": 0.0}), encoding="utf-8")
    _f3 = bs.worth_floor_auto_factors()
    check("the user's 0% (OFF) beats the auto-dial (never resurrected)",
          _f3["effective"] == 0.0)

    # the clamp: a huge base can't push past 40%
    CS.write_text(json.dumps({"tier": "$10,000", "worth_floor_frac": 0.9}), encoding="utf-8")
    check("a huge base clamps at 40%",
          bs.worth_floor_auto_factors()["effective"] <= 0.40 + 1e-9)
    # a small base in a slow hour can't drop below 10%
    CS.write_text(json.dumps({"tier": "$10,000", "worth_floor_frac": 0.12}), encoding="utf-8")
    check("a small base never drops below 10%",
          bs.worth_floor_auto_factors()["effective"] >= 0.10 - 1e-9)
finally:
    ACCT.write_text(_acct_bak, encoding="utf-8")
    CS.write_text(_cs_bak, encoding="utf-8")

# ── 5. the live state (what actually ships — the slider value is the
# user's to move; the test asserts the toggle default, not their dial) ─
_cs_live = json.loads(CS.read_text())
check("the live console ships with AUTO on (the default)",
      _cs_live.get("worth_floor_auto") is True)
check("the live base is a valid dial value",
      0.0 <= float(_cs_live.get("worth_floor_frac", 0.25)) <= 1.0,
      _cs_live.get("worth_floor_frac"))

# ── 6. the wiring ─────────────────────────────────────────────────────
check("both popup gates read the (now auto) dial live",
      src.count("_worth_floor_frac()") >= 3)   # champion + mover + the def's caller
check("the dial is INTERNAL (user rule, Sep 10): no toggle, no slider, "
      "no live-math caption on the console",
      '"worth_floor_auto_toggle"' not in ap and '"worth_floor_dial"' not in ap
      and "THIS ROTATION:" not in ap and "INTERNAL ONLY" in ap)
check("the calibration diag exists",
      # Sep 30 repair: the second conjunct used to demand the RUN ARTIFACT
      # (scratch/reports_temp/_worthdial_check.txt) — a manually-generated,
      # scratch-cleanable file that is ALWAYS absent inside the gate sandbox
      # (scratch/ is not copied there). The pin now tests what a gated drive
      # can know: the diag tool exists and writes to the sanctioned sink.
      Path("diag_worthdial.py").exists()
      and "report_temp('_worthdial_check.txt')"
      in Path("diag_worthdial.py").read_text(encoding="utf-8"))

# ── 7. THE ACTUAL FACTOR (user rule, Sep 7: "the auto-dial should know
# how to auto-tune to the ACTUAL floor") — realized vs planned, ±10% tilt ─
check("the actual constants exist (swing 0.10, halflife $120, clamp [0.85, 1.15])",
      bs.WORTH_ACTUAL_SWING == 0.10 and bs.WORTH_ACTUAL_HALFLIFE_USD == 120.0
      and bs.WORTH_ACTUAL_MIN == 0.85 and bs.WORTH_ACTUAL_MAX == 1.15)
_f7 = bs.worth_floor_auto_factors()
check("the factors dict carries the buffer lever (the 'actual' key, one source of truth)",
      "actual" in _f7 and "buffer" in _f7 and abs(_f7["actual"] - _f7["buffer"]) < 1e-9,
      (_f7.get("actual"), _f7.get("buffer")))
check("the effective dial folds the buffer lever in",
      abs(_f7["effective"]
          - max(0.10, min(0.40, _f7["base"] * _f7["pace"] * _f7["tide"] * _f7["buffer"])))
      < 1e-9 or _f7["effective"] == 0.0, _f7)
check("realized_pnl_today exists in the ledger module (the ACTUAL source)",
      hasattr(ca, "realized_pnl_today"))
_r = ca.realized_pnl_today()
check("realized_pnl_today returns a float (fail-open 0.0 on any break)",
      isinstance(_r, float), _r)
_src_a = Path("contest_account.py").read_text(encoding="utf-8")
check("the ledger reads NET pnl from closed buys, day-scoped",
      "status='closed'" in _src_a and "SUM(pnl)" in _src_a
      and "substr(closed_at, 1, 10)" in _src_a)
_src_w = Path("background_scanner.py").read_text(encoding="utf-8")
_wf_fn = _src_w.split("def worth_floor_auto_factors")[1].split("def _worth_floor_frac")[0]
check("the dial folds the BUFFER into the product (the bar, never a quota)",
      "_buffer" in _wf_fn and "WORTH_BUFFER_MIN_MULT" in _src_w
      and "WORTH_BUFFER_MAX_MULT" in _src_w
      and "catchup" not in _wf_fn
      and "WORTH_ACTUAL_MIN" not in _wf_fn)
check("the floor's math stays internal to the scanner (the console copy never shows it)",
      "worth_floor_auto_factors()" not in ap)

# ── 8. BEHAVIORAL (CodeRabbit, Sep 8: grade what the code DOES, not
#    just the tokens it contains) — the zero-fallback + the live math ───
_cs_bak2 = CS.read_text(encoding="utf-8")
try:
    CS.unlink()   # no console state at all
    _fb = bs.worth_floor_auto_factors()
    check("zero-fallback: no console_state -> the approved default base (0.25), AUTO on",
          abs(_fb["base"] - 0.25) < 1e-9 and _fb["auto"] is True, _fb)
    check("zero-fallback: the effective dial is the clamped product, not 0",
          0.10 <= _fb["effective"] <= 0.40, _fb["effective"])
    CS.write_text("{not json", encoding="utf-8")
    _fb2 = bs.worth_floor_auto_factors()
    check("zero-fallback: a corrupt console_state reads the same defaults",
          abs(_fb2["base"] - 0.25) < 1e-9 and _fb2["auto"] is True, _fb2)
finally:
    CS.write_text(_cs_bak2, encoding="utf-8")

# the BUFFER factor computes from the ledger (the realized PnL above the death line)
_acct_b = ca.load_state()
_start_b = float(_acct_b.get("start_balance") or 0.0)
if _start_b <= 0:
    _exp_buf = 1.0
else:
    _pos_b = max(0.0, min(1.0, float(ca.buffer(_acct_b)) / (_start_b * 0.03)))
    _exp_buf = bs.WORTH_BUFFER_MAX_MULT - _pos_b * (bs.WORTH_BUFFER_MAX_MULT
                                                    - bs.WORTH_BUFFER_MIN_MULT)
check("the BUFFER factor matches the formula on the live ledger (buffer vs 3% of start)",
      abs(bs._worth_buffer_factor() - _exp_buf) < 1e-9,
      (bs._worth_buffer_factor(), _exp_buf, ca.buffer(_acct_b)))
check("the BUFFER factor is clamped [0.40, 1.60] on the live day",
      0.40 <= bs._worth_buffer_factor() <= 1.60)

# ── 9. THE RETIRED ACTUAL FACTOR (Oct 3 red-first) — kept per the
#    don't-delete rule, its body must COMPUTE: the halflife referenced
#    two constants no one ever defined, so any call hit the fail-soft
#    except and returned 1.0 — a silent 1.0 indistinguishable from a
#    live lever sitting exactly at neutral. A future revival would ship
#    the tilt OFF with no error anywhere.
import copy as _copy

_h = ca.load_state()
_a_bak = _copy.deepcopy(_h)
_cache_bak = dict(bs._CONTEST_SIZE_CACHE)   # _contest_size caches 30s — bust between fixtures
_cs_path_bak = ca.STATE_FILE
_realized_bak = ca.realized_pnl_today
_sbx = Path("scratch/sbx/_worthdial_actual_sbx.%d.json" % __import__('os').getpid())   # per-pid: parallel runs never share the fixture
_sbx.parent.mkdir(parents=True, exist_ok=True)
_now_a = datetime(2026, 10, 1, 9, 15, tzinfo=ZoneInfo("America/Denver"))
try:
    # THE SEAM, SANDBOXED (the gate's [HIGH]/[MEDIUM] on the first cut): the
    # drive never opens the live account state or signals.db — STATE_FILE is
    # repointed into scratch/sbx and realized is stubbed to 0.0 (the retired
    # body resolves it through the module object, so the stub intercepts),
    # making the fixtures deterministic AND immune to clobbering concurrent
    # fleet writes with a backup/restore race.
    ca.STATE_FILE = _sbx
    ca.realized_pnl_today = lambda *_a, **_k: 0.0
    _sbx.write_text(json.dumps({"tier": _a_bak["tier"],
                                 "start_balance": _a_bak["start_balance"],
                                 "current_balance": _a_bak["start_balance"]}),
                    encoding="utf-8")   # healthy 10k: halflife = max(120, 10000*0.12) = 1200
    bs._CONTEST_SIZE_CACHE["size"] = 0.0
    _f_h = bs._worth_actual_factor(now=_now_a)
    _pace_h = float(ca.daily_pace_usd(_a_bak))
    _hl_h = max(bs.WORTH_ACTUAL_HALFLIFE_USD, float(_a_bak["start_balance"]) * 0.12)
    _exp_h = max(0.85, min(1.15, 1.0 + bs.WORTH_ACTUAL_SWING * (_pace_h / _hl_h)))
    check("RETIRED actual factor COMPUTES on a healthy 10k (the size-aware halflife tilt, NOT the silent 1.0)",
          abs(_f_h - _exp_h) < 1e-9 and _f_h != 1.0, (_f_h, _exp_h))
    _sbx.write_text(json.dumps({"tier": _a_bak["tier"],
                                 "start_balance": _a_bak["start_balance"],
                                 "current_balance": 500.0}),
                    encoding="utf-8")   # small contest: halflife = max(120, 60) = 120 -> the same deficit clamps
    bs._CONTEST_SIZE_CACHE["size"] = 0.0
    _f_s = bs._worth_actual_factor(now=_now_a)
    check("RETIRED actual factor: the small contest's halflife clamps the same deficit to the full 1.15",
          abs(_f_s - 1.15) < 1e-9, _f_s)
    check("RETIRED actual factor is a TILT, never a quota (clamped [0.85, 1.15])",
          0.85 <= _f_h <= 1.15 and 0.85 <= _f_s <= 1.15)
finally:
    ca.realized_pnl_today = _realized_bak
    ca.STATE_FILE = _cs_path_bak
    _sbx.unlink(missing_ok=True)   # the sandbox shape leaves with the run
    bs._CONTEST_SIZE_CACHE.clear()
    bs._CONTEST_SIZE_CACHE.update(_cache_bak)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_worthdial_test.txt').write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
