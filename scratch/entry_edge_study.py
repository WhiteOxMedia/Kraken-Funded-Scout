"""WHICH TIMING SIGNAL ACTUALLY PAYS ON THE CHAMPION PATH? (Nov 15)

The tournament ranks champion movers by day-cheapness, and the graded evidence
says that preference is anti-predictive. This asks the question properly:

  * chronological replay over the stored 5m tapes, no lookahead — at each
    decision point the scanner's own `evaluate_strategy` sees only bars up to
    that hour, and each candidate's signals are computed from the same window;
  * PAIRED — every candidate at a point is marked to the same 60-minute
    forward window from the same tape, net of the round-trip cost the journal
    actually paid, and compared with the coin the bracket actually fires
    (highest confidence);
  * SPLIT — the earlier part of the history is the fit window (where a signal
    and its direction are chosen), the later part is an untouched holdout.

Signals tested (all cheaply derivable from the 720-bar deep read the scanner
already takes): cheap (the current ranking), pos_hour, pos_day, off_high_24h,
off_low_24h, mom_2h, mom_6h, vol_24h.

Writes one JSONL of decision points (so the analysis can be re-run without the
replay) and prints the fit/holdout table.

Run:  .venv/Scripts/python.exe -X utf8 scratch/entry_edge_study.py \
          [--days 60] [--step 6] [--fit 0.6]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import buy_low as bl          # noqa: E402
import config as cfgmod       # noqa: E402
import scanner as sc          # noqa: E402

ARCHIVES = [ROOT / "scratch" / "kraken_5m_long_183d_aligned.json",
            ROOT / "scratch" / "kraken_5m_cover_2026-11-15.json"]
TRADES = ROOT / "contest_trades.jsonl"
OUT = ROOT / "scratch" / "_edge_decisions.jsonl"

LOOKBACK = 120
HORIZON_M = 60.0

SIGNALS = ["cheap", "pos_hour", "pos_day", "off_high_24h", "off_low_24h",
           "mom_2h", "mom_6h", "vol_24h"]


# ── tape helpers (same definitions as the scanner's live read) ─────────────
def load_tape():
    merged: dict[str, dict[int, list]] = {}
    for path in ARCHIVES:
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        for sym, bars in (raw.get("pairs") or {}).items():
            slot = merged.setdefault(sym, {})
            for b in bars:
                slot.setdefault(int(b[0]), b)
    return {s: [by[t] for t in sorted(by)] for s, by in merged.items()}


def hourly(bars):
    buckets = {}
    for b in bars:
        h = int(b[0]) // 3600 * 3600
        cur = buckets.get(h)
        if cur is None:
            buckets[h] = [b[0], b[1], b[2], b[3], b[4], b[5]]
        else:
            cur[2] = max(cur[2], b[2])
            cur[3] = min(cur[3], b[3])
            cur[4] = b[4]
            cur[5] = cur[5] + b[5]
    return [[h * 1000] + buckets[h][1:] for h in sorted(buckets)]


_STAMPS: dict[int, tuple[int, list]] = {}


def _stamps(bars):
    key = id(bars)
    hit = _STAMPS.get(key)
    if hit is not None and hit[0] == len(bars):
        return hit[1]
    idx = [int(b[0]) for b in bars]
    _STAMPS[key] = (len(bars), idx)
    return idx


def fwd_pct(bars_5m, ts, minutes):
    stamps = _stamps(bars_5m)
    i0 = bisect.bisect_left(stamps, ts)
    if i0 >= len(bars_5m):
        return None
    j = bisect.bisect_left(stamps, ts + minutes * 60)
    if j >= len(bars_5m) or float(bars_5m[i0][4]) <= 0:
        return None
    return (float(bars_5m[j][4]) / float(bars_5m[i0][4]) - 1.0) * 100.0


def day_pos(bars_5m, ts):
    stamps = _stamps(bars_5m)
    hi_i = bisect.bisect_right(stamps, ts) - 1
    if hi_i < 12:
        return None
    d0 = bisect.bisect_left(stamps, int(ts) // 86400 * 86400)
    day = bars_5m[d0:hi_i + 1]
    if len(day) < 12:
        return None
    px = float(bars_5m[hi_i][4])
    dhi = max(float(b[2]) for b in day)
    dlo = min(float(b[3]) for b in day)
    if not (dhi > dlo):
        return None
    return (px - dlo) / (dhi - dlo)


def hour_pos(window):
    """Where the last close sits in the PRIOR 12 hourly bars (no lookahead)."""
    if len(window) < 14:
        return None
    prev = window[-13:-1]
    hi = max(float(b[2]) for b in prev)
    lo = min(float(b[3]) for b in prev)
    px = float(window[-1][4])
    if not (hi > lo) or px <= 0:
        return None
    return (px - lo) / (hi - lo)


def signals_for(window, bars_5m, ts):
    px = float(window[-1][4])
    out = {}
    if len(window) >= 25:
        hi24 = max(float(b[2]) for b in window[-24:])
        lo24 = min(float(b[3]) for b in window[-24:])
        out["off_high_24h"] = (hi24 - px) / px * 100.0
        out["off_low_24h"] = (px - lo24) / px * 100.0
        rets = []
        for i in range(len(window) - 24, len(window)):
            c0 = float(window[i - 1][4])
            c1 = float(window[i][4])
            if c0 > 0:
                rets.append((c1 / c0 - 1.0) * 100.0)
        out["vol_24h"] = statistics.pstdev(rets) if len(rets) > 2 else None
    if len(window) >= 7:
        out["mom_6h"] = (px / float(window[-7][4]) - 1.0) * 100.0
    if len(window) >= 3:
        out["mom_2h"] = (px / float(window[-3][4]) - 1.0) * 100.0
    out["pos_hour"] = hour_pos(window)
    out["pos_day"] = day_pos(bars_5m, ts)
    parts = [p for p in (out["pos_hour"], out["pos_day"]) if p is not None]
    out["cheap"] = 1.0 - sum(parts) / len(parts) if parts else None
    return out


# ── the replay ────────────────────────────────────────────────────────────
def collect(days, step_h, cost_pct):
    tape = load_tape()
    cfg = cfgmod.TradingConfig()
    cfg.mtf_enabled = False
    cfg.liquidity_filter_enabled = False
    cfg.deep_scan_enabled = False
    hourly_tape = {}
    for sym, bars in tape.items():
        if len(bars) > 500:
            hb = hourly(bars)
            hourly_tape[sym] = (hb, [int(b[0]) for b in hb])
    end = max(b[-1][0] for b in tape.values())
    start = end - days * 86400

    points = 0
    ts = int(start // 3600 * 3600)
    with open(OUT, "w", encoding="utf-8") as out:
        while ts <= end:
            cands = []
            for sym, (hb, stamps) in hourly_tape.items():
                i = bisect.bisect_right(stamps, ts * 1000) - 1
                if i < 60:
                    continue
                window = hb[max(0, i - LOOKBACK + 1):i + 1]
                try:
                    df = sc._ohlcv_to_df(window)
                    sig = sc.evaluate_strategy(df, cfg)
                except Exception:
                    continue
                if sig.action != "buy":
                    continue
                sg = signals_for(window, tape[sym], ts)
                fwd = fwd_pct(tape[sym], ts, HORIZON_M)
                if fwd is None:
                    continue
                cands.append({"pair": sym, "conf": float(sig.confidence),
                              "fwd": fwd - cost_pct, "sig": sg})
            if len(cands) >= 2:
                points += 1
                out.write(json.dumps({"t": ts, "cands": cands}) + "\n")
            ts += step_h * 3600
            if points % 25 == 0 and points:
                print(f"  ..{points} points at {ts}", flush=True)
    print(f"decision points with >=2 buy candidates: {points}", flush=True)
    return points


# ── the analysis ──────────────────────────────────────────────────────────
def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def paired_ci(diffs):
    """mean and 95% CI of a paired difference, normal approximation."""
    n = len(diffs)
    if n < 2:
        return (statistics.mean(diffs) if diffs else 0.0), 0.0, 0.0
    m = statistics.mean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(n)
    return m, m - 1.96 * se, m + 1.96 * se


def arm(rows, key, sign, window):
    """Per-point: fired (max conf) vs preferred (max sign*signal near-equal)."""
    diffs, fired_all, pref_all, changed = [], [], [], 0
    for pt in rows:
        cands = pt["cands"]
        fired = max(cands, key=lambda c: c["conf"])
        near = [c for c in cands
                if c["conf"] >= fired["conf"] - window
                and c["sig"].get(key) is not None]
        if not near:
            continue
        pref = max(near, key=lambda c: sign * c["sig"][key])
        diffs.append(pref["fwd"] - fired["fwd"])
        fired_all.append(fired["fwd"])
        pref_all.append(pref["fwd"])
        if pref["pair"] != fired["pair"]:
            changed += 1
    return {"n": len(diffs), "diff": paired_ci(diffs), "changed": changed,
            "fired": fired_all, "pref": pref_all}


def summarize(tag, rows, fit_frac):
    if not rows:
        print("no decisions collected")
        return {}
    cut = int(len(rows) * fit_frac)
    fit, hold = rows[:cut], rows[cut:]
    print(f"\n{'='*96}\n{tag}: {len(rows)} points | fit {len(fit)} | "
          f"holdout {len(hold)} (chronological, no overlap)\n{'='*96}")
    print(f"{'signal':<14}{'dir':<5}{'fit n':>6}{'fit diff':>10}{'fit CI':>16}"
          f"{'hold n':>7}{'hold diff':>10}{'hold CI':>16}  verdict")

    results = {}
    for key in SIGNALS:
        best = None
        for sign, dname in ((1, "+"), (-1, "-")):
            f = arm(fit, key, sign, bl.BUY_LOW_CONF_WINDOW)
            if f["n"] < 10:
                continue
            m, lo, hi = f["diff"]
            score = m
            if best is None or score > best[0]:
                best = (score, sign, dname, f)
        if best is None:
            continue
        _, sign, dname, f = best
        h = arm(hold, key, sign, bl.BUY_LOW_CONF_WINDOW)
        mf, lof, hif = f["diff"]
        mh, loh, hih = h["diff"] if h["n"] >= 2 else (0.0, 0.0, 0.0)
        clears = h["n"] >= 15 and loh > 0 and mh > 0
        results[key] = {"sign": sign, "dir": dname, "fit": f, "hold": h,
                        "clears": clears}
        print(f"{key:<14}{dname:<5}{f['n']:>6}{mf:>+10.3f}"
              f"{lof:>+8.3f}..{hif:<+7.3f}{h['n']:>7}{mh:>+10.3f}"
              f"{loh:>+8.3f}..{hih:<+7.3f}  "
              f"{'CLEARS' if clears else 'no'}")
    # the winner: biggest holdout difference that also clears
    win = [k for k, v in results.items() if v["clears"]]
    print("\n-- absolute arms on the holdout (net of cost, per trade) --")
    for k in SIGNALS:
        v = results.get(k)
        if not v:
            continue
        h = v["hold"]
        if not h["n"]:
            continue
        fw = 100.0 * sum(1 for x in h["fired"] if x > 0) / h["n"]
        pw = 100.0 * sum(1 for x in h["pref"] if x > 0) / h["n"]
        print(f"{k:<14}{v['dir']}  fired win {fw:5.1f}% mean {statistics.mean(h['fired']):+6.3f}%  |"
              f"  pref win {pw:5.1f}% mean {statistics.mean(h['pref']):+6.3f}%  "
              f"(changed {h['changed']}/{h['n']})")
    print(f"\nwinner(s) clearing the baseline out of sample: {win or 'NONE'}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--step", type=float, default=6.0)
    ap.add_argument("--fit", type=float, default=0.6)
    ap.add_argument("--analyse-only", action="store_true")
    args = ap.parse_args()

    costs = []
    if TRADES.exists():
        for line in TRADES.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if float(r["size"]) > 0:
                    costs.append(float(r["cost_rt"]) / float(r["size"]) * 100.0)
            except Exception:
                continue
    cost_pct = statistics.median(costs) if costs else 0.0
    print(f"cost {cost_pct:.3f}%/trade (median cost_rt/size), horizon {HORIZON_M:.0f}m")

    if not args.analyse_only:
        collect(args.days, args.step, cost_pct)
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    res = summarize(f"ENTRY EDGE — {args.days}d every {args.step:.0f}h",
                    rows, args.fit)
    keep = {k: {"dir": v["dir"], "fit_n": v["fit"]["n"],
                "fit_diff": round(v["fit"]["diff"][0], 4),
                "hold_n": v["hold"]["n"],
                "hold_diff": round(v["hold"]["diff"][0], 4),
                "hold_ci": [round(v["hold"]["diff"][1], 4),
                            round(v["hold"]["diff"][2], 4)],
                "clears": v["clears"]}
            for k, v in res.items()}
    (ROOT / "scratch" / "_edge_result.json").write_text(
        json.dumps(keep, indent=1), encoding="utf-8")
    print("\nwrote scratch/_edge_result.json")


if __name__ == "__main__":
    main()
