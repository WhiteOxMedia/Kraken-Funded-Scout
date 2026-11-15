"""WHAT A COIN CAN CARRY (Nov 15) — the champion question, priced.

The tournament ranks on signal confidence, which says nothing about how much
money the coin can safely take. The 3% guard already prices that per coin:
`contest_account.max_entry_usd(balance, dd)` where `dd` is the coin's profiled
window drawdown — a quiet coin supports a far bigger entry than a whippy one at
the same buffer. So "would the champion be better if we picked the coin that
could carry the most spend?" is a real, testable question.

This module answers it three ways, in the order the mission requires:

  1. DEEP DATA. `deep_scan()` gives the tournament what it was missing: as much
     of the coin's own history as one fetch returns (not the 200-candle quick
     check), the empirical worst window drawdown from it, realized volatility,
     the profiled drawdown the guard actually uses, the market read (24h volume,
     spread), and finally the dollar capacity that follows. Every field says
     where it came from; a missing source falls back and is marked, never guessed.
  2. A SHADOW VERDICT, every fire, flag or no flag: `verdict()` names the
     near-equal candidate that could carry the most spend, by how many dollars,
     and whether the bracket would have changed. `apply()` honours it only when
     `config.spend_rank` is on — default OFF, so live orders do not move.
  3. THE STUDY, on real closes: `scratch/spend_capacity_study.py`.

Nothing here touches exits, stops or the death-line guard: capacity is read from
the guard, never written to it.
"""
from __future__ import annotations

import time

# Bump when the verdict's shape or meaning changes.
SPEND_STAMP_VERSION = 1

# "As good a signal": confidence may be this many points under the champion's.
SPEND_CONF_WINDOW = 10.0

# A capacity edge worth switching champions for, in dollars of entry the guard
# would allow. Below this the two coins are the same trade at this balance.
SPEND_MIN_GAIN_USD = 25.0

# Conservative fallback: the volatility engine's own default for a coin it has
# no profile for (contest_account.DEFAULT_WINDOW_DD_PCT is 3.0).
DEFAULT_DD_PCT = 3.0

# Rolling windows the empirical drawdown is measured over, in hours. The 4h one
# is the guard's own "worst normal case" shape; the 24h one is the day's mood.
DD_WINDOWS_H = (4, 24)


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _account_ctx():
    """(balance, start) from the live contest account, or (0, None) if unreadable."""
    try:
        import contest_account as ca
        st = ca.load_state() or {}
        # the state's own key is current_balance; "balance" is only a legacy guess
        bal = _num(st.get("current_balance")) or _num(st.get("balance"))
        start = _num(st.get("start_balance"))
        return bal or 0.0, start
    except Exception:
        return 0.0, None


def profile_dd_pct(pair: str) -> tuple[float, str]:
    """The profiled window drawdown the 3% guard prices this coin with."""
    try:
        import contest_account as ca
        dd = _num(ca.window_drawdown_pct(pair))
        if dd is not None and dd > 0:
            return float(dd), "volatility_profile"
    except Exception:
        pass
    return DEFAULT_DD_PCT, "default"


def empirical_dd(bars, windows_h=DD_WINDOWS_H) -> dict:
    """Worst peak-to-trough drop over rolling windows, from real closes.

    `bars` is [[ts_ms, o, h, l, c, v], ...] ascending. Returns one entry per
    window: {"n": samples, "dd_pct": worst drop}. No lookahead — each window is
    a contiguous run, and the measure is the drop inside it, which is exactly
    what a stop would have eaten.
    """
    out = {}
    try:
        closes = [float(b[4]) for b in bars if len(b) >= 5 and float(b[4]) > 0]
    except Exception:
        return out
    if len(closes) < 30:
        return out
    for hours in windows_h:
        k = int(max(2, hours))            # 1h bars → k hours
        if len(closes) <= k:
            continue
        worst = 0.0
        for i in range(k, len(closes)):
            hi = max(closes[i - k:i + 1])
            lo = min(closes[i - k:i + 1])
            if hi > 0:
                worst = max(worst, (hi - lo) / hi * 100.0)
        out[f"dd_{hours}h_pct"] = round(worst, 3)
    if len(closes) >= 31:
        rets = [abs(closes[i] / closes[i - 1] - 1.0) * 100.0
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        if rets:
            rets.sort()
            out["abs_move_med_pct"] = round(rets[len(rets) // 2], 3)
            out["abs_move_p95_pct"] = round(rets[int(0.95 * (len(rets) - 1))], 3)
    return out


# One deep read per coin per pass is enough: the history moves by one bar an
# hour and the ticker by seconds, so a short cache keeps a 54-pair scan from
# re-fetching the same tape (measured 1.9 s per coin end to end).
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_S = 600.0


def deep_scan(kraken, pair: str, config=None, *, bars=None) -> dict:
    """THE DEEP READ for one coin: history, volatility, market, capacity.

    `bars` lets a caller pass history it already holds (the tournament has the
    evaluation window). When `kraken` is given and `deep_lookback_candles` is
    set, one deeper fetch is made — the tournament should know the coin's own
    tape, not only the 200-candle quick check. Every failure is recorded in
    `src`, and nothing raises: a scan that cannot price a coin says so.
    """
    cfg = config
    lookback = int(getattr(cfg, "deep_lookback_candles", 1000) or 0)
    _key = (str(pair), id(kraken), lookback)
    _now = time.time()
    if bars is None:
        _hit = _CACHE.get(_key)
        if _hit and _now - _hit[0] < _CACHE_TTL_S:
            return dict(_hit[1])
    out = {"pair": pair, "src": [], "bars": 0}

    deep = None
    if kraken is not None and lookback > 0:
        try:
            deep = kraken.fetch_ohlcv(pair=pair, interval_minutes=60,
                                      limit=lookback)
        except Exception:
            deep = None
    if deep and len(deep) >= 30:
        out["src"].append("deep_history")
        out["bars"] = len(deep)
        try:
            out["span_days"] = round(
                (float(deep[-1][0]) - float(deep[0][0])) / 86_400_000.0, 1)
        except Exception:
            pass
    else:
        deep = bars
        out["src"].append("eval_window" if deep else "no_history")
        out["bars"] = len(deep) if deep else 0

    if deep:
        out.update(empirical_dd(deep))

    try:
        tk = kraken.fetch_ticker(pair) if kraken is not None else {}
        hi, lo = _num(tk.get("high")), _num(tk.get("low"))
        last = _num(tk.get("last"))
        if hi and lo and last and hi > lo:
            out["day_range_pct"] = round((hi - lo) / lo * 100.0, 3)
            out["pos_in_day_pct"] = round((last - lo) / (hi - lo) * 100.0, 1)
        vol = _num(tk.get("quoteVolume")) or _num(tk.get("baseVolume"))
        if vol:
            out["volume_24h_usd"] = round(vol, 0)
        bid, ask = _num(tk.get("bid")), _num(tk.get("ask"))
        if bid and ask and bid > 0:
            out["spread_pct"] = round((ask - bid) / bid * 100.0, 4)
        if len(out) > 3:
            out["src"].append("ticker")
    except Exception:
        pass

    dd_prof, dd_src = profile_dd_pct(pair)
    out["dd_profile_pct"] = round(dd_prof, 3)
    out["dd_src"] = dd_src
    out["dd_pct"] = round(dd_prof, 3)
    # ONE CAP NUMBER, THE GUARD'S OWN. `cap_usd` is what LIVE sizing would allow
    # on this coin at this buffer — priced with the same profiled drawdown
    # `max_entry_usd` is called with everywhere else. The deep history does not
    # silently reprice it: a coin that swings wider than its profile is reported
    # beside it as `dd_empirical_pct` / `cap_deep_usd`, so the evidence is visible
    # without the tournament and the money disagreeing about the same number.
    emp = max([v for k, v in out.items()
               if k.startswith("dd_") and k.endswith("h_pct")] or [0.0])
    if emp > 0:
        out["dd_empirical_pct"] = round(emp, 3)

    bal, start = _account_ctx()
    out["balance"] = bal
    try:
        import contest_account as ca
        cap = _num(ca.max_entry_usd(bal, dd_prof, start)) if bal > 0 else 0.0
        if bal > 0 and emp > dd_prof:
            out["cap_deep_usd"] = round(
                _num(ca.max_entry_usd(bal, emp, start)) or 0.0, 2)
    except Exception:
        cap = 0.0
    out["cap_usd"] = round(cap or 0.0, 2)
    if bal > 0 and cap:
        out["cap_frac_pct"] = round(cap / bal * 100.0, 1)
    if "cap_usd" not in out or not out["cap_usd"]:
        out["src"].append("no_capacity")
    if bars is None:
        _CACHE[_key] = (_now, dict(out))
    return out


def verdict(champion, results, flag_on: bool, *,
            window: float = SPEND_CONF_WINDOW,
            min_gain: float = SPEND_MIN_GAIN_USD) -> dict:
    """Would the coin that carries the most spend make a better champion?

    Pure. `results` is the bracket's evaluated ScanResults, each carrying a
    `spend` dict from deep_scan(). One record per fire, whether or not the flag
    is on — the decision is graded later against the close it did or did not take.
    """
    out = {"v": SPEND_STAMP_VERSION, "flag": "spend_rank", "on": bool(flag_on),
           "would": "insufficient", "applied": False, "pool": 0,
           "cap_usd": None, "gain_usd": None}
    try:
        pool = []
        items = results.values() if hasattr(results, "values") else results
        for r in items:
            sig = getattr(r, "signal", None)
            spend = getattr(r, "spend", None)
            if sig is None or getattr(sig, "action", "") != "buy":
                continue
            if not isinstance(spend, dict):
                continue
            cap = _num(spend.get("cap_usd"))
            if cap is None or cap <= 0:
                continue
            pool.append((r, cap))
        out["pool"] = len(pool)

        ch_spend = getattr(champion, "spend", None)
        cap_ch = _num(ch_spend.get("cap_usd")) if isinstance(ch_spend, dict) else None
        if cap_ch is not None and cap_ch > 0:
            out["cap_usd"] = round(cap_ch, 2)
        if not pool:
            out["would"] = "no_spend" if cap_ch is None else "insufficient"
            return out
        if cap_ch is None or cap_ch <= 0:
            out["would"] = "insufficient"
            return out

        conf = _num(getattr(champion.signal, "confidence", None)) or 0.0
        cands = [(r, cap) for (r, cap) in pool
                 if (_num(getattr(r.signal, "confidence", None)) or 0.0)
                 >= conf - float(window)]
        if not cands:
            cands = [(champion, cap_ch)]
        best_r, best_cap = max(
            cands, key=lambda t: (round(t[1], 2),
                                  _num(getattr(t[0].signal, "confidence", None)) or 0.0))
        if best_r is champion or round(best_cap - cap_ch, 2) < float(min_gain):
            out["would"] = "hold"
            return out
        out.update({"would": "prefer",
                    "from": getattr(champion, "pair", ""),
                    "from_cap_usd": round(cap_ch, 2),
                    "to": getattr(best_r, "pair", ""),
                    "to_cap_usd": round(best_cap, 2),
                    "gain_usd": round(best_cap - cap_ch, 2),
                    "applied": bool(flag_on)})
        return out
    except Exception:
        out["would"] = "insufficient"
        return out


def apply(champion, results, config):
    """Run the verdict and honour it only when `spend_rank` is on."""
    flag_on = bool(getattr(config, "spend_rank", False))
    window = _num(getattr(config, "spend_rank_window", None))
    gain = _num(getattr(config, "spend_rank_min_usd", None))
    rec = verdict(champion, results, flag_on,
                  window=(SPEND_CONF_WINDOW if window is None else window),
                  min_gain=(SPEND_MIN_GAIN_USD if gain is None else gain))
    if rec.get("applied") and rec.get("to"):
        try:
            chosen = results[rec["to"]]
        except Exception:
            rec["applied"] = False
            return champion, rec
        rec["cap_usd"], rec["cap_before_usd"] = rec.get("to_cap_usd"), rec.get("cap_usd")
        return chosen, rec
    return champion, rec
