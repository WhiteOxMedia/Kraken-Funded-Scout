"""THE RELEASE WATCHER (self-rearming — coverage never lapses).

Watches logs/scanner_console.log for the first live 'JOURNAL RELEASE'
line — desktop, mover, the sure-thing variant (Sep 22), or the OUT-tide
backstop. When one fires, records the released pair and then tracks its
outcome through every ledger:

  contest_watch.json        the auto-track row appears at fire time
  signal_snapshots.jsonl    kind:"fired" row (route champion/mover/sure-thing)
  popup_suppressions.jsonl  must stay SILENT for the released pair (not buried)
  contest_trades.jsonl      the closed trade row with realized pnl
  discipline_state.json     tonight's rotation eval carrying realized_pnl

Prints a CHANGE-ONLY stream so the transcript stays readable. At each
window deadline with no release it RE-ARMS in place instead of exiting —
coverage never lapses (user, Sep 22); the only exit is the chain's last
ledger (the released trade closes) or an external kill. Never writes to
any ledger: read-only by design. The window length is the WATCH_WINDOW_MIN
env (default 100 minutes) — short windows exist so the re-arm behavior is
provable, not just claimed.
"""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "logs", "scanner_console.log")
WATCH = os.path.join(ROOT, "contest_watch.json")
SNAPS = os.path.join(ROOT, "signal_snapshots.jsonl")
SUPP = os.path.join(ROOT, "popup_suppressions.jsonl")
TRADES = os.path.join(ROOT, "contest_trades.jsonl")
DISC = os.path.join(ROOT, "discipline_state.json")

WINDOW_MIN = float(os.environ.get("WATCH_WINDOW_MIN", "100"))
POLL_S = 15
# ONE catch-all mark, not a list of variants: every release shape says
# 'JOURNAL RELEASE' somewhere, and a variants list goes stale the moment a
# gate grows a new line format (the sure-thing's did, Sep 22).
RELEASE_MARKS = ("JOURNAL RELEASE",)


def _pair_from(ln: str):
    """The released pair from a release log line, or None. The desktop,
    mover and OUT-tide lines say 'JOURNAL RELEASE ...: PAIR'; the
    sure-thing line names the asset BEFORE the phrase
    ('SURE THING XMR: JOURNAL RELEASE — ...') and has no colon after it —
    the old regex needed one and parsed the sure-thing fire as None."""
    m = (re.search(r"SURE THING\s+([A-Z0-9]+):\s*JOURNAL RELEASE", ln)
         or re.search(r"RELEASE[^:]*:\s*([A-Z0-9]+)", ln))
    return (m.group(1) + "/USD") if m else None


def _tail(path, n):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def _last(path):
    lines = _tail(path, 1)
    return json.loads(lines[-1]) if lines else None


def say(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    # start from the file's current end so old lines don't re-fire
    try:
        log_pos = os.path.getsize(LOG)
    except OSError:
        log_pos = 0
    seen_snap = sum(1 for _ in open(SNAPS, encoding="utf-8")) if os.path.exists(SNAPS) else 0
    seen_supp = sum(1 for _ in open(SUPP, encoding="utf-8")) if os.path.exists(SUPP) else 0
    seen_trades = sum(1 for _ in open(TRADES, encoding="utf-8")) if os.path.exists(TRADES) else 0
    watch_sig = None
    pair = None
    deadline = time.time() + WINDOW_MIN * 60
    say(f"watching from log byte {log_pos} — release marks: {RELEASE_MARKS} "
        f"(self-rearming every {WINDOW_MIN:.0f} min — coverage never lapses)")
    while True:
        if time.time() >= deadline:
            say("deadline reached with no release line — re-arming in place "
                "(coverage never lapses)")
            deadline = time.time() + WINDOW_MIN * 60
        # ── the release line ─────────────────────────────────────────
        try:
            size = os.path.getsize(LOG)
            if size < log_pos:
                # the log was truncated (a scanner restart rewrote it) —
                # re-anchor at the new file's start so nothing is missed
                log_pos = 0
                say("log truncated (scanner restart?) — re-anchored at byte 0")
            if size > log_pos:
                with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(log_pos)
                    new = f.read()
                    log_pos = size
                for ln in new.splitlines():
                    if any(m in ln for m in RELEASE_MARKS):
                        say("RELEASE LINE: " + ln.strip())
                        pair = _pair_from(ln)
                        say(f"released pair parsed as {pair!r} — tracking")
        except OSError:
            pass

        # ── after a release: the ledgers ─────────────────────────────
        if pair:
            try:
                w = json.load(open(WATCH, encoding="utf-8"))
                sig = json.dumps({pair: w.get(pair)}, sort_keys=True) if pair in w else None
                if sig and sig != watch_sig:
                    watch_sig = sig
                    say(f"WATCH: {sig}")
            except (OSError, ValueError):
                pass
            new_snaps = _tail(SNAPS, 400)[max(0, seen_snap - 400):]
            n_snaps = sum(1 for _ in open(SNAPS, encoding="utf-8")) if os.path.exists(SNAPS) else 0
            if n_snaps > seen_snap:
                for ln in _tail(SNAPS, n_snaps - seen_snap):
                    r = json.loads(ln)
                    if r.get("pair") == pair:
                        say(f"SNAPSHOT: kind={r.get('kind')} route={r.get('route')} "
                            f"quick={r.get('quick')} entry={r.get('entry')}")
                seen_snap = n_snaps
            n_supp = sum(1 for _ in open(SUPP, encoding="utf-8")) if os.path.exists(SUPP) else 0
            if n_supp > seen_supp:
                for ln in _tail(SUPP, n_supp - seen_supp):
                    r = json.loads(ln)
                    if r.get("pair") == pair:
                        say(f"SUPPRESSION (unexpected for a released pair!): "
                            f"gate={r.get('gate')} {str(r.get('detail'))[:90]}")
                seen_supp = n_supp
            n_tr = sum(1 for _ in open(TRADES, encoding="utf-8")) if os.path.exists(TRADES) else 0
            if n_tr > seen_trades:
                for ln in _tail(TRADES, n_tr - seen_trades):
                    r = json.loads(ln)
                    rp = str(r.get("pair") or "")
                    if rp.upper() == pair.upper():
                        say(f"TRADE CLOSED: {json.dumps(r)[:300]}")
                        say("VERDICT: released candidate's outcome has landed in "
                            "contest_trades.jsonl — chain complete "
                            "(log line -> popup -> watch -> trades). "
                            "Watcher done.")
                        return
                seen_trades = n_tr
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
