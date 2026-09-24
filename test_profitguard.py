"""test_profitguard.py — the adaptive profit guard (user spec, Sep 22).

HERMETIC first: the scanner seams (_fleet_tide, rotation_key_for_hour,
_now_mst) are stubbed before import, the thresholds/state files are
pointed into a tmp dir, and every modifier is pinned to fixed stubs so
each formula's arithmetic is computed, not read. LIVE probes of the real
book ride inside, clearly marked.
"""
import json
import math
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

FAILS = []
PASSES = []


def check(n, cond, why=""):
    (PASSES if cond else FAILS).append(n)
    print(f"  {'PASS' if cond else 'FAIL'} {n}: {why}")


_MST = timezone(timedelta(hours=-6))
HERE = Path(__file__).resolve().parent
import tempfile
TMP = Path(tempfile.mkdtemp(prefix="pgtmp_"))
TMP.mkdir(exist_ok=True)
(TMP / "profit_guard_state.json").unlink(missing_ok=True)   # no cross-run leaks

# ── stubs BEFORE the import ──────────────────────────────────────────────
bs = types.ModuleType("background_scanner")
bs._fleet_tide = lambda max_age_s=300: (78.0, "RISING")
bs.rotation_key_for_hour = lambda h: ["00-04", "04-08", "08-12", "12-16",
                                      "16-20", "20-24"][min(h // 4, 5)]
bs._now_mst = lambda: datetime.now(_MST)
bs.TIDE_RISING_MIN = 75.0
bs.TIDE_OUT_MAX = 35.0
sys.modules["background_scanner"] = bs

import profit_guard as pg

_ORIG_RS = pg.rotation_strength
_ORIG_RUN = pg._run_lengths
_ORIG_AM = pg.asset_memory
_ORIG_FACTS = pg._daily_facts

_REAL_ROOT = pg.ROOT
pg.ROOT = TMP                    # thresholds + state + trades all in tmp now
pg.STATE_FILE = TMP / "profit_guard_state.json"  # leak-audit: ok (suite-long redirect into the mkdtemp world; restore-never, suite-per-process)
(TMP / "rotation_thresholds.json").write_text(json.dumps({
    "median": {"weekday": {
        "08-12": {"avg_move": 2.4, "std": 1.5, "median_volume_usd": 60000,
                  "false_breakout_rate": 0.05},
        "16-20": {"avg_move": 0.8, "std": 0.9, "median_volume_usd": 4000,
                  "false_breakout_rate": 0.40}}}}), encoding="utf-8")


def _reset():
    pg._ENV_CACHE.update(ts=0.0, v=None)


# ════════ 1. EnvScore ════════
print("== 1. EnvScore = w_r * RS + w_t * TIDE ==")
_when = datetime(2026, 9, 22, 9, 0, tzinfo=_MST)          # weekday 08-12 cell
check(1.1, pg.rotation_strength(_when) == 5,
      "RotationStrength: 1 + 2.4/1.5 + liquidity + clean breakouts -> 5")
_when2 = datetime(2026, 9, 22, 17, 0, tzinfo=_MST)        # weekday 16-20 cell
check(1.2, pg.rotation_strength(_when2) == 1,
      "RotationStrength: thin liquidity + choppy breakouts clamp to 1")
check(1.3, pg.tide_score() == 5.0, "RISING tide scores 5")
bs._fleet_tide = lambda max_age_s=300: (20.0, "OUT")
check(1.4, pg.tide_score() == 0.0, "OUT tide scores 0")
bs._fleet_tide = lambda max_age_s=300: (60.0, "FLAT")
check(1.5, abs(pg.tide_score() - 3.25) < 1e-9,
      "FLAT interpolates breadth 60% -> 3.25 inside 2-4")
bs._fleet_tide = lambda max_age_s=300: (78.0, "RISING")
_reset()
check(1.6, abs(pg.env_score(_when, force=True) - 5.0) < 1e-9,
      "EnvScore = 0.6*5 + 0.4*5 = 5.0")
bs._fleet_tide = lambda max_age_s=300: (20.0, "OUT")
_reset()
check(1.7, abs(pg.env_score(_when, force=True) - 3.0) < 1e-9,
      "EnvScore with OUT tide = 0.6*5 (weights renormalized)")
bs._fleet_tide = lambda max_age_s=300: (_ for _ in ()).throw(RuntimeError("dead"))
pg.ROOT = HERE / "_pgtmp_nothresholds"
pg.ROOT.mkdir(exist_ok=True)
_reset()
check(1.8, pg.env_score(_when, force=True) is None,
      "both halves dead -> None (never an invented env)")

# ════════ 2. DiscMod ════════
print("== 2. DiscMod = 1 - penalty/10 ==")
check(2.1, pg.disc_mod_from_penalty(0) == 1.0, "penalty 0 -> 1.0")
check(2.2, pg.disc_mod_from_penalty(2.0) == 0.8, "penalty 2 -> 0.8 (typical)")
check(2.3, pg.disc_mod_from_penalty(10) == 0.0, "penalty 10 -> 0.0")
check(2.4, pg.disc_mod_from_penalty(12) == 0.0, "over-10 clamps to 0.0")
check(2.5, pg.disc_mod_from_penalty(None) is None, "None -> None (neutral)")

# ════════ 3. MLT_Corr + daily facts ════════
print("== 3. MLT_Corr = MoneyLeftOnTable / DailyGoal ==")
pg.ROOT = TMP
pg._daily_facts = lambda when=None: {"score": 80.0, "total_left": 30.0,
                                     "daily_pace": 60.0}
check(3.1, pg.money_left_on_table() == 30.0, "yesterday's left reads from the facts")
check(3.4, pg.discipline_penalty() == 2.0, "score 80 -> penalty 2.0")
check(3.5, abs(pg.disc_mod_from_penalty(pg.discipline_penalty()) - 0.8) < 1e-9,
      "DiscMod composes from the same facts (0.8)")
# THE ADAPTIVE GOAL (Sep 23): stubbed NEUTRAL for the raw pins above, then
# pinned scaling explicitly — the context multiplier folds into the pace
import carve_out as _co_pg
_gc_orig = _co_pg.goal_context
_gb_orig = _co_pg.goal_bounds
_co_pg.goal_bounds = lambda *a, **k: (0.0, 0.0)   # band neutral: the raw pins
                                                  # test the multiplier path,
                                                  # not the regime's band
_co_pg.goal_context = lambda when=None: (1.0, False)
check(3.2, pg.daily_goal_usd() == 60.0, "the pace reads from the facts")
check(3.3, abs(pg.mlt_corr() - 0.5) < 1e-9, "MLT_Corr = 30/60 = +0.5 (loosen)")
_co_pg.goal_context = lambda when=None: (1.2, True)
check(3.8, pg.daily_goal_usd() == 72.0,
      "the goal scales with the context (60 x 1.2 = 72)")
_co_pg.goal_context = lambda when=None: (1.0, False)
_co_pg.goal_bounds = lambda *a, **k: (0.0, 0.0)
check("3.8b", pg.daily_goal_usd() == 60.0,
      "a dark band restores the pace (no clamp applied)")
_co_pg.goal_context = lambda when=None: (1.4, True)
_co_pg.goal_bounds = lambda base, when=None, res=None: (round(base * 0.9, 2), round(base * 1.2, 2))
check("3.8c", abs(pg.daily_goal_usd() - 72.0) < 1e-9,
      "the pace clamps to the band ceiling (60x1.4=84 capped at 60x1.2=72)")
_co_pg.goal_context = lambda when=None: (1.1, True)
check("3.8d", abs(pg.daily_goal_usd() - 66.0) < 1e-9,
      "a pace inside the band passes through untouched (60x1.1=66)")
_co_pg.goal_context = _gc_orig
_co_pg.goal_bounds = _gb_orig
pg._daily_facts = lambda when=None: {"score": 100.0, "total_left": 0.0,
                                     "daily_pace": 0.0}
check(3.6, pg.mlt_corr() is None, "zero pace -> None (no divide-by-zero)")
pg._daily_facts = lambda when=None: None
check(3.7, pg.mlt_corr() is None and pg.discipline_penalty() is None
      and pg.money_left_on_table() is None,
      "unreadable facts -> every slow modifier None (honest)")

# ════════ 4. VolMod ════════
print("== 4. VolMod = CurrentVol / BaselineVol ==")
check(4.1, pg.vol_ratio("ZEC/USD") is None,
      "HERMETIC: no candle db in tmp -> None (neutral in product)")

# ── THE DIRECT WINDOW (Sep 23, user: "deepen the guard's direct candle
# window from 20 to 100"): a REAL sqlite fixture proves the floor moved
# WITH the window — 99 candles must answer None (a 20-floor would have
# answered here) and 100 must compute. The loud-tail ordering check is
# ATR-variant-proof: a louder last-15 block yields a strictly higher
# realized vol under Wilder, ewm, or rolling-mean smoothing alike.
import sqlite3 as _sq
import pandas as _pd


def _candle_db(rows, path):
    # the REAL cache's shape: vol_ratio filters WHERE pair=?
    cc = _sq.connect(path)
    cc.execute("DROP TABLE IF EXISTS ohlcv")   # the fixture rebuilds per case
    cc.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, high REAL, "
               "low REAL, close REAL, volume REAL)")
    cc.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)",
                   [("ZEC/USD",) + r for r in rows])
    cc.commit()
    cc.close()


def _rows(n, loud_tail=0):
    ts0 = 1_700_000_000
    out = []
    for i in range(n):
        if loud_tail and i >= n - loud_tail:      # the loud block: +-1 range
            out.append((ts0 + i * 300, 100.0, 101.0, 99.0, 100.0, 10.0))
        else:                                     # the quiet body: zero range
            out.append((ts0 + i * 300, 100.0, 100.0, 100.0, 100.0, 10.0))
    return out


_keys = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"]
(TMP / "rotation_thresholds.json").write_text(json.dumps(
    {"median": {"weekday": {k: {"std": 1.2} for k in _keys},
                "weekend": {k: {"std": 1.2} for k in _keys}}}), encoding="utf-8")
_db = TMP / "ohlcv_cache.db"
_candle_db(_rows(99), _db)
check(4.2, pg.vol_ratio("ZEC/USD") is None,
      "99 candles -> None: the floor moved WITH the window (a 20-floor "
      "would have answered here — this is the 20->100 pin)")
_candle_db(_rows(100), _db)
_quiet = pg.vol_ratio("ZEC/USD")
check(4.3, isinstance(_quiet, float) and _quiet >= 0.0,
      f"100 candles + a readable rotation cell -> a real ratio ({_quiet})")
_candle_db(_rows(100, loud_tail=15), _db)
_loud = pg.vol_ratio("ZEC/USD")
check(4.4, isinstance(_loud, float) and _loud > _quiet,
      f"a loud last-15 block prices STRICTLY higher than a quiet series "
      f"({_loud} > {_quiet}) — the current window's content drives the number")
check(4.5, pg.VOL_WINDOW == 100 and "LIMIT ?" in (HERE / "profit_guard.py").read_text(encoding="utf-8")
      and "params=(pair, VOL_WINDOW)" in (HERE / "profit_guard.py").read_text(encoding="utf-8")
      and "LIMIT 20" not in (HERE / "profit_guard.py").read_text(encoding="utf-8"),
      "the window is the VOL_WINDOW dial (100), bound as a parameter — "
      "no LIMIT 20 literal survives to drift apart from the floor")
_db.unlink(missing_ok=True)
(TMP / "rotation_thresholds.json").unlink(missing_ok=True)

# ════════ 5. AssetMemory ════════
print("== 5. AssetMemory = asset run / fleet run ==")
pg._run_lengths = lambda: {"ZEC": 200.0, "ADA": 100.0, "__fleet__": 160.0}
check(5.1, abs(pg.asset_memory("ZEC/USD") - 1.25) < 1e-9,
      "ZEC runs 200 vs fleet 160 -> 1.25 (wider net)")
check(5.2, abs(pg.asset_memory("ADA/USD") - 0.625) < 1e-9,
      "ADA runs 100 vs fleet 160 -> 0.625 (tighter net)")
check(5.3, pg.asset_memory("NOPE/USD") == 1.0,
      "unknown asset -> 1.0 neutral (absence is not unreadability)")
pg._run_lengths = lambda: {}
check(5.4, pg.asset_memory("ZEC/USD") is None, "no book at all -> None")

# ════════ 6. FundingMod ════════
print("== 6. FundingMod ==")
check(6.1, pg.funding_mod("NOPE/USD") == 1.0,
      "unknown/unavailable funding -> honest neutral 1.0")

# ════════ 7. DecayMod ════════
print("== 7. DecayMod = exp(-t/tau) ==")
w_fresh = {"added": datetime.now(_MST).isoformat()}
check(7.1, pg.decay_mod(w_fresh) == 1.0, "fresh position -> 1.0")
w_old = {"added": (datetime.now(_MST) - timedelta(minutes=90)).isoformat()}
check(7.2, pg.decay_mod(w_old) == 0.7, "90 min = one tau -> floored at 0.7")
w_mid = {"added": (datetime.now(_MST) - timedelta(minutes=30)).isoformat()}
check(7.3, abs(pg.decay_mod(w_mid) - math.exp(-1 / 3)) < 0.01,
      "30 min -> e^(-1/3) ~ 0.717 (continuous decay)")
check(7.4, pg.decay_mod({"added": "2026-09-22T08:55:24"}) >= 0.7,
      "naive legacy stamp handled (no crash, floored)")
check(7.5, pg.decay_mod(None) == 1.0, "no row -> 1.0")
check(7.6, pg.decay_mod({"opened_ts": None, "added": w_fresh["added"]}) == 1.0,
      "opened_ts preferred, added is the fallback")

# ════════ 8. the composite + trailing rules ════════
print("== 8. GuardFactor composite ==")
pg._daily_facts = lambda when=None: {"score": 100.0, "total_left": 0.0,
                                     "daily_pace": 60.0}
pg.rotation_strength = lambda when=None: 5
pg.asset_memory = lambda p: 1.0
pg._run_lengths = lambda: {}
pg._FUNDING_CACHE.clear()
pg._FUNDING_CACHE["ZEC/USD"] = (1.0, time.time())
pg.ROOT = TMP
_reset()
_strong = pg.guard_factor("ZEC/USD", w_fresh)
check(8.1, abs(_strong["factor"] - 5.0 / 3.0) < 0.01 and _strong["readable"],
      "everything strong: env 5 -> term 1.67, all else neutral -> 1.67 "
      "(>1.2: let it run, unclamped)")
pg._daily_facts = lambda when=None: {"score": 0.0, "total_left": 0.0,
                                     "daily_pace": 60.0}
_reset()
_weak0 = pg.guard_factor("ZEC/USD", w_fresh)
check(8.2, _weak0["factor"] == 0.5 and _weak0["readable"],
      "discipline 0: DiscMod 0 -> clamped 0.5 (tighten hard)")
pg._daily_facts = lambda when=None: {"score": 80.0, "total_left": 30.0,
                                     "daily_pace": 60.0}
pg.rotation_strength = lambda when=None: 1
bs._fleet_tide = lambda max_age_s=300: (20.0, "OUT")
_reset()
_mixed = pg.guard_factor("ZEC/USD", w_fresh)
check(8.3, _mixed["factor"] == 0.5,
      "weak env 0.6 -> term 0.2, x disc 0.8 x (1+0.5) = 0.24 -> clamped "
      "0.5 (<0.9: tighten hard, the clamp floor holds)")
check(8.4, abs(pg.lockin_pct(_mixed) * _mixed["factor"] - 1.0) < 0.01,
      "the binding holds: LockInPct * GF = BaseLockIn (armed cap = goal*ARM*GF)")
check(8.5, abs(pg.trail_pct(3.0, _mixed) - 3.0 * 0.5) < 0.01,
      "TrailPct = BaseTrailPct * GF (0.5 -> 1.5%: the running trail scales)")
pg.rotation_strength = _ORIG_RS      # the real fn — nothresholds ROOT -> None
pg._daily_facts = lambda when=None: None
bs._fleet_tide = lambda max_age_s=300: (_ for _ in ()).throw(RuntimeError("dead"))
pg.ROOT = HERE / "_pgtmp_nothresholds"
pg._FUNDING_CACHE["ZEC/USD"] = (1.0, time.time())
_reset()
_dead = pg.guard_factor("ZEC/USD", w_fresh)
check(8.6, _dead["readable"] is False and _dead["factor"] == 1.0,
      "loop halves dead, nothing stored -> readable False, neutral 1.0 "
      "(the committed fixed rules stand)")
check(8.7, pg.trail_pct(3.0, _dead) is None and pg.lockin_pct(_dead) is None,
      "an unreadable factor yields NO trail/lock-in override")
for f in (TMP / "profit_guard_state.json",):
    f.write_text(json.dumps({"adjusted_on": "2026-09-21", "factor": 1.3}),
                 encoding="utf-8")
_reset()
_carried = pg.guard_factor("ZEC/USD", w_fresh)
check(8.8, _carried["readable"] is True and _carried["factor"] == 1.3
      and _carried["carried_from"] == "2026-09-21",
      "loop dead but yesterday's factor stored -> carried (readable, 1.3)")

# ════════ 9. the daily self-adjustment loop ════════
print("== 9. step 10: the cooldown self-adjustment ==")
pg.ROOT = TMP
pg._daily_facts = lambda when=None: {"score": 90.0, "total_left": 15.0,
                                     "daily_pace": 60.0}
pg.rotation_strength = lambda when=None: 4
bs._fleet_tide = lambda max_age_s=300: (78.0, "RISING")
_reset()
(TMP / "profit_guard_state.json").unlink(missing_ok=True)
# THE FROZEN COOLDOWN CLOCK (hermetic by contract): self_adjust takes an
# explicit `now`, so the drive freezes it — the stamp's date can never
# flip with time of day (the metalayers shadow-bomb class).
from datetime import datetime as _dt, timezone as _tz
_ADJ_NOW = _dt(2026, 9, 22, 21, 0, tzinfo=_tz.utc)
_adj = pg.self_adjust(now=_ADJ_NOW)
check(9.1, _adj is not None and (TMP / "profit_guard_state.json").exists(),
      "first call adjusts and stamps the state file")
_st = json.loads((TMP / "profit_guard_state.json").read_text(encoding="utf-8"))
check(9.2, _st.get("adjusted_on") == "2026-09-22",
      "the stamp is the frozen clock's MST date (once per day)")
check(9.3, isinstance(_st.get("factor"), (int, float)),
      "GuardFactor stored for tomorrow's learning")
check(9.4, pg.self_adjust(now=_ADJ_NOW) is None,
      "second call same day -> None (idempotent)")
(TMP / "profit_guard_state.json").unlink(missing_ok=True)
pg._daily_facts = lambda when=None: None
pg.rotation_strength = _ORIG_RS      # real fn — the stub must not leak in
bs._fleet_tide = lambda max_age_s=300: (_ for _ in ()).throw(RuntimeError("dead"))
pg.ROOT = HERE / "_pgtmp_nothresholds"
_reset()
check(9.5, pg.self_adjust(now=_ADJ_NOW) is None
      and not (TMP / "profit_guard_state.json").exists(),
      "nothing readable -> no adjust, NO stamp (retries next cycle)")
(TMP / "profit_guard_state.json").write_text("garbage", encoding="utf-8")
check(9.6, pg.stored_factor() is None,
      "corrupted state reads as None (never crashes the caller)")
pg.ROOT = TMP
pg._daily_facts = lambda when=None: {"score": 90.0, "total_left": 15.0,
                                     "daily_pace": 60.0}
_reset()
_adj2 = pg.self_adjust(now=_ADJ_NOW)
check(9.7, _adj2 is not None,
      "corrupted state does not block a fresh adjustment (restart-proof)")

# ════════ 10. wiring census + LIVE probes ════════
print("== 10. wiring census + LIVE probes on the real book ==")
SRC = (HERE / "background_scanner.py").read_text(encoding="utf-8")
check(10.1, "import profit_guard as _pg" in SRC and "_pg.guard_factor(pair, w)" in SRC,
      "the exit watch computes the guard factor per position")
check(10.2, "ADAPTIVE GUARD" in SRC and "_pg.explain(" in SRC,
      "the fired GET OUT explains the factor in plain words")
check(10.3, "import profit_guard as _pgv" in SRC,
      "her voice computes the SAME stop the exit enforces (parity)")
check(10.4, "import profit_guard as _pgc" in SRC and "_pgc.self_adjust(now)" in SRC,
      "the 20:00 cooldown gate runs the daily self-adjustment")
check(10.5, '"opened_ts": _now_mst().isoformat()' in SRC,
      "auto-track rows carry opened_ts (the decay's clock)")
# scrub every stub and re-import REAL modules — the live probes must read
# the real book, not the section-1/8/9 stubs (the contamination the era
# drive taught us to check for)
pg.ROOT = _REAL_ROOT    # leak-audit: ok — the deliberate hand-back at the LIVE-probe section
# (Sep 23, the gate's first staged run) the LIVE probes read the REAL book
# from the module ROOT — but under the commit gate this drive runs from its
# scratch with the repo only as PYTHONPATH's fallback, so ROOT-as-HERE
# finds no contest_trades.jsonl and 10.7 fails against a healthy book.
# Resolve the real repo from PYTHONPATH (the first existing entry that
# holds the trades ledger); in-repo runs resolve HERE and behave exactly
# as before.
_REPO_ROOT = _REAL_ROOT
for _pp in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    # clock-audit: ok — LIVE-PROBE (existence-only walk for the real repo root; documented gate-scratch-safe) (clockbombs census 4)
    if _pp and (Path(_pp) / "contest_trades.jsonl").exists():
        _REPO_ROOT = Path(_pp)
        break
pg.ROOT = _REPO_ROOT
_RealState = _REPO_ROOT / "profit_guard_state.json"
_RealBytes = _RealState.read_bytes() if _RealState.exists() else None
# leak-audit: ok — the LIVE probe wants the REAL module state, and the
# tail restores it byte-exact (or scrubs what this run created)
pg.STATE_FILE = _RealState
sys.modules.pop("profit_guard", None)
sys.modules.pop("background_scanner", None)
pg_real = __import__("profit_guard")
__import__("background_scanner")          # the real seams for tide/rotation
# the re-import resolves ROOT to its own dir — under the gate that's the
# scratch again, so the fresh module is pointed at the real repo too
pg_real.ROOT = _REPO_ROOT
# leak-audit: ok — same _RealState as pg's LIVE swap above; the same
# tail restore (or scrub) covers pg_real's view of the file
pg_real.STATE_FILE = _RealState
_lres = pg_real.guard_factor("ZEC/USD", {"added": "2026-09-22T08:55:24-06:00"})
check(10.6, isinstance(_lres.get("factor"), (int, float)),
      "LIVE: the real book yields a factor")
print(f"        (live factor {_lres['factor']} readable={_lres['readable']} "
      f"env={_lres['env']} mlt={_lres['mlt_corr']} decay={_lres['decay_mod']})")
_rl = pg_real._run_lengths()
check(10.7, _rl.get("__fleet__", 0) > 0,
      "LIVE: fleet run length computes from contest_trades.jsonl")
print(f"        (fleet avg run {_rl.get('__fleet__', 0):.0f} min, "
      f"{sum(1 for k in _rl if k != '__fleet__')} assets)")
_lres_h = pg_real.guard_factor("ZEC/USD", {"added": "2026-09-22T08:55:24-06:00"})
check(10.8, pg_real.explain(_lres_h, "ZEC") and len(pg_real.explain(_lres_h, "ZEC")) > 10,
      "LIVE: the explain line is hearable")
print(f"        ({pg_real.explain(_lres_h, 'ZEC')})")
# (CodeRabbit major) the probe must never clobber the machine's real
# guard state: byte-exact restore. The 10.9 check only scrubs when this
# run CREATED the file — a pre-existing state file is the scanner's.
if _RealBytes is not None:
    _RealState.write_bytes(_RealBytes)
    print("        (real guard state restored byte-exact)")
    check(10.9, _RealState.exists(),
          "LIVE: the real guard state survived the probe untouched")
elif _RealState.exists():
    _RealState.unlink()
    print("        (probe-created state scrubbed — tonight's one-shot unstamped)")
    check(10.9, not _RealState.exists(),
          "LIVE: the probe-created one-shot stamp was scrubbed")
else:
    check(10.9, True, "LIVE: no state file existed — nothing to protect")

RESULT = f"{len(PASSES)} passed, {len(FAILS)} failed"
print(RESULT)
sys.exit(1 if FAILS else 0)
