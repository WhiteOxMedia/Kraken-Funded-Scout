"""diag_coinfloordelta.py — THE COIN-FLOOR DELTA (Sep 17).

WHY THIS EXISTS: coin_floors.py learns each coin's own score-floor
relief from its blocked trades — but learning is not pricing. This
study prices the DELTA the way the fleet prices everything else: one
replay of every contest store, two worlds side by side.

  WORLD G (the global floor): every blocked buy under the assembled
  floor (~84, the usual rail) stays blocked. Baseline dollars: 0.

  WORLD P (the per-coin floors): each coin's own earned relief applies
  — a blocked hour at the coin's personal bar (floor - edge, never
  below 70) enters the ledger at its real size (position x the brain's
  ladder) and grades end-move and ran (+2% MFE, the popup contract)
  over the popup's 4h horizon.

The delta is the answer in dollars: what the personal floors RELEASE
(payers admitted, their $), what they ADMIT (duds that bled), and the
net — the number the fleet weighs before trusting the relief. Shares
ONE set of learning semantics with the live learner (coin_floors.
_learn_from built the edges being priced) and ONE forward-outcome
contract with the ladder grade and the clamp-cost study. Monthly
driver: the relief re-prices itself as stores grow. Hermetic pins
bound the replay (max_pairs, injected floors, sandbox stores).
"""
from __future__ import annotations

import glob
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("coinfloordelta")

BASE = Path(__file__).parent
OUT = BASE / "_coinfloordelta_check.txt"
LEDGER = BASE / "_coinfloordelta_monthly.jsonl"
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"
POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
HORIZON = 4
WARMUP = 30
WORLD_BAR = 84.0        # the global world's assembled floor (the usual rail)
FIRST_DELAY_S = 3600.0  # after the learner's own first pass (~5 min boot + play)
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


def _floors_records() -> dict:
    """The learner's on-disk coins (what the funnel would actually
    apply). Empty dict = no relief learned = the delta is zero by
    construction (an honest study, not a fabricated one)."""
    try:
        import coin_floors
        return json.loads(coin_floors.FILE.read_text(encoding="utf-8")) \
            .get("coins") or {}
    except Exception as exc:
        logger.debug("floors records unreadable (%s)", exc)
        return {}


def _blocked_trades(path, cfg) -> list:
    """One store -> the blocked buys under WORLD_BAR inside the court
    band [72, 84): conf, entry, end-move %, ran (+2% MFE), collapsed
    to consecutive-hour trades. Same court the learner learned from."""
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
        in_band = (conf < WORLD_BAR and _cf.BAND_LO <= conf)
        if in_band:
            if not prev:
                entry = float(win["close"].iloc[-1])
                fwd = df.iloc[i + 1: i + 1 + HORIZON]
                end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
                ran = bool((float(fwd["high"].max()) / entry - 1.0) * 100.0
                           >= _cf.RAN_PCT)
                trades.append({"c": conf, "entry": entry, "end": end,
                               "ran": ran})
            prev = True
        else:
            prev = False
    return trades


def run_study(max_pairs: int | None = None, stores_dir: Path | None = None,
              floors: dict | None = None, world_bar: float = WORLD_BAR,
              position_usd: float | None = None) -> dict:
    """One full delta study. Writes the txt, appends the monthly
    record, returns the summary. Bounds make it hermetic: max_pairs
    slices the stores, floors/stores_dir/position_usd are injectable
    for the pins."""
    global POSITION_USD
    POSITION_USD = position_usd if position_usd is not None else _position_usd()
    from config import DEFAULT_CONFIG as cfg
    from hindsight_scorer import brain_size_mult
    import coin_floors as _cf
    recs = floors if floors is not None else _floors_records()
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

    summary = {"ts": _utcnow_iso(), "pairs": 0, "world_bar": world_bar,
               "position_usd": POSITION_USD, "relief_coins": 0,
               "released": {"n": 0, "payers": 0, "usd": 0.0,
                            "mfe_usd": 0.0},
               "duds": {"n": 0, "usd": 0.0}, "net_usd": 0.0,
               "per_coin": {}, "bounded": bounded}
    out = ["=== THE COIN-FLOOR DELTA: personal floors vs the global "
           "bar (Sep 17) ===",
           f"worlds: G = everything under {world_bar:.0f} stays blocked "
           f"(baseline $0) | P = each coin's earned relief applies "
           f"(bar - edge, never < {_cf.FLOOR_BAR_FLOOR:.0f})",
           f"position ${POSITION_USD:,.0f} x the brain's ladder | "
           f"horizon {HORIZON}h | ran = +{_cf.RAN_PCT:.0f}% MFE "
           f"(the popup contract)",
           f"relief coins on disk: "
           f"{sum(1 for c in recs.values() if float(c.get('edge') or 0) > 0)}"
           f" of {len(recs)} learned", ""]
    if not paths:
        out.append("no stores found — the study cannot run")
        OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
        _append(summary)
        return summary

    per = {}
    for path in paths:
        pair = Path(path).name.replace("-1h.feather", "")
        try:
            trades = _blocked_trades(path, cfg)
        except Exception as exc:
            logger.debug("coinfloordelta: %s skipped (%s)", pair, exc)
            continue
        edge = float((recs.get(pair) or {}).get("edge") or 0.0)
        if edge <= 0:
            continue          # no relief learned: G and P agree, $0 delta
        pbar = max(_cf.FLOOR_BAR_FLOOR, world_bar - edge)
        for t in trades:
            if t["c"] >= pbar:
                continue      # P still blocks it (above the personal bar)
            size = POSITION_USD * brain_size_mult(t["c"])
            usd = size * t["end"] / 100.0
            mfe_usd = size * _cf.RAN_PCT / 100.0 if t["ran"] else 0.0
            d = per.setdefault(pair, {"rel_n": 0, "rel_pay": 0,
                                      "rel_usd": 0.0, "rel_mfe_usd": 0.0,
                                      "dud_n": 0, "dud_usd": 0.0})
            if t["ran"]:
                d["rel_n"] += 1
                d["rel_pay"] += 1
                d["rel_usd"] += usd
                d["rel_mfe_usd"] += mfe_usd
            else:
                # released-but-not-ran: a payer only if it still ended
                # green (the MFE bar is the popup's contract, the end is
                # the honest ledger); duds are the admitted bleed
                if t["end"] > 0:
                    d["rel_n"] += 1
                    d["rel_usd"] += usd
                else:
                    d["dud_n"] += 1
                    d["dud_usd"] += usd
    # aggregate
    for pair, d in per.items():
        summary["released"]["n"] += d["rel_n"]
        summary["released"]["payers"] += d["rel_pay"]
        summary["released"]["usd"] += d["rel_usd"]
        summary["duds"]["n"] += d["dud_n"]
        summary["duds"]["usd"] += d["dud_usd"]
        d["net_usd"] = round(d["rel_usd"] + d["dud_usd"], 2)
        for k in ("rel_usd", "dud_usd", "rel_mfe_usd"):
            d[k] = round(d[k], 2)
    summary["released"]["mfe_usd"] = round(
        sum(d["rel_mfe_usd"] for d in per.values()), 2)
    summary["relief_coins"] = len(per)
    summary["pairs"] = len(paths)
    summary["per_coin"] = dict(sorted(per.items(),
                                      key=lambda kv: -kv[1]["net_usd"]))
    summary["net_usd"] = round(summary["released"]["usd"]
                               + summary["duds"]["usd"], 2)

    out.append(f"stores replayed: {len(paths)}"
               f"{' (BOUNDED SAMPLE — the driver runs the 54)' if bounded else ''}")
    out.append("")
    if not per:
        out.append("no earned relief in the floors file (or no store "
                   "hours inside any coin's court) — the delta is $0 by "
                   "construction; the learner's driver fills the file "
                   "and this study re-prices monthly.")
    else:
        out.append("── PER RELIEF COIN ──")
        out.append(f"{'coin':<12} {'edge':>5} {'released':>8} {'payers':>7} "
                   f"{'rel$':>10} {'duds':>6} {'dud$':>10} {'net$':>10}")
        for pair, d in summary["per_coin"].items():
            edge = float((recs.get(pair) or {}).get("edge") or 0.0)
            out.append(f"{pair:<12} {edge:>5.1f} {d['rel_n']:>8} "
                       f"{d['rel_pay']:>7} {d['rel_usd']:>+10.2f} "
                       f"({d['rel_mfe_usd']:>+8.2f} ceiling) "
                       f"{d['dud_n']:>6} {d['dud_usd']:>+10.2f} "
                       f"{d['net_usd']:>+10.2f}")
        out.append("")
        out.append(f"TOTAL: released {summary['released']['n']} trades "
                   f"({summary['released']['payers']} ran, ran-ceiling "
                   f"${summary['released']['mfe_usd']:+,.2f}), admitted "
                   f"{summary['duds']['n']} duds (${summary['duds']['usd']:+,.2f}) "
                   f"— NET ${summary['net_usd']:+,.2f} per window")
        # THE SAMPLE RUNG (Sep 20): the verdict is an ADVISORY — it
        # can tighten the relief band fleet-wide — so it answers to
        # the ladder's SAMPLE_BARS['COINFLOOR_DELTA'] rung. Below the
        # bar the graded reliefs still render (the numbers are the
        # record), but the verdict names its thinness instead of
        # advising on a sample one hot coin can dominate.
        try:
            from evidence_ladder import SAMPLE_BARS as _sb, \
                samples_mature as _sm
            _rung, _sm_ok = _sb["COINFLOOR_DELTA"], \
                _sm("COINFLOOR_DELTA", summary["released"]["n"])
        except Exception:
            _rung, _sm_ok = 12, summary["released"]["n"] >= 12
        _n_rel = summary["released"]["n"]
        verdict = ("THE DELTA IS MONEY: the personal floors admit "
                   "edge the global bar taxes"
                   if _sm_ok and summary["net_usd"] > 0 else
                   "THE RELIEF BLEEDS: the admitted duds outpay the "
                   "released payers — tighten the clamp or shrink the band"
                   if _sm_ok and summary["net_usd"] < 0 else
                   "WASH: the relief neither pays nor bleeds this window"
                   if _sm_ok else
                   f"TOO THIN TO VERDICT: {_n_rel} graded relief(s), the "
                   f"ladder's COINFLOOR_DELTA rung needs {_rung} — the "
                   "relief's net is information, not advice yet")
        out.append(f">> {verdict}")
        summary["verdict"] = verdict
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    _append(summary)
    return summary


def _append(summary: dict):
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("coin-floor delta ledger append failed (%s) — "
                       "the txt still carries the verdict", exc)


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S) -> None:
    """The daemon loop: one pass after the learner has filled its file,
    then monthly. Never raises; dies with the stop event."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("COIN-FLOOR DELTA: monthly re-price — %d relief "
                        "coin(s), net %+.2f | %s", s["relief_coins"],
                        s["net_usd"],
                        (s.get("verdict") or "no relief yet")
                        .split(":")[0])
        except Exception as exc:
            logger.info("COIN-FLOOR DELTA: pass failed (%s) — this "
                        "month's re-price skips, the ledger stands", exc)
        stop.wait(period_s)
