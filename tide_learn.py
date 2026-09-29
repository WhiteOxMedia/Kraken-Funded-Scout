"""THE TIDE LEARNER (Sep 10) — the OUT gate learns from the deep candle
store instead of assuming "all ships sink together".

THE EVIDENCE (diag_fleetstudy.py -> _fleetstudy.txt, EXTENDED Sep 10:
the 1h store from build_deepstore.py — 102 live pairs x 49 days,
gapless, from the engine's own cache): OUT tides are NOT a zero-payer
world. On the longer window the 30-day ranking flips by universe —
all-pairs FLAT ~= OUT (~12%), but the POPUP FLEET (the 54 the gates
trade) still pays MORE in OUT hours than FLAT (12.3% vs 9.4%, and
52.7% of the fleet rises next 4h in OUT hours). Correlations run
+0.22..+0.25 — never the lockstep the block assumed.

THE MODEL: per tide state, measure the POPUP FLEET's forward payer
rate over the deep history (the states stay all-pairs, mirroring the
live gate); a candidate may fire in that tide when its quick score
clears the bar for its state's top slice — the (1 - payer_rate)
quantile of the funnel's OWN historical quick-score distribution. A
12% payer tide demands a top-12% score; a 23% payer tide demands a
top-23% score. The tide stops being a wall and becomes a price.

THE OUT-BAR REFIT (Sep 11, diag_tidebarrefit.py -> _tidebarrefit.txt):
the fixed quantile assumed score rank == payer rank — the same
assumption the Sure Thing refit caught backwards. Fitting the
score->payer mapping on LIVE FUNNEL OUTCOMES (suppressed candidates
joined to the deep 5m store, the 60-min contract) moved the bar:
quantile 94 fired 3 holdout candidates at 0% payers ($0.00/fire)
while the outcome-fitted 80 fired 12 at 25% (+$3.30/fire) — and the
old [86,97] clamp excluded the 80-90 band where the payers live
(train fires above 86: 36 candidates, zero payers). OUT now ships the
EV-max OUTCOME bar when the evidence is thick (>= 40 joined
candidates, >= 6 fires at the bar, in-sample EV >= the quantile's).
The outcome bar clamps [80, 97].

THE PER-STATE EXTENSION (Sep 11): the same refit now runs for EVERY
tide state — the ledgers stamp each candidate with its live tide, so
FLAT and RISING candidates join the store exactly like OUT's. Each
state's bar ships on ITS OWN evidence (the same >= 40 / >= 6 / EV
gates measured within that state); thin or losing evidence keeps that
state on the quantile, named honestly in its record. The clamp floor
stays OUTCOME_BAR_FLOOR until a state's own EV curve earns a
different one.

THE AUTO-FLIP (Sep 11, tidebar_weekly.py): hindsight can veto the
in-sample gates. When a state's outcome bar loses to the quantile 3
weeks running AND on the pooled 4-week window, tidebar_weekly writes
tide_bar_flips.json and this module HONORS it: the flipped state
ships the quantile (its record says so in 'why') until the bar wins 2
weeks running plus the pool — then the flip-back lands and the bar
returns. Fail-open to the fit when the flip file is missing or
unreadable.

FAIL-CLOSED: no store, too few hours, a stale cache, or thin outcome
evidence -> the quantile bar, or bar None -> the caller keeps the
blanket block. The learner never OPENS a gate it cannot measure.
"""
import json
import math
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
DEEP_DIR = ROOT / "surething_data" / "kraken"
SUPPRESSIONS_FILE = ROOT / "popup_suppressions.jsonl"
SNAPSHOTS_FILE = ROOT / "signal_snapshots.jsonl"

# THE 1H STORE (Sep 10, build_deepstore.py): all live pairs, gapless
# hourly closes from the engine's own cache — the primary. The 5m
# coinbase files remain a fallback for pairs the 1h build skipped.
HORIZON_H = 4          # the popup horizon (one rotation)
GOAL_PCT = 1.5         # "pays like a popup"
FLEET_MIN = 40         # deep ships needed to call an hour's tide
MIN_HOURS = 48         # state needs this many hours of history to price it
BAR_MIN, BAR_MAX = 86.0, 97.0   # the quantile bar never leaves this band
# THE OUTCOME BAR (Sep 11): fitted from live funnel outcomes, not a
# quantile. Its floor is EVIDENCE (diag_tidebarrefit.py: the holdout
# EV curve is positive 78-84, $0.00 at the old 86-97 regime in train),
# so the clamp sits lower than the quantile's.
OUTCOME_BAR_FLOOR = 80.0   # the no-relief floor: a bar below this ships
                           # only through the EV ladder below
# THE EV LADDER (Sep 23, the burial-ledger tuning: the 08-12 class sat at
# quick 57-72 — 15-30 pts under the shipped 88 — and the study's EV curve
# peaked at bar 72, $4.87/fire vs the shipped regime's $3.30, while BOTH
# proposed reliefs failed the same data: no flat relief reaches the class
# (nothing fires between 76 and 89) and a lone-wolf move gate separates
# nothing (payers at +0.13%, duds at +16.3%). The real lock was the
# FLOOR: the fit could see the 72-band and was forbidden to ship it.
# The ladder answers with evidence, not a moved constant: a bar below
# 80 ships only when it beats the best >=80 bar by EV_LADDER_MARGIN
# IN-SAMPLE on the state's own outcomes — mispriced-funnel evidence
# opens the band, ordinary evidence keeps the 80 floor (fail-closed).
OUTCOME_BAR_FLOOR_MIN = 68.0   # the ladder's absolute floor (the study's
                               # EV curve was already declining below 68)
EV_LADDER_MARGIN = 1.15        # the below-80 bar must beat the >=80 bar
                               # by 15% EV to ship
OUTCOME_MIN_JOIN = 40  # joined candidates IN THE STATE needed to fit it
OUTCOME_MIN_FIRES = 6  # a candidate bar needs this many fires in-sample
OUTCOME_WINDOW_BARS = 12   # the 60-min contract on 5m candles
OUTCOME_GOAL_PCT = 1.5
OUTCOME_BASE_USD = 1055.0  # the refit's $/fire convention
CACHE_TTL_S = 1800.0   # re-learn every 30 min (the store refreshes daily)

# THE AUTO-FLIP LEDGER (Sep 11, tidebar_weekly.py): hindsight's veto on
# the in-sample ship gates. A state flipped True ships the QUANTILE
# until the weekly hindsight flips it back. Fail-open: no file -> no flip.
FLIP_FILE = ROOT / "tide_bar_flips.json"


def _flipped_states():
    try:
        d = json.loads(FLIP_FILE.read_text(encoding="utf-8"))
        return {s for s, v in (d or {}).items()
                if isinstance(v, dict) and v.get("flipped")}
    except Exception:
        return set()

_cache = {"data": None, "ts": 0.0, "err": None}
_lock = threading.Lock()


def _load_deep_fleet():
    """{pair: hourly close Series} from the deep store; the 1h store
    wins per pair, the 5m coinbase files cover the rest. Returns
    (df, err)."""
    import pandas as pd
    closes = {}
    try:
        files = sorted(DEEP_DIR.glob("*.feather"))
    except Exception as exc:
        return None, str(exc)
    for f in files:
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
        try:
            s["ts"] = pd.to_datetime(s["ts"], utc=True)
        except Exception:
            continue
        hourly = s.set_index("ts")["close"].resample("1h").last().dropna()
        if len(hourly) >= 200:
            closes[pair] = hourly
    if len(closes) < FLEET_MIN:
        return None, f"only {len(closes)} deep pairs (need >= {FLEET_MIN})"
    return pd.DataFrame(closes).sort_index(), None


def _tide_states(px, tide_class):
    """Per-hour breadth over the deep fleet -> state Series (the scanner's
    math: % of ships above their own EMA21)."""
    import pandas as pd
    ema21 = px.ewm(span=21, adjust=False).mean()
    above = px > ema21
    n = above.notna().sum(axis=1)
    breadth = above.mean(axis=1) * 100.0
    breadth[n < FLEET_MIN] = float("nan")
    return breadth.dropna().apply(tide_class)


def _funnel_score_quantiles():
    """The funnel's OWN quick-score history (the suppression log's 'quick'
    fields — real candidates the gates saw, not a synthetic ladder).
    Returns the sorted list of scores, or None."""
    scores = []
    try:
        with open(SUPPRESSIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    q = r.get("quick")
                    if q is not None:
                        q = float(q)
                        if 0 < q <= 100:
                            scores.append(q)
                except Exception:
                    continue
    except Exception:
        return None
    if len(scores) < 50:
        return None
    return sorted(scores)


def _quantile(sorted_scores, q):
    """Quantile (0..1) of a pre-sorted list (linear interpolation)."""
    if not sorted_scores:
        return None
    idx = q * (len(sorted_scores) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(sorted_scores[int(idx)])
    frac = idx - lo
    return float(sorted_scores[lo] * (1 - frac) + sorted_scores[hi] * frac)


# ── THE OUTCOME REFIT (Sep 11) — the OUT bar fitted from live funnel
# outcomes instead of the fixed quantile. diag_tidebarrefit.py priced
# it: quantile 94 -> 3 holdout fires, 0 payers, $0.00/fire; the
# outcome-fitted 80 -> 12 fires at 25% payers, +$3.30/fire.
def _outcome_records():
    """Suppressed candidates from both ledgers with their live tide
    stamp: a DataFrame [ts, pair, quick, state], or None. The 'tide'
    stamp reads like 'OUT 30%' — the state is the first token; every
    state's rows are kept so each tide's fit sees only its own."""
    import pandas as pd
    rows = []
    for path in (SUPPRESSIONS_FILE, SNAPSHOTS_FILE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    tide = ((r.get("tide") or "").split() or [""])[0].upper()
                    q = r.get("quick")
                    if tide not in ("OUT", "FLAT", "RISING") or q is None:
                        continue
                    try:
                        rows.append({"ts": pd.to_datetime(r["ts"], utc=True),
                                     "pair": str(r["pair"]),
                                     "quick": float(q),
                                     "state": tide})
                    except Exception:
                        continue
        except Exception:
            continue
    if not rows:
        return None
    return pd.DataFrame(rows)


def _pair_5m_arrs(pair):
    """(ts_s int64 sorted, high, close) from the pair's native 5m
    feather, or None."""
    import numpy as np
    import pandas as pd
    fn = DEEP_DIR / (pair.replace("/", "_") + "-5m.feather")
    if not fn.exists():
        return None
    try:
        df = pd.read_feather(fn)
        cols = {c.lower(): c for c in df.columns}
        tcol = cols.get("date") or cols.get("ts")
        if tcol is None or "close" not in cols or "high" not in cols:
            return None
        ts = (pd.to_datetime(df[tcol], utc=True)
              .astype("datetime64[ns, UTC]").astype("int64") // 10**9
              ).to_numpy(np.int64)
        order = np.argsort(ts)
        return (ts[order], df["high"].to_numpy(float)[order],
                df["close"].to_numpy(float)[order])
    except Exception:
        return None


def _ev_ladder_floor(fit: dict | None) -> float:
    """The floor the state's OWN evidence earns: OUTCOME_BAR_FLOOR_MIN
    when a computed fit clears the ladder (positive EV beating the best
    >=80 bar by EV_LADDER_MARGIN), else the static OUTCOME_BAR_FLOOR.
    Called with a fit-shaped dict {"ev_o", "ev_q"} or None — never
    raises, never returns a floor above OUTCOME_BAR_FLOOR."""
    try:
        if (fit and float(fit.get("ev_o") or 0.0) > 0.0
                and float(fit.get("ev_o")) >= float(fit.get("ev_q") or 0.0)
                * EV_LADDER_MARGIN):
            return OUTCOME_BAR_FLOOR_MIN
    except Exception:
        pass
    return OUTCOME_BAR_FLOOR


def _outcome_fit_bar(state, scores, arr_cache=None):
    """The EV-max bar for ONE tide state, fitted on that state's own
    live-candidate outcomes, or None when the state's evidence is thin
    (that state keeps the quantile — fail-closed). $/fire follows the
    refit's convention: 1055 base x brain_size_mult x 1.5% on payers.
    Chronological 70/30 split within the state; the bar is picked on
    train and the caller's in-sample EV check must not lose to the
    state's quantile bar. arr_cache is shared across states so one
    learn() pass reads each pair's 5m candles once."""
    try:
        import numpy as np
        import pandas as pd
        import hindsight_scorer as _hs
        recs = _outcome_records()
        if recs is None:
            return None
        recs = recs[recs["state"] == state]
        if len(recs) < OUTCOME_MIN_JOIN:
            return None
        recs["bucket"] = recs["ts"].dt.floor("15min")
        recs = (recs.sort_values("quick", ascending=False)
                    .drop_duplicates(subset=["pair", "bucket"])
                    .reset_index(drop=True))
        joined = []
        if arr_cache is None:
            arr_cache = {}
        for r in recs.itertuples():
            a = arr_cache.get(r.pair)
            if a is None:
                a = arr_cache[r.pair] = _pair_5m_arrs(r.pair)
            if a is None:
                continue
            i = int(np.searchsorted(a[0], int(r.ts.timestamp())))
            ts, h, c = a
            if i < 0 or i + 1 + OUTCOME_WINDOW_BARS > len(c):
                continue
            entry = float(c[i])
            if entry <= 0:
                continue
            mfe = float(h[i + 1: i + 1 + OUTCOME_WINDOW_BARS].max()) / entry - 1.0
            joined.append({"ts": r.ts, "quick": r.quick,
                           "payer": mfe >= OUTCOME_GOAL_PCT / 100.0})
        if len(joined) < OUTCOME_MIN_JOIN:
            return None
        J = pd.DataFrame(joined).sort_values("ts").reset_index(drop=True)
        O = J[J["payer"].notna()]
        split = O["ts"].quantile(0.70)
        tr = O[O["ts"] <= split]
        if len(tr) < OUTCOME_MIN_JOIN:
            return None
        s_tr = tr["quick"].to_numpy(float)
        y_tr = tr["payer"].to_numpy(bool)

        def ev(s, y, bar):
            m = s >= bar
            n = int(m.sum())
            if not n:
                return 0.0, 0
            usd = sum(OUTCOME_BASE_USD * _hs.brain_size_mult(float(si)) *
                      (OUTCOME_GOAL_PCT / 100.0 if yy else 0.0)
                      for si, yy in zip(s[m], y[m]))
            return usd / n, n

        rate_tr = float(y_tr.mean())
        # THE EV LADDER: search the full band, then let the state's own
        # evidence choose the floor — a below-80 bar ships only when it
        # beats the best >=80 bar by EV_LADDER_MARGIN (mispriced funnel);
        # otherwise the static 80 floor stands (or None if nothing there
        # has enough fires — the quantile fallback, fail-closed).
        best_low = None    # EV-max over the whole band (>= FLOOR_MIN)
        best_80 = None     # EV-max over the static floor (>= 80)
        for B in range(int(OUTCOME_BAR_FLOOR_MIN), 98):
            e, n_fire = ev(s_tr, y_tr, float(B))
            if n_fire < OUTCOME_MIN_FIRES:
                continue
            if best_low is None or e > best_low[1]:
                best_low = (float(B), e)
            if B >= int(OUTCOME_BAR_FLOOR) and (best_80 is None or e > best_80[1]):
                best_80 = (float(B), e)
        if best_low is None:
            return None
        if best_low[0] >= OUTCOME_BAR_FLOOR:
            chosen = best_low                       # old regime
        if best_80 is None:
            # review triage (Sep 27): fewer than six training fires
            # scored at or above 80 - the >=80 band carries no
            # evidence to compare against, so shipping a below-80
            # bar here skipped the ladder's own EV comparison.
            # Fail CLOSED: return no outcome fit and let the
            # quantile fallback stay in force (the comment above
            # always promised this; the code finally keeps it).
            return None
        elif best_low[1] > 0.0 and best_low[1] >= best_80[1] * EV_LADDER_MARGIN:
            chosen = best_low                       # the ladder ADMITS: mispriced
            # (round-3 review: admission also demands POSITIVE EV — a
            # 0-vs-0 tie admitted a below-80 bar whose fires pay
            # NOTHING; zero evidence of mispricing is the DENY case)
        else:
            chosen = best_80                        # the ladder DENIES: static floor
        bar_o = round(max(OUTCOME_BAR_FLOOR_MIN, min(97.0, chosen[0])), 1)
        ev_o, _ = ev(s_tr, y_tr, bar_o)
        bar_q = round(max(BAR_MIN, min(BAR_MAX,
                      _quantile(scores, max(0.0, min(1.0, 1.0 - rate_tr))))), 1)
        ev_q, _ = ev(s_tr, y_tr, bar_q)
        return {"bar": bar_o, "ev_o": ev_o, "ev_q": ev_q, "n": len(O),
                "payer_rate": rate_tr,
                "ev_ladder": bar_o < OUTCOME_BAR_FLOOR}
    except Exception:
        return None


def learn(tide_class=None, now=None):
    """The full learning pass. Returns:
    {generated_ts, hours, window, states: {OUT: {payer_rate, hours, bar},
     FLAT: ..., RISING: ...}, err}
    bar = the (1 - payer_rate) quantile of the funnel's quick-score
    history, clamped [BAR_MIN, BAR_MAX]; None when that state's evidence
    is too thin or the score history is missing. NEVER raises: every
    outcome (model or failure) is cached with a fresh ts, so callers
    rate-limit themselves to one build per TTL."""
    import pandas as pd
    if tide_class is None:
        # lazy import — the scanner imports THIS module, never the reverse
        import background_scanner as bs
        tide_class = bs._tide_class
    with _lock:
        px, err = _load_deep_fleet()
        if px is None:
            # CACHE THE FAILURE (ts is stamped) — a broken store rate-
            # limits to one attempt per TTL instead of re-reading every
            # feather file on every gate hit.
            _cache.update(data=None, ts=time.time(), err=err)
            return {"err": err, "states": {}, "generated_ts": None,
                    "hours": 0, "window": None}
        try:
            states = _tide_states(px, tide_class)
            fwd = px.shift(-HORIZON_H) / px - 1.0
            # THE POPUP FLEET PAYS THE PRICE (Sep 10, the extended
            # store): payer rates are measured over cfg.trading_pairs
            # (the 54 the gates trade) — the fleet study's two cuts
            # disagree (microcap pumps inflate the all-pairs rate), and
            # the bar must price what the popups could have monetized.
            # The tide STATES still mirror the live gate: breadth over
            # the whole store, exactly like _fleet_tide's cache read.
            try:
                import config as _cfgmod
                _pp = [p for p in _cfgmod.DEFAULT_CONFIG.trading_pairs
                       if p in px.columns]
            except Exception:
                _pp = []
            fpx = px[_pp] if len(_pp) >= FLEET_MIN else px
            ffwd = fpx.shift(-HORIZON_H) / fpx - 1.0
            scores = _funnel_score_quantiles()
            out = {"OUT": {}, "FLAT": {}, "RISING": {}}
            # THE PER-STATE OUTCOME FITS (Sep 11): every tide state's bar
            # is fitted on ITS OWN candidates when its own evidence is
            # thick; the pair-candle cache is shared so one pass reads
            # each 5m feather once across all three fits.
            fits = {}
            _flipped = _flipped_states()
            if scores is not None:
                _arr_cache: dict = {}
                for _st in out:
                    # A state flipped by the weekly hindsight keeps its
                    # fit computed (the report counterfactuals it every
                    # week) but the QUANTILE ships live until the
                    # flip-back. The fit still rides the EV-vs-quantile
                    # gate below, so a benched bar cannot return by itself.
                    if _st not in _flipped:
                        fits[_st] = _outcome_fit_bar(_st, scores,
                                                     arr_cache=_arr_cache)
            for st in out:
                hrs = states.index[states == st]
                hrs = [h for h in hrs if h in ffwd.dropna(how="all").index]
                if len(hrs) < MIN_HOURS:
                    out[st] = {"payer_rate": None, "hours": len(hrs), "bar": None,
                               "why": f"only {len(hrs)}h of history (need >= {MIN_HOURS})"}
                    continue
                f = ffwd.loc[hrs]
                rate = float((f >= GOAL_PCT / 100.0).mean().mean())
                bar = None
                why = None
                bar_src = "quantile"
                if scores is None:
                    why = "no funnel score history"
                elif st in _flipped:
                    why = ("auto-flipped to the quantile by the weekly "
                           "hindsight (the outcome bar underperformed "
                           "3 weeks running); it returns when it wins "
                           "2 weeks running plus the pooled window")
                elif st in fits and fits[st] is not None \
                        and fits[st]["ev_o"] >= fits[st]["ev_q"]:
                    # THE OUTCOME BAR SHIPS FOR THIS STATE: fitted on the
                    # state's own live funnel outcomes, EV-max in-sample.
                    # Otherwise the quantile stays.
                    bar = fits[st]["bar"]
                    bar_src = "outcomes"
                else:
                    raw = _quantile(scores, max(0.0, min(1.0, 1.0 - rate)))
                    bar = round(max(BAR_MIN, min(BAR_MAX, raw)), 1)
                    _fit = fits.get(st)
                    if _fit is not None:
                        why = ("outcome fit lost to the quantile in-sample; "
                               "quantile bar (fallback)")
                    elif scores is not None:
                        why = ("outcome fit thin; quantile bar (fallback)")
                rec = {"payer_rate": round(rate, 4), "hours": len(hrs),
                       "bar": bar, "bar_src": bar_src}
                if why:
                    rec["why"] = why
                if st in fits and fits[st] is not None:
                    rec["outcome_fit"] = {"n": fits[st]["n"],
                                          "payer_rate": round(fits[st]["payer_rate"], 4),
                                          "ev_o": round(fits[st]["ev_o"], 3),
                                          "ev_q": round(fits[st]["ev_q"], 3),
                                          "ev_ladder": bool(fits[st].get("ev_ladder"))}
                out[st] = rec
            data = {"generated_ts": (now or pd.Timestamp.utcnow()).isoformat(),
                    "hours": int(states.shape[0]),
                    "window": [str(px.index[0]), str(px.index[-1])],
                    "states": out, "err": None}
            _cache.update(data=data, ts=time.time(), err=None)
            return data
        except Exception as exc:
            # the model build itself blew up — cache the failure (ts is
            # stamped) so the next call waits out the TTL
            _cache.update(data=None, ts=time.time(), err=str(exc))
            return {"err": str(exc), "states": {}, "generated_ts": None,
                    "hours": 0, "window": None}


def _tide_class_ref():
    """The scanner's tide classifier (lazy import — this module never
    makes background_scanner import IT at module load)."""
    import background_scanner as bs
    return bs._tide_class


def warm():
    """Refresh the cache (best-effort, never raises)."""
    try:
        learn()
    except Exception:
        pass


def tide_state_floor(state: str, max_age_s: float = CACHE_TTL_S):
    """(bar, meta) for ANY tide state's gate — the state's learned
    confidence bar (outcome-fitted when its own candidate evidence is
    thick, quantile otherwise). (None, why) when the learner has no
    evidence for that state: the caller keeps its current pricing
    (fail-closed). A stale cache triggers ONE synchronous re-learn
    (~seconds, then cached for the TTL); failures are cached with a
    fresh ts too, so a broken store rate-limits to one attempt per
    TTL."""
    state = (state or "OUT").upper()
    if state not in ("OUT", "FLAT", "RISING"):
        return None, f"unknown tide state {state!r}"
    with _lock:
        data, err = _cache["data"], _cache["err"]
        age = time.time() - _cache["ts"]
    # a FRESH failure is authoritative: don't rebuild until the TTL
    # expires (the store isn't going to heal in the next gate hit)
    fresh_failure = data is None and err is not None and age <= max_age_s
    if not fresh_failure and (data is None or age > max_age_s):
        learn()   # refreshes or records the failure (ts stamped)
        with _lock:
            data, err = _cache["data"], _cache["err"]
            age = time.time() - _cache["ts"]
    if data is None:
        return None, f"no learned tide model ({err or 'unavailable'})"
    st = (data.get("states") or {}).get(state) or {}
    bar = st.get("bar")
    if bar is None:
        return None, (st.get("why") or f"the {state} evidence is too thin")
    src = st.get("bar_src") or "quantile"
    fit = st.get("outcome_fit") or {}
    if src == "outcomes":
        meta = (f"learned {state} bar {bar:.0f} (outcome-fitted on "
                f"{fit.get('n', 0)} live candidates, payers "
                f"{fit.get('payer_rate', 0) * 100:.1f}%; deep history "
                f"payers {st.get('payer_rate', 0) * 100:.1f}% over "
                f"{st.get('hours')}h)")
    else:
        meta = (f"learned {state} bar {bar:.0f} "
                f"(payers {st.get('payer_rate', 0) * 100:.1f}% over "
                f"{st.get('hours')}h of deep history)")
    return float(bar), meta


def state_bar_floor(state: str) -> float:
    """The floor matching the state's SHIPPED bar source: BAR_MIN for a
    quantile bar, OUTCOME_BAR_FLOOR for an outcome bar, and the ladder's
    OUTCOME_BAR_FLOOR_MIN when that state's own evidence admitted a
    below-80 bar (the fit's ev_ladder flag). The personalizer floors
    with this instead of BAR_MIN so a ladder-admitted fleet bar is not
    silently re-raised per coin. Fail-closed to BAR_MIN — never wider
    than the old regime on any confusion."""
    try:
        d = ((snapshot().get("data") or {}).get("states") or {}).get(state) or {}
        if d.get("bar") is None:
            return BAR_MIN
        if d.get("bar_src") == "outcomes":
            fit = d.get("outcome_fit") or {}
            return OUTCOME_BAR_FLOOR_MIN if fit.get("ev_ladder") else OUTCOME_BAR_FLOOR
        return BAR_MIN
    except Exception:
        return BAR_MIN


def tide_out_floor(max_age_s: float = CACHE_TTL_S):
    """(bar, meta) for the OUT gate — the learned confidence bar.
    (None, why) when the learner has no evidence: the caller keeps the
    blanket block (fail-closed). Thin alias over tide_state_floor."""
    return tide_state_floor("OUT", max_age_s)


def snapshot():
    """The cached model for diagnostics (never computes)."""
    with _lock:
        return {"age_s": round(time.time() - _cache["ts"], 1) if _cache["ts"] else None,
                "data": _cache["data"], "err": _cache["err"]}
