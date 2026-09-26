"""pool/push_report.py — THE DAILY PUSH REPORT (Sep 26).

One number-set per day, straight from the push audit trail:

    per category (jackpot / scout / uncategorized):
      sent         — push_send_allowed rows, summed `sent`
      dead         — push_send_allowed rows, summed `dead`
      blocked      — push_send_blocked rows (opted_out etc.)
      rate_limited — push_rate_limited rows (per-user + broadcast)
    plus store motion: push_subscribe / push_unsubscribe counts and
    the end-of-day subscriber count.

The day bucket is UTC (created_at epoch -> UTC calendar day). Rollups
live in the `push_daily_report` table (one row per category per day)
and are computed IDEMPOTENTLY: recomputing a day replaces its rows,
so the scheduler can recompute "yesterday" after every midnight and
the numbers never double-count. Live ranges (today) are computed on
read, never persisted — a half-finished day must not look like data.

Everything here is derived from audits; nothing is sampled, nothing
estimated. If the audit trail missed a send, the report says less —
it never invents more.
"""
import calendar
import json
import time

import database as db
import push_service as push_svc

CATEGORIES = ("jackpot", "scout", "uncategorized")
RETENTION_DAYS = 90          # rollup rows older than this are pruned


def _day_bounds(epoch: float) -> tuple:
    """UTC calendar-day [start, end) for an epoch second."""
    st = calendar.timegm(time.gmtime(int(epoch)))
    return st - (st % 86400), st - (st % 86400) + 86400


def _cat_from_event(event: str) -> str:
    return push_svc.CATEGORY.get(event, "uncategorized")


def _sum_details(rows) -> dict:
    """Sum the integer fields we care about out of details JSON."""
    out = {"sent": 0, "dead": 0, "rows": 0}
    for r in rows:
        try:
            d = json.loads(r["details"] or "{}")
        except ValueError:
            continue
        out["sent"] += int(d.get("sent") or 0)
        out["dead"] += int(d.get("dead") or 0)
        out["rows"] += 1
    return out


def compute_day(day_start_epoch: float) -> dict:
    """Aggregate one UTC day's push activity from the audit trail.
    Returns the report dict; also persists it (idempotent) unless
    persist=False."""
    day_start, day_end = _day_bounds(day_start_epoch)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT action, user_id, details, created_at FROM audit_log "
            "WHERE created_at >= ? AND created_at < ? AND ( "
            "action LIKE 'push_%') ORDER BY created_at",
            (day_start, day_end)).fetchall()

    per_cat = {c: {"sent": 0, "dead": 0, "blocked": 0,
                   "rate_limited": 0, "broadcasts": 0}
               for c in CATEGORIES}
    subs_in = subs_out = 0
    events_counter = {}
    for r in rows:
        action = r["action"]
        try:
            d = json.loads(r["details"] or "{}")
        except ValueError:
            d = {}
        event = str(d.get("event") or "")
        cat = _cat_from_event(event) if event else "uncategorized"
        if action == "push_send_allowed":
            per_cat[cat]["sent"] += int(d.get("sent") or 0)
            per_cat[cat]["dead"] += int(d.get("dead") or 0)
            events_counter[event] = events_counter.get(event, 0) + 1
        elif action == "push_send_blocked":
            per_cat[cat]["blocked"] += 1
            events_counter[event] = events_counter.get(event, 0) + 1
        elif action == "push_rate_limited":
            if d.get("scope") == "broadcast":
                per_cat[cat]["broadcasts"] += 0   # scope marker only
            per_cat[cat]["rate_limited"] += 1
        elif action == "push_subscribe":
            subs_in += 1
        elif action == "push_unsubscribe":
            subs_out += 1

    # subscriber count: the CURRENT store size (motion facts in/out
    # are what history proves; the live count is a today-snapshot)
    live_subs = push_svc._load(push_svc.SUBS_KEY)

    report = {
        "day": time.strftime("%Y-%m-%d", time.gmtime(day_start)),
        "day_start": day_start,
        "day_end": day_end,
        "categories": per_cat,
        "subscriptions": {"subscribed": subs_in, "unsubscribed": subs_out,
                          "current_count": len(live_subs)},
        "events": events_counter,
        "computed_at": time.time(),
    }

    # persist: one row per category per day, REPLACE on recompute
    with db.get_db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS push_daily_report ("
            "day TEXT, category TEXT, sent INTEGER DEFAULT 0,"
            " dead INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,"
            " rate_limited INTEGER DEFAULT 0, subscribed INTEGER DEFAULT 0,"
            " unsubscribed INTEGER DEFAULT 0, events_json TEXT,"
            " computed_at REAL, PRIMARY KEY (day, category))")
        for cat, m in per_cat.items():
            conn.execute(
                "INSERT OR REPLACE INTO push_daily_report "
                "(day, category, sent, dead, blocked, rate_limited,"
                " subscribed, unsubscribed, events_json, computed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report["day"], cat, m["sent"], m["dead"], m["blocked"],
                 m["rate_limited"],
                 subs_in if cat == "jackpot" else 0,
                 subs_out if cat == "jackpot" else 0,
                 json.dumps(events_counter) if cat == "jackpot" else "[]",
                 report["computed_at"]))
    return report


def get_range(days: int = 1, persist_live: bool = False) -> dict:
    """The admin read: yesterday (the completed day) plus the last
    `days`-1 before it, from the persisted rollups — computed fresh
    only when a day was never rolled up. Today (unfinished) comes as
    a LIVE overlay computed on read and never persisted."""
    now = time.time()
    today_start, _ = _day_bounds(now)
    out_days = []
    for back in range(1, max(1, days) + 1):
        d_start = today_start - back * 86400
        if persist_live:
            rep = compute_day(d_start)
        else:
            rep = _read_persisted(d_start) or compute_day(d_start)
        out_days.append(rep)
    live_today = compute_day(today_start)
    # today must NOT persist (unfinished day); wipe any rows the
    # compute call just wrote for today
    with db.get_db() as conn:
        conn.execute("DELETE FROM push_daily_report WHERE day=?",
                     (live_today["day"],))
    return {"generated_at": now,
            "days": out_days,
            "today_live": live_today,
            "retention_days": RETENTION_DAYS}


def _read_persisted(day_start_epoch: float):
    """Load a persisted rollup, or None when that day was never
    computed."""
    day_start, _ = _day_bounds(day_start_epoch)
    day = time.strftime("%Y-%m-%d", time.gmtime(day_start))
    with db.get_db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS push_daily_report ("
            "day TEXT, category TEXT, sent INTEGER DEFAULT 0,"
            " dead INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,"
            " rate_limited INTEGER DEFAULT 0, subscribed INTEGER DEFAULT 0,"
            " unsubscribed INTEGER DEFAULT 0, events_json TEXT,"
            " computed_at REAL, PRIMARY KEY (day, category))")
        rows = conn.execute(
            "SELECT * FROM push_daily_report WHERE day=?", (day,)).fetchall()
    if not rows:
        return None
    per_cat = {c: {"sent": 0, "dead": 0, "blocked": 0,
                   "rate_limited": 0, "broadcasts": 0} for c in CATEGORIES}
    subs_in = subs_out = 0
    events = {}
    for r in rows:
        per_cat[r["category"]] = {
            "sent": r["sent"], "dead": r["dead"], "blocked": r["blocked"],
            "rate_limited": r["rate_limited"], "broadcasts": 0}
        if r["category"] == "jackpot":
            subs_in, subs_out = r["subscribed"], r["unsubscribed"]
            try:
                events = json.loads(r["events_json"] or "[]")
            except ValueError:
                events = {}
    return {"day": day, "day_start": day_start,
            "day_end": day_start + 86400,
            "categories": per_cat,
            "subscriptions": {"subscribed": subs_in,
                              "unsubscribed": subs_out},
            "events": events, "persisted": True}


def prune() -> int:
    """Drop rollups past retention. Called from the scheduler's daily
    tick."""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(
        time.time() - RETENTION_DAYS * 86400))
    with db.get_db() as conn:
        cur = conn.execute("DELETE FROM push_daily_report WHERE day < ?",
                           (cutoff,))
        return cur.rowcount
