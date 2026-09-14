"""THE LADDER AUDIT (Sep 11) — does brain_size_mult still match the
scores that lean on it?

brain_size_mult's table (85→1.00 ... 45→0.20, else 0.10) was calibrated
in the old score regime. Two refits now price DOLLARS through it:
  - tide_learn._outcome_fit_bar  (per-tide outcome bars; scores are the
    FUNNEL QUICK scores from the ledgers — live bands 80-97)
  - surething_autorefit / diag_surething_refit  (the Sure Thing weight
    fits; scores are COIL REFIT scores — scale tops ~60, bar 45)

THE CHECK, per population and per LADDER BUCKET (45-55, 55-65, 65-75,
75-85, 85+): payer rate and $/fire on BOTH the 60-min contract (the
tide fits' join) and the 240-min contract (the popup's re-contracted
clock). The ladder matches a population when its $/fire rises with the
bucket — the size multiplier is monotone in score, so any inversion is
the ladder sizing a WEAKER bucket above a STRONGER one. Dead buckets
(no fires in the population) are named; dollars use the house
convention (1055 x mult x 1.5% per payer).

Writes _ladderaudit.txt.
Run: .venv\\Scripts\\python.exe -X utf8 diag_ladderaudit.py
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
OUT = BASE / "_ladderaudit.txt"
STORE = BASE / "surething_data" / "kraken"

import hindsight_scorer as hs
import tide_learn as tl

BASE_USD = 1055.0
GOAL = 0.015
W1, W4 = 12, 48          # 60-min and 240-min contracts on 5m bars
BUCKETS = [(45.0, 55.0, "45-55"), (55.0, 65.0, "55-65"),
           (65.0, 75.0, "65-75"), (75.0, 85.0, "75-85"),
           (85.0, 101.0, "85+")]


def bucket_of(s):
    for lo, hi, name in BUCKETS:
        if lo <= s < hi:
            return name
    return None          # below 45: the 0.10 bucket, nothing fires there


def _bucket_stats(rows):
    """[(name, n, payers_1h, payers_4h, usd_1h, usd_4h)] per bucket."""
    out = {}
    for name in ("45-55", "55-65", "65-75", "75-85", "85+"):
        out[name] = [0, 0, 0, 0.0, 0.0]
    for s, y1, y4 in rows:
        b = bucket_of(s)
        if b is None:
            continue
        m = hs.brain_size_mult(s)
        st = out[b]
        st[0] += 1
        st[1] += bool(y1)
        st[2] += bool(y4)
        if y1:
            st[3] += BASE_USD * m * GOAL
        if y4:
            st[4] += BASE_USD * m * GOAL
    return out


def _fmt(title, stats, key_n, key_p, key_usd, win_label):
    lines = [f"── {title} ({win_label}) ──"]
    total_n = sum(v[key_n] for v in stats.values())
    lines.append(f"{'bucket':<8}{'mult':>6}{'fires':>7}{'payers':>8}"
                 f"{'rate':>8}{'$/fire':>9}")
    for lo, hi, name in BUCKETS:
        n, p1, p4, u1, u4 = stats[name]
        p = p1 if win_label.startswith("60") else p4
        u = u1 if win_label.startswith("60") else u4
        mult = hs.brain_size_mult(lo + 1.0)   # any score in the bucket
        if n == 0:
            lines.append(f"{name:<8}{mult:>6.2f}{n:>7}{'—':>8}{'—':>8}{'—':>9}"
                         f"   (dead)")
            continue
        lines.append(f"{name:<8}{mult:>6.2f}{n:>7}{p:>8}"
                     f"{p / n * 100:>7.1f}%{u / n:>+9.2f}")
    lines.append(f"total fires in population: {total_n}")
    return lines


def quick_population():
    """The tide fits' population: ledgers' quick scores joined to the
    5m store (both contracts). Reuses tide_learn's own machinery."""
    recs = tl._outcome_records()
    if recs is None:
        return None
    recs["bucket"] = recs["ts"].dt.floor("15min")
    recs = (recs.sort_values("quick", ascending=False)
                .drop_duplicates(subset=["pair", "bucket"])
                .reset_index(drop=True))
    rows = []
    cache = {}
    for r in recs.itertuples():
        a = cache.get(r.pair)
        if a is None:
            a = cache[r.pair] = tl._pair_5m_arrs(r.pair)
        if a is None:
            continue
        ts, h, c = a
        i = int(np.searchsorted(ts, int(r.ts.timestamp())))
        if i < 0 or i + 1 + W4 >= len(c):
            continue
        entry = float(c[i])
        if entry <= 0:
            continue
        m1 = float(h[i + 1: i + 1 + W1].max()) / entry - 1.0
        m4 = float(h[i + 1: i + 1 + W4].max()) / entry - 1.0
        rows.append((float(r.quick), m1 >= GOAL, m4 >= GOAL))
    return rows


def coil_population():
    """The Sure Thing fits' population: the replay's episodes scored by
    the LIVE shipped weights, labeled on the 4h contract (the 60-min
    cross-check comes from the same replay's 48-bar MFE series)."""
    import sure_thing as stmod
    import diag_surething_refit as dg
    import surething_autorefit as ar
    rows = []
    for ep in dg.collect():
        r = dg.episode_rows(ep)
        if r is None:
            continue
        hour, feats, _lab60, rec_peak = r
        t_peak = next((t for t, rr in ep["steps"] if rr is rec_peak), None)
        if t_peak is None or t_peak + W4 >= len(ep["arrs"][4]):
            continue
        ts, o, h, l, c, v = ep["arrs"]
        entry = float(c[t_peak])
        if entry <= 0:
            continue
        m1 = float(h[t_peak + 1: t_peak + 1 + W1].max()) / entry - 1.0
        m4 = float(h[t_peak + 1: t_peak + 1 + W4].max()) / entry - 1.0
        score = float(np.array(feats, float)
                      @ np.array([float(stmod.REFIT_W.get(n, 0.0))
                                  for n in dg.FEAT_NAMES]))
        rows.append((score, m1 >= GOAL, m4 >= GOAL))
    return rows


def main():
    lines = ["=== THE LADDER AUDIT — brain_size_mult vs the refit scores ===",
             f"run at: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"table in force: {hs.BRAIN_SIZE_TABLE}",
             "the ladder matches a population when $/fire RISES with the bucket",
             ""]
    verdicts = []

    for title, fn in (("POPULATION A — funnel quick scores "
                       "(what the tide outcome fits price)", quick_population),
                      ("POPULATION B — Sure Thing coil refit scores "
                       "(what the weight fits price)", coil_population)):
        lines.append(f"== {title} ==")
        rows = fn()
        if rows is None:
            lines.append("  no ledger data — skipped")
            verdicts.append((title, None))
            continue
        stats = _bucket_stats(rows)
        for win_label, kn, kp, ku in (("60-min contract", 0, 1, 3),
                                      ("240-min contract", 0, 2, 4)):
            lines.extend(_fmt(title.split("—")[0].strip(), stats, kn, kp, ku,
                              win_label))
            # coherence: among NON-DEAD buckets, does $/fire rise?
            obs = [(name, stats[name][ku] / stats[name][kn])
                   for _, _, name in BUCKETS if stats[name][kn] >= 3]
            if len(obs) >= 2:
                pairs_ok = all(obs[i][1] <= obs[i + 1][1] + 1e-9
                               for i in range(len(obs) - 1))
                verdicts.append((f"{title.split('—')[0].strip()} "
                                 f"[{win_label}]", pairs_ok, obs))
            lines.append("")
    lines.append("── VERDICT ──")
    for v in verdicts:
        if v[1] is None:
            lines.append(f"  {v[0]}: skipped (no data)")
        else:
            obs_s = ", ".join(f"{n}: ${u:+.2f}" for n, u in v[2])
            lines.append(f"  {v[0]}: "
                         + ("MATCHES — $/fire rises with the ladder"
                            if v[1] else "INVERTED — the ladder sizes a "
                            "weaker bucket above a stronger one")
                         + f"  ({obs_s})")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
