"""go_big_week.py — ONE all-in a week, recorded, so "about once a week" is a
fact the code can check instead of a habit the market has to respect.

WHAT ALREADY EXISTS: `contest_account.go_big_supported` answers "can this coin
and this rotation carry the whole account", and `contest_account.go_big_plan`
sizes it (100% of the balance, a 1%-of-start net stop, a 5% take, and the hard
death-line arithmetic). The scanner, the QC panel and the paper day loop all
call them.

WHAT WAS MISSING: neither function knows WHEN the last all-in happened. The
scanner's only limit was `slot_open` — "the book must be flat" — and a flat book
in a fast tape can come back three times in an afternoon. The user rule is
"about once a week go all in when it calls for it", so the calendar is part of
the policy, not a detail of the book.

THE THREE REFUSALS IN THIS MODULE (and nothing else)
  1. one GO BIG per `MIN_DAYS` (7) — the weekly slot
  2. never a second while one is still open (the cash ceiling also forbids it,
     but a ledger that cannot describe its own open position is not a ledger)
  3. never the same pair again inside `PAIR_COOLDOWN_FRAC` of that week

This is NOT a risk gate. The death line, the buffer floor and the stop
arithmetic stay in `contest_account.go_big_plan`; this file only counts weeks.
Never raises into a caller: a corrupt state file reads as "never fired", which
fails toward the ordinary (non-all-in) sizing the caller already had.

    python go_big_week.py            print the state and the next eligible time
    python go_big_week.py --json     the same as JSON
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "go_big_week_state.json")

DAY = 86400.0
MIN_DAYS = 7.0              # "about once a week"
PAIR_COOLDOWN_FRAC = 1.0    # a pair that took the slot waits the same week
STALE_DAYS = 14.0           # an "open" nobody closed cannot block forever
UNCONFIRMED_S = 600.0       # a write that never became a position is unspent


def _blank() -> dict:
    return {"fires": [], "closed": [], "open": None, "last_fire_ts": None}


def load_state(path: str | None = None) -> dict:
    """The ledger, or a blank one. A broken file reads as "never fired".

    SELF-HEALING: an `open` older than STALE_DAYS is closed here. The scanner
    records the fire where the buy is written; the sell can happen in a module
    that never heard of this file, and a ledger whose `open` can only be cleared
    by a hand edit would refuse every all-in after the first one. Fourteen days
    is far past any all-in the exit watch would still be riding.
    """
    p = path or STATE
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            out = _blank()
            out.update({k: d[k] for k in out if k in d})
            op = out.get("open")
            if op and time.time() - float(op.get("ts") or 0) > STALE_DAYS * DAY:
                out.setdefault("closed", []).append({**op, "stale": True})
                out["open"] = None
                try:
                    save_state(out, path)
                except OSError:
                    pass
            return out
    except (OSError, ValueError, TypeError):
        pass
    return _blank()


def reconcile(book_open: bool, now: float | None = None,
              path: str | None = None) -> dict:
    """Follow the book: if nothing is held, the recorded open all-in is closed.

    Callers that can see the live book (the scanner's contest watch) pass
    `book_open`; the ledger then cannot stay stuck on a position that is gone.
    A flat book closes the record with no P&L (the book's own ledger has it).
    """
    s = load_state(path)
    t = float(now if now is not None else time.time())
    op = s.get("open")
    if op and not book_open:
        age = t - float(op.get("ts") or 0)
        if age <= UNCONFIRMED_S:
            # THE WRITE NEVER BECAME A POSITION (the bot refused the pending
            # signal, the pair was restricted, the book filled elsewhere):
            # the week is NOT spent.  Only a confirmed all-in costs the slot.
            s["fires"] = [f for f in (s.get("fires") or [])
                          if f.get("ts") != op.get("ts")]
            s["last_fire_ts"] = max((float(f.get("ts") or 0)
                                     for f in s["fires"]), default=None)
            s["open"] = None
            s.setdefault("closed", []).append({**op, "unconfirmed": True})
        else:
            s = note_exit(now=t, state=s)
        try:
            save_state(s, path)
        except OSError:
            pass
    return s


def save_state(s: dict, path: str | None = None) -> None:
    p = path or STATE
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


def last_fire_ts(path: str | None = None) -> float | None:
    """When the last GO BIG fired, or None. The one number callers need."""
    v = load_state(path).get("last_fire_ts")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def can_fire(now: float | None = None, pair: str | None = None,
             state: dict | None = None, min_days: float = MIN_DAYS,
             path: str | None = None) -> tuple[bool, str]:
    """May a GO BIG entry fire now? (ok, why). Never raises."""
    try:
        t = float(now if now is not None else time.time())
        s = state if state is not None else load_state(path)
        wait = max(0.0, float(min_days)) * DAY
        op = s.get("open")
        if op:
            return False, (f"a GO BIG is already open on {op.get('pair')} "
                           f"(opened {_ago(t, op.get('ts'))}) — one at a time")
        last = s.get("last_fire_ts")
        if last is not None:
            last = float(last)
            if t - last < wait:
                return False, (f"the weekly slot is spent: last GO BIG fired "
                               f"{_ago(t, last)}; it opens in "
                               f"{(last + wait - t) / DAY:.1f}d "
                               f"({_when(last + wait)})")
            if pair:
                prev = [f for f in s.get("fires") or []
                        if f.get("pair") == pair and last - float(f.get("ts") or 0)
                        < wait * PAIR_COOLDOWN_FRAC]
                if prev:
                    return False, (f"{pair} took the last GO BIG "
                                   f"{_ago(t, last)} — it waits the same week")
        return True, "the weekly GO BIG slot is open"
    except (TypeError, ValueError) as exc:
        # a broken read must not become an all-in
        return False, f"the GO BIG calendar is unreadable ({exc})"


def note_fire(pair: str, usd: float, why: str = "", now: float | None = None,
              path: str | None = None, state: dict | None = None) -> dict:
    """Record an all-in that actually fired. Returns the new state."""
    t = float(now if now is not None else time.time())
    s = state if state is not None else load_state(path)
    rec = {"ts": t, "pair": str(pair or ""), "usd": round(float(usd or 0.0), 2),
           "why": str(why or "")}
    s.setdefault("fires", []).append(rec)
    s["last_fire_ts"] = t
    s["open"] = rec
    if state is None:
        save_state(s, path)
    return s


def note_exit(pnl_usd: float | None = None, net_pct: float | None = None,
              now: float | None = None, path: str | None = None,
              state: dict | None = None) -> dict:
    """Close the open all-in (the book is flat again). Returns the new state."""
    t = float(now if now is not None else time.time())
    s = state if state is not None else load_state(path)
    op = s.get("open")
    if op:
        rec = {**op, "closed_ts": t,
               "held_days": round((t - float(op.get("ts") or t)) / DAY, 3),
               "pnl_usd": None if pnl_usd is None else round(float(pnl_usd), 2),
               "net_pct": None if net_pct is None else round(float(net_pct), 4)}
        s.setdefault("closed", []).append(rec)
        s["open"] = None
        if state is None:
            save_state(s, path)
    return s


def status(now: float | None = None, path: str | None = None) -> dict:
    """The console's view: when the slot opens, what fired, how it went."""
    t = float(now if now is not None else time.time())
    s = load_state(path)
    last = s.get("last_fire_ts")
    nxt = (float(last) + MIN_DAYS * DAY) if last is not None else t
    closed = s.get("closed") or []
    wins = [c for c in closed if (c.get("pnl_usd") or 0) > 0]
    return {"open": s.get("open"), "last_fire_ts": last,
            "days_since": round((t - float(last)) / DAY, 2) if last else None,
            "next_eligible_ts": nxt,
            "next_eligible": _when(nxt),
            "fires_30d": len([f for f in (s.get("fires") or [])
                              if t - float(f.get("ts") or 0) <= 30 * DAY]),
            "closed": len(closed),
            "wins": len(wins),
            "pnl_usd": round(sum(float(c.get("pnl_usd") or 0.0)
                                 for c in closed), 2)}


def _ago(now: float, ts: float) -> str:
    d = (float(now) - float(ts)) / DAY
    if d < 1.0:
        return f"{d * 24:.1f}h ago"
    return f"{d:.1f}d ago"


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))


def main(argv: list[str]) -> int:
    st = status()
    if "--json" in argv:
        print(json.dumps(st, indent=1))
        return 0
    print(f"GO BIG: one all-in per {MIN_DAYS:.0f} days")
    print(f"  next slot: {st['next_eligible']}"
          + ("" if st["last_fire_ts"] else " (never fired — open now)"))
    if st["last_fire_ts"]:
        print(f"  last fire: {_when(st['last_fire_ts'])} "
              f"({st['days_since']}d ago)")
    else:
        print("  last fire: never")
    print(f"  open: {st['open'] or 'none'}")
    print(f"  last 30 days: {st['fires_30d']} fires; "
          f"closed {st['closed']} (wins {st['wins']}), net ${st['pnl_usd']:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
