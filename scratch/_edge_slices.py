"""_edge_slices.py — WHERE the promoted edge actually lives.

`edge_engine.py` promotes ONE policy and `edge_live.py` applies it to EVERY
symbol in EVERY rotation.  That is a single global claim, and the live router
has no way to act on anything finer.  This splits the claim into the two cells
a router would need:

    per coin      a pair's own net expectancy under the promoted rule
    per rotation  the 4h MST rotation-of-day's net expectancy

Every trade is the policy's own resolved exit (the live ATR stop / 2.5R target,
chandelier variant, 0.08% round trip), so a cell's mean IS the edge that cell
would have delivered.

Cell verdicts
  EDGE      n >= MIN_N, mean > 0, t >= T_MIN, and positive in all three windows
  THIN      n >= MIN_N but the evidence is not stable (reported with its t)
  NO EVIDENCE  n < MIN_N — the cell exists but says nothing
  MISSING   the cell never produced a signal at all

The second half is the only test that matters for going live: an allow-list
built on windows 1-2 and then chained on window 3 (which never entered its
construction), beside the opposite split (the cells that were NEGATIVE on 1-2).
If the filter has real content, the rejects must underperform the allows.

Read-only over ohlcv_cache.db + edge_registry.json.
Writes scratch/_edge_slices.json.

Usage:  python scratch/_edge_slices.py [policy_id]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

OUT = ROOT / "scratch" / "_edge_slices.json"
REG = ROOT / "edge_registry.json"
MIN_N = 15
T_MIN = 2.0
MST = "America/Denver"
ROTS = ("00-04", "04-08", "08-12", "12-16", "16-20", "20-24")


def rot_of(ts) -> str:
    h = int(pd.Timestamp(ts).tz_convert(MST).hour) // 4 * 4
    return f"{h:02d}-{h + 4:02d}"


def cell_stats(nets: list[float]) -> dict:
    x = np.asarray([v for v in nets if v is not None and math.isfinite(v)],
                   dtype=float)
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win": None,
                "t": None, "lower": None}
    m = float(x.mean())
    if n < 2:
        return {"n": n, "mean": round(m, 4), "median": round(m, 4),
                "win": round(float((x > 0).mean()), 3), "t": None,
                "lower": None}
    se = float(x.std(ddof=1) / math.sqrt(n))
    t = m / se if se > 0 else 0.0
    return {"n": n, "mean": round(m, 4), "median": round(float(np.median(x)), 4),
            "win": round(float((x > 0).mean()), 3), "t": round(t, 2),
            "lower": round(m - 1.96 * se, 4)}


def verdict(st: dict, per_window_means: list[float | None]) -> str:
    if st["n"] == 0:
        return "MISSING"
    if st["n"] < MIN_N:
        return "NO EVIDENCE"
    pos = [v for v in per_window_means if v is not None]
    if (st["mean"] or 0) > 0 and (st["t"] or 0) >= T_MIN and pos \
            and all(v > 0 for v in pos) and len(pos) == 3:
        return "EDGE"
    return "THIN"


def fm(v, spec: str) -> str:
    return "   -  " if v is None else format(v, spec)


def table(cells: dict[str, list[dict]], label: str, universe: list[str]):
    rows = {}
    wbounds = [(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC"))
               for a, b in ee.WINDOWS]
    for key in universe:
        trades = cells.get(key) or []
        st = cell_stats([t["net"] for t in trades])
        pwm = []
        for lo, hi in wbounds:
            sub = [t["net"] for t in trades if lo <= t["ts"] < hi]
            pwm.append(round(float(np.mean(sub)), 4) if sub else None)
        rows[key] = {**st, "windows": pwm, "verdict": verdict(st, pwm)}
    print()
    print(f"== {label} ==")
    print(f"{'cell':<20} {'n':>5} {'mean%':>8} {'med%':>8} {'win':>5} "
          f"{'t':>6} {'lo95':>8}  {'w1/w2/w3':<22} verdict")
    for k in sorted(rows, key=lambda k: -(rows[k]["mean"] or -9e9)):
        r = rows[k]
        wm = "/".join("  -  " if v is None else f"{v:+6.2f}" for v in r["windows"])
        print(f"{k:<20} {r['n']:>5} {fm(r['mean'], '+8.2f')} "
              f"{fm(r['median'], '+8.2f')} {fm(r['win'], '5.2f')} "
              f"{fm(r['t'], '+6.2f')} {fm(r['lower'], '+8.2f')}  "
              f"{wm:<22} {r['verdict']}")
    return rows


def chain_windows(pol, sel, tag: str) -> dict:
    ch = ep.chain_result(pol, sel)
    ws = ch.get("windows") or []
    w3 = ws[2] if len(ws) > 2 else {}
    print(f"  {tag:<28} trades={ch.get('trades')} final={ch.get('final')} "
          f"passes={ch.get('passes')}/3 died={ch.get('died')} "
          f"w3={w3.get('return_pct')}%")
    return {"tag": tag, "trades": ch.get("trades"), "final": ch.get("final"),
            "passes": ch.get("passes"), "died": ch.get("died"),
            "w3_return_pct": w3.get("return_pct"),
            "w3_trades": w3.get("trades"),
            "windows": [{"window": w.get("window"),
                         "trades": w.get("trades"),
                         "return_pct": w.get("return_pct")}
                        for w in ws]}


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    reg = json.loads(REG.read_text(encoding="utf-8"))
    pid = pid or reg.get("active")
    cand = (reg.get("candidates") or {}).get(pid) or {}
    spec = cand.get("spec")
    if not spec:
        print(f"no spec for policy {pid!r}", file=sys.stderr)
        return 2
    pol = ep.policy_from_spec(spec)
    if pol is None:
        print(f"spec for {pid!r} is not executable", file=sys.stderr)
        return 2
    print(f"policy {pid}\n  {pol.label}")

    pairs = ee.load_pairs()
    print(f"tape: {len(pairs)} pairs", flush=True)
    rows = ep.build_base_rows(pairs)
    print(f"signal rows: {len(rows)}", flush=True)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    sel = ep.selected_for(pol, rows, resolver)
    print(f"selected trades for this policy: {len(sel)}", flush=True)

    per_coin: dict[str, list[dict]] = {}
    per_rot: dict[str, list[dict]] = {}
    for r, res in sel:
        t = {"ts": r["ts"], "net": float(res["net"]),
             "why": res["why"], "hold": res["hold"]}
        per_coin.setdefault(r["pair"], []).append(t)
        per_rot.setdefault(rot_of(r["ts"]), []).append(t)

    coin_rows = table(per_coin, "PER COIN", sorted(pairs))
    rot_rows = table(per_rot, "PER ROTATION (4h MST)", list(ROTS))

    # ── the live question: does a window-1/2 allow-list survive window 3? ──
    w12 = (pd.Timestamp(ee.WINDOWS[0][0], tz="UTC"),
           pd.Timestamp(ee.WINDOWS[1][1], tz="UTC"))

    def split_filter(keyfn):
        """Cells judged on windows 1-2 ONLY: allows, rejects, their means."""
        acc: dict[str, list[float]] = {}
        for r, res in sel:
            if w12[0] <= r["ts"] < w12[1]:
                acc.setdefault(keyfn(r), []).append(float(res["net"]))
        allow, reject = set(), set()
        for k, v in acc.items():
            if len(v) >= MIN_N:
                (allow if float(np.mean(v)) > 0 else reject).add(k)
        return allow, reject, acc

    coin_allow, coin_reject, coin_acc = split_filter(lambda r: r["pair"])
    rot_allow, rot_reject, rot_acc = split_filter(lambda r: rot_of(r["ts"]))

    def chain_on(keep, tag):
        sub = [(r, res) for r, res in sel if keep(r)]
        return chain_windows(pol, sub, tag)

    out_chains = [chain_on(lambda r: True, "ALL cells (the shipped claim)"),
                  chain_on(lambda r: r["pair"] in coin_allow,
                           f"coins allowed on w1-2 (n={len(coin_allow)})"),
                  chain_on(lambda r: r["pair"] in coin_reject,
                           f"coins REJECTED on w1-2 (n={len(coin_reject)})"),
                  chain_on(lambda r: rot_of(r["ts"]) in rot_allow,
                           f"rotations allowed on w1-2 ({sorted(rot_allow)})"),
                  chain_on(lambda r: rot_of(r["ts"]) in rot_reject,
                           f"rotations rejected on w1-2 ({sorted(rot_reject)})")]

    # ── the MISSING rotations: 00-04 and 20-24 are unreachable under the
    # "us" hour gate (UTC 13-24 = 06-17 MST), so measure the same rule with
    # that gate removed - the gate, not the market, is what excludes them.
    alt = ep.make_policy("long", str(spec["entry"]), str(spec["regime"]),
                         str(spec["exit"]), float(spec["size_frac"]),
                         str(spec["vol_gate"]), "any")
    sel_any = ep.selected_for(alt, rows, resolver)
    per_rot_any: dict[str, list[dict]] = {}
    for r, res in sel_any:
        per_rot_any.setdefault(rot_of(r["ts"]), []).append(
            {"ts": r["ts"], "net": float(res["net"]), "why": res["why"],
             "hold": res["hold"]})
    rot_any_rows = table(per_rot_any, "PER ROTATION, hour gate ANY", list(ROTS))
    print(f"  selected with the gate removed: {len(sel_any)} "
          f"(vs {len(sel)} gated)")
    chain_any = chain_windows(alt, sel_any, "hour gate ANY (all rotations)")

    # ── PERMUTATION NULL FOR THE PER-COIN SELECTION ───────────────────────
    # The allow-list is "pairs with n >= MIN_N and mean > 0 on windows 1-2".
    # To ask whether AVA/INJ/ETC are real or the top of a noisy spread, hold
    # everything fixed except the association: shuffle the pair LABELS across
    # the windows-1-2 trades (so every pair keeps its exact trade count and the
    # outcome pool is identical), rebuild the allow-list, chain it, and score
    # window 3 - the only window neither list was built from.
    import random

    def w3_of(sub) -> float | None:
        ch = ep.chain_result(pol, sub)
        ws = ch.get("windows") or []
        if len(ws) < 3 or not ws[2].get("trades"):
            return None
        return float(ws[2]["return_pct"])

    w12_labels = [r["pair"] for r, _ in sel if w12[0] <= r["ts"] < w12[1]]
    w12_nets = [float(res["net"]) for r, res in sel if w12[0] <= r["ts"] < w12[1]]
    real_w3 = w3_of([(r, res) for r, res in sel if r["pair"] in coin_allow])
    all_w3 = out_chains[0]["w3_return_pct"]
    print()
    print(f"permutation null over {len(w12_labels)} windows-1-2 trades")
    print(f"  real allow-list (n={len(coin_allow)}) window-3 chain: {real_w3:+.3f}%")
    print(f"  all-cells window-3 chain:                            {all_w3:+.3f}%")

    rng = random.Random(20261116)
    n_perm = 2000
    null_w3: list[float] = []
    allow_sizes: list[int] = []
    beat_all = worse_than_real = 0
    for _ in range(n_perm):
        lab = list(w12_labels)
        rng.shuffle(lab)
        acc: dict[str, list[float]] = {}
        for k, v in zip(lab, w12_nets):
            a = acc.get(k)
            if a is None:
                acc[k] = [1, v]
            else:
                a[0] += 1
                a[1] += v
        allow = {k for k, (n, tot) in acc.items()
                 if n >= MIN_N and tot > 0}
        allow_sizes.append(len(allow))
        v = w3_of([(r, res) for r, res in sel if r["pair"] in allow])
        if v is None:
            continue
        null_w3.append(v)
        if v >= all_w3:
            beat_all += 1
        if v <= real_w3:
            worse_than_real += 1

    null_w3.sort()
    m = len(null_w3)

    def pct(frac: float) -> float:
        return null_w3[min(m - 1, int(frac * m))]

    beat_frac = beat_all / m if m else float("nan")
    bad_frac = worse_than_real / m if m else float("nan")
    print(f"  null window-3: mean {float(np.mean(null_w3)):+.3f}% "
          f"median {pct(0.5):+.3f}%  p10 {pct(0.10):+.3f}%  p90 {pct(0.90):+.3f}%")
    print(f"  null beats the all-cells chain : {beat_all}/{m} = {beat_frac:.3f}")
    print(f"  null at or worse than the REAL : {worse_than_real}/{m} = {bad_frac:.3f}"
          f"   -> the real list sits at percentile {bad_frac * 100:.1f}")
    print(f"  allow-list size: real {len(coin_allow)}, "
          f"null median {int(np.median(allow_sizes))}")

    # ── IS A SINGLE COIN'S MEAN UNUSUAL? ───────────────────────────────────
    # The selection test above judges the allow-list as a procedure. This asks
    # the narrower question about the three standouts: a k-trade mean that high
    # would be ordinary if a blind draw of the same size from the same pool
    # reached it often. Sampled WITHOUT replacement from the pool of all
    # selected trades (so a coin's own trades cannot be double counted).
    npr = np.random.default_rng(20261116)
    pool = np.array([float(res["net"]) for _, res in sel])
    coin_null = {}
    for coin in ("AVA/USD", "INJ/USD", "ETC/USD"):
        nets = [t["net"] for t in (per_coin.get(coin) or [])]
        k = len(nets)
        if not k or k >= len(pool):
            continue
        obs = float(np.mean(nets))
        draws = np.array([npr.choice(pool, size=k, replace=False).mean()
                          for _ in range(2000)])
        frac = float((draws >= obs).mean())
        coin_null[coin] = {"n": k, "mean": round(obs, 3),
                           "fallback_pool_mean": round(float(pool.mean()), 3),
                           "frac_blind_draws_at_least_as_good": round(frac, 4),
                           "draw_mean": round(float(draws.mean()), 3),
                           "draw_p95": round(float(np.quantile(draws, 0.95)), 3)}
        print(f"  {coin:<9} n={k:>3} mean {obs:+.2f}%  blind draws of {k} "
              f"reach it {frac * 100:5.1f}% of the time "
              f"(draw mean {float(draws.mean()):+.2f}%, p95 "
              f"{float(np.quantile(draws, 0.95)):+.2f}%)")

    # ── LISTING / RECENCY / LIQUIDITY CHECK ON THE THREE STANDOUTS ───────
    # A per-coin mean is only forward-tradeable if the coin was on the tape for
    # the whole sample (not a listing artefact), its tape reaches the present
    # (not a stale tail), and its hourly dollar volume can absorb the live clip
    # (0.06 x $10k = $600) without the 0.08% charge becoming fiction.
    tape_end = max(df.index.max() for df in pairs.values())
    clip_usd = 600.0
    try:
        import spread_table as _st
        measured = {c: float(_st.roundtrip_pct(c)) for c in
                    ("AVA/USD", "INJ/USD", "ETC/USD")}
    except Exception:
        measured = {}
    quality = {}
    print()
    print("== COIN QUALITY (the three standouts) ==")
    for coin in ("AVA/USD", "INJ/USD", "ETC/USD"):
        trades = per_coin.get(coin) or []
        df = pairs.get(coin)
        if not trades or df is None:
            continue
        ts_list = [pd.Timestamp(t["ts"]) for t in trades]
        nets = [t["net"] for t in trades]
        dv_all = (df["close"] * df["volume"]).dropna()
        on_trade = dv_all.reindex(ts_list).dropna()
        lag_days = (tape_end - df.index.max()).total_seconds() / 86400.0
        months = {}
        for t in ts_list:
            months[t.strftime("%Y-%m")] = months.get(t.strftime("%Y-%m"), 0) + 1
        wcounts = []
        for lo, hi in [(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC"))
                       for a, b in ee.WINDOWS]:
            sub = [n for n, t in zip(nets, ts_list) if lo <= t < hi]
            wcounts.append({"n": len(sub),
                            "mean": round(float(np.mean(sub)), 2) if sub else None,
                            "min": round(float(min(sub)), 2) if sub else None,
                            "max": round(float(max(sub)), 2) if sub else None})
        vol_med = float(on_trade.median()) if len(on_trade) else None
        order = sorted(range(len(nets)), key=lambda j: -nets[j])
        rest = [nets[j] for j in order[1:]]
        top3 = []
        for j in order[:3]:
            dv_hour = dv_all.get(ts_list[j])
            top3.append({"ts": str(ts_list[j])[:16], "net_pct": round(nets[j], 2),
                         "dollar_vol_that_hour": None if dv_hour is None or
                         pd.isna(dv_hour) else round(float(dv_hour), 0)})
        quality[coin] = {
            "n": len(trades),
            "drop_best_trade_mean": round(float(np.mean(rest)), 3) if rest else None,
            "top3_trades": top3,
            "trades": [{"ts": str(t)[:16], "net": round(n, 2)}
                       for t, n in zip(ts_list, nets)],
            "first_trade": str(min(ts_list).date()),
            "last_trade": str(max(ts_list).date()),
            "trades_by_month": months,
            "per_window": wcounts,
            "candles": int(len(df)),
            "tape_start": str(df.index.min().date()),
            "tape_end": str(df.index.max().date()),
            "stale_days_vs_tape": round(lag_days, 1),
            "dollar_vol_median_1h": round(float(dv_all.median()), 0),
            "dollar_vol_median_on_trades": None if vol_med is None
            else round(vol_med, 0),
            "clip_share_of_hourly_vol": None if not vol_med
            else round(clip_usd / vol_med, 3),
            "zero_volume_hours_pct": round(float((dv_all <= 0).mean()) * 100, 1),
            "round_trip_pct_measured": measured.get(coin),
            "round_trip_pct_charged": ee.FEE_RT_PCT,
        }
        q = quality[coin]
        print(f"{coin:<9} n={q['n']:<3} trades {q['first_trade']} .. {q['last_trade']}"
              f"  months={months}")
        print(f"{'':<9} tape {q['tape_start']} -> {q['tape_end']} "
              f"({q['candles']} candles, stale {q['stale_days_vs_tape']:.0f}d vs "
              f"{str(tape_end.date())})")
        print(f"{'':<9} $vol/h median {q['dollar_vol_median_1h']:,.0f}"
              f"  on trades {q['dollar_vol_median_on_trades']:,.0f}"
              f"  -> $600 clip = {q['clip_share_of_hourly_vol'] * 100:.1f}% of an hour"
              f"  zero-vol hours {q['zero_volume_hours_pct']:.0f}%")
        print(f"{'':<9} round trip measured {q['round_trip_pct_measured']}% vs "
              f"charged {q['round_trip_pct_charged']}%")
        for i, w in enumerate(wcounts, 1):
            print(f"{'':<9} w{i}: n={w['n']:<3} mean {w['mean']}  "
                  f"range [{w['min']}, {w['max']}]")
        print(f"{'':<9} months: " + json.dumps(months))
        print(f"{'':<9} mean without its single best trade: "
              f"{q['drop_best_trade_mean']:+.2f}%  (best {top3[0]['net_pct']:+.2f}% "
              f"on {top3[0]['ts']}, that hour's $vol "
              f"{top3[0]['dollar_vol_that_hour']:,.0f})")
        print(f"{'':<9} top3: " + json.dumps(top3))

    payload = {
        "policy": pid,
        "label": pol.label,
        "spec": spec,
        "tape": {"pairs": len(pairs), "signal_rows": len(rows),
                 "selected": len(sel),
                 "through": str(max(r["ts"] for r, _ in sel))},
        "thresholds": {"min_n": MIN_N, "t_min": T_MIN},
        "per_coin": coin_rows,
        "per_rotation": rot_rows,
        "per_rotation_hour_any": rot_any_rows,
        "hour_gate_removed": {"selected": len(sel_any),
                              "chain": chain_any},
        "w12_selection": {
            "coin_allowed": sorted(coin_allow),
            "coin_rejected": sorted(coin_reject),
            "coins_with_w12_evidence": len(coin_acc),
            "rotation_allowed": sorted(rot_allow),
            "rotation_rejected": sorted(rot_reject),
            "rotation_w12": {k: cell_stats(v) for k, v in sorted(rot_acc.items())},
        },
        "chains": out_chains,
        "coin_quality": quality,
        "permutation_test": {
            "statistic": "window-3 chain return pct",
            "n_perm": m,
            "real_allow_list_w3_pct": real_w3,
            "all_cells_w3_pct": all_w3,
            "null_mean_pct": round(float(np.mean(null_w3)), 4),
            "null_median_pct": round(pct(0.5), 4),
            "null_p10_pct": round(pct(0.10), 4),
            "null_p90_pct": round(pct(0.90), 4),
            "frac_null_beats_all_cells": round(beat_frac, 4),
            "frac_null_at_or_worse_than_real": round(bad_frac, 4),
            "real_allow_size": len(coin_allow),
            "null_allow_size_median": int(np.median(allow_sizes)),
            "seed": 20261116,
            "coin_draw_null": coin_null,
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ── the audit: what has no evidence, and why live cannot use any of it ──
    miss = [k for k, v in coin_rows.items() if v["verdict"] == "MISSING"]
    thin = [k for k, v in coin_rows.items() if v["verdict"] == "NO EVIDENCE"]
    print(f"\n== AUDIT ==")
    print(f"coins with an edge      : "
          f"{sum(1 for v in coin_rows.values() if v['verdict'] == 'EDGE')}")
    print(f"coins THIN              : "
          f"{sum(1 for v in coin_rows.values() if v['verdict'] == 'THIN')}")
    print(f"coins with no evidence  : {len(thin)}")
    print(f"coins with NO signals   : {len(miss)} of {len(coin_rows)}")
    print(f"rotations with an edge  : "
          f"{[k for k, v in rot_rows.items() if v['verdict'] == 'EDGE']}")
    print("rotations w1-2 means    : "
          + json.dumps({k: round(float(np.mean(v)), 3)
                        for k, v in sorted(rot_acc.items())}))
    print("rotations EDGE with the gate removed: "
          + str([k for k, v in rot_any_rows.items() if v["verdict"] == "EDGE"]))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
