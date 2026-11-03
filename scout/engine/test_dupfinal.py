"""FINAL lock verification: duplicates of scanner AND bot must refuse.

(Sep 5 rewrite — the old version had two flaws: (1) it launched a
duplicate bot that OVERWROTE the live bot.lock with its own pid, then
got killed — every battery run corrupted the live lock and broke the
real bot's duplicate protection; (2) it demanded a refusal even when no
bot was running. The rewrite: locate the LIVE bot pid first, REPAIR the
lock to it when stale, snapshot/restore the scanner lock, skip cleanly
when a component isn't running, and always leave the locks pointing at
live processes.)"""
import subprocess
import time
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

lines = []


def find_live_pid(needle):
    """pid of a python process whose command line contains needle."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             "ForEach-Object { \"{0}`t{1}\" -f $_.ProcessId, $_.CommandLine }"],
            capture_output=True, text=True, timeout=30)
        for row in out.stdout.splitlines():
            row = row.strip()
            if not row:
                continue
            pid_s, _, cmd = row.partition("\t")
            if needle in cmd:
                return int(pid_s)
    except Exception as exc:
        lines.append(f"  find_live_pid({needle}) failed: {exc}")
    return None


def run_dup(cwd, exe, script, args, name, deadline=20):
    logf = open(f"_dupfinal_{name}.txt", "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([exe, script] + args, stdout=logf,
                         stderr=subprocess.STDOUT, cwd=cwd)
    exited = None
    end = time.time() + deadline
    while time.time() < end:
        if p.poll() is not None:
            exited = p.returncode
            break
        time.sleep(0.5)
    if exited is None:
        p.kill()
        exited = "KILLED (still running after deadline)"
    logf.close()
    log = open(f"_dupfinal_{name}.txt", encoding="utf-8", errors="replace").read()
    refused = "already running" in log.lower()
    lines.append(f"{name}: exit={exited} refused={refused}")
    lines.append(f"  first 200 chars: {log[:200]!r}")
    return refused


# ── the scanner side (the dup refuses before ever writing the lock) ──
# clock-audit: ok — SANDBOXED (snapshot read for byte-for-byte restore in the finally) (clockbombs census 4)
scanner_lock = Path(r"C:\Users\birds\Workspace\kraken_scout\scanner.lock")
scanner_snap = scanner_lock.read_text() if scanner_lock.exists() else None
r1 = run_dup(
    r"C:\Users\birds\Workspace\kraken_scout",
    r".venv\Scripts\python.exe", "background_scanner.py", [], "scanner")
if scanner_snap is not None and scanner_lock.exists():
    scanner_lock.write_text(scanner_snap)   # never corrupt the live lock

# ── the bot side (SELF-HEALING: the lock must name a LIVE python) ────
bot_dir = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
bot_lock = bot_dir / "bot.lock"
bot_pid = find_live_pid("bot_runner.py")
r2 = False
if bot_pid is None:
    lines.append("bot: SKIP — no live bot_runner found (the duplicate "
                 "refusal can't be tested without a real instance)")
    r2 = True  # not a failure: nothing to protect right now
else:
    if bot_lock.exists() and bot_lock.read_text().strip() != str(bot_pid):
        lines.append(f"bot: stale lock repaired "
                     f"{bot_lock.read_text().strip()} -> {bot_pid}")
    bot_lock.write_text(str(bot_pid))
    r2 = run_dup(str(bot_dir), r".venv\Scripts\python.exe",
                 "bot_runner.py", ["--interval", "15"], "bot")
    now_lock = bot_lock.read_text().strip() if bot_lock.exists() else ""
    if now_lock == str(bot_pid):
        lines.append("bot: lock intact after the duplicate test")
    else:
        lines.append(f"bot: WARNING lock now {now_lock!r} (expected {bot_pid}) "
                     f"— repairing")
        bot_lock.write_text(str(bot_pid))

lines.append("PASS - duplicates refused (or cleanly skipped: not running)"
             if (r1 and r2) else "FAIL - at least one duplicate ran")
open(scratch_space.report_temp('_dupfinal_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
