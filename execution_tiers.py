"""
Execution Tiers — sliding-scale market impact & liquidity system.

Shared library used by BOTH sides:
  - kraken_xrp_trading_bot (execution: real gates + tiered order placement)
  - kraken_scout console/scanner (advisory readouts only — never writes the bridge)

Tiers (order notional vs top-of-book depth, top 10 levels):
  T1 Micro 0-5%        → market order
  T2 Small 5-20%       → market order
  T3 Medium 20-50%     → limit ladder (3 slices, timeout, market-sweep remainder)
  T4 Large 50-100%     → TWAP (4 market slices, 30s apart, abort checks per slice)
  T5 Very Large 100-300% → cap to 50% of book → ladder, remainder queued for scale-in
  T6 Institutional 300%+  → same as T5

Buy gate pipeline (order matters):
  1. 24h blacklist (thin book or 2 slippage-audit strikes)
  2. Liquidity health score >= 60 (spread / depth bands / engine σ / maker presence)
  3. Hybrid sizing: account-% sized, hard-capped at Tier 2 (20% of book)
  4. Slippage forecast (VWAP walk) <= 0.3%
  5. Depth stability: two samples 10s apart — best-level ±20%, spread may not
     blow out, no sudden liquidity disappearance
  6. Tiered execution with per-slice aborts (spread 2x, depth -50%, price ±0.5%,
     slippage 2x forecast) — buys never chase, sells always complete
  7. Post-trade slippage audit: actual > 2x forecast → strike (size x0.5 1h,
     2nd strike in 24h → 24h blacklist)
  8. Scale-in queue: capped remainders drained <=20% of book per tick,
     all gates re-checked, expires after 2h / 4 slices

All gates FAIL OPEN when the public book endpoint errors — a flaky endpoint
must not halt trading (behaviour degrades to today's plain market orders).

State lives in execution_state.json next to this file (bot is the only
writer; scanner/console read).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("execution_tiers")

MST = ZoneInfo("America/Denver")  # auto DST — never hardcode an offset
BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / "execution_state.json"

# ── Tier table ────────────────────────────────────────────────────────────
DEPTH_LEVELS = 10          # "top of book" = cumulative depth of top N levels
TIER_BOUNDS = [
    (5.0, "T1 Micro"),
    (20.0, "T2 Small"),
    (50.0, "T3 Medium"),
    (100.0, "T4 Large"),
    (300.0, "T5 Very Large"),
    (math.inf, "T6 Institutional"),
]

# ── Sizing ────────────────────────────────────────────────────────────────
SIZE_CAP_PCT = 20.0        # hybrid sizing cap: never above Tier 2
T5_CAP_PCT = 50.0          # T5/6 execution cap: Tier 3 boundary
THIN_BOOK_USD = 500.0      # below this → 24h blacklist
MIN_TRADE_USD = 100.0      # mirror of the bot's minimum trade

# ── Health score (pass >= HEALTH_MIN) ────────────────────────────────────
HEALTH_MIN = 60
SPREAD_FULL_BPS = 10.0     # spread at/below → full 25 pts
SPREAD_ZERO_BPS = 100.0    # spread at/above → 0 pts
BAND_POINTS = {1: 15, 2: 10, 5: 10}          # USD depth within ±N% of mid
BAND_FULL_USD = {1: 10_000.0, 2: 20_000.0, 5: 40_000.0}
VOL_FULL_PCT = 1.0         # session σ at/below → full 20 pts
VOL_ZERO_PCT = 5.0         # session σ at/above → 0 pts
VOL_MISSING_PTS = 10.0     # neutral when the engine has no cell
MAKER_MISSING_PTS = 10.0   # neutral when no stability data yet

# ── Slippage forecast ─────────────────────────────────────────────────────
SLIPPAGE_MAX_PCT = 0.3     # skip buys whose VWAP-walk forecast exceeds this

# ── Depth stability (two samples) ────────────────────────────────────────
STABILITY_WINDOW_S = 10
STABILITY_DEPTH_PCT = 20.0      # best-level depth may not DRY more than this (dries only —
                                # a book getting DEEPER is good news, never "unstable")
STABILITY_SPREAD_PCT = 20.0     # spread may widen at most this (relative)…
STABILITY_SPREAD_ABS_BPS = 10.0  # …and at most this (absolute bps)
STABILITY_TOB_DROP_PCT = 50.0   # top-10 depth may not drop more than this
STABILITY_LEVEL_MIN_USD = 500.0 # front rows smaller than this are order-slot noise,
                                # not stability signals (CRV's "$49 book" was one tiny order)

# ── Execution ─────────────────────────────────────────────────────────────
LADDER_SLICES = 3
LADDER_TIMEOUT_S = 45
LADDER_POLL_S = 5
TWAP_SLICES = 4
TWAP_INTERVAL_S = 30

# Abort conditions, checked between slices (buys stop, sells finish fast)
ABORT_SPREAD_MULT = 2.0
ABORT_DEPTH_DROP_PCT = 50.0
ABORT_PRICE_JUMP_PCT = 0.5
ABORT_SLIP_MULT = 2.0

# ── Slippage audit (self-correcting) ─────────────────────────────────────
AUDIT_STRIKE_RATIO = 2.0        # actual > 2x forecast → strike
AUDIT_STRIKE_MULT = 0.5         # strike 1: size x0.5…
AUDIT_STRIKE_COOLDOWN_MIN = 60  # …for 60 minutes
AUDIT_BLACKLIST_H = 24          # strike 2 within 24h → blacklist
AUDIT_STRIKE_WINDOW_H = 24

# ── Scale-in queue ────────────────────────────────────────────────────────
SCALE_MAX_SLICES = 4
SCALE_EXPIRE_H = 2
SCALE_REASON_MARK = "liquidity scale-in slice"  # suppresses re-scheduling


def _now() -> datetime:
    return datetime.now(MST)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ── Shared state (blacklist / strikes / pending scales) ──────────────────

def _load_state() -> dict:
    try:
        with open(STATE_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict):
            doc.setdefault("blacklist", {})
            doc.setdefault("strikes", {})
            doc.setdefault("pending_scales", {})
            return doc
    except Exception:
        pass
    return {"blacklist": {}, "strikes": {}, "pending_scales": {}}


def _save_state(state: dict) -> None:
    """Atomic write (tmp + replace). Bot is the only writer; races benign."""
    try:
        tmp = str(STATE_JSON) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, STATE_JSON)
    except Exception as exc:
        logger.warning("Could not save execution state: %s", exc)


def blacklist_pair(pair: str, hours: float, reason: str) -> None:
    fresh = _load_state()
    fresh.setdefault("blacklist", {})[pair] = {
        "until": (_now() + timedelta(hours=hours)).isoformat(),
        "reason": reason,
    }
    _save_state(fresh)
    logger.warning("BLACKLIST %s for %.0fh: %s", pair, hours, reason)


def is_blacklisted(pair: str) -> tuple[bool, str]:
    try:
        entry = _load_state().get("blacklist", {}).get(pair)
        if not entry:
            return False, ""
        until = datetime.fromisoformat(entry["until"])
        if _now() >= until:
            fresh = _load_state()
            fresh.get("blacklist", {}).pop(pair, None)
            _save_state(fresh)  # lazy prune
            return False, ""
        return True, entry.get("reason", "blacklisted")
    except Exception:
        return False, ""


def get_size_multiplier(pair: str) -> float:
    """Strike-1 pairs trade at half size until the cooldown lapses."""
    try:
        st = _load_state().get("strikes", {}).get(pair)
        if not st:
            return 1.0
        until = st.get("mult_until")
        if until and _now() < datetime.fromisoformat(until) and st.get("count", 0) >= 1:
            return float(st.get("size_mult", 1.0))
    except Exception:
        pass
    return 1.0


def audit_slippage(pair: str, forecast_pct, actual_pct) -> str:
    """Compare forecast vs actual slippage; strikes on 2x blowouts."""
    if forecast_pct is None or actual_pct is None:
        return ""
    f = max(float(forecast_pct), 0.02)  # floor: avoid silly ratios on ~0 forecasts
    a = float(actual_pct)
    if a <= f * AUDIT_STRIKE_RATIO:
        return ""  # fill came in at or under forecast — nothing to do
    now = _now()
    fresh = _load_state()
    strikes = fresh.setdefault("strikes", {})
    st = strikes.get(pair) or {"count": 0}
    first = st.get("first")
    try:
        first_dt = datetime.fromisoformat(first) if first else now
    except Exception:
        first_dt = now
    if now - first_dt > timedelta(hours=AUDIT_STRIKE_WINDOW_H):
        st = {"count": 0}  # window rolled over
    st["count"] = int(st.get("count", 0)) + 1
    st["first"] = (first_dt if st["count"] > 1 else now).isoformat()
    st["last"] = now.isoformat()
    if st["count"] >= 2:
        fresh.setdefault("blacklist", {})[pair] = {
            "until": (now + timedelta(hours=AUDIT_BLACKLIST_H)).isoformat(),
            "reason": f"slippage audit: {st['count']} strikes (last actual {a:.2f}% vs forecast {f:.2f}%)",
        }
        strikes.pop(pair, None)
        _save_state(fresh)
        msg = (f"SLIPPAGE AUDIT: {pair} blacklisted {AUDIT_BLACKLIST_H}h "
               f"(actual {a:.2f}% vs forecast {f:.2f}%, {st['count']} strikes)")
        logger.warning(msg)
        return msg
    st["size_mult"] = AUDIT_STRIKE_MULT
    st["mult_until"] = (now + timedelta(minutes=AUDIT_STRIKE_COOLDOWN_MIN)).isoformat()
    strikes[pair] = st
    _save_state(fresh)
    msg = (f"SLIPPAGE AUDIT: {pair} actual {a:.2f}% vs forecast {f:.2f}% — "
           f"size x{AUDIT_STRIKE_MULT} for {AUDIT_STRIKE_COOLDOWN_MIN}min "
           f"(strike {st['count']}/{2})")
    logger.warning(msg)
    return msg


# ── Book measurement ──────────────────────────────────────────────────────

def _fetch_ob(exchange, pair: str, limit: int = 25) -> dict:
    """fetch_order_book across the three wrapper shapes we have:
    raw ccxt instance, KrakenAPI wrapper, console _Bare wrapper (both expose .exchange)."""
    fn = getattr(exchange, "fetch_order_book", None)
    if callable(fn):
        return fn(pair, limit=limit)
    inner = getattr(exchange, "exchange", None)
    if inner is not None and hasattr(inner, "fetch_order_book"):
        return inner.fetch_order_book(pair, limit=limit)
    raise RuntimeError("no fetch_order_book available on exchange object")


def measure_book(exchange, pair: str) -> dict | None:
    """One public order-book snapshot with both sides summarised."""
    ob = _fetch_ob(exchange, pair, limit=25)
    # Kraken levels can carry a 3rd element (timestamp) — star-unpack handles both
    bids = [[float(p), float(s)] for p, s, *_ in (ob.get("bids") or [])]
    asks = [[float(p), float(s)] for p, s, *_ in (ob.get("asks") or [])]
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0 if mid > 0 else 0.0
    return {
        "pair": pair,
        "ts": time.time(),
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "bid_tob": sum(p * s for p, s in bids[:DEPTH_LEVELS]),
        "ask_tob": sum(p * s for p, s in asks[:DEPTH_LEVELS]),
    }


def classify_tier(notional_usd: float, tob_usd: float) -> tuple[int, str, float]:
    if tob_usd <= 0:
        return 6, "T6 Institutional", 999.0
    cons = notional_usd / tob_usd * 100.0
    for i, (hi, name) in enumerate(TIER_BOUNDS, start=1):
        if cons < hi:
            return i, name, cons
    return 6, "T6 Institutional", cons


def forecast_slippage(levels, notional_usd: float, mid: float, side: str = "buy"):
    """VWAP-walk the visible book with the order → (slippage_pct, vwap).
    Subsumes the (size/TOB)^2 x spread heuristic — it walks actual depth.
    Beyond visible levels the remainder is extrapolated at last price +1%."""
    if not levels or notional_usd <= 0 or not mid:
        return None, None
    remaining = float(notional_usd)
    cost = 0.0
    qty = 0.0
    last_px = float(levels[-1][0])
    for p, s in levels:
        lvl_usd = p * s
        take = min(remaining, lvl_usd)
        q = take / p
        cost += take
        qty += q
        remaining -= take
        if remaining <= 1e-9:
            break
    if qty <= 0:
        return None, None
    if remaining > 0:
        px_pen = last_px * 1.01
        vwap = (cost + remaining) / (qty + remaining / px_pen)
    else:
        vwap = cost / qty
    slip = (vwap - mid) / mid * 100.0 if side == "buy" else (mid - vwap) / mid * 100.0
    return slip, vwap


def _current_vol_std(pair: str):
    """Session σ for this asset right now, from the Volatility Engine JSON."""
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        if not doc:
            return None
        n = _now()
        rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
        if rot is None:
            return None
        sched = "weekend" if n.weekday() >= 5 else "weekday"
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot)
                or doc.get("median", {}).get(sched, {}).get(rot))
        if cell and cell.get("std") is not None:
            return float(cell["std"])
    except Exception:
        pass
    return None


def liquidity_health_score(book: dict, pair: str, tob_delta_pct=None) -> tuple[int, dict]:
    """0-100 composite. Components: spread 25, depth bands 35, volatility 20,
    maker presence 20. Volatility reuses the Volatility Engine (no new pipeline)."""
    breakdown = {}
    sp = book["spread_bps"]
    breakdown["spread"] = round(25.0 * _clamp((SPREAD_ZERO_BPS - sp) / (SPREAD_ZERO_BPS - SPREAD_FULL_BPS), 0, 1), 1)
    mid = book["mid"]
    for band, max_pts in BAND_POINTS.items():
        usd = 0.0
        for side_levels in (book["asks"], book["bids"]):
            for p, s in side_levels:
                if abs(p - mid) / mid * 100.0 <= band:
                    usd += p * s
        breakdown[f"depth_{band}%"] = round(max_pts * min(1.0, usd / BAND_FULL_USD[band]), 1)
    std = _current_vol_std(pair)
    if std is None:
        breakdown["volatility"] = VOL_MISSING_PTS
    else:
        breakdown["volatility"] = round(20.0 * _clamp((VOL_ZERO_PCT - std) / (VOL_ZERO_PCT - VOL_FULL_PCT), 0, 1), 1)
    if tob_delta_pct is None:
        breakdown["maker"] = MAKER_MISSING_PTS
    else:
        d = abs(float(tob_delta_pct))
        breakdown["maker"] = 20.0 if d <= 10 else (12.0 if d <= 20 else (6.0 if d <= 35 else 0.0))
    score = int(round(sum(breakdown.values())))
    return score, breakdown


def check_depth_stability(exchange, pair: str, side: str = "asks") -> dict:
    """Two samples STABILITY_WINDOW_S apart. Best-level depth ±20%, spread may
    not blow out, no sudden liquidity disappearance. FAILS OPEN on API errors."""
    try:
        b1 = measure_book(exchange, pair)
        time.sleep(STABILITY_WINDOW_S)
        b2 = measure_book(exchange, pair)
        if not b1 or not b2:
            return {"stable": True, "reason": "book unavailable (fail-open)", "tob_delta_pct": None}
        lv1 = b1["asks"] if side == "asks" else b1["bids"]
        lv2 = b2["asks"] if side == "asks" else b2["bids"]
        d1 = lv1[0][0] * lv1[0][1] if lv1 else 0.0
        d2 = lv2[0][0] * lv2[0][1] if lv2 else 0.0
        tob1 = b1["ask_tob"] if side == "asks" else b1["bid_tob"]
        tob2 = b2["ask_tob"] if side == "asks" else b2["bid_tob"]
        reasons = []
        # LEVEL-1 DRY CHECK (fixed Aug 31 — the gate that ate a 92% pick):
        # the old abs() form flagged books getting DEEPER as "unstable"
        # (HYPE $536→$12,865, a 24× improvement) and judged sub-$100 front
        # rows (CRV $49→$32 — one tiny order slot moving). Now: only a
        # DRY of a MATERIAL row (≥ STABILITY_LEVEL_MIN_USD) counts. The
        # cumulative book has its own directional tob-drop check below —
        # that one is the real measure of the book evaporating.
        if (d1 >= STABILITY_LEVEL_MIN_USD
                and d2 < d1 * (1.0 - STABILITY_DEPTH_PCT / 100.0)):
            reasons.append(f"best-level depth ${d1:.0f}→${d2:.0f} (dried)")
        sp1, sp2 = b1["spread_bps"], b2["spread_bps"]
        if (sp2 - sp1) > max(STABILITY_SPREAD_PCT / 100.0 * sp1, STABILITY_SPREAD_ABS_BPS):
            reasons.append(f"spread {sp1:.1f}→{sp2:.1f}bps")
        if tob1 > 0 and (tob1 - tob2) / tob1 * 100.0 > STABILITY_TOB_DROP_PCT:
            reasons.append(f"book depth -{(tob1 - tob2) / tob1 * 100.0:.0f}%")
        delta = (tob2 - tob1) / tob1 * 100.0 if tob1 else None
        if reasons:
            return {"stable": False, "reason": "; ".join(reasons), "tob_delta_pct": delta}
        return {"stable": True, "reason": "", "tob_delta_pct": delta}
    except Exception as exc:
        return {"stable": True, "reason": f"check failed (fail-open): {exc}", "tob_delta_pct": None}


# ── Buy gate pipeline ─────────────────────────────────────────────────────

def gate_buy(exchange, pair: str, desired_usd: float, min_cost_usd: float = 0.0,
             with_stability: bool = True) -> dict:
    """Instant gates (blacklist → health → cap → slippage) + optional 10s
    stability check. Returns {'ok': bool, 'reason', 'spend_usd', ...}.
    Measurement failures FAIL OPEN (degraded=True, full size)."""
    bl, bl_reason = is_blacklisted(pair)
    if bl:
        return {"ok": False, "reason": f"blacklisted ({bl_reason})", "blacklisted": True}

    try:
        book = measure_book(exchange, pair)
    except Exception as exc:
        logger.warning("Book measurement failed for %s — fail-open: %s", pair, exc)
        book = None
    if not book or not book.get("mid") or book["ask_tob"] <= 0:
        return {"ok": True, "degraded": True, "spend_usd": desired_usd,
                "score": None, "tier": (1, "T1 (degraded)", 0.0),
                "slippage_pct": None, "reason": "book unavailable"}

    # Thin-book BLACKLIST removed (user: "no more thin books") — the bot's
    # ~$172 positions are small relative to most books, and the real gates
    # (health ≥60, T2 cap, slippage ≤0.3%, stability) protect the order.
    # Only the slippage-audit blacklist (actual bad fills, 2 strikes) remains.

    score, breakdown = liquidity_health_score(book, pair)
    if score < HEALTH_MIN:
        return {"ok": False, "reason": f"liquidity health {score} < {HEALTH_MIN}",
                "score": score, "breakdown": breakdown, "book": book}

    mult = get_size_multiplier(pair)
    spend = desired_usd * mult
    capped = min(spend, SIZE_CAP_PCT / 100.0 * book["ask_tob"])
    if min_cost_usd > 0 and capped < min_cost_usd:
        return {"ok": False,
                "reason": f"book too thin after {SIZE_CAP_PCT:.0f}% cap (${capped:.0f} < ${min_cost_usd:.0f} min order)",
                "score": score, "breakdown": breakdown, "book": book}

    slip, _vwap = forecast_slippage(book["asks"], capped, book["mid"], "buy")
    if slip is not None and slip > SLIPPAGE_MAX_PCT:
        return {"ok": False, "reason": f"forecast slippage {slip:.2f}% > {SLIPPAGE_MAX_PCT}%",
                "score": score, "breakdown": breakdown, "book": book, "slippage_pct": slip}

    stability = None
    if with_stability:
        stability = check_depth_stability(exchange, pair, "asks")
        if not stability.get("stable"):
            return {"ok": False, "reason": f"depth unstable: {stability.get('reason', '?')}",
                    "score": score, "breakdown": breakdown, "book": book, "stability": stability}

    tier = classify_tier(capped, book["ask_tob"])
    logger.info("LIQUIDITY GATE %s: health %d · %s %.1f%% of $%.0f book · slip %.3f%% · spend $%.0f%s",
                pair, score, tier[1], tier[2], book["ask_tob"], slip or 0.0, capped,
                " · DEGRADED" if mult < 1.0 else "")
    return {"ok": True, "spend_usd": capped, "score": score, "breakdown": breakdown,
            "tier": tier, "slippage_pct": slip, "size_mult": mult, "book": book,
            "stability": stability, "blacklisted": False}


# ── Tiered execution ──────────────────────────────────────────────────────

def _wrap_single(order: dict, volume: float) -> dict:
    filled = float(order.get("filled") or 0)
    avg = float(order.get("average") or order.get("price") or 0)
    cost = float(order.get("cost") or 0)
    fee = float(order.get("fee", {}).get("cost", 0)) if isinstance(order.get("fee"), dict) else 0.0
    return {"id": str(order.get("id") or ""), "status": order.get("status") or "filled",
            "average": avg, "filled": filled, "cost": cost, "fee": fee,
            "unfilled_volume": max(0.0, volume - filled), "aborted": False}


def _finalize_single(kraken, pair: str, order: dict, volume: float, validate: bool) -> dict:
    """Resolve sparse market-order responses with one fetch_order."""
    res = _wrap_single(order, volume)
    if res["filled"] == 0 and res["id"] and not validate:
        try:
            time.sleep(1)
            final = kraken.fetch_order(res["id"], pair)
            res["filled"] = float(final.get("filled") or 0)
            res["average"] = float(final.get("average") or final.get("price") or res["average"])
            res["cost"] = float(final.get("cost") or 0)
            fi = final.get("fee") or {}
            if isinstance(fi, dict):
                res["fee"] = float(fi.get("cost") or 0)
            res["status"] = final.get("status") or res["status"]
        except Exception as exc:
            logger.debug("fetch_order after market order failed: %s", exc)
    if res["filled"] == 0:
        res["filled"] = volume
        res["cost"] = res["cost"] or (res["average"] * volume if res["average"] else 0.0)
    return res


def _hostile_reasons(b_start: dict, b_now: dict, side: str = "buy",
                     slip_so_far=None, forecast=None) -> list[str]:
    """Breakout detection between slices: spread 2x, depth -50%, price ±0.5%,
    realized slippage 2x forecast."""
    reasons = []
    sp1, sp2 = b_start["spread_bps"], b_now["spread_bps"]
    if sp2 > ABORT_SPREAD_MULT * max(sp1, 1.0):
        reasons.append(f"spread {sp1:.1f}→{sp2:.1f}bps")
    tob1 = b_start["ask_tob"] if side == "buy" else b_start["bid_tob"]
    tob2 = b_now["ask_tob"] if side == "buy" else b_now["bid_tob"]
    if tob1 > 0 and tob2 < tob1 * (1 - ABORT_DEPTH_DROP_PCT / 100.0):
        reasons.append(f"depth -{(1 - tob2 / tob1) * 100.0:.0f}%")
    m1, m2 = b_start["mid"], b_now["mid"]
    if m1 > 0 and abs(m2 - m1) / m1 * 100.0 > ABORT_PRICE_JUMP_PCT:
        reasons.append(f"price {'+' if m2 > m1 else ''}{(m2 - m1) / m1 * 100.0:.2f}%")
    if slip_so_far is not None and forecast is not None and slip_so_far >= ABORT_SLIP_MULT * max(forecast, 0.05):
        reasons.append(f"slip {slip_so_far:.2f}% ≥ 2x forecast")
    return reasons


def _slip_so_far(filled: float, cost: float, start_mid: float, side: str):
    if filled <= 0 or cost <= 0 or not start_mid:
        return None
    avg = cost / filled
    return (avg - start_mid) / start_mid * 100.0 if side == "buy" else (start_mid - avg) / start_mid * 100.0


def _ladder(kraken, pair: str, volume: float, book: dict, validate: bool, side: str) -> dict:
    """Tier 3: 3 limit orders priced at successive levels (bounded walk),
    timeout, cancel leftovers, market-sweep remainder. Buys abort the sweep
    on a hostile book; sells always sweep (exits must complete)."""
    levels = book["asks"] if side == "buy" else book["bids"]
    is_buy = side == "buy"
    n = min(LADDER_SLICES, len(levels))
    if n == 0:
        return _finalize_single(kraken, pair,
                                (kraken.place_market_buy if is_buy else kraken.place_market_sell)(pair, volume, validate=validate),
                                volume, validate)

    if validate:  # paper: simulate the bounded walk at level prices
        per = volume / n
        cost = sum(per * float(levels[i][0]) for i in range(n))
        return {"id": "", "status": "filled", "average": cost / volume, "filled": volume,
                "cost": cost, "fee": 0.0, "unfilled_volume": 0.0, "aborted": False, "paper": True}

    place = kraken.place_limit_buy if is_buy else kraken.place_limit_sell
    per = volume / n
    entries = []
    for i in range(n):
        px = float(levels[i][0])
        try:
            o = place(pair, round(per, 6), px, validate=False)
            entries.append([str(o.get("id") or ""), px, False])
        except Exception as exc:
            logger.warning("Ladder slice %d/%d failed for %s: %s", i + 1, n, pair, exc)

    deadline = time.time() + LADDER_TIMEOUT_S
    filled = cost = fee = 0.0
    while time.time() < deadline:
        open_entries = [e for e in entries if not e[2]]
        if not open_entries:
            break
        time.sleep(LADDER_POLL_S)
        for e in open_entries:
            oid = e[0]
            if not oid:
                e[2] = True  # no id → assume gone
                continue
            try:
                o = kraken.fetch_order(oid, pair)
                f = float(o.get("filled") or 0)
                if f > 0 or o.get("status") in ("closed", "filled"):
                    filled += f
                    cost += float(o.get("cost") or 0)
                    fi = o.get("fee") or {}
                    if isinstance(fi, dict):
                        fee += float(fi.get("cost") or 0)
                    e[2] = True
            except Exception:
                pass

    # Cancel leftovers, then harvest any late partial fills
    for e in entries:
        if not e[2] and e[0]:
            try:
                kraken.cancel_order(e[0])
            except Exception:
                pass
            try:
                o = kraken.fetch_order(e[0], pair)
                f = float(o.get("filled") or 0)
                if f > 0:
                    filled += f
                    cost += float(o.get("cost") or 0)
                    fi = o.get("fee") or {}
                    if isinstance(fi, dict):
                        fee += float(fi.get("cost") or 0)
            except Exception:
                pass

    remainder = volume - filled
    aborted = False
    if remainder > 1e-9:
        hostile = []
        if is_buy:
            try:
                b2 = measure_book(kraken, pair)
                if b2:
                    hostile = _hostile_reasons(book, b2, "buy",
                                               _slip_so_far(filled, cost, book["mid"], "buy"))
            except Exception:
                pass
        if hostile:
            logger.warning("LADDER ABORT %s: %s — leaving %.6f unfilled", pair, "; ".join(hostile), remainder)
            aborted = True
        else:
            fn = kraken.place_market_buy if is_buy else kraken.place_market_sell
            w = _finalize_single(kraken, pair, fn(pair, round(remainder, 6), validate=False), remainder, False)
            filled += w["filled"]
            cost += w["cost"]
            fee += w["fee"]

    ids = ",".join(e[0] for e in entries if e[0])[:80]
    return {"id": ids, "status": "filled" if volume - filled <= 1e-9 else "partial",
            "average": (cost / filled) if filled else 0.0, "filled": filled,
            "cost": cost, "fee": fee, "unfilled_volume": max(0.0, volume - filled),
            "aborted": aborted}


def _twap(kraken, pair: str, volume: float, book: dict, validate: bool, side: str,
          forecast) -> dict:
    """Tier 4: 4 market slices, TWAP_INTERVAL_S apart, book re-checked per
    slice with abort conditions. Slice size also bounded by the increment
    rule (5% <20% consumption, 10% <100%, 25% above). Buys abort (never
    chase); sells force-complete with a final market sweep."""
    is_buy = side == "buy"
    place = kraken.place_market_buy if is_buy else kraken.place_market_sell
    start_mid = book["mid"]
    filled = cost = fee = 0.0
    slice_v = volume / TWAP_SLICES
    aborted = False

    if validate:  # paper: simulate at start mid
        return {"id": "", "status": "filled", "average": start_mid, "filled": volume,
                "cost": volume * start_mid, "fee": 0.0, "unfilled_volume": 0.0,
                "aborted": False, "paper": True}

    for i in range(TWAP_SLICES):
        remaining_v = volume - filled
        if remaining_v <= 1e-9:
            break
        v = slice_v
        try:
            b = measure_book(kraken, pair)
            if b:
                hostile = _hostile_reasons(book, b, side,
                                           _slip_so_far(filled, cost, start_mid, side), forecast)
                if hostile:
                    logger.warning("TWAP ABORT %s at slice %d: %s", pair, i + 1, "; ".join(hostile))
                    aborted = True
                    break
                tob = b["ask_tob"] if is_buy else b["bid_tob"]
                px = b["mid"]
                if tob > 0 and px > 0:
                    cons_slice = (slice_v * px) / tob * 100.0
                    inc = 5.0 if cons_slice < 20 else (10.0 if cons_slice < 100 else 25.0)
                    v = min(slice_v, inc / 100.0 * tob / px)
        except Exception:
            pass
        v = min(v, remaining_v)
        if v <= 0:
            break
        try:
            w = _finalize_single(kraken, pair, place(pair, round(v, 6), validate=False), v, False)
            filled += w["filled"]
            cost += w["cost"]
            fee += w["fee"]
        except Exception as exc:
            logger.warning("TWAP slice %d/%d failed for %s: %s", i + 1, TWAP_SLICES, pair, exc)
            break
        if i < TWAP_SLICES - 1:
            time.sleep(TWAP_INTERVAL_S)

    remainder = volume - filled
    if remainder > 1e-9:
        if is_buy and aborted:
            logger.warning("TWAP %s: %.6f left unfilled after abort (not chasing)", pair, remainder)
        else:
            w = _finalize_single(kraken, pair, place(pair, round(remainder, 6), validate=False), remainder, False)
            filled += w["filled"]
            cost += w["cost"]
            fee += w["fee"]

    return {"id": "", "status": "filled" if volume - filled <= 1e-9 else "partial",
            "average": (cost / filled) if filled else 0.0, "filled": filled,
            "cost": cost, "fee": fee, "unfilled_volume": max(0.0, volume - filled),
            "aborted": aborted}


def execute_tiered_buy(kraken, pair: str, volume: float, ref_price: float,
                       validate: bool = False) -> dict:
    """Entry point for buys. Returns a ccxt-compatible order dict plus tier
    metadata (tier, consumption_pct, forecast_pct, slippage_pct, method,
    unfilled_volume, aborted). Caller decides what to do with the remainder."""
    t0 = time.time()
    book = None
    try:
        book = measure_book(kraken, pair)
    except Exception as exc:
        logger.warning("Book measurement failed for %s — plain market fallback: %s", pair, exc)
    if not book or book["ask_tob"] <= 0 or not ref_price or ref_price <= 0:
        order = kraken.place_market_buy(pair, volume, validate=validate)
        res = _finalize_single(kraken, pair, order, volume, validate)
        res.update({"tier": None, "tier_name": "T1 (degraded)", "consumption_pct": 0.0,
                    "forecast_pct": None, "slippage_pct": None, "method": "market (degraded)",
                    "mid": None, "audit": "", "exec_seconds": round(time.time() - t0, 1),
                    "tiered": True})
        return res

    mid = book["mid"]
    notional = volume * ref_price
    forecast, _vw = forecast_slippage(book["asks"], notional, mid, "buy")
    num, name, cons = classify_tier(notional, book["ask_tob"])
    logger.info("EXEC %s BUY $%.0f → %s (%.1f%% of $%.0f book) · forecast slip %.3f%%",
                pair, notional, name, cons, book["ask_tob"], forecast or 0.0)

    if num <= 2:
        res = _finalize_single(kraken, pair, kraken.place_market_buy(pair, volume, validate=validate), volume, validate)
        method = "market"
    elif num == 3:
        res = _ladder(kraken, pair, volume, book, validate, "buy")
        method = "limit ladder"
    elif num == 4:
        res = _twap(kraken, pair, volume, book, validate, "buy", forecast)
        method = "twap"
    else:
        # T5/T6: execute only what the book absorbs (Tier 3 boundary)
        cap_vol = (T5_CAP_PCT / 100.0 * book["ask_tob"]) / ref_price
        if cap_vol >= volume:
            res = _ladder(kraken, pair, volume, book, validate, "buy")
            method = "limit ladder"
        elif cap_vol > 0:
            res = _ladder(kraken, pair, cap_vol, book, validate, "buy")
            res["unfilled_volume"] = volume - res.get("filled", 0.0)
            method = f"capped ladder ({T5_CAP_PCT:.0f}% of book)"
        else:
            res = {"id": "", "status": "skipped", "average": 0.0, "filled": 0.0,
                   "cost": 0.0, "fee": 0.0, "unfilled_volume": volume, "aborted": False}
            method = "skipped (no capacity)"

    avg = res.get("average") or ref_price
    actual = (avg - mid) / mid * 100.0 if mid else None
    note = "" if validate else audit_slippage(pair, forecast, actual)
    res.update({"tier": num, "tier_name": name, "consumption_pct": round(cons, 1),
                "forecast_pct": None if forecast is None else round(forecast, 3),
                "slippage_pct": None if actual is None else round(actual, 3),
                "method": method, "mid": mid, "audit": note,
                "exec_seconds": round(time.time() - t0, 1), "tiered": True})
    return res


def execute_tiered_sell(kraken, pair: str, volume: float, ref_price: float = 0.0,
                        validate: bool = False) -> dict:
    """Entry point for sells. Same tiering, but NEVER caps and ALWAYS
    completes — exits are risk management. Large sells ladder/TWAP to avoid
    crashing the book, then market-sweep whatever is left."""
    t0 = time.time()
    book = None
    try:
        book = measure_book(kraken, pair)
    except Exception as exc:
        logger.warning("Book measurement failed for %s — plain market fallback: %s", pair, exc)
    if not book or book["bid_tob"] <= 0:
        order = kraken.place_market_sell(pair, volume, validate=validate)
        res = _finalize_single(kraken, pair, order, volume, validate)
        res.update({"tier": None, "tier_name": "T1 (degraded)", "consumption_pct": 0.0,
                    "forecast_pct": None, "slippage_pct": None, "method": "market (degraded)",
                    "mid": None, "audit": "", "exec_seconds": round(time.time() - t0, 1),
                    "tiered": True})
        return res

    mid = book["mid"]
    px = ref_price if ref_price and ref_price > 0 else mid
    notional = volume * px
    forecast, _vw = forecast_slippage(book["bids"], notional, mid, "sell")
    num, name, cons = classify_tier(notional, book["bid_tob"])
    logger.info("EXEC %s SELL $%.0f → %s (%.1f%% of $%.0f book) · forecast slip %.3f%%",
                pair, notional, name, cons, book["bid_tob"], forecast or 0.0)

    if num <= 2:
        res = _finalize_single(kraken, pair, kraken.place_market_sell(pair, volume, validate=validate), volume, validate)
        method = "market"
    elif num == 3:
        res = _ladder(kraken, pair, volume, book, validate, "sell")
        method = "limit ladder"
    else:
        res = _twap(kraken, pair, volume, book, validate, "sell", forecast)
        method = "twap"

    avg = res.get("average") or mid
    actual = (mid - avg) / mid * 100.0 if mid else None
    note = "" if validate else audit_slippage(pair, forecast, actual)
    res.update({"tier": num, "tier_name": name, "consumption_pct": round(cons, 1),
                "forecast_pct": None if forecast is None else round(forecast, 3),
                "slippage_pct": None if actual is None else round(actual, 3),
                "method": method, "mid": mid, "audit": note,
                "exec_seconds": round(time.time() - t0, 1), "tiered": True})
    return res


# ── Scale-in queue (multi-tick position building on thin books) ──────────

def in_buy_cooldown(now: datetime | None = None) -> bool:
    """True during the 20:00-24:00 no-buy cooldown (single source: engine ROTATIONS)."""
    n = now or _now()
    try:
        from volatility_engine import ROTATIONS
        for s, e, k, *_ in ROTATIONS:
            if s <= n.hour < e:
                return k == "20-24"
    except Exception:
        return 20 <= n.hour < 24
    return False


def schedule_scale_in(pair: str, remaining_usd: float, entry_ref: float = 0.0) -> bool:
    """Queue a capped remainder for multi-tick scale-in (≤20% of book per slice)."""
    if remaining_usd < MIN_TRADE_USD:
        return False
    fresh = _load_state()
    scales = fresh.setdefault("pending_scales", {})
    now = _now()
    sc = scales.get(pair)
    if sc:
        sc["remaining_usd"] = float(sc.get("remaining_usd", 0.0)) + float(remaining_usd)
        sc["slices_left"] = max(int(sc.get("slices_left", 0)), SCALE_MAX_SLICES)
        sc["expire"] = (now + timedelta(hours=SCALE_EXPIRE_H)).isoformat()
    else:
        scales[pair] = {"remaining_usd": float(remaining_usd), "slices_left": SCALE_MAX_SLICES,
                        "expire": (now + timedelta(hours=SCALE_EXPIRE_H)).isoformat(),
                        "entry_ref": float(entry_ref or 0.0), "created": now.isoformat()}
    _save_state(fresh)
    logger.info("SCALE-IN scheduled: %s $%.0f over ≤%d slices (expires in %dh)",
                pair, remaining_usd, SCALE_MAX_SLICES, SCALE_EXPIRE_H)
    return True


def cancel_scale_in(pair: str) -> None:
    """Called when a position is closed — stop scaling into it."""
    try:
        fresh = _load_state()
        if fresh.get("pending_scales", {}).pop(pair, None) is not None:
            _save_state(fresh)
            logger.info("SCALE-IN cancelled for %s (position closed)", pair)
    except Exception:
        pass


def drain_pending_scales(kraken, config) -> str:
    """One pass over the scale-in queue. Executes at most one slice per pair
    per tick through the bot engine (so positions fold + trades log), with the
    FULL gate pipeline re-checked per slice. Kill switch and the 20-24 cooldown
    pause the drain. Returns a human-readable summary ('' if nothing to do)."""
    scales = _load_state().get("pending_scales") or {}
    if not scales:
        return ""
    if getattr(config, "paper_mode", False):
        return "paper mode — scale-ins skipped"
    try:
        from signal_bridge import is_kill_switch_active
        if is_kill_switch_active():
            return "kill switch active — scale-ins paused"
    except Exception:
        pass
    if in_buy_cooldown():
        return "cooldown (20-24) — scale-ins paused"

    notes = []
    now = _now()
    for pair in list(scales.keys()):
        sc = dict(scales[pair])
        rem = float(sc.get("remaining_usd", 0.0))
        try:
            expire = datetime.fromisoformat(sc.get("expire", ""))
        except Exception:
            expire = now
        if now >= expire:
            scales.pop(pair)
            notes.append(f"{pair} scale-in expired (${rem:.0f} unfilled)")
            continue
        if rem < MIN_TRADE_USD or int(sc.get("slices_left", 0)) <= 0:
            scales.pop(pair)
            notes.append(f"{pair} scale-in done (${rem:.0f} residual)")
            continue

        try:
            liq = gate_buy(kraken, pair, rem, min_cost_usd=0.0, with_stability=True)
        except Exception as exc:
            liq = {"ok": False, "reason": f"gate error: {exc}"}
        if not liq.get("ok"):
            reason = liq.get("reason", "?")
            if "blacklist" in reason or "thin book" in reason:
                scales.pop(pair)
                notes.append(f"{pair} scale-in dropped ({reason})")
            else:
                sc["slices_left"] = int(sc.get("slices_left", 0)) - 1
                if sc["slices_left"] <= 0:
                    scales.pop(pair)
                else:
                    scales[pair] = sc
                notes.append(f"{pair} slice skipped ({reason})")
            continue

        slice_usd = float(liq.get("spend_usd", rem))
        try:
            from strategy import Signal
            from bot_engine import BotEngine
            engine = BotEngine(kraken, config)
            px = (liq.get("book") or {}).get("mid") or kraken.fetch_price(pair)
            sig = Signal(action="buy", confidence=0, price=px,
                         reasons=[f"{SCALE_REASON_MARK} (${slice_usd:.0f} of ${rem:.0f})"])
            old_pair = config.pair
            config.pair = pair
            try:
                res = engine._execute_buy(sig, pair=pair)
            finally:
                config.pair = old_pair
        except Exception as exc:
            logger.warning("Scale-in slice failed for %s: %s", pair, exc)
            sc["slices_left"] = int(sc.get("slices_left", 0)) - 1
            if sc["slices_left"] <= 0:
                scales.pop(pair)
            else:
                scales[pair] = sc
            notes.append(f"{pair} slice failed ({exc})")
            continue

        if "error" in res or "skipped" in res:
            why = res.get("error") or res.get("skipped") or "?"
            sc["slices_left"] = int(sc.get("slices_left", 0)) - 1
            if sc["slices_left"] <= 0:
                scales.pop(pair)
            else:
                scales[pair] = sc
            notes.append(f"{pair} slice rejected ({why})")
            continue

        executed = float(res.get("cost") or 0) or slice_usd
        sc["remaining_usd"] = max(0.0, rem - executed)
        sc["slices_left"] = int(sc.get("slices_left", 0)) - 1
        notes.append(f"{pair} +${executed:.0f} (${sc['remaining_usd']:.0f} left, {sc['slices_left']} slices)")
        if sc["remaining_usd"] < MIN_TRADE_USD or sc["slices_left"] <= 0:
            scales.pop(pair)
        else:
            scales[pair] = sc

    # Merge into a FRESH read before saving — gate_buy may have written state
    # (thin-book blacklist / audit strikes) while we were executing.
    fresh = _load_state()
    fresh["pending_scales"] = scales
    _save_state(fresh)
    return "; ".join(notes)


# ── Advisory snapshot (console + scanner — read-only, never gates money) ──

def liquidity_snapshot(exchange, pair: str, notional_usd: float) -> dict:
    """One book snapshot + tier/health/slip readouts for display. Blacklist
    status included so the scanner can skip writing dead-market signals."""
    out = {"ok": False, "blacklisted": False, "blacklist_reason": None}
    bl, bl_reason = is_blacklisted(pair)
    out["blacklisted"], out["blacklist_reason"] = bl, bl_reason
    try:
        book = measure_book(exchange, pair)
    except Exception as exc:
        out["reason"] = f"book unavailable: {exc}"
        return out
    if not book or not book.get("mid"):
        out["reason"] = "empty book"
        return out
    out["ok"] = True
    out["book"] = {"ask_tob": book["ask_tob"], "bid_tob": book["bid_tob"],
                   "mid": book["mid"], "spread_bps": book["spread_bps"],
                   "best_ask": book["best_ask"], "best_bid": book["best_bid"]}
    score, breakdown = liquidity_health_score(book, pair)
    out["score"], out["breakdown"] = score, breakdown
    # thin-book branch removed ("no more thin books") — thin books flow
    # through the normal tier/health math like everything else
    tier = classify_tier(notional_usd, book["ask_tob"])
    slip, _vw = forecast_slippage(book["asks"], notional_usd, book["mid"], "buy")
    out["tier"], out["slippage_pct"] = tier, slip
    out["capped_usd"] = min(notional_usd, SIZE_CAP_PCT / 100.0 * book["ask_tob"])
    num = tier[0]
    if num <= 2:
        out["method"], out["est_time"] = "market order", "instant"
    elif num == 3:
        out["method"], out["est_time"] = f"limit ladder ×{LADDER_SLICES}", f"~{LADDER_TIMEOUT_S}s"
    elif num == 4:
        out["method"], out["est_time"] = f"TWAP ×{TWAP_SLICES}", f"~{TWAP_SLICES * TWAP_INTERVAL_S // 60} min"
    else:
        out["method"], out["est_time"] = f"capped @ {T5_CAP_PCT:.0f}% book + ladder", f"~{LADDER_TIMEOUT_S}s"
    return out
