"""WHY DO THE KEPT 0.85+ FIRES LOSE — extended entry, or an exit that gives it back?
(Nov 15)

The gate counterfactual settled that no `_entry_quality` threshold turns the kept
fires profitable: at 350 measured fires the kept band books -$197 in-sample and
loses in November at every setting. So the threshold is not the question. This
asks what IS: on the fires the gate keeps, is the loss paid at the entry (buying
an already-extended mover) or at the exit (a gain that was there and got given
back)?

For every kept fire it computes what the scanner could have known at fire time
from the tape only — the run-up over the prior 3/6/12 hourly bars, the fill's
distance above the recent low and below the recent high, how much of the prior
move had already happened, and the bar-to-bar extension at the fill — and the
post-entry path the tape records: peak gain, the giveback from peak to exit, and
how much of the peak was actually captured.

Features are compared between winners and losers with a real effect size
(Cohen's d and a bootstrap-free normal CI on the mean difference), not a fitted
model. Then the single candidate change the evidence names is tested under the
same honest policy as the gate run (unmeasured vetoed, zero pass-through), on
Sep-Oct and out of sample on November.

Run: .venv/Scripts/python.exe -X utf8 scratch/kept_diagnosis.py
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import breakout_gate as bg                       # noqa: E402
import scanner as sc                             # noqa: E402
from entry_edge_study import load_tape           # noqa: E402
from gate_counterfactual import clean_ledger, entry_quality, parse_ts, hourly_rows  # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
OUT = ROOT / "scratch" / "_kept_diagnosis.json"
GATE = bg.BREAKOUT_MIN
STOP_PCT = 3.0
TRAIL_PCT = 1.5
ARM_FRAC = 0.5
WINDOW_MIN = 180.0
NOV1 = None


def bars_between(bars, t0, t1):
    lo = bisect.bisect_left([b[0] for b in bars], t0)
    hi = bisect.bisect_right([b[0] for b in bars], t1)
    return bars[lo:hi]


def pre_entry(bars, at_s, px):
    """Everything knowable at the fill, from bars strictly before it."""
    i = bisect.bisect_right([b[0] for b in bars], at_s)
    prior = bars[max(0, i - 12):i]          # the prior hour of 5m bars
    if len(prior) < 6:
        return None
    hi = max(float(b[2]) for b in prior)
    lo = min(float(b[3]) for b in prior)
    prev_close = float(prior[-1][4])
    out = {
        "off_low_pct": (px / lo - 1.0) * 100.0 if lo > 0 else None,
        "off_high_pct": (px / hi - 1.0) * 100.0 if hi > 0 else None,
        "bar_ext_pct": (px / prev_close - 1.0) * 100.0 if prev_close > 0 else None,
        "move_done": (px - lo) / (hi - lo) if hi > lo else None,
    }
    got = hourly_rows(bars, at_s)
    if got:
        order, buckets = got
        closes = [buckets[h][3] for h in order]
        for n, key in ((3, "runup_3h_pct"), (6, "runup_6h_pct"), (12, "runup_12h_pct")):
            if len(closes) > n:
                ref = closes[-1 - n]
                out[key] = (px / ref - 1.0) * 100.0 if ref > 0 else None
        lows = [buckets[h][2] for h in order[-12:]]
        highs = [buckets[h][1] for h in order[-12:]]
        if lows and highs and max(highs) > min(lows):
            out["pos_12h"] = (px - min(lows)) / (max(highs) - min(lows))
    return out


def post_path(bars, at_s, px, exit_px):
    """What the tape shows after the fill: peak, giveback, capture."""
    seg = bars_between(bars, at_s, at_s + WINDOW_MIN * 60.0)
    if len(seg) < 3:
        return None
    peak = max(float(b[2]) for b in seg)
    return {"mfe_pct": (peak / px - 1.0) * 100.0,
            "giveback_pct": (peak - exit_px) / peak * 100.0 if peak > 0 else None,
            "captured": ((exit_px / px - 1.0) / (peak / px - 1.0))
            if peak > px else None}


def simulate(bars, at_s, px, goal_pct, cost_pct, target_pct=None):
    """The shipped exit shape, optionally with a take-profit at `target_pct`."""
    seg = bars_between(bars, at_s, at_s + WINDOW_MIN * 60.0)
    if len(seg) < 3:
        return None
    stop = px * (1.0 - STOP_PCT / 100.0)
    peak, armed = px, False
    for b in seg:
        hi, lo, cl = float(b[2]), float(b[3]), float(b[4])
        if target_pct is not None and hi >= px * (1.0 + target_pct / 100.0):
            return target_pct - cost_pct
        if lo <= stop:
            return (stop / px - 1.0) * 100.0 - cost_pct
        peak = max(peak, hi)
        if not armed and (peak / px - 1.0) * 100.0 >= goal_pct * ARM_FRAC:
            armed = True
        if armed and cl <= peak * (1.0 - TRAIL_PCT / 100.0):
            return (cl / px - 1.0) * 100.0 - cost_pct
    return (float(seg[-1][4]) / px - 1.0) * 100.0 - cost_pct


def mean_ci(vals):
    if len(vals) < 2:
        return (0.0, 0.0, 0.0)
    m = statistics.mean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals))
    return (m, m - 1.96 * se, m + 1.96 * se)


def describe(name, wins, losses, unit="%"):
    """Mean difference between winners and losers, with effect size."""
    a = [v for v in wins if v is not None]
    b = [v for v in losses if v is not None]
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2) / (len(a) + len(b) - 2))
    d = (ma - mb) / pooled if pooled > 0 else 0.0
    se = math.sqrt(sa ** 2 / len(a) + sb ** 2 / len(b))
    diff = ma - mb
    print(f"    {name:<16} winners {ma:>+7.2f}{unit}  losers {mb:>+7.2f}{unit}  "
          f"diff {diff:>+7.2f}  95% CI [{diff - 1.96 * se:>+7.2f}, {diff + 1.96 * se:>+7.2f}]"
          f"  d={d:>+5.2f}")
    return {"name": name, "win_mean": round(ma, 3), "loss_mean": round(mb, 3),
            "diff": round(diff, 3), "ci": [round(diff - 1.96 * se, 3),
                                           round(diff + 1.96 * se, 3)],
            "cohens_d": round(d, 3), "n_win": len(a), "n_loss": len(b)}


def arm(rows, keep):
    sel = [r for r in rows if keep(r)]
    if not sel:
        return {"n": 0, "net": 0.0, "win_pct": 0.0, "avg": 0.0, "worst": 0.0, "max_dd": 0.0}
    pnl = [r["_sim"] if "_sim" in r else float(r["pnl_net"]) for r in sel]
    bal = peak = low = 0.0
    for p in pnl:
        bal += p
        peak = max(peak, bal)
        low = min(low, bal)
    return {"n": len(sel), "net": round(sum(pnl), 2),
            "win_pct": round(100.0 * sum(1 for p in pnl if p > 0) / len(pnl), 1),
            "avg": round(sum(pnl) / len(pnl), 3), "worst": round(min(pnl), 2),
            "max_dd": round(peak - low, 2)}


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
            exit_px = float(r.get("exit") or 0.0)
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
        if eq is None or eq < GATE:                 # only the fires today's gate keeps
            continue
        pre = pre_entry(bars, opened, px)
        post = post_path(bars, opened, px, exit_px)
        if pre is None or post is None:
            continue
        rec = dict(r)
        rec.update({"_t": opened, "_eq": eq, "_pre": pre, "_post": post,
                    "_cost_pct": 100.0 * cost / size, "_goal": goal,
                    "_sym": sym, "_px": px})
        kept.append(rec)
    kept.sort(key=lambda r: r["_t"])
    print(f"kept 0.85+ fires with full tape context: {len(kept)}")
    wins = [r for r in kept if float(r["pnl_net"]) > 0]
    losses = [r for r in kept if float(r["pnl_net"]) <= 0]
    base = arm(kept, lambda r: True)
    print(f"  baseline: {base['n']} fires, {base['win_pct']}% wins, "
          f"net ${base['net']:+.2f} (${base['avg']:+.3f}/fire), worst ${base['worst']:+.2f}")

    # ── 1. the decomposition: was the loss at the entry or at the exit?
    print(f"\n  WHY THEY LOSE — winners n={len(wins)} vs losers n={len(losses)}")
    pre_feats = [("off_low_pct", "fill over 12h low"), ("off_high_pct", "fill vs 12h high"),
                 ("bar_ext_pct", "bar-to-bar extension"), ("move_done", "share of hour move"),
                 ("runup_3h_pct", "run-up 3h"), ("runup_6h_pct", "run-up 6h"),
                 ("runup_12h_pct", "run-up 12h"), ("pos_12h", "position in 12h range")]
    rows_out = {"pre": [], "post": []}
    for key, label in pre_feats:
        got = describe(label, [r["_pre"].get(key) for r in wins],
                       [r["_pre"].get(key) for r in losses])
        if got:
            rows_out["pre"].append(got)
    print("    -- post-entry path (tape) --")
    for key, label in (("mfe_pct", "peak gain (MFE)"), ("giveback_pct", "peak-to-exit giveback"),
                       ("captured", "share of peak captured")):
        got = describe(label, [r["_post"].get(key) for r in wins],
                       [r["_post"].get(key) for r in losses])
        if got:
            rows_out["post"].append(got)

    # how much of the loss is "never went up" vs "went up and gave it back"
    never = [r for r in losses if r["_post"]["mfe_pct"] <= 0.5]
    gave_back = [r for r in losses if r["_post"]["mfe_pct"] > 0.5]
    def money(rs):
        return round(sum(float(r["pnl_net"]) for r in rs), 2)
    print(f"\n    losers that never got 0.5% up:   n={len(never):>3}  {money(never):>+9.2f}")
    print(f"    losers that were up and gave it back: n={len(gave_back):>3}  "
          f"{money(gave_back):>+9.2f}   (peak {statistics.mean([r['_post']['mfe_pct'] for r in gave_back]):.2f}%)"
          if gave_back else "")
    win_mfe = statistics.mean([r["_post"]["mfe_pct"] for r in wins]) if wins else 0.0
    win_cap = [r["_post"]["captured"] for r in wins if r["_post"].get("captured") is not None]
    print(f"    winners' mean peak {win_mfe:+.2f}%, mean capture {statistics.mean(win_cap)*100:.0f}%"
          f" of it" if win_cap else "")

    # ── 2. the single candidate change the evidence names
    cand = {}
    for th in (2.0, 3.0, 4.0, 5.0):
        cand[f"target_{th:g}"] = th
    best = None
    print(f"\n  CANDIDATE — take the gain at +X% instead of letting the trail give it back")
    print(f"    {'variant':<14}{'n':>5}{'win%':>7}{'net $':>11}{'$/fire':>9}{'worst':>9}{'maxDD':>9}")
    variant_rows = {}
    for name, th in cand.items():
        for r in kept:
            r["_sim"] = simulate(tape[r["_sym"]], r["_t"], r["_px"], r["_goal"],
                                r["_cost_pct"], target_pct=th)
        a = arm(kept, lambda r: r["_sim"] is not None)
        variant_rows[name] = a
        print(f"    {name:<14}{a['n']:>5}{a['win_pct']:>7.1f}{a['net']:>+11.2f}"
              f"{a['avg']:>+9.3f}{a['worst']:>+9.2f}{a['max_dd']:>9.2f}")
    for r in kept:
        r.pop("_sim", None)
    # the same simulation WITHOUT a target, so the comparison is exit-vs-exit
    for r in kept:
        r["_sim"] = simulate(tape[r["_sym"]], r["_t"], r["_px"], r["_goal"], r["_cost_pct"])
    same = arm(kept, lambda r: r["_sim"] is not None)
    print(f"    {'sim, no target':<14}{same['n']:>5}{same['win_pct']:>7.1f}{same['net']:>+11.2f}"
          f"{same['avg']:>+9.3f}{same['worst']:>+9.2f}{same['max_dd']:>9.2f}")
    for r in kept:
        r.pop("_sim", None)
    best_name = max(variant_rows, key=lambda k: variant_rows[k]["net"])
    best = variant_rows[best_name]
    print(f"    best by net: {best_name} (${best['net']:+.2f})")

    # ── 2b. the other candidate the mission allows: refuse the extended mover
    # The pre-entry effects above are all d < 0.4, so this is expected to be
    # noise-fitting — measured anyway, so the doc can say so with numbers.
    print(f"\n  CANDIDATE B — veto the already-extended mover (the pre-entry features)")
    veto_defs = {
        "bar_ext>0.5": lambda r: (r["_pre"].get("bar_ext_pct") or 0) > 0.5,
        "runup3h>3": lambda r: (r["_pre"].get("runup_3h_pct") or 0) > 3.0,
        "runup3h>5": lambda r: (r["_pre"].get("runup_3h_pct") or 0) > 5.0,
        "off_low>2": lambda r: (r["_pre"].get("off_low_pct") or 0) > 2.0,
        "move_done>0.95": lambda r: (r["_pre"].get("move_done") or 0) > 0.95,
    }
    print(f"    {'veto':<16}{'n':>5}{'win%':>7}{'net $':>11}{'$/fire':>9}{'worst':>9}{'maxDD':>9}")
    veto_rows = {}
    for name, drop in veto_defs.items():
        a = arm(kept, lambda r, d=drop: not d(r))
        veto_rows[name] = a
        print(f"    {name:<16}{a['n']:>5}{a['win_pct']:>7.1f}{a['net']:>+11.2f}"
              f"{a['avg']:>+9.3f}{a['worst']:>+9.2f}{a['max_dd']:>9.2f}")

    # ── 3. the split that decides it
    print(f"\n  OUT OF SAMPLE — chosen on Sep-Oct, scored on November")
    split = datetime(2026, 11, 1).timestamp()
    train = [r for r in kept if r["_t"] < split]
    test = [r for r in kept if r["_t"] >= split]

    def on(rows, th):
        for r in rows:
            r["_sim"] = (simulate(tape[r["_sym"]], r["_t"], r["_px"], r["_goal"],
                                  r["_cost_pct"], target_pct=th) if th else
                         simulate(tape[r["_sym"]], r["_t"], r["_px"], r["_goal"],
                                  r["_cost_pct"]))
        a = arm(rows, lambda r: r["_sim"] is not None)
        for r in rows:
            r.pop("_sim", None)
        return a

    print(f"    train {len(train)} fires | test {len(test)} fires")
    print(f"    {'variant':<16}{'train n':>8}{'train $':>10}{'Nov n':>7}{'Nov $':>10}"
          f"{'Nov win%':>10}{'Nov DD':>8}")
    split_rows = {}
    plan = [("baseline booked", "booked"), ("sim, no target", None)] + list(cand.items())
    for name, th in plan:
        if th == "booked":
            a = arm(train, lambda r: True)
            b = arm(test, lambda r: True)
        else:
            a, b = on(train, th), on(test, th)
        split_rows[name] = {"train": a, "nov": b}
        print(f"    {name:<16}{a['n']:>8}{a['net']:>+10.2f}{b['n']:>7}{b['net']:>+10.2f}"
              f"{b['win_pct']:>10.1f}{b['max_dd']:>8.2f}")

    OUT.write_text(json.dumps({
        "kept_fires": len(kept), "gate_min": GATE,
        "baseline": base, "winners": len(wins), "losers": len(losses),
        "effects": rows_out,
        "loss_split": {"never_up_n": len(never), "never_up_usd": money(never),
                       "gave_back_n": len(gave_back), "gave_back_usd": money(gave_back)},
        "variants": variant_rows, "vetoes": veto_rows, "best_by_net": best_name,
        "sim_no_target": same, "split": split_rows,
    }, indent=1), encoding="utf-8")
    print("\nwrote scratch/_kept_diagnosis.json")


if __name__ == "__main__":
    main()
