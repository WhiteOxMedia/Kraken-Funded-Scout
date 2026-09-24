"""diag_laddergrade.py — THE WHOLE NO-CHASE LADDER, GRADED RUNG BY RUNG
ACROSS ALL 54 CONTEST COINS (Sep 16). The fleet form of the no-chase
question (diag_nochase.py was ZEC; diag_nochase_fleet.py priced one
rung; this prices EVERY rung and retires the one-rung driver).

THE QUESTION (user): is any rung of the dip-buy ladder DEAD WEIGHT
(never decides a trade) or DECISIVELY COSTLY (its refusals blocked
trades that would have paid)?

THE LADDER (strategy.py's dip-buy section — each rung HARD-RETURNS, so
every refusal hour is owned by exactly one rung, exclusive by
construction — the hold reason names the owner):
  1. RSI ceiling    `rsi_ceiling = 78.0 if trending_up else 68.0`  (L892)
  2. pullback gate  `price > ema_short * 1.015`                    (L912)
  3. falling knife  `trending_down and macd_hist <= 0`             (L930)
  4. score floor    `buy_score >= config.min_confidence`           (L1119)
     — disabled by a cfg override (min_confidence=0), no patching.

THE HONEST COUNTERFACTUAL (one rung removed, nothing else): the exact
strategy source with ONLY that rung's line made unreachable (tempdir
module), or the cfg with min_confidence=0 for the floor. A rung's
"decisive refusals" = hours where the real world HELD on that rung AND
the rung-free world actually buys (>= min_confidence). Consecutive
refusal hours = one trade. Dollars: live contest size x
brain_size_mult(the counterfactual's confidence); the trade pays the
4h end move (one rotation, the popup's hold style); mfe is the
sell-the-top ceiling.

VERDICTS PER RUNG (fleet-wide):
  DEAD WEIGHT     decisive 0 everywhere — the rung never decides;
                  other rungs catch every refusal first.
  COSTLY          net > 0 — the trades it blocked would have PAID.
  EARNING ITS KEEP net < 0 — the trades it blocked would have LOST.

THE MONTHLY DRIVER: the ladder prices a RECURRING decision, so the
study re-prices itself monthly (boot-seed pass ~45 min after boot, then
every 30 days), appending _laddergrade_monthly.jsonl. Heavy (~30 min
for 54 stores) — its own daemon thread; a broken pass never takes the
funnel down. SUPERSEDES diag_nochase_fleet's single-rung driver.

Run: .venv\\Scripts\\python.exe -X utf8 diag_laddergrade.py
Writes _laddergrade_check.txt.
"""
import glob
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = scratch_space.report_temp("_laddergrade_check.txt")   # scratch policy
LEDGER = BASE / "_laddergrade_monthly.jsonl"
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"
POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
HORIZON = 4
WARMUP = 30
FIRST_DELAY_S = 2700.0             # first pass ~45 min after boot (heavy)
MONTHLY_PERIOD_S = 30 * 86400.0
logger = logging.getLogger("laddergrade")

# ── THE RUNG REGISTRY (the ladder's anatomy, source-pinned) ──────────
RUNGS = [
    {"key": "rsi", "name": "RSI ceiling (78 uptrend / 68 dip)",
     "needle": "rsi_ceiling = 78.0 if trending_up else 68.0",
     "patched": "rsi_ceiling = 200.0 if trending_up else 200.0  # COUNTERFACTUAL: RSI ceiling disabled",
     "match": lambda rsn: bool(re.search(r"RSI [\d.]+ above (78|68)\.0", rsn))},
    {"key": "pullback", "name": "pullback gate (price > 1.5% above EMA)",
     "needle": "if ema_short_val and price and price > ema_short_val * 1.015:",
     "patched": "if False:  # COUNTERFACTUAL: pullback gate disabled",
     "match": lambda rsn: "waiting for pullback to EMA" in rsn},
    {"key": "knife", "name": "falling knife (downtrend, no MACD turn)",
     "needle": "if trending_down and (macd_hist_now is None or macd_hist_now <= 0):",
     "patched": "if False:  # COUNTERFACTUAL: falling-knife gate disabled",
     "match": lambda rsn: "not catching a falling knife" in rsn},
]
# rung 4 (score floor) is a cfg override, no patch:
SCORE_RUNG = {"key": "score", "name": "score floor (min_confidence)"}

_CFS = None   # {key: (module|None, cfg_override|None)} — built once


def _position_usd() -> float:
    """Today's ledger (the live contest size) with the study's own
    fallback — same convention as the clamp-cost driver."""
    try:
        import background_scanner as _bs
        _s = float(_bs._contest_size())
        if _s > 0:
            return _s
    except Exception as exc:
        logger.debug("live contest size unreadable (%s) — the fallback "
                     "$%.0f stands", exc, POSITION_USD_FALLBACK)
    return POSITION_USD_FALLBACK


def _build_counterfactuals():
    """One patched module per module-rung (tempdir, own names), built
    ONCE per process; the score rung rides a cfg copy."""
    global _CFS
    if _CFS is not None:
        return _CFS
    import strategy as real
    src = Path(real.__file__).read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="laddergrade_"))
    sys.path.insert(0, str(tmp))
    cfs = {}
    for i, r in enumerate(RUNGS):
        assert src.count(r["needle"]) == 1, \
            f"rung line not unique ({r['key']}): {src.count(r['needle'])}"
        name = f"strategy_norung_{i}_{r['key']}"
        (tmp / f"{name}.py").write_text(
            src.replace(r["needle"], r["patched"]), encoding="utf-8")
        cfs[r["key"]] = (getattr(__import__(name), "evaluate_strategy"), None)
    import dataclasses
    from config import DEFAULT_CONFIG as _cfg0
    cfg_free = dataclasses.replace(_cfg0, min_confidence=0.0)
    cfs[SCORE_RUNG["key"]] = (real.evaluate_strategy, cfg_free)
    _CFS = (real, cfs)
    return _CFS


def _replay_pair(path, real, cfs, cfg, brain_mult) -> dict | None:
    """One store through the shared-real ladder grade. The real world is
    evaluated ONCE per hour; each rung's counterfactual only on the
    hours that rung owned the refusal."""
    import pandas as pd
    df = pd.read_feather(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    if len(df) <= WARMUP + HORIZON + 10:
        return None
    pair = Path(path).name.replace("-1h.feather", "")
    rungs = {r["key"]: {"refusals": 0, "prev": False, "trades": [],
                        "fired": 0} for r in RUNGS}
    rungs[SCORE_RUNG["key"]] = {"refusals": 0, "prev": False, "trades": [],
                                "fired": 0}
    for i in range(WARMUP, len(df) - HORIZON):
        win = df.iloc[: i + 1]
        s_real = real.evaluate_strategy(win.copy(), cfg)
        if s_real.action == "buy":
            for st in rungs.values():
                st["prev"] = False
            continue
        if s_real.action == "sell":
            # a SELL verdict is the ladder's own opposite signal, not a
            # blocked BUY — no rung's court owns it
            for st in rungs.values():
                st["prev"] = False
            continue
        rsn = " | ".join(str(r) for r in (s_real.reasons or []))
        owner = None
        for r in RUNGS:
            if r["match"](rsn):
                owner = r["key"]
                break
        if owner is None:
            owner = SCORE_RUNG["key"]   # the residual holds: the floor's court
        st = rungs[owner]
        st["refusals"] += 1
        # consecutive refusal hours = one trade
        if st["prev"]:
            continue
        mod_eval, cfg_ovr = cfs[owner]
        s_cf = mod_eval(win.copy(), cfg_ovr or cfg)
        if s_cf.action != "buy" or float(s_cf.confidence or 0) <= 0 \
                or float(s_cf.confidence) < (cfg_ovr or cfg).min_confidence:
            st["prev"] = False
            continue
        st["prev"] = True
        entry = float(win["close"].iloc[-1])
        fwd = df.iloc[i + 1: i + 1 + HORIZON]
        end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
        mfe = (float(fwd["high"].max()) / entry - 1.0) * 100.0
        size = POSITION_USD * brain_mult(float(s_cf.confidence))
        st["trades"].append({"end": end, "mfe": mfe,
                             "usd": size * end / 100.0,
                             "mfe_usd": size * mfe / 100.0})
    out = {"pair": pair, "hours": len(df)}
    for k, st in rungs.items():
        left = sum(t["usd"] for t in st["trades"] if t["usd"] > 0)
        saved = sum(-t["usd"] for t in st["trades"] if t["usd"] < 0)
        out[k] = {"fired": st["refusals"], "decisive": len(st["trades"]),
                  "left_usd": round(left, 2), "saved_usd": round(saved, 2),
                  "mfe_left_usd": round(sum(t["mfe_usd"] for t in st["trades"]
                                            if t["mfe_usd"] > 0), 2),
                  "net_usd": round(left - saved, 2)}
    return out


def run_study(max_pairs: int | None = None) -> dict:
    """One full ladder grade. Writes the txt, appends the monthly
    record, returns the summary. `max_pairs` bounds the replay
    (hermetic pins + smoke runs); the driver passes None for the 54."""
    global POSITION_USD
    POSITION_USD = _position_usd()
    t_start = time.time()
    real, cfs = _build_counterfactuals()
    from config import DEFAULT_CONFIG as cfg
    from hindsight_scorer import brain_size_mult
    all_paths = sorted(glob.glob(str(BASE / STORES_GLOB)))
    # the 54 contest coins only — cfg.trading_pairs is THE contest list
    wanted = {str(p).replace("/", "_") for p in cfg.trading_pairs}
    paths = [p for p in all_paths
             if Path(p).name.replace("-1h.feather", "") in wanted]
    bounded = max_pairs is not None and len(paths) > max_pairs
    if bounded:
        _step = max(1, len(paths) // max_pairs)
        paths = paths[::_step][:max_pairs]

    out = []
    out.append("=== THE NO-CHASE LADDER, RUNG BY RUNG, 54 COINS (Sep 16) ===")
    out.append(f"stores: {len(paths)} of the {len(wanted)} contest coins"
               f"{' (BOUNDED SAMPLE — the driver runs the 54)' if bounded else ''}"
               f" | horizon 4h | one rung removed at a time, nothing else")
    summary = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
               "pairs": 0, "rungs": {}, "position_usd": POSITION_USD,
               "bounded": bounded}
    if not paths:
        out.append("no contest stores found — the study cannot run")
        OUT.write_text("\n".join(out) + "\n", encoding="utf-8")   # OUT routes to scratch by default (tests may sandbox it)
        _append(summary)
        return summary

    rung_keys = [r["key"] for r in RUNGS] + [SCORE_RUNG["key"]]
    agg = {k: {"fired": 0, "decisive": 0, "left_usd": 0.0, "saved_usd": 0.0,
               "mfe_left_usd": 0.0, "pairs_decisive": 0} for k in rung_keys}
    per_rows = []
    for p in paths:
        try:
            r = _replay_pair(p, real, cfs, cfg, brain_size_mult)
        except Exception as exc:
            logger.info("laddergrade: %s skipped (%s)", Path(p).name, exc)
            continue
        if r is None:
            continue
        per_rows.append(r)
        for k in rung_keys:
            rk = r[k]
            agg[k]["fired"] += rk["fired"]
            agg[k]["decisive"] += rk["decisive"]
            agg[k]["left_usd"] += rk["left_usd"]
            agg[k]["saved_usd"] += rk["saved_usd"]
            agg[k]["mfe_left_usd"] += rk["mfe_left_usd"]
            if rk["decisive"]:
                agg[k]["pairs_decisive"] += 1
    hours_total = sum(r["hours"] for r in per_rows)

    out.append(f"replayed: {len(per_rows)} stores, {hours_total:,} "
               f"store-hours (pass took {(time.time() - t_start) / 60:.0f} "
               f"min) | position ${POSITION_USD:,.0f} x the brain's ladder")
    out.append("")
    out.append("── PER RUNG, FLEET-WIDE ──")
    out.append(f"{'rung':<46} {'refusals':>8} {'decisive':>8} "
               f"{'left$':>10} {'saved$':>10} {'net$':>10}  verdict")
    for r in RUNGS + [SCORE_RUNG]:
        k = r["key"]
        a = agg[k]
        net = round(a["left_usd"] - a["saved_usd"], 2)
        if a["decisive"] == 0:
            verdict = "DEAD WEIGHT (never decides)"
        elif net > 0:
            verdict = "COSTLY (its blocks would have paid)"
        else:
            verdict = "EARNING ITS KEEP (its blocks would have lost)"
        out.append(f"{r['name']:<46} {a['fired']:>8} {a['decisive']:>8} "
                   f"{a['left_usd']:>+10.2f} {a['saved_usd']:>10.2f} "
                   f"{net:>+10.2f}  {verdict}")
        summary["rungs"][k] = {**a, "net_usd": net, "verdict": verdict}
    out.append("")
    dec_pairs = [(r["pair"], k, r[k]) for r in per_rows for k in rung_keys
                 if r[k]["decisive"]]
    if dec_pairs:
        out.append("── THE DECISIVE REFUSALS (where the rung IS the decider) ──")
        for pair, k, rk in sorted(dec_pairs, key=lambda x: -abs(x[2]["net_usd"]))[:15]:
            rname = next((r["name"] for r in RUNGS if r["key"] == k),
                         SCORE_RUNG["name"])
            out.append(f"  {pair:<12} {rname}: {rk['decisive']} trade(s), "
                       f"net {rk['net_usd']:+.2f} USD")
    else:
        out.append("── THE DECISIVE REFUSALS ──")
        out.append("  none across the fleet: every rung's refusals were "
                   "re-refused by the rung-free world or by the score "
                   "floor — the ladder is a redundant mesh on this window.")
    out.append("")
    out.append("READ: DEAD WEIGHT here means 'no decisive refusal in this "
               "window' — the rung may still guard tail hours the census "
               "never sampled. COSTLY is the actionable verdict: its "
               "blocked trades would have paid. Retiring a rung is a "
               "user decision; this study only prices it.")

    summary["pairs"] = len(per_rows)
    summary["hours_total"] = hours_total
    summary["pass_min"] = round((time.time() - t_start) / 60.0, 1)
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")   # OUT routes to scratch by default (tests may sandbox it)
    _append(summary)
    return summary


def _append(summary: dict):
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("ladder-grade ledger append failed (%s) — the txt "
                       "still carries the verdict", exc)


def main():
    s = run_study()
    parts = " | ".join(f"{k}: {v['verdict'].split(' (')[0]} {v['net_usd']:+.2f}"
                       for k, v in s["rungs"].items())
    print(f"ladder grade: {s['pairs']} stores | {parts}")


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one pass shortly after boot (the ledger record
    seeds the history), then monthly. Never raises; dies with the
    scanner. Each month's record grades the ladder on the stores AS
    THEY STAND — the window grows with the store builders."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("LADDER-GRADE: monthly re-grade — %d stores | %s",
                        s["pairs"],
                        " | ".join(f"{k} {v['verdict'].split(' (')[0]} "
                                   f"{v['net_usd']:+.2f}"
                                   for k, v in s["rungs"].items()))
        except Exception as exc:
            logger.info("LADDER-GRADE: pass failed (%s) — this month's "
                        "re-grade skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    main()
