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
  MISSED       a popup FIRED, the coin paid its goal within the window,
               the user never entered → the popup's framed spend x goal
  GIVEBACK     entered, the goal/peak was reached, exited (or valued)
               below it → peak $ - realized $
  MISSED EXIT  the goal was reached, no exit at all (still holding the
               giveback) — the harshest weight
  EARLY EXIT   sold before the goal while the coin paid it afterward →
               the goal's $ - realized $
  SYSTEM       THE SEAM (user rule, Sep 4: "sewing the suppression log
  BURIAL       to the discipline score so it learns from it"): a paying
               candidate the FUNNEL buried (the suppression log's gate)
               → balance x goal, THEORETICAL — no popup ever reached the
               user, so it's the machine's leave, not theirs. Its own
               learned weight; the plate attributes it to the gate; the
               insight names the gate when it dominates. The weight
               LEARNS whether the gates are calibrated: buried payers
               pile up → it stings; buried candidates mostly don't pay →
               it eases (the gates were right).
Deductions scale against the rotation's share of the daily pace; each
component's weight is LEARNED. Good behaviors (took a paying popup,
exited at/within the goal window) earn small bonuses.

ATTRIBUTION (v2, the POPCAT-correction class): "fired" is now grounded —
a winners-table crown that appears in the suppression log (and was never
spoken) is a BURIAL, not a user miss; a spoken "Buy {asset}!" line
(voice_log) confirms a fire even if a earlier burial attempt was logged
(suppressed at 09:00, the tide turned, fired at 10:30 → fired). The
winners-table approximation only remains for candidates with neither
record."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
STATE_FILE = Path(__file__).parent / "discipline_state.json"
SUPPRESSION_FILE = Path(__file__).parent / "popup_suppressions.jsonl"
VOICE_FILE = Path(__file__).parent / "voice_log.jsonl"

# the learned component weights (bounded; nudged by learn())
DEFAULT_WEIGHTS = {
    "missed": 1.0,        # a paying popup never taken
    "giveback": 0.9,      # reached the goal, exited below the peak
    "missed_exit": 1.25,  # reached the goal, no exit at all
    "early_exit": 0.5,    # sold before the goal, coin paid after
    "system_burial": 1.0, # a paying candidate the funnel buried (the seam)
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
    """The candidate popups in the window: the winners table filtered to
    the rotation's popup rail, best per pair (v2: candidates — the
    fired/buried attribution happens in evaluate_rotation via the
    suppression log + the voice record)."""
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


def _suppressions_in_rotation(start, end, log_file=None) -> dict[str, dict]:
    """THE SEAM'S record: the funnel's burials in the window, deduped per
    pair (POPCAT's 12 crowns were ONE opportunity) — the LAST entry per
    pair is the verdict that held. Returns {pair: {gate, ts, quick,
    brain, n}}. The mover path's pairs (outside the 54) are included:
    a buried mover popup is money left too."""
    f = Path(log_file) if log_file else SUPPRESSION_FILE
    out: dict[str, dict] = {}
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = datetime.fromisoformat(str(d.get("ts", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=MST)
                ts = ts.astimezone(MST)
                if not (start <= ts < end):
                    continue
                pair = str(d.get("pair") or "")
                if not pair:
                    continue
                prev = out.get(pair) or {"n": 0}
                out[pair] = {"gate": str(d.get("gate") or "?"), "ts": ts,
                             "quick": d.get("quick"), "brain": d.get("brain"),
                             "n": prev.get("n", 0) + 1}
            except Exception:
                continue
    except Exception:
        pass
    return out


def _fired_pairs(start, end, voice_file=None) -> set[str]:
    """The pairs a popup actually FIRED for in the window — the one mouth
    speaks every champion/mover alert ("Buy {asset}! ..."). This is the
    ground truth that separates a user miss (fired, never taken) from a
    system burial (suppressed, never surfaced) — and rescues the
    suppressed-then-fired case (buried at 09:00, the tide turned, fired
    at 10:30 → FIRED)."""
    f = Path(voice_file) if voice_file else VOICE_FILE
    fired: set[str] = set()
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = datetime.fromisoformat(str(d.get("ts", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=MST)
                ts = ts.astimezone(MST)
                if not (start <= ts < end):
                    continue
                text = str(d.get("text") or "")
                if text.startswith("Buy ") and "!" in text:
                    asset = text[4:].split("!")[0].strip().upper()
                    if asset and len(asset) <= 12:
                        fired.add(f"{asset}/USD")
            except Exception:
                continue
    except Exception:
        pass
    return fired


def _entry_at(exchange, pair: str, when: datetime) -> float:
    """The price when the funnel buried it (the 1h candle close at the
    suppression timestamp — the entry approximation for the candle test)."""
    try:
        since = int(when.timestamp() * 1000) - 3_600_000
        ohl = exchange.fetch_ohlcv(pair, timeframe="1h", since=since, limit=3)
        for c in (ohl or []):
            ct = datetime.fromtimestamp(c[0] / 1000, tz=MST)
            if ct <= when <= ct + timedelta(hours=1):
                return float(c[4])
        if ohl:
            return float(ohl[-1][4])
    except Exception:
        pass
    return 0.0


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

    # ── THE SEAM (user rule, Sep 4: "sewing the suppression log to the
    #    discipline score so it learns from it") — the attribution: a
    #    paying candidate the funnel BURIED is the machine's leave, not
    #    the user's. Fired = spoken (voice log) or never suppressed; a
    #    spoken line rescues the suppressed-then-fired case.
    supp = _suppressions_in_rotation(start, end)
    fired = _fired_pairs(start, end)

    # ── the components ───────────────────────────────────────────────
    missed, giveback, missed_exit, early_exit = [], [], [], []
    system_burial = []
    bonuses = 0.0
    sched = "weekend" if start.weekday() >= 5 else "weekday"
    for p in popups:
        goal = _rotation_goal_pct(p["pair"], rot_key, sched)
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
        buried = (p["pair"] in supp and p["pair"] not in fired)
        if paid and not taken:
            p["usd"] = balance * goal / 100.0
            if buried:
                p["gate"] = supp[p["pair"]]["gate"]
                system_burial.append(p)
            else:
                missed.append(p)
        elif paid and taken:
            bonuses += 2.0    # took a popup that paid — discipline

    # suppressed pairs the winners table never saw (the mover path) —
    # the same candle test; only pairs that never fired count as burials
    popup_pairs = {p["pair"] for p in popups}
    for pair, s in supp.items():
        if pair in popup_pairs or pair in fired:
            continue
        goal = _rotation_goal_pct(pair, rot_key, sched)
        entry = _entry_at(exchange, pair, s["ts"])
        if entry > 0 and _coin_paid(exchange, pair, entry, goal, s["ts"], end):
            system_burial.append({"pair": pair, "gate": s["gate"],
                                  "usd": balance * goal / 100.0,
                                  "goal_pct": goal, "paid": True,
                                  "taken": False})
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
           "early_exit": sum(t.get("_left", 0) for t in early_exit),
           "system_burial": sum(p.get("usd", 0) for p in system_burial)}

    # the gate that buried the most theoretical money (the insight names it)
    _gate_usd: dict[str, float] = {}
    for p in system_burial:
        _gate_usd[p.get("gate") or "?"] = _gate_usd.get(p.get("gate") or "?", 0) + float(p.get("usd", 0))
    top_gate = max(_gate_usd, key=_gate_usd.get) if _gate_usd else None

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
        "system_left": round(usd["system_burial"], 2),
        "top_gate": top_gate,
        "system_pairs": [{"pair": p["pair"], "gate": p.get("gate"),
                          "usd": round(float(p.get("usd", 0)), 2)}
                         for p in system_burial],
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
              ("date", "rot", "score", "total_left", "system_left",
               "top_gate", "realized_pnl", "balance_after", "ts")}
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
    nudge the dominant weight when they disagree. Returns the lesson.
    (The seam: when SYSTEM BURIAL dominates, the lesson names the gate —
    the machine's leave is calibration data, not user blame.)"""
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
        if dominant == "system_burial":
            lesson = (f"Score {score:.0f} but the rotation lost ${abs(pnl):.0f} — and the "
                      f"'{result.get('top_gate') or '?'}' gate buried paying candidates "
                      f"(theoretical ${usd.get('system_burial', 0):.0f}). The system_burial "
                      f"weight rises {old:.2f} → {w[dominant]:.2f}: the gate keeps costing — "
                      f"the carve-out data builds.")
        else:
            lesson = (f"Score {score:.0f} but the rotation lost ${abs(pnl):.0f} — "
                      f"the '{dominant}' weight rises {old:.2f} → {w[dominant]:.2f}: "
                      f"next time this pattern counts harder.")
    elif score <= 40 and pnl > result["pace_share"] and dominant:
        # low score but the money came — over-penalizing (the 'missed'
        # winners were probably right to skip); soften the dominant weight
        old = w.get(dominant, 1.0)
        w[dominant] = round(max(W_MIN, old * (1.0 - NUDGE)), 3)
        if dominant == "system_burial":
            lesson = (f"Score {score:.0f} but the rotation MADE ${pnl:.0f} — the buried "
                      f"candidates mostly didn't pay: the gates were RIGHT to bury them. "
                      f"The system_burial weight eases {old:.2f} → {w[dominant]:.2f}.")
        else:
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
            if result.get("system_left", 0) > 1:
                line += (f"The funnel buried ${result['system_left']:,.0f} theoretical — "
                         f"that's on the machine, not you! ")
        else:
            line += "Nothing left on the table! "
        line += _insight(result, missed, missed_exit)
        bs._speak_server(line, rate=1)
    except Exception:
        pass


def _insight(result: dict, missed: list, missed_exit: list) -> str:
    """ONE actionable suggestion — the sharpest lever this rotation."""
    usd = result["usd_left"]
    if (usd.get("system_burial", 0) > 1
            and usd.get("system_burial", 0) >= max(usd.values())):
        gate = result.get("top_gate") or "?"
        asset = "?"
        try:
            asset = (result.get("system_pairs") or [{}])[0].get("pair", "?").split("/")[0]
        except Exception:
            pass
        return (f"The {gate} gate buried {asset} (+${usd['system_burial']:,.0f} "
                f"theoretical) — carve-out data, not a discipline miss. "
                f"Your taps were clean.")
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
