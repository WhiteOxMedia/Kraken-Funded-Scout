"""vrsc_price.py — THE LIVE VRSC/USD RATE (Oct 28).

WHY THIS FILE EXISTS: the June 25 rev of the funding bridge introduced a
second mineable coin, Verus Coin (VRSC), on a per-region download branch
(`api_miner_download`, api region rules Oct 28) — users whose `cf-ipcountry`
is US/CA get a Verus miner at na.luckpool.net:3956 instead of the Monero
XMRig at mine.xmrjackpot.com:3333. The payout bridge (`jackpot_funding.py`)
prices VRSC entries and covers the jackpot pool by converting VRSC at its
spot before the draw, at the rate THIS module fetches. No live VRSC price
existed outside this file, so the bridge was falling back to a pinned
number; this file exists so that pinned number stops being the only truth.

THE SOURCE: CoinGecko's PUBLIC simple/price API (`/simple/price`, one VRSC
read). A public read: no key, no signature, no order, nothing that can
move money. Unlike XMR, no fork of this repo has a Verus-custody exchange
attachment to cruise for spot, so CoinGecko's aggregated tick — the
reference the funding bridge treats as the conversion venue — is the only
defensible number to quote to miners.

THE CACHING CONTRACT (the site must never hammer the price API) — the
Oct 27 settings-in-memory-engine review order: identical to the xmr_price
sibling every number the pool quotes already honours:
  - at most ONE fetch per TTL (15 min);
  - single-flight: concurrent readers start at most one refresh between
    them, and the attempt is stamped in the DB before the fetch, so a
    second process on the same database is gated too;
  - a failed fetch is retried no sooner than RETRY_AFTER (120 s), so an
    outage costs one request every two minutes, not one per page view;
  - never in the request path: the refresh runs in a daemon thread and
    the reader is answered from cache (stale-while-revalidate), so a
    slow or dead feed can never stall a render;
  - a short in-process memo, because a caller that prices 200 ledger
    rows in a loop must not open 200 sqlite reads for one rate.

THE KEYS — a pinned rate can never be mistaken for a fetched one. A
AJAX reader onthe PAYOUT BRIDGE must never confuse a CoinGecko-supplied
VRSC number with an operator-pinned one, so all six live-state keys are
disjoint from the XMR set:
  VRSC_USD_RATE          the OPERATOR'S pinned rate. If set, it WINS:
                         nothing is fetched and the site quotes it.
  VRSC_USD_LIVE_RATE     the last GOOD fetched rate (what readers serve)
  VRSC_USD_LIVE_TS       when that rate was fetched
  VRSC_USD_LIVE_SRC      the venue that produced it
  VRSC_USD_LIVE_ATTEMPT  when the last attempt (good or bad) started
  VRSC_USD_LIVE_ERR      the last failure, for the operator to read

FAILURE IS NORMAL HERE. No network, CoinGecko down, a changed JSON shape
CoinGecko will print the message about when that happens — the reader
keeps serving the last good rate, and with nothing ever fetched it
returns the caller's fallback. This module never raises and never
invents a number.

THE EPISODE RECORD: the reader also writes the transitions
  vrsc_rate.served        what readers are served CHANGED:
                          live <-> stale <-> fallback, override on/off
                          (previous state, previous rate, new rate, age)
  vrsc_rate.fetch_failed  the feed broke (once per failure streak, with
                          the error and what was still being served)
  vrsc_rate.recovered     it came back — with the recovered rate, the
                          number of failed attempts and the outage length

A refresh that leaves the state alone logs nothing, so the log grows
with events, not with page views — and a state must LAST STICKY_SECONDS
(120) before it counts, because the first read past every TTL is
answered stale for the second the refresh takes. Settings carry the
memory across processes and restarts: VRSC_USD_RATE_STATE (last served
state), VRSC_USD_RATE_CANDIDATE (a state not yet stuck) and
VRSC_USD_RATE_FAILS ("count|first_failure_ts"). Every audit write is
best-effort: a logging failure never takes a page down.

KILL SWITCH: VRSC_PRICE_LIVE=0 disables fetching entirely (offline
boxes; test runs that must not touch the network). Reads still honour
the pinned rate and the last cached value.
"""
import json
import math
import os
import threading
import time
import urllib.request

import database as db

API_URL = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=verus-coin&vs_currencies=usd")
CG_PARAM = "verus-coin"
SOURCE = "coingecko"

TTL_SECONDS = 900.0            # a fetched rate is good for 15 minutes
RETRY_AFTER_SECONDS = 120.0    # after a failure, wait this long
TIMEOUT_SECONDS = 3.0          # a dead feed costs one request, not a page
MEMO_SECONDS = 5.0             # per-process memo, not a second cache tier

OVERRIDE_KEY = "VRSC_USD_RATE"
RATE_KEY = "VRSC_USD_LIVE_RATE"
TS_KEY = "VRSC_USD_LIVE_TS"
SRC_KEY = "VRSC_USD_LIVE_SRC"
ATTEMPT_KEY = "VRSC_USD_LIVE_ATTEMPT"
ERR_KEY = "VRSC_USD_LIVE_ERR"
# THE EPISODE RECORD (Oct 28): what the site last SERVED and how many
# fetches have failed in a row. Transitions are written to audit_log so a
# bad-rate episode can be reconstructed after the fact.
STATE_KEY = "VRSC_USD_RATE_STATE"
FAILS_KEY = "VRSC_USD_RATE_FAILS"
PENDING_KEY = "VRSC_USD_RATE_PENDING"     # a transition the log refused
CANDIDATE_KEY = "VRSC_USD_RATE_CANDIDATE"   # a state that has not stuck yet
STICKY_SECONDS = 120.0   # a state must last this long to count as served

_lock = threading.Lock()
_inflight = False
_memo = {"at": 0.0, "value": None}
_state_lock = threading.Lock()


# ── settings helpers (a settings failure is never a page failure) ─────
def _setting(key, default=None):
    try:
        return db.get_setting(key, default)
    except Exception:
        return default


def _write(key, value):
    try:
        db.set_setting(key, value)
    except Exception:
        pass


def _positive(raw):
    """A finite, positive float, or None. Rejects '', 0, negative and
    the two infinities — every one of which would price the pot at a
    number nobody meant."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value) or value <= 0:
        return None
    return value


# ── the three tiers, in the order they win ────────────────────────────
def override():
    """The operator's pinned rate, or None."""
    return _positive(_setting(OVERRIDE_KEY))


def live_enabled():
    """FETCHING is on unless VRSC_PRICE_LIVE says otherwise. Default on:
    a pot that is covered by converting VRSC needs a real rate without
    being asked."""
    raw = str(os.environ.get("VRSC_PRICE_LIVE", "1")).strip().lower()
    return raw not in ("0", "false", "off", "no")


def last_good():
    """(rate, fetched_at, source) of the last GOOD fetch; Nones if the
    box has never fetched one."""
    rate = _positive(_setting(RATE_KEY))
    if rate is None:
        return None, None, None
    return rate, (_positive(_setting(TS_KEY)) or 0.0), str(_setting(SRC_KEY) or SOURCE)


# ── the fetch ─────────────────────────────────────────────────────────
def _fetch():
    """One public CoinGecko simple/price read: the VRSC/USD spot.
    Raises on anything unexpected — the caller records the failure."""
    req = urllib.request.Request(
        API_URL, headers={"User-Agent": "xmrjackpot-pool/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    row = (data or {}).get(CG_PARAM)
    if row is None and data:
        # CoinGecko keys the body by coin id; the one pair it served is
        # the only defensible row to take (defensive against a rename).
        row = next(iter(data.values()), None)
    price = _positive((row or {}).get("usd"))
    if price is None:
        raise ValueError("CoinGecko body carried no usd price")
    return price


# ── the episode record ────────────────────────────────────────────────
# Two different questions, two different rows:
#   vrsc_rate.served       the state a reader was answered with CHANGED
#                          (live -> stale -> fallback, override on/off)
#   vrsc_rate.fetch_failed the feed broke  (first failure of a streak)
#   vrsc_rate.recovered    it came back (and the value it came back with)
# A routine refresh that leaves the state alone writes nothing, so the
# log stays proportional to events rather than to page views. Every
# write is wrapped: rate logging must never break a page.
def _audit(action, **details):
    """One audit row. Returns True when it landed: a caller that must
    not lose the event can keep it and retry."""
    try:
        db.add_audit_log(None, action,
                         json.dumps(details, separators=(",", ":"),
                                    default=str), "")
        return True
    except Exception:
        return False


def _flush_pending():
    """Retry a transition the audit writer refused last time. State is
    written first and the row second, so a refusal loses NOTHING: the
    details wait in settings until a write goes through."""
    raw = _setting(PENDING_KEY)
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except Exception:
        _write(PENDING_KEY, "")
        return
    if _audit("vrsc_rate.served", **(payload or {})):
        _write(PENDING_KEY, "")


def _state_of(value):
    """The four things a reader can be served, as a name."""
    if value.get("source") == "override":
        return "override"
    if value.get("source") == "default":
        return "fallback"
    return "stale" if value.get("stale") else "live"


def _candidate():
    raw = _setting(CANDIDATE_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _stored_state():
    raw = _setting(STATE_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def note_state(value, now=None):
    """Write ONE audit row when the served state has really changed.

    A CHANGE HAS TO STICK BEFORE IT IS AN EVENT. Every 15 minutes the
    first read past the TTL is answered stale while the refresh it just
    started runs — a sub-second blip that is the cache working, not an
    episode. Logging those would write rows forever and bury the one bad
    day an operator is looking for. So a new state becomes a CANDIDATE
    first, and is only recorded as served once it has been observed for
    STICKY_SECONDS; `state_since` carries the moment it actually began,
    so the delay costs nothing in reconstruction. A blip that returns to
    the logged state simply drops its candidate.

    `previous` is None on the first observation after a restart or a
    fresh database, which is logged at once: the site came up on the
    fallback is an event, not a blip. Returns the row's details when it
    logged, else None — tests read that; readers ignore it.
    """
    now = time.time() if now is None else now
    state = _state_of(value)
    try:
        rate = float(value.get("rate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    as_of = value.get("as_of")
    try:
        age = int(max(0, now - float(as_of))) if as_of else None
    except (TypeError, ValueError):
        age = None
    with _state_lock:
        _flush_pending()
        prev = _stored_state()
        cand = _candidate()
        if prev and prev.get("state") == state:
            if cand:
                _write(CANDIDATE_KEY, "")    # the blip is over
            return None
        if prev is None:
            # nothing recorded yet: coming up on a fallback IS the event
            since = now
        else:
            if not cand or cand.get("state") != state:
                _write(CANDIDATE_KEY, json.dumps(
                    {"state": state, "since": now, "rate": round(rate, 8),
                     "source": value.get("source")},
                    separators=(",", ":")))
                return None
            try:
                since = float(cand.get("since") or now)
            except (TypeError, ValueError):
                since = now
            if (now - since) < STICKY_SECONDS:
                return None
        details = {
            "state_since": since,
            "previous": (prev or {}).get("state"),
            "previous_rate": (prev or {}).get("rate"),
            "previous_as_of": (prev or {}).get("as_of"),
            "state": state,
            "rate": round(rate, 8),
            "source": value.get("source"),
            "age_s": age,
            "stale": bool(value.get("stale")),
        }
        _write(STATE_KEY, json.dumps(
            {"state": state, "rate": round(rate, 8),
             "source": value.get("source"), "as_of": as_of,
             "at": now}, separators=(",", ":")))
        if not _audit("vrsc_rate.served", **details):
            # the writer was down: keep the event rather than lose it
            _write(PENDING_KEY, json.dumps(details, separators=(",", ":"),
                                           default=str))
        _write(CANDIDATE_KEY, "")
    return details


def _fails():
    """(count, first_failure_ts) of the current failure streak."""
    raw = str(_setting(FAILS_KEY) or "")
    count, _, since = raw.partition("|")
    try:
        return int(count or 0), (float(since) if since else None)
    except (TypeError, ValueError):
        return 0, None


def _note_failure(exc):
    """Count the failure; audit only the FIRST of a streak."""
    count, since = _fails()
    since = since or time.time()
    _write(FAILS_KEY, "%d|%.3f" % (count + 1, since))
    if count == 0:
        prev = _stored_state() or {}
        _audit("vrsc_rate.fetch_failed",
               error=str(exc)[:180],
               serving=prev.get("state"),
               serving_rate=prev.get("rate"),
               serving_age_s=(None if not prev.get("as_of") else
                              int(max(0, time.time() - float(prev["as_of"])))))


def _note_recovery(rate):
    """A success after failures: the recovered value, and the outage."""
    count, since = _fails()
    if count:
        _audit("vrsc_rate.recovered", rate=round(float(rate), 8),
               failed_attempts=count,
               outage_s=(int(max(0, time.time() - since)) if since else None),
               source=SOURCE)
    _write(FAILS_KEY, "")


def _fetch_and_store():
    """One fetch plus its outcome written down. The attempt is stamped
    BEFORE the request, so a slow or hung feed still gates the retry
    window (and gates other processes sharing the database)."""
    _write(ATTEMPT_KEY, "%.3f" % time.time())
    try:
        rate = _fetch()
    except Exception as exc:
        _write(ERR_KEY, "%d|%s" % (int(time.time()), str(exc)[:180]))
        _note_failure(exc)
        return None
    _write(RATE_KEY, "%.8f" % rate)
    _write(TS_KEY, "%.3f" % time.time())
    _write(SRC_KEY, SOURCE)
    _write(ERR_KEY, "")
    _note_recovery(rate)
    _memo["value"] = None       # next read resolves from the fresh value
    return rate


def _worker():
    global _inflight
    try:
        _fetch_and_store()
    finally:
        with _lock:
            _inflight = False


def refresh_now(force=False):
    """Fetch ONCE, synchronously, and record the outcome.

    force=True ignores the kill switch and the pinned rate (the manual
    'refresh the rate now' button, and the tests). Returns the new rate,
    or None when the fetch failed or was not attempted.
    """
    global _inflight
    if not force and (not live_enabled() or override() is not None):
        return None
    with _lock:
        if _inflight:
            return None
        _inflight = True
    try:
        return _fetch_and_store()
    finally:
        with _lock:
            _inflight = False


def _maybe_refresh(now):
    """Start at most one background refresh, only when the cache is
    stale and the retry window has passed. Returns True if it started
    (tests use that; readers do not wait on it)."""
    global _inflight
    if not live_enabled() or override() is not None:
        return False
    rate, ts, _src = last_good()
    if rate is not None and (now - ts) < TTL_SECONDS:
        return False
    attempt = _positive(_setting(ATTEMPT_KEY)) or 0.0
    if (now - attempt) < RETRY_AFTER_SECONDS:
        return False
    with _lock:
        if _inflight:
            return False
        _inflight = True
    threading.Thread(target=_worker, name="vrsc-price", daemon=True).start()
    return True


# ── the reader ────────────────────────────────────────────────────────
def current(fallback):
    """The rate to quote, with its provenance. NEVER raises.

    Returns {"rate": float, "source": "override"|"coingecko"|"default",
    "as_of": epoch|None, "stale": bool}. "stale" is True when the number
    being served is older than the TTL or is the fallback — the caller
    may label it, but must never present a stale number as freshly
    fetched.
    """
    now = time.time()
    memo = _memo.get("value")
    if memo is not None and (now - _memo["at"]) < MEMO_SECONDS:
        return dict(memo)

    try:
        pinned = override()
        if pinned is not None:
            value = {"rate": pinned, "source": "override",
                     "as_of": None, "stale": False}
        else:
            rate, ts, src = last_good()
            fresh = rate is not None and (now - ts) < TTL_SECONDS
            _maybe_refresh(now)
            if rate is None:
                value = {"rate": float(fallback), "source": "default",
                         "as_of": None, "stale": True}
            else:
                value = {"rate": rate, "source": src or SOURCE,
                         "as_of": ts, "stale": not fresh}
    except Exception:
        # the cache chain itself broke: the caller's own constant is
        # still better than an exception on a page render
        value = {"rate": float(fallback), "source": "default",
                 "as_of": None, "stale": True}

    # THE EPISODE RECORD: a change in what readers are served is logged
    # once, here, where the answer is actually decided — after it has
    # proved it stuck (see note_state).
    try:
        note_state(value, now)
    except Exception:
        pass

    _memo["at"], _memo["value"] = now, dict(value)
    return value


def reset():
    """Drop the per-process memo and the in-flight flag (tests)."""
    global _inflight
    with _lock:
        _inflight = False
    _memo["at"], _memo["value"] = 0.0, None
