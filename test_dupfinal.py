"""FINAL lock verification: duplicates of scanner AND bot must refuse."""
import subprocess
import time

lines = []


def try_dup(cwd, exe, script, args, name):
    logf = open(f"_dupfinal_{name}.txt", "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(
        [exe, script] + args, stdout=logf, stderr=subprocess.STDOUT, cwd=cwd,
    )
    deadline = time.time() + 15
    exited = None
    while time.time() < deadline:
        if p.poll() is not None:
            exited = p.returncode
            break
        time.sleep(0.5)
    if exited is None:
        p.kill()
        exited = "KILLED (still running after 15s)"
    logf.close()
    log = open(f"_dupfinal_{name}.txt", encoding="utf-8", errors="replace").read()
    refused = "already running" in log.lower()
    lines.append(f"{name}: exit={exited} refused={refused}")
    lines.append(f"  first 200 chars: {log[:200]!r}")
    return refused


r1 = try_dup(
    r"C:\Users\birds\Workspace\kraken_scout",
    r".venv\Scripts\python.exe", "background_scanner.py", [], "scanner",
)
r2 = try_dup(
    r"C:\Users\birds\Workspace\kraken_xrp_trading_bot",
    r".venv\Scripts\python.exe", "bot_runner.py", ["--interval", "15"], "bot",
)

lines.append(("PASS - both duplicates refused" if (r1 and r2) else "FAIL - at least one duplicate ran"))
open("_dupfinal_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
