"""THE +2% TAKE-PROFIT: DECIDED (Nov 15).

One question, one number. The exit study showed the kept 0.85+ fires give back a
gain that was there (winners capture 42% of their peak) and that taking +2% turns
the ten-week window to break-even — but November, the only out-of-sample month,
was −$1.13. This joins every piece of evidence there is into a decision:

  * the Sep 3 – Nov 15 arms (baseline booked, the same exit engine without a
    target, and the +2% target) with fires, win rate, net, $/fire, worst fire and
    max drawdown, whole window and November;
  * the per-fire deltas, so the sample size needed to defend the effect is a
    number rather than a hope;
  * everything the live near-miss recorder has accumulated — how many rows exist,
    how many are resolved, and how many actually reached +2% (the only rows that
    can speak to a take-profit), backfilling the peak for rows resolved before the
    recorder stored it.

The bar is the agreed one: November out-of-sample positive with a sample large
enough to defend. Anything else is a rejection, stated with the count still needed.

Run: .venv/Scripts/python.exe -X utf8 scratch/take_profit_verdict.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import breakout_gate as bg                                  # noqa: E402
from entry_edge_study import load_tape                      # noqa: E402
from gate_counterfactual import clean_ledger, entry_quality, parse_ts  # noqa: E402
from kept_diagnosis import simulate, arm                    # noqa: E402
import near_miss as nm                                      # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
OUT = ROOT / "scratch" / "_take_profit_verdict.json"
TARGET = 2.0
MIN_SAMPLE = 30          # fires needed in November before a zero can be called anything


def dist(vals):
    if len(vals) < 2:
        return 0.0, 0.0
    return statistics.mean(vals), statistics.stdev(vals)


def main():
    tape = load_tape()
    raw = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    ledger, _ = clean_ledger(raw)
    kept = []
    for r in ledger:
        sym = (r.get("pair") or "").split("/")[0]
        bars = tape.get(sym)
        sold = parse_ts(r.get("sold_at") or "")
        try:
            held = float(r.get("held_min") or 0.0)
            px = float(r.get("entry") or 0.0)
            size = float(r.get("size") or 0.0)
            cost = float(r.get("cost_rt") or 0.0)
            goal = float(r.get("goal_pct") or 5.0)
        except (TypeError, ValueError):
            continue
        if bars is None or sold is None or held <= 0 or px <= 0 or size <= 0:
            continue
        opened = sold - held * 60.0
        if opened < bars[0][0] or opened > bars[-1][0]:
            continue
        eq = entry_quality(bars, opened, px)
        if eq is None or eq < bg.BREAKOUT_MIN:       # the fires today's gate keeps
            continue
        rec = dict(r)
        rec.update({"_t": opened, "_sym": sym, "_px": px, "_goal": goal,
                    "_cost_pct": 100.0 * cost / size})
        kept.append(rec)
    kept.sort(key=lambda r: r["_t"])
    print(f"kept 0.85+ fires: {len(kept)}")

    for r in kept:
        bars = tape[r["_sym"]]
        r["_sim"] = simulate(bars, r["_t"], r["_px"], r["_goal"], r["_cost_pct"])
        r["_tp"] = simulate(bars, r["_t"], r["_px"], r["_goal"], r["_cost_pct"],
                            target_pct=TARGET)

    def stats_of(sel, valfn):
        """One arm, read from its own value function so arms cannot alias."""
        pnl = [valfn(r) for r in sel]
        pnl = [p for p in pnl if p is not None]
        if not pnl:
            return {"n": 0, "net": 0.0, "win_pct": 0.0, "avg": 0.0, "worst": 0.0,
                    "max_dd": 0.0}
        bal = peak = low = 0.0
        for p in pnl:
            bal += p
            peak = max(peak, bal)
            low = min(low, bal)
        return {"n": len(pnl), "net": round(sum(pnl), 2),
                "win_pct": round(100.0 * sum(1 for p in pnl if p > 0) / len(pnl), 1),
                "avg": round(sum(pnl) / len(pnl), 3), "worst": round(min(pnl), 2),
                "max_dd": round(peak - low, 2)}

    def rows_of(sel):
        return {
            "baseline": stats_of(sel, lambda r: float(r["pnl_net"])),
            "no_target": stats_of(sel, lambda r: r["_sim"]),
            "candidate": stats_of(sel, lambda r: r["_tp"]),
        }

    allrows = rows_of(kept)
    split = datetime(2026, 11, 1).timestamp()
    train = [r for r in kept if r["_t"] < split]
    nov = [r for r in kept if r["_t"] >= split]
    trainrows, novrows = rows_of(train), rows_of(nov)

    def show(title, rows):
        print(f"\n  {title}")
        print(f"    {'arm':<12}{'fires':>6}{'win%':>7}{'net $':>11}{'$/fire':>9}"
              f"{'worst':>9}{'maxDD':>9}")
        for name, a in rows.items():
            print(f"    {name:<12}{a['n']:>6}{a['win_pct']:>7.1f}{a['net']:>+11.2f}"
                  f"{a['avg']:>+9.3f}{a['worst']:>+9.2f}{a['max_dd']:>9.2f}")

    show(f"WHOLE WINDOW — {datetime.fromtimestamp(kept[0]['_t']).date()} to "
         f"{datetime.fromtimestamp(kept[-1]['_t']).date()}", allrows)
    show(f"TRAIN Sep–Oct (n={len(train)})", trainrows)
    show(f"OUT OF SAMPLE — November (n={len(nov)})", novrows)

    # how much evidence the November effect would need
    deltas = [r["_tp"] - r["_sim"] for r in nov
              if r["_tp"] is not None and r["_sim"] is not None]
    m, sd = dist(deltas)
    need = math.ceil((1.96 * sd / m) ** 2) if m and m > 0 and sd else None
    print(f"\n  per-fire take-profit effect in November: mean ${m:+.4f}, sd ${sd:.4f} "
          f"over {len(deltas)} fires")
    print(f"  fires needed for that effect to clear a 95% CI: {need}")
    total_delta = novrows["candidate"]["net"] - novrows["no_target"]["net"]
    print(f"  November: candidate ${novrows['candidate']['net']:+.2f} vs no-target "
          f"${novrows['no_target']['net']:+.2f} vs booked ${novrows['baseline']['net']:+.2f}"
          f"  (take-profit added ${total_delta:+.2f})")

    # ── the live recorder's own evidence
    print(f"\n  LIVE NEAR-MISS RECORDER")
    rows = []
    if nm.PATH.exists():
        rows = [json.loads(l) for l in nm.PATH.read_text(encoding="utf-8").splitlines()
                if l.strip()]
    resolved = [r for r in rows if r.get("resolved")]
    with_peak = [r for r in resolved if r.get("peak_pct") is not None]
    reached = [r for r in with_peak if r.get("reached_2pct")]
    live_written = [r for r in rows if r.get("source") == "scanner"]
    backfilled = 0
    try:
        import ccxt
        ex = ccxt.kraken({"enableRateLimit": True})
        before = len(with_peak)
        backfilled = nm.backfill_peaks(ex)
        rows = [json.loads(l) for l in nm.PATH.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        resolved = [r for r in rows if r.get("resolved")]
        with_peak = [r for r in resolved if r.get("peak_pct") is not None]
        reached = [r for r in with_peak if r.get("reached_2pct")]
        print(f"    backfilled peaks on {backfilled} earlier resolved row(s) "
              f"(had {before})")
    except Exception as exc:                      # noqa: BLE001
        print(f"    peak backfill unavailable: {exc}")
    print(f"    rows {len(rows)} | live-written {len(live_written)} | resolved "
          f"{len(resolved)} | with a peak path {len(with_peak)} | reached +{TARGET:g}% "
          f"{len(reached)}")
    for r in with_peak[:8]:
        print(f"      {r['pair']:9s} {r['ts'][11:19]} peak {r['peak_pct']:+.2f}%  "
              f"fwd {r.get('fwd_pct'):+.2f}%  stop {r.get('hit_hard_stop')}")

    # ── the decision
    nov_net = novrows["candidate"]["net"]
    nov_n = novrows["candidate"]["n"]
    ship = nov_net > 0 and nov_n >= MIN_SAMPLE and len(reached) >= MIN_SAMPLE
    if ship:
        verdict = "SHIP"
        why = (f"November ${nov_net:+.2f} over {nov_n} fires, live sample "
               f"{len(reached)} reached +{TARGET:g}%")
    elif nov_net <= 0:
        verdict = "REJECT"
        why = (f"November is ${nov_net:+.2f} over {nov_n} fires — not positive, so the "
               f"agreed bar is not met; the live recorder holds {len(with_peak)} "
               f"resolved rows and {len(reached)} that reached +{TARGET:g}%, "
               f"far under the {need} fires the effect would need")
    else:
        verdict = "REJECT (sample)"
        why = (f"November ${nov_net:+.2f} is positive but on {nov_n} fires and "
               f"{len(reached)} live +{TARGET:g}% rows — under the {MIN_SAMPLE} needed")
    print(f"\n  VERDICT: {verdict}\n    {why}")

    OUT.write_text(json.dumps({
        "target_pct": TARGET,
        "kept_fires": len(kept),
        "whole_window": allrows, "train": trainrows, "nov": novrows,
        "nov_delta_mean": round(m, 4), "nov_delta_sd": round(sd, 4),
        "fires_needed_for_ci": need, "min_sample": MIN_SAMPLE,
        "live": {"rows": len(rows), "live_written": len(live_written),
                 "resolved": len(resolved), "with_peak": len(with_peak),
                 "reached_target": len(reached), "backfilled": backfilled},
        "verdict": verdict, "reason": why,
    }, indent=1), encoding="utf-8")
    print("\nwrote scratch/_take_profit_verdict.json")


if __name__ == "__main__":
    main()
