"""RUNTIME TRIPWIRE (RUNTIME_INVARIANTS.md) — passive monitor, READ-ONLY.

Usage:
    python runtime_tripwire.py --init     # (re)base the checksum snapshot
    python runtime_tripwire.py            # compare, alert, refresh snapshot

Alerts on:
  1. runtime-state files whose contents changed since the last run
  2. any commit that touched a runtime-state path since the last run
  3. any stash appearing in the stash ref (a stash touching ANYTHING is a
     policy violation while the scanner runs)

The snapshot lives OUTSIDE the repo (..\\runtime_tripwire_state.json) so no
hook dance or commit can sweep it. Exit 0 = quiet, exit 2 = anomalies.
This script never writes, moves, or stashes anything — it only reads, hashes,
and compares.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.path.dirname(BASE), "runtime_tripwire_state.json")

# mirrors RUNTIME_INVARIANTS.md "What counts as runtime state"
RUNTIME_FILES = [
    "contest_watch.json", "contest_account.json", "contest_trades.jsonl",
    "take_payouts.json", "signal_snapshots.jsonl", "snapshot_outcomes.jsonl",
    "signals.db", "ohlcv_cache.db", "rotation_thresholds.json",
    "rotation_lockout.json", "climb_levels.json", "volatility_universe.json",
    "sure_thing_autoweights.json", "coin_floors.json", "discipline_state.json",
    "scanner_state.json", "scanner.lock", "voice_log.jsonl", "scanner.log",
    "console_state.json", "console_prefs.json", "popup_suppressions.jsonl",
]


def sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None  # absent right now — legitimate for volatile files


def snapshot():
    snap = {}
    for name in RUNTIME_FILES:
        p = os.path.join(BASE, name)
        if os.path.exists(p):
            snap[name] = sha256(p)
    return snap


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def main():
    init = "--init" in sys.argv
    alerts = []
    old = {}
    if os.path.exists(STATE):
        try:
            with open(STATE, "r", encoding="utf-8") as f:
                old = json.load(f).get("files", {})
        except Exception:
            old = {}

    new = snapshot()

    if not init:
        # 1. content drift in runtime state
        for name, digest in sorted(new.items()):
            if name in old and old[name] != digest:
                alerts.append(f"runtime file CHANGED: {name}")
        # 2. commits touching runtime paths since the last run
        head_now = git("rev-parse", "HEAD").strip()
        head_prev = old.get("_head") if isinstance(old.get("_head"), str) else None
        if head_prev and head_prev != head_now:
            touched = git("diff", "--name-only", f"{head_prev}..{head_now}")
            for line in touched.splitlines():
                if os.path.basename(line.strip()) in RUNTIME_FILES:
                    alerts.append(f"COMMIT {head_now[:8]} touched runtime path: {line.strip()}")
        new["_head"] = head_now
        # 3. any stash existence is a violation while the scanner runs
        if os.path.exists(os.path.join(BASE, ".git", "refs", "stash")):
            alerts.append("a STASH exists (refs/stash) — stash use is banned "
                          "while the scanner runs")
    else:
        new["_head"] = git("rev-parse", "HEAD").strip()

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if init:
        print(f"[tripwire] snapshot based at {stamp}: {len(new) - 1} runtime files")
        return 0
    if alerts:
        print(f"[tripwire] {stamp} — {len(alerts)} ALERT(S):")
        for a in alerts:
            print("  ⚠ " + a)
        return 2
    print(f"[tripwire] {stamp} — quiet: {len(new) - 1} runtime files, "
          "no stash, no runtime-path commits since last run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
