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
     the SAME shared 30s throttle and     audit trail the operator's button uses. Unconfigured burns no rate credit, so a bare box
     is checked weekly for free;
  5. prunes expired auth sessions DAILY (once per UTC day, the same
     marker pattern as the retention prune) — get_session already
     refuses expired tokens, so they are pure table growth; removed
     rows are audited with their count, and a no-op day is silent;
  6. runs sweep_lib.orphans() WEEKLY (default 604800s, floor one
     hour, its own SCHEDULER_ORPHAN_* dial + marker) so orphan
     build-up is reported by the timer, not the next manual sweep:
     a clean week is silent, any violation count prints ONE
     scheduler.orphans_report audit row per week (with the per-edge
     violations and sample rowids) and carries alert=true in the
     tick summary + admin status, and is fanned out to admin
     web-push subscribers (push_service.send_admin_push, Sep 27);
  7. sends the WEEKLY OPS DIGEST (its own SCHEDULER_DIGEST_* dial +
     marker, Sep 27): ONE line — this week's orphan totals, the
     prune counts (retention + sessions), and the Kraken weekly
     verdict (count + last verdict) — pushed to the ADMIN
     subscribers and receipted as ONE scheduler.ops_digest audit
     row per week, clean weeks included (the all-clear IS the
     value; a missing digest is what the dead-man pattern is for).
     The digest window anchors to the SAME Sunday 06:15 local
     boundary as the orphan scan, on purpose: it composes from the
     audit LEDGER inside the window, so it reports the fresh scan
     this week actually ran, not a stale one. No SMTP transport
     exists in this stack, so "email or push" resolves to push —
     the delivery target is the same proven WNS/web-push pipe the
     orphan alert uses, with send_admin_push("ops_digest",
     extra={"_digest_line": ...}) overriding the template body
     for this send only.

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

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
if str(_ROOT / "tools") not in _sys.path:
    # the sweep discipline and the task registrar live in tools/ (Oct
    # 29 root organization); the weekly orphan leg reads them there.
    _sys.path.insert(0, str(_ROOT / "tools"))
import sweep_lib
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
ORPHAN_ENABLED_KEY = "SCHEDULER_ORPHAN_ENABLED"   # "1" -> weekly report
ORPHAN_INTERVAL_KEY = "SCHEDULER_ORPHAN_INTERVAL_S"
ORPHAN_LAST_KEY = "SCHEDULER_LAST_ORPHANS"   # {"ts", "total", "edges"}
ORPHAN_DEFAULT_INTERVAL_S = 7 * 86400        # the weekly default
ORPHAN_FLOOR_S = 3600                        # never scan more often
# the scheduled task's own last-ACTUAL-run stamp (epoch; written
# by run_sweep_report_task.ps1 only AFTER a real receipt lands)
ORPHAN_TASK_MARKER = _ROOT / "task_sweep_report_last.txt"
ORPHAN_DEADMAN_MAX_AGE_S = 8 * 86400  # one week + a day of slack
# (must match register_sweep_report_task.py DEADMAN_MAX_AGE_S)
DIGEST_ENABLED_KEY = "SCHEDULER_DIGEST_ENABLED"    # "1" -> weekly digest
DIGEST_INTERVAL_KEY = "SCHEDULER_DIGEST_INTERVAL_S"
DIGEST_LAST_KEY = "SCHEDULER_LAST_DIGEST"   # {"ts", "line", "push"}
DIGEST_DEFAULT_INTERVAL_S = 7 * 86400       # the weekly default
DIGEST_FLOOR_S = 3600                       # never spam more often
DIGEST_AUDIT_ACTION = "scheduler.ops_digest"  # ONE receipt row/week

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


def _orphan_enabled():
    # the weekly orphan report defaults ON - an unreported orphan
    # build-up is exactly how 287 rows pile up unnoticed again
    return str(db.get_setting(ORPHAN_ENABLED_KEY, "1") or "") == "1"


def _orphan_interval_s():
    try:
        v = int(float(db.get_setting(ORPHAN_INTERVAL_KEY,
                                      ORPHAN_DEFAULT_INTERVAL_S)
                      or ORPHAN_DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        return ORPHAN_DEFAULT_INTERVAL_S
    return max(ORPHAN_FLOOR_S, v)


def _orphan_due(last):
    """THE SCAN-WINDOW CONTRACT (Sep 27 alignment): at the weekly dial
    the leg is due when the marker predates the LAST local window
    boundary the Task Scheduler job fires on
    (register_sweep_report_task.SCAN_DOW/SCAN_HOUR/SCAN_MINUTE - one
    window, two readers, both surfaces scan the same day even after a
    mid-week server restart). Sub-weekly operator dials keep pure
    interval semantics (a faster dial must not silently skip a week)."""
    interval = _orphan_interval_s()
    if interval < 7 * 86400:
        try:
            ts = float(last.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return (time.time() - ts) >= interval
    import register_sweep_report_task as _reg
    try:
        ts = float(last.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    return _orphan_window_index(time.time()) > _orphan_window_index(ts)


def _orphan_window_index(epoch_s):
    """Epoch of the last SCAN window boundary that has fully passed by
    epoch_s, computed from the LOCAL calendar (DST cannot move the
    day: the boundary is rebuilt per call, never by fixed offsets).
    Comparing the marker's window to now's window is the once-per-week
    rule; a future-window-this-week case folds back one week."""
    import datetime
    import register_sweep_report_task as _reg
    if epoch_s < 86400:
        # no/unparseable marker: long before any real window (mktime
        # cannot build pre-epoch local calendars on every platform)
        return 0.0
    lt = time.localtime(epoch_s)
    days_since = (lt.tm_wday - _reg.SCAN_DOW) % 7
    d = (datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday)
         - datetime.timedelta(days=days_since))
    boundary = time.mktime((d.year, d.month, d.day,
                            _reg.SCAN_HOUR, _reg.SCAN_MINUTE,
                            0, 0, 0, -1))
    if epoch_s < boundary:
        boundary -= 7 * 86400
    return boundary


def _digest_enabled():
    # the weekly digest defaults ON — the summary an operator never
    # has to remember to ask for; flipping OFF is the explicit choice
    return str(db.get_setting(DIGEST_ENABLED_KEY, "1") or "") == "1"


def _digest_interval_s():
    try:
        v = int(float(db.get_setting(DIGEST_INTERVAL_KEY,
                                     DIGEST_DEFAULT_INTERVAL_S)
                      or DIGEST_DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        return DIGEST_DEFAULT_INTERVAL_S
    return max(DIGEST_FLOOR_S, v)


def _digest_window_index(epoch_s):
    """The digest window IS the scan window (the same Sunday 06:15
    local boundary the orphan leg and the Task Scheduler job read).
    One window, three readers: the digest must compose from THIS
    window's fresh orphan scan, not last week's."""
    return _orphan_window_index(epoch_s)


def _digest_due(last):
    """The orphan leg's due-shape: at the weekly dial the marker
    predating the last window boundary is due (restart-proof, DST-
    safe); sub-weekly operator dials keep pure interval semantics
    (a fast dial must not silently skip a week)."""
    interval = _digest_interval_s()
    if interval < 7 * 86400:
        try:
            ts = float(last.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return (time.time() - ts) >= interval
    try:
        ts = float(last.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    return _digest_window_index(time.time()) > _digest_window_index(ts)


def _digest_compose():
    """THE ONE LINE, from the audit LEDGER (not from leg state): the
    orphan scan's receipt row inside this window, the prune rows,
    and the weekly KC verdicts — the log is what actually ran, so
    the digest cannot describe a week the legs did not have. Clean
    sources print as 0/none; the line is the all-clear then."""
    win = _digest_window_index(time.time())
    orphans = "?"
    edges = 0
    try:
        orl = json.loads(db.get_setting(ORPHAN_LAST_KEY, "{}") or "{}")
        if isinstance(orl, dict) and \
                _digest_window_index(float(orl.get("ts") or 0.0)) >= win \
                and "total" in orl:
            orphans = str(orl.get("total"))
            edges = int(orl.get("edges") or 0)
    except (TypeError, ValueError):
        pass
    pruned = 0
    sessions = 0
    kc_last = "none"
    kc_n = 0
    try:
        with db.get_db() as conn:
            for r in conn.execute(
                    "SELECT action, details, created_at FROM audit_log "
                    "WHERE action IN ('push_report.pruned', "
                    "'scheduler.sessions_pruned', "
                    "'scheduler.kc_ran') AND created_at >= ?",
                    (win,)).fetchall():
                try:
                    d = json.loads(r["details"] or "{}")
                except (TypeError, ValueError):
                    d = {}
                if r["action"] == "push_report.pruned":
                    pruned += int(d.get("rows") or 0)
                elif r["action"] == "scheduler.sessions_pruned":
                    sessions += int(d.get("rows") or 0)
                else:
                    kc_n += 1
                    kc_last = str(d.get("verdict") or "?")
    except Exception:                              # noqa: BLE001
        # a read failure degrades to unknown markers, never kills
        # the tick (the orphan-leg rule)
        pruned = sessions = "?"
    return (f"orphans={orphans} ({edges} edges) | "
            f"pruned={pruned}+{sessions} | "
            f"kc={kc_last} (x{kc_n})"), {
        "orphans": orphans, "edges": edges, "pruned": pruned,
        "sessions": sessions, "kc": kc_last, "kc_count": kc_n,
    }


def _deliver_digest(line: str) -> int:
    """Push the digest to the ADMIN subscribers through the same
    proven sender the orphan alert uses. No SMTP transport exists in
    this stack (smtplib appears nowhere), so 'email or push'
    resolves to push. The dynamic line rides extra._digest_line —
    a body override only this caller names. A failed send is
    audited by the sender's own path and returned as sent:0; the
    digest leg itself must never die on the pipe."""
    try:
        res = push_svc.send_admin_push("ops_digest",
                                       extra={"_digest_line": line})
        return int(res.get("sent", 0))
    except Exception:                              # noqa: BLE001
        return 0


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
    # 6. the weekly orphan-report leg: sweep_lib.orphans() through a
    # mode=ro connection - a READER the timer may run against the
    # live primary with zero write access. Silence on a clean week
    # (the prune no-op-day rule); any violation count speaks ONCE
    # per interval and carries alert=true so the admin card can
    # demand action instead of waiting for the next manual sweep.
    try:
        orl = json.loads(db.get_setting(ORPHAN_LAST_KEY, "{}") or "{}")
        if not isinstance(orl, dict):
            orl = {}
    except ValueError:
        orl = {}
    if _orphan_enabled() and _orphan_due(orl):
        try:
            edges = sweep_lib.orphans(db.DB_PATH)
            total = sum(e["violations"] for e in edges)
            orl = {"ts": time.time(), "total": total,
                   "edges": len(edges)}
            summary["orphans"] = {"total": total, "edges": len(edges),
                                  "alert": bool(total)}
            if total:
                kr.emit_audit(None, "scheduler.orphans_report",
                              total=total,
                              edges=json.dumps(edges)[:800])
                # the web-push surface (Sep 27): the audit row sits in
                # the DB until an admin looks; the push lands on the
                # phone. ADMIN subscribers only (send_admin_push), and
                # the leg must never die on the push - the alert
                # already exists in the audit trail either way, and a
                # failed send is audited, not swallowed.
                try:
                    pres = push_svc.send_admin_push("orphans_alert")
                    summary["orphans"]["push"] = int(pres.get("sent", 0))
                except Exception as pexc:          # noqa: BLE001
                    summary["orphans"]["push"] = "error"
                    try:
                        kr.emit_audit(None, "scheduler.error",
                                      leg="orphan_push",
                                      error=str(pexc)[:200])
                    except Exception:              # noqa: BLE001
                        print("scheduler: orphan push audit failed",
                              file=sys.stderr)
            # a clean week stamps its marker silently
        except Exception as exc:                   # noqa: BLE001
            # the leg must never kill the tick; the marker stays
            # unstamped so the scan is RETRIED next tick
            summary["orphans"] = f"error: {str(exc)[:80]}"
            try:
                kr.emit_audit(None, "scheduler.error",
                              leg="orphan_report",
                              error=str(exc)[:200])
            except Exception:                      # noqa: BLE001
                print("scheduler: orphan audit failed",
                      file=sys.stderr)
    elif not _orphan_enabled():
        summary["orphans"] = {"skipped": "disabled"}
    db.set_setting(ORPHAN_LAST_KEY, json.dumps(orl))
    # 6b. THE DEAD-MAN: the out-of-process Task Scheduler job is
    # the second alert surface, and a scheduled task that STOPS
    # running is the one failure no report can reveal. The
    # wrapper's marker file (stamped only after a real receipt)
    # is policed every tick: missing, unreadable, or older than
    # ORPHAN_DEADMAN_MAX_AGE_S -> alert=true in the summary and
    # ONE scheduler.orphans_deadman audit row per day (the prune
    # marker pattern). A fresh marker is silent.
    try:
        _today = time.strftime("%Y-%m-%d", time.gmtime())
        _age = None
        try:
            _age = (time.time()
                    - float(ORPHAN_TASK_MARKER.read_text(
                        encoding="ascii").strip()))
        except (OSError, ValueError):
            _age = None
        if _age is None or _age > ORPHAN_DEADMAN_MAX_AGE_S:
            summary["orphans_deadman"] = {
                "alert": True,
                "marker_age_days": (round(_age / 86400, 1)
                                    if _age is not None else None)}
            if str(db.get_setting("ORPHAN_DEADMAN_ALERTED", "")
                   or "") != _today:
                kr.emit_audit(None, "scheduler.orphans_deadman",
                              marker=("missing" if _age is None
                                      else f"{_age / 86400:.1f}d"))
                db.set_setting("ORPHAN_DEADMAN_ALERTED", _today)
    except Exception as exc:                       # noqa: BLE001
        try:
            kr.emit_audit(None, "scheduler.error",
                          leg="orphans_deadman",
                          error=str(exc)[:200])
        except Exception:                          # noqa: BLE001
            print("scheduler: deadman audit failed", file=sys.stderr)
    # 7. THE WEEKLY OPS DIGEST: one line — orphans, prune counts,
    # the Kraken weekly verdict — pushed to the ADMIN subscribers
    # and receipted as ONE scheduler.ops_digest row per week, clean
    # weeks included (the all-clear IS the value; a week with no
    # digest row is the gap the dead-man pattern polices elsewhere).
    # The window boundary shared with the orphan scan guarantees the
    # digest composes from THIS window's fresh scan, and a compose/
    # send failure leaves the marker unstamped so the digest is
    # RETRIED next tick (the credential-leg rule).
    try:
        dig = json.loads(db.get_setting(DIGEST_LAST_KEY, "{}") or "{}")
        if not isinstance(dig, dict):
            dig = {}
    except ValueError:
        dig = {}
    if _digest_enabled() and _digest_due(dig):
        try:
            line, parts = _digest_compose()
            sent = _deliver_digest(line)
            summary["ops_digest"] = {"line": line, "push": sent,
                                     **parts}
            kr.emit_audit(None, DIGEST_AUDIT_ACTION,
                          line=line, parts=json.dumps(parts)[:400])
            dig = {"ts": time.time(), "line": line, "push": sent}
        except Exception as exc:                   # noqa: BLE001
            # the leg must never kill the tick; the marker stays
            # unstamped so the digest is RETRIED next tick
            summary["ops_digest"] = f"error: {str(exc)[:80]}"
            try:
                kr.emit_audit(None, "scheduler.error",
                              leg="ops_digest",
                              error=str(exc)[:200])
            except Exception:                      # noqa: BLE001
                print("scheduler: digest audit failed",
                      file=sys.stderr)
    elif not _digest_enabled():
        summary["ops_digest"] = {"skipped": "disabled"}
    db.set_setting(DIGEST_LAST_KEY, json.dumps(dig))
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
        "orphans": {"enabled": _orphan_enabled(),
                    "interval_s": _orphan_interval_s(),
                    "last": json.loads(
                        db.get_setting(ORPHAN_LAST_KEY, "{}") or "{}")},
        "digest": {"enabled": _digest_enabled(),
                   "interval_s": _digest_interval_s(),
                   "last": json.loads(
                       db.get_setting(DIGEST_LAST_KEY, "{}") or "{}")},
        "window": window_status(),
        "last_notice_expiry":
            str(db.get_setting(LAST_NOTICE_KEY, "") or "") or None,
        "vapid": push_svc.vapid_status(),
    }
