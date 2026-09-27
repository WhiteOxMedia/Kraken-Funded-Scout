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
     distinguish "alive" from "silently dead";
  4. runs kr.credential_selftest() on a WEEKLY cadence (default
     604800s, operator-adjustable, floor one hour) so the stored
     posture verdict never goes stale: the proof the pair WORKS is
     refreshed by the same timer that runs the projection, through
     the SAME shared 30s throttle and audit trail the operator's
     button uses. Unconfigured burns no rate credit, so a bare box
     is checked weekly for free;
  5. prunes expired auth sessions DAILY (once per UTC day, the same
     marker pattern as the retention prune) — get_session already
     refuses expired tokens, so they are pure table growth; removed
     rows are audited with their count, and a no-op day is silent.

Every tick is usable synchronously (tick_once) so tests and the admin
trigger exercise the exact code the timer runs.
"""
import json
import sys
import threading
import time
import traceback

import database as db
import kraken_referral as kr
import push_report
import push_service as push_svc

HEARTBEAT_KEY = "SCHEDULER_HEARTBEAT"        # epoch of last completed tick
LAST_WORKER_KEY = "SCHEDULER_LAST_WORKER"    # {"ts": epoch, "reason": str}
LAST_NOTICE_KEY = "SCHEDULER_LAST_NOTICE"    # expiry the notice last fired for
WINDOW_KEY = "KRAKEN_REFERRAL_WINDOW"        # {"expires_at": <epoch seconds>}
ENABLED_KEY = "SCHEDULER_ENABLED"            # "1" -> thread runs
INTERVAL_KEY = "SCHEDULER_INTERVAL_S"        # seconds between worker runs
DEFAULT_INTERVAL_S = 3600
NOTICE_WINDOW_S = 48 * 3600                  # notify inside the final 48h
KC_ENABLED_KEY = "SCHEDULER_KC_ENABLED"      # "1" -> weekly self-test leg
KC_INTERVAL_KEY = "SCHEDULER_KC_INTERVAL_S"  # seconds between self-tests
KC_LAST_KEY = "SCHEDULER_LAST_KC"            # {"ts": epoch, "verdict": str}
KC_DEFAULT_INTERVAL_S = 7 * 86400            # the weekly default
KC_FLOOR_S = 3600                            # never dial Kraken more often

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


def _kc_enabled():
    # the weekly self-test defaults ON — an unattended verdict is how
    # a dead key hides; flipping OFF is the operator's explicit choice
    return str(db.get_setting(KC_ENABLED_KEY, "1") or "") == "1"


def _kc_interval_s():
    try:
        v = int(float(db.get_setting(KC_INTERVAL_KEY, KC_DEFAULT_INTERVAL_S)
                      or KC_DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        return KC_DEFAULT_INTERVAL_S
    return max(KC_FLOOR_S, v)


def _kc_due(last):
    ts = last.get("ts")
    try:
        ts = float(ts) if ts else 0.0
    except (TypeError, ValueError):
        ts = 0.0
    return (time.time() - ts) >= _kc_interval_s()


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
    # the weekly self-test's own marker, loaded with the others
    try:
        kcl = json.loads(db.get_setting(KC_LAST_KEY, "{}") or "{}")
        if not isinstance(kcl, dict):
            kcl = {}
    except ValueError:
        kcl = {}
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
    # 3. the daily push report: yesterday's completed day, recomputed
    # idempotently (REPLACE semantics — never double-counts), plus the
    # once-a-day retention prune
    try:
        yesterday = time.time() - 86400
        push_report.compute_day(yesterday)
        prune_marker = str(db.get_setting("PUSH_REPORT_PRUNED", "") or "")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if prune_marker != today:
            removed = push_report.prune()
            db.set_setting("PUSH_REPORT_PRUNED", today)
            if removed:
                kr.emit_audit(None, "push_report.pruned", rows=removed)
        summary["push_report"] = "rolled up"
    except Exception as exc:                           # noqa: BLE001
        summary["push_report"] = f"error: {str(exc)[:80]}"
        try:
            kr.emit_audit(None, "scheduler.error",
                          leg="push_report", error=str(exc)[:200])
        except Exception:                              # noqa: BLE001
            print("scheduler: push_report audit failed", file=sys.stderr)
    # 5. the daily session prune: once per UTC day (the retention
    # prune's marker pattern), silently no-op when nothing expired —
    # the audit log hears about real work only.
    try:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if str(db.get_setting("SESSIONS_PRUNED", "") or "") != today:
            removed = db.delete_expired_sessions()
            db.set_setting("SESSIONS_PRUNED", today)
            if removed:
                kr.emit_audit(None, "scheduler.sessions_pruned",
                              rows=removed)
            summary["sessions_pruned"] = removed
    except Exception as exc:                           # noqa: BLE001
        summary["sessions_pruned"] = f"error: {str(exc)[:80]}"
        try:
            kr.emit_audit(None, "scheduler.error",
                          leg="sessions_prune", error=str(exc)[:200])
        except Exception:                              # noqa: BLE001
            print("scheduler: sessions audit failed", file=sys.stderr)
    # 4. the weekly credential self-test leg: the SAME call the admin
    # key-check button makes (one signed read-only Balance probe, the
    # shared KC throttle in kraken_referral, verdict persisted to the
    # KC_LAST_KEY posture store, audited there) — its own interval
    # marker and cadence so the verdict the posture prints is never
    # stale. A throttled or unconfigured answer still stamps (both are
    # free) and is audited only once per gap, like the worker's skip.
    if _kc_enabled() and _kc_due(kcl):
        try:
            res = kr.credential_selftest()
            verdict = str(res.get("verdict") or "unknown")
            kcl = {"ts": time.time(), "verdict": verdict}
            summary["kc"] = {"verdict": verdict,
                              "tier": res.get("credentials_tier") or ""}
            if res.get("retry_after_s") is not None:
                summary["kc"]["retry_after_s"] = res["retry_after_s"]
            if verdict in ("throttled", "unconfigured"):
                # nothing was proven either way — the once-per-gap skip
                # audit, not a per-attempt result row
                kr.emit_audit(None, "scheduler.kc_skipped",
                              reason=verdict, interval_s=_kc_interval_s())
            else:
                kr.emit_audit(None, "scheduler.kc_ran",
                              verdict=verdict,
                              tier=res.get("credentials_tier") or "")
        except Exception as exc:                       # noqa: BLE001
            # the leg must never kill the tick (the worker leg's rule)
            summary["kc"] = f"error: {str(exc)[:80]}"
            try:
                kr.emit_audit(None, "scheduler.error",
                              leg="credential_selftest",
                              error=str(exc)[:200])
            except Exception:                          # noqa: BLE001
                print("scheduler: kc audit failed", file=sys.stderr)
    elif not _kc_enabled():
        summary["kc"] = {"skipped": "disabled"}
    db.set_setting(KC_LAST_KEY, json.dumps(kcl))
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
    try:
        kcl = json.loads(db.get_setting(KC_LAST_KEY, "{}") or "{}")
        if not isinstance(kcl, dict):
            kcl = {}
    except ValueError:
        kcl = {}
    return {
        "enabled": _enabled(),
        "thread_alive": bool(_state["thread"] and _state["thread"].is_alive()),
        "interval_s": _interval(),
        "heartbeat_age_s": (round(time.time() - hb, 1) if hb else None),
        "last_worker": last,
        "kc": {"enabled": _kc_enabled(), "interval_s": _kc_interval_s(),
               "last": kcl},
        "window": window_status(),
        "last_notice_expiry":
            str(db.get_setting(LAST_NOTICE_KEY, "") or "") or None,
        "vapid": push_svc.vapid_status(),
    }
