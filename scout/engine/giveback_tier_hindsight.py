"""giveback_tier_hindsight.py — grades every SPENT give-back tier against
what the day actually did afterward (user ask, Oct 6).

The tier ladder (background_scanner._track_day_giveback) tightens the
guard when the day's give-back crosses the tier bar; ONE attempt per
tier, whatever the outcome — applied, refused and queued all land a
ledger row and burn the tier's count (a refused attempt spends the knob
without tightening anything). This module answers the question the
ledger alone cannot: did the re-arm tighten at the right moment, or did
it cut a recovery short?

The grade window is day-scoped (the ladder is a day-scoped knob): from
the tier's stamp to MST midnight of that day. The post-window facts read
the verdict study's candle-peak book (_verdict_study.grade_trades — the
SAME graded trades the nightly verdict-cost report cites; the journal
stores no peak, the candles know the truth):
  banked      = realized pnl of the window's applied trades
  handed back = the give-back verdict classes' left dollars (stayed too
                long / exited too soon / goal hit and never exited)

Verdicts, per spent tier:
  unproven     — the tier's window is still live (today's attempt)
  silence      — no banked trade followed; cure and cost both unproven
  right moment — the day handed back more after the re-arm (applied)
  paid for     — the same under a REFUSAL: the guard stayed loose and
                 the day handed back more (the collision cost money)
  cut short    — the day banked after the re-arm with little handed back
  cheap        — the same under a refusal: the day banked anyway
  mixed        — both legs moved; the receipts speak

Newest section prepends to logs/giveback_tier_hindsight.md, the same
house shape as the verdict-cost report: never raises, a failed write is
a missing report, the review and console keep their live reads.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")   # the same fixed zone discipline grades by

REPORT_PATH = Path(__file__).parent / "logs" / "giveback_tier_hindsight.md"

# the verdict classes whose LEFT dollars are handed-back money — the peak
# was on the tape and the account left with less (prefixes match
# discipline._asset_verdict's class names)
HAND_BACK_PREFIXES = ("stayed too long", "exited too soon",
                      "goal hit and never exited")


def _grade_bars():
    """(hand_back_bar, heal_bar) at the SAME scale the ladder arms at:
    half the tier-1 trigger and the trigger itself. Fail-soft to the
    10k-account numbers — a broken account read degrades the bars, it
    never zeroes them (a zero bar would grade every tick a verdict)."""
    try:
        import contest_account as _ca_gh
        _trig = float(_ca_gh.giveback_thresholds()[0])
        if _trig > 0:
            return (round(_trig / 2.0, 2), round(_trig, 2))
    except Exception:
        pass
    return (10.0, 20.0)


def spent_tiers(ledger=None):
    """The give-back tier attempts — applied, refused AND queued (a
    refusal burns the tier, so it earns a grade too). Expired rows are
    NOT attempts: the box's lift rides along as a receipt. Fail-soft
    empty."""
    try:
        if ledger is None:
            import profit_guard as _pg_gh
            ledger = (_pg_gh._state() or {}
                      ).get("auto_adjust_ledger") or []
        rows = [r for r in ledger
                if str(r.get("rotation_id") or "").startswith("giveback")
                and r.get("status") in ("applied", "refused", "queued")]
        rows.sort(key=lambda r: str(r.get("applied_at") or ""))
        return rows
    except Exception:
        return []


def _lifts(ledger=None):
    """rotation_id -> the box's lift stamp (the ledger's expired rows):
    when the tier's tightening came off. Fail-soft empty."""
    try:
        if ledger is None:
            import profit_guard as _pg_gh
            ledger = (_pg_gh._state() or {}
                      ).get("auto_adjust_ledger") or []
        out = {}
        for r in ledger:
            if (str(r.get("rotation_id") or "").startswith("giveback")
                    and r.get("status") == "expired"):
                out[str(r.get("rotation_id"))] = (
                    str(r.get("applied_at") or "")[:16].replace("T", " "))
        return out
    except Exception:
        return {}


def _graded_book():
    """The verdict study's candle-peak book — the post-window facts read
    the SAME graded trades the nightly verdict-cost report cites. An
    unreadable candle store degrades to an empty book (the grades say
    silence with the receipt of why); it never raises."""
    try:
        import _verdict_study as _vs_gh
        return _vs_gh.grade_trades() or []
    except Exception:
        return []


def _post_stats(tier, graded, now=None):
    """What the day actually did AFTER the tier: window = the tier's
    stamp -> MST midnight of the tier's day (the ladder is day-scoped;
    the next day owns its own tiers). Banked = the window's applied
    trades' realized pnl; handed back = the give-back verdict classes'
    left dollars (candle-peak based)."""
    now = now or datetime.now().astimezone()
    ts = datetime.fromisoformat(str(tier.get("applied_at") or ""))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=MST)
    day = str(tier.get("day") or ts.astimezone(MST).date().isoformat())
    _d = datetime.fromisoformat(day).date()
    _end = datetime.combine(_d, datetime.min.time(), tzinfo=MST)
    _end = _end + timedelta(days=1)
    start_ms = int(ts.timestamp() * 1000)
    end_ms = int(_end.timestamp() * 1000)
    rows_after = [g for g in graded
                  if start_ms <= int(g.get("ex_ms") or 0) < end_ms]
    _applied = [g for g in rows_after if g.get("applied")]
    banked = round(sum(float(g.get("realized") or 0.0) for g in _applied), 2)
    handed = round(sum(float(g.get("left") or 0.0) for g in _applied
                       if str(g.get("verdict") or "")
                       .startswith(HAND_BACK_PREFIXES)), 2)
    return {"day": day,
            "open": day == now.astimezone(MST).date().isoformat(),
            "n": len(_applied), "banked": banked, "handed_back": handed}


def _grade(tier, graded, lifts, now=None):
    """One (line, tag) for one spent tier. An unreadable row grades as
    its own tag — one bad ledger row must not kill the pass."""
    try:
        stats = _post_stats(tier, graded, now)
    except Exception:
        return ("- unreadable tier row — the pass skips it, the ledger "
                "keeps the record", "ungraded")
    try:
        gb_bar, heal_bar = _grade_bars()
        rid = str(tier.get("rotation_id") or "")
        tier_lbl = ("t" + rid.split("-tier")[1]) if "-tier" in rid else "t1"
        stamp = str(tier.get("applied_at") or "")[:16].replace("T", " ")
        status = str(tier.get("status") or "?")
        if status == "applied":
            head = (f"{stamp} {tier_lbl} APPLIED "
                    f"{float(tier.get('before') or 0.0):.2f}→"
                    f"{float(tier.get('after') or 0.0):.2f}")
        else:
            head = (f"{stamp} {tier_lbl} {status.upper()} "
                    f"({tier.get('reason') or 'no reason recorded'}, "
                    f"factor {float(tier.get('before') or 0.0):.2f} held)")
        _lift = lifts.get(rid)
        if _lift and status == "applied":
            head += f" (box lifted {_lift})"
        tail = (f"{stats['n']} banked trades after — banked "
                f"${stats['banked']:+,.2f}, handed back "
                f"${stats['handed_back']:,.2f}")
        if stats["open"]:
            return (f"- {head}: {tail} → unproven — the tier's window is "
                    f"still live", "unproven")
        if stats["n"] == 0:
            return (f"- {head}: {tail} → silence — no banked trade "
                    f"followed; cure and cost both unproven", "silence")
        if stats["handed_back"] >= gb_bar:
            if status == "applied":
                return (f"- {head}: {tail} → the re-arm landed at the "
                        f"right moment — the day handed back more under "
                        f"the tighter guard", "right moment")
            return (f"- {head}: {tail} → the refusal was paid for — the "
                    f"guard stayed loose and the day handed back more",
                    "paid for")
        if stats["banked"] >= heal_bar:
            if status == "applied":
                return (f"- {head}: {tail} → the re-arm cut a recovery "
                        f"short — the day banked after with little handed "
                        f"back", "cut short")
            return (f"- {head}: {tail} → the refusal was cheap — the day "
                    f"banked with the guard loose", "cheap")
        return (f"- {head}: {tail} → mixed — the day neither healed nor "
                f"handed back enough to grade clean; the receipts speak",
                "mixed")
    except Exception:
        return ("- unreadable tier row — the pass skips it, the ledger "
                "keeps the record", "ungraded")


def grade_tier(tier, graded, lifts, now=None):
    """The public single-tier grade: the line a caller (plate, console,
    review) can cite. Returns the line only."""
    return _grade(tier, graded, lifts, now)[0]


def day_line(tiers, graded, lifts, history, now=None):
    """THE CROSS-CHANNEL LINE (user ask, Oct 6): the discipline plate's
    one line where the two verdict channels meet - the hindsight
    scorer's grade of the give-back GATE (right moment / paid for / cut
    short / cheap / mixed) beside the learning ladder's own money
    verdict (worth_ladder.compute_verdict) for the SAME day. DISAGREE
    is stamped only on strict polarity conflicts: the gate's hindsight
    says the tier hurt (paid for / cut short) on a day the ladder
    PROVED, or the gate aced it (right moment / cheap) on a day the
    ladder could not prove - anything mixed, silent or ungraded shows
    both reads without a flag. Pure over its inputs (the drive grades
    it without streamlit or the live ledgers); "" when there is no
    spent tier to compare or nothing is readable."""
    try:
        _rows = [t for t in (tiers or []) if isinstance(t, dict)]
        if not _rows:
            return ""
        _rows.sort(key=lambda r: str(r.get("applied_at") or ""))
        _graded = list(graded or [])
        _day = ""
        for _t in reversed(_rows):
            try:
                _day = str(_post_stats(_t, _graded, now).get("day") or "")
            except Exception:
                _day = ""
            if _day:
                break
        if not _day:
            return ""
        _tags, _hb, _bk = [], 0.0, 0.0
        for _t in _rows:
            try:
                _st = _post_stats(_t, _graded, now)
            except Exception:
                continue
            if str(_st.get("day") or "") != _day:
                continue
            try:
                _tag = _grade(_t, _graded, lifts or {}, now)[1]
            except Exception:
                _tag = "ungraded"
            _tags.append(str(_tag))
            if _tag in ("right moment", "paid for"):
                _hb += float(_st.get("handed_back") or 0.0)
            elif _tag in ("cut short", "cheap"):
                _bk += float(_st.get("banked") or 0.0)
        if not _tags:
            return ""
        _good = any(t in ("right moment", "cheap") for t in _tags)
        _bad = any(t in ("paid for", "cut short") for t in _tags)
        _pol = ("good" if _good and not _bad else
                "bad" if _bad and not _good else "none")
        _n, _p = 0, 0
        try:
            import worth_ladder as _wl_dl
        except Exception:
            _wl_dl = None
        for _h in (history or []):
            if not isinstance(_h, dict) or str(_h.get("date") or "") != _day:
                continue
            _n += 1
            try:
                if _wl_dl is not None and _wl_dl.compute_verdict(
                        _h.get("realized_pnl"),
                        _h.get("total_left")) == "PROVEN":
                    _p += 1
            except Exception:
                pass
        _tagtxt = " + ".join(_tags)
        if _hb > 0:
            _dollars = f"${_hb:,.2f} handed back"
        elif _bk > 0:
            _dollars = f"${_bk:,.2f} banked after"
        else:
            _dollars = ""
        try:
            _dt = datetime.fromisoformat(_day)
            _daylbl = f"{_dt.strftime('%b')} {_dt.day}"
        except Exception:
            _daylbl = _day
        _ltxt = f"{_p}/{_n} proven" if _n else "no rotations on record"
        _flag = ""
        if ((_pol == "bad" and _n and _p >= 1)
                or (_pol == "good" and _n and _p == 0)):
            _flag = (" <span style='color:#ff6b5e;font-weight:bold;'>"
                     "DISAGREE</span>")
        elif _n and ((_pol == "good" and _p >= 1)
                     or (_pol == "bad" and _p == 0)):
            _flag = " · agree"
        _mid = f" ({_dollars})" if _dollars else ""
        return ("<br>⚖️ gate × ladder — {}: gate <b>{}</b>{} · "
                "ladder <b>{}</b>{}".format(_daylbl, _tagtxt, _mid,
                                            _ltxt, _flag))
    except Exception:
        return ""



def write_report(ledger=None, graded=None, now=None):
    """Prepend this pass's section (newest first, like the verdict-cost
    report). Never raises — a failed write is a missing report, the
    review keeps its live reads. Returns the path or None."""
    try:
        now = now or datetime.now().astimezone()
        tiers = spent_tiers(ledger)
        _book = graded if graded is not None else _graded_book()
        lifts = _lifts(ledger)
        gb_bar, heal_bar = _grade_bars()
        stamp = now.astimezone(MST).strftime("%Y-%m-%d %H:%M")
        if not tiers:
            lines = [f"### {stamp} - no spent give-back tiers on the "
                     f"ledger", ""]
        else:
            lines = [f"### {stamp} - {len(tiers)} spent give-back tier(s) "
                     f"graded (hand-back bar ${gb_bar:,.2f}, heal bar "
                     f"${heal_bar:,.2f})", ""]
            tags = {}
            for _t in tiers:
                _line, _tag = _grade(_t, _book, lifts, now)
                tags[_tag] = tags.get(_tag, 0) + 1
                lines.append(_line)
            _read = ", ".join(f"{v} {k}" for k, v in sorted(tags.items()))
            lines.append("")
            lines.append(f"ladder read: {_read}")
        _prev = (REPORT_PATH.read_text(encoding="utf-8")
                 if REPORT_PATH.exists() else "")
        _text = chr(10).join(lines) + chr(10) + chr(10) + _prev
        REPORT_PATH.parent.mkdir(exist_ok=True)
        _tmp = str(REPORT_PATH) + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write(_text)
        os.replace(_tmp, REPORT_PATH)
        return REPORT_PATH
    except Exception:
        return None
