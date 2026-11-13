"""Live path for the validated 5m bounce + PEPE GO BIG edge.

Rule A -- liquid capitulation bounce:
  * last closed 5m bar at or below -4.0% versus the prior close
  * 20% of that bar's quote volume >= $10,000
  * enter next market tick, hold at most 6 bars, stop -2%, take +3%

Rule B -- PEPE GO BIG:
  * fresh EMA9/21 cross up on a green 5m bar
  * contest_account.go_big_supported / go_big_plan decide the entry
  * hard $100 stop, $500 take, momentum-death exit

One all-in slot.  This module only proposes an entry; the scanner's contest
watch and exit watch own the position and the death floor.
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
CARRY_FRAC = 0.20
BOUNCE_STOP_PCT = 2.0
BOUNCE_TAKE_PCT = 3.0
PEPE_STOP_USD = 100.0
PEPE_TAKE_USD = 500.0
PEPE_MIN_BUFFER_USD = 250.0

ASSETS = [
    "BTC", "ETH", "ZEC", "XRP", "SOL", "PEPE", "NEAR", "HYPE", "SUI", "LTC",
    "ADA", "TAO", "UNI", "DOGE", "LINK", "AVAX", "INJ", "AAVE", "PUMP", "TON",
    "BCH", "XLM", "ATOM", "ARB", "OP", "ONDO", "WLD", "RENDER", "BNB", "TRX",
]

_last_bar_scanned = 0.0


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    out: list[float] = []
    for value in values:
        out.append(float(value) if not out
                   else alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _closed(rows: list, now: float) -> list:
    """Drop the in-progress candle; ccxt includes it on Kraken."""
    rows = [r for r in (rows or []) if len(r) >= 6]
    if rows and float(rows[-1][0]) + BAR_SEC * 1000.0 > now * 1000.0:
        rows = rows[:-1]
    return rows


def _frame(rows: list) -> dict | None:
    if len(rows) < 30:
        return None
    close = [float(r[4]) for r in rows]
    return {
        "open": [float(r[1]) for r in rows],
        "close": close,
        "qv": [float(r[5]) * float(r[4]) for r in rows],
        "ts": [int(r[0]) for r in rows],
        "ema9": _ema(close, 9),
        "ema21": _ema(close, 21),
    }


def _breadth(frames: dict[str, dict]) -> float:
    if not frames:
        return 0.0
    above = sum(f["close"][-1] > f["ema21"][-1] for f in frames.values())
    return 100.0 * above / len(frames)


def scan_frames(frames: dict[str, dict]) -> dict | None:
    """The one entry this closed bar offers, or None."""
    # Bounce candidates first, in the validated replay's asset order.
    for asset, f in frames.items():
        prev, close = f["close"][-2], f["close"][-1]
        drop = (close / prev - 1.0) * 100.0 if prev > 0 else 0.0
        if drop <= -DROP_PCT:
            carry = f["qv"][-1] * CARRY_FRAC
            if carry >= MIN_CARRY_USD:
                return {"asset": asset, "kind": "bounce",
                        "drop_pct": drop, "carry": carry}

    f = frames.get("PEPE")
    if not f:
        return None
    close = f["close"][-1]
    e9, p9 = f["ema9"][-1], f["ema9"][-2]
    e21, p21 = f["ema21"][-1], f["ema21"][-2]
    if not (e9 > p9 and e9 > e21 and p9 <= p21
            and close > f["open"][-1]):
        return None
    strength = (close / e21 - 1.0) * 100.0
    return {"asset": "PEPE", "kind": "pepe",
            "carry": f["qv"][-1] * CARRY_FRAC,
            "conviction": min(100.0, 85.0 + max(0.0, strength) * 10.0)}


def _entry_epoch(w: dict) -> float | None:
    raw = w.get("opened_ts") or w.get("added")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _levels(w: dict) -> tuple[float, float, float, float]:
    entry = float(w.get("entry") or 0.0)
    size = float(w.get("size") or 0.0)
    stop_usd = float(w.get("hard_stop_usd") or 0.0)
    goal_usd = float(w.get("goal_usd") or 0.0)
    goal_pct = float(w.get("goal_pct") or 0.0)
    stop = entry * (1.0 - stop_usd / size) if entry > 0 and size > 0 and stop_usd > 0 else 0.0
    take = (entry * (1.0 + goal_usd / size) if entry > 0 and size > 0 and goal_usd > 0
            else entry * (1.0 + goal_pct / 100.0) if entry > 0 and goal_pct > 0 else 0.0)
    return entry, size, stop, take


def exit_signal(exchange, pair: str, w: dict, *,
                now: float | None = None) -> str | None:
    """First stop, take or momentum exit from the 5m tape, or None."""
    entry, size, stop, take = _levels(w)
    entry_ts = _entry_epoch(w)
    if entry <= 0 or size <= 0 or entry_ts is None:
        return None
    now = time.time() if now is None else float(now)
    rows = [r for r in (exchange.fetch_ohlcv(pair, "5m", limit=120) or [])
            if len(r) >= 6]
    entry_ms = entry_ts * 1000.0
    from_entry = [i for i, r in enumerate(rows)
                  if float(r[0]) + BAR_SEC * 1000.0 > entry_ms]
    if not from_entry:
        return None

    kind = "pepe" if w.get("go_big") else "bounce"
    first = from_entry[0]
    scan_idx = from_entry[:HOLD_BARS] if kind == "bounce" else from_entry
    closes = [float(r[4]) for r in rows]
    ema9 = _ema(closes, 9) if kind == "pepe" else []

    for j in scan_idx:
        r = rows[j]
        open_, high, low = float(r[1]), float(r[2]), float(r[3])
        if stop > 0 and open_ <= stop:
            return f"STOPPED OUT — 5m bar opened ${open_:.6g} through the ${stop:.6g} stop."
        if stop > 0 and low <= stop:
            return f"STOPPED OUT — 5m bar low ${low:.6g} hit the ${stop:.6g} stop."
        if take > 0 and open_ >= take:
            return f"TAKE IT — 5m bar opened ${open_:.6g} through the ${take:.6g} goal."
        if take > 0 and high >= take:
            return f"TAKE IT — 5m bar high ${high:.6g} hit the ${take:.6g} goal."
        if kind == "pepe" and j > first \
                and float(r[0]) + BAR_SEC * 1000.0 <= now * 1000.0 \
                and closes[j - 1] >= ema9[j - 1] and closes[j] < ema9[j]:
            return f"PEPE GO BIG EXIT — 5m close ${closes[j]:.6g} crossed below EMA9 ${ema9[j]:.6g}."
    return None


def ticker_exit(w: dict, live: float, now: float | None = None) -> str | None:
    """Ticker-only fallback if the 5m read fails."""
    entry, size, stop, take = _levels(w)
    if entry <= 0 or size <= 0 or live <= 0:
        return None
    if stop > 0 and live <= stop:
        return f"STOPPED OUT — ticker ${live:.6g} hit the ${stop:.6g} stop."
    if take > 0 and live >= take:
        return f"TAKE IT — ticker ${live:.6g} hit the ${take:.6g} goal."
    exit_ts = float(w.get("bounce_exit_ts") or 0.0)
    if exit_ts and (time.time() if now is None else now) >= exit_ts:
        return "5m BOUNCE TIME EXIT — 6 bars held; clear the slot."
    return None


def _watch_is_flat() -> bool:
    try:
        doc = json.loads(WATCH_JSON.read_text(encoding="utf-8"))
        return not any(v is not None for v in (doc or {}).values())
    except Exception:
        return False


def tick(exchange, track, logger=None, now: float | None = None) -> dict | None:
    """Scan the last closed bar once; hand any fire to ``track``."""
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
        state = ca.load_state()
        balance = float(state["current_balance"])
    except Exception as exc:
        _log("warning", "BOUNCE LIVE: account read failed (%s) — no entry", exc)
        return None
    if balance <= 0:
        _last_bar_scanned = bar
        return None

    frames: dict[str, dict] = {}
    for asset in ASSETS:
        try:
            frame = _frame(_closed(
                exchange.fetch_ohlcv(f"{asset}/USD", "5m", limit=80), now))
            if frame:
                frames[asset] = frame
        except Exception as exc:
            _log("debug", "BOUNCE LIVE: %s 5m read failed: %s", asset, exc)
    _last_bar_scanned = bar
    if len(frames) < 10:
        _log("warning", "BOUNCE LIVE: only %d 5m frames — no entry", len(frames))
        return None

    newest = max(f["ts"][-1] for f in frames.values()) / 1000.0
    if now - (newest + BAR_SEC) > BAR_SEC * 1.5:
        _log("info", "BOUNCE LIVE: newest closed bar is stale — no entry")
        return None

    signal = scan_frames(frames)
    if not signal:
        return None
    pair = f"{signal['asset']}/USD"
    try:
        entry = float(exchange.fetch_ticker(pair)["last"])
    except Exception as exc:
        _log("warning", "BOUNCE LIVE: %s ticker failed (%s) — no entry", pair, exc)
        return None
    if entry <= 0 or not _watch_is_flat():
        return None

    if signal["kind"] == "bounce":
        size = balance
        stop_usd = size * BOUNCE_STOP_PCT / 100.0
        take_pct, goal_usd = BOUNCE_TAKE_PCT, 0.0
        hold_ts = now + HOLD_BARS * BAR_SEC
        go_big = False
        why = (f"5m capitulation bounce — {signal['drop_pct']:+.2f}% close, "
               f"${signal['carry']:,.0f} carry (>= $10k), 6-bar hold")
    else:
        try:
            breadth = _breadth(frames)
            tide_state = "RISING" if breadth >= 75 else "FLAT"
            gb_ok, gb_why = ca.go_big_supported(
                action="buy", tide_state=tide_state, tide_pct=breadth,
                carry_usd=signal["carry"], balance=balance,
                conviction=signal["conviction"],
                floor=ca.GO_BIG_MIN_CONVICTION, slot_open=False)
            if not gb_ok:
                _log("info", "BOUNCE LIVE: PEPE GO BIG refused — %s", gb_why)
                return None
            plan = ca.go_big_plan(
                balance, float(state["start_balance"]), supported=True,
                stopped=False, stop_usd=PEPE_STOP_USD,
                take_usd=PEPE_TAKE_USD, min_buffer_usd=PEPE_MIN_BUFFER_USD)
        except Exception as exc:
            _log("warning", "BOUNCE LIVE: PEPE GO BIG gate failed (%s)", exc)
            return None
        if not plan.get("ok"):
            _log("info", "BOUNCE LIVE: PEPE GO BIG plan refused — %s",
                 plan.get("why"))
            return None
        size = float(plan["spend_usd"])
        stop_usd = float(plan["stop_usd"])
        goal_usd = float(plan["take_usd"])
        take_pct = goal_usd / size * 100.0 if size > 0 else 0.0
        hold_ts = None
        go_big = True
        why = f"PEPE GO BIG — {gb_why}; {plan.get('why')}"

    ok = track(
        pair=pair, entry=entry, size=size, goal_pct=take_pct,
        window_min=(HOLD_BARS * 5 if signal["kind"] == "bounce" else 60.0),
        why=why, source="bounce_live",
        setup={"route": "bounce_live", "kind": signal["kind"]},
        plan_basis="validated 5m closed-bar edge", exchange=exchange,
        go_big=go_big, hard_stop_usd=stop_usd, goal_usd=goal_usd,
        bounce_exit_ts=hold_ts,
    )
    if not ok:
        _log("warning", "BOUNCE LIVE: %s fire refused by the watch writer",
             signal["asset"])
        return None
    _log("info", "BOUNCE LIVE FIRE: %s $%.2f all-in — %s",
         signal["asset"], size, why)
    return {"pair": pair, "entry": entry, "size": size,
            "kind": signal["kind"], "why": why}
