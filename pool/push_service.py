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
import base64
import calendar
import json
import threading
import time
from urllib.parse import urlparse
from pathlib import Path

import database as db
import field_crypto as _fc

# ── storage (settings-backed: no schema owner on this box) ───────────
SUBS_KEY = "push_subscriptions"
SPOOL_PATH = Path(__file__).resolve().parent / "push_events.jsonl"
RATE_KEY = "push_send_rate"
MAX_PER_HOUR = 20          # per user, per event class
# THE PAID EVENTS (Oct 24): the two signals are the product Scout Pro
# sells, so they are gated on the entitlement at the SENDER — not just
# in the browser, where localStorage decides nothing that matters.
SIGNAL_EVENTS = ("signal_buy", "signal_exit")

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
    "push_test": {
        "title": "🔔 Push test from xmrjackpot",
        "body": ("This is what a xmrjackpot notification looks like "
                 "on this device — you are all set."),
        "tag": "push-test",
        "url": "/",
        "rate": 4,
    },
    "referral_window": {
        "title": "⏳ Referral window closing",
        "body": ("Your Kraken referral bonus window is about to "
                 "expire — complete the challenge in time and the "
                 "bonus is gifted to the jackpot pool."),
        "tag": "referral-window",
        "url": "/",
        "rate": 2,
    },
    "push_test_signal": {
        "title": "📡 Scout test from xmrjackpot",
        "body": ("This is what a Scout signal alert looks like on "
                 "this device — the pipe works."),
        "tag": "scout-test",
        "url": "/",
        "rate": 2,
    },
    "orphans_alert": {
        "title": "🧹 Orphaned rows detected",
        "body": ("The weekly FK sweep detector found orphaned "
                 "rows in the pool database - do not sweep blind: "
                 "plan with sweep_db.py --report first."),
        "tag": "orphans-alert",
        "url": "/#admin",
        "rate": 2,
    },
    "ops_digest": {
        "title": "🗓 Weekly ops digest",
        "body": ("Orphans, prune counts, and the Kraken weekly "
                 "verdict, in one line - open the admin panel for "
                 "the full receipt."),
        "tag": "ops-digest",
        "url": "/#admin",
        "rate": 2,
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
    "referral_window": "jackpot",
    "push_test": "jackpot",
    "signal_buy": "scout",
    "signal_exit": "scout",
    "push_test_signal": "scout",
    # the orphan alert is an OPERATOR signal, not a member feature:
    # it rides the scout category so admins who opted out of scout
    # pushes are honored, and members never see infrastructure
    # alerts (send_admin_push filters by is_admin anyway)
    "orphans_alert": "scout",
    # the digest is the same family: operator-scale news, admin
    # fan-out only, the opt-out gate still honored via the category
    "ops_digest": "scout",
}
PREFS_KEY = "push_prefs"          # {"<uid>": {"preferences": {scout,jackpot},
                                  #  "updated_at": stamp}} — THE ENVELOPE
                                  # (the raw-dict comment here is what
                                  # caused the set_pref nesting bug)
DEFAULT_PREFS = {"scout": True, "jackpot": True}


def _load_prefs() -> dict:
    """The prefs store is a DICT keyed by user id (the subscription
    store is a list; this one is per-user preferences). The blob is
    sealed at rest like the subscription store — _open_blob handles
    envelopes for both."""
    rows = _open_blob(PREFS_KEY, "{}")
    return rows if isinstance(rows, dict) else {}


def get_prefs(user_id: int) -> dict:
    return get_prefs_updated(user_id)[0]


_last_stamp = {"v": ""}


def _bump(stamp: str) -> str:
    """One second past the given stamp, UTC (handles the :59 -> next
    minute rollover via epoch arithmetic)."""
    t = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(calendar.timegm(t) + 1))


def _now_iso() -> str:
    """The prefs envelope's updated_at: UTC, Z suffix - e.g.
    2026-09-25T21:00:00Z. Second precision, but STRICTLY MONOTONIC:
    a write in the same wall-clock second as the previous one gets the
    previous stamp + 1s, so no two real changes ever share a version -
    the optimistic-concurrency ETag must move on every change, or two
    same-second writes are indistinguishable and a stale If-Match is
    accepted (same-second lost update, Sep 26 E2E acceptance 7.4).
    Format, Z suffix and the idempotent-write contract are unchanged."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if stamp <= _last_stamp["v"]:
        stamp = _bump(_last_stamp["v"])
    _last_stamp["v"] = stamp
    return stamp


def get_prefs_updated(user_id: int) -> tuple:
    rows = _load_prefs()
    entry = rows.get(str(int(user_id))) or {}
    prefs = dict(entry.get("preferences", DEFAULT_PREFS))
    # SELF-HEAL (Sep 26, live-server drive 7.1): pre-fix set_pref stored
    # the whole envelope as the preferences value (double nesting), so
    # later bulk writes merged into a shape the contract never pinned —
    # the observed {'preferences': {...}, 'updated_at': ..., 'scout':
    # True, 'jackpot': True} blob — and a restore write could no-op.
    # Unwrap until the value is the plain {scout, jackpot} dict; the
    # next write persists the repair. A clean dict never carries those
    # two keys (set_prefs/set_pref only ever write scout/jackpot), so
    # the condition cannot false-positive.
    hops = 0
    while (isinstance(prefs, dict) and hops < 3
           and ("preferences" in prefs or "updated_at" in prefs)):
        prefs = dict(prefs.get("preferences") or DEFAULT_PREFS)
        hops += 1
    return prefs, entry.get("updated_at")


# THE PREFS STORE LOCK (Sep 26, threaded race): the prefs store is a
# read-modify-write over ONE blob holding EVERY user's prefs, so
# concurrent writers — two devices firing the same version at once, or
# writes for two different users — must serialize or whole rows can be
# lost between one writer's _load_prefs and its _save. Every write
# below happens under this lock; readers stay lock-free (the blob is
# written as one atomic settings row, so readers see old or new,
# never torn).
_PREFS_LOCK = threading.RLock()


def set_prefs(user_id: int, prefs: dict, if_match: str = None) -> dict:
    """BULK set from the API envelope: {"preferences": {"jackpot": b,
    "scout": b}}. Unknown category keys are ignored; known keys update;
    updated_at stamps the change; one audit row names what moved.
    ATOMIC OPTIMISTIC CONCURRENCY (Sep 26, the simultaneous race): pass
    the If-Match version and the version check + write happen under ONE
    lock — two devices firing the same stale version at the same
    instant get exactly one applied write and one 412-shaped conflict,
    never two 200s and never a doubled stamp."""
    if not isinstance(prefs, dict):
        return {"ok": False, "reason": "preferences must be an object"}
    with _PREFS_LOCK:
        if if_match is not None:
            held = f'"{get_prefs_updated(user_id)[1] or "v0"}"'
            if if_match != held:
                prefs_now, upd_now = get_prefs_updated(user_id)
                return {"ok": False, "conflict": True,
                        "reason": "version conflict - re-read and retry",
                        "prefs": prefs_now, "updated_at": upd_now}
        rows = _load_prefs()
        key = str(int(user_id))
        current, _ = get_prefs_updated(user_id)
        changed = []
        for cat in ("scout", "jackpot"):
            if cat in prefs and bool(prefs[cat]) != bool(current.get(cat, True)):
                current[cat] = bool(prefs[cat])
                changed.append(f"{cat}={'on' if current[cat] else 'off'}")
            elif cat in prefs:
                current[cat] = bool(prefs[cat])
        if changed:
            rows[key] = {"preferences": current,
                         "updated_at": _now_iso()}
            _save(PREFS_KEY, rows)
            _audit(user_id, "push_pref_changed",
                   {"changed": changed, "via": "push_service"})
        return {"ok": True, "prefs": current, "updated_at": rows.get(key, {}).get("updated_at")}


def set_pref(user_id: int, category: str, enabled: bool,
             if_match: str = None) -> dict:
    """Single-toggle write, same atomic If-Match contract as set_prefs
    (check + write under one lock)."""
    if category not in ("scout", "jackpot"):
        return {"ok": False, "reason": "unknown category"}
    with _PREFS_LOCK:
        if if_match is not None:
            held = f'"{get_prefs_updated(user_id)[1] or "v0"}"'
            if if_match != held:
                prefs_now, upd_now = get_prefs_updated(user_id)
                return {"ok": False, "conflict": True,
                        "reason": "version conflict - re-read and retry",
                        "prefs": prefs_now, "updated_at": upd_now}
        rows = _load_prefs()
        key = str(int(user_id))
        # BUGFIX (Sep 26, live-server drive 7.1): rows[key] is the ENVELOPE
        # {"preferences": ..., "updated_at": ...}, not the prefs dict —
        # reading it here stored the envelope inside itself (double
        # nesting) on every legacy single-toggle write, which then made
        # later bulk writes/reads merge into a shape the contract never
        # pinned. Read through the canonical accessor instead.
        prefs = get_prefs_updated(user_id)[0]
        prefs[category] = bool(enabled)
        rows[key] = {"preferences": prefs, "updated_at": _now_iso()}
        _save(PREFS_KEY, rows)
        _audit(user_id, "push_pref_changed",
               {"category": category, "enabled": bool(enabled),
                "via": "push_service"})
        return {"ok": True, "prefs": prefs,
                "updated_at": rows[key]["updated_at"]}


def _open_blob(key, default):
    """Read one settings blob, opening the at-rest envelope when
    present (AES-256-GCM via field_crypto). Plaintext blobs —
    pre-migration rows, or runs without a key — parse transparently.
    A blob sealed under a key we do not have returns the DEFAULT view
    (sends no-op safely, prefs fall back); the sealed bytes stay in
    settings, so restoring the key restores the store — nothing is
    zeroed by a missing key."""
    raw = db.get_setting(key, default)
    if isinstance(raw, str) and raw.startswith(_fc.PREFIX):
        try:
            raw = _fc.decrypt(raw)
        except Exception:
            return json.loads(default)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return json.loads(default)


def _load(key):
    rows = _open_blob(key, "[]")
    return rows if isinstance(rows, list) else []


def _load_subs() -> list:
    """THE SUBSCRIPTION STORE'S ACCESSOR (Sep 26 audit): a list of
    WELL-SHAPED rows ({user_id: int, endpoint: str, keys: dict,
    ts: float}). Rows that lost their shape — a partial write, a hand
    edit, an old migration — are DROPPED here, fail-soft: every
    consumer used to call int(r.get("user_id")) directly on the raw
    list, so one poisoned row raised inside a list comprehension and
    crashed EVERY send (route 500s, the drain loop dead) instead of
    one device going silent. Same discipline _rate_ok applies inline
    to the rate store."""
    out = []
    for r in _load(SUBS_KEY):
        if not isinstance(r, dict):
            continue
        uid = r.get("user_id")
        if not isinstance(uid, int):
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
        if not isinstance(r.get("endpoint"), str) or not r["endpoint"]:
            continue
        r = dict(r)
        r["user_id"] = uid
        out.append(r)
    return out


def _save(key, rows):
    blob = json.dumps(rows, separators=(",", ":"))
    if _fc.available():
        blob = _fc.encrypt(blob)
    db.set_setting(key, blob)


def public_key() -> str:
    """The OPERATOR-provisioned VAPID public key (settings). Empty
    until provisioned — the client degrades quietly."""
    return str(db.get_setting("VAPID_PUBLIC_KEY", "") or "")


def vapid_status() -> dict:
    """Read-only posture for the admin card: whether real delivery
    can work, and which provision path produced the keys."""
    priv = str(db.get_setting("VAPID_PRIVATE_KEY", "") or "")
    pub = public_key()
    return {
        "provisioned": bool(priv and pub),
        "source": str(db.get_setting("VAPID_SOURCE", "") or "") or
                  ("operator" if priv and pub else ""),
        "public_key_tail": ("…" + pub[-8:]) if pub else "",
        "private_key_tail": ("…" + priv[-4:]) if priv else "",
    }


def vapid_ensure(generate: bool = False, _force: bool = False):
    """Provision the VAPID pair so phones actually receive pushes.
    py_vapid is OPTIONAL: without it the caller gets a precise error
    instead of a half-written keypair. generate=True creates a fresh
    pair ONLY when none is stored (never clobbers operator keys —
    rotation is its own explicit call, which is the only path that
    sets _force). Keys are stored as PEM: the private key is exactly
    the PKCS8 string pywebpush accepts, and the public key is the
    urlb64 P-256 point browsers need as applicationServerKey."""
    try:
        from py_vapid import Vapid
    except Exception:
        return {"ok": False,
                "reason": "py_vapid not installed in the server venv"}
    priv = str(db.get_setting("VAPID_PRIVATE_KEY", "") or "")
    pub = public_key()
    if priv and pub and not generate:
        return {"ok": True, "created": False,
                "mode": "already provisioned", "source":
                str(db.get_setting("VAPID_SOURCE", "") or "") or "operator"}
    if generate and (priv or pub) and not _force:
        return {"ok": False,
                "reason": "keys already exist — use rotate (this "
                          "never overwrites silently)",
                "source": str(db.get_setting("VAPID_SOURCE", "") or "")
                          or "operator"}
    v = Vapid()
    v.generate_keys()
    priv_pem = v.private_pem().decode("ascii")
    pub_pem = v.public_pem().decode("ascii")
    # applicationServerKey for pushManager.subscribe(): the raw 65-byte
    # uncompressed EC point is the tail of the SPKI DER of the P-256
    # public key.
    der = base64.b64decode("".join(
        l for l in pub_pem.splitlines() if "-----" not in l))
    app_key = base64.urlsafe_b64encode(der[-65:]).decode("ascii").rstrip("=")
    point = base64.urlsafe_b64decode(app_key + "==")
    if len(app_key) != 87 or len(point) != 65 or point[0] != 0x04:
        return {"ok": False, "reason": "generated key failed sanity check"}
    db.set_setting("VAPID_PRIVATE_KEY", priv_pem)
    db.set_setting("VAPID_PUBLIC_KEY", app_key)
    db.set_setting("VAPID_SOURCE", "generated")
    return {"ok": True, "created": True, "source": "generated",
            "public_key": app_key}


def vapid_rotate():
    """Issue a NEW keypair, keeping the old one as VAPID_PREVIOUS just
    long enough to re-subscribe devices: rotating invalidates every
    existing subscription, so the honest contract is 'rotate only as
    part of a re-subscribe campaign' — the operator trigger returns
    the new public key and the count of subscriptions that will need
    to re-enable."""
    prior = str(db.get_setting("VAPID_PRIVATE_KEY", "") or "")
    if not prior:
        return {"ok": False, "reason": "no keys to rotate — ensure first"}
    res = vapid_ensure(generate=True, _force=True)
    if not res.get("ok"):
        return res
    db.set_setting("VAPID_PREVIOUS", prior)
    subs = _load(SUBS_KEY)
    db.set_setting("VAPID_SOURCE", "rotated")
    return {"ok": True, "rotated": True, "source": "rotated",
            "public_key": public_key(),
            "subscriptions_needing_resubscribe": len(subs)}


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
    rows = _load_subs()
    for r in rows:
        if r.get("endpoint") == endpoint:
            if int(r.get("user_id", -1)) == user_id:
                r["keys"] = subscription.get("keys") or {}
                r["ts"] = time.time()
                _save(SUBS_KEY, rows)
                _audit(user_id, "push_subscribe",
                       {"reason": "refreshed",
                        "endpoint_host": urlparse(endpoint).netloc,
                        "via": "push_service"})
                return {"ok": True, "reason": "refreshed"}
            return {"ok": False, "reason": "endpoint belongs to another user"}
    rows.append({
        "user_id": int(user_id),
        "endpoint": endpoint,
        "keys": subscription.get("keys") or {},
        "ts": time.time(),
    })
    _save(SUBS_KEY, rows)
    # Sep 26 E2E acceptance: subscription creation is a key user action
    # and must be auditable. Only the push-service HOST is logged - the
    # full endpoint carries opaque device tokens and stays in the store.
    _audit(user_id, "push_subscribe",
           {"reason": "subscribed",
            "endpoint_host": urlparse(endpoint).netloc,
            "via": "push_service"})
    return {"ok": True, "reason": "subscribed"}


def unsubscribe(user_id: int, endpoint: str) -> dict:
    rows = _load_subs()
    kept = [r for r in rows
            if not (r.get("endpoint") == endpoint
                    and int(r.get("user_id", -1)) == user_id)]
    if len(kept) == len(rows):
        return {"ok": False, "reason": "not found"}
    _save(SUBS_KEY, kept)
    _audit(user_id, "push_unsubscribe",
           {"endpoint_host": urlparse(endpoint).netloc,
            "via": "push_service"})
    return {"ok": True}


_rate_lock = threading.Lock()   # the rate store is read-modify-write;
                                # scheduler threads + the async loop can
                                # interleave without this (two same-user
                                # sends both passing the limit check)


def _rate_ok(user_id: int, event: str) -> bool:
    """Per-user, per-event-class sliding hourly window. The whole
    check-then-append is one LOCKED step (Sep 26 audit): the store is
    read-modify-write on a shared file, and an unlocked interleave let
    two same-user sends both pass the limit check."""
    now = time.time()
    with _rate_lock:
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


def send_test(user_id: int, category: str) -> dict:
    """THE PIPE TEST (Sep 26, user request): fire ONE real templated
    push of the chosen family (jackpot -> push_test, scout ->
    push_test_signal) through the SAME path a real event takes —
    subscription lookup, the opt-out gate, the rate limiter, the
    audit, VAPID delivery. Never invents a verdict: the caller gets
    the sender's own result ({sent, reason, dead}) and the category,
    so the client can tell 'you opted out' from 'no device
    registered' from 'sent'. The _test suffix keeps these probes out
    of the signal feed and the report's traffic rollup (scrape_
    recent_signals whitelists signal_buy/signal_exit only; the report
    counts non-test templates)."""
    # (the family set, NOT `category not in CATEGORY` - CATEGORY is
    # keyed by EVENT names, its values are the families)
    if category not in ("jackpot", "scout"):
        return {"ok": False, "reason": "unknown category"}
    event = "push_test" if category == "jackpot" else "push_test_signal"
    res = send_to_user(user_id, event)
    res["category"] = category
    res["ok"] = bool(res.get("sent"))
    return res


def send_to_user(user_id: int, event: str, extra: dict = None) -> dict:
    """Send one templated event to ONE user's devices. Never sends to
    the wrong user: the subscription is looked up by the user_id the
    caller is already authenticated as, and the payload carries only
    the template's text plus optional whitelisted interpolations."""
    ev = EVENTS.get(event)
    if ev is None:
        return {"sent": 0, "reason": "unknown event"}
    subs = [r for r in _load_subs() if r["user_id"] == user_id]
    if not subs:
        return {"sent": 0, "reason": "no subscription"}
    # THE OPT-OUT GATE (user order, Sep 25 evening VII): the user's
    # dashboard toggles decide, per category, before anything sends.
    # AUDITED (user flow, Sep 26): a suppressed notification is a
    # logged decision (send_blocked) — silence leaves no trace, and
    # the audit order matches the gate order: block -> rate-limit ->
    # send -> allow.
    cat = CATEGORY.get(event)
    # THE ENTITLEMENT GATE (Oct 24), before the opt-out gate: a signal is
    # the paid product, so a locked account never receives one however
    # its toggles read. Logged like every other suppression — a refused
    # signal is a decision, not silence.
    if event in SIGNAL_EVENTS and not db.has_scout_pro(user_id):
        _audit(user_id, "push_send_blocked",
               {"event": event, "category": cat,
                "reason": "scout_pro_required", "via": "push_service"})
        return {"sent": 0, "reason": "scout pro required"}
    if cat and not get_prefs(user_id).get(cat, True):
        _audit(user_id, "push_send_blocked",
               {"event": event, "category": cat,
                "reason": "opted_out", "via": "push_service"})
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
    # THE SCOPED BODY OVERRIDE (Sep 27, the weekly ops digest): the
    # template bodies are static, but the digest's whole value is its
    # per-week LINE. Scoped twice over: only the ops_digest event on
    # a send whose caller named _digest_line (scheduler leg 7) — no
    # user-facing path can carry arbitrary text into any other event,
    # and the width cap matches the extra-interpolation cap.
    if event == "ops_digest" and (extra or {}).get("_digest_line"):
        payload["body"] = str(extra["_digest_line"])[:200]
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
                # TTL is REQUIRED by WNS (every Edge/Windows device):
                # without it the push service answers 400 Bad Request
                # and the send dies as a false 'dead' endpoint (found
                # live, Sep 27 - the real admin device subscribed and
                # the test fire 400'd until this header existed; FCM
                # tolerates the absence, which is why sandbox drives
                # never saw it). 24h: alerts are day-scale.
                headers={"TTL": "86400"},
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
        _audit(user_id, "push_send_allowed",
               {"event": event, "category": cat, "sent": sent,
                "dead": dead, "via": "push_service"})
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
    subs = [r for r in _load_subs() if r["user_id"] > 0]
    if not subs:
        return {"sent": 0, "reason": "no subscription"}
    blocked_users = []
    if event in SIGNAL_EVENTS:
        # THE BROADCAST GATE (Oct 24): a signal reaches Pro subscribers
        # only. Everyone else is counted and named in the audit row —
        # the operator can see the paid audience, and a locked account
        # never learns the signal fired.
        eligible = []
        for _r in subs:
            _uid = int(_r["user_id"])
            if db.has_scout_pro(_uid):
                eligible.append(_r)
            elif _uid not in blocked_users:
                blocked_users.append(_uid)
        subs = eligible
        if not subs:
            _audit(None, "push_broadcast",
                   {"event": event, "delivered": 0, "recipients": 0,
                    "scout_pro_blocked": len(blocked_users),
                    "reason": "scout_pro_required",
                    "via": "push_service"})
            return {"sent": 0, "reason": "scout pro required",
                    "eligible": 0, "blocked": len(blocked_users)}
    if not _rate_ok(0, event):
        # the broadcast limiter's refusal is a logged decision too —
        # an unaudited silent no-send would hide real suppression
        _audit(None, "push_rate_limited",
               {"event": event, "scope": "broadcast",
                "via": "push_service"})
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
                                    "scout_pro_blocked": len(blocked_users),
                                    "via": "push_service"})
    return {"sent": total, "eligible": len(_unique_users),
            "blocked": len(blocked_users)}


def send_admin_push(event: str, extra: dict = None) -> dict:
    """Fan one event out to ADMIN subscribers only (Sep 27: the
    orphan alert's surface - an infrastructure warning belongs to
    the operators, not every member's phone). Per-USER fan-out
    through send_to_user (all of an admin's endpoints, the opt-out
    gate, per-user rate limits, and the 410-pruning all apply
    unchanged); one admin_push audit row records the delivery.
    Pass extra={"_digest_line": str} to replace the template body
    for THIS send only (the weekly ops digest's dynamic line).
    """
    if event not in EVENTS:
        return {"sent": 0, "reason": "unknown event"}
    admins = [u["id"] for u in db.get_all_users() if u.get("is_admin")]
    subs = {int(r["user_id"]) for r in _load_subs()
            if int(r.get("user_id") or 0) > 0}
    targets = sorted(set(admins) & subs)
    if not targets:
        return {"sent": 0, "reason": "no admin subscriber"}
    total = 0
    for uid in targets:
        res = send_to_user(uid, event, extra)
        total += int(res.get("sent", 0))
    _audit(None, "push_admin_push",
           {"event": event, "delivered": total,
            "recipients": len(targets), "via": "push_service"})
    return {"sent": total, "recipients": len(targets)}


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
        # (Sep 26 audit) a hand-edited spool row can carry an unconvertible
        # target (a 10k-digit 'int'); a raise here would kill the drain
        try:
            result = (send_to_user(int(target), r["event"], r.get("extra"))
                      if isinstance(target, int) or
                      (isinstance(target, str) and target.isdigit())
                      else send_to_subscribers(r["event"], r.get("extra")))
        except (TypeError, ValueError, OverflowError):
            continue
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
                   WHERE action IN ('push_send_allowed', 'push_broadcast')
                   AND created_at >= ?
                   AND (action = 'push_broadcast'
                        OR (action = 'push_send_allowed' AND user_id = ?))
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
