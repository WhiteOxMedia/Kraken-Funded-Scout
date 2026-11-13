"""Live entry path for the validated 5m bounce + PEPE GO BIG edge.

The tape validation lives in ``scratch/bounce_core.py``.  This module is the
same rule against the live exchange feed:

* Rule A -- liquid capitulation bounce: the last CLOSED 5m bar closes at or
  below -4.0% versus the prior close, and 20% of its quote volume is at least
  $10,000.  Enter at the current market (the next bar's open in practice),
  hold at most 6 bars, stop -2%, take +3%.
* Rule B -- PEPE GO BIG: a fresh EMA9/21 cross up on a green 5m bar, breadth
  at/above 50%, and the normal carry >= balance gate.  Stop -1%, take +5%
  ($100 / $500 on the $10,000 contest).

Only one all-in slot exists.  ``tick`` refuses to fire while ANY contest watch
row is open, and the scanner's ordinary exit watch owns the row afterwards:
the row carries its hard stop, goal and, for the bounce, the 6-bar deadline.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCH_JSON = ROOT / "contest_watch.json"

BAR_SEC = 300
HOLD_BARS = 6
DROP_PCT = 4.0
MIN_CARRY_USD = 10_000.0
BOUNCE_STOP_PCT = 2.0
BOUNCE_TAKE_PCT = 3.0
PEPE_STOP_USD = 100.0
PEPE_TAKE_USD = 500.0
PEPE_MIN_BUFFER_USD = 250.0
BREADTH_MIN = 50.0
CARRY_FRAC = 0.20

ASSETS = [
    "BTC", "ETH", "ZEC", "XRP", "SOL", "PEPE", "NEAR", "HYPE", "SUI", "LTC",
    "ADA", "TAO", "UNI", "DOGE", "LINK", "AVAX", "INJ", "AAVE", "PUMP", "TON",
    "BCH", "XLM", "ATOM", "ARB", "OP", "ONDO", "WLD", "RENDER", "BNB", "TRX",
]
PEPE_PAIR = "PEPE/USD"

_last_bar_scanned = 0.0


def _ema(values: list[float], period: int) -> list[float]:
    out: list[float] = []
    alpha = 2.0 / (period + 1.0)
    for i, value in enumerate(values):
        out.append(float(value) if i == 0
                   else alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _closed(rows: list, now: float | None = None) -> list:
    """Drop the in-progress candle; ccxt includes it on Kraken."""
    rows = [r for r in (rows or []) if isinstance(r, (list, tuple)) and len(r) >= 6]
    if not rows:
        return []
    now_ms = (time.time() if now is None else float(now)) * 1000.0
    if float(rows[-1][0]) + BAR_SEC * 1000.0 > now_ms:
        rows = rows[:-1]
    return rows


def _quote_volume(row) -> float:
    """ccxt's 5m volume is base units; the rule is 20% of QUOTE volume."""
    return float(row[5] or 0.0) * float(row[4] or 0.0)


def _frame(rows: list) -> dict | None:
    if len(rows) < 30:
        return None
    close = [float(r[4]) for r in rows]
    return {
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": close,
        "qv": [_quote_volume(r) for r in rows],
        "ts": [int(r[0]) for r in rows],
        "ema9": _ema(close, 9),
        "ema21": _ema(close, 21),
    }


def scan_frames(frames: dict[str, dict],
                pepe_carry: float = MIN_CARRY_USD) -> dict | None:
    """Return the one entry this closed bar offers, or None.

    Bounce candidates win over PEPE when both appear on the same bar, matching
    the validated event ordering (bounce events are built before PEPE).  PEPE's
    carry gate is the account balance, exactly as the GO BIG rule states.
    """
    breadth = [a for a, f in frames.items()
               if f["close"][-1] > f["ema21"][-1]]
    breadth_pct = 100.0 * len(breadth) / len(frames) if frames else 0.0

    bounces = []
    for asset, f in frames.items():
        prev, close = f["close"][-2], f["close"][-1]
        if prev <= 0:
            continue
        drop = (close / prev - 1.0) * 100.0
        carry = f["qv"][-1] * CARRY_FRAC
        if drop <= -DROP_PCT and carry >= MIN_CARRY_USD:
            bounces.append({"asset": asset, "kind": "bounce",
                            "drop_pct": drop, "carry": carry})
    if bounces:
        bounces.sort(key=lambda b: (b["drop_pct"], -b["carry"], b["asset"]))
        return bounces[0]

    if breadth_pct < BREADTH_MIN:
        return None
    f = frames.get("PEPE")
    if not f:
        return None
    close, prev = f["close"][-1], f["close"][-2]
    e9, p9 = f["ema9"][-1], f["ema9"][-2]
    e21, p21 = f["ema21"][-1], f["ema21"][-2]
    carry = f["qv"][-1] * CARRY_FRAC
    crossed = e9 > p9 and e9 > e21 and p9 <= p21
    if crossed and close > f["open"][-1] and carry >= float(pepe_carry):
        strength = (close / e21 - 1.0) * 100.0
        return {"asset": "PEPE", "kind": "pepe", "drop_pct": 0.0,
                "carry": carry, "breadth_pct": breadth_pct,
                "conviction": min(100.0, 85.0 + max(0.0, strength) * 10.0)}
    return None



def _entry_epoch(w: dict) -> float | None:
    for key in ("opened_ts", "added"):
        raw = w.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError):
            continue
    return None


def _edge_kind(w: dict) -> str:
    setup = w.get("setup") if isinstance(w.get("setup"), dict) else {}
    return str(setup.get("kind") or ("pepe" if w.get("go_big") else "bounce"))


def exit_signal(exchange, pair: str, w: dict, *, live: float | None = None,
                now: float | None = None) -> dict | None:
    """First validated 5m exit for this edge row, or None.

    The tape rules are the ones ``bounce_core.exit_fill`` and the GO BIG
    replay used: check the entry bar and every later bar's OPEN then LOW/HIGH,
    stop before take.  For PEPE, a closed 5m close that crosses below EMA9 is
    the momentum death exit.  Returning a reason lets the scanner take the
    ordinary GET OUT auto-close path; it never writes the book here.
    """
    entry = float(w.get("entry") or 0.0)
    size = float(w.get("size") or 0.0)
    entry_ts = _entry_epoch(w)
    if entry <= 0 or size <= 0 or entry_ts is None:
        return None
    stop_usd = float(w.get("hard_stop_usd") or 0.0)
    stop = entry * (1.0 - stop_usd / size) if stop_usd > 0 else 0.0
    goal_usd = float(w.get("goal_usd") or 0.0)
    goal_pct = float(w.get("goal_pct") or 0.0)
    take = (entry * (1.0 + goal_usd / size) if goal_usd > 0 else
            entry * (1.0 + goal_pct / 100.0) if goal_pct > 0 else 0.0)

    now = time.time() if now is None else float(now)
    rows = [r for r in (exchange.fetch_ohlcv(pair, "5m", limit=120) or [])
            if isinstance(r, (list, tuple)) and len(r) >= 6]
    if not rows:
        return None
    entry_ms = entry_ts * 1000.0
    from_entry = [i for i, r in enumerate(rows)
                  if float(r[0]) + BAR_SEC * 1000.0 > entry_ms]
    if not from_entry:
        return None
    first = from_entry[0]
    kind = _edge_kind(w)
    # The bounce only owns its entry bar plus five more; a high/low after the
    # 6-bar window belongs to the time exit, not to the stop/take.
    scan_idx = from_entry[:HOLD_BARS] if kind == "bounce" else from_entry
    closes = [float(r[4]) for r in rows]
    # The live ticker is a fresher close for the in-progress bar; high/low
    # still come from the exchange's 5m candle.
    if live and rows[-1][0] + BAR_SEC * 1000.0 > now * 1000.0:
        closes[-1] = float(live)
    ema9 = _ema(closes, 9)

    for j in scan_idx:
        r = rows[j]
        open_, high, low = float(r[1]), float(r[2]), float(r[3])
        if stop > 0 and open_ <= stop:
            return {"kind": "stop_gap",
                    "reason": (f"STOPPED OUT — 5m bar opened ${open_:.6g} "
                               f"through the ${stop:.6g} stop.")}
        if stop > 0 and low <= stop:
            return {"kind": "stop",
                    "reason": (f"STOPPED OUT — 5m bar low ${low:.6g} hit the "
                               f"${stop:.6g} stop.")}
        if take > 0 and open_ >= take:
            return {"kind": "take_gap",
                    "reason": (f"TAKE IT — 5m bar opened ${open_:.6g} through "
                               f"the ${take:.6g} goal.")}
        if take > 0 and high >= take:
            return {"kind": "take",
                    "reason": (f"TAKE IT — 5m bar high ${high:.6g} hit the "
                               f"${take:.6g} goal.")}
        if kind == "pepe" and j > first \
                and float(r[0]) + BAR_SEC * 1000.0 <= now * 1000.0 \
                and closes[j - 1] >= ema9[j - 1] and closes[j] < ema9[j]:
            return {"kind": "momentum",
                    "reason": (f"PEPE GO BIG EXIT — 5m close ${closes[j]:.6g} "
                               f"crossed below EMA9 ${ema9[j]:.6g}.")}
    return None


def _watch_is_flat() -> bool:
    try:
        doc = json.loads(WATCH_JSON.read_text(encoding="utf-8"))
        return not any(v is not None for v in (doc or {}).values())
    except Exception:
        return False


def tick(exchange, track, logger=None, now: float | None = None) -> dict | None:
    """Scan the last closed 5m bar once and hand a fire to ``track``.

    ``track`` is ``background_scanner.auto_track_popup``.  No bridge order is
    written here: the contest watch is the paper book, and the scanner's exit
    watch closes it through the normal GET OUT path.
    """
    global _last_bar_scanned

    def _log(level: str, msg: str, *args) -> None:
        if logger is not None:
            getattr(logger, level)(msg, *args)

    now = time.time() if now is None else float(now)
    bar = float(int(now // BAR_SEC) * BAR_SEC)
    if bar <= _last_bar_scanned:
        return None
    if not _watch_is_flat():
        _last_bar_scanned = bar
        return None

    import contest_account as ca
    try:
        balance = float(ca.load_state()["current_balance"])
    except Exception as exc:
        _log("warning", "BOUNCE LIVE: account read failed (%s) — no entry", exc)
        return None
    if balance <= 0:
        _last_bar_scanned = bar
        return None

    frames: dict[str, dict] = {}
    for asset in ASSETS:
        pair = f"{asset}/USD"
        try:
            rows = _closed(exchange.fetch_ohlcv(pair, "5m", limit=80), now)
            frame = _frame(rows)
            if frame:
                frames[asset] = frame
        except Exception as exc:
            _log("debug", "BOUNCE LIVE: %s 5m read failed: %s", pair, exc)
    _last_bar_scanned = bar
    if len(frames) < 10:
        _log("warning", "BOUNCE LIVE: only %d 5m frames — no entry",
             len(frames))
        return None

    newest = max(f["ts"][-1] for f in frames.values()) / 1000.0
    if now - (newest + BAR_SEC) > BAR_SEC * 1.5:
        _log("info", "BOUNCE LIVE: newest closed bar is stale (%.0fs) — no entry",
             now - (newest + BAR_SEC))
        return None

    signal = scan_frames(frames, pepe_carry=max(balance, MIN_CARRY_USD))
    if not signal:
        return None

    pair = f"{signal['asset']}/USD"
    try:
        entry = float(exchange.fetch_ticker(pair)["last"])
    except Exception as exc:
        _log("warning", "BOUNCE LIVE: %s ticker failed (%s) — no entry",
             pair, exc)
        return None
    if entry <= 0:
        return None

    # Re-check after the slow 5m fetch: another route may have opened a slot.
    if not _watch_is_flat():
        _log("info", "BOUNCE LIVE: %s signal skipped — the one slot is occupied",
             signal["asset"])
        return None

    if signal["kind"] == "bounce":
        size = balance
        stop_usd = size * BOUNCE_STOP_PCT / 100.0
        take_pct = BOUNCE_TAKE_PCT
        goal_usd = 0.0
        hold_ts = now + HOLD_BARS * BAR_SEC
        go_big = False
        why = (f"5m capitulation bounce — {signal['drop_pct']:+.2f}% close, "
               f"${signal['carry']:,.0f} carry (>= $10k), 6-bar hold")
    else:
        # THE VALIDATED GO BIG GATE: the same support + plan calls the
        # replay used. PEPE is not all-in on a hand-rolled breadth check.
        try:
            state = ca.load_state()
            start = float(state.get("start_balance") or 0.0)
            tide_state = "RISING" if signal["breadth_pct"] >= 75 else "FLAT"
            gb_ok, gb_why = ca.go_big_supported(
                action="buy", tide_state=tide_state,
                tide_pct=float(signal["breadth_pct"]),
                carry_usd=float(signal["carry"]), balance=balance,
                conviction=float(signal["conviction"]),
                floor=float(ca.GO_BIG_MIN_CONVICTION), slot_open=False)
            if not gb_ok:
                _log("info", "BOUNCE LIVE: PEPE GO BIG refused — %s", gb_why)
                return None
            plan = ca.go_big_plan(
                balance, start, supported=True, stopped=False,
                stop_usd=PEPE_STOP_USD, take_usd=PEPE_TAKE_USD,
                min_buffer_usd=PEPE_MIN_BUFFER_USD)
        except Exception as exc:
            _log("warning", "BOUNCE LIVE: PEPE GO BIG gate failed (%s) — "
                 "no entry", exc)
            return None
        if not plan.get("ok"):
            _log("info", "BOUNCE LIVE: PEPE GO BIG plan refused — %s",
                 plan.get("why"))
            return None
        size = float(plan["spend_usd"])
        stop_usd = float(plan["stop_usd"])
        take_usd = float(plan["take_usd"])
        take_pct = take_usd / size * 100.0 if size > 0 else 0.0
        goal_usd = take_usd
        hold_ts = None
        go_big = True
        why = (f"PEPE GO BIG — {gb_why}; {plan.get('why')} "
               f"(5m cross, breadth {signal['breadth_pct']:.0f}%, "
               f"${signal['carry']:,.0f} carry)")

    ok = track(
        pair=pair, entry=entry, size=size, goal_pct=take_pct,
        window_min=(HOLD_BARS * 5 if signal["kind"] == "bounce" else 60.0),
        why=why, source="bounce_live",
        setup={"route": "bounce_live", "kind": signal["kind"]},
        plan_basis="validated 5m closed-bar edge", exchange=exchange,
        go_big=go_big,
        hard_stop_usd=stop_usd,
        goal_usd=goal_usd,
        bounce_exit_ts=hold_ts,
    )
    if ok:
        _log("info", "BOUNCE LIVE FIRE: %s $%.2f all-in — %s",
             signal["asset"], balance, why)
        return {"pair": pair, "entry": entry, "size": balance,
                "kind": signal["kind"], "why": why}
    _log("warning", "BOUNCE LIVE: %s fire refused by the watch writer",
         signal["asset"])
    return None
