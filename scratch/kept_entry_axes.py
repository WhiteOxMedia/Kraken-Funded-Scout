"""THE ENTRY AXES THE GATE HAS NOT TESTED — participation, compression, structure (Nov 15).

Settled and not revisited here: the `entry_quality` threshold has nothing left, and
price-extension features (run-up, distance from the low/high, position in range,
bar-to-bar extension) do not separate winners from losers on the kept 0.85+ fires
(|d| <= 0.38). This asks whether anything on the axes the tournament has NEVER
looked at does, using only bars before the fill:

  participation  volume expansion against the coin's own baseline, and the volume
                 trend into the fire;
  compression    how wide the recent range is against the move being made, and how
                 long the coin sat coiled before the breakout;
  structure      whether the fill is a dip-and-recover inside an uptrend, a first
                 break of the prior high, or a late re-break of one.

Each is reported winners vs losers with the difference, its 95% CI and Cohen's d,
so "clears a real effect size" is a number (|d| >= 0.5 with a CI excluding zero),
not a story. If nothing clears, that is the finding and the entry side stops here.

Run: .venv/Scripts/python.exe -X utf8 scratch/kept_entry_axes.py
"""
from __future__ import annotations

import bisect
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import breakout_gate as bg                                    # noqa: E402
from entry_edge_study import load_tape                        # noqa: E402
from gate_counterfactual import clean_ledger, entry_quality, parse_ts, hourly_rows  # noqa: E402
from kept_diagnosis import simulate, arm, describe            # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
OUT = ROOT / "scratch" / "_kept_entry_axes.json"
GATE = bg.BREAKOUT_MIN
HOUR = 3600


def _hourly(bars, at_s, n_hours):
    """Aggregate the last n_hours of 5m bars into buckets (o,h,l,c,vol)."""
    i = bisect.bisect_right([b[0] for b in bars], at_s)
    seg = bars[max(0, i - n_hours * 12):i]
    out = []
    cur = None
    for b in seg:
        h = int(b[0] // HOUR)
        vol = float(b[5]) if len(b) > 5 else 0.0
        if cur is None or h != cur[0]:
            if cur:
                out.append(cur)
            cur = [h, float(b[1]), float(b[2]), float(b[3]), float(b[4]), vol]
        else:
            cur[2] = max(cur[2], float(b[2]))
            cur[3] = min(cur[3], float(b[3]))
            cur[4] = float(b[4])
            cur[5] += vol
    if cur:
        out.append(cur)
    return out


def axes(bars, at_s, px):
    """Every feature here is computed from bars strictly before the fill."""
    i = bisect.bisect_right([b[0] for b in bars], at_s)
    hist = bars[:i]
    if len(hist) < 12 * 12:
        return None
    h24 = _hourly(bars, at_s, 24)
    h48 = _hourly(bars, at_s, 48)
    h6 = h24[-6:]
    h3 = h24[-3:]
    if len(h24) < 12 or len(h48) < 24 or not h6:
        return None
    out = {}

    # ── participation ────────────────────────────────────────────────────────
    vols = [b[5] for b in h24]
    if sum(vols) > 0:
        last_vol = vols[-1]
        base = statistics.median(vols[:-1]) if len(vols) > 2 else 0.0
        out["vol_exp_bar"] = (last_vol / base) if base > 0 else None
        v3 = sum(b[5] for b in h3)
        v_prev3 = sum(b[5] for b in h24[-6:-3])
        out["vol_trend_3h"] = (v3 / v_prev3) if v_prev3 > 0 else None
        out["vol_exp_6h"] = (sum(b[5] for b in h6) /
                             statistics.median([sum(b[5] for b in h24[k:k + 6])
                                                for k in range(0, 18)]) or 0.0)
        out["vol_24h_vs_48h"] = None
        v48_first = sum(b[5] for b in h48[:24])
        if v48_first > 0:
            out["vol_24h_vs_48h"] = sum(vols) / v48_first

    # ── compression ──────────────────────────────────────────────────────────
    hi6 = max(b[2] for b in h6)
    lo6 = min(b[3] for b in h6)
    hi24 = max(b[2] for b in h24)
    lo24 = min(b[3] for b in h24)
    out["range_6h_pct"] = (hi6 - lo6) / px * 100.0 if px > 0 else None
    out["range_6_24"] = (hi6 - lo6) / (hi24 - lo24) if hi24 > lo24 else None
    med_rng = statistics.median([(b[2] - b[3]) / b[4] for b in h48 if b[4] > 0])
    coil = sum(1 for b in h24[-12:] if b[4] > 0 and (b[2] - b[3]) / b[4] < 0.5 * med_rng)
    out["coil_hours"] = coil
    rng6 = statistics.mean([(b[2] - b[3]) / b[4] for b in h6 if b[4] > 0])
    rng48 = statistics.mean([(b[2] - b[3]) / b[4] for b in h48 if b[4] > 0])
    out["atr_6_48"] = (rng6 / rng48) if rng48 > 0 else None

    # ── structure ────────────────────────────────────────────────────────────
    hi12 = max(b[2] for b in h24[-12:])
    lo3 = min(b[3] for b in h3)
    out["pullback_depth_pct"] = (hi12 - lo3) / hi12 * 100.0 if hi12 > 0 else None
    out["recovery"] = ((px - lo3) / (hi12 - lo3)) if hi12 > lo3 else None
    prior_hi = max(b[2] for b in h24[:-1])
    out["first_break"] = 1.0 if px >= prior_hi else 0.0
    # distinct crossings of the prior-12h high inside the last 6 hours
    ref = max(b[2] for b in h24[-18:-6]) if len(h24) >= 18 else prior_hi
    crosses, above = 0, False
    for b in h6:
        now = b[4] >= ref
        if now and not above:
            crosses += 1
        above = now
    out["rebreak_6h"] = float(crosses)
    peak_i = max(range(len(h24[-24:])), key=lambda k: h24[-24:][k][2])
    out["hours_since_high"] = float(len(h24[-24:]) - 1 - peak_i)
    return out


FEATURES = [
    ("vol_exp_bar", "volume vs own baseline (last hour)", "participation"),
    ("vol_trend_3h", "volume trend 3h vs prior 3h", "participation"),
    ("vol_exp_6h", "6h volume vs own 6h baseline", "participation"),
    ("vol_24h_vs_48h", "24h volume vs prior 24h", "participation"),
    ("range_6h_pct", "6h range width (%)", "compression"),
    ("range_6_24", "6h range / 24h range", "compression"),
    ("coil_hours", "hours coiled (range < half norm), last 12h", "compression"),
    ("atr_6_48", "6h range vs 48h range", "compression"),
    ("pullback_depth_pct", "dip from 12h high to 3h low (%)", "structure"),
    ("recovery", "fill's recovery within that dip", "structure"),
    ("first_break", "first break of the prior high (1=yes)", "structure"),
    ("rebreak_6h", "distinct re-breaks in 6h", "structure"),
    ("hours_since_high", "hours since the 24h high", "structure"),
]


def main():
    tape = load_tape()
    raw = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    ledger, _ = clean_ledger(raw)
    kept, no_vol = [], 0
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
        if eq is None or eq < GATE:
            continue
        ax = axes(bars, opened, px)
        if ax is None:
            continue
        rec = dict(r)
        rec.update({"_t": opened, "_eq": eq, "_ax": ax, "_sym": sym, "_px": px,
                    "_cost_pct": 100.0 * cost / size, "_goal": goal})
        kept.append(rec)
    kept.sort(key=lambda r: r["_t"])
    wins = [r for r in kept if float(r["pnl_net"]) > 0]
    losses = [r for r in kept if float(r["pnl_net"]) <= 0]
    print(f"kept 0.85+ fires with the full pre-entry frame: {len(kept)} "
          f"(winners {len(wins)}, losers {len(losses)})")
    vol_n = sum(1 for r in kept if r["_ax"].get("vol_exp_bar") is not None)
    print(f"  with real volume on the tape: {vol_n} of {len(kept)}")

    print(f"\n  {'feature':<40}{'n_w':>4}{'n_l':>4}{'win':>8}{'loss':>8}{'diff':>8}"
          f"{'95% CI':>18}{'d':>7}")
    findings = []
    by_axis = {}
    for key, label, axis in FEATURES:
        a = [r["_ax"].get(key) for r in wins if r["_ax"].get(key) is not None]
        b = [r["_ax"].get(key) for r in losses if r["_ax"].get(key) is not None]
        if len(a) < 10 or len(b) < 10:
            continue
        got = describe(f"{axis}:{key}", a, b)
        if not got:
            continue
        got.update({"label": label, "axis": axis})
        findings.append(got)
        by_axis.setdefault(axis, []).append(got)
        sig = ""
        if abs(got["cohens_d"]) >= 0.5:
            sig = "  <- real effect size" if (got["ci"][0] > 0 or got["ci"][1] < 0) else "  <- big but CI spans 0"
        print(f"    {label:<38}{got['n_win']:>4}{got['n_loss']:>4}"
              f"{got['win_mean']:>+8.2f}{got['loss_mean']:>+8.2f}{got['diff']:>+8.3f}"
              f"  [{got['ci'][0]:>+7.3f},{got['ci'][1]:>+7.3f}]{got['cohens_d']:>+7.2f}{sig}")

    strong = [f for f in findings if abs(f["cohens_d"]) >= 0.5 and
              (f["ci"][0] > 0 or f["ci"][1] < 0)]
    print(f"\n  clears |d| >= 0.5 with a CI excluding zero: "
          f"{[f['name'] for f in strong] or 'NONE'}")
    best = max(findings, key=lambda f: abs(f["cohens_d"])) if findings else None
    if best:
        print(f"  largest effect on any new axis: {best['label']} d={best['cohens_d']:+.2f} "
              f"(winners {best['win_mean']:+.2f} vs losers {best['loss_mean']:+.2f})")

    # ── the candidate variant, only if something cleared ────────────────────
    result = {"kept": len(kept), "winners": len(wins), "losers": len(losses),
              "with_volume": vol_n, "features": findings, "strong": strong,
              "best": best["name"] if best else None}
    split = datetime(2026, 11, 1).timestamp()
    if strong:
        f = max(strong, key=lambda x: abs(x["cohens_d"]))
        key = f["name"].split(":", 1)[1]
        direction = 1 if f["diff"] > 0 else -1
        vals = sorted(v["_ax"][key] for v in kept if v["_ax"].get(key) is not None)
        cut = vals[int(len(vals) * 0.25)] if direction > 0 else vals[int(len(vals) * 0.75)]
        keep = (lambda r: (r["_ax"].get(key) is not None and
                           direction * (r["_ax"][key] - cut) >= 0))
        print(f"\n  CANDIDATE — keep only fires on the favourable quartile of {key} "
              f"({'above' if direction > 0 else 'below'} {cut:.3f})")
        for r in kept:
            r["_sim"] = simulate(tape[r["_sym"]], r["_t"], r["_px"], r["_goal"],
                                 r["_cost_pct"])
        rows = {"baseline": arm(kept, lambda r: True),
                "sim_no_target": arm(kept, lambda r: r["_sim"] is not None),
                "candidate": arm(kept, keep)}
        for r in kept:
            r.pop("_sim", None)
        for k, v in rows.items():
            print(f"    {k:<14}{v['n']:>5}{v['win_pct']:>7.1f}{v['net']:>+11.2f}"
                  f"{v['avg']:>+9.3f}{v['worst']:>+9.2f}{v['max_dd']:>9.2f}")
        train = [r for r in kept if r["_t"] < split]
        test = [r for r in kept if r["_t"] >= split]
        a, b = arm(train, keep), arm(test, keep)
        a0, b0 = arm(train, lambda r: True), arm(test, lambda r: True)
        print(f"    train {a0['n']}->{a['n']} fires  ${a0['net']:+.2f} -> ${a['net']:+.2f}"
              f" | Nov {b0['n']}->{b['n']} fires  ${b0['net']:+.2f} -> ${b['net']:+.2f}"
              f"  win {b['win_pct']:.1f}%  DD {b['max_dd']:.2f}")
        result.update({"candidate": {"key": key, "cut": cut, "rows": rows,
                                     "train": {"base": a0, "cand": a},
                                     "nov": {"base": b0, "cand": b}}})
    else:
        print("\n  no candidate variant: nothing on these axes separates the outcomes,"
              "\n  so there is no entry rule here to test and the entry side stops.")

    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("\nwrote scratch/_kept_entry_axes.json")


if __name__ == "__main__":
    main()
