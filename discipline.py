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
# ── THE OPPORTUNITY COST (user rule, Sep 7: "the Discipline score gave
# itself a 100 because there were 0 popups — 0 popups does not help the
# goal; the goal is the BEST popups at the RIGHT time, maximizing
# profits"): a rotation with no FIRED popup is not a perfect rotation —
# it's a rotation where the funnel surfaced nothing worth taking, so
# "did I leave money on the table?" is UNANSWERABLE, not "yes". The
# empty-fire score is capped at NEUTRAL_CAP and the plate/voice say
# why. A healthy system FIRES: 0 fires means the gates are strangling
# the funnel (or the tape was dead) — either way the score must not
# read as discipline perfection, or the self-learning weights drift
# toward "suppress everything" (the exact wrong lesson).
EMPTY_FIRE_CAP = 60.0     # a zero-fire rotation tops out here (CAREFUL band)
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


# ── THE JOURNAL THE GATES OPEN (Sep 10: the seam sewn BOTH sides) ─────
# Until now the discipline seam was one-way: the suppression log fed the
# score, the score spoke, and the gates never read a word back. These two
# functions close the loop:
#   record_burial_ledger() — per rotation, per gate: how many paying
#       candidates each gate buried and the theoretical dollars. This is
#       the JOURNAL — hindsight, written where the gates can open it.
#   gate_relief() — the gates' side: a bounded, recency-bound relief in
#       popup-floor points, computed ONLY from buried payers that never
#       fired. Buried duds ease the relief back down; a silent journal
#       (a gate behaving) gives nothing. Fail-closed everywhere.
SEAM_RELIEF_MAX_PTS = 4.0    # hard cap: never wider than 4 popup-floor pts
SEAM_RELIEF_DECAY_H = 8.0    # relief fades to zero 8h after the last lesson
SEAM_RELEASE_SLACK = 5.0     # the journal release: a candidate within 5 pts
                             # of the gate's bar is a near-miss it may re-see


def record_burial_ledger(state: dict, result: dict) -> None:
    """Append this rotation's per-gate burial record to the journal
    (state[burial_ledger], kept 30 rotations). One entry per (date, rot)
    — re-evaluations replace. Mutates the caller's state in place (the
    evaluation's own save persists it — no second load/save, no clobber)."""
    try:
        entry = {"date": result.get("date"), "rot": result.get("rot"),
                 "ts": result.get("ts"),
                 "gates": {str(p.get("gate") or "?"): 
                           {"n": 0, "usd": 0.0}
                           for p in (result.get("system_pairs") or [])},
                 "duds": {}}
        for p in (result.get("system_pairs") or []):
            g = str(p.get("gate") or "?")
            entry["gates"][g]["n"] += 1
            entry["gates"][g]["usd"] += float(p.get("usd") or 0)
        for p in (result.get("system_duds") or []):
            g = str(p.get("gate") or "?")
            entry.setdefault("duds", {}).setdefault(g, 0)
            entry["duds"][g] += 1
        led = [h for h in (state.get("burial_ledger") or [])
               if not (h.get("date") == entry["date"]
                       and h.get("rot") == entry["rot"])]
        led.append(entry)
        state["burial_ledger"] = led[-30:]
    except Exception:
        pass   # the journal never breaks the score


def gate_relief(gate: str, now=None) -> float:
    """Relief in popup-floor points for ONE gate, from the journal: 1 pt
    per buried payer that rotation (the learned system_burial weight
    scales it), capped at SEAM_RELIEF_MAX_PTS, decaying linearly to zero
    over SEAM_RELIEF_DECAY_H since the last burial entry. A gate that
    buried only duds gets nothing — the dud entries drive the relief
    DOWN (each buried-and-not-paid candidate claws back 0.5 pts)."""
    try:
        st = load_state()
        led = st.get("burial_ledger") or []
        if not led:
            return 0.0
        now = now or _now()
        last_ts = None
        pts = 0.0
        for h in led[-5:]:   # recent rotations only
            g = (h.get("gates") or {}).get(gate)
            if not g:
                continue
            pts += float(g.get("n") or 0) * float(st.get("weights", {}).get("system_burial", 1.0))
            dud_n = float((h.get("duds") or {}).get(gate) or 0)
            pts -= 0.5 * dud_n   # buried duds claw the relief back
            ts = h.get("ts")
            if ts:
                last_ts = ts if last_ts is None else max(last_ts, ts)
        if pts <= 0:
            return 0.0
        if last_ts:
            age_h = (now - datetime.fromisoformat(str(last_ts))).total_seconds() / 3600.0
            if age_h < 0:
                age_h = 0.0
            pts *= max(0.0, 1.0 - age_h / SEAM_RELIEF_DECAY_H)
        return round(min(SEAM_RELIEF_MAX_PTS, max(pts, 0.0)), 2)
    except Exception:
        return 0.0   # fail-closed: a broken journal opens nothing


def journal_release(gate: str, quick: float, floor: float, now=None) -> bool:
    """THE JOURNAL RELEASE (Sep 21, the verdict-cost study: deep-veto was
    the single largest mispriced pool — 86 burials, 86 high-quality, 24
    of them ran +3% after). When a gate buries champion-grade candidates
    that go on to pay, the journal grants ONE release: the candidate's
    quick score must sit within SEAM_RELEASE_SLACK points of the gate's
    bar (deep-veto's soft edge is HOLD-vs-SELL — SELL always stands).
    Same caps as every relief: the journal's total for the gate (buried
    payers minus duds, the learned burial weight) capped at
    SEAM_RELIEF_MAX_PTS, decaying to zero over SEAM_RELIEF_DECAY_H —
    a mispriced funnel eases, a behaving funnel gives nothing.
    gate_relief decides from the journal's own hindsight; this reads
    the LIVE candidate — fail-closed everywhere (a broken journal
    releases nothing)."""
    try:
        # convert once, reject non-finite (CodeRabbit, Sep 21): a NaN
        # quick slips every comparison (all False) and would be RELEASED
        # whenever the journal has relief — a broken score must close,
        # never open
        import math as _math
        _q, _f = float(quick), float(floor)
        if not (_math.isfinite(_q) and _math.isfinite(_f)):
            return False
        if _q + SEAM_RELEASE_SLACK < _f:
            return False
        return journal_release_pts(gate, now) > 0.0
    except Exception:
        return False


def journal_release_pts(gate: str, now=None) -> float:
    """The relief number the release rides — gate_relief's exact math.
    Kept separate so the gates can show the points in their log line
    while journal_release() stays a boolean decision."""
    return gate_relief(gate, now=now)


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
    # (pairs it buried that DIDN'T pay are the gate being RIGHT — they
    # become system_duds and claw the relief back down)
    popup_pairs = {p["pair"] for p in popups}
    system_duds = []
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
        else:
            system_duds.append({"pair": pair, "gate": s["gate"]})
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
    # THE OPPORTUNITY COST (Sep 7): zero FIRED popups caps the score —
    # a rotation where nothing was surfaced cannot grade "perfect".
    # The candidates may have existed and not paid (honest no-trade) or
    # been strangled by the gates — the capped score + the empty-fire
    # flag says which world, and the learning step treats a capped
    # score + losing money as the gates' fault (system_burial rises).
    empty_fire = not fired
    if empty_fire and score > EMPTY_FIRE_CAP:
        score = EMPTY_FIRE_CAP
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
        "system_duds": [{"pair": p["pair"], "gate": p.get("gate")}
                        for p in system_duds],
        "popups": [{"pair": p["pair"], "paid": p.get("paid"),
                    "taken": p.get("taken"), "goal_pct": round(p.get("goal_pct", 0), 2)}
                   for p in popups],
        "realized_pnl": round(realized_pnl, 2),
        "balance_after": balance,
        "empty_fire": empty_fire,
        "bonuses": bonuses, "weights": dict(weights),
        "ts": _now().isoformat(),
    }

    # ── self-learning: when the score and the money disagree, adjust ──
    record_burial_ledger(state, result)   # the seam's ledger: WHICH gate buried, how much
    lesson = _learn(state, result)
    result["lesson"] = lesson
    _entry = {k: result[k] for k in
              ("date", "rot", "score", "total_left", "system_left",
               "top_gate", "realized_pnl", "balance_after", "ts",
               "system_pairs", "system_duds")}   # the per-pair burials
    # persist too (Sep 21): the cooldown review's per-asset layer and the
    # console's review plate read them — a burial with no pair named is
    # money the review can never attribute
    _entry["usd_left"] = dict(result["usd_left"])   # the component map:
    # the cooldown review aggregates the DAY from these rows (Sep 21)
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
    # ── THE BURIAL PILE-UP (Sep 10: the seam sewn both sides): when the
    # funnel buried TWO OR MORE paying candidates in one rotation, that is
    # calibration evidence on its own — no realized-money disagreement is
    # needed (a no-trade rotation with $118 buried must still sting, or
    # the weight never moves and the gates never learn).
    # Balanced by the duds: buried-and-not-paid candidates (the gate was
    # RIGHT) shrink the pile-up before it counts — net (payers − duds)
    # must be ≥ 2.
    _net = (len(result.get("system_pairs") or [])
            - len(result.get("system_duds") or []))
    if usd.get("system_burial", 0) > 1 and _net >= 2:
        old = w.get("system_burial", 1.0)
        w["system_burial"] = round(min(W_MAX, old * (1.0 + NUDGE)), 3)
        _gates = sorted({str(p.get("gate") or "?") for p in result["system_pairs"]})
        lesson = (f"The funnel buried {len(result['system_pairs'])} paying candidates "
                  f"(${usd['system_burial']:,.0f} theoretical; gates: {', '.join(_gates)}) — "
                  f"the system_burial weight rises {old:.2f} → {w['system_burial']:.2f}: "
                  f"the journal opens the gates a little where they keep burying payers.")
    elif result.get("empty_fire") and pnl < 0:
        # THE OPPORTUNITY COST (Sep 7): nothing fired AND the rotation
        # lost money — the funnel surfaced nothing while the account
        # bled. That is the gates' leave, whatever the suppression log
        # says: the system_burial weight rises (the carve-out data
        # builds) and the lesson names the silence.
        old = w.get("system_burial", 1.0)
        w["system_burial"] = round(min(W_MAX, old * (1.0 + NUDGE)), 3)
        lesson = (f"No popups fired and the rotation lost ${abs(pnl):.0f} — "
                  f"the funnel surfaced nothing worth taking while the "
                  f"account bled. The system_burial weight rises "
                  f"{old:.2f} → {w['system_burial']:.2f}: a silent funnel "
                  f"is not a disciplined one.")
    elif score >= 80 and pnl < 0 and dominant:
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
    elif result.get("empty_fire"):
        lesson = (f"No popups fired — score capped at {score:.0f} (the "
                  f"opportunity cost: a silent rotation cannot grade "
                  f"perfect). Realized ${pnl:+.0f}; weights hold.")
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
        if result.get("empty_fire"):
            line = (f"Rotation closed! No popups fired — score {s:.0f}, "
                    f"capped: a silent rotation can't grade perfect. ")
        else:
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
            line += ("Nothing left on the table — but nothing was offered "
                     "either! " if result.get("empty_fire")
                     else "Nothing left on the table! ")
        line += _insight(result, missed, missed_exit)
        bs._speak_server(line, rate=1)
    except Exception:
        pass


def cooldown_review(exchange=None, when=None, speak=True, dry_run=False) -> dict:
    """THE COOLDOWN REVIEW (user spec, Sep 21) — "What did I learn today,
    and how do I increase the PnL when this setup appears again?"

    The day-level layer on the SAME rails as the per-rotation score:

      1. money left on the table   sum of today's rotation results'
                                   usd_left components (missed / giveback
                                   / missed_exit / system_burial)
      2. discipline score          today's rotations, averaged
      3. liquidity conditions      candle tests that returned no data
                                   (the honest unknown) + tide at close
      4. vs the daily goal         realized PnL vs the tier's daily pace
      5. adjustments ACTUALLY made the day's learned weight deltas — the
                                   calibration that really happened
      6. suggestions               the top gate's name + the SAME
                                   _insight lever the rotation score uses

    Self-learning: one extra _learn step on the day's AGGREGATE (same
    weight-nudge machinery as the rotation score), so the brain log can
    learn from the day the way it learns from a rotation. The lesson
    rides the state's lessons log (the same one the console reads).

    dry_run: skip the state write and the voice — for tests.
    Fail-soft everywhere: a missing rotation result is a skipped fact,
    never an exception; the review speaks only what it can prove."""
    when = when or _now()
    _today = when.strftime("%Y-%m-%d")
    state = load_state()

    # ── 1. the day's rotation results (already learned at each rollover)
    _today_rots = [h for h in (state.get("history") or [])
                   if h.get("date") == _today
                   and h.get("rot") != "DAY"]   # the day's own row is
    # the OUTPUT, never an input (a re-evaluation must not aggregate
    # itself — the live 20:08 run exposed exactly that)
    _sched = "weekend" if when.weekday() >= 5 else "weekday"
    _sched = "both" if _rot_is_split(when) else _sched

    # ── 2. aggregate the money-left components across today's rotations
    _agg = {"missed": 0.0, "giveback": 0.0, "missed_exit": 0.0,
            "early_exit": 0.0, "system_burial": 0.0}
    _other_left = 0.0   # legacy rows: a total without component detail
    for h in _today_rots:
        _ul = h.get("usd_left")
        if isinstance(_ul, dict) and _ul:
            for _k in _agg:
                _agg[_k] += float(_ul.get(_k) or 0.0)
            continue
        # rows written before the component map was persisted: the
        # system share is stored; the rest is real money-left whose
        # split is honestly unknown (reported, not attributed)
        _tl = float(h.get("total_left") or 0.0)
        _sl = float(h.get("system_left") or 0.0)
        _agg["system_burial"] += _sl
        _other_left += max(0.0, _tl - _sl)
    agg = _agg
    total_left = round(sum(agg.values()) + _other_left, 2)
    rot_scores = [float(h["score"]) for h in _today_rots
                  if h.get("score") is not None]
    avg_score = (round(sum(rot_scores) / len(rot_scores), 1)
                 if rot_scores else None)

    # ── 3. realized vs the daily goal
    try:
        import contest_account as _ca
        _acct = _ca.load_state()
        _pace = float(_ca.daily_pace_usd(_acct))
        _bal = float(_acct.get("current_balance") or 0.0)
    except Exception:
        _pace, _bal = 240.0, 10000.0
    _day_rows = []
    try:
        _p = Path(__file__).parent / "contest_trades.jsonl"
        for _line in _p.read_text(encoding="utf-8").splitlines():
            if not _line.strip():
                continue
            _t = json.loads(_line)
            _ts = datetime.fromisoformat(str(_t.get("sold_at", "")))
            if _ts.tzinfo is None:
                _ts = _ts.replace(tzinfo=MST)
            if _ts.astimezone(MST).strftime("%Y-%m-%d") == _today \
                    and _t.get("applied"):
                _day_rows.append(_t)
    except Exception:
        pass
    _realized = round(sum(float(_t.get("pnl_net") or 0)
                          for _t in _day_rows), 2)
    _n_trades = len(_day_rows)

    # ── 4. liquidity conditions — the honest unknown. The candle tests
    # are live-API; at review time they are history. What the LEDGER
    # proves: today's system burials where no candle data existed are
    # not graded; the tide at close IS provable without the API.
    try:
        import background_scanner as _bs
        _tide_pct, _tide_state = _bs._fleet_tide()
        _tide_txt = f"{_tide_state.lower()} ({_tide_pct:.0f}% of the fleet up)"
    except Exception:
        _tide_txt = None
    _no_candle = 0   # burials whose candle test could not run — not graded

    # ── 5. the adjustments ACTUALLY made today: the weight deltas from
    # the day's rotation lessons (the real calibration, not a wish)
    _deltas = _day_weight_deltas(state, _today)

    # ── 6b. THE PER-ASSET REVIEW (user spec, Sep 21: "review all the
    # assets and how they actually performed ... did we buy the top,
    # exit too soon or stay too long, what happened to the money left
    # on the table?"): every asset the day touched — sold, still held,
    # and what the funnel buried — each with one factual verdict using
    # the SAME component arithmetic the rotation score uses.
    assets = _day_assets(exchange, _today, state)

    # ── 6c. THE BOOK-WIDE VERDICT COST (user, Sep 21): the study's
    # ranking rides the nightly line — the top verdict class across ALL
    # closed trades, not just today's, so the biggest leak is named
    # every night and the DAY row carries its history.
    _topv = _top_verdict_class()

    # ── 6. the aggregate learning step — the day itself is one lesson on
    # the SAME weights the rotation score nudges. The brain log learns
    # from the day in the same way.
    _day_result = {
        "date": _today, "rot": "DAY", "score": avg_score,
        "pace_share": max(_pace, 1.0),
        "usd_left": {k: round(v, 2) for k, v in agg.items()},
        "total_left": total_left,
        "system_left": round(agg.get("system_burial", 0.0), 2),
        "top_gate": _top_gate_of_day(state, _today),
        "system_pairs": _day_system_pairs(state, _today),
    "system_duds": _day_system_duds(state, _today),
        "popups": [],
        "assets": assets,
        "realized_pnl": _realized,
        "balance_after": _bal,
        "empty_fire": not _day_fired_any(_today),
        "bonuses": 0.0, "weights": dict(state.get("weights") or {}),
        "ts": _now().isoformat(),
    }
    # a day with no rotation results has NO score — the learning step
    # needs a number to disagree with; without one, the review records
    # facts and holds the weights (a verdict it cannot supply is not a
    # nudge)
    if _day_result["score"] is not None:
        lesson = _learn(state, _day_result)
    else:
        lesson = ("No rotation evaluations for today — the review "
                  "records the facts; weights hold until there is a "
                  "score to learn from.")
    _day_result["lesson"] = lesson
    # the asset-level story rides the day lesson — the brain log learns
    # the sharpest asset fact the same way it learns the aggregates
    _alead = _asset_lead(assets)
    if _alead:
        lesson = f"{lesson} {_alead}"
        _day_result["lesson"] = lesson
    _day_result["top_verdict"] = _topv

    # ── 7. persist: the day entry rides the history (one per day) and
    # the lesson rides the lessons log — the console's brain-log readers
    # pick it up exactly like a rotation lesson
    if not dry_run:
        state["history"] = [h for h in state["history"]
                            if not (h.get("date") == _today
                                    and h.get("rot") == "DAY")]
        state["history"].append({
            "date": _today, "rot": "DAY", "score": avg_score,
            "total_left": total_left, "realized_pnl": _realized,
            "balance_after": _bal, "usd_left": _day_result["usd_left"],
            "top_gate": _day_result["top_gate"], "ts": _now().isoformat(),
            "top_verdict": _topv,
        })
        state["history"] = state["history"][-60:]
        save_state(state)
        if speak:
            _speak_cooldown_review(_day_result, _pace, _realized,
                                   _n_trades, _tide_txt, _no_candle)

    return {
        "date": _today, "kind": "cooldown_review", "score": avg_score,
        "rotations": len(_today_rots),
        "usd_left": _day_result["usd_left"], "total_left": total_left,
        "top_gate": _day_result["top_gate"],
        "system_left": _day_result["system_left"],
        "realized_pnl": _realized, "n_trades": _n_trades,
        "daily_pace": round(_pace, 2),
        "vs_pace": round(_realized - _pace, 2),
        "tide_at_close": _tide_txt,
        "no_candle": _no_candle,
        "weight_deltas": _deltas,
        "assets": assets,
        "lesson": lesson,
        "top_verdict": _topv,
    }


def _speak_cooldown_review(result, pace, realized, n_trades, tide_txt,
                           no_candle) -> None:
    """The cooldown review in her voice: the facts, the gap, the plan."""
    try:
        import background_scanner as bs
        _p = []
        _p.append(f"Today's review. "
                  f"{n_trades} trade{'s' if n_trades != 1 else ''}, "
                  f"{'plus' if realized >= 0 else 'minus'} "
                  f"{abs(realized):,.0f} dollars realized"
                  + (f", the goal was {pace:,.0f}" if pace > 0 else "") + ".")
        if result["total_left"] > 1:
            _p.append(f"{result['total_left']:,.0f} dollars was left on the table")
            if result.get("top_gate"):
                _p.append(f"the {result['top_gate']} gate buried the most")
        if tide_txt:
            _p.append(f"the tide closed {tide_txt}")
        if result.get("top_verdict"):
            _tv = result["top_verdict"]
            _tv_name = str(_tv[0]).split(" - ")[0]
            _lesson_txt = str(result.get("lesson") or "").lower()
            if _tv_name.lower() not in _lesson_txt:
                _p.append(f"the costliest pattern on the book is "
                          f"{_tv_name} at {_tv[1]:,.0f} dollars")
        # the asset story rides the lesson (appended once, by the
        # review) — the lesson is the last clause; no separate asset
        # line, she would say it twice
        if result.get("lesson"):
            _p.append(str(result["lesson"]))
        # normalize: clauses carry no trailing period — the joiner owns
        # the sentence boundaries (the live 20:08 line said "240..")
        _p = [str(c).rstrip(".") for c in _p]
        bs._speak_server(". ".join(_p) + ".", rate=1)
    except Exception:
        pass


def _rot_is_split(when) -> bool:
    """True when the MST day started mid-rotation (weekday boundary).
    The day's rotation results may belong to a rotation that opened the
    previous MST day — the review aggregates by date, honestly."""
    return False  # today's 20-24 close always owns its own day-window


# ── THE DAY-LEVEL AGGREGATION HELPERS (the review reads only what the
#    rotation evaluations already proved — no new theory) ────────────

def _day_system_pairs(state, today):
    out = []
    for h in state.get("history") or []:
        if h.get("date") == today and h.get("rot") != "DAY":
            out.extend(h.get("system_pairs") or [])
    return out


def _day_system_duds(state, today):
    out = []
    for h in state.get("history") or []:
        if h.get("date") == today and h.get("rot") != "DAY":
            out.extend(h.get("system_duds") or [])
    return out


def _day_fired_any(today) -> bool:
    """Did ANY popup fire today? The SAME evidence the rotation's fired
    set uses: a spoken 'Buy {asset}!' line in the voice log (the fire
    confirmation — a burial row proves a burial, never a fire)."""
    try:
        p = Path(__file__).parent / "voice_log.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = str(d.get("ts", ""))
                if not ts.startswith(today):
                    continue
                text = str(d.get("text") or "")
                if text.startswith("Buy ") and "!" in text:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _top_gate_of_day(state, today):
    gate_usd = {}
    for h in state.get("history") or []:
        if h.get("date") == today and h.get("rot") != "DAY":
            for p in (h.get("system_pairs") or []):
                g = str(p.get("gate") or "?")
                gate_usd.get(g)
                gate_usd[g] = gate_usd.get(g, 0.0) + float(p.get("usd") or 0.0)
    return max(gate_usd, key=gate_usd.get) if gate_usd else None


def _day_rot_fired(h) -> bool:
    """RETAINED as documentation of the wrong heuristic: the burial
    ledger records burials, not fires — _day_fired_any (voice log) is
    the factual fire evidence. Kept defined per don't-delete."""
    return _day_fired_any(str(h.get("date") or ""))


def _day_weight_deltas(state, today):
    # The adjustments ACTUALLY made today: weight changes recorded in
    # today's lessons. Parses the lessons log's rises/eases sentences
    # (the only record of the nudge) - factual by construction.
    deltas = {}
    import re as _re
    for l in state.get("lessons") or []:
        if not str(l.get("ts", "")).startswith(today):
            continue
        for m in _re.finditer(
                r"(\w+) (?:weight )?(?:rises|eases) ([\d.]+) (?:.>?) ([\d.]+)",
                str(l.get("lesson") or "")):
            name, a, b = m.group(1), float(m.group(2)), float(m.group(3))
            deltas[name] = round(deltas.get(name, 0.0) + (b - a), 3)
    return deltas


def _insight(result: dict, missed: list, missed_exit: list) -> str:
    # ONE actionable suggestion - the sharpest lever this rotation.
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
                f"theoretical) - carve-out data, not a discipline miss. "
                f"Your taps were clean.")
    if usd.get("missed_exit", 0) >= max(usd.values()):
        return ("Next time: when the goal hits, tap SOLD - the exit scan "
                "already told you when.")
    if usd.get("missed", 0) >= max(usd.values()):
        return ("Next time: a paying popup is the system's best call - "
                "take the green light when it fires.")
    if usd.get("giveback", 0) >= max(usd.values()):
        return ("Next time: bank at the goal - the trail protects the "
                "rest, but the goal is yours to take.")
    return ("Keep the rotation rhythm: goal, window, exit - the machine "
            "keeps the clock, you keep the taps.")


# ── THE PER-ASSET REVIEW (user spec, Sep 21: "review all the assets
# and how they actually performed ... did we buy the top, exit too soon
# or stay too long, what happened to the money left on the table?") ──
# One factual verdict per asset the day touched, using the SAME
# component arithmetic as the rotation score: realized vs the goal,
# the peak reached, and where the theoretical money went.

def _asset_verdict(entry, exit_px, peak, goal_pct, realized, size):
    """(verdict, usd_left) from the trade's real numbers — the same
    giveback/missed-exit shape the rotation evaluation computes."""
    if not entry or not peak or entry <= 0:
        return ("unreadable", 0.0)
    peak_pct = (peak / entry - 1.0) * 100.0
    if goal_pct <= 0:
        goal_pct = 1.5
    reached = peak_pct >= goal_pct * 0.9
    if exit_px and exit_px > 0:
        # stayed-too-long first: a peak far past the goal with most of
        # it given back is that verdict, not "exited too soon"
        if peak_pct >= goal_pct * 1.5 and realized < peak_pct / 100.0 * size * 0.5:
            left = max(0.0, size * peak_pct / 100.0 - max(realized, 0.0))
            return ("stayed too long - gave back over half the peak", left)
        if reached and realized < goal_pct / 100.0 * size * 0.9:
            left = max(0.0, size * peak_pct / 100.0 - max(realized, 0.0))
            return ("exited too soon - the goal was on the tape", left)
        if realized >= goal_pct / 100.0 * size * 0.9:
            return ("took the goal - the discipline shape", 0.0)
        if entry > 0 and exit_px < entry * 0.995 and peak_pct <= goal_pct * 0.5:
            return ("bought the top - the coin never ran", max(0.0, -realized))
        return ("scratch - small move, small result", 0.0)
    if reached:
        left = max(0.0, size * peak_pct / 100.0)
        return ("goal hit and never exited", left)
    return ("still holding under the goal", 0.0)


def _day_assets(exchange, today, state):
    """Every asset the day touched, factually: sold trades (their real
    numbers), still-held watch rows (live price), and the funnel's
    buried candidates (the rotation evaluations' system_pairs). Returns
    the list the review reports — no theory, only recorded outcomes."""
    out = []
    try:
        p = Path(__file__).parent / "contest_trades.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            ts = str(t.get("sold_at", ""))
            if not ts.startswith(today):
                continue
            entry = float(t.get("entry") or 0.0)
            exit_px = float(t.get("exit") or 0.0)
            size = float(t.get("size") or 0.0)
            realized = float(t.get("pnl_net") or 0.0)
            goal = float(t.get("goal_pct") or 1.5)
            high = float(t.get("high") or 0.0) or max(exit_px, entry)
            v, left = _asset_verdict(entry, exit_px, high, goal,
                                     realized, size)
            out.append({"asset": str(t.get("pair", "?")).split("/")[0],
                        "kind": "sold", "entry": entry, "exit": exit_px,
                        "peak": high, "goal_pct": goal,
                        "realized": realized, "size": size,
                        "verdict": v, "usd_left": round(left, 2)})
    except Exception:
        pass
    # still-held: the watch rows added today (the live price closes them)
    try:
        import json as _j
        wdoc = _j.loads((Path(__file__).parent / "contest_watch.json")
                        .read_text(encoding="utf-8"))
        for pair, wr in (wdoc or {}).items():
            try:
                ad = str(wr.get("added") or "")
                if not ad.startswith(today):
                    continue
                entry = float(wr.get("entry") or 0.0)
                size = float(wr.get("size") or 0.0)
                if not entry or not size:
                    continue
                goal = float(wr.get("goal_pct") or 1.5)
                high = float(wr.get("high") or entry)
                live = None
                try:
                    live = float(exchange.fetch_ticker(pair)["last"])
                except Exception:
                    live = None
                cur = (live / entry - 1.0) * size if live else 0.0
                v, left = _asset_verdict(entry, live or 0.0, high, goal,
                                         cur, size)
                out.append({"asset": str(pair).split("/")[0],
                            "kind": "held", "entry": entry,
                            "exit": live, "peak": high, "goal_pct": goal,
                            "realized": round(cur, 2), "size": size,
                            "verdict": v, "usd_left": round(left, 2)})
            except Exception:
                continue
    except Exception:
        pass
    # buried: what the funnel never let reach the user (from the day's
    # rotation evaluations — the same rows the seam scores)
    for h in (state.get("history") or []):
        if h.get("date") != today or h.get("rot") == "DAY":
            continue
        for sp in (h.get("system_pairs") or []):
            out.append({"asset": str(sp.get("pair", "?")).split("/")[0],
                        "kind": "buried", "gate": sp.get("gate"),
                        "usd_left": round(float(sp.get("usd") or 0.0), 2),
                        "verdict": "buried by the funnel - never offered"})
    return out


def _asset_lead(assets):
    """The sharpest asset fact, for the day lesson: the single biggest
    left-on-the-table line, or the clean-taps line when nothing leaked."""
    real = [a for a in (assets or []) if a.get("kind") in ("sold", "held")]
    buried = [a for a in (assets or []) if a.get("kind") == "buried"]
    if real:
        top = max(real, key=lambda a: float(a.get("usd_left") or 0.0))
        if float(top.get("usd_left") or 0.0) > 1:
            return (f"{top['asset']}: {top['verdict']} - "
                    f"${top['usd_left']:,.0f} of the move went with it.")
        bad = [a for a in real if "bought the top" in str(a.get("verdict"))]
        if bad:
            return (f"{bad[0]['asset']}: bought the top - the setup "
                    f"appears again, the tape did not.")
    if buried:
        bg = max(buried, key=lambda a: float(a.get("usd_left") or 0.0))
        if float(bg.get("usd_left") or 0.0) > 1:
            return (f"The funnel buried {bg['asset']} "
                    f"(${bg['usd_left']:,.0f} theoretical, "
                    f"{bg.get('gate') or '?'} gate) - the machine's leave.")
    if real:
        return "Every asset taken closed clean - nothing leaked."
    return ""


def _top_verdict_class():
    """The most expensive verdict class across the closed-trade ledger,
    cited from the nightly diagnostic: _verdict_study.summary() is the
    single source of truth (the same numbers the report and the console
    plate show); this picks the top row. User, Sep 21: the nightly
    review speaks the current top verdict class and its dollar cost.
    (verdict, usd_left) or None — fail-soft: an unreadable ledger or
    candle store says nothing, never breaks the review."""
    try:
        import _verdict_study as _vs
        s = _vs.summary()
        if not s or s[0][2] <= 0:
            return None
        return (str(s[0][0]), float(s[0][2]))
    except Exception:
        return None
