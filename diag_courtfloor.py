"""diag_courtfloor.py — THE COURT-FLOOR STUDY (Sep 17).

WHY THIS EXISTS: coin_floors.py learns each coin's relief from its
blocked-trade court, but the court's floor (BAND_LO = 72) was set by
judgment. Every blocked buy scoring below 72 vanishes from the
learner's evidence — uncounted, unlearned, unrelieved. This study
prices what that exclusion cost: one replay of every contest store
with a WIDER net ([40, 72) — 40 is the strategy's buy floor), each
trade graded forward through the popup contract, priced in the fleet's
ledger convention (position x the brain's ladder x the end-move, the
ran ceiling alongside).

The band structure is the answer's shape:
  - payers bunched in [70, 72) -> the cheap fix is lowering BAND_LO to
    70 (FLOOR_BAR_FLOOR already refuses bars below 70);
  - payers deep in [40, 60)   -> the court itself is the question.

Each trade carries its hour's ts so the study can also report the
RECENT windows (14d / 30d) — the all-time number and the live regime
can disagree, and the decayed learner should be judged by the regime
it actually faces. Monthly driver on the boot family: the exclusion
re-prices itself as the stores grow. Hermetic pins bound the replay
(max_pairs, sandbox stores, injected position).
"""
from __future__ import annotations

import glob
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

logger = logging.getLogger("courtfloor")

BASE = Path(__file__).parent
OUT = BASE / "_courtfloor_check.txt"
LEDGER = BASE / "_courtfloor_monthly.jsonl"
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"
POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
HORIZON = 4
WARMUP = 30
COURT_LO = 40.0         # the strategy's buy floor: nothing below can block
COURT_HI = 72.0         # BAND_LO — the learner's court starts here
BANDS = ((40.0, 50.0), (50.0, 60.0), (60.0, 70.0), (70.0, 72.0))
FIRST_DELAY_S = 3600.0
MONTHLY_PERIOD_S = 30 * 86400.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _position_usd() -> float:
    """Today's ledger (the live contest size) with the study's own
    fallback — the family convention."""
    try:
        import background_scanner as _bs
        _s = float(_bs._contest_size())
        if _s > 0:
            return _s
    except Exception as exc:
        logger.debug("live contest size unreadable (%s) — the fallback "
                     "$%.0f stands", exc, POSITION_USD_FALLBACK)
    return POSITION_USD_FALLBACK


def _blocked_below(path, cfg, lo: float = COURT_LO,
                   hi: float = COURT_HI) -> list:
    """One store -> the blocked buys the learner's court EXCLUDED:
    hours scoring a buy-conf in [lo, hi), consecutive runs collapsed to
    one trade, each graded end-move and ran (the popup contract) and
    stamped with its entry hour's ts. Same shape as the learner's
    replay, wider net."""
    import pandas as _pd
    from strategy import evaluate_strategy
    import coin_floors as _cf
    df = _pd.read_feather(path)
    df["date"] = _pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    trades = []
    if len(df) <= WARMUP + HORIZON + 10:
        return trades
    prev = False
    for i in range(WARMUP, len(df) - HORIZON):
        win = df.iloc[: i + 1]
        try:
            s = evaluate_strategy(win.copy(), cfg)
            conf = float(getattr(s, "confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        in_band = lo <= conf < hi
        if in_band:
            if not prev:
                entry = float(win["close"].iloc[-1])
                fwd = df.iloc[i + 1: i + 1 + HORIZON]
                end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
                ran = bool((float(fwd["high"].max()) / entry - 1.0) * 100.0
                           >= _cf.RAN_PCT)
                trades.append({"c": conf, "entry": entry, "end": round(end, 2),
                               "ran": ran,
                               "ts": float(df.index[i].timestamp())})
            prev = True
        else:
            prev = False
    return trades


def _price(trades: list, position_usd: float, now: float) -> dict:
    """The fleet ledger over a trade list: payers (ran, the popup
    contract), duds (end red), ledger dollars (end-move at the brain's
    ladder), the ran ceiling, and the recent-window splits."""
    from hindsight_scorer import brain_size_mult
    agg = {"n": 0, "payers": 0, "duds": 0, "ledger_usd": 0.0,
           "ran_ceiling_usd": 0.0, "w14": {"n": 0, "payers": 0,
                                           "ledger_usd": 0.0},
           "w30": {"n": 0, "payers": 0, "ledger_usd": 0.0}}
    for t in trades:
        size = position_usd * brain_size_mult(t["c"])
        usd = size * float(t["end"]) / 100.0
        agg["n"] += 1
        agg["ledger_usd"] += usd
        if t["ran"]:
            agg["payers"] += 1
            agg["ran_ceiling_usd"] += size * 2.0 / 100.0
        if float(t["end"]) <= 0:
            agg["duds"] += 1
        age_d = max(0.0, (now - float(t["ts"])) / 86400.0)
        for win, days in (("w14", 14.0), ("w30", 30.0)):
            if age_d <= days:
                agg[win]["n"] += 1
                agg[win]["ledger_usd"] += usd
                if t["ran"]:
                    agg[win]["payers"] += 1
    for k in ("ledger_usd", "ran_ceiling_usd"):
        agg[k] = round(agg[k], 2)
    for win in ("w14", "w30"):
        agg[win]["ledger_usd"] = round(agg[win]["ledger_usd"], 2)
    return agg


def run_study(max_pairs: int | None = None, stores_dir: Path | None = None,
              position_usd: float | None = None, now: float | None = None,
              write: bool = True) -> dict:
    """One full exclusion study across the contest stores. Bounds make
    it hermetic: max_pairs slices, stores_dir/position_usd injectable
    for the pins."""
    global POSITION_USD
    POSITION_USD = position_usd if position_usd is not None else _position_usd()
    from config import DEFAULT_CONFIG as cfg
    import coin_floors as _cf
    t_now = time.time() if now is None else float(now)
    glob_pattern = (str(stores_dir / "*_USD-1h.feather") if stores_dir
                    else str(BASE / STORES_GLOB))
    all_paths = sorted(glob.glob(glob_pattern))
    wanted = {str(p).replace("/", "_") for p in (cfg.trading_pairs or [])}
    paths = [p for p in all_paths
             if Path(p).name.replace("-1h.feather", "") in wanted] \
        if not stores_dir else all_paths
    bounded = max_pairs is not None and len(paths) > max_pairs
    if bounded:
        _step = max(1, len(paths) // max_pairs)
        paths = paths[::_step][:max_pairs]

    summary = {"ts": _utcnow_iso(), "pairs": len(paths),
               "position_usd": POSITION_USD, "bounded": bounded,
               "total": {"n": 0, "payers": 0, "duds": 0,
                         "ledger_usd": 0.0, "ran_ceiling_usd": 0.0},
               "bands": {}, "per_coin": {}}
    out = ["=== THE COURT-FLOOR STUDY: what the learner's 72-point band "
           "EXCLUDED (Sep 17) ===",
           f"net: blocked buys scoring [{COURT_LO:.0f}, {COURT_HI:.0f}) — "
           f"the court the learner never saw (40 = the strategy's buy "
           f"floor, 72 = BAND_LO)",
           f"position ${POSITION_USD:,.0f} x the brain's ladder | "
           f"horizon {HORIZON}h | ran = +{_cf.RAN_PCT:.0f}% MFE (the "
           f"popup contract) | ledger $ = the end-move",
           ""]

    per_coin = {}
    band_tot = {f"[{lo:.0f},{hi:.0f})": {"n": 0, "payers": 0,
                                         "ledger_usd": 0.0,
                                         "ran_ceiling_usd": 0.0}
                for lo, hi in BANDS}
    for path in paths:
        pair = Path(path).name.replace("-1h.feather", "")
        try:
            trades = _blocked_below(path, cfg)
        except Exception as exc:
            logger.debug("courtfloor: %s skipped (%s)", pair, exc)
            continue
        if not trades:
            continue
        pc = _price(trades, POSITION_USD, t_now)
        per_coin[pair] = pc
        for t in trades:
            for lo, hi in BANDS:
                if lo <= float(t["c"]) < hi:
                    key = f"[{lo:.0f},{hi:.0f})"
                    from hindsight_scorer import brain_size_mult as _bm
                    _u = POSITION_USD * _bm(t["c"]) * float(t["end"]) / 100.0
                    band_tot[key]["n"] += 1
                    band_tot[key]["ledger_usd"] = round(
                        band_tot[key]["ledger_usd"] + _u, 2)
                    if t["ran"]:
                        band_tot[key]["payers"] += 1
                        band_tot[key]["ran_ceiling_usd"] = round(
                            band_tot[key]["ran_ceiling_usd"]
                            + POSITION_USD * _bm(t["c"]) * 2.0 / 100.0, 2)
                    break
    for key, d in band_tot.items():
        summary["bands"][key] = d
        summary["total"]["n"] += d["n"]
        summary["total"]["payers"] += d["payers"]
        summary["total"]["ledger_usd"] = round(
            summary["total"]["ledger_usd"] + d["ledger_usd"], 2)
        summary["total"]["ran_ceiling_usd"] = round(
            summary["total"]["ran_ceiling_usd"] + d["ran_ceiling_usd"], 2)
    summary["per_coin"] = dict(sorted(
        per_coin.items(),
        key=lambda kv: -kv[1]["ledger_usd"])[:12])
    top = {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv)
               for kk, vv in v.items()} for k, v in
           summary["per_coin"].items()}
    summary["per_coin"] = top

    if write:
        out.append(f"stores replayed: {len(paths)}"
                   f"{' (BOUNDED SAMPLE)' if bounded else ''}")
        out.append("")
        out.append(f"{'BAND':<12}{'n':>6}{'payers':>8}{'ledger $':>12}"
                   f"{'ran-ceil $':>12}")
        for key, d in band_tot.items():
            out.append(f"{key:<12}{d['n']:>6}{d['payers']:>8}"
                       f"{d['ledger_usd']:>12,.2f}{d['ran_ceiling_usd']:>12,.2f}")
        tot = summary["total"]
        out.append("-" * 50)
        out.append(f"{'TOTAL':<12}{tot['n']:>6}{tot['payers']:>8}"
                   f"{tot['ledger_usd']:>12,.2f}{tot['ran_ceiling_usd']:>12,.2f}")
        out.append("")
        rate = (tot["payers"] / tot["n"] * 100.0) if tot["n"] else 0.0
        out.append(f"the excluded world: {tot['n']} blocked trades, "
                   f"{tot['payers']} payers ({rate:.0f}% ran) — "
                   f"ledger ${tot['ledger_usd']:+,.2f} end-move, "
                   f"${tot['ran_ceiling_usd']:,.2f} ran-ceiling")
        out.append("")
        out.append("recent windows (the regime the decayed learner faces):")
        # window aggregates ride the per-coin splits computed in _price
        wsum = {"w14": {"n": 0, "payers": 0, "ledger_usd": 0.0},
                "w30": {"n": 0, "payers": 0, "ledger_usd": 0.0}}
        for path2 in paths:
            pair2 = Path(path2).name.replace("-1h.feather", "")
            pc = per_coin.get(pair2)
            if not pc:
                continue
            for win in ("w14", "w30"):
                wsum[win]["n"] += pc[win]["n"]
                wsum[win]["payers"] += pc[win]["payers"]
                wsum[win]["ledger_usd"] = round(
                    wsum[win]["ledger_usd"] + pc[win]["ledger_usd"], 2)
        for win in ("w14", "w30"):
            d = wsum[win]
            out.append(f"  {win}: {d['n']} trades, {d['payers']} payers, "
                       f"ledger ${d['ledger_usd']:+,.2f}")
        out.append("")
        out.append("top coins by excluded ledger $:")
        for pair2, pc in list(top.items())[:8]:
            out.append(f"  {pair2:<12} {pc['n']:>4} trades, "
                       f"{pc['payers']} payers, ${pc['ledger_usd']:+,.2f}")
        scratch_space.report_temp('_courtfloor_check.txt').write_text("\n".join(out) + "\n", encoding="utf-8")
    _append(summary)
    return summary


def _append(rec: dict) -> None:
    try:
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as exc:
        logger.warning("courtfloor ledger append failed (%s)", exc)


def monthly_driver_loop(stop, first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S) -> None:
    """The scanner thread body: one pass shortly after boot, then
    monthly. Never raises; dies with the stop event."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("COURT-FLOOR: exclusion re-priced — %d trades, "
                        "%d payers, ledger $%.2f (next in %.0f days)",
                        s["total"]["n"], s["total"]["payers"],
                        s["total"]["ledger_usd"], period_s / 86400.0)
        except Exception as exc:
            logger.info("COURT-FLOOR: pass failed (%s) — this cycle "
                        "skips", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run_study()
    print("done ->", OUT.name)
