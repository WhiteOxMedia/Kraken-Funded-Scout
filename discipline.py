"""THE DISCIPLINE SCORE (user spec, Sep 4) — "Did I leave money on the
table?"

A per-rotation evaluation comparing:
  - THE ROTATION GOAL (what the system said: the popups that fired, the
    goals they carried, the worth-floor logic that gated them)
  - REALITY (what the coins actually did — the scanner's candles after
    each alert)
  - THE USER (what they took, what they exited, the contest balance)
→ a 0-100 score + the money-left breakdown + one insight, spoken at the
rotation rollover and shown side-by-side with the contest results on
the console's MASTER page.

SELF-LEARNING (continuous + cumulative): every evaluation records its
components and the realized rotation P&L; when the score and the money
disagree (high score + losing rotation, or low score + winning one)
the dominant component's weight is nudged and the lesson is logged —
the score gets sharper over time at telling REAL money-left (a missed
popup that would have paid and been exited) from theoretical.

MONEY LEFT ON THE TABLE — the components:
  MISSED       a popup fired, the coin paid its goal within the window,
               the user never entered → the popup's framed spend x goal
  GIVEBACK     entered, the goal/peak was reached, exited (or valued)
               below it → peak $ - realized $
  MISSED EXIT  the goal was reached, no exit at all (still holding the
               giveback) — the harshest weight
  EARLY EXIT   sold before the goal while the coin paid it afterward →
               the goal's $ - realized $
Deductions scale against the rotation's share of the daily pace; each
component's weight is LEARNED. Good behaviors (took a paying popup,
exited at/within the goal window) earn small bonuses.

APPROXIMATION (v1, refined by learning): "fired popups" are read from
the tournament winners table filtered to the rotation's popup rail —
the table logs champions at send_alert time, including some that later
suppressed at the brain bar; the rail filter approximates the fired
gauntlet."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
STATE_FILE = Path(__file__).parent / "discipline_state.json"

# the learned component weights (bounded; nudged by learn())
DEFAULT_WEIGHTS = {
    "missed": 1.0,        # a paying popup never taken
    "giveback": 0.9,      # reached the goal, exited below the peak
    "missed_exit": 1.25,  # reached the goal, no exit at all
    "early_exit": 0.5,    # sold before the goal, coin paid after
}
W_MIN, W_MAX = 0.25, 2.5
NUDGE = 0.10             # ±10% per learning step
BONUS_MAX = 10.0         # good-behavior bonus cap (points)
ROTATIONS = [("00-04", 0, 4), ("04-08", 4, 8), ("08-12", 8, 12),
             ("12-16", 12, 16), ("16-20", 16, 20), ("20-24", 20, 24)]


def _now():
    return datetime.now(MST)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"weights": dict(DEFAULT_WEIGHTS), "history": [], "lessons": []}


def save_state(state: dict) -> None:
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)


def _rot_window(rot_key: str, date: datetime):
    for key, h0, h1 in ROTATIONS:
        if key == rot_key:
            start = date.replace(hour=h0, minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=4)
            return start, end
    return None, None


def _rotation_goal_pct(pair: str, rot_key: str, sched: str) -> float:
    """The goal a popup in THIS rotation carried: the engine's standard
    expected move for the coin/rotation/schedule, clamped [1, 3]."""
    try:
        from volatility_engine import get_move_threshold
        thr = get_move_threshold(pair, rot_key, sched, level="standard")
        if thr and float(thr) > 0.2:
            return max(1.0, min(3.0, float(thr)))
    except Exception:
        pass
    return 1.5


def _popups_in_rotation(start, end) -> list[dict]:
    """The (approximated) fired popups in the window: the winners table
    filtered to the rotation's popup rail, best per pair."""
    try:
        import db
        rows = db.get_tournament_winners(limit=200) or []
    except Exception:
        return []
    rail = 84.0   # the loosest rotation rail — the approximation floor
    try:
        from background_scanner import ROTATION_POPUP_FLOOR, _schedule_for_dow
        rail = float(min(ROTATION_POPUP_FLOOR.values()))
        sched = _schedule_for_dow(start.weekday())
    except Exception:
        sched = "weekday"
    best: dict[str, dict] = {}
    for r in rows:
        try:
            ts = r.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ZoneInfo("UTC"))
            ts = ts.astimezone(MST)
            if not (start <= ts < end):
                continue
            if float(r.confidence or 0) < rail:
                continue
            if str(r.action or "").lower() != "buy":
                continue
            p = {"pair": r.pair, "confidence": float(r.confidence or 0),
                 "entry": float(r.entry_price or r.price or 0), "ts": ts}
            if p["pair"] not in best or p["confidence"] > best[p["pair"]]["confidence"]:
                best[p["pair"]] = p
        except Exception:
            continue
    return list(best.values())


def _sold_in_rotation(start, end) -> list[dict]:
    """The SOLD trades (realized outcomes) inside the window."""
    out = []
    try:
        p = Path(__file__).parent / "contest_trades.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
                ts = datetime.fromisoformat(t["sold_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=MST)
                ts = ts.astimezone(MST)
                if start <= ts < end:
                    t["_ts"] = ts
                    out.append(t)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _coin_paid(exchange, pair: str, entry: float, goal_pct: float,
               alert: datetime, window_end: datetime) -> bool:
    """Did the coin reach entry x (1 + goal) inside the window? (the
    scanner's real data — 1h candles; fail-closed: unreadable = no.)"""
    try:
        since = int(alert.timestamp() * 1000) - 3_600_000
        ohl = exchange.fetch_ohlcv(pair, timeframe="1h", since=since, limit=10)
        for c in (ohl or []):
            ct = datetime.fromtimestamp(c[0] / 1000, tz=MST)
            if alert <= ct <= window_end and c[2]:
                if float(c[2]) >= entry * (1.0 + goal_pct / 100.0):
                    return True
        return False
    except Exception:
        return False


def evaluate_rotation(exchange, rot_key: str, when=None, speak=True) -> dict:
    """The full per-rotation evaluation. Returns the structured result
    (also logged + spoken). Safe to call at the rollover."""
    when = when or _now()
    date = when - timedelta(hours=4) if rot_key == "20-24" and when.hour < 4 else when
    start, end = _rot_window(rot_key, date)
    if start is None:
        return {"score": None, "error": f"bad rotation {rot_key}"}
    state = load_state()
    weights = state.get("weights") or dict(DEFAULT_WEIGHTS)

    try:
        import contest_account as ca
        acct = ca.load_state()
        pace = ca.daily_pace_usd(acct)
        balance = float(acct.get("current_balance") or 0)
    except Exception:
        pace, balance = 240.0, 10000.0
    pace_share = max(pace / 5.0, 1.0)   # the rotation's share of the day

    popups = _popups_in_rotation(start, end)
    sold = _sold_in_rotation(start, end)
    sold_pairs = {t["pair"] for t in sold}

    # ── the components ───────────────────────────────────────────────
    missed, giveback, missed_exit, early_exit = [], [], [], []
    bonuses = 0.0
    for p in popups:
        goal = _rotation_goal_pct(p["pair"], rot_key, "weekend"
                                  if start.weekday() >= 5 else "weekday")
        p["goal_pct"] = goal
        taken = p["pair"] in sold_pairs
        if not taken:
            # still held from this window? the watch's added timestamp
            try:
                wdoc = json.loads((Path(__file__).parent / "contest_watch.json").read_text())
                wr = wdoc.get(p["pair"]) or {}
                ad = datetime.fromisoformat(wr["added"]) if wr.get("added") else None
                if ad is not None:
                    if ad.tzinfo is None:
                        ad = ad.replace(tzinfo=MST)
                    taken = start <= ad.astimezone(MST) < end
            except Exception:
                pass
        paid = _coin_paid(exchange, p["pair"], p["entry"] or 1.0, goal, p["ts"], end)
        p["paid"] = paid
        p["taken"] = taken
        if paid and not taken:
            p["usd"] = balance * goal / 100.0
            missed.append(p)
        elif paid and taken:
            bonuses += 2.0    # took a popup that paid — discipline
    for t in sold:
        entry, exitp = float(t.get("entry") or 0), float(t.get("exit") or 0)
        size, realized = float(t.get("size") or 0), float(t.get("pnl_net") or 0)
        goal = float(t.get("goal_pct") or 1.5)
        if not entry or not size:
            continue
        peak_pct = (float(t.get("high") or exitp) - entry) / entry * 100 if t.get("high") else \
            (exitp - entry) / entry * 100
        goal_usd = size * goal / 100.0
        if realized >= goal_usd * 0.9:
            bonuses += 2.0    # exited at the goal — the discipline shape
        elif realized < goal_usd and peak_pct >= goal * 0.9:
            # reached (nearly) the goal but exited below it
            left = max(0.0, size * peak_pct / 100.0 - max(realized, 0.0))
            if left > 0:
                t["_left"] = left
                giveback.append(t)
        elif realized < 0 and peak_pct >= goal * 0.9:
            t["_left"] = size * peak_pct / 100.0 - realized
            missed_exit.append(t)

    # still-held: the goal reached with no exit at all (the harshest)
    try:
        wdoc = json.loads((Path(__file__).parent / "contest_watch.json").read_text())
        for pair, wr in wdoc.items():
            try:
                ad = datetime.fromisoformat(wr.get("added") or "")
                if ad.tzinfo is None:
                    ad = ad.replace(tzinfo=MST)
                ad = ad.astimezone(MST)
                if not (start <= ad < end):
                    continue
                entry, size = float(wr.get("entry") or 0), float(wr.get("size") or 0)
                high = float(wr.get("high") or entry)
                goal = float(wr.get("goal_pct") or 1.5)
                if not entry or not size:
                    continue
                peak_usd = size * (high - entry) / entry * 100 / 100 * 100
                peak_usd = size * ((high / entry) - 1.0)
                if (high / entry - 1.0) * 100 >= goal:
                    live = None
                    try:
                        live = float(exchange.fetch_ticker(pair)["last"])
                    except Exception:
                        pass
                    cur_usd = size * ((live / entry) - 1.0) if live else 0.0
                    left = max(0.0, peak_usd - max(cur_usd, 0.0))
                    if left > 0:
                        missed_exit.append({"pair": pair, "_left": left,
                                            "held": True, "goal_pct": goal})
            except Exception:
                continue
    except Exception:
        pass

    usd = {"missed": sum(p.get("usd", 0) for p in missed),
           "giveback": sum(t.get("_left", 0) for t in giveback),
           "missed_exit": sum(t.get("_left", 0) for t in missed_exit),
           "early_exit": sum(t.get("_left", 0) for t in early_exit)}

    # ── the score ────────────────────────────────────────────────────
    deductions = sum(
        min(50.0, 50.0 * (usd[k] / pace_share) * weights.get(k, 1.0))
        for k in usd)
    score = max(0.0, min(100.0, 100.0 - deductions + min(bonuses, BONUS_MAX)))
    realized_pnl = sum(float(t.get("pnl_net") or 0) for t in sold)

    result = {
        "date": start.strftime("%Y-%m-%d"), "rot": rot_key,
        "score": round(score, 1), "pace_share": round(pace_share, 2),
        "usd_left": {k: round(v, 2) for k, v in usd.items()},
        "total_left": round(sum(usd.values()), 2),
        "popups": [{"pair": p["pair"], "paid": p.get("paid"),
                    "taken": p.get("taken"), "goal_pct": round(p.get("goal_pct", 0), 2)}
                   for p in popups],
        "realized_pnl": round(realized_pnl, 2),
        "balance_after": balance,
        "bonuses": bonuses, "weights": dict(weights),
        "ts": _now().isoformat(),
    }

    # ── self-learning: when the score and the money disagree, adjust ──
    lesson = _learn(state, result)
    result["lesson"] = lesson
    _entry = {k: result[k] for k in
              ("date", "rot", "score", "total_left", "realized_pnl",
               "balance_after", "ts")}
    # one entry per (date, rot) — re-evaluations replace (the plate shows
    # the latest; the history stays clean)
    state["history"] = [h for h in state["history"]
                        if not (h.get("date") == _entry["date"]
                                and h.get("rot") == _entry["rot"])]
    state["history"].append(_entry)
    state["history"] = state["history"][-60:]
    save_state(state)

    if speak:
        _speak_result(result, missed, missed_exit)
    return result


def _learn(state: dict, result: dict) -> str:
    """One learning step: compare the score with the realized money and
    nudge the dominant weight when they disagree. Returns the lesson."""
    w = state.setdefault("weights", dict(DEFAULT_WEIGHTS))
    score, pnl = result["score"], result["realized_pnl"]
    usd = result["usd_left"]
    lesson = ""
    dominant = max(usd, key=usd.get) if any(usd.values()) else None
    if score >= 80 and pnl < 0 and dominant:
        # high score but the money went backwards — the score is
        # over-crediting; make the dominant component sting more
        old = w.get(dominant, 1.0)
        w[dominant] = round(min(W_MAX, old * (1.0 + NUDGE)), 3)
        lesson = (f"Score {score:.0f} but the rotation lost ${abs(pnl):.0f} — "
                  f"the '{dominant}' weight rises {old:.2f} → {w[dominant]:.2f}: "
                  f"next time this pattern counts harder.")
    elif score <= 40 and pnl > result["pace_share"] and dominant:
        # low score but the money came — over-penalizing (the 'missed'
        # winners were probably right to skip); soften the dominant weight
        old = w.get(dominant, 1.0)
        w[dominant] = round(max(W_MIN, old * (1.0 - NUDGE)), 3)
        lesson = (f"Score {score:.0f} but the rotation MADE ${pnl:.0f} — "
                  f"the '{dominant}' weight eases {old:.2f} → {w[dominant]:.2f}: "
                  f"some 'left on the table' was right to leave.")
    else:
        lesson = (f"Score and money agree (score {score:.0f}, realized "
                  f"${pnl:+.0f}) — weights hold; the calibration stands.")
    state.setdefault("lessons", []).append(
        {"ts": result["ts"], "lesson": lesson})
    state["lessons"] = state["lessons"][-30:]
    return lesson


def _speak_result(result: dict, missed: list, missed_exit: list):
    """The dopamine delivery — the one voice, at the rollover."""
    try:
        import background_scanner as bs
        s = result["score"]
        grade = ("SNIPER!" if s >= 85 else "SOLID!" if s >= 65 else
                 "CAREFUL!" if s >= 40 else "UNDISCIPLINED!")
        line = f"Rotation closed! Discipline score: {s:.0f}! {grade} "
        if result["total_left"] > 1:
            line += (f"You left ${result['total_left']:,.0f} on the table. ")
            if missed:
                line += (f"{missed[0]['pair'].split('/')[0]} paid without you! ")
            if missed_exit:
                line += "And a goal was reached with no exit! "
        else:
            line += "Nothing left on the table! "
        line += _insight(result, missed, missed_exit)
        bs._speak_server(line, rate=1)
    except Exception:
        pass


def _insight(result: dict, missed: list, missed_exit: list) -> str:
    """ONE actionable suggestion — the sharpest lever this rotation."""
    usd = result["usd_left"]
    if usd.get("missed_exit", 0) >= max(usd.values()):
        return ("Next time: when the goal hits, tap SOLD — the exit scan "
                "already told you when.")
    if usd.get("missed", 0) >= max(usd.values()):
        return ("Next time: a paying popup is the system's best call — "
                "take the green light when it fires.")
    if usd.get("giveback", 0) >= max(usd.values()):
        return ("Next time: bank at the goal — the trail protects the "
                "rest, but the goal is yours to take.")
    return ("Keep the rotation rhythm: goal, window, exit — the machine "
            "keeps the clock, you keep the taps.")
