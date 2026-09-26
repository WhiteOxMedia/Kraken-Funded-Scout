"""pool/scheduler.py — THE RECURRING LEG (Sep 26).

One daemon thread per server process that:

  1. runs the Kraken referral worker on an HOURLY cadence (operator-
     adjustable via SCHEDULER_INTERVAL_S, floor 300s) — skipping LOUDLY
     but once-per-gap when no provider is configured, so the audit log
     never fills with per-minute noise;
  2. fires kr.notify_window_closing() against the OPERATOR-DEFINED
     expiry calendar (KRAKEN_REFERRAL_WINDOW = {"expires_at": epoch}),
     notifying ONCE per expiry inside the final 48h — "send the user a
     notice before the window expires and no Referral bonus is gifted";
  3. stamps a heartbeat (SCHEDULER_HEARTBEAT) so the admin card can
     distinguish "alive" from "silently dead".

Every tick is usable synchronously (tick_once) so tests and the admin
trigger exercise the exact code the timer runs.
"""
import json
import threading
import time
import traceback

import database as db
import kraken_referral as kr
import push_service as push_svc

HEARTBEAT_KEY = "SCHEDULER_HEARTBEAT"        # epoch of last completed tick
LAST_WORKER_KEY = "SCHEDULER_LAST_WORKER"    # {"ts": epoch, "reason": str}
LAST_NOTICE_KEY = "SCHEDULER_LAST_NOTICE"    # expiry the notice last fired for
WINDOW_KEY = "KRAKEN_REFERRAL_WINDOW"        # {"expires_at": <epoch seconds>}
ENABLED_KEY = "SCHEDULER_ENABLED"            # "1" -> thread runs
INTERVAL_KEY = "SCHEDULER_INTERVAL_S"        # seconds between worker runs
DEFAULT_INTERVAL_S = 3600
NOTICE_WINDOW_S = 48 * 3600                  # notify inside the final 48h

_state = {"thread": None, "stop": None}


def _interval():
    try:
        v = int(float(db.get_setting(INTERVAL_KEY, DEFAULT_INTERVAL_S)
                      or DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_S
    return max(300, v)


def _enabled():
    return str(db.get_setting(ENABLED_KEY, "0") or "") == "1"


def window_status():
    """The operator-defined expiry calendar, read-only."""
    try:
        w = json.loads(db.get_setting(WINDOW_KEY, "{}") or "{}")
    except ValueError:
        w = {}
    if not isinstance(w, dict):
        w = {}
    exp = w.get("expires_at")
    try:
        exp = float(exp) if exp else 0
    except (TypeError, ValueError):
        exp = 0
    out = {"configured": exp > 0, "expires_at": exp or None}
    if exp > 0:
        out["expires_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(exp))
        out["seconds_remaining"] = round(exp - time.time(), 0)
        out["expired"] = time.time() >= exp
    return out


def _due_notice(win):
    """Due inside the final 48h, never after expiry — the moment to
    act has passed; an expired window goes quiet."""
    if not win.get("configured"):
        return False
    rem = win.get("seconds_remaining", 0)
    return 0 < rem <= NOTICE_WINDOW_S


def tick_once(reason: str = "manual") -> dict:
    """One scheduler cycle: worker leg (interval-gated) + notice leg
    (calendar-gated). Returns the summary the audits echo."""
    summary = {"worker": None, "notice": None}
    # 1. the worker leg
    try:
        last = json.loads(db.get_setting(LAST_WORKER_KEY, "{}") or "{}")
        if not isinstance(last, dict):
            last = {}
    except ValueError:
        last = {}
    gap_ok = (not last.get("ts")) or \
             (time.time() - float(last["ts"])) >= _interval() * 0.9
    if not gap_ok:
        summary["worker"] = {"skipped": "interval_not_elapsed"}
    elif kr.provider_mode() == "unconfigured":
        if last.get("reason") != "unconfigured":
            # exactly ONE skipped row per gap: the audit that says why
            # the projection is not moving, without flooding the log
            kr.emit_audit(None, "scheduler.worker_skipped",
                          reason="referral provider unconfigured")
        last = {"ts": time.time(), "reason": "unconfigured"}
        summary["worker"] = {"skipped": "unconfigured"}
    else:
        res = kr.run_worker()
        last = {"ts": time.time(), "reason": "ran"}
        kr.emit_audit(None, "scheduler.worker_ran",
                      mode=res.get("mode"),
                      processed=res.get("processed"),
                      errors=res.get("errors"),
                      seed=res.get("referral_seed"),
                      anomaly=res.get("anomaly"),
                      trigger=reason)
        summary["worker"] = res
    db.set_setting(LAST_WORKER_KEY, json.dumps(last))
    # 2. the notice leg — the operator's calendar decides; a window is
    # notified at most ONCE per expiry (marker), no matter how many
    # ticks land inside the final 48h
    win = window_status()
    if _due_notice(win):
        marker = str(db.get_setting(LAST_NOTICE_KEY, "") or "")
        if marker == str(win.get("expires_at")):
            summary["notice"] = {"skipped": "already_notified_for_this_expiry"}
        else:
            sent = kr.notify_window_closing()
            db.set_setting(LAST_NOTICE_KEY, str(win.get("expires_at")))
            summary["notice"] = {"expires_at": win.get("expires_at"),
                                 "sent": sent.get("sent", 0),
                                 "reason": sent.get("reason")}
            kr.emit_audit(None, "scheduler.notice_fired",
                          expires_at=win.get("expires_at"),
                          sent=sent.get("sent", 0),
                          reason=sent.get("reason"))
    elif summary["worker"] and not summary["worker"].get("skipped"):
        kr.emit_audit(None, "scheduler.notice_idle",
                      configured=win.get("configured"),
                      expired=win.get("expired", False))
    db.set_setting(HEARTBEAT_KEY, str(time.time()))
    return summary


def loop(stop_event):
    """Hourly cadence: first tick immediately, then every interval.
    The thread must never die silently — every exception surfaces as
    a scheduler.error audit row AND a stderr traceback."""
    while not stop_event.is_set():
        try:
            tick_once(reason="scheduler")
        except Exception:                                  # noqa: BLE001
            traceback.print_exc()
            try:
                kr.emit_audit(None, "scheduler.error",
                              error=traceback.format_exc()[-400:])
            except Exception:                              # noqa: BLE001
                traceback.print_exc()
        stop_event.wait(_interval())


def start() -> bool:
    """Idempotent: one daemon thread per process, started only when
    SCHEDULER_ENABLED=1 (the admin card / API flips it)."""
    if not _enabled():
        return False
    if _state["thread"] and _state["thread"].is_alive():
        return False
    _state["stop"] = threading.Event()
    t = threading.Thread(target=loop, args=(_state["stop"],),
                         daemon=True, name="kraken-scheduler")
    _state["thread"] = t
    t.start()
    kr.emit_audit(None, "scheduler.started",
                  interval_s=_interval(), enabled=True)
    return True


def stop(timeout: float = 3.0) -> bool:
    _state["stop"] = _state["stop"] or threading.Event()
    _state["stop"].set()
    t = _state["thread"]
    if t and t.is_alive():
        t.join(timeout=timeout)
    _state["thread"] = None
    return True


def status() -> dict:
    hb = float(db.get_setting(HEARTBEAT_KEY, "0") or 0)
    try:
        last = json.loads(db.get_setting(LAST_WORKER_KEY, "{}") or "{}")
        if not isinstance(last, dict):
            last = {}
    except ValueError:
        last = {}
    return {
        "enabled": _enabled(),
        "thread_alive": bool(_state["thread"] and _state["thread"].is_alive()),
        "interval_s": _interval(),
        "heartbeat_age_s": (round(time.time() - hb, 1) if hb else None),
        "last_worker": last,
        "window": window_status(),
        "last_notice_expiry":
            str(db.get_setting(LAST_NOTICE_KEY, "") or "") or None,
        "vapid": push_svc.vapid_status(),
    }
