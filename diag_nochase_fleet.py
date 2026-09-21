"""diag_nochase_fleet.py — THE NO-CHASE RULE, GRADED ACROSS THE FLEET
(Sep 16). The monthly, fleet-wide form of diag_nochase.py.

WHY THIS EXISTS: the ZEC study (diag_nochase.py) proved the no-chase
ladder's honesty on ONE coin — the RSI ceiling never decided a trade
because the pullback gate refused first. But "which gate decides" is a
per-coin fact, and the funnel runs the ladder on every coin every hour.
This study replays EVERY 1h feather store through the same honest
counterfactual (the identical strategy with ONLY the RSI ceiling line
disabled) and prices, in dollars across the whole fleet: how much the
RSI refusals LEFT ON THE TABLE vs SAVED over each store's history.

CONVENTIONS (shared with the ZEC study):
  * horizon 4h (one rotation, the popup's hold style); position =
    the live contest size x brain_size_mult (the funnel's ladder).
  * decisive refusal = the real world holds on the RSI ceiling AND the
    gate-free world actually buys; consecutive refusals = one trade.
  * the census names which gate refused the gate-free world — if the
    pullback gate leads fleet-wide too, the ceiling stays a messenger,
    not a decider.

THE MONTHLY DRIVER: the no-chase ladder prices a RECURRING decision,
so the study re-prices itself monthly (one boot-seed pass ~40 min after
boot, then every 30 days), appending _nochasefleet_monthly.jsonl. A
pass is heavy (102 stores x the full-history replay) — it runs in its
own daemon thread and a broken pass never takes the funnel down.

Run: .venv\\Scripts\\python.exe -X utf8 diag_nochase_fleet.py
Writes _nochasefleet_check.txt.
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

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = BASE / "_nochasefleet_check.txt"
LEDGER = BASE / "_nochasefleet_monthly.jsonl"
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"
POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
HORIZON = 4          # hours — one rotation, the popup's hold style
WARMUP = 30
FIRST_DELAY_S = 2400.0             # first pass ~40 min after boot (heavy)
MONTHLY_PERIOD_S = 30 * 86400.0
logger = logging.getLogger("nochasefleet")


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


_CF = None


def _build_counterfactual():
    """strategy.py's exact source with ONLY the RSI ceiling line made
    unreachable, in a tempdir under its own module name. Built ONCE per
    process (the monthly loop reuses it — no tempdir pile-up)."""
    global _CF
    if _CF is not None:
        return _CF
    import strategy as real
    src = Path(real.__file__).read_text(encoding="utf-8")
    needle = "rsi_ceiling = 78.0 if trending_up else 68.0"
    assert src.count(needle) == 1, f"ceiling line not unique: {src.count(needle)}"
    patched = src.replace(
        needle,
        "rsi_ceiling = 200.0 if trending_up else 200.0  # COUNTERFACTUAL: the no-chase gate disabled")
    tmp = Path(tempfile.mkdtemp(prefix="nochase_fleet_"))
    (tmp / "strategy_nochase_fleet.py").write_text(patched, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    import strategy_nochase_fleet
    _CF = (real, strategy_nochase_fleet)
    return _CF


def _replay_pair(path, real, cf, cfg, brain_mult) -> dict | None:
    """One store through the honest counterfactual. Returns the pair
    summary (or None when the store is too short)."""
    import pandas as pd
    df = pd.read_feather(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    if len(df) <= WARMUP + HORIZON + 10:
        return None
    pair = Path(path).name.replace("-1h.feather", "")
    rows = []
    prev_decisive = False
    fired78 = fired68 = 0
    cf_blockers = {}
    for i in range(WARMUP, len(df) - HORIZON):
        win = df.iloc[: i + 1]
        s_real = real.evaluate_strategy(win.copy(), cfg)
        rsn = " | ".join(str(r) for r in (s_real.reasons or []))
        m78 = re.search(r"RSI [\d.]+ above 78\.0", rsn)
        m68 = re.search(r"RSI [\d.]+ above 68\.0", rsn)
        if m78:
            fired78 += 1
        if m68:
            fired68 += 1
        if s_real.action == "buy" or not (m78 or m68):
            prev_decisive = False
            continue
        s_cf = cf.evaluate_strategy(win.copy(), cfg)
        if s_cf.action != "buy" or float(s_cf.confidence or 0) < cfg.min_confidence:
            prev_decisive = False
            cfn = " | ".join(str(r) for r in (s_cf.reasons or []))
            if "waiting for pullback to EMA" in cfn:
                cf_blockers["pullback gate (price above EMA)"] = \
                    cf_blockers.get("pullback gate (price above EMA)", 0) + 1
            elif "falling knife" in cfn.lower() or "not a buy signal" in cfn.lower():
                cf_blockers["falling-knife gate"] = \
                    cf_blockers.get("falling-knife gate", 0) + 1
            elif s_cf.confidence is not None and float(s_cf.confidence) > 0 \
                    and float(s_cf.confidence) < cfg.min_confidence:
                cf_blockers["score below min_confidence"] = \
                    cf_blockers.get("score below min_confidence", 0) + 1
            else:
                cf_blockers["other dip-buy gate / no signal"] = \
                    cf_blockers.get("other dip-buy gate / no signal", 0) + 1
            continue
        if prev_decisive:
            continue
        prev_decisive = True
        entry = float(win["close"].iloc[-1])
        fwd = df.iloc[i + 1: i + 1 + HORIZON]
        end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
        mfe = (float(fwd["high"].max()) / entry - 1.0) * 100.0
        mae = (float(fwd["low"].min()) / entry - 1.0) * 100.0
        size = POSITION_USD * brain_mult(float(s_cf.confidence))
        rows.append({"end": end, "mfe": mfe, "mae": mae, "size": size,
                     "usd": size * end / 100.0,
                     "mfe_usd": size * mfe / 100.0})
    left = sum(r["usd"] for r in rows if r["usd"] > 0)
    saved = sum(-r["usd"] for r in rows if r["usd"] < 0)
    mfe_left = sum(r["mfe_usd"] for r in rows if r["mfe_usd"] > 0)
    return {"pair": pair, "hours": len(df), "fired78": fired78,
            "fired68": fired68, "decisive": len(rows),
            "left_usd": round(left, 2), "saved_usd": round(saved, 2),
            "mfe_left_usd": round(mfe_left, 2), "net_usd": round(left - saved, 2),
            "blockers": cf_blockers}


def run_study(max_stores: int | None = None) -> dict:
    """One full fleet pass. Writes the txt, appends the monthly record,
    returns the summary. Never raises past the writes. `max_stores`
    bounds the replay (the hermetic pins + smoke runs use 2-3 stores;
    the driver passes None for the full fleet)."""
    global POSITION_USD
    POSITION_USD = _position_usd()
    t_start = time.time()
    real, cf = _build_counterfactual()
    from config import DEFAULT_CONFIG as cfg
    from hindsight_scorer import brain_size_mult
    paths = sorted(glob.glob(str(BASE / STORES_GLOB)))
    bounded = max_stores is not None and len(paths) > max_stores
    if bounded:
        # deterministic slice: every Nth store so the sample spans the
        # alphabet instead of clumping on AAA*
        _step = len(paths) // max_stores
        paths = paths[::_step][:max_stores]
    out = []
    out.append("=== THE NO-CHASE RULE ACROSS THE FLEET (Sep 16) ===")
    out.append(f"stores: {len(paths)}{' (BOUNDED SAMPLE — the driver runs the full fleet)' if bounded else ''} "
               f"| horizon 4h | counterfactual = the "
               f"identical strategy with ONLY the RSI ceiling disabled")
    if not paths:
        out.append("no stores found — the study cannot run (the 5m/1h "
                   "builders have not populated surething_data/kraken)")
        summary = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
                   "pairs": 0, "hours_total": 0, "decisive": 0,
                   "left_usd": 0.0, "saved_usd": 0.0, "mfe_left_usd": 0.0,
                   "net_usd": 0.0, "position_usd": POSITION_USD}
        OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
        _append(summary)
        return summary

    per = []
    for p in paths:
        try:
            r = _replay_pair(p, real, cf, cfg, brain_size_mult)
        except Exception as exc:
            logger.info("nochase-fleet: %s skipped (%s)",
                        Path(p).name, exc)
            continue
        if r is not None:
            per.append(r)
    hours_total = sum(r["hours"] for r in per)
    decisive = sum(r["decisive"] for r in per)
    left = round(sum(r["left_usd"] for r in per), 2)
    saved = round(sum(r["saved_usd"] for r in per), 2)
    mfe_left = round(sum(r["mfe_left_usd"] for r in per), 2)
    net = round(left - saved, 2)
    oldest = min(os.path.getmtime(p) for p in paths)

    out.append(f"replayed: {len(per)} stores, {hours_total:,} store-hours "
               f"(pass took {(time.time() - t_start) / 60:.0f} min) | "
               f"position ${POSITION_USD:,.0f} x the brain's size ladder")
    out.append(f"store freshness: oldest feather "
               f"{datetime.fromtimestamp(oldest).strftime('%b %d %H:%M')} — "
               f"the window ends where the store builders last extended")
    out.append("")
    out.append(f"── THE CENSUS (the ceiling fired, the gate-free world refused) ──")
    agg = {}
    for r in per:
        for k, v in r["blockers"].items():
            agg[k] = agg.get(k, 0) + v
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        out.append(f"  {k}: {v} hour(s)")
    out.append("")
    out.append("── PER PAIR (sorted by |net of the rule|) ──")
    out.append(f"{'pair':<12} {'hours':>5} {'ceil78':>6} {'ceil68':>6} "
               f"{'decisive':>8} {'left$':>9} {'saved$':>9} {'net$':>9}")
    for r in sorted(per, key=lambda r: -abs(r["net_usd"])):
        out.append(f"{r['pair']:<12} {r['hours']:>5} {r['fired78']:>6} "
                   f"{r['fired68']:>6} {r['decisive']:>8} "
                   f"{r['left_usd']:>+9.2f} {r['saved_usd']:>9.2f} "
                   f"{r['net_usd']:>+9.2f}")
    out.append("")
    out.append("── THE FLEET HEADLINE ──")
    out.append(f"decisive refusals: {decisive} | LEFT ON THE TABLE "
               f"+{left:,.2f} USD | SAVED +{saved:,.2f} USD | NET {net:+,.2f} "
               f"USD (positive = loosening the ceiling would have PAID; "
               f"negative = the rule MAKES money fleet-wide)")
    out.append(f"mfe ceiling (always sell the top): +{mfe_left:,.2f} USD")
    out.append("")
    out.append("READ: the ZEC deep-dive (diag_nochase.py) stays the worked "
               "example; this fleet pass prices the same ladder on every "
               "coin — a decisive refusal HERE is where the RSI line is "
               "the decider, not the messenger.")

    summary = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
               "pairs": len(per), "hours_total": hours_total,
               "bounded": bounded,
               "decisive": decisive, "left_usd": left, "saved_usd": saved,
               "mfe_left_usd": mfe_left, "net_usd": net,
               "position_usd": POSITION_USD,
               "pass_min": round((time.time() - t_start) / 60.0, 1)}
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    _append(summary)
    return summary


def _append(summary: dict):
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("nochase-fleet ledger append failed (%s) — the txt "
                       "still carries the verdict", exc)


def main():
    s = run_study()
    print(f"fleet: {s['pairs']} stores, {s['decisive']} decisive refusals | "
          f"net {s['net_usd']:+,.2f} USD | ledger: {LEDGER.name}")


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one pass shortly after boot (the ledger record
    seeds the history), then monthly. Never raises; dies with the
    scanner. Each month's record prices the no-chase ladder on the
    stores AS THEY STAND — the window grows with the store extenders."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("NOCHASE-FLEET: monthly re-price — %d stores, "
                        "%d decisive, net %+.2f USD (%.0f min)",
                        s["pairs"], s["decisive"], s["net_usd"],
                        s.get("pass_min", 0.0))
        except Exception as exc:
            logger.info("NOCHASE-FLEET: pass failed (%s) — this month's "
                        "re-price skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    main()
