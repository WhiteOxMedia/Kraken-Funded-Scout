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

# ── THE SELF-RESOLVING VERDICT (user rule, Oct 5: "we want the number
# to be gave back $0 ... the calibration can hold"): an UNPROVEN lesson
# is not the end — the money has not spoken YET. When a later
# evaluation (the next rollover, the cooldown review) finds the prior
# rotation's left-on-table money REALIZED — pairs that paid inside the
# window later SOLD for real profit in the journal — and the window's
# remaining leave has drained to dust, the stored lesson upgrades from
# "unproven" to "stands": the verdict a day can earn retroactively.
# CONSERVATIVE BY CONSTRUCTION: no journal evidence leaves every lesson
# exactly as it was; a no-trade window can only resolve when realized
# money OUTSIDE it PROVES the leave landed (buried payers are the
# machine's leave — their own later sale proves the leave landed, not
# the user's discipline).
RESOLVE_DUST_USD = 1.0    # the same crumb the voice itself ignores
RESOLVE_MIN_PROFIT_USD = 5.0   # the leave must land as REAL profit
RESOLVE_MAX_LOOKBACK_DAYS = 3  # evidence older than this never joins
RESOLVE_ENTRY_TOL = 0.10 # a sale proves THE opportunity only when its
                        # entry is this close to the opportunity entry
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
# THE PAYER HORIZON (Oct 1, the right-censoring rule): the window a
# burial needs to prove itself before the boundary judges it. Burials
# closer to the boundary than this are UNJUDGED (system_pending) —
# never duds, never docked; the 1h-candle coin test otherwise read
# censoring as "the gate was right" and the dock acquitted itself.
BURIAL_HORIZON_HOURS = 3.0
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


def _worst_burial_gate(system_pairs: list, system_duds: list) -> dict | None:
    """THE PER-GATE PAYER RATIO (user ask, Sep 28: the boundary names
    WHICH gate buried the most payers and by how much): count payers
    and duds per gate from the evaluation's own attribution and pick
    the worst — payers PRIMARY (which gate cost the most), payer-rate
    the tiebreak. None when nothing paid (no gate to blame).
    Module-level so the drive can execute it on synthetic data."""
    pay: dict[str, int] = {}
    dud: dict[str, int] = {}
    for p in system_pairs or []:
        g = str(p.get("gate") or "?")
        pay[g] = pay.get(g, 0) + 1
    for d in system_duds or []:
        g = str(d.get("gate") or "?")
        dud[g] = dud.get(g, 0) + 1
    worst = None
    for g, n in pay.items():
        buried = n + dud.get(g, 0)
        rate = n / buried if buried else 0.0
        if worst is None or (n, rate) > (worst["payers"], worst["rate"]):
            worst = {"gate": g, "payers": n, "buried": buried,
                     "rate": round(rate, 2)}
    return worst


def _worst_gate_txt(wg: dict | None) -> str:
    """The worst-gate clause the boundary, the voice, and the lesson
    all speak (empty string when there is no gate to blame)."""
    if not wg:
        return ""
    return (f" Worst: the '{wg['gate']}' gate buried "
            f"{wg['payers']} payer(s) of {wg['buried']} "
            f"({wg['rate']:.0%} payer-rate).")


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


def evaluate_rotation(exchange, rot_key: str, when=None, speak=True,
                      watch_file=None) -> dict:
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
            # still held from this window? the watch's added timestamp.
            # THE INJECTION SEAM (the hermeticity sweep, Sep 28): the
            # watch file is a LIVE runtime file the scanner owns — a
            # default-path read inside an evaluation a drive can pull
            # is the prose-backfill class, so the path is injectable.
            try:
                _wf = Path(watch_file) if watch_file \
                    else Path(__file__).parent / "contest_watch.json"
                wdoc = json.loads(_wf.read_text())
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
    # THE RIGHT-CENSORING RULE (Oct 1, the operator's confirm): a burial
    # evaluated with less than the payer horizon left in its window had
    # almost no chance to prove itself — the 1h-candle coin test read
    # censoring as acquittal and the dock's payer-rate math and the
    # honest-drought reason both followed. Unjudged, never a dud, no
    # dock: the machine never acquits itself on right-censored evidence.
    system_pending = []
    for pair, s in supp.items():
        if pair in popup_pairs or pair in fired:
            continue
        goal = _rotation_goal_pct(pair, rot_key, sched)
        # DETECT FIRST (the review's refinement, Oct 1): censoring makes
        # NO-evidence unjudgable — it never discounts DETECTED evidence.
        # The coin test runs BEFORE the horizon classifies: a payer the
        # candles saw still docks, whatever its horizon.
        entry = _entry_at(exchange, pair, s["ts"])
        paid = (entry > 0
                and _coin_paid(exchange, pair, entry, goal, s["ts"], end))
        if paid:
            system_burial.append({"pair": pair, "gate": s["gate"],
                                  "usd": balance * goal / 100.0,
                                  "goal_pct": goal, "paid": True,
                                  "taken": False, "entry": entry})
        elif (end - s["ts"]).total_seconds() < BURIAL_HORIZON_HOURS * 3600:
            # no evidence AND almost no horizon to find any — unjudged
            system_pending.append(
                {"pair": pair, "gate": s["gate"],
                 "horizon_left_h": round((end - s["ts"]).total_seconds()
                                         / 3600.0, 2)})
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
        _wf = Path(watch_file) if watch_file \
            else Path(__file__).parent / "contest_watch.json"
        wdoc = json.loads(_wf.read_text())
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
    # WHY WAS IT SILENT (user ask, Sep 28: "when a rotation happens but
    # no popups fired it should ask why + self adjust — she says 'all
    # good' but doesn't check if it is"): the voice already said "no
    # popups fired" without saying WHY. The diagnosis reads the
    # evaluation's own data: rail-worthy candidates the funnel BURIED
    # (the gates strangled the rotation — the machine's fault), a
    # candidates-but-no-fire drought, or the honest no (nothing
    # rail-worthy existed at all). A burial docks the guard through the
    # SAL penalty channel (same key, shared cap, one decay) — the
    # ask-why self-adjusts the widths instead of just narrating.
    empty_why = ""
    empty_buried = 0
    empty_payers = 0
    worst_gate = None   # initialized OUTSIDE the empty branch (the
                        # isolated review's critical, Sep 28): a FIRING
                        # rotation constructs the same result dict — an
                        # unbound name here would NameError the whole
                        # evaluation into the scanner's debug-swallow
    empty_pending = 0   # (Oct 1) same class: the censoring count rides
                        # the result dict on FIRING rotations too
    _silent_dock = {}
    if empty_fire:
        gates = sorted({str((supp.get(p) or {}).get("gate") or "?")
                        for p in supp})
        empty_buried = len(supp)
        # THE MEASURED COST (user ask, Sep 28: "dock proportional to the
        # measured opportunity cost"): the evaluation ALREADY ran every
        # suppressed pair through the coin test — system_burial holds
        # the ones that reached goal (the machine's fault), system_duds
        # the ones that didn't (the gate being RIGHT). The dock's size
        # follows that measurement: payers x payer-rate, not a flag.
        empty_payers = len(system_burial)
        empty_pending = len(system_pending)
        worst_gate = _worst_burial_gate(system_burial, system_duds)
        # the why carries the unjudged rows too — censoring never
        # vanishes into "the gate was right"
        _pend_txt = ""
        if empty_pending:
            _pend_txt = (f"; {empty_pending} more burial(s) too close to "
                         f"the boundary to judge (under the "
                         f"{BURIAL_HORIZON_HOURS:.0f}h payer horizon) — "
                         f"unjudged, no dock")
        if empty_buried > empty_pending:
            empty_why = (f"{empty_buried} rail-worthy candidate(s) were "
                         f"buried, {empty_payers} of them reached goal "
                         f"(gates: {', '.join(gates)})"
                         + _worst_gate_txt(worst_gate) + _pend_txt)
        elif empty_pending:
            empty_why = (f"{empty_pending} rail-worthy candidate(s) were "
                         f"buried too close to the boundary to judge — "
                         f"unjudged, no dock")
        elif popups:
            empty_why = ("candidates existed but none fired — a drought in "
                         "the funnel, not the market")
        else:
            empty_why = "the funnel offered nothing rail-worthy — the market's honest no"
        try:
            import meta_layers as _mlp
            _silent_dock = _mlp.dock_silent_rotation(
                empty_buried - empty_pending, when=when,
                n_payers=empty_payers,
                rot_key=rot_key)   # the EVALUATED window (the boundary
                                   # fires at 16:00 ABOUT 12-16 — the
                                   # dedup key must name the window
                                   # judged, not the one beginning)
        except Exception:
            _silent_dock = {}
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
        "system_pending": system_pending,
        "empty_pending": empty_pending,
        "popups": [{"pair": p["pair"], "paid": p.get("paid"),
                    "taken": p.get("taken"), "goal_pct": round(p.get("goal_pct", 0), 2)}
                   for p in popups],
        "realized_pnl": round(realized_pnl, 2),
        "balance_after": balance,
        "empty_fire": empty_fire,
        "empty_why": empty_why,
        "empty_buried": empty_buried,
        "empty_payers": empty_payers,
        "worst_gate": worst_gate,
        "silent_dock": _silent_dock,
        "bonuses": bonuses, "weights": dict(weights),
        "ts": _now().isoformat(),
    }

    # ── self-learning: when the score and the money disagree, adjust ──
    # THE RESOLVER RUNS FIRST (the most recent unproven lesson gets
    # first claim on the fresh evidence; this window's own result is
    # not in the history yet, so it is never its own resolver)
    _resolve_prior_verdict(state, start, end)
    record_burial_ledger(state, result)   # the seam's ledger: WHICH gate buried, how much
    lesson = _learn(state, result)
    result["lesson"] = lesson
    _worth_ladder_ingest(result)   # THE LADDER'S INGEST: only a
    # PROVEN rotation (money confirmed the score) ever mints a
    # floor candidate; QC holds the apply either way (fail-soft)
    _entry = {k: result[k] for k in
              ("date", "rot", "score", "total_left", "system_left",
               "top_gate", "realized_pnl", "balance_after", "ts",
               "system_pairs", "system_duds",
               "system_pending", "empty_pending")}   # the per-pair burials
    # (+ the unjudged rows: the censoring evidence must outlive the
    # call — the review's medium, Oct 1: a result field the _entry
    # never copied died at the boundary)
    # persist too (Sep 21): the cooldown review's per-asset layer and the
    # console's review plate read them — a burial with no pair named is
    # money the review can never attribute
    # THE VERDICT RIDES THE ROW (the resolver’s inbox: the exact
    # unproven language _learn rendered, so a later evaluation can
    # upgrade the one lesson it proved) — and so does the PAIR
    # EVIDENCE (the resolver’s join keys: which pairs the window
    # itself left on the table, per component — a sale proves the
    # leave only when it sells one of THESE)
    _entry["verdict"] = lesson
    _entry["left_pairs"] = {
        "missed": [p.get("pair") for p in missed],
        "giveback": [t.get("pair") for t in giveback],
        "missed_exit": [t.get("pair") for t in missed_exit],
        "early_exit": [],
        "system_burial": [p.get("pair") for p in system_burial],
    }
    _entry["left_entries"] = {
        "missed": {p.get("pair"): float(p.get("entry") or 0.0)
                   for p in missed},
        "giveback": {t.get("pair"): float(t.get("entry") or 0.0)
                     for t in giveback},
        "missed_exit": {t.get("pair"): float(t.get("entry") or 0.0)
                        for t in missed_exit},
        "early_exit": {},
        "system_burial": {p.get("pair"): float(p.get("entry") or 0.0)
                          for p in system_burial},
    }
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


def _resolve_prior_verdict(state: dict, current_start=None,
                           current_end=None) -> int:
    """The verdict resolver (Oct 5): find the most recent UNPROVEN
    rotation lesson in the history, re-join its leave to the SOLD
    journal as of NOW, and upgrade the lesson when the money has
    spoken. Returns 0 (nothing changed) or 1 (a verdict moved).

    THE PAIR-MATCHED JOIN (the second opinion medium, cured): a sale
    proves a window leave only when the sold PAIR is one the window
    itself named (left_pairs, the missed / giveback / missed_exit /
    system_burial pairs the evaluation stored). Unrelated profit
    proves nothing; a legacy row without the map never resolves. A
    user-leave sale must sit INSIDE the window (the late-journaled
    exit); a MARKED burial pair proves the leave wherever AFTER the
    window it finally sold (the leave of the machine lands when the
    pair pays, not when the journal writes). Each sale credits its
    realized PROFIT (a fresh round-trip loss after the leave landed
    is not this window leave), the credit debits the stored leave,
    and the verdict moves only when its WORDS change (idempotent:
    a re-review of an already graded window appends no duplicate
    lesson).
    ONE SALE PROVES ONE VERDICT (the fifth-round medium, cured): a
    sale row is CONSUMED on use — its signature lands in the
    state’s resolver-used ledger, and no later call may credit it
    for ANOTHER verdict (the same pair buried on three days does not
    turn one sale into three proofs).
    Fail-soft: any broken read leaves the lesson untouched (0)."""
    try:
        rows = []
        try:
            _p = Path(__file__).parent / "contest_trades.jsonl"
            for _line in _p.read_text(encoding="utf-8").splitlines():
                if not _line.strip():
                    continue
                try:
                    _t = json.loads(_line)
                    if not _t.get("applied"):
                        continue   # only a sale that COUNTED proves money
                    _ts = datetime.fromisoformat(str(_t.get("sold_at", "")))
                    if _ts.tzinfo is None:
                        _ts = _ts.replace(tzinfo=MST)
                    rows.append((_ts.astimezone(MST),
                                 float(_t.get("pnl_net") or 0.0),
                                 str(_t.get("pair") or ""),
                                 float(_t.get("entry") or 0.0),
                                 str(_t.get("pair") or "") + "|"
                                 + str(_t.get("sold_at") or "")))
                except Exception:
                    continue
        except Exception:
            return 0
        if not rows:
            return 0
        _floor = (_now() - timedelta(days=RESOLVE_MAX_LOOKBACK_DAYS))
        _used_set = set(state.get("resolver_used_sales") or [])
        for h in reversed(state.get("history") or []):
            if h.get("rot") in (None, "", "DAY"):
                continue
            verdict = str(h.get("verdict") or "")
            if "unproven" not in verdict:
                if verdict:
                    continue   # a graded verdict, not unproven
                # THE LEGACY SHIM (user ask, Oct 5): rows written
                # before the verdict rode the row carry None - the
                # agreement bar decides eligibility from the row's
                # OWN stored numbers, so the inbox never claims a
                # rotation the bar itself proves
                _r0 = float(h.get("realized_pnl") or 0.0)
                _l0 = float(h.get("total_left") or 0.0)
                if _r0 > 0.0 and _l0 <= 1.0:
                    continue   # proven without the journal
                verdict = "unproven"   # eligible; words below gate
            _d = h.get("date")
            if not _d:
                continue
            try:
                _s, _e = _rot_window(str(h.get("rot")),
                                     datetime.strptime(_d, "%Y-%m-%d")
                                     .replace(tzinfo=MST))
            except Exception:
                continue
            if _s is None or _s < _floor:
                continue   # unparseable, or older than the lookback
            if current_start and _e >= current_start:
                continue   # the window under evaluation now, not yet closed
            usd = h.get("usd_left")
            pairs_map = h.get("left_pairs")
            if not (isinstance(usd, dict) and usd):
                continue   # legacy row, no component map: never resolves
            if not (isinstance(pairs_map, dict)
                    and any(bool(v) for v in pairs_map.values())):
                # THE LEGACY SHIM: the evaluation ALWAYS recorded
                # which pairs the gates buried (system_pairs) - the
                # burial map derives from the row's OWN record (the
                # pair-only join the resolver already honors for
                # pre-entry rows). Nothing is invented: no record,
                # no resolution.
                _sp = sorted(set(str(p.get("pair") or "")
                                 for p in (h.get("system_pairs")
                                           or [])))
                _sp = [q for q in _sp if q]
                if not _sp:
                    continue   # no pair evidence: never resolves
                pairs_map = {"system_burial": _sp}
            entries_map = (h.get("left_entries")
                           if isinstance(h.get("left_entries"), dict)
                           else {})   # opportunity entries: the join
                                      # key for THE sale (a row written
                                      # before this map keeps the
                                      # pair-only semantics)
            _left = round(sum(float(v) or 0.0 for v in usd.values()), 2)
            if _left <= RESOLVE_DUST_USD:
                continue   # nothing left to prove: not the resolver case
            # ONE OPPORTUNITY-SALE PER (PAIR, COMPONENT), DRAINED
            # PER-COMPONENT, ACCUMULATED ACROSS PASSES — the review
            # mediums through six rounds: the leave is one missed move
            # per pair, proven by THE sale that took it (entry within
            # RESOLVE_ENTRY_TOL — an unrelated same-pair round trip
            # entered elsewhere proves nothing), the pair's best
            # qualifying sale (later round trips never stack past the
            # pair's own drainage), the credit drains ONLY the
            # components its pairs belong to (one oversized sale cannot
            # prove another pair's unrealized leave), the running
            # credit ACCUMULATES on the row (several smaller sales can
            # combine to fully resolve a window), and the winning
            # sales are CONSUMED (one sale proves one verdict, never
            # the same-pair burial of another day).
            _vc = (dict(h.get("verdict_credit"))
                   if isinstance(h.get("verdict_credit"), dict) else {})
            _credit = 0.0
            _claimed = set()   # a pair already credited in one component
            _used = []         # signatures this upgrade consumes
            for _c, _pairs in pairs_map.items():
                for _pair in (_pairs or []):
                    if _pair in _claimed:
                        continue
                    _op = (entries_map or {}).get(_c, {}).get(_pair)
                    _best = 0.0
                    _best_sig = None
                    for _ts, _pnl, _rp, _re, _sig in rows:
                        if _rp != _pair or _pnl <= 0 or _sig in _used_set:
                            continue
                        if _op and not (
                                abs(_re - _op) <= RESOLVE_ENTRY_TOL * _op):
                            continue   # not THE opportunity
                        if _c == "system_burial":
                            if _ts >= _e and _pnl > _best:
                                _best = _pnl
                                _best_sig = _sig
                        elif _s <= _ts < _e and _pnl > _best:
                            _best = _pnl
                            _best_sig = _sig
                    if _best > 0:
                        _credit = round(_credit + _best, 2)
                        _vc[_c] = round(float(_vc.get(_c) or 0.0) + _best, 2)
                        _claimed.add(_pair)
                        _used.append(_best_sig)
            if _credit < RESOLVE_MIN_PROFIT_USD:
                continue   # no new evidence this pass: the words stay
            # the credit drains only its own components; the verdict is
            # proven when EVERY component's remainder is dust
            _rem_map = {c: max(0.0, round(float(usd.get(c) or 0.0)
                                          - float(_vc.get(c) or 0.0), 2))
                        for c in set(pairs_map) | {k for k, v in usd.items()
                                                  if float(v or 0.0) > 0.0}}
            _rem = round(sum(_rem_map.values()), 2)
            _run = round(sum(float(v) or 0.0 for v in _vc.values()), 2)
            if _rem > RESOLVE_DUST_USD:
                _stuck = ", ".join(f"{c} ${v:,.0f}"
                                   for c, v in sorted(_rem_map.items())
                                   if v > RESOLVE_DUST_USD)
                _new = (f"unproven (partially resolved: ${_run:,.0f} of its "
                        f"${_left:,.0f} realized, ${_rem:,.0f} still unproven "
                        f"[{_stuck}] — the money is landing)")
            else:
                _new = (f"proven after the fact (score {h.get('score'):.0f}, "
                        f"leave ${_left:,.0f} realized — the money "
                        f"landed, the calibration stands)")
            if _new == verdict:
                continue   # already graded exactly this: no duplicate
                           # lesson, and the embodied sale credit stays
                           # live for the next better evidence
            h["verdict"] = _new
            h["verdict_ts"] = _now().isoformat()
            h["verdict_credit"] = _vc   # the running credit:
                                         # smaller sales COMBINE
                                         # across passes
            # the winning sales are CONSUMED: one sale proves one
            # verdict, never the same-pair burial of another day
            _ledger = state.setdefault("resolver_used_sales", [])
            _ledger.extend(_used)
            state["resolver_used_sales"] = _ledger[-200:]
            _kind = ("partially proven" if _rem > RESOLVE_DUST_USD
                     else "PROVEN after the fact")
            _tail = ("the money is landing." if _rem > RESOLVE_DUST_USD
                     else "the money landed, the calibration stands.")
            state.setdefault("lessons", []).append(
                {"ts": h["verdict_ts"],
                 "lesson": f"Rotation {_d} {h.get('rot')} {_kind}: "
                           f"${_credit:,.0f} of its ${_left:,.0f} leave "
                           f"realized — {_tail}"})
            return 1
        return 0
    except Exception:
        return 0


def _giveback_dominant(usd: dict) -> bool:
    """Giveback dominates the day's leave AND clears the machine's own
    tier-scaled tighten trigger — the floor that keeps dust from
    steering weights. Fail-soft False: an unreadable trigger never
    moves a weight on a lie."""
    try:
        _gb = float((usd or {}).get("giveback") or 0.0)
        if _gb <= 0:
            return False
        if _gb < max((float(v) or 0.0) for v in (usd or {}).values()):
            return False
        import contest_account as _ca_gbdom
        return _gb >= float(_ca_gbdom.giveback_thresholds()[0])
    except Exception:
        return False


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
    if _giveback_dominant(usd):
        # ── THE GIVEBACK'S OWN CHANNEL, FIRST IN THE CHAIN (user ask,
        # Oct 5; the second opinion's precedence medium): when the
        # rotation's biggest leave is money the account REACHED and
        # handed back, the giveback weight rises even when the score's
        # other components dilute the sting — and even when a burial
        # pile-up rode the same day (the burial learned at its own
        # rotation's rollover; the biggest leave names its own weight
        # here). The machine's own tighten trigger is the floor (dust
        # dominates nothing worth moving); neither the softening branch
        # below nor any other branch can reach a giveback-dominant
        # result, because this branch answers first.
        old = w.get("giveback", 1.0)
        w["giveback"] = round(min(W_MAX, old * (1.0 + NUDGE)), 3)
        lesson = (f"Gave back ${usd['giveback']:,.0f} after the goal — the "
                  f"rotation's biggest leave. The giveback weight rises "
                  f"{old:.2f} → {w['giveback']:.2f}: money reached and "
                  f"handed back counts, whatever else the score diluted.")
    elif usd.get("system_burial", 0) > 1 and _net >= 2:
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
        # builds) and the lesson names the silence — WITH the worst
        # gate named (the per-gate payer ratio, Sep 28).
        old = w.get("system_burial", 1.0)
        w["system_burial"] = round(min(W_MAX, old * (1.0 + NUDGE)), 3)
        lesson = (f"No popups fired and the rotation lost ${abs(pnl):.0f} — "
                  f"the funnel surfaced nothing worth taking while the "
                  f"account bled. The system_burial weight rises "
                  f"{old:.2f} → {w['system_burial']:.2f}: a silent funnel "
                  f"is not a disciplined one."
                  + _worst_gate_txt(result.get("worst_gate")))
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
                  f"perfect). Realized ${pnl:+.0f}; weights hold."
                  + _worst_gate_txt(result.get("worst_gate")))
    else:
        # ── THE AGREEMENT BAR (user rule, Oct 5): the old catch-all
        # printed "Score and money agree ... the calibration stands" for
        # score 50 + realized $+0 + $505 left on the table — a verdict
        # the money had not earned. Agreement is the money CONFIRMING
        # the score, and a calibration can stand only on REALIZED
        # money: the leave must realize (the left-on-table money
        # becomes realized) and the money left on the table must
        # become $0 (≤ the $1 crumb the voice itself ignores). Until
        # then the verdict is UNPROVEN — weights hold, nothing stands.
        _left = float(result.get("total_left")
                      or sum(float(v) or 0.0 for v in usd.values()))
        if pnl > 0 and _left <= 1:
            lesson = (f"Score and money agree (score {score:.0f}, realized "
                      f"${pnl:+.0f}) — weights hold; the calibration stands.")
        else:
            _why = ("the money went backwards" if pnl < 0 else
                    f"${_left:,.0f} left on the table" if _left > 1 else
                    "nothing realized")
            lesson = (f"Score {score:.0f}, realized ${pnl:+.0f} — {_why}: "
                      f"the money hasn't confirmed the score, so this is "
                      f"not agreement. Weights hold unproven — the "
                      f"calibration stands only when the leave realizes "
                      f"and the money left on the table becomes $0.")
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
            if result.get("empty_why"):
                line += f"Because: {result['empty_why']}. "
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


def cooldown_review(exchange=None, when=None, speak=True, dry_run=False,
                    watch_file=None) -> dict:
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
    # THE RESOLVER AT COOLDOWN (the night’s last look at today’s
    # unproven lessons — the day’s own window no longer shadows them)
    _resolve_prior_verdict(state)
    _worth_ladder_reconcile(state)   # the reconciler pass: an
    # entry the money has since proven flips Unproven -> Proven
    # ONCE (audit row + the next day's candidate; idempotent by
    # the ladder's own ledger)
    state.setdefault("lessons", [])

    # ── 6b. THE PER-ASSET REVIEW (user spec, Sep 21: "review all the
    # assets and how they actually performed ... did we buy the top,
    # exit too soon or stay too long, what happened to the money left
    # on the table?"): every asset the day touched — sold, still held,
    # and what the funnel buried — each with one factual verdict using
    # the SAME component arithmetic the rotation score uses.
    assets = _day_assets(exchange, _today, state, watch_file=watch_file)

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
    _worth_ladder_ingest(_day_result)   # the DAY aggregate rides
    # the same agreement bar (realized > 0, table drained) before
    # the ladder may consider a floor nudge from it
    # the night-to-night tracker: when one pattern has topped the book a
    # week straight, the streak rides the review (return, DAY row, voice)
    # so the warning is impossible to miss on any surface
    _strk, _strk_name, _strk_usd = top_verdict_streak()
    _day_result["verdict_streak"] = (
        {"n": _strk, "name": _strk_name, "usd": round(_strk_usd, 2)}
        if _strk >= VERDICT_STREAK_WARN_N else None)
    # the color-level streak (user, Sep 21): the same LEAK color atop the
    # strip five nights running — coarser than the class streak, so a
    # mixed red week (stayed-too-long + bought-the-top) still warns
    _cstrk, _ccol, _cusd = verdict_color_streak()
    _day_result["verdict_color_streak"] = (
        {"n": _cstrk, "color": _ccol, "usd": round(_cusd, 2)}
        if _cstrk >= VERDICT_COLOR_STREAK_WARN_N else None)
    # the top-three line (user, Sep 22): when the #1 pattern has held
    # VERDICT_TOP3_STREAK_N nights running, the review carries the
    # report's top three — the return, the DAY row and the voice share it
    try:
        _t3 = (verdict_top_three()
               if _strk >= VERDICT_TOP3_STREAK_N else None)
    except Exception:
        _t3 = None
    _day_result["top_three"] = _t3
    # ── THE ERA COMPARE ANNOUNCEMENT (user, Sep 22): the re-base's
    # week-one read rides this night's review — the scanner's one-shot
    # runs on/after Sep 28 and its report lands beside the verdict-
    # cost's; she speaks the headline from the same grading the report
    # used. Before the target date: silent (no pre-week nagging). On or
    # after it: the comparison when the post-era can support one, the
    # armed-state line when it cannot. Fail-soft: a broken module is a
    # missing clause, never a broken review.
    try:
        import era_compare as _ec
        _ecs = _ec.summary()
        if _today >= str(_ec.TARGET_DATE):
            if _ecs.get("readable"):
                _day_result["era_line"] = _ec.top_line(_ecs)
            else:
                _day_result["era_line"] = (
                    f"the re-base week-one read is due — the new era holds "
                    f"{_ecs['post']['trades']} closed trades, short of the "
                    f"{_ecs['min_post_trades']} a comparison needs")
        else:
            _day_result["era_line"] = None
    except Exception:
        _day_result["era_line"] = None

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
            "verdict_streak": _day_result["verdict_streak"],
            "verdict_color_streak": _day_result["verdict_color_streak"],
            "top_three": _day_result.get("top_three"),
            "era_line": _day_result.get("era_line"),
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
        "verdict_streak": _day_result["verdict_streak"],
        "verdict_color_streak": _day_result["verdict_color_streak"],
        "top_three": _day_result.get("top_three"),
        "era_line": _day_result.get("era_line"),
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
        _t3 = result.get("top_three")
        if _t3 and len(_t3) >= 2:
            # the top three replaces the single mention that night — she
            # would otherwise say the #1 twice
            _p.append("the top three on the book: " + "; ".join(
                f"{nm} {u:,.0f} dollars across {n} trades"
                for nm, n, u in _t3))
        elif result.get("top_verdict"):
            _tv = result["top_verdict"]
            _tv_name = str(_tv[0]).split(" - ")[0]
            _lesson_txt = str(result.get("lesson") or "").lower()
            if _tv_name.lower() not in _lesson_txt:
                _p.append(f"the costliest pattern on the book is "
                          f"{_tv_name} at {_tv[1]:,.0f} dollars")
        _vst = result.get("verdict_streak")
        if _vst and int(_vst.get("n") or 0) >= VERDICT_STREAK_WARN_N:
            _p.append(f"and {_vst['name']} has topped the book "
                      f"{_vst['n']} nights straight — the dial is waiting")
            # the study's proposal rides the warning (user, Sep 22): say
            # the SPECIFIC dial, from the trail sim, or nothing — a warning
            # without a defended number is noise
            _prop = trail_proposal(str(_vst.get("name") or ""))
            if _prop:
                _p.append(_prop["proposal"])
        _vcs = result.get("verdict_color_streak")
        if _vcs and int(_vcs.get("n") or 0) >= VERDICT_COLOR_STREAK_WARN_N:
            _p.append(f"and the {_vcs['color']} leak has topped the strip "
                      f"{_vcs['n']} nights straight — the pattern is "
                      f"bigger than one gate")
        # the era-compare week-one read (user, Sep 22): the scheduled
        # verdict on the re-based ratchet — the comparison when the data
        # supports it, the honest shortfall line when it does not
        if result.get("era_line"):
            _p.append(str(result["era_line"]))
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


def _day_assets(exchange, today, state, watch_file=None):
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
    # watch_file rides the same injection seam as evaluate_rotation (the
    # hermeticity sweep, Sep 28).
    try:
        import json as _j
        _wf = Path(watch_file) if watch_file \
            else Path(__file__).parent / "contest_watch.json"
        wdoc = _j.loads(_wf.read_text(encoding="utf-8"))
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


def top_verdict() -> dict | None:
    """TONIGHT'S PATTERN for the console plate (user, Sep 22: "show the
    top-verdict pattern alongside the per-asset verdicts"): the
    costliest verdict class on the closed-trade book, its dollar cost,
    and the trade count — straight from _verdict_study.summary(), the
    same source her 20:00 line cites via _top_verdict_class, so the
    plate, the voice and the nightly report can never disagree.
    Returns {'verdict', 'usd_left', 'n'} or None when the book is clean
    (nothing left on the table) or the ledger cannot be graded."""
    _t = _top_verdict_class()
    if not _t:
        return None
    _n = 0
    try:
        import _verdict_study as _vs
        s = _vs.summary() or []
        _n = next((int(n) for k, n, u in s if str(k) == str(_t[0])), 0)
    except Exception:
        pass
    return {"verdict": str(_t[0]), "usd_left": float(_t[1]), "n": _n}


VERDICT_STREAK_WARN_N = 7   # user, Sep 21: a week straight of the same top


def verdict_history(days: int = 14):
    """THE NIGHT-TO-NIGHT TRACKER (user, Sep 21: track how the top verdict
    class and its cost move night to night). The nightly top-verdict
    trail, oldest -> newest, from the persisted DAY rows: top_verdict
    when the row carries it; the usd_left component map for the nights
    before that field existed (the strip's derivation). Nights with no
    review are skipped. Never raises."""
    try:
        st_ = load_state()
        hist = [h for h in (st_.get("history") or []) if h.get("rot") == "DAY"]
        hist.sort(key=lambda h: str(h.get("date") or ""))
        comp = {"missed": "missed the popup",
                "giveback": "gave back after the goal",
                "missed_exit": "goal hit, never exited",
                "early_exit": "exited too soon",
                "system_burial": "buried by the funnel"}
        out = []
        for h in hist[-days:]:
            tv = h.get("top_verdict")
            if tv and len(tv) >= 2:
                out.append((str(h.get("date") or "")[:10], str(tv[0]),
                            float(tv[1] or 0.0)))
                continue
            if float(h.get("total_left") or 0) > 1:
                ul = h.get("usd_left") or {}
                if ul:
                    k = max(ul, key=lambda kk: float(ul.get(kk) or 0.0))
                    if float(ul.get(k) or 0.0) > 0:
                        out.append((str(h.get("date") or "")[:10],
                                    comp.get(k, "buried by the funnel"),
                                    float(ul.get(k) or 0.0)))
        return out
    except Exception:
        return []


def top_verdict_streak():
    """Consecutive most-recent nights the SAME verdict class tops the
    book (short-form name, before ' - '), capped at the trail's depth.
    (streak, name, latest usd_left) — (0, '', 0.0) when nothing tops.
    The warning fires in the review's voice and on the plate when the
    streak reaches VERDICT_STREAK_WARN_N: a week of the same pattern
    means the dial is waiting, not drifting. Never raises."""
    try:
        hist = verdict_history()
        if not hist:
            return 0, "", 0.0
        top = str(hist[-1][1]).split(" - ")[0]
        if not top:
            return 0, "", 0.0
        n = 0
        for _d, name, _usd in reversed(hist):
            if str(name).split(" - ")[0] == top:
                n += 1
            else:
                break
        return n, top, float(hist[-1][2])
    except Exception:
        return 0, "", 0.0


VERDICT_COLOR_STREAK_WARN_N = 5   # user, Sep 21: five nights of the same leak color

# THE LEAK COLORS — an exact mirror of the console strip's table
# (app.py's _vcol/_VC): her warning and the glyphs can never disagree.
_VERDICT_COLORS = (("took the goal", "green"),
                   ("exited too soon", "amber"),
                   ("stayed too long", "red"),
                   ("bought the top", "red"),
                   ("goal hit and never exited", "red"),
                   ("goal hit, never exited", "red"),
                   ("buried by the funnel", "violet"),
                   ("still holding under the goal", "amber"),
                   ("scratch", "grey"), ("unreadable", "grey"))


def verdict_color(name) -> str:
    """The strip color for a verdict/component name — the same substring
    table the day-by-day glyphs paint with. Unknown names are grey (the
    strip's own fallback). Never raises."""
    try:
        for k, c in _VERDICT_COLORS:
            if k in str(name):
                return c
        return "grey"
    except Exception:
        return "grey"


def verdict_color_streak():
    """Consecutive most-recent nights the same LEAK COLOR tops the book —
    coarser than the class streak: stayed-too-long and bought-the-top are
    different classes but the same red leak. (streak, color, latest usd).
    Green is never a leak (a week of goals is good news, not an alarm)
    and grey is not a color — both return a 0 streak. Never raises."""
    try:
        hist = verdict_history()
        if not hist:
            return 0, "", 0.0
        col = verdict_color(hist[-1][1])
        if col in ("grey", "green"):
            return 0, col, 0.0
        n = 0
        for _d, name, _u in reversed(hist):
            if verdict_color(name) == col:
                n += 1
            else:
                break
        return n, col, float(hist[-1][2])
    except Exception:
        return 0, "", 0.0


def verdict_top_three() -> list | None:
    """The report's top three patterns for her voice (user, Sep 22: speak
    the top three when the #1 has held three nights running): the three
    costliest classes from _verdict_study.summary() — the same ranking
    the nightly report writes — as voice-safe short names with dollars
    and trade counts. None when the book is clean or unreadable. Never
    raises."""
    try:
        import _verdict_study as _vs
        s = [r for r in (_vs.summary() or []) if float(r[2]) > 0][:3]
        if not s:
            return None
        return [(str(k).split(" - ")[0], int(n), float(u)) for k, n, u in s]
    except Exception:
        return None


VERDICT_TOP3_STREAK_N = 3   # user, Sep 22: three nights running earns the top three

# the widths the nightly report sims (the study's own armed-trail sweep)
TRAIL_SIM_ABLE = (1.0, 1.5, 2.0, 3.0)


def trail_proposal(name: str) -> dict | None:
    """The verdict-cost study's trail sim, focused on ONE pattern (user,
    Sep 22: when the week warning fires, propose the specific dial). Sims
    the study's POST-GOAL-ARMED trail — the live machinery's shape, the
    stop exists only after the peak reaches 90% of the trade's goal — at
    the report's own widths over the streak pattern's trades, and returns
    {width, delta, n, proposal} for the best POSITIVE edge: per-trade
    improvement first, the took-the-goal control (a good dial must not
    break what works) as a guard. None (fail-soft, never raises) when the
    study can't read its ledgers, the pattern has no trades, or no width
    shows a positive edge — she stays silent rather than propose a dial
    the sim can't defend."""
    try:
        import _verdict_study as _vs
        graded = _vs.grade_trades()
        rows = [g for g in graded
                if str(g["verdict"]).split(" - ")[0] == name]
        if not rows:
            return None
        ctrl = [g for g in graded if "took the goal" in g["verdict"]]
        best = None
        for _P in TRAIL_SIM_ABLE:
            _d, _n = _vs._sim_armed(rows, _P)
            if _n and _d > 0:
                _per = _d / _n
                _dc_, _nc = _vs._sim_armed(ctrl, _P)
                _per_c = (_dc_ / _nc) if _nc else 0.0
                if _per_c < 0:                 # a good dial must not break the control
                    continue
                if best is None or _per > best["per"]:
                    best = {"width": _P, "delta": _d, "n": _n,
                            "per": _per}
        if not best:
            return None
        best["proposal"] = (f"the sim says a {best['width']:.1f}% armed trail "
                            f"would have kept {best['per']:.0f} dollars more "
                            f"per trade across {best['n']} "
                            f"trade{'s' if best['n'] != 1 else ''} — "
                            f"the dial change to make")
        return best
    except Exception:
        return None


def _worth_ladder_ingest(result: dict) -> None:
    """THE LEARNING LADDER'S INGEST SEAM (user spec, Oct 5): feed one
    closed rotation to the worth ladder. Only a PROVEN rotation -
    realized > 0 AND the table drained to the crumb - ever mints a
    floor candidate; an Unproven rotation renders as such and mints
    nothing. QC holds every apply. Fail-soft and flag-gated: a dead or
    disabled ladder never blocks the score (the flag IS the canary
    lever, and off is today's behavior byte for byte)."""
    try:
        import worth_ladder as _wl
        if not _wl.worth_ladder_enabled():
            return
        _rid = (str(result.get("date") or "") + "-"
                + str(result.get("rot") or ""))
        _wl.propose_from_rotation(
            rotation_id=_rid, score=result.get("score") or 0.0,
            realized_usd=result.get("realized_pnl") or 0.0,
            total_left_usd=result.get("total_left") or 0.0,
            current_floor=_wl.current_floor_frac())
    except Exception:
        pass


def _worth_ladder_reconcile(state: dict) -> None:
    """THE RECONCILER PASS AT COOLDOWN (hard rule 5): every history
    entry the money has since proven flips to Proven once - audit row
    verdict_flip: proven_by_realization, plus the re-evaluated
    candidate for the NEXT day's floor. Idempotent by the ladder's own
    ledger; fail-soft and flag-gated exactly like the ingest."""
    try:
        import worth_ladder as _wl
        if not _wl.worth_ladder_enabled():
            return
        _floor = _wl.current_floor_frac()
        for _h in (state.get("history") or [])[-18:]:
            _rid = (str(_h.get("date") or "") + "-"
                    + str(_h.get("rot") or ""))
            _wl.reconciler_flip(
                rotation_id=_rid,
                realized_usd=float(_h.get("realized_pnl") or 0.0),
                total_left_usd=float(_h.get("total_left") or 0.0),
                current_floor=_floor)
    except Exception:
        pass
