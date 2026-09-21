"""diag_directiongrade.py — THE DIRECTION CROSS-CHECK, GRADED (Sep 18).

WHY THIS EXISTS: on the UNI morning (+33% in 24h, +$208 of hindsight
ceiling), the cheap quick-read answered `direction` — not-buy — on the
top mover every 2-minute pass, and that answer STOPPED EVERYTHING: the
brain's full-history evaluator was never asked at the good entries.
The replay proved the brain would have bought at 09:00 and 10:00 UTC.
A cheap veto that outranks an expensive reader is exactly the kind of
decision that must be priced, not trusted.

THE MACHINERY (two halves):
  1. CAPTURE (background_scanner): when the head-of-class verdict on
     the TOP mover is a direction-stage not-buy, the scanner
     cross-checks the brain on the SAME window (the funnel's own df,
     no extra API calls) and appends a row to direction_notes.jsonl —
     the disagreement record. 4-hour per-pair cooldown; fail-closed
     (any error = no row, the scan never notices).
  2. GRADE (this module, monthly on the boot family): each row is
     replayed on the deep 1h store — the brain re-evaluates the full
     history UP TO the row's hour bucket, and the forward window is
     priced in the popup convention: end-move at the full position
     (what the brain's entry would have paid), ran = +2% MFE within
     the 4-bar horizon, ceiling = position x 2% for each ran. Bands
     group rows by the brain's replayed conviction (0/5/10/20/35) so
     the desk can read WHERE the brain's buys would have paid.

THE ACCOUNTING PRINCIPLE: only brain-BUY rows are money the veto
refused — a brain-hold row is the cheap read being RIGHT (both said
no) and prices as confirmation, not loss. Hold rows' forward windows
are still reported per band, because "the brain said hold and it ran
anyway" is the evidence that the brain's OWN bar misses runners.

Hermetic by construction: stores_dir/position injectable, write=False
returns the summary untouched. The study prices candles, never trades.
"""
from __future__ import annotations

import glob
import json
import logging
import time
import datetime as _dt
from pathlib import Path

BASE = Path(__file__).resolve().parent
NOTES = BASE / "direction_notes.jsonl"
OUT = BASE / "_directiongrade_check.txt"
LEDGER = BASE / "_directiongrade_monthly.jsonl"
CHASE_LEDGER = BASE / "_chaseback_monthly.jsonl"   # the missed pool's source
SUPPRESSIONS = BASE / "popup_suppressions.jsonl"   # the funnel's burial ledger

# THE DEEP LOG WINDOW (Sep 20): the rotated-log replay leg keeps its own
# window — the rotation retention (14d) — not the notes-feed's 7d. The
# direction gate was born Sep 12 and the brain cross-check only Sep 16,
# so the sell-veto bands' longest honest sample lives in Sep 12-15's
# log lines; a 7d window excludes them and the bands form on days of
# data instead of weeks. (Sep 13's rotation is already gone.) The JSONL
# leg stays on `days`: its rows are exact, and the freshness that
# matters there is the feed's own.


LOG_REPLAY_DAYS_DEFAULT = 14.0   # scanner.log rotations kept (TimedRotating backupCount)

# The gates whose rows ARE a brain direction stance at a vetoed hour
# (see the family note in the backfill below).
_DIRECTION_STAGE_GATES = frozenset({
    "deep-direction", "sure-thing-deep-veto", "deep-veto"})


def backfill_notes_from_suppressions(
        sup_path: Path | None = None, notes_path: Path | None = None,
        now: float | None = None, days: float = 7.0,
        dry_run: bool = False,
        log_glob: str | None = None,
        log_days: float | None = None) -> dict:
    """THE BACKFILL (Sep 18): the funnel's deep-direction burial rows are
    direction-veto events — but the cross-check capture (note_
    direction_veto) only fires on the TOP mover, so most vetoed hours
    never got a note and the monthly grade starts empty. This replays
    the burial ledger into the notes feed so the FIRST grade has data
    on day one.

    Two sources (Sep 19, the backfill-to-the-bar pass):
      1. the JSONL ledger — the direction-stage family's rows
         (_DIRECTION_STAGE_GATES: deep-direction, sure-thing-deep-veto,
         deep-veto; widened Sep 20 so the SELLVETO_REVIEW bar forms on
         every brain-stance burial, not just the funnel's). The stance
         text lives in `detail` (the _log_suppression signature: gate,
         detail, **ctx); the original code read `why`, which every row
         is missing — ALL backfilled rows classified as hold, 13 real
         sells invisible. Reads detail with a why fallback.
      2. the dated rotated logs (scanner.log.YYYY-MM-DD) — the ledger
         was born Sep 16; the "DEEP DIRECTION VETO (desktop)" log line
         reaches back to Sep 12. Each line is parsed into a note dated
         by its rotation file (+ the fleet's fixed -06:00 offset), so
         the pre-ledger days grade too. Wall-clock minute precision;
         the JSONL's exact stamps win by the (pair, hour) dedupe.
         (Sep 20: this leg's window is `log_days` — defaulting to
         LOG_REPLAY_DAYS_DEFAULT, 14d of rotations — so the gate's
         first week backfills the sell-veto bands; `days` still bounds
         the JSONL leg and the existing callers.)

    One brain stance per (pair, hour): notes dedupe on the hour bucket,
    and the suppression rows repeat within an hour — collapse first.
    Idempotent: any (pair, hour) already in the notes is skipped. Non-
    buy rows are KEPT. Hermetic: injectable paths, dry_run counts
    without writing. Never raises."""
    import time as _t
    import re as _re
    sup = Path(sup_path) if sup_path is not None else SUPPRESSIONS
    notes = Path(notes_path) if notes_path is not None else NOTES
    nowt = _t.time() if now is None else float(now)
    _log_days = (LOG_REPLAY_DAYS_DEFAULT if log_days is None
                 else float(log_days))
    seen_notes: set[tuple] = set()
    if notes.exists():
        for line in notes.read_text(encoding="utf-8",
                                    errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ts = _dt_parse(d.get("ts"))
                if ts is None:
                    continue
                seen_notes.add((str(d.get("pair")), int(ts // 3600)))
            except Exception:
                continue
    cand: dict[tuple, dict] = {}
    scanned = 0
    log_scanned = 0
    try:
        raw = sup.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        # THE DIRECTION-STAGE FAMILY (Sep 20, the review bar's accelerating
        # backfill): three gates bury a candidate because the BRAIN's deep
        # read contradicted the setup — deep-direction (the funnel's
        # direction stage), sure-thing-deep-veto (the same brain read
        # against a Sure Thing coil), deep-veto (the bridge/desktop veto
        # on movers). Every one is a real brain stance at a vetoed hour;
        # the sell_veto_bands, the SELLVETO_REVIEW row rung, and the
        # relaxation sweep all count graded sell rows, so excluding the
        # two sibling gates made the threshold form on a crippled sample
        # (18 of 23 harvestable sells were invisible). The stance text
        # conventions differ slightly ("the scan contradicts the buy" vs
        # "...the breakout" vs "mover: ...the brain says no") but the
        # sell marker — deep-reads sell/short — is identical; the stance
        # parser below is shared. Non-direction gates (tide-out,
        # worth-floor, climb-*, thin/watch/book/bridge stages) stay out:
        # their rows either carry no brain stance or veto on sizing and
        # trend shape, not direction.
        if str(r.get("gate") or "") not in _DIRECTION_STAGE_GATES:
            continue
        ts = _dt_parse(r.get("ts"))
        if ts is None or (nowt - ts) > days * 86400.0:
            continue
        scanned += 1
        key = (str(r.get("pair")), int(ts // 3600))
        if key in seen_notes or key in cand:
            continue
        try:
            brain_conf = float(r.get("brain"))
        except (TypeError, ValueError):
            continue
        # THE VETO'S OWN DIRECTION (Sep 19): the stance text lives in
        # `detail` (the _log_suppression signature) — `why` is never
        # written; the original why-only read classified every row as
        # hold and hid the sells. detail first, why as a fallback.
        _txt = str(r.get("detail") or r.get("why") or "")
        try:
            quick_conf = float(r.get("quick"))
        except (TypeError, ValueError):
            quick_conf = None
        cand[key] = {
            "ts": _dt.datetime.fromtimestamp(
                ts, _dt.timezone.utc).isoformat(),
            "pair": key[0],
            "quick_action": "hold",
            "quick_conf": quick_conf,
            "brain_action": "hold",
            "brain_conf": round(brain_conf, 1),
            "brain_why": "",
            "close": None,
            "bars": 0,
            "why": "backfill: direction-stage burial "
                   f"({str(r.get('gate'))} in the suppression ledger)"}
        _m = _re.search(r"deep-?reads?\s+(sell|short)", _txt,
                        _re.IGNORECASE)
        if _m:
            cand[key]["brain_action"] = "sell"
            cand[key]["brain_why"] = _txt[:80]
    # SOURCE 2 — the dated rotated logs (the pre-ledger days). The
    # line: "12:00 AM [INFO]   DEEP DIRECTION VETO (desktop): INJ
    # deep-reads hold 40% — the scan contradicts the buy". Dated by
    # the rotation file's name; stamped at the fleet's fixed -06:00
    # offset (the ledger's own convention).
    import os as _osmod
    _lg = log_glob if log_glob is not None else "scanner.log.20*"
    _off6 = _dt.timezone(_dt.timedelta(hours=-6))
    _line_re = _re.compile(
        r"(\d{1,2}):(\d{2}) (AM|PM)\s+\[\w+\]\s+DEEP DIRECTION VETO "
        r"\(desktop\): (\S+) deep-reads (sell|short|hold) "
        r"(\d+(?:\.\d+)?)%\s*—", _re.IGNORECASE)
    try:
        # the glob module (not Path().glob): accepts absolute patterns,
        # which the hermetic pins inject via tmpdir paths
        _log_paths = sorted(glob.glob(_lg))
    except Exception:
        _log_paths = []
    for _lps in _log_paths:
        _lp = Path(_lps)
        _dm = _re.search(r"(\d{4})-(\d{2})-(\d{2})$", _lp.name)
        if not _dm:
            continue
        _y, _mo, _dy = (int(_dm.group(1)), int(_dm.group(2)),
                        int(_dm.group(3)))
        try:
            _lraw = _lp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _line in _lraw.splitlines():
            if "DEEP DIRECTION VETO (desktop):" not in _line:
                continue
            _m = _line_re.search(_line)
            if not _m:
                continue
            _hh, _mm, _ap, _asset, _act, _conf = (_m.group(1), _m.group(2),
                                                  _m.group(3).upper(),
                                                  _m.group(4),
                                                  _m.group(5).lower(),
                                                  _m.group(6))
            _h12 = int(_hh) % 12 + (12 if _ap == "PM" else 0)
            _ts = _dt.datetime(_y, _mo, _dy, _h12, int(_mm),
                               tzinfo=_off6).timestamp()
            if (nowt - _ts) > _log_days * 86400.0:
                continue
            log_scanned += 1
            key = (f"{_asset}/USD", int(_ts // 3600))
            if key in seen_notes or key in cand:
                continue
            _why = (f"deep-reads {_act} {_conf}% — the scan "
                    "contradicts the buy")
            cand[key] = {
                "ts": _dt.datetime.fromtimestamp(
                    _ts, _dt.timezone.utc).isoformat(),
                "pair": key[0],
                "quick_action": "hold",
                "quick_conf": None,
                "brain_action": ("sell" if _act in ("sell", "short")
                                 else "hold"),
                "brain_conf": round(float(_conf), 1),
                "brain_why": _why[:80],
                "close": None,
                "bars": 0,
                "why": "backfill: rotated-log DEEP DIRECTION VETO "
                       f"({_lp.name})"}
    added = sorted(cand.values(), key=lambda x: x["ts"])
    if not dry_run and added:
        lines = []
        if notes.exists():
            lines = [l for l in notes.read_text(encoding="utf-8",
                                                errors="replace")
                     .splitlines() if l.strip()]
        lines.extend(json.dumps(a, ensure_ascii=False, default=str)
                     for a in added)
        _tmp = str(notes) + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-2000:]))
        import os as _os
        _os.replace(_tmp, str(notes))
    return {"scanned": scanned, "added": len(added),
            "skipped_hours": scanned - len(added), "rows": added,
            "log_scanned": log_scanned}


def _dt_parse(ts) -> float | None:
    """ISO timestamps (any offset) or epochs -> epoch seconds; None on
    junk (the capture writers never emit one, but drift happens)."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        pass
    try:
        return _dt.datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return None
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"

WARMUP = 60           # bars of history before a brain read (courtfloor's floor)
HORIZON = 4           # the popup contract's 4-bar forward window
RAN_PCT = 2.0         # "it ran" = +2% MFE within the horizon (coin_floors' bar)

MONTHLY_PERIOD_S = 30 * 86400.0
FIRST_DELAY_S = 3600.0
POSITION_USD_FALLBACK = 1000.0
POSITION_USD = POSITION_USD_FALLBACK

# ── THE FOLLOW-THROUGH GATE (Sep 18): the bar the brain's high-
# conviction buys must clear before the scanner is ALLOWED to
# auto-queue direction-vetoed top movers into the next pass's deep-read
# queue. N_MIN high-band rows (fewer = a hot streak, not a regime) with
# BUY_RATE_MIN of them brain-buys AND ran-rate BUY_RAN_MIN — all three
# from the LIVED grade, never the sim. Every dial here is evidence,
# not preference.
FT_BAND = "35"        # the brain's top conviction band
FT_N_MIN = 10         # fewer rows = too thin to arm anything
FT_BUY_RATE_MIN = 0.60
FT_RAN_MIN = 0.30

# THE SELL-VETO BAND SPLIT (Sep 19): a band needs this many graded
# sell rows before its net can earn the conviction crossover — one hot
# hour never sets the threshold.
SELL_BAND_MIN_N = 3

# brain-conviction bands (the replayed brain's confidence): 0-4 hold-zone,
# 5/10/20/35 = the buy ladder's rungs. A row lands in the highest band it
# clears — the band IS the brain's stance at that hour.
BANDS = ((0.0, "0"), (5.0, "5"), (10.0, "10"), (20.0, "20"), (35.0, "35"))

logger = logging.getLogger("directiongrade")


def _position_usd() -> float:
    """Today's ledger (the live contest size) with the study's own
    fallback — the family convention (chaseback/courtfloor)."""
    try:
        import background_scanner as _bs
        _s = float(_bs._contest_size())
        if _s > 0:
            return _s
    except Exception as exc:
        logger.debug("live contest size unreadable (%s) — the fallback "
                     "$%.0f stands", exc, POSITION_USD_FALLBACK)
    return POSITION_USD_FALLBACK


def _grade_one(path: Path, ts_epoch: float, now: float | None = None) -> dict | None:
    """One row replayed on its deep store: the brain's read of the full
    history up to the row's hour bucket + the forward window priced in
    the popup convention. None when the store cannot answer (missing,
    too short, row outside its span, horizon unclosed, or the
    horizon's last bar still FORMING — a partial bar's close drifts and
    re-prices the row pass to pass; the guard keeps every graded number
    final)."""
    import pandas as _pd
    from strategy import evaluate_strategy
    from config import DEFAULT_CONFIG as cfg
    try:
        df = _pd.read_feather(path)
        df["date"] = _pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date").sort_index()
        if len(df) <= WARMUP + HORIZON + 2:
            return None
        # the epoch cast is the chaseback regression: naive astype('int64')
        # returned µs-scale ints on this stack — 1000x off, every lookup
        # dead. datetime64[s] is the unambiguous seconds cast (pinned).
        # The stores are tz-aware (to_datetime utc=True), which rejects a
        # direct naive cast — convert to UTC, drop tz, then cast.
        _ix = df.index
        if getattr(_ix, "tz", None) is not None:
            _ix = _ix.tz_convert("UTC").tz_localize(None)
        epochs = _ix.astype("datetime64[s]").astype("int64")
        pos = int(epochs.searchsorted(int(ts_epoch), side="right")) - 1
        if pos < WARMUP or pos + HORIZON >= len(df):
            return None
        # THE CLOSED-BAR GUARD (Sep 19): the horizon's LAST bar must be
        # complete. The 5m extender writes the forming hour's 1h bar, so
        # a pass can price a row against a bar whose close is still
        # moving — the 16:04Z -> 17:06Z ledger wobble (buys$ 208.36 ->
        # 188.63, a ran-count flip) was exactly that. Ungraded until the
        # bar closes; the grade never re-prices a closed row.
        _nowt = time.time() if now is None else float(now)
        if int(epochs[pos + HORIZON]) + 3600 > _nowt:
            return None
        entry = float(df["close"].iloc[pos])
        fwd = df.iloc[pos + 1: pos + 1 + HORIZON]
        end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
        ran = bool((float(fwd["high"].max()) / entry - 1.0) * 100.0
                   >= RAN_PCT)
        brain = evaluate_strategy(df.iloc[: pos + 1].copy(), cfg)
        bconf = float(getattr(brain, "confidence", 0.0) or 0.0)
        return {"entry": round(entry, 6),
                "end": round(end, 2),
                "ran": ran,
                "brain_action": str(getattr(brain, "action", "hold")),
                "brain_conf": round(bconf, 1),
                "hour_open": int(epochs[pos]),
                "lag_min": round((float(ts_epoch) - int(epochs[pos]))
                                 / 60.0, 1)}
    except Exception as exc:
        logger.debug("grade failed for %s @%s: %s", path.name, ts_epoch, exc)
        return None


def _band_of(bconf: float) -> str:
    """The highest brain-conviction band the row clears."""
    key = BANDS[0][1]
    for lo, k in BANDS:
        if bconf >= lo:
            key = k
    return key


def _world(rows: list) -> dict:
    """One band's ledger: rows, brain buys (the veto's refused money),
    brain holds (the veto's confirmations), and brain SELLS — the
    funnel's direction vetoes (Sep 19, user: "did the sells the funnel
    refused ever run after being passed over?"). A replayed SELL is not
    a hold: end < 0 means the sell was RIGHT (the veto saved the long's
    loss), end >= 0 or ran means the sell was WRONG (the refused buy's
    money). Pricing is the same long-side convention everywhere: usd =
    the end-move dollars the refused BUY would have made/lost; ceiling
    = the ran money."""
    w = {"n": 0, "buys": 0, "buys_ran": 0, "buys_usd": 0.0,
         "buys_ceiling": 0.0, "holds": 0, "holds_ran": 0, "holds_usd": 0.0,
         "sells": 0, "sells_ran": 0, "sells_usd": 0.0,
         "sells_ceiling": 0.0}
    for r in rows:
        w["n"] += 1
        usd = POSITION_USD * float(r["end"]) / 100.0
        ceil = POSITION_USD * RAN_PCT / 100.0 if r["ran"] else 0.0
        if r["brain_action"] == "buy":
            w["buys"] += 1
            w["buys_ran"] += int(r["ran"])
            w["buys_usd"] += usd
            w["buys_ceiling"] += ceil
        elif r["brain_action"] == "sell":
            w["sells"] += 1
            w["sells_ran"] += int(r["ran"])
            w["sells_usd"] += usd
            w["sells_ceiling"] += ceil
        else:
            w["holds"] += 1
            w["holds_ran"] += int(r["ran"])
            w["holds_usd"] += usd
    for k in ("buys_usd", "buys_ceiling", "holds_usd",
              "sells_usd", "sells_ceiling"):
        w[k] = round(w[k], 2)
    return w


def _sell_verdict(rows: list, position_usd: float) -> dict:
    """THE SELLS' VERDICT (Sep 19, pure — hermetic-pin-able like the
    other helpers): given the graded rows whose replayed brain action
    is SELL, price the direction veto's hindsight. Long-side convention:
    each row's `end` is what the REFUSED BUY would have made/lost.
    right = ends below zero (the sell call held — the veto saved
    position_usd * |end|); wrong = ends at/above zero (refused money,
    plus the ran ceiling). net = refused - saved: negative means the
    sell vetoes PAID (saved more than they refused)."""
    n = len(rows)
    ran = sum(int(bool(r["ran"])) for r in rows)
    saved = round(-sum(position_usd * float(r["end"]) / 100.0
                       for r in rows if float(r["end"]) < 0.0), 2)
    refused = round(sum(position_usd * float(r["end"]) / 100.0
                        for r in rows if float(r["end"]) >= 0.0), 2)
    ceiling = round(sum(position_usd * RAN_PCT / 100.0
                        for r in rows if r["ran"]), 2)
    return {"n": n, "ran": ran,
            "ran_rate": (round(ran / n, 3) if n else None),
            "refused_usd": refused, "refused_ceiling": ceiling,
            "right": sum(1 for r in rows if float(r["end"]) < 0.0),
            "saved_usd": saved,
            "net_usd": round(refused - saved, 2)}


def _sell_verdict_by_band(sell_rows: list, position_usd: float,
                          min_n: int = SELL_BAND_MIN_N) -> dict:
    """THE SELL VETO, SPLIT BY BAND (Sep 19, pure — hermetic-pin-able):
    the pooled sell verdict answers 'do the vetoes pay overall'; this
    split answers the question she actually asked — at WHAT brain
    conviction does the brain's sell deserve the power to kill a green
    light? Each sell row lands in the highest BANDS rung its
    brain_conf clears (the same ladder the buy side grades), priced by
    the same long-side convention as _sell_verdict: end < 0 = the sell
    was RIGHT (saved losses), end >= 0 or ran = WRONG (refused money).
    Returns per-band verdict dicts plus `crossover` — the lowest band
    whose net_usd < 0 (the vetoes PAID) on a qualifying sample. That
    band's floor IS the emerged conviction threshold: below it the
    brain's sell has no priced teeth, at/above it the veto earns its
    override. Bands missing rows render as n=0 (fail-open); min_n
    guards the crossover against one hot hour (a band of SELL_BAND_MIN_N
    rows or fewer never earns the threshold, whatever its net).
    Never raises."""
    by_band: dict[str, list] = {}
    for r in sell_rows:
        by_band.setdefault(_band_of(float(r.get("brain_conf") or 0.0)),
                           []).append(r)
    bands = {k: _sell_verdict(by_band.get(k) or [], position_usd)
             for _, k in BANDS}
    crossover = None
    for _lo, _k in BANDS:
        b = bands[_k]
        if b["n"] >= min_n and b["net_usd"] < 0.0:
            crossover = _k
            break
    return {"bands": bands, "crossover": crossover, "min_n": min_n}


# ── THE LEARNED VETO BAR (Sep 20, user: "Rebuild the Sure Thing deep
# veto data-driven: honor sell-vetoes only above a learned conviction
# bar, pass low-confidence holds, and ship it with the monthly re-fit
# machinery") ─────────────────────────────────────────────────────────
# The Sure Thing's deep veto (background_scanner 7168) kills any coil
# whose deep read isn't a buy, at ANY confidence — while the band
# evidence says the cheap sells have no priced teeth. The crossover
# band's floor IS the emerged bar: sells at/above it have priced teeth
# (their band's veto pays), below it they pass and the coil fires.
# DEFAULT_VETO_BAR=0.0 keeps today's behavior (every sell vetoes) until
# the monthly re-fit ships a learned value.
DEFAULT_VETO_BAR = 0.0
VETO_BAR_JSON = BASE / "veto_bar.json"


def learned_veto_bar(svb: dict, default: float = DEFAULT_VETO_BAR) -> dict:
    """The veto bar the band evidence earns (pure — hermetic-pin-able).
    crossover names the lowest band whose sell-veto net pays (net < 0)
    on >= min_n rows; that band's floor (BANDS lo) is the bar: a sell at
    or above it carries priced teeth, below it the brain's stance has
    never priced as a veto. No crossover (thin samples, or every band
    costs) means the evidence cannot set the bar: the default (0.0)
    ships — fail-conservative, every sell still vetoes. Never raises."""
    try:
        _x = (svb or {}).get("crossover")
        if _x is None:
            return {"bar": default, "source": "default",
                    "reason": "no band crossover yet (sample or "
                              "evidence) — every sell still vetoes"}
        for _lo, _k in BANDS:
            if _k == _x:
                return {"bar": _lo, "source": "learned",
                        "reason": (f"band {_x}'s sell-veto net pays on "
                                   f">={(svb or {}).get('min_n')} rows — "
                                   "its floor is the conviction bar")}
        return {"bar": default, "source": "default",
                "reason": "crossover band unknown — default stands"}
    except Exception:
        return {"bar": default, "source": "default",
                "reason": "evidence unreadable — default stands"}


def ship_veto_bar(learned: dict, path=None) -> bool:
    """The monthly re-fit's SHIP: stamp + write the bar the scanner's
    Sure Thing deep veto reads. Returns False without raising when the
    write fails — a failed ship leaves the previous bar standing."""
    _doc = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            **learned}
    try:
        (path or VETO_BAR_JSON).write_text(json.dumps(_doc),
                                           encoding="utf-8")
        return True
    except OSError:
        return False


def read_veto_bar(path=None) -> float:
    """The reader the scanner mirrors: the shipped bar, or the default
    (0.0 — every sell vetoes) when nothing shipped yet, the file is
    unreadable, or the value is malformed/negative. Never raises."""
    try:
        _doc = json.loads((path or VETO_BAR_JSON).read_text(
            encoding="utf-8"))
        _v = float(_doc.get("bar"))
        return _v if _v >= 0.0 else DEFAULT_VETO_BAR
    except Exception:
        return DEFAULT_VETO_BAR


# ── THE VETO RELAXATION (Sep 20, her question: "since even band-35
# sell vetoes cost money, price what relaxing the deep-direction veto
# would have earned and design the relaxation rule"). Two design
# decisions before any number:
#   THE RUNNER DISCIPLINE — a relaxed vetoed entry exits at the
#   fleet's own runner rule (the +4% the ran-ceiling already speaks);
#   relaxing never converts a refused winner into an unmanaged ride.
#   The loss side is NOT capped: a stop has to trade to exist, and the
#   vetoed hour that broke the re-entry test can break again — the
#   losses_eaten line is the honest minus every world must carry.
#   THE DIAL — relax only sells BELOW a brain-conviction bar, keeping
#   the high-conviction vetoes' teeth. Never auto-applied (the
#   pool-gate precedent): the grade recommends, she ships.
RELAX_RUNNER_CAP_PCT = RAN_PCT   # the fleet's own runner exit
# The dial lives in the SELL-confidence domain (the brain's own stance
# conviction at the vetoed hour — the Sep 20 sample clusters 0-50, so
# buy-ladder values 65+ bind nothing and are dead settings):
#   None = relax every sell veto; 50/40/30/20 = keep the vetoes at or
#   above that conviction, relax everything below.
RELAX_CONF_DIAL = (None, 50.0, 40.0, 30.0, 20.0)


def relax_exit_pct(end_pct: float,
                   cap_pct: float | None = RELAX_RUNNER_CAP_PCT) -> float:
    """The relaxed entry's exit move: the row's end, capped at the
    runner exit when the world uses the runner discipline. cap_pct=None
    is the keep world (ride to the horizon, uncapped). Pure."""
    end = float(end_pct)
    if cap_pct is None:
        return end
    return min(end, float(cap_pct))


def _relax_world(sell_rows: list, position_usd: float,
                 cap_pct: float | None = RELAX_RUNNER_CAP_PCT,
                 conf_max: float | None = None) -> dict:
    """One relaxation world, priced row by row (pure — the pins ride
    hand arithmetic). conf_max=None relaxes every sell row; a number
    relaxes only sells BELOW that brain conviction. The veto itself
    trades nothing, so earned_usd IS the world's delta vs the lived
    rule; losses_eaten_usd names what the world takes on where the
    veto was right."""
    rows = [r for r in sell_rows
            if conf_max is None
            or float(r.get("brain_conf") or 0.0) < float(conf_max)]
    earned = released = eaten = 0.0
    overridden_right = 0
    for r in rows:
        end = float(r["end"])
        if end >= 0.0:
            released += position_usd * end / 100.0
        else:
            eaten += position_usd * abs(end) / 100.0
            overridden_right += 1
        earned += position_usd * relax_exit_pct(end, cap_pct) / 100.0
    return {"world": ("timestop" if cap_pct is None else "runner"),
            "conf_max": conf_max, "n_relaxed": len(rows),
            "earned_usd": round(earned, 2),
            "released_usd": round(released, 2),
            "losses_eaten_usd": round(eaten, 2),
            "right_vetoes_overridden": overridden_right}


def _relax_sweep(sell_rows: list, position_usd: float) -> dict:
    """THE SWEEP (Sep 20): the designed rule's dial crossed with the
    two exit worlds, priced on the same graded sell rows as the band
    split. `recommendation` speaks only when a world prices positive
    (the veto cost money — that is the premise being tested), names
    the dial setting, the right-vetoes it overrides, and the sample's
    honesty (below the SELLVETO_REVIEW bar it says watch, not ship).
    Never raises."""
    try:
        worlds = []
        for conf_max in RELAX_CONF_DIAL:
            for cap in (None, RELAX_RUNNER_CAP_PCT):
                worlds.append(_relax_world(
                    sell_rows, position_usd, cap_pct=cap,
                    conf_max=conf_max))
        best = None
        for w in worlds:
            if w["earned_usd"] > 0.0 and (best is None
                                          or w["earned_usd"]
                                          > best["earned_usd"]):
                best = w
        n = len(sell_rows)
        rec = None
        if best is not None:
            # THE LADDER GATE (Sep 20): the sweep's verdict answers to
            # the same rung the SELLVETO_REVIEW does — a relaxation
            # judged on fewer than 30 graded sell rows is WATCH, never
            # SHIP-CANDIDATE, even when every world prices positive.
            try:
                from evidence_ladder import rows_mature as _rm
                _mature = _rm("SELLVETO_REVIEW", n)
            except Exception:
                _mature = n >= 30
            _dial = ("every sell veto" if best["conf_max"] is None
                     else f"sell conf < {best['conf_max']:g}")
            _disc = ("a 6h time-stop exit" if best["world"] == "timestop"
                     else f"the +{RELAX_RUNNER_CAP_PCT:g}% runner exit")
            rec = (f"{'SHIP-CANDIDATE' if _mature else 'WATCH, DONT SHIP'}: "
                   f"relax {_dial} with {_disc} prices "
                   f"+${best['earned_usd']:,.2f} on {best['n_relaxed']} of "
                   f"{n} sell rows ({best['right_vetoes_overridden']} "
                   f"right vetoes overridden, "
                   f"${best['losses_eaten_usd']:,.2f} losses eaten) — "
                   "re-price monthly across windows before any ship")
        return {"n": n, "worlds": worlds, "recommendation": rec}
    except Exception as exc:
        logger.debug("relaxation sweep failed: %s", exc)
        return {"n": len(sell_rows), "worlds": [], "recommendation": None}


def _chase_missed_pool(ledger_path: Path | None = None) -> dict:
    """The chase-coverage study's MISSED pool, read from its ledger (the
    file is one pretty-printed snapshot record, os.replace'd each pass —
    but concatenated records are tolerated via raw_decode so the reader
    never breaks on format drift). Returns {'ts', 'n', 'rate', 'usd_turn'}
    with Nones when the ledger is missing/unreadable — fail-open; the
    weekly comparison speaks only with a readable pool."""
    p = ledger_path if ledger_path is not None else CHASE_LEDGER
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ts": None, "n": None, "rate": None, "usd_turn": None}
    dec = json.JSONDecoder()
    i, last = 0, None
    while i < len(raw):
        while i < len(raw) and raw[i] in " \r\n\t":
            i += 1
        if i >= len(raw):
            break
        try:
            obj, j = dec.raw_decode(raw, i)
        except ValueError:
            break
        last = obj
        i = j
    if not isinstance(last, dict):
        return {"ts": None, "n": None, "rate": None, "usd_turn": None}
    m = last.get("missed") or {}
    mn = int(m.get("n") or 0)
    return {"ts": last.get("ts"),
            "n": mn if mn else None,
            "rate": ((int(m.get("ran") or 0) / mn) if mn else None),
            "usd_turn": ((float(m.get("usd") or 0.0) / mn) if mn else None)}


def _detect_rearm(summary: dict | None) -> dict | None:
    """THE RE-ARM ALERT (Sep 19): the next time fresh vetoed hours push the
    band-35 brain-buy rate back above the 60% arming bar after a dip, the
    moment gets SPOKEN and priced. Stateless by design: the flip is read
    from the ledger's own last record (armed -> unarmed -> armed), so a
    scanner restart can neither suppress nor re-fire the alert. Fresh
    hours = capture rows newer than the prev record's ts (the n-delta
    lies when rows age out of the grade window). Returns the alert dict
    for the driver to voice, or None (no flip, no prev, or still
    unarmed). Never raises — a broken alert must never take down the
    grade's ledger write."""
    try:
        ft = (summary or {}).get("follow_through") or {}
        if not ft.get("armed"):
            return None
        prev = None
        _prev_ts = None
        if LEDGER.exists():
            _lines = [l for l in LEDGER.read_text(
                encoding="utf-8", errors="replace").splitlines()
                if l.strip()]
            if _lines:
                _d = json.loads(_lines[-1])
                if isinstance(_d, dict):
                    prev = _d.get("follow_through") or {}
                    _prev_ts = _d.get("ts")
        if prev and prev.get("armed"):
            return None
        # fresh capture rows since the prev record's ts (0 on first arm)
        n_new = 0
        _prev_epoch = _dt_parse(_prev_ts) if _prev_ts else None
        if prev and _prev_epoch and NOTES.exists():
            for line in NOTES.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    _nd = json.loads(line)
                    _nts = _dt_parse(str(_nd.get("ts") or ""))
                    if _nts is not None and _nts > _prev_epoch:
                        n_new += 1
                except Exception:
                    continue
        _rate_pct = round(ft.get("buy_rate") or 0, 2) * 100
        if prev:
            say = (f"The follow-through gate re-armed! {ft.get('buys')} of "
                   f"{ft.get('n')} vetoed hours say buy - "
                   f"{_rate_pct:.0f} percent, back above the 60 percent "
                   f"bar. {n_new} fresh hours since the dip. "
                   f"Listening for the first adoption.")
        else:
            say = (f"The follow-through gate armed! {ft.get('buys')} of "
                   f"{ft.get('n')} vetoed hours say buy - "
                   f"{_rate_pct:.0f} percent, above the 60 percent bar. "
                   f"Listening for the first adoption.")
        return {"say": say, "n": ft.get("n"), "buys": ft.get("buys"),
                "buy_rate": ft.get("buy_rate"), "ran_rate": ft.get("ran_rate"),
                "new_hours": n_new, "first_arm": not bool(prev)}
    except Exception:
        return None


def run_grade(max_rows: int | None = None, stores_dir: Path | None = None,
              position_usd: float | None = None, now: float | None = None,
              write: bool = True, chase_ledger: Path | None = None,
              return_rows: bool = False) -> dict:
    """Grade every capture row against its deep store. Bounds make it
    hermetic: max_rows slices, stores_dir/position injectable.
    return_rows=True (Sep 20) adds the graded rows themselves under
    'rows' — the relaxation study prices per-row, which the aggregates
    cannot reconstruct (the runner cap clips each row's capture
    individually); the ledger record is unchanged."""
    global POSITION_USD
    POSITION_USD = position_usd if position_usd is not None \
        else _position_usd()
    if now is None:
        now = time.time()
    # ── the rows (oldest first; junk lines tolerated) ──
    rows_raw: list[dict] = []
    if NOTES.exists():
        for line in NOTES.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                if isinstance(doc, dict) and doc.get("pair"):
                    rows_raw.append(doc)
            except Exception:
                continue
    rows_raw = rows_raw[-max_rows:] if max_rows else rows_raw
    # dedupe: one grade per (pair, hour bucket) — the cooldown gives 4h,
    # but a manual re-fire inside an hour must not double-count
    seen: set[tuple] = set()
    notes = []
    for d in rows_raw:
        try:
            ts = time.mktime(time.strptime(d["ts"][:19],
                                           "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(
                    str(d["ts"]).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
        key = (str(d["pair"]), int(ts // 3600))
        if key in seen:
            continue
        seen.add(key)
        d = dict(d)
        d["_ts"] = float(ts)
        notes.append(d)

    # ── the stores, by pair ──
    glob_pattern = (str(stores_dir / "*_USD-1h.feather") if stores_dir
                    else str(BASE / STORES_GLOB))
    by_pair = {}
    for p in sorted(glob.glob(glob_pattern)):
        stem = Path(p).name.replace("-1h.feather", "")
        # THE PAIR-FORMAT BRIDGE (Sep 18): the capture stores pairs in
        # the venue form ("ZEC/USD") while store stems use the file
        # form ("ZEC_USD") — index BOTH, or every note grades nowhere
        # (the latent miss the follow-through probe surfaced).
        by_pair[stem] = Path(p)
        by_pair[stem.replace("_", "/")] = Path(p)

    graded: list[dict] = []
    for d in notes:
        pair = str(d["pair"])
        store = by_pair.get(pair)
        if store is None:
            continue
        g = _grade_one(store, d["_ts"], now=now)
        if g is None:
            continue
        g["pair"] = pair
        g["quick_action"] = str(d.get("quick_action") or "hold")
        g["quick_conf"] = d.get("quick_conf")
        g["why"] = str(d.get("why") or "")[:80]
        g["ts"] = d["_ts"]
        graded.append(g)

    # ── the bands + the per-pair map ──
    bands: dict[str, dict] = {}
    for _lo, k in BANDS:
        bands[k] = _world([r for r in graded
                           if _band_of(float(r["brain_conf"])) == k])
    per_pair: dict[str, dict] = {}
    for r in graded:
        pp = per_pair.setdefault(r["pair"], {"n": 0, "brain_buys": 0,
                                             "brain_usd": 0.0,
                                             "brain_ceiling": 0.0})
        pp["n"] += 1
        usd = POSITION_USD * float(r["end"]) / 100.0
        if r["brain_action"] == "buy":
            pp["brain_buys"] += 1
            pp["brain_usd"] += usd
            pp["brain_ceiling"] += (POSITION_USD * RAN_PCT / 100.0
                                    if r["ran"] else 0.0)
    for pp in per_pair.values():
        pp["brain_usd"] = round(pp["brain_usd"], 2)
        pp["brain_ceiling"] = round(pp["brain_ceiling"], 2)

    # THE FOLLOW-THROUGH GATE (Sep 18): does the brain's high-conviction
    # buy pay CONSISTENTLY at the vetoed hours? The grade's own verdict,
    # computed from the lived band-35 world — the scanner reads this
    # field and only queues when it is True (and holds nothing when the
    # record is too thin: fail-closed).
    _ftb = bands.get(FT_BAND) or {}
    _ftn = int(_ftb.get("n") or 0)
    _ftrate = (_ftb.get("buys") / _ftn) if _ftn else 0.0
    _ftran = (_ftb.get("buys_ran") / _ftb.get("buys")
              if _ftb.get("buys") else 0.0)
    follow_through = {
        "band": FT_BAND, "n": _ftn, "buys": int(_ftb.get("buys") or 0),
        "buy_rate": round(_ftrate, 3), "ran_rate": round(_ftran, 3),
        "armed": bool(_ftn >= FT_N_MIN
                      and _ftrate >= FT_BUY_RATE_MIN
                      and _ftran >= FT_RAN_MIN)}

    # THE TWO BLIND SPOTS, WEEKLY (Sep 18): the brain-hold rows that RAN
    # anyway are the cheap read's own refused money — read them in the
    # same week-window convention as the chase study's w14, beside the
    # chase pool's missed world, so the two blind spots price together.
    from datetime import datetime as _dts, timezone as _tzc
    _wk = [g for g in graded
           if (now - float(g["ts"])) <= 7 * 86400.0]
    _wh = _world(_wk)
    _hr = (_wh["holds_ran"] / _wh["holds"]) if _wh["holds"] else None
    chase = _chase_missed_pool(chase_ledger)
    weekly_holds = {"n": _wh["n"], "holds": _wh["holds"],
                    "holds_ran": _wh["holds_ran"],
                    "holds_usd": _wh["holds_usd"],
                    "ran_rate": (round(_hr, 3) if _hr is not None else None),
                    "chase": chase}

    # THE SELLS' HINDSIGHT (Sep 19, user: "grade the direction-stage
    # rejections' hindsight monthly like the direction grade already
    # does — did the sells the funnel refused ever run after being
    # passed over?"): every graded row whose replayed brain action is
    # SELL is an hour the funnel refused a BUY because the brain said
    # sell. The same ledger, the same closed-bar guard, the same
    # monthly pass — nothing new to feed it.
    sell_vetoes = _sell_verdict(
        [g for g in graded if g["brain_action"] == "sell"],
        POSITION_USD)
    # THE BAND SPLIT (Sep 19, her question): the pooled verdict can hide
    # a ladder — cheap sells that cost and conviction sells that saved.
    # The split prices each band and, when the data supports it, names
    # the crossover: the emerged threshold where the brain's sell
    # starts paying as an override.
    sell_veto_bands = _sell_verdict_by_band(
        [g for g in graded if g["brain_action"] == "sell"],
        POSITION_USD)
    # THE RELAXATION SWEEP (Sep 20): the vetoes cost money — price the
    # worlds where they don't, and name the rule that would earn it.
    sell_veto_relax = _relax_sweep(
        [g for g in graded if g["brain_action"] == "sell"],
        POSITION_USD)
    # THE LEARNED VETO BAR (Sep 20): the crossover band's floor, learned
    # fresh every pass and SHIPPED to veto_bar.json — the scanner's Sure
    # Thing deep veto honors sells only at/above the bar and passes the
    # low-confidence holds. A failed ship is recorded, not raised.
    _learned_veto = learned_veto_bar(sell_veto_bands)
    if write:
        _learned_veto["shipped"] = ship_veto_bar(_learned_veto)
    # THE MATURITY GATE (Sep 19, her rule — surfaced, not noticed): the
    # review recommendation may only speak once the SELLVETO_REVIEW
    # row-rung is reached — 30+ graded sell rows. The ladder is the
    # single bar-owner; the grade counts and decides what to say; the
    # plate renders it. Below the bar the record carries the wait.
    try:
        from evidence_ladder import ROW_BARS, rows_mature
        _sv_review_n = len([g for g in graded
                            if g["brain_action"] == "sell"])
        _sv_review = (
            {"mature": True,
             "n": _sv_review_n,
             "bar": ROW_BARS["SELLVETO_REVIEW"],
             "net_usd": sell_vetoes["net_usd"],
             "recommendation": (
                 "REVIEW THE SELL VETO: "
                 + ("the vetoes PAY (net "
                    f"${sell_vetoes['net_usd']:+,.2f}) — the veto earns "
                    "its override; consider extending the deep-direction "
                    "gate's reach"
                    if sell_vetoes["net_usd"] < 0 else
                    ("the vetoes COST (net "
                     f"${sell_vetoes['net_usd']:+,.2f}) — the refused "
                     "buys out-earned the saved losses; consider relaxing "
                     "the deep-direction veto"
                     if sell_vetoes["net_usd"] > 0 else
                     "the vetoes wash (net $0.00)")))}
            if rows_mature("SELLVETO_REVIEW", _sv_review_n) else
            {"mature": False, "n": _sv_review_n,
             "bar": ROW_BARS["SELLVETO_REVIEW"],
             "net_usd": sell_vetoes["net_usd"],
             "recommendation": None})
    except Exception as _svr_exc:
        logger.debug("sell-veto review gate read failed (%s) — no "
                     "recommendation this pass", _svr_exc)
        _sv_review = {"mature": False, "n": 0,
                      "bar": None, "net_usd": None,
                      "recommendation": None}

    summary = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
               + "Z", "notes": len(notes), "graded": len(graded),
               "ungraded": len(notes) - len(graded),
               "position_usd": POSITION_USD, "bands": bands,
               "follow_through": follow_through,
               "weekly_holds": weekly_holds,
               "sell_vetoes": sell_vetoes,
               "sell_veto_bands": sell_veto_bands,
               "sell_veto_relax": sell_veto_relax,
               "learned_veto": _learned_veto,
               "sell_veto_review": _sv_review,
               "per_pair": dict(sorted(per_pair.items(),
                                       key=lambda kv: -kv[1]["brain_usd"]))}

    # ── the txt report ──
    out = ["=== THE DIRECTION CROSS-CHECK, GRADED (Sep 18) ===",
           f"rows: {len(notes)} captured, {len(graded)} graded, "
           f"{summary['ungraded']} ungradeable (store missing/short/"
           "horizon open) | position $" f"{POSITION_USD:,.0f} | horizon "
           f"{HORIZON}h | ran = +{RAN_PCT:.0f}% MFE",
           ""]
    if graded:
        out += ["THE BRAIN'S STANCE AT EVERY VETOED HOUR — by replayed "
                "brain conviction (a row lands in the highest band it "
                "clears; only brain-BUY rows are money the cheap read "
                "refused):",
                "  band | rows | brain-buy | ran |   end-move $ | ceiling $ "
                "| brain-hold rows priced"]
        for _lo, k in BANDS:
            b = bands[k]
            if not b["n"]:
                continue
            out.append(f"   >={k:>2} | {b['n']:>4} | {b['buys']:>9} | "
                       f"{b['buys_ran']:>3} | {b['buys_usd']:>+12,.2f} | "
                       f"{b['buys_ceiling']:>9,.2f} | "
                       f"{b['holds']} rows, ${b['holds_usd']:+,.2f} "
                       f"({b['holds_ran']} ran)")
        vetoed_usd = sum(b["buys_usd"] for b in bands.values())
        vetoed_ceil = sum(b["buys_ceiling"] for b in bands.values())
        out += ["",
                f"THE VETO'S PRICE: the brain would have bought at "
                f"{sum(b['buys'] for b in bands.values())} of {len(graded)} "
                f"vetoed hours — ${vetoed_usd:+,.2f} end-move, "
                f"${vetoed_ceil:,.2f} at the ran ceiling. Brain-hold rows "
                f"are the cheap read being RIGHT (confirmation, not loss).",
                "",
                "THE SELLS' HINDSIGHT (monthly, same grade): " + (
                    f"the funnel refused BUYs at {sell_vetoes['n']} hours "
                    f"because the brain said sell — {sell_vetoes['right']} "
                    f"of them fell (the sell was RIGHT, ${sell_vetoes['saved_usd']:,.2f} "
                    f"of losses the veto saved), {sell_vetoes['ran']} ran "
                    f"({(sell_vetoes['ran_rate'] or 0.0) * 100:.0f}%, "
                    f"${sell_vetoes['refused_usd']:+,.2f} the refused buys "
                    f"would have made, ${sell_vetoes['refused_ceiling']:,.2f} "
                    f"at the ceiling) — net ${sell_vetoes['net_usd']:+,.2f}: "
                    + ("the sell vetoes PAID" if sell_vetoes['net_usd'] < 0
                       else ("the sell vetoes COST — the refused buys "
                             "out-earned the saved losses"
                             if sell_vetoes['net_usd'] > 0
                             else "the sell vetoes washed"))
                    if sell_vetoes["n"] else
                    "no sell-veto rows graded yet — a replayed SELL at a "
                    "vetoed hour prices here (the long-side convention: "
                    "end < 0 = the sell was right)"),
                ""]
        # THE BAND LADDER (Sep 19, her question): at what conviction
        # does the brain's sell deserve to kill a green light? One line
        # per band with its own priced verdict; the crossover names the
        # emerged threshold when a qualifying band pays.
        out += ["THE SELL VETO BY BRAIN CONVICTION — at what band does "
                "the brain's sell actually pay as an override?"]
        for _lo, _k in BANDS:
            _b = sell_veto_bands["bands"][_k]
            if not _b["n"]:
                out.append(f"  band >=+{_k}: no sell-veto rows yet")
                continue
            out.append(
                f"  band >=+{_k}: {_b['n']} hrs, {_b['right']} fell, "
                f"{_b['ran']} ran ({(_b['ran_rate'] or 0.0) * 100:.0f}%), "
                f"saved ${_b['saved_usd']:,.2f} vs refused "
                f"${_b['refused_usd']:+,.2f} — net ${_b['net_usd']:+,.2f} "
                + ("the veto PAYS here"
                   if _b["net_usd"] < 0 else
                   ("the veto COSTS here" if _b["net_usd"] > 0
                    else "washes")))
        out.append(
            (f"THE CONVICTION THRESHOLD: at band >=+{sell_veto_bands['crossover']} "
             f"the brain's sell earns its override — below that, the "
             "cheap read's green light has priced room to run "
             f"(a band needs {sell_veto_bands['min_n']} rows to speak)")
            if sell_veto_bands["crossover"] else
            "NO CONVICTION THRESHOLD YET: no qualifying band's sell "
            "vetoes pay — the pooled number is already the best the "
            "sell veto does")
        out.append(
            (f"MATURITY GATE ({_sv_review['n']}/{_sv_review['bar']} graded "
             "sell rows): " + _sv_review["recommendation"])
            if _sv_review.get("mature") and _sv_review.get("recommendation") else
            f"MATURITY GATE: {_sv_review['n']}/{_sv_review['bar']} graded "
            "sell rows — the verdict stays advisory until the ladder's "
            "SELLVETO REVIEW rung is reached")
        out += ["",
                "FOLLOW-THROUGH VERDICT: " + (
                    f"ARMED — band >=+{FT_BAND} holds "
                    f"{follow_through['buys']}/{follow_through['n']} "
                    f"brain-buys ({follow_through['buy_rate'] * 100:.0f}%), "
                    f"{follow_through['ran_rate'] * 100:.0f}% ran; "
                    "direction-vetoed top movers enter the next pass's "
                    "deep-read queue automatically (the priced pass's "
                    "own veto still stands; the brain re-decides there)"
                    if follow_through["armed"] else
                    f"dormant — band >=+{FT_BAND} has "
                    f"{follow_through['n']} rows (need {FT_N_MIN}), "
                    f"{follow_through['buy_rate'] * 100:.0f}% brain-buys "
                    f"(need {FT_BUY_RATE_MIN * 100:.0f}%), "
                    f"{follow_through['ran_rate'] * 100:.0f}% ran "
                    f"(need {FT_RAN_MIN * 100:.0f}%) — nothing auto-queues"),
                "",
                "per pair (worst refused money first):"]
        for pair, pp in summary["per_pair"].items():
            if pp["n"]:
                out.append(f"  {pair}: {pp['n']} rows, brain bought "
                           f"{pp['brain_buys']} — ${pp['brain_usd']:+,.2f} "
                           f"(${pp['brain_ceiling']:,.2f} ceiling)")
    else:
        out.append("no rows graded yet — the cross-check has not fired "
                   "(or no store can answer the captured hours).")
    # THE TWO BLIND SPOTS, TOGETHER (Sep 18): always rendered — the
    # direction veto's week and the chase pool's missed world are one
    # reading, even while one side is still unmeasured.
    _c = weekly_holds["chase"]
    if weekly_holds["holds"]:
        out.append(
            f"THE WEEK IN HOLDS (last 7d): "
            f"{weekly_holds['holds_ran']}/{weekly_holds['holds']} "
            f"brain-hold rows ran anyway "
            f"({weekly_holds['ran_rate'] * 100:.0f}%), "
            f"${weekly_holds['holds_usd']:+,.2f} end — the cheap read's "
            "refused money this week")
    else:
        out.append("THE WEEK IN HOLDS (last 7d): no brain-hold rows "
                   "graded this week — the veto's own blind spot is "
                   "still unmeasured")
    if _c["n"]:
        _vs = (f" vs the veto week's {weekly_holds['ran_rate'] * 100:.0f}%"
               if weekly_holds["ran_rate"] is not None else "")
        out.append(
            f"TWO BLIND SPOTS TOGETHER: the chase study's MISSED pool "
            f"— {_c['n']} setups, {_c['rate'] * 100:.0f}% ran, "
            f"${_c['usd_turn']:+,.2f}/turn (study "
            f"{str(_c['ts'])[:10]}){_vs} — read the veto's holds and the "
            "tracker's missed setups as one class of refused money")
    else:
        out.append("TWO BLIND SPOTS TOGETHER: the chase study's missed "
                   "pool is unreadable — the comparison waits for its "
                   "ledger (the veto's week stands alone above)")
    out.append("")

    if write:
        OUT.write_text("\n".join(out), encoding="utf-8")
        _rearm = _detect_rearm(summary)
        try:
            with open(LEDGER, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(summary) + "\n")
        except Exception as exc:
            logger.debug("direction-grade ledger append failed: %s", exc)
        if _rearm:
            try:
                # production-only voice (imported inside the driver's own
                # process); tests sandbox the ledger and never reach here
                from background_scanner import _speak_server
                _speak_server(_rearm["say"], kind=None, asset=None,
                              source="scanner")
                logger.info(" FOLLOW-THROUGH RE-ARM: %s", _rearm["say"])
            except Exception as exc:
                logger.debug("re-arm speak failed: %s", exc)
    if return_rows:
        summary["rows"] = graded   # the study's per-row seam; the
        # ledger record is unchanged (rows never serialize to it)
    return summary


def monthly_driver_loop(stop, first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one grade shortly after boot, then monthly —
    the veto's price re-prices itself as rows and stores grow. Never
    raises; dies with the scanner."""
    import threading
    if stop.wait(first_delay_s):
        return
    while not stop.is_set():
        try:
            _bf = backfill_notes_from_suppressions()
            logger.info(" DIRECTION BACKFILL: %d burial rows replayed, "
                        "%d new vetoed hours into the notes feed",
                        _bf["scanned"], _bf["added"])
        except Exception as _bf_exc:
            logger.debug("direction backfill failed: %s", _bf_exc)
        try:
            run_grade(write=True)
        except Exception as exc:
            logger.debug("direction-grade pass failed: %s", exc)
        if stop.wait(period_s):
            return


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s = run_grade(write=True)
    print(OUT.read_text(encoding="utf-8"))
