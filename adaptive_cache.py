# adaptive_cache.py -- THE ADAPTIVE TTL SYSTEM (user spec, Sep 5:
# "Adaptive TTL System (Full Specification)").
#
# Every console fetcher gets: a BASE TTL (the old static value), a MIN
# (tightest allowed when markets move), a MAX (loosest when calm), an
# ADAPTIVE multiplier from the volatility regime, EVENT-driven
# invalidation (user actions), and CROSS-CACHE invalidation (one cache's
# fresh data forcing another's refresh).
#
# THE VOLATILITY MODEL: rolling abs pct change of ticker prices over the
# last 60s (every real ticker fetch samples; served cache hits sample
# nothing — only new information moves the regime):
#     calm     < 0.3%  -> x1.5  (loosen: stability when nothing moves)
#     normal   0.3-1.0 -> x1.0  (base)
#     volatile 1.0-3.0 -> x0.5  (tighten: freshness when markets move)
#     extreme  > 3.0%  -> x0.25 (very tight)
# Archival fetchers (payouts, past winners) ignore the multiplier —
# history is history at any volatility.
#
# CROSS-CACHE RULES (run at fetch time inside the engine):
#     ticker_px     -> bot_positions:  price moved >0.8% since last fetch
#     qc_liquidity  -> scanner_status: book depth changed >10%
#     bot_positions -> qc_liquidity:   the position set changed (enter/exit)
#     fetch_df      -> chart overlays: overlays render FROM the df every
#                                      rerun (no separate cache) — inherent.
# A REGIME CHANGE forces one refresh of ticker_px + fetch_df (the console
# snaps to the new cadence immediately).
#
# The store is MODULE-level (one process = one console): market
# volatility and freshness are global truths, shared across browser
# sessions. ac_cached never raises on a fetch failure — it re-raises and
# keeps the previous value, so the next read retries fresh.

import time

# ── THE SPEC TABLE: (base_s, min_s, max_s, adaptive) ──────────────────
SPEC = {
    "ticker_px":     (30,  8,   45,  True),
    "fetch_df":      (45,  20,  90,  True),
    "qc_liquidity":  (60,  20,  120, True),
    "past_winners":  (60,  60,  300, False),
    "scanner_status": (60, 20,  120, True),
    "fetch_payouts": (180, 180, 600, False),
    "bot_positions": (30,  10,  45,  True),
}

# (vol_pct_above, multiplier, name) — first match wins; None = no ceiling
REGIMES = [(0.3, 1.5, "calm"), (1.0, 1.0, "normal"),
           (3.0, 0.5, "volatile"), (None, 0.25, "extreme")]
VOL_WINDOW_S = 60.0
PRICE_MOVE_INVALIDATES_POSITIONS = 0.008   # 0.8%
DEPTH_CHANGE_INVALIDATES_SCANNER = 0.10    # 10%

_STORE = {}        # (name, key) -> {"ts": float, "val": any, "meta": any}
_INVALID = set()   # (name, key) prefixes/keys forced stale by events
_VOL_SAMPLES = []  # (ts, pair, px) — real fetches only
_REGIME = {"name": "normal", "mult": 1.0, "since": 0.0}


# ── the volatility model ──────────────────────────────────────────────
def sample_price(pair: str, px: float):
    """Record one real ticker read (never a cache hit, never a 0.0
    failure) and recompute the regime. Cheap: list ops on a 60s window."""
    try:
        px = float(px)
        if px <= 0 or not pair:
            return
        now = time.time()
        _VOL_SAMPLES.append((now, str(pair), px))
        _trim_vol_window(now)
        _recompute_regime(now)
    except Exception:
        pass


def _trim_vol_window(now):
    cutoff = now - VOL_WINDOW_S
    global _VOL_SAMPLES
    if _VOL_SAMPLES and _VOL_SAMPLES[0][0] < cutoff:
        _VOL_SAMPLES = [s for s in _VOL_SAMPLES if s[0] >= cutoff]


def vol_pct() -> float:
    """Mean abs pct move per pair across the 60s sample window (a
    fleet-wide read: the market moves together). 0.0 with thin samples."""
    now = time.time()
    _trim_vol_window(now)
    if len(_VOL_SAMPLES) < 2:
        return 0.0
    by_pair = {}
    for _ts, pair, px in _VOL_SAMPLES:
        by_pair.setdefault(pair, []).append(px)
    moves = []
    for pair, pxs in by_pair.items():
        if len(pxs) >= 2 and pxs[0] > 0:
            moves.append(abs(pxs[-1] / pxs[0] - 1.0) * 100.0)
    if not moves:
        return 0.0
    return sum(moves) / len(moves)


def regime() -> dict:
    """{"name", "mult", "vol_pct"} — the current market regime."""
    return {"name": _REGIME["name"], "mult": _REGIME["mult"],
            "vol_pct": round(vol_pct(), 3)}


def _recompute_regime(now):
    v = vol_pct()
    # thin data holds the current regime — one sample at boot must not
    # flip normal->calm (which would fire the change-invalidation on the
    # very first fetch); the regime only moves on real evidence
    if len(_VOL_SAMPLES) < 3:
        return
    for above, mult, name in REGIMES:
        if above is None or v < above:
            if _REGIME["name"] != name:
                # REGIME CHANGE: the cadence snaps — one forced refresh of
                # the price + candle caches so the console re-reads the
                # market at the new tightness immediately
                _REGIME.update({"name": name, "mult": mult, "since": now})
                ac_invalidate("ticker_px", "fetch_df")
            else:
                _REGIME["mult"] = mult
            return


# ── the adaptive TTL ──────────────────────────────────────────────────
def ttl_for(name: str) -> int:
    """clamp(base x regime multiplier, min, max); archival ignores the
    multiplier (base always)."""
    base, mn, mx, adaptive = SPEC[name]
    if not adaptive:
        return int(base)
    ttl = base * _REGIME["mult"]
    return int(max(mn, min(mx, ttl)))


# ── the cache itself ──────────────────────────────────────────────────
def ac_cached(name: str, key, fetch, copy_result=False):
    """Serve from the adaptive store while fresh; fetch through on stale
    (event-flagged, TTL-expired, or unknown key). copy_result=True serves
    every caller its OWN copy (the candle frames get live-price seeded by
    the exit paths — one shared frame would poison every reader)."""
    now = time.time()
    k = (name, str(key))
    ent = _STORE.get(k)
    ttl = ttl_for(name)
    forced = k in _INVALID or (name,) in _INVALID
    if ent is not None and not forced and (now - ent["ts"]) < ttl:
        val = ent["val"]
        return val.copy() if copy_result and hasattr(val, "copy") else val
    # stale/forced/unknown -> a real fetch
    val = fetch()
    meta = _meta_for(name, val)
    old_meta = ent.get("meta") if ent else None
    _INVALID.discard(k)
    _INVALID.discard((name,))
    _STORE[k] = {"ts": now, "val": val, "meta": meta}
    if name == "ticker_px":
        try:
            sample_price(key, float(val))
        except Exception:
            pass
    _cross_invalidate(name, old_meta, meta)
    # copy_result copies on EVERY serve — fetch path included: the first
    # caller mutating its frame must not poison the stored one either
    return val.copy() if copy_result and hasattr(val, "copy") else val


def _meta_for(name, val):
    """The fingerprint each cross-cache rule compares."""
    try:
        if name == "ticker_px":
            return float(val)
        if name == "qc_liquidity":
            for k in ("ask_tob", "carry_usd", "depth", "bid_tob", "notional"):
                v = (val or {}).get(k)
                if isinstance(v, (int, float)) and v > 0:
                    return float(v)
            return None
        if name == "bot_positions":
            return tuple(sorted((p.get("id"), p.get("pair"))
                                for p in (val or [])))
    except Exception:
        return None
    return None


def _cross_invalidate(name, old_meta, new_meta):
    """One cache's fresh data forces another's refresh (the spec's rules)."""
    try:
        if name == "ticker_px" and old_meta and new_meta \
                and old_meta > 0 and abs(new_meta / old_meta - 1.0) > PRICE_MOVE_INVALIDATES_POSITIONS:
            ac_invalidate("bot_positions")
        if name == "qc_liquidity" and old_meta and new_meta \
                and abs(new_meta / old_meta - 1.0) > DEPTH_CHANGE_INVALIDATES_SCANNER:
            ac_invalidate("scanner_status")
        if name == "bot_positions" and old_meta != new_meta and \
                (old_meta is not None or new_meta is not None):
            ac_invalidate("qc_liquidity")
    except Exception:
        pass


# ── event-driven invalidation (user + system actions) ─────────────────
def ac_invalidate(*names):
    """Force these fetchers stale on the next read — user actions (scan,
    trade, pair switch) and system events call this. A bare name
    invalidates every key under it."""
    for n in names:
        if n in SPEC:
            _INVALID.add((n,))


def ac_reset():
    """Test hook: wipe the store, samples, invalidations, regime."""
    _STORE.clear()
    _INVALID.clear()
    _VOL_SAMPLES.clear()
    _REGIME.update({"name": "normal", "mult": 1.0, "since": 0.0})


# ── staleness-aware UI ────────────────────────────────────────────────
FRESH_LABELS = {"ticker_px": "PX", "fetch_df": "CHART", "qc_liquidity": "BOOK",
                "past_winners": "PICKS", "scanner_status": "SCAN",
                "fetch_payouts": "PAYOUTS", "bot_positions": "BOT"}


def freshness() -> list[dict]:
    """Per-fetcher staleness for the UI plate:
    fresh <10s green / mid 10-30s yellow / stale >ttl orange / forced red."""
    out = []
    now = time.time()
    for name in SPEC:
        entries = [e for (n, _k), e in _STORE.items() if n == name]
        if not entries:
            out.append({"name": name, "label": FRESH_LABELS.get(name, name),
                        "age": None, "ttl": ttl_for(name), "color": "off"})
            continue
        e = max(entries, key=lambda x: x["ts"])
        age = now - e["ts"]
        forced = (name,) in _INVALID
        if forced:
            color = "red"
        elif age < 10:
            color = "green"
        elif age < 30:
            color = "yellow"
        elif age > ttl_for(name):
            color = "orange"
        else:
            color = "yellow"
        out.append({"name": name, "label": FRESH_LABELS.get(name, name),
                    "age": age, "ttl": ttl_for(name), "color": color})
    return out


def freshness_html() -> str:
    """The sidebar freshness plate: one colored row + tooltips
    ('Last updated 17s ago — next refresh in 13s')."""
    r = regime()
    bits = [f"<span style='opacity:.75'>⚡ {r['name'].upper()} ×{r['mult']:.2f}"
            f" (vol {r['vol_pct']:.2f}%)</span>"]
    cols = {"green": "#4be38a", "yellow": "#ffd75e", "orange": "#ff9d4d",
            "red": "#ff6b5e", "off": "#888"}
    for f in freshness():
        c = cols.get(f["color"], "#888")
        if f["age"] is None:
            txt = f"{f['label']}: never"
            tip = f"{f['name']}: not fetched yet"
        else:
            nxt = max(0, int(f["ttl"] - f["age"]))
            txt = f"{f['label']}: {int(f['age'])}s"
            tip = (f"Last updated {int(f['age'])}s ago — "
                   f"next refresh in {nxt}s (ttl {f['ttl']}s)")
        bits.append(f"<span title='{tip}' style='color:{c}'>{txt}</span>")
    return " · ".join(bits)
