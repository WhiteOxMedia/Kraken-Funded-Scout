"""THE PRICE ALERT WATCHER (ops tool, Sep 29): one pair, one level, one
ping on the phone the moment the level is touched — the operator's
"tell me when the recovery leg starts" tripwire.

    # the launch (detached, its own logs — see the boot log for the pid):
    uv run python -X utf8 ops/price_alert.py --pair FIL/USD --above 1.085

    # optional forms:
    uv run python -X utf8 ops/price_alert.py --pair SOL/USD --below 150.0
    uv run python -X utf8 ops/price_alert.py --pair FIL/USD --above 1.085 --interval 30

HOW IT WATCHES: every --interval seconds (default 30) it fetches the
pair's last price from Kraken's PUBLIC ticker (no keys, no trading
endpoint) and compares it to the line. The moment the condition holds —
price >= --above (or <= --below) — it pushes ONE high-priority alert
through the SAME private ntfy pipe the scanner's GET OUTs use
(push_topic.txt), naming the pair, the line, the crossed price and the
desk command that prices the next move. Then, exactly like a GET OUT,
it fires ONE automatic second attempt ~60s later (ntfy's 200 says
nothing about the phone — the 16:05 lesson: an accepted push is still
"in doubt" until attempt 2 is also on the wire; a duplicate ping is the
cheap failure, a missed entry is not) — and then the watcher is DONE:
it disarms, writes its fired record, and exits. One alert, one job.

HONESTY RAILS:
  - a failed fetch NEVER fires — a stale price is a lie with decimals;
    the cycle just logs and waits (failures are visible in the log)
  - the feed failing ~15 min straight sends ONE low-priority "feed is
    down, still trying" note (once per run, not a nag), and keeps trying
  - a failed ntfy send keeps the alert ARMED and retries every cycle
    (up to 30) — a dead pipe must not silently swallow the trip
  - the lock file refuses a second watcher for the same pair (a double
    is two phones buzzing at the same moment); a DEAD lock is retaken
    (the recycled-pid trap: the pid must name a live python process)

READ-ONLY ON THE FIRM: it touches no state the scanner, bot or console
owns — no watch file, no account state, no bridge DB. Its own runtime
state (price_alert.lock, price_alert_state.json) is gitignored and
beside the point of the fleet.
"""
from __future__ import annotations

import argparse
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
LOCK_FILE = ROOT / "price_alert.lock"
STATE_FILE = ROOT / "price_alert_state.json"
TOPIC_FILE = ROOT / "push_topic.txt"

FEED_FAIL_NOTE_AFTER_S = 900.0   # ~15 min of dead feed -> one degradation note
SEND_RETRY_CAP = 30              # failed ntfy sends before giving up (logged)
FETCH_TIMEOUT_S = 10
PUSH_TIMEOUT_S = 10


def _now() -> datetime:
    return datetime.now(MST)


def _log(msg: str) -> None:
    print(f"{_now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


# ── the lock: one watcher per pair, dead locks are retaken ────────────

def _pid_alive_python(pid: int) -> bool:
    """True only when the pid names a LIVE PYTHON process — the recycled-
    pid trap (tasklist sees ANY process on that pid) closed the cheap way."""
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
    """Refuse a live watcher (the double-buzz), retake a dead lock."""
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            old = 0
        if _pid_alive_python(old):
            _log(f"REFUSED: a watcher already runs (pid {old}) — one "
                 "alert per pair, a double is two phones buzzing")
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


# ── the price feed: public ticker, fail-honest ────────────────────────

def fetch_price(pair: str) -> float | None:
    try:
        import ccxt
        ex = ccxt.kraken({"enableRateLimit": True})
        return float(ex.fetch_ticker(pair)["last"])
    except Exception as exc:
        _log(f"fetch failed {pair}: {exc}")
        return None


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


# ── the pipe: the same private ntfy topic the scanner's GET OUTs use ──

def push_topic() -> str | None:
    try:
        return TOPIC_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def send_ntfy(text: str, prio: str, tag: str) -> bool:
    """One ntfy attempt. True only on an HTTP accept — which is delivery
    to the SERVER, never proof the phone showed it (the wire format the
    scanner's _send_ntfy uses: same title header, same priority scheme)."""
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


# ── the trigger arithmetic (pure; the drive pins this) ────────────────

def tripped(price: float, above: float | None, below: float | None) -> bool:
    """The condition: at/above the line (recovery), or at/below it
    (breakdown) when a below line is armed. The line itself counts —
    'at 1.085' means 1.085 pings."""
    if above is not None and price >= above:
        return True
    if below is not None and price <= below:
        return True
    return False


def fire_text(pair: str, price: float, above: float | None,
              below: float | None) -> str:
    line = above if above is not None else below
    sense = "above" if above is not None else "below"
    return (f"📈 {pair} {price} — {sense} your {line} alert line. "
            f"The recovery leg is on — price it first: "
            f"uv run python -X utf8 ops/recovery_scenarios.py "
            f"--pair {pair}")


# ── the loop ──────────────────────────────────────────────────────────

def run(pair: str, above: float | None, below: float | None,
        interval: float) -> int:
    if above is None and below is None:
        _log("no line armed — pass --above and/or --below")
        return 2
    if not claim_lock():
        return 2
    try:
        return _run_locked(pair, above, below, interval)
    finally:
        release_lock()


def _run_locked(pair: str, above: float | None, below: float | None,
                interval: float) -> int:
    state = _load_state()
    if state.get("armed") is False and state.get("pair") == pair:
        _log(f"the {pair} alert already fired at "
             f"{state.get('fired_at', '?')} (price {state.get('fired_price')}) "
             "— delete price_alert_state.json to re-arm; exiting")
        return 0
    line = above if above is not None else below
    _log(f"ARMED: {pair} {'>=' if above is not None else '<='} {line} — "
         f"checking every {interval:.0f}s; one ping then done")
    send_failures = 0
    feed_failures = 0
    feed_note_sent = False
    while True:
        price = fetch_price(pair)
        if price is None:
            feed_failures += 1
            if (not feed_note_sent
                    and feed_failures * interval >= FEED_FAIL_NOTE_AFTER_S):
                feed_note_sent = True
                send_ntfy(f"⚠ {pair} alert watcher: the price feed has "
                          f"been down ~{feed_failures * interval / 60:.0f} "
                          "min — still armed, still trying",
                          "low", "warning")
            time.sleep(interval)
            continue
        if feed_failures:
            _log(f"feed recovered after {feed_failures} failed cycles")
        feed_failures = 0
        state.update({"pair": pair, "last_price": price,
                      "last_ok_ts": time.time(),
                      "cycles": int(state.get("cycles", 0)) + 1})
        if not tripped(price, above, below):
            _save_state(state)   # the operator can see the watcher breathe
            time.sleep(interval)
            continue
        # ── THE TRIP ──
        text = fire_text(pair, price, above, below)
        if not send_ntfy(text, "high", "chart_with_upwards_trend"):
            send_failures += 1
            if send_failures >= SEND_RETRY_CAP:
                _log(f"GIVING UP after {send_failures} failed sends — "
                     f"the alert never reached the pipe (the level was "
                     f"{line}, the price was {price})")
                return 3
            _log(f"send failed ({send_failures}/{SEND_RETRY_CAP}) — the "
                 "alert stays ARMED, retrying next cycle")
            time.sleep(interval)
            continue
        # accepted send — the ONE second attempt, ~60s later (the GET
        # OUT lesson: a 200 is not a delivery receipt; the duplicate is
        # the cheap failure)
        time.sleep(60.0)
        send_ntfy(text + "  (attempt 2/2 — ignore if you saw the first)",
                  "high", "chart_with_upwards_trend")
        fired_at = _now().isoformat()
        state.update({"armed": False, "fired_at": fired_at,
                      "fired_price": price, "fired_line": line})
        _save_state(state)
        _log(f"DONE: {pair} tripped {line} at {price} — one ping on the "
             "phone (plus its attempt 2/2), the watcher is discharged")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="one price line, one phone ping, then done — the "
                    "recovery tripwire (read-only on the firm)")
    ap.add_argument("--pair", required=True, help="e.g. FIL/USD")
    ap.add_argument("--above", type=float, default=None,
                    help="fire when price >= this (the recovery line)")
    ap.add_argument("--below", type=float, default=None,
                    help="fire when price <= this (the breakdown line)")
    ap.add_argument("--interval", type=float, default=30.0,
                    help="seconds between checks (default 30)")
    args = ap.parse_args()
    return run(args.pair, args.above, args.below, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
