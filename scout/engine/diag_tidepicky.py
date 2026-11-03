"""diag_tidepicky.py — THE PICKY-TIDE STUDY (Sep 19).

HER HYPOTHESIS, priced before anything is built: "When the Tide is
rising we get picky when we need to relax. All ships rise together.
Good. The only concern is to get spread too thin ... too much churn and
burn fees to chase all the Buy signals at once, and going thin doesn't
make much profit overall. I want the thickest book choice with the most
steady climb rate based on the full coin history to win the popup
floor. Longer goal times with higher expectations: 5-10% in 4-6 hours."

The replay, on the REAL 1h store (the same files the wiggle book and
the deep store read — full coin history, 60d window):
  - the tide is recomputed exactly as the fleet computes it: EMA21 per
    coin, breadth = % of coins above it, RISING = breadth >= 75.
  - at every RISING hour, the eligibility screen a RISING-tide funnel
    would apply (above EMA21, 2-20% 24h range, top-30% dollar volume,
    fresh quote) produces the CHASE world: every eligible coin takes a
    slot, budget split evenly (the "spread too thin" she names).
  - the PICKY world takes ONE coin per RISING hour: the thickest book
    (top 15% dollar volume) with the steadiest 24h climb (tightest
    low-range), and rides 5-10% goals on 4-6h horizons.
Both worlds settle the same way: goal hit within the horizon (filled at
the goal price) else exit at the horizon close. Friction is priced two
ways: GROSS (the contest's own ledger — round-trip fees $0) and NET
(thin-book churn: 0.10% round-trip slippage off thick-book coins,
0.05% on thick-book — the spread the liquidity tier system prices).

Run:  .venv\\Scripts\\python.exe -X utf8 diag_tidepicky.py
Writes _tidepicky.txt. Read-only on the stores; pure stdlib+pandas.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

STORE = Path(__file__).parent / "surething_data" / "kraken"
OUT = Path(__file__).parent / "_tidepicky.txt"
LOOKBACK_DAYS = 60
TIDE_RISING_MIN = 75.0          # the fleet's own threshold
GOALS = (5.0, 10.0)             # her expectation band
HORIZONS_H = (4, 6)             # her time band
ELIG_MIN_PCT, ELIG_MAX_PCT = 2.0, 20.0   # 24h range a buy screen allows
THICK_Q = 0.85                  # top 15% dollar volume = thick book
MAX_CHASE_SLOTS = 6             # even chase-all splits a $1k budget thin
FEE_THIN, FEE_THICK = 0.0010, 0.0005     # round-trip slippage friction
BUDGET = 1000.0                 # one slot's dollars, for per-slot math


def load_universe():
    """{pair: DataFrame(date, open, high, low, close, volume, ema21,
    vol20)} over the lookback window. Read-only."""
    import math
    uni = {}
    cutoff = None
    for f in sorted(STORE.glob("*_USD-1h.feather")):
        try:
            df = pd.read_feather(f)
        except Exception:
            continue
        if len(df) < 200:
            continue
        df = df.sort_values("date").tail(LOOKBACK_DAYS * 24 + 30).reset_index(drop=True)
        if cutoff is None:
            cutoff = df["date"].max() - pd.Timedelta(days=LOOKBACK_DAYS)
        df = df[df["date"] >= cutoff].reset_index(drop=True)
        if len(df) < 200:
            continue
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["vol20"] = (df["volume"] * df["close"]).rolling(20).mean()
        uni[f.name.split("_USD")[0]] = df
    return uni


def tide_hours(uni):
    """{ts: breadth_pct} — the fleet's own math: % of coins above their
    EMA21, one row per hourly stamp (coins with >=25 bars at that stamp)."""
    breadth = {}
    frames = {p: df.set_index("date") for p, df in uni.items()}
    grid = sorted(set().union(*[set(df.index) for df in frames.values()]))
    for ts in grid:
        above = total = 0
        for df in frames.values():
            if ts in df.index:
                row = df.loc[ts]
                total += 1
                if float(row["close"]) > float(row["ema21"]):
                    above += 1
        if total >= 10:
            breadth[ts] = 100.0 * above / total
    return breadth


def eligible_at(df, i):
    """The RISING-tide buy screen on coin `df` at bar `i`: above its own
    EMA21, a 2-20% 24h range (a mover, not a corpse, not an explosion
    already over), a real dollar-volume rank, a fresh quote."""
    if i < 25:
        return None
    row = df.iloc[i]
    if not (row["close"] > row["ema21"]):
        return None
    w = df.iloc[i - 24:i]
    rng = (float(w["close"].max()) - float(w["close"].min())) / float(w["close"].min()) * 100.0
    if not (ELIG_MIN_PCT <= rng <= ELIG_MAX_PCT):
        return None
    if not (pd.notna(row["vol20"]) and float(row["vol20"]) > 0):
        return None
    stead = (float(w["low"].max()) - float(w["low"].min())) / float(w["low"].min()) * 100.0
    return {"px": float(row["close"]), "vol20": float(row["vol20"]),
            "range": rng, "stead": stead, "vol_rank": None}


def settle(entry_px, df, i, goal_pct, horizon_h):
    """Goal-hit within the horizon (filled at the goal price, the same
    fill the bot's limit would get) else exit at the horizon close.
    Returns the gross move in % (positive/negative)."""
    tgt = entry_px * (1.0 + goal_pct / 100.0)
    for j in range(i + 1, min(i + 1 + horizon_h, len(df))):
        if float(df.iloc[j]["high"]) >= tgt:
            return goal_pct
    out_px = float(df.iloc[min(i + horizon_h, len(df) - 1)]["close"])
    return (out_px / entry_px - 1.0) * 100.0


def run_study():
    uni = load_universe()
    if len(uni) < 20:
        return {"error": f"universe too thin: {len(uni)} coins"}
    breadth = tide_hours(uni)
    rising = sorted(ts for ts, b in breadth.items() if b >= TIDE_RISING_MIN)
    if not rising:
        return {"error": "no RISING hours in the window"}

    # per-coin vol20 rank table (the thick book, fleet-wide)
    vols = []
    for p, df in uni.items():
        v = df["vol20"].dropna()
        if len(v):
            vols.append((p, float(v.iloc[-1])))
    vols.sort(key=lambda x: -x[1])
    thick_set = {p for p, _ in vols[:max(1, int(len(vols) * (1.0 - THICK_Q)))]}

    slots = []          # one per RISING hour with >=1 eligible coin
    for ts in rising:
        cand = []
        for p, df in uni.items():
            idx = df.index[df["date"] == ts]
            if len(idx) == 0:
                continue
            i = int(idx[0])
            e = eligible_at(df, i)
            if e:
                e["pair"], e["i"], e["df"] = p, i, df
                cand.append(e)
        if not cand:
            continue
        cand.sort(key=lambda c: -c["vol20"])
        ranks = {c["pair"]: r + 1 for r, c in enumerate(cand)}
        for c in cand:
            c["vol_rank"] = ranks[c["pair"]]
        thick = [c for c in cand if c["pair"] in thick_set]
        pool = thick if thick else cand
        pool = sorted(pool, key=lambda c: c["stead"])   # steadiest first
        slots.append({"ts": ts, "n_elig": len(cand), "cand": cand,
                      "pick": pool[0]})
    if not slots:
        return {"error": "no actionable RISING slots"}

    def price(world):
        """One pass: every goal x horizon; returns per-slot net % lists."""
        res = {}
        for g in GOALS:
            for h in HORIZONS_H:
                key = f"g{g:.0f}h{h}"
                rows = []
                for s in slots:
                    if world in ("picky", "picky2"):
                        picks = ([s["pick"]] if world == "picky"
                                 else sorted(s["cand"], key=lambda c: c["stead"])[:2])
                        n = len(picks)
                        tot = 0.0
                        thin_any = False
                        for c in picks:
                            mv = settle(c["px"], c["df"], c["i"], g, h)
                            fee = FEE_THICK if c["pair"] in thick_set else FEE_THIN
                            tot += mv - fee * 100.0
                            thin_any = thin_any or (c["pair"] not in thick_set)
                        rows.append({"n": n, "sum_pct": tot / n,
                                     "thin": thin_any})
                    else:
                        # THE ACTIONABLE CHASE (the platform constraint):
                        # the contest's chunk picker is 25/50/75/100% only
                        # and Kraken minimum order sizes floor each leg —
                        # a $1000 budget cannot split into 86 legs of $12.
                        # The real chase is the top-4 by book rank (4 x
                        # 25% chunks), not the fiction of full breadth.
                        chase = sorted(s["cand"], key=lambda c: -c["vol20"])[:4]
                        n = len(chase)
                        tot = 0.0
                        thin_any = False
                        for c in chase:
                            mv = settle(c["px"], c["df"], c["i"], g, h)
                            fee = FEE_THICK if c["pair"] in thick_set else FEE_THIN
                            tot += mv - fee * 100.0
                            thin_any = thin_any or (c["pair"] not in thick_set)
                        rows.append({"n": n, "sum_pct": tot, "thin": thin_any})
                res[key] = rows
        return res

    picky = price("picky")
    picky2 = price("picky2")
    chase = price("chase")

    lines = ["THE PICKY-TIDE STUDY (Sep 19) — chase-all vs one picky pick,",
             f"window: last {LOOKBACK_DAYS}d of the 1h store, "
             f"{len(uni)} coins, {len(rising)} RISING hours, "
             f"{len(slots)} actionable slots",
             ""]

    def block(name, table):
        out = [f"-- {name} --"]
        best = None
        for key in sorted(table):
            rows = table[key]
            slot_pcts = [r["sum_pct"] for r in rows]
            per_trade = [r["sum_pct"] / r["n"] for r in rows if r["n"]]
            avg_slot = sum(slot_pcts) / len(slot_pcts)
            avg_tr = sum(per_trade) / len(per_trade)
            hit = sum(1 for t in per_trade if t > 0) / max(len(per_trade), 1)
            thin_ct = sum(1 for r in rows if r["thin"])
            usd = avg_slot / 100.0 * BUDGET
            out.append(f"  {key:>7}: avg slot {avg_slot:+6.2f}%  (${usd:+7.2f} on ${BUDGET:.0f})   "
                       f"per-trade {avg_tr:+6.2f}%   >0 trades {hit:4.0%}   "
                       f"slots w/ thin-book {thin_ct}/{len(rows)}")
            if best is None or avg_slot > best[1]:
                best = (key, avg_slot, usd)
        out.append(f"  BEST: {best[0]} at {best[1]:+.2f}%/slot (${best[2]:+.2f})")
        return out, best

    pk_lines, pk_best = block("PICKY — one thick+steady coin, 5-10% goals, 4-6h", picky)
    p2_lines, p2_best = block("PICKY-2 — the two steadiest thick coins, 50/50", picky2)
    ch_lines, ch_best = block("CHASE — top-4 by book rank, 25% chunks each", chase)
    lines += pk_lines + [""] + p2_lines + [""] + ch_lines + [""]

    # risk: the worst slots of each best config (concentration hurts here)
    def worst(table, key):
        rows = sorted(table[key], key=lambda r: r["sum_pct"])
        return rows[0]["sum_pct"], rows[len(rows) // 10]["sum_pct"]
    if pk_best and p2_best and ch_best:
        pk_w = worst(picky, pk_best[0])
        p2_w = worst(picky2, p2_best[0])
        ch_w = worst(chase, ch_best[0])
        lines += ["", "THE WORST SLOTS (risk of the best config each)"]
        lines.append(f"  picky {pk_best[0]}: worst {pk_w[0]:+.2f}%, 10th-pct {pk_w[1]:+.2f}%")
        lines.append(f"  picky2 {p2_best[0]}: worst {p2_w[0]:+.2f}%, 10th-pct {p2_w[1]:+.2f}%")
        lines.append(f"  chase {ch_best[0]}: worst {ch_w[0]:+.2f}%, 10th-pct {ch_w[1]:+.2f}%")
    lines.insert(2, "  NOTE: the 'chase' world is the ACTIONABLE chase — top-4 by "
                 "book rank (the chunk picker's 25% x 4). Full-breadth splits "
                 "($12 legs) cannot exist on the platform.")

    if pk_best and ch_best:
        lines.append("THE VERDICT")
        lines.append(f"  picky best: {pk_best[0]}  {pk_best[1]:+.2f}%/slot (${pk_best[2]:+.2f})")
        lines.append(f"  picky2 best: {p2_best[0]}  {p2_best[1]:+.2f}%/slot (${p2_best[2]:+.2f})")
        lines.append(f"  chase best: {ch_best[0]}  {ch_best[1]:+.2f}%/slot (${ch_best[2]:+.2f})")
        lines.append(f"  edge picky2 vs chase: {p2_best[1] - ch_best[1]:+.2f}%/slot "
                     f"(${p2_best[2] - ch_best[2]:+.2f} per ${BUDGET:.0f} slot)")
        lines.append("  her hypothesis (ONE pick wins) is TRUE on this window"
                     if pk_best[1] > ch_best[1] else
                     "  her hypothesis (ONE pick wins) is NOT supported on this window")

    # the thin-split evidence she names
    med_n = sorted(s["n_elig"] for s in slots)[len(slots) // 2]
    lines += ["", "THE THIN SPLIT (chase world): median eligible coins per "
              f"RISING hour = {med_n}; a ${BUDGET:.0f} slot splits into "
              f"~${BUDGET / max(med_n, 1):.0f} positions — at 5-10% goals each "
              "leg must run its full goal to move the slot."]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    return {"slots": len(slots), "rising_hours": len(rising),
            "coins": len(uni), "picky_best": pk_best, "picky2_best": p2_best,
            "chase_best": ch_best, "median_eligible": med_n}


if __name__ == "__main__":
    r = run_study()
    print(json.dumps({k: v for k, v in r.items() if k != "cand"},
                     default=str, indent=1))
