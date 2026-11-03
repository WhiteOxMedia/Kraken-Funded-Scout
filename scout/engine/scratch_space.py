"""scratch_space.py — THE SCRATCH ROUTER (Sep 24).

WHY THIS EXISTS: drives wrote their check reports and sandbox shapes
into the repo ROOT for months — kill the shell mid-run and the hygiene
gate finds a stray that nothing will regenerate. The scratch policy
(Sep 24) ends that class: every volatile, runtime, diagnostic, or
temporary artifact routes into the repo's scratch/ tree (or the OS temp
dir for genuinely volatile operations), and NOTHING scratch-shaped is
ever written into the repo root again.

THE SIX POLICY DIRS (scratch/, all git-ignored, all swept by
kraken_stalewatch):
    scratch/logs/          run logs that are not production logs/
    scratch/sbx/           sandbox shapes (the _sbx_* family and friends)
    scratch/dbshape/       DB shape snapshots (reserved: none written yet)
    scratch/reports_temp/  drive check reports (_*_check.txt and friends)
    scratch/miner_temp/    reserved per policy: this repo runs no miner
    scratch/bot_temp/      bot-side volatile ops (reserved: none written yet)

RULE OF ROUTING: a drive that wants a scratch path imports THIS module
and asks for it — it never builds a root-relative Path for a write.
Reads of a report the drive itself just wrote route through the same
helper, so writer and reader can never disagree about the directory.

Run:  .venv\\Scripts\\python.exe -X utf8 scratch_space.py   (self-check)
"""

import tempfile
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent / "scratch"
LOGS = SCRATCH / "logs"
SBX = SCRATCH / "sbx"
DBSHAPE = SCRATCH / "dbshape"
REPORTS_TEMP = SCRATCH / "reports_temp"
MINER_TEMP = SCRATCH / "miner_temp"   # reserved by policy; this repo runs no miner
BOT_TEMP = SCRATCH / "bot_temp"

_DIRS = (LOGS, SBX, DBSHAPE, REPORTS_TEMP, MINER_TEMP, BOT_TEMP)


def ensure() -> None:
    """Create every policy dir. Fail-soft callers can ignore errors;
    a missing dir is recreated on the next routed write."""
    for d in _DIRS:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def os_temp() -> Path:
    """Volatile operations route here: %TEMP%/kraken_scout/ (policy 2).
    Nothing under here is ever summarized into repo/reports/."""
    p = Path(tempfile.gettempdir()) / "kraken_scout"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sbx(name: str) -> Path:
    """A sandbox shape file: scratch/sbx/<name>."""
    ensure()
    return SBX / name


def report_temp(name: str) -> Path:
    """A drive check report: scratch/reports_temp/<name>."""
    ensure()
    return REPORTS_TEMP / name


def dbshape(name: str) -> Path:
    """A DB shape snapshot: scratch/dbshape/<name>."""
    ensure()
    return DBSHAPE / name


def logs_dir() -> Path:
    ensure()
    return LOGS


if __name__ == "__main__":
    ensure()
    ot = os_temp()
    print("scratch router self-check:")
    for d in _DIRS:
        print(f"  {'OK ' if d.is_dir() else 'MISSING'}  {d}")
    print(f"  OK   {ot}  (OS temp for volatile ops)")
    missing = [d for d in _DIRS if not d.is_dir()]
    raise SystemExit(1 if missing else 0)
