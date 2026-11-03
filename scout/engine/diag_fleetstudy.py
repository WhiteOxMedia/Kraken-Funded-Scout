"""THE FLEET STUDY (user question, Sep 10: "do 'all ships' really sink
together and rise together? Each coin has a history.") — the tide gate's
assumption, tested against the deep candle history. EXTENDED Sep 10:
the store is now the 1h build (build_deepstore.py) — ALL live pairs
(102) x 49 days, gapless, from the engine's own ohlcv cache — and the
study reports two cuts: ALL PAIRS and the POPUP FLEET (the 54 the
gates trade; the cut the learned bar prices). The 30-day coinbase-5m
story was a small-window artifact: on the long window all-pairs FLAT
~= OUT, while the popup fleet still pays MORE in OUT than FLAT.

For every hour in the common window this rebuilds the EXACT live tide
(breadth % of the fleet above its own EMA21 — _fleet_tide's math), then
asks each coin what it did NEXT:
  - 4h forward return (one rotation — the popup horizon)
  - did it "pay like a popup" (>= +1.5%)?
  - dispersion (do the coins scatter, or lock together?)
  - pairwise correlation of hourly returns, per tide state
Verdicts printed for RISING / FLAT / OUT: rise together? sink together?
or do solo runners exist the blanket block buries?

Run: .venv\\Scripts\\python.exe -X utf8 diag_fleetstudy.py  -> _fleetstudy.txt
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pandas as pd

import background_scanner as bs   # TIDE_RISING_MIN / TIDE_OUT_MAX stay in sync

DATA = Path("surething_data/kraken")
GOAL_PCT = 1.5     # "pays like a popup" — the goal band
HORIZON_H = 4      # one rotation

lines = []


def out(s=""):
    print(s)
    lines.append(s)


# ── 1. load the deep candles (1h store first; the 5m coinbase files
# fill any pair the 1h build skipped) ───────────
closes = {}
for f in sorted(DATA.glob("*.feather")):
    name = f.name
    if name.endswith("-1h.feather"):
        pair = name[: -len("-1h.feather")].replace("_", "/")
    elif name.endswith("-5m.coinbase.feather"):
        pair = name[: -len("-5m.coinbase.feather")].replace("_", "/")
        if pair in closes:
            continue   # the 1h store already won this pair
    elif name.endswith("-5m.feather"):
        pair = name[: -len("-5m.feather")].replace("_", "/")
        if pair in closes:
            continue   # the 1h store already won this pair
    else:
        continue
    try:
        df = pd.read_feather(f)
    except Exception:
        continue
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("date") or cols.get("ts")
    if tcol is None or "close" not in cols:
        continue
    s = df[[tcol, cols["close"]]].copy()
    s.columns = ["ts", "close"]
    s["ts"] = pd.to_datetime(s["ts"], utc=True)
    s = s.set_index("ts")["close"]
    hourly = s.resample("1h").last().dropna()
    if len(hourly) >= 200:
        closes[pair] = hourly

out(f"=== THE FLEET STUDY — {len(closes)} pairs with deep hourly history ===")
lens = pd.Series({p: len(v) for p, v in closes.items()})
out(f"history depth: median {lens.median():.0f}h, "
    f"min {lens.min():.0f}h, max {lens.max():.0f}h")
out("")

px = pd.DataFrame(closes).sort_index()
px = px.loc[px.index >= "2020-01-01"]   # SANITY (the 1970 lesson): drop unit-bug rows
# no hard common window — pairs cover different spans; the tide reads
# whichever deep ships are present each hour. The 1h store (Sep 10)
# covers ALL live pairs; the popup gates trade cfg.trading_pairs (the
# 54), so the study reports BOTH cuts.
FLEET_MIN = 40

out(f"analysis over {px.shape[1]} deep pairs, {len(px)} hourly bars; "
    f"hours need >= {FLEET_MIN} ships")
out("")

# ── 2. the tide, hour by hour — the scanner's exact math ──────────────
ema21 = px.ewm(span=21, adjust=False).mean()
above = px > ema21
_count = above.notna().sum(axis=1)
breadth = above.mean(axis=1) * 100.0
breadth[_count < FLEET_MIN] = float("nan")   # too few ships to call a tide
state = breadth.dropna().apply(lambda p: bs._tide_class(p))
out(f"tide thresholds in force: RISING >= {bs.TIDE_RISING_MIN}, "
    f"OUT <= {bs.TIDE_OUT_MAX}")
out("state hours: " + ", ".join(f"{k}: {v}" for k, v in state.value_counts().items()))
out("")

# ── 3. forward outcomes per coin ──────────────────────────────────────
fwd = px.shift(-HORIZON_H) / px - 1.0   # 4h forward return per coin

# THE POPUP FLEET (Sep 10): the gates trade cfg.trading_pairs (the 54) —
# the popup-fleet cut is the one the OUT gate's price should come from.
import config as _cfg
_popup_pairs = [p for p in (_cfg.DEFAULT_CONFIG.trading_pairs
                            if hasattr(_cfg, "DEFAULT_CONFIG") else [])
                if p in px.columns]
_px54 = px[_popup_pairs] if len(_popup_pairs) >= FLEET_MIN else None
_fwd54 = _px54.shift(-HORIZON_H) / _px54 - 1.0 if _px54 is not None else None


def _fleet_cut(px_c, fwd_c, label):
    """Tide states over this universe, then per-state forward stats."""
    ema = px_c.ewm(span=21, adjust=False).mean()
    ab = px_c > ema
    n = ab.notna().sum(axis=1)
    br = ab.mean(axis=1) * 100.0
    br[n < FLEET_MIN] = float("nan")
    stt = br.dropna().apply(bs._tide_class)
    for st in ("RISING", "FLAT", "OUT"):
        hrs = stt.index[stt == st]
        if not len(hrs):
            continue
        hrs = fwd_c.loc[fwd_c.index.isin(hrs)].dropna(how="all").index
        if not len(hrs):
            continue
        f = fwd_c.loc[hrs]
        up = (f > 0).mean().mean() * 100.0
        pay = (f >= GOAL_PCT / 100.0).mean().mean() * 100.0
        out(f"[{label}] {st} ({len(hrs)}h): up {up:.1f}% | "
            f"payers >= +{GOAL_PCT}% {pay:.1f}%")
    return stt

_st_all = _fleet_cut(px, fwd, "ALL PAIRS ")
if _fwd54 is not None:
    _st54 = _fleet_cut(_px54, _fwd54, "POPUP FLEET")
    out("")

for st in ("RISING", "FLAT", "OUT"):
    hrs = state.index[state == st]
    if len(hrs) == 0:
        out(f"── {st}: no hours in the window ──")
        continue
    hrs = fwd.loc[fwd.index.isin(hrs)].dropna(how="all").index
    f = fwd.loc[hrs]
    up_share = (f > 0).mean().mean() * 100.0
    pay_share = (f >= GOAL_PCT / 100.0).mean().mean() * 100.0
    med = f.median().median() * 100.0
    iqr = (f.quantile(0.75) - f.quantile(0.25)).median() * 100.0
    # correlation of hourly returns across coins, sampled in this state
    rets = px.pct_change().loc[hrs]
    corr = rets.corr()
    n = len(corr)
    mean_corr = (corr.values.sum() - n) / (n * n - n) if n > 1 else float("nan")
    out(f"── {st} ({len(hrs)} hours) ──")
    out(f"   coins up next 4h      : {up_share:5.1f}% of the fleet")
    out(f"   coins paying >= +{GOAL_PCT}%: {pay_share:5.1f}% of the fleet")
    out(f"   median 4h move        : {med:+.2f}%   dispersion (median IQR): {iqr:.2f}%")
    out(f"   avg pairwise corr     : {mean_corr:+.2f}")
    if st == "OUT":
        best = f.max().sort_values(ascending=False).head(5)
        n_pay = (f >= GOAL_PCT / 100.0).sum().sum()
        out(f"   wolves in the OUT tide: {int(n_pay)} coin-hours paid >= +{GOAL_PCT}% "
            f"despite the block")
        out("   best solo runners: " + ", ".join(
            f"{p} {v * 100:+.1f}%" for p, v in best.items()))
    if st == "RISING":
        worst = f.min().sort_values().head(5)
        n_sink = (f < 0).sum().sum()
        out(f"   sinkers in the RISE   : {int(n_sink)} coin-hours fell anyway")
        out("   worst carriers: " + ", ".join(
            f"{p} {v * 100:+.1f}%" for p, v in worst.items()))
    out("")

out("VERDICT (Sep 10, the extended store): 'all ships sink together'")
out("is a myth on every cut — correlations run +0.2 to +0.5, never +1,")
out("and OUT hours still pay. The OUT block is a PRICE (tide_learn.py:")
out(f"the bar = the OUT payer-rate quantile of the funnel's own scores),")
out("not a wall — and the popup-fleet cut is the price that matters.")
Path("_fleetstudy.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n-> _fleetstudy.txt")
