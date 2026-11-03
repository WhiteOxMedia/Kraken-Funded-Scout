"""THE SILENT DOCK TRIPWIRE (ops tool, Oct 8, Tripwire A): a phone
ping when the silent-rotation dock pushes discipline_penalty_extra
past 2.0 (user ask Oct 8, latched ONCE PER DAY like the extension
bell — a thrashing penalty is the lesson's DOSE, a nagging alert is
noise; but a SILENT thrash is the exact blind this tripwire exists
to catch). The threshold rides the dock's OWN units: 2.0 is the same
number the score's silent-rotation dock uses (the dock's max dose,
the honest silence vs the funnel-being-right line). The trip fires
ONE high-priority push through the SAME private ntfy pipe as the
GET OUTs (push_topic.txt), then the ONE second attempt ~60s later
(the 16:05 lesson, same as every push that must land), then the
watcher is DONE for the day (the latch, day-keyed: it wakes again
tomorrow at the next rotation).

WHAT WATCHES: every --interval seconds poll meta_layers.silent_
penalty_ambient() (the reader, the honest seam — DISCIPLINE PENALTY
EXTRA + the rcl/silence SOURCE label). If live_penalty > 2.0 AND
the day-key hasn't fired, DISPATCH: (RML, RA) = the reader's source
labels; the push body carries the source, the penalty, the dose.

HONESTY RAILS (the price tripwire's own rails, inherited):
  - the trip is JUDGED BY THE SOURCE: a dose with a source label of
    RML (the drift verdict) is a drift-dock ding (the dose says
    "the reentry gate priced this against reality" — not silent);
    a dose under RA is the SILENT-ROTATION dock itself (the funnel
    buried payers behind a silent rotation — the exact blind the
    tripwire exists for). The push body CARRIES the label either
    way; the phone is never lied to about the dose's source.
  - a failed push keeps the trip ARMED and retries (up to 30);
    a dead pipe must not silently swallow the trip.
  - the LATCH is day-keyed, a fresh day-latch re-arms (tomorrow's
    silent dock re-alarms a fresh morning, like the extension bell).
  - the drive calls _dry_run() (a hermetic sensor without side
    effects); production calls run() — the ambient reader without
    the dry-run seam is the honest production sensor.

READ-ONLY ON THE FIRM: touches no state the scanner, bot or console
owns — the meta_layers reader is ITS OWN state (the dry_run seam
never touches the live file). Its own runtime state
(silent_alert.lock, silent_alert_state.json) is gitignored.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MST = ZoneInfo("America/Denver")
LOCK_FILE = ROOT / "silent_alert.lock"
STATE_FILE = ROOT / "silent_alert_state.json"
TOPIC_FILE = ROOT / "push_topic.txt"

THRESHOLD = 2.0     # the ask, literal: past 2.0
SEND_RETRY_CAP = 30
PUSH_TIMEOUT_S = 10


def _now() -> datetime:
    return datetime.now(MST)


def _log(msg: str) -> None:
    print(f"{_now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _pid_alive_python(pid: int) -> bool:
    """The recycled-pid trap, the cheap way (tasklist sees ANY process)."""
    if not pid or pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        return "python" in out.lower()
    except Exception:
        return False


def claim_lock() -> bool:
    """Refuse a live watcher, retake a dead lock."""
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            old = 0
        if _pid_alive_python(old):
            _log(f"REFUSED: a silent-dock tripwire already runs (pid {old}) "
                 "— one alert per day is the contract")
            return False
        _log(f"stale lock (pid {old} gone) — retaking")
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass


def _load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_state(doc: dict) -> None:
    try:
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, str(STATE_FILE))
    except Exception as exc:
        _log(f"state write failed (non-fatal): {exc}")


def push_topic() -> str | None:
    try:
        return TOPIC_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def send_ntfy(text: str, prio: str, tag: str) -> bool:
    """One ntfy attempt on the scanner's own pipe (the wire format is
    the GET OUT push shape — same title header, same priority scheme)."""
    topic = push_topic()
    if not topic:
        _log("push skipped — no push_topic.txt (the pipe has no address)")
        return False
    try:
        import urllib.parse
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{urllib.parse.quote(topic)}",
            data=str(text).encode("utf-8"),
            headers={"Title": "Kraken Scout", "Priority": prio,
                     "Tags": tag})
        urllib.request.urlopen(req, timeout=PUSH_TIMEOUT_S)
        _log(f"📲 PUSHED ({prio}): {str(text)[:90]}")
        return True
    except Exception as exc:
        _log(f"push failed — the phone may have missed: {exc}")
        return False


# ── the trigger read (the honest ambient reader; the drive pins this) ─

def ambient_penalty(dry_run: bool = True):
    """The sensor: ONE read of the penalty world. Returns the
    silent_penalty_ambient dict. Production reads the live meta
    state; the drive reads its sandbox (the dry_run rebind)."""
    import meta_layers as ml
    if dry_run:
        return ml.silent_penalty_ambient(dry_run=True)
    return ml.silent_penalty_ambient()


def tripped(reading: dict) -> bool:
    """past 2.0 is the trip line (the ask, literal)."""
    return float(reading.get("live_penalty") or 0.0) > THRESHOLD


def fire_text(reading: dict) -> str:
    src = str(reading.get("source") or "?")
    pen = float(reading.get("live_penalty") or 0.0)
    dose_kind = ("the drift verdict priced reality against the funnel "
                 "(RML — not silent, the reentry gate owned it)"
                 if "RML" in src else
                 "the silent-rotation dock — the funnel buried payers "
                 "behind a quiet rotation (RA — the exact blind)")
    return (f"🔇 Penalty {pen:.1f} past 2.0 — {dose_kind}. "
            f"The lesson is costing more than the fun rides "
            f"({pen:.1f}/3.0) — widen the gates or take the dose.")


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _fired_today(state: dict, now: datetime) -> bool:
    return str(state.get("fired_day") or "") == _day_key(now)


def _run_locked(interval: float, dry_run: bool) -> int:
    state = _load_state()
    if _fired_today(state, _now()):
        _log(f"already fired today ({state.get('fired_day')}) — the latch "
             "holds until the next day-key; exiting")
        return 0
    _log(f"ARMED: silent-dock tripwire — poll every {interval:.0f}s, "
         f"trip at live_penalty > {THRESHOLD}, latched once per day")
    send_failures = 0
    while True:
        reading = ambient_penalty(dry_run=dry_run)
        if not tripped(reading):
            state.update({"last_penalty": reading.get("live_penalty"),
                          "last_source": reading.get("source"),
                          "last_ok_ts": time.time(),
                          "cycles": int(state.get("cycles", 0)) + 1})
            _save_state(state)
            time.sleep(interval)
            continue
        # ── THE TRIP ──
        text = fire_text(reading)
        if not send_ntfy(text, "high", "mute"):
            send_failures += 1
            if send_failures >= SEND_RETRY_CAP:
                _log(f"GIVING UP after {send_failures} failed sends — the "
                     "trip never reached the pipe (the penalty was "
                     f"{reading.get('live_penalty')})")
                return 3
            _log(f"send failed ({send_failures}/{SEND_RETRY_CAP}) — "
                 "the trip stays ARMED, retrying next cycle")
            time.sleep(interval)
            continue
        # attempt 2/2 — the 200 is not a delivery receipt
        time.sleep(60.0)
        send_ntfy(text + "  (attempt 2/2 — ignore if you saw the first)",
                  "high", "mute")
        fired_at = _now().isoformat()
        state.update({"armed": False, "fired_day": _day_key(_now()),
                      "fired_at": fired_at,
                      "fired_penalty": reading.get("live_penalty"),
                      "fired_source": reading.get("source")})
        _save_state(state)
        _log(f"DONE: penalty {reading.get('live_penalty')} past {THRESHOLD} "
             "— one ping (plus attempt 2/2), latched until the next day")
        return 0


def run(interval: float, dry_run: bool = False) -> int:
    if not claim_lock():
        return 2
    try:
        return _run_locked(interval, dry_run)
    finally:
        release_lock()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="one silent-rotation dock over 2.0, one phone ping, "
                    "latched once per day (read-only on the firm)")
    ap.add_argument("--interval", type=float, default=30.0,
                    help="seconds between checks (default 30)")
    args = ap.parse_args()
    return run(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
