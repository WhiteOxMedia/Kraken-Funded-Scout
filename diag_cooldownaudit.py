"""THE COOLDOWN AUDIT (Sep 11) — what did the 20-24 blackout cost?

The question: the 20-24 MST rotation is a hard block (flatten at 20:00,
NO buys until 00:00). How many Sure Thing candidates did it bury this
week, how many of those paid, and what were those dollars?

THE METHOD (all real machinery, no simulation of the detector):
  - the deep 5m store (the recalibrated Kraken spot candles), every
    bar whose Denver-local time falls in 20:00-24:00, Sep 5-10;
  - the REAL prefilter + stmod.analyze() on each candidate's trailing
    720-bar window (STRIDE 3, identical to diag_surething_refit);
  - the LIVE fire bar (sure_thing.PRE_BREAKOUT_THRESHOLD = 45, the
    refit-era bar) on the refit score analyze() already dispatches;
  - the popup's OWN contract: +1.5% MFE within 240 min (48 bars) —
    the re-contracted clock. The old 60-min window is reported as the
    conservative cross-check;
  - dollars: 1055 base x brain_size_mult(quick) x 1.5% per payer, the
    shared hindsight convention (diag_surething_refit, tide refit);
  - dedupe: max score per pair per 15-min bucket (the funnel's own
    scan-repeat handling).

HONESTY: bars whose forward window runs past the store's edge are
counted as censored, not as misses. The block ALSO suppressed the
funnel upstream (only 9 ledger rows even reached the gates in 20-24
this week), so this is a counterfactual: what the detector WOULD have
fired if the popup ran through the night.

Writes _cooldownaudit.txt.
Run: .venv\\Scripts\\python.exe -X utf8 diag_cooldownaudit.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
OUT = BASE / "_cooldownaudit.txt"
STORE = BASE / "surething_data" / "kraken"

import sure_thing as stmod            # the real detector (refit dispatch on)
import hindsight_scorer as _hs        # the sizing ladder

FIRE_BAR = float(stmod.PRE_BREAKOUT_THRESHOLD)   # 45.0, the refit era
GOAL_PCT = 1.5
WIN_BARS_4H = 48                       # the popup's re-contracted promise
WIN_BARS_1H = 12                       # the conservative cross-check
BASE_USD = 1055.0
STRIDE = 3
ZONE = __import__("zoneinfo").ZoneInfo("America/Denver")

# the week under audit: Sep 5 00:00 -> Sep 11 (the ledgers' window);
# cooldown windows are Denver-local 20:00-24:00, DST-aware
DAYS = [datetime(2026, 9, d, tzinfo=ZONE) for d in range(5, 11)]


def _windows():
    """[(day_label, t0_epoch, t1_epoch)] for each night's 20-24."""
    out = []
    for d in DAYS:
        t0 = int(d.replace(hour=20, minute=0).timestamp())
        t1 = int((d + timedelta(days=1)).timestamp())   # 24:00 == next 00:00
        out.append((d.strftime("%b %d"), t0, t1))
    return out


WINDOWS = _windows()


def _feather_rows(pair: str):
    f = STORE / f"{pair.replace('/', '_')}-5m.feather"
    if not f.exists():
        return None
    try:
        df = pd.read_feather(f)
    except Exception:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    ts = (pd.to_datetime(df["date"], utc=True)
          .astype("datetime64[ns, UTC]").astype("int64") // 10**9
          ).to_numpy(np.int64)
    return (ts, df["open"].to_numpy(float), df["high"].to_numpy(float),
            df["low"].to_numpy(float), df["close"].to_numpy(float),
            np.nan_to_num(df["volume"].to_numpy(float), nan=0.0))


def main():
    lines = ["=== THE 20-24 COOLDOWN AUDIT — what the blackout buried ===",
             f"run at: {datetime.now(ZONE).strftime('%Y-%m-%d %H:%M %Z')}",
             f"fire bar: {FIRE_BAR:.0f} (the refit era) | goal +{GOAL_PCT}% | "
             f"contract: 240 min (48 bars), 60-min cross-check",
             f"nights: {', '.join(w[0] for w in WINDOWS)}",
             ""]
    fires = []          # (night, ts, pair, score, payer4h, mfe4h, payer1h)
    censored = 0
    below_bar = 0       # armed but under the fire bar (context)
    pairs = sorted(p.name.replace("-5m.feather", "").replace("_", "/")
                   for p in STORE.glob("*-5m.feather"))
    import sure_thing as _st_alive  # noqa: F401  (import cost up front)
    for pair in pairs:
        data = _feather_rows(pair)
        if data is None:
            continue
        ts, o, h, l, c, v = data
        n = len(c)
        if n < 800:
            continue
        zh_s = pd.Series(h).rolling(24).max()
        zl_s = pd.Series(l).rolling(24).min()
        zh = zh_s.shift(1).to_numpy()
        zl = zl_s.shift(1).to_numpy()
        thr_arr = zh * (1.0 - stmod.CEILING_TOUCH_PCT / 100.0)
        tests = (pd.Series(h) >= thr_arr).rolling(24).sum().shift(1).to_numpy()
        rng = (zh - zl) / ((zh + zl) / 2.0) * 100.0
        # in-window bar indices (any night)
        mask = np.zeros(n, dtype=bool)
        for _lbl, t0, t1 in WINDOWS:
            mask |= (ts >= t0) & (ts < t1)
        idxs = np.flatnonzero(mask)
        for t in idxs[::STRIDE]:
            if not np.isfinite(zh[t]) or rng[t] > stmod.COIL_MAX_RANGE_PCT \
                    or tests[t] < stmod.MIN_CEILING_TESTS:
                continue
            lo = max(0, t - 719)
            win = list(zip(ts[lo:t + 1], o[lo:t + 1], h[lo:t + 1],
                           l[lo:t + 1], c[lo:t + 1], v[lo:t + 1]))
            r = stmod.analyze(win)
            if not r.get("armed"):
                continue
            score = float(r.get("score") or 0.0)
            if score < FIRE_BAR:
                below_bar += 1
                continue
            # outcome on the popup's 4h contract
            if t + WIN_BARS_4H >= n:
                censored += 1
                continue
            entry = float(c[t])
            if entry <= 0:
                continue
            mfe4 = float(h[t + 1: t + 1 + WIN_BARS_4H].max()) / entry - 1.0
            mfe1 = float(h[t + 1: t + 1 + WIN_BARS_1H].max()) / entry - 1.0
            night = datetime.fromtimestamp(int(ts[t]), ZONE).strftime("%b %d")
            fires.append((night, int(ts[t]), pair, score,
                          mfe4 >= GOAL_PCT / 100.0, mfe4 * 100.0,
                          mfe1 >= GOAL_PCT / 100.0))

    # dedupe: EPISODE-level, per pair — consecutive fires within 30 min
    # are one coil re-arming (the popup fires once; a 15-min bucket
    # split one AIXBT episode into two "fires" on the first run)
    ded = []
    for f in sorted(fires, key=lambda x: (x[2], x[1])):
        if ded and ded[-1][2] == f[2] and f[1] - ded[-1][1] <= 1800 \
                and f[3] <= ded[-1][3] + 1e-9:
            continue                      # same episode, weaker-or-equal
        if ded and ded[-1][2] == f[2] and f[1] - ded[-1][1] <= 1800:
            ded[-1] = f                   # same episode, stronger
            continue
        ded.append(f)
    ded.sort(key=lambda x: x[1])

    def price(payers_scores):
        return sum(BASE_USD * _hs.brain_size_mult(s) * GOAL_PCT / 100.0
                   for s in payers_scores)

    n_f = len(ded)
    payers = [f for f in ded if f[4]]
    payers_1h = [f for f in ded if f[6]]
    usd = price([f[3] for f in payers])
    usd_1h = price([f[3] for f in payers_1h])
    lines.append(f"armed-and-over-the-bar candidates in 20-24 (deduped): {n_f}"
                 + (f"  (+{censored} censored at the store's edge)"
                    if censored else ""))
    lines.append(f"armed but under the {FIRE_BAR:.0f} bar: {below_bar}")
    if n_f == 0:
        lines.append("")
        lines.append("VERDICT: the cooldown buried nothing this week — the "
                     "detector never armed over the bar in the night windows.")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return
    rate4 = len(payers) / n_f
    rate1 = len(payers_1h) / n_f
    lines.append("")
    lines.append(f"THE 4h CONTRACT (the popup's own promise): "
                 f"{len(payers)}/{n_f} paid = {rate4 * 100:.1f}%  "
                 f"-> ${usd:.2f} total, ${usd / n_f:+.2f}/fire")
    lines.append(f"the 60-min cross-check (the old clock):        "
                 f"{len(payers_1h)}/{n_f} paid = {rate1 * 100:.1f}%  "
                 f"-> ${usd_1h:.2f} total, ${usd_1h / n_f:+.2f}/fire")
    lines.append("")
    lines.append("── per night ──")
    for lbl, t0, t1 in WINDOWS:
        nf = [f for f in ded if t0 <= f[1] < t1]
        np_ = [f for f in nf if f[4]]
        u = price([f[3] for f in np_])
        lines.append(f"  {lbl} 20-24: {len(nf):3d} fires, "
                     f"{len(np_):3d} payers ({(len(np_) / len(nf) * 100 if nf else 0):4.1f}%), "
                     f"${u:7.2f}")
    lines.append("")
    lines.append("── by score band ──")
    for lo, hi in ((45, 55), (55, 65), (65, 101)):
        band = [f for f in ded if lo <= f[3] < hi]
        if not band:
            continue
        bp = [f for f in band if f[4]]
        ub = price([f[3] for f in bp])
        lines.append(f"  {lo}-{min(hi, 100)}: {len(band):3d} fires, "
                     f"{len(bp):3d} payers ({len(bp) / len(band) * 100:4.1f}%), "
                     f"${ub:7.2f} (${ub / len(band):+.2f}/fire)")
    lines.append("")
    top = sorted(payers, key=lambda f: -(f[5]))[:8]
    lines.append("── the biggest buried payers ──")
    for f in top:
        ts_h = datetime.fromtimestamp(f[1], ZONE).strftime("%b %d %H:%M")
        lines.append(f"  {f[2]:<14} {ts_h}  score {f[3]:.0f}  "
                     f"mfe +{f[5]:.1f}%  "
                     f"${BASE_USD * _hs.brain_size_mult(f[3]) * GOAL_PCT / 100:.2f}")
    lines.append("")
    lines.append("READ: a positive $/fire means the 20-24 block buried real "
                 "money this week; the per-night table shows whether it is "
                 "one night or the pattern. Censored bars are excluded "
                 "everywhere (never counted as misses).")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
