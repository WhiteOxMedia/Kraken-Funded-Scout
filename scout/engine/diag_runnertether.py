"""diag_runnertether.py — THE RUNNER-TETHER BACKTEST (Sep 20).

WHY THIS EXISTS: the goal system caps every computed goal at
GOAL_MAX_PCT (6.0) and re-goals one ladder step per deep verdict —
deliberately (the Jul 31 lesson). The cost of that discipline is the
RUNNER: a fire whose coin keeps climbing after the position is gone.
The user asks: how many runner fires did the old far-cap bury this
month, and what would they have paid in dollars?

THE COUNTERFACTUAL (all conventions stated, no invented fills):
  - Runner burial: a closed trade whose coin printed a post-exit high
    >= exit x 1.02 within the horizon — the coin demonstrably ran on
    without the position.
  - THE TETHER FIX: instead of the fixed-goal sell, ride from the
    actual exit with the fleet's OWN giveback tolerance (the profit
    guard's 1.5% trail): exit at the first close >= 1.5% below the
    running peak, or at horizon end (8 hourly bars — one re-goal
    cadence), whichever comes first.
  - Bar discipline: continuation is measured from the bar AFTER the
    exit bar (post-exit money must survive to a bar close to count —
    no intra-bar fantasy fills).
  - Fees: the tether is the same position sold later — one sell
    either way, so no fee delta; slippage is not modeled.

THE ATTRIBUTION: buried dollars split by goal_pct band. If the 6%
far-cap were the binding constraint, burials cluster at high goals;
if the base ladder's modest goals bury more, the cap is exonerated
and the tether (not a higher cap) is the right fix.

Hermetic: a pure core (price_tether) + thin store loader; every path
a module constant the suite rebinds. Read-only on production data.
"""
from __future__ import annotations

import json
from pathlib import Path

TRADES_FILE = Path(__file__).parent / "contest_trades.jsonl"
STORES_DIR = Path(__file__).parent / "surething_data" / "kraken"
OUT = Path(__file__).parent / "_runnertether.txt"

TRAIL = 0.015          # the profit guard's own giveback tolerance
HORIZON_BARS = 8       # one re-goal cadence (the deep-verdict step)
RUNNER_RUN = 0.02      # a burial needs a +2% continuation beyond the exit
ARM_RUN = 0.02         # THE ARM: the tether only diverges once the coin
                       # confirms the run (+2% past the exit) — a naive
                       # tether-everything world rode the losers down and
                       # priced net -$47k this month; the armed tether
                       # keeps every non-runner's original exit untouched


def price_tether(bars, exit_px: float, trail: float = TRAIL,
                 horizon: int = HORIZON_BARS,
                 arm: float = ARM_RUN) -> dict:
    """The ARMED tether counterfactual on hourly bars. `bars` =
    [(high, close)] for the bars AFTER the exit bar. The tether arms
    only once the running peak (highs) reaches exit x (1 + arm) — the
    coin CONFIRMED the run; before that, the original exit stands and
    nothing is claimed (a naive tether-everything world rode the losers
    down: this month that world prices net -$47k). Once armed, ride with
    the guard's own `trail`: exit at the first CLOSE below peak x
    (1 - trail), else at the horizon's last close. Returns (exit price,
    armed, detail) — armed False = no divergence (delta 0)."""
    if exit_px <= 0:
        return {"tether_exit": None, "armed": False,
                "detail": "no exit price"}
    if not bars:
        return {"tether_exit": None, "armed": False,
                "detail": "no bars after exit"}
    arm_level = exit_px * (1.0 + arm)
    peak = 0.0
    _armed_at = None
    for _i, (h, c) in enumerate(bars[:horizon]):
        peak = max(peak, float(h))
        if _armed_at is None and peak >= arm_level:
            _armed_at = _i + 1          # the run is confirmed: hold
        if _armed_at is not None and float(c) <= peak * (1.0 - trail):
            return {"tether_exit": float(c), "armed": True,
                    "detail": f"trail broke bar +{_i + 1} "
                              f"(armed at +{_armed_at}, peak {peak:.6g})"}
    if _armed_at is None:
        return {"tether_exit": None, "armed": False,
                "detail": "never confirmed +2%"}
    last_c = float(bars[min(horizon, len(bars)) - 1][1])
    return {"tether_exit": last_c, "armed": True,
            "detail": f"horizon end (armed at +{_armed_at}, "
                      f"peak {peak:.6g}, "
                      f"{min(horizon, len(bars))} bars)"}


def _bars_after(store: Path, exit_ts: str) -> list:
    """The hourly bars strictly after the exit hour. Missing store or
    truncation is honest: fewer bars than the horizon = the horizon
    was cut short (today's trades)."""
    import pandas as pd
    try:
        df = pd.read_feather(store)
    except Exception:  # documented skip: unreadable store -> the trade
        return None    # is counted in the summary's skipped list, spoken in the txt
    col = ("date" if "date" in df.columns
           else ("time" if "time" in df.columns else df.columns[0]))
    df = df.sort_values(col)
    from datetime import datetime as _dt
    try:
        t = _dt.fromisoformat(str(exit_ts))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.astimezone()
    # match the column's tz flavor (this build's stores are tz-aware
    # UTC; naive columns still work)
    from datetime import timezone as _tz
    col_tz = df[col].dt.tz
    if col_tz is not None:
        if t.tzinfo is None:
            t = t.tz_localize(_tz.utc)
        else:
            t = t.astimezone(col_tz)
    elif t.tzinfo is not None:
        t = t.astimezone(_tz.utc).replace(tzinfo=None)
    cut = df[df[col] > t]
    return list(zip(cut["high"].astype(float).to_list(),
                    cut["close"].astype(float).to_list()))


def run_study() -> dict:
    """One pass over this month's closed trades. Returns the summary;
    writes _runnertether.txt with the per-trade table."""
    lines = ["THE RUNNER-TETHER BACKTEST (Sep 20)",
             f"trail {TRAIL * 100:.1f}% (the guard's own giveback) | "
             f"horizon {HORIZON_BARS}h | runner = post-exit high >= "
             f"exit x {1 + RUNNER_RUN:.2f}",
             "continuation measured from the bar AFTER the exit; no "
             "fee delta (same position, one sell)\n"]
    try:
        rows = [json.loads(l) for l in
                TRADES_FILE.read_text(encoding="utf-8",
                                      errors="replace").splitlines()
                if l.strip()]
    except OSError:
        rows = []
    sep = [r for r in rows
           if str(r.get("sold_at", "")).startswith("2026-09")]
    graded, skipped = [], []
    for r in sep:
        pair = str(r.get("pair") or "")
        exit_px = float(r.get("exit") or 0.0)
        size = float(r.get("size") or 0.0)
        if not pair or exit_px <= 0 or size <= 0:
            skipped.append(r)
            continue
        store = STORES_DIR / f"{pair.replace('/', '_')}-1h.feather"
        bars = _bars_after(store, r.get("sold_at"))
        if bars is None:
            skipped.append(r)
            continue
        got = price_tether(bars, exit_px)
        if not got["armed"] or got["tether_exit"] is None:
            continue          # never confirmed the run: the original exit stands
        # THE SIZE CONVENTION (verified against the ledger's own
        # pnl_gross): size is DOLLARS PAID — pnl_gross = pct x size —
        # so the tether's delta is the pct move x the dollar size
        delta = (got["tether_exit"] / exit_px - 1.0) * size
        graded.append({**r, "tether_exit": got["tether_exit"],
                       "delta_usd": round(delta, 2),
                       "armed": got["armed"],
                       "detail": got["detail"],
                       "truncated": len(bars) < HORIZON_BARS})

    for g in graded:
        g["is_runner"] = bool(g["armed"] and g["delta_usd"] > 0)
    runners = [g for g in graded if g["is_runner"]]
    net = round(sum(g["delta_usd"] for g in graded), 2)
    runner_usd = round(sum(g["delta_usd"] for g in runners), 2)
    cost_usd = round(sum(g["delta_usd"] for g in graded
                         if g["delta_usd"] <= 0), 2)
    buried = len(graded)

    def band(p):
        p = float(p or 0)
        return "<=2" if p <= 2.0 else ("2-4" if p < 4.0 else ">=4")

    bands = {}
    for g in runners:
        bands.setdefault(band(g.get("goal_pct")), []).append(g)

    lines.append(f"closed trades this month: {len(sep)}  priced: "
                 f"{len(graded)}  skipped (no store/price): "
                 f"{len(skipped)}")
    lines.append(f"RUNNER BURIALS (the coin confirmed +2% past the "
                 f"exit — the far-cap's ride): {buried}")
    lines.append(f"  what the armed tether would have released: "
                 f"{runner_usd:+.2f} USD")
    lines.append(f"  where the armed tether paid a giveback (confirmed, "
                 f"then died): {cost_usd:+.2f} USD")
    lines.append(f"  NET (the armed fix's honest bottom line): "
                 f"{net:+.2f} USD")
    lines.append("")
    lines.append("BURIALS BY GOAL BAND — did the 6% far-cap bury them, "
                 "or the base ladder?")
    for b in ("<=2", "2-4", ">=4"):
        v = bands.get(b) or []
        if v:
            lines.append(f"  goal {b:>3}%: {len(v):2d} burials  "
                         f"{sum(x['delta_usd'] for x in v):+9.2f} USD  "
                         f"({', '.join(sorted({x['pair'].split('/')[0] for x in v}))})")
    lines.append("")
    lines.append("PER-TRADE (runners only):")
    for g in sorted(runners, key=lambda x: -x["delta_usd"]):
        lines.append(f"  {g['pair']:14s} sold {str(g['sold_at'])[:16]}  "
                     f"goal {float(g.get('goal_pct') or 0):.2f}%  "
                     f"exit {g['exit']:.5g} -> tether {g['tether_exit']:.5g} "
                     f"= {g['delta_usd']:+8.2f} USD  [{g['detail']}]"
                     + (" (horizon truncated)" if g["truncated"] else ""))

    summary = {"priced": len(graded), "runners": len(runners),
               "runner_usd": runner_usd, "cost_usd": cost_usd,
               "net_usd": net,
               "bands": {b: {"n": len(v),
                             "usd": round(sum(x["delta_usd"] for x in v), 2)}
                         for b, v in bands.items()}}
    try:
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return summary


if __name__ == "__main__":
    print(json.dumps(run_study(), indent=1))
