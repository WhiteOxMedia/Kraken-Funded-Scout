"""push_service.py — THE NATIVE PUSH SERVICE (Sep 25, charter).

xmrjackpot.com's own push notification system — replaces the old
Ntfy path. One brain per layer:

  - SUBSCRIPTIONS: one row per (user_id, endpoint), the user's own
    browser registration — never a miner address, never a key, never
    a third-party identity. Storage is settings-backed (the same
    schema-owner discipline as jackpot_funding): no database.py edit
    needed while that file belongs to another thread.
  - EVENTS: the charter's event set — jackpot win, claim-window
    reminder, challenge unlock, the two long-bullish signals
    (Buy Now / Get Out Now), miner status, ticket update — each with
    ONE template (title/body/tag/url). Templates are the only thing
    that ever becomes notification text; free-form content never
    enters a payload.
  - SENDS: rate-limited per user per event class, audited in
    audit_log (user-scoped, names and numbers only), and fail-soft —
    a dead endpoint unsubscribes itself instead of erroring forever.
  - SECRETS: the VAPID keys are settings the OPERATOR provisions
    (py_vapid → VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY); this module
    never generates, prints, or leaks them. The public key endpoint
    serves the public key only.

THE WIRING (done, Sep 25): the HTTP routes (/api/push/public-key,
/api/push/subscribe, /api/push/unsubscribe, /api/push/event) are LIVE
in pool/server.py — wired verbatim from the handlers below (the server
adds the auth: session cookie for the browser leg, X-Operator-Token
for the machine leg), plus a /push_sw.js root-path route so the service
worker scopes to / (the /static/ default scope could never control
pages at / — caught by the live e2e proof).
"""
import json
import time
from pathlib import Path

import database as db

# ── storage (settings-backed: no schema owner on this box) ───────────
SUBS_KEY = "push_subscriptions"
SPOOL_PATH = Path(__file__).resolve().parent / "push_events.jsonl"
RATE_KEY = "push_send_rate"
MAX_PER_HOUR = 20          # per user, per event class
EVENTS = {
    "jackpot_win": {
        "title": "🎉 YOU WON THE JACKPOT",
        "body": ("The daily prize is yours — open xmrjackpot.com to "
                 "claim it before the window closes."),
        "tag": "jackpot-win",
        "url": "/",
        "rate": 4,
    },
    "claim_reminder": {
        "title": "⏳ Your claim window is closing",
        "body": ("Your jackpot claim deadline is near — claim now so "
                 "the prize rolls back to the pool."),
        "tag": "claim-reminder",
        "url": "/",
        "rate": 2,
    },
    "challenge_unlock": {
        "title": "🧜 The Kraken Challenge unlocked",
        "body": ("Your win opened the challenge guide — the referral "
                 "link is waiting on your jackpot plate."),
        "tag": "challenge-unlock",
        "url": "/",
        "rate": 4,
    },
    "signal_buy": {
        "title": "📈 BUY NOW",
        "body": ("Kraken Funded long signal: the window is open. "
                 "Details on your Scout console."),
        "tag": "signal-buy",
        "url": "/",
        "rate": 6,
    },
    "signal_exit": {
        "title": "📉 GET OUT NOW",
        "body": ("Kraken Funded long signal: the exit is live. "
                 "Details on your Scout console."),
        "tag": "signal-exit",
        "url": "/",
        "rate": 6,
    },
    "miner_status": {
        "title": "⛏ Miner Bot update",
        "body": ("Your Miner Bot status changed — open the console "
                 "for the details."),
        "tag": "miner-status",
        "url": "/",
        "rate": 4,
    },
    "ticket_update": {
        "title": "🎟 Ticket update",
        "body": ("Your entry count changed — see the jackpot plate "
                 "for your tickets in the next drawing."),
        "tag": "ticket-update",
        "url": "/",
        "rate": 4,
    },
}


# THE TWO DASHBOARD CATEGORIES (user order, Sep 25 evening VII):
# every event belongs to one of the two notification families the
# user can opt out of independently on their dashboard panel.
CATEGORY = {
    "jackpot_win": "jackpot",
    "claim_reminder": "jackpot",
    "challenge_unlock": "jackpot",
    "ticket_update": "jackpot",
    "miner_status": "jackpot",
    "signal_buy": "scout",
    "signal_exit": "scout",
}
PREFS_KEY = "push_prefs"          # {"<user_id>": {"scout": bool, "jackpot": bool}}
DEFAULT_PREFS = {"scout": True, "jackpot": True}


def _load_prefs() -> dict:
    """The prefs store is a DICT keyed by user id (the subscription
    store is a list; this one is per-user preferences)."""
    raw = db.get_setting(PREFS_KEY, "{}")
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, dict) else {}
    except (ValueError, TypeError):
        return {}


def get_prefs(user_id: int) -> dict:
    rows = _load_prefs()
    return dict(rows.get(str(int(user_id)), DEFAULT_PREFS))


def set_pref(user_id: int, category: str, enabled: bool) -> dict:
    if category not in ("scout", "jackpot"):
        return {"ok": False, "reason": "unknown category"}
    rows = _load_prefs()
    key = str(int(user_id))
    prefs = dict(rows.get(key, DEFAULT_PREFS))
    prefs[category] = bool(enabled)
    rows[key] = prefs
    _save(PREFS_KEY, rows)
    _audit(user_id, "push_pref_changed",
           {"category": category, "enabled": bool(enabled),
            "via": "push_service"})
    return {"ok": True, "prefs": get_prefs(user_id)}


def _load(key):
    raw = db.get_setting(key, "[]")
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, list) else []
    except (ValueError, TypeError):
        return []


def _save(key, rows):
    db.set_setting(key, json.dumps(rows, separators=(",", ":")))


def public_key() -> str:
    """The OPERATOR-provisioned VAPID public key (settings). Empty
    until provisioned — the client degrades quietly."""
    return str(db.get_setting("VAPID_PUBLIC_KEY", "") or "")


def subscribe(user_id: int, subscription: dict) -> dict:
    """Store one browser registration for one user. The subscription's
    endpoint is the only identity it carries; it is stored per user
    and never shared across users."""
    if not isinstance(user_id, int) or user_id <= 0:
        return {"ok": False, "reason": "bad user"}
    if not isinstance(subscription, dict):
        return {"ok": False, "reason": "bad subscription"}
    endpoint = str(subscription.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        return {"ok": False, "reason": "endpoint must be https"}
    rows = _load(SUBS_KEY)
    for r in rows:
        if r.get("endpoint") == endpoint:
            if int(r.get("user_id", -1)) == user_id:
                r["keys"] = subscription.get("keys") or {}
                r["ts"] = time.time()
                _save(SUBS_KEY, rows)
                return {"ok": True, "reason": "refreshed"}
            return {"ok": False, "reason": "endpoint belongs to another user"}
    rows.append({
        "user_id": int(user_id),
        "endpoint": endpoint,
        "keys": subscription.get("keys") or {},
        "ts": time.time(),
    })
    _save(SUBS_KEY, rows)
    return {"ok": True, "reason": "subscribed"}


def unsubscribe(user_id: int, endpoint: str) -> dict:
    rows = _load(SUBS_KEY)
    kept = [r for r in rows
            if not (r.get("endpoint") == endpoint
                    and int(r.get("user_id", -1)) == user_id)]
    if len(kept) == len(rows):
        return {"ok": False, "reason": "not found"}
    _save(SUBS_KEY, kept)
    return {"ok": True}


def _rate_ok(user_id: int, event: str) -> bool:
    """Per-user, per-event-class sliding hourly window."""
    now = time.time()
    hits = [(u, e, t) for (u, e, t) in
            (x if isinstance(x, (list, tuple)) else (0, "", 0)
             for x in _load(RATE_KEY))
            if u == user_id and e == event and now - t < 3600]
    limit = EVENTS.get(event, {}).get("rate", MAX_PER_HOUR)
    if len(hits) >= limit:
        return False
    hits.append((user_id, event, now))
    kept = [h for h in _load(RATE_KEY)
            if isinstance(h, (list, tuple)) and len(h) == 3
            and now - float(h[2]) < 3600]
    kept.append((user_id, event, now))
    _save(RATE_KEY, kept)
    return True


def _audit(user_id, action, details):
    """Audit or die loudly: a push send is a logged event — if the
    audit write fails, the failure must surface, not vanish (an
    except:pass here would make every 'logged' claim unverifiable)."""
    db.add_audit_log(user_id, action, json.dumps(details))


def send_to_user(user_id: int, event: str, extra: dict = None) -> dict:
    """Send one templated event to ONE user's devices. Never sends to
    the wrong user: the subscription is looked up by the user_id the
    caller is already authenticated as, and the payload carries only
    the template's text plus optional whitelisted interpolations."""
    ev = EVENTS.get(event)
    if ev is None:
        return {"sent": 0, "reason": "unknown event"}
    subs = [r for r in _load(SUBS_KEY) if int(r.get("user_id", -1)) == user_id]
    if not subs:
        return {"sent": 0, "reason": "no subscription"}
    # THE OPT-OUT GATE (user order, Sep 25 evening VII): the user's
    # dashboard toggles decide, per category, before anything sends.
    cat = CATEGORY.get(event)
    if cat and not get_prefs(user_id).get(cat, True):
        return {"sent": 0, "reason": f"{cat} notifications opted out"}
    if not _rate_ok(user_id, event):
        _audit(user_id, "push_rate_limited",
               {"event": event, "via": "push_service"})
        return {"sent": 0, "reason": "rate limited"}
    payload = {
        "title": ev["title"],
        "body": ev["body"],
        "tag": ev["tag"],
        "url": ev["url"],
    }
    if extra:
        payload.update({k: str(v)[:120] for k, v in extra.items()
                        if k in ("amount_xmr", "deadline", "signal")})
    sent = dead = 0
    try:
        from pywebpush import webpush, WebPushException
        have_lib = True
    except Exception:
        have_lib = False
    vapid_priv = db.get_setting("VAPID_PRIVATE_KEY", "")
    if have_lib and not vapid_priv:
        # review triage (Sep 25): a missing operator key is a CONFIG
        # error, not a dead device — deleting subscriptions for it
        # would have silently destroyed every registration on the
        # first unprovisioned send
        return {"sent": 0, "reason": "VAPID key not provisioned"}
    for r in subs:
        if not have_lib:
            break
        try:
            webpush(
                subscription_info={"endpoint": r["endpoint"],
                                   "keys": r.get("keys") or {}},
                data=json.dumps(payload),
                vapid_private_key=vapid_priv,
                vapid_claims={"sub": "mailto:operator@xmrjackpot.com"},
            )
            sent += 1
        except WebPushException as wexc:
            # only a real endpoint death unsubscribes (410 Gone is the
            # protocol's own signal); config/transport errors keep the
            # registration and retry later
            resp = getattr(wexc, "response", None)
            status = getattr(resp, "status_code", None) or \
                getattr(resp, "status", 0)
            if status == 410 or "410" in str(wexc):
                dead += 1
                unsubscribe(user_id, r["endpoint"])
            else:
                dead += 1
        except Exception:
            dead += 1
    if have_lib and (sent or dead):
        _audit(user_id, "push_sent", {"event": event, "sent": sent,
                                      "dead": dead,
                                      "via": "push_service"})
    return {"sent": sent, "dead": dead,
            "reason": None if sent else
            ("webpush library not installed" if not have_lib
             else "no deliverable endpoint")}


def send_to_subscribers(event: str, extra: dict = None) -> dict:
    """Broadcast one templated event to EVERY subscriber (signal
    events: the same signal is for everyone opted in). Per-user rate
    limits still apply inside send_to_user; the broadcast itself has
    its own limiter bucket (user_id 0) and one audit row."""
    ev = EVENTS.get(event)
    if ev is None:
        return {"sent": 0, "reason": "unknown event"}
    subs = [r for r in _load(SUBS_KEY) if int(r.get("user_id", -1)) > 0]
    if not subs:
        return {"sent": 0, "reason": "no subscription"}
    if not _rate_ok(0, event):
        return {"sent": 0, "reason": "rate limited"}
    total = 0
    # review triage (Sep 25 evening V): subs is one row PER ENDPOINT —
    # looping rows sent to every device of every user once per device.
    # Fan out per UNIQUE user instead; send_to_user already covers all
    # of a user's endpoints.
    _unique_users = sorted({int(r["user_id"]) for r in subs})
    for _uid in _unique_users:
        res = send_to_user(_uid, event, extra)
        total += int(res.get("sent", 0))
    _audit(None, "push_broadcast", {"event": event, "delivered": total,
                                    "recipients": len(_unique_users),
                                    "via": "push_service"})
    return {"sent": total}


def drain_spool(spool_path=None) -> int:
    """The pool side's reader: hand Scout's spooled events (written by
    scout/push_client.py when the operator route is not yet wired)
    through the rate-limited, audited sender. ONE reader — the
    poller's loop calls this each cycle. A row that fails 50 drains
    is dropped (a week-old signal is not a signal anymore); the spool
    never grows past its writer's own cap."""
    path = Path(spool_path) if spool_path else SPOOL_PATH
    if not path.exists():
        return 0
    try:
        rows = [json.loads(l) for l in
                path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return 0
    kept, handed = [], 0
    for r in rows:
        if not isinstance(r, dict) or "event" not in r:
            continue
        tries = int(r.get("tries", 0)) + 1
        if tries > 50:
            continue
        target = r.get("user_id")
        result = (send_to_user(int(target), r["event"], r.get("extra"))
                  if isinstance(target, int) or
                  (isinstance(target, str) and target.isdigit())
                  else send_to_subscribers(r["event"], r.get("extra")))
        if result.get("sent"):
            handed += 1
        elif result.get("reason") == "unknown event":
            continue
        else:
            r["tries"] = tries
            kept.append(r)
    try:
        path.write_text(
            "\n".join(json.dumps(r, separators=(",", ":")) for r in kept)
            + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass
    return handed


# ── route handlers (for the server thread to wire verbatim) ──────────
def handle_public_key():
    return {"public_key": public_key()}


def handle_subscribe(user_id: int, body: dict):
    return subscribe(user_id, (body or {}).get("subscription") or {})


def handle_unsubscribe(user_id: int, body: dict):
    return unsubscribe(user_id, str((body or {}).get("endpoint") or ""))


def scrape_recent_signals(user_id: int, limit: int = 15,
                          window_s: int = 86400):
    """User-scoped rows for the Scout Signals dashboard panel: the last
    `limit` signal audits within the last `window_s` seconds,
    whitelisted to exactly (action, timestamp, title). Scoping (second
    triage, Sep 25 evening batch): `push_sent` rows are filtered to
    THIS caller's user_id; `push_broadcast` rows (user_id NULL) are the
    signal events by nature and are the only cross-user rows shown —
    and only their signal titles, never a targeted event's. No
    endpoints, no keys, no details JSON beyond the event name."""
    limit = max(1, min(int(limit), 30))
    since = time.time() - max(60, min(int(window_s), 7 * 86400))
    try:
        with db.get_db() as conn:
            # (third triage, Sep 25 late batch: the window is wider than
            # the limit because the SIGNAL filter below runs BEFORE the
            # cap — a user's jackpot_win / claim_reminder pushes must
            # never displace real signals, nor appear as them)
            rows = conn.execute(
                """SELECT action, details, created_at FROM audit_log
                   WHERE action IN ('push_sent', 'push_broadcast')
                   AND created_at >= ?
                   AND (action = 'push_broadcast'
                        OR (action = 'push_sent' AND user_id = ?))
                   ORDER BY created_at DESC LIMIT 200""",
                (since, int(user_id)),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            action = str(r["action"])
            ts = float(r["created_at"])
        except (TypeError, ValueError, IndexError):
            continue
        # ONLY signal events belong in a signals feed: the audit row's
        # details names the event template — anything but signal_buy /
        # signal_exit is filtered out before the limit applies
        try:
            ev_name = str(json.loads(r["details"]).get("event") or "")
        except Exception:
            continue
        if ev_name not in ("signal_buy", "signal_exit"):
            continue
        ev = EVENTS.get(ev_name)
        out.append({"action": action, "ts": ts,
                    "title": ev["title"] if ev else ev_name})
        if len(out) >= limit:
            break
    return out
