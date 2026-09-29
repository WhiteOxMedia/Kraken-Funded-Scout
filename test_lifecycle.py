"""test_lifecycle.py - THE LAUNCH-LIFECYCLE CONTRACT (Sep 15).

Pins the start/stop machinery in source so neither side can silently
regress to the boot-opener / double-console bugs:

  1. THE WATCHDOG'S FLEET GATE (reminders/console_watchdog.ps1) -
     the Sep 7 watchdog healed a dead console UNCONDITIONALLY, every
     minute: at unlock it could start a console before the desktop
     shortcut ever ran, and it resurrected consoles the user killed.
     Pinned: marker required, BOTH pid locks checked with a live-python
     guard, and the gate ORDERED BEFORE the respawn.
  2. THE LAUNCHER'S RE-ARM (launch_stack.ps1) - starting the stack by
     hand is the only thing that re-arms the watchdog, and only when
     every component verified up. Pinned: marker written strictly
     inside `if ($okAll)`, stop_stack.lock cleared, and the marker
     filename IDENTICAL to the one the watchdog reads (if the paths
     drift, the re-arm arms nothing and boot heals nothing).
  3. THE STOP PATH (stop_stack.ps1) - disarms the watchdog BEFORE the
     first kill pass (no resurrection mid-shutdown), takes the launcher
     mutex, and verifies silence twice before declaring victory.
  4. THE WINDOWLESS CLICK PATH (launch_kraken_hidden.vbs + the desktop
     Kraken.lnk) - the shortcut targets the VBS host (no bat console),
     and the launcher's checklist is transcribed, not printed.

Also pins the launcher's sync-safety contracts (Sep 14): no foreground
console, no Read-Host trap, no -RedirectStandardOutput (the inherited-
pipe hold), all five components through Start-Verified (the stack grew
to five when hindsight_service joined, Sep 29).

Style: test_floodguard section 10 - source-pins, cheap, hermetic.
"""

import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
WD = BASE / "reminders" / "console_watchdog.ps1"
LS = BASE / "launch_stack.ps1"
SS = BASE / "stop_stack.ps1"
VBS = BASE / "launch_kraken_hidden.vbs"
HD = BASE / "start_detached.ps1"
# read-only suite constant (never reassigned, never restored) — the
# marker's NAME, not a swap of any module state. leak-audit: ok
MARKER_NAME = ".console_watchdog.on"

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def ordered(src, first: str, second: str) -> bool:
    """True when `first` appears in src BEFORE `second` (ordering pin)."""
    a, b = src.find(first), src.find(second)
    return 0 <= a < b


wd = WD.read_text(encoding="utf-8", errors="replace") if WD.exists() else ""
ls = LS.read_text(encoding="utf-8", errors="replace") if LS.exists() else ""
ss = SS.read_text(encoding="utf-8", errors="replace") if SS.exists() else ""
vbs = VBS.read_text(encoding="utf-8", errors="replace") if VBS.exists() else ""
hd = HD.read_text(encoding="utf-8", errors="replace") if HD.exists() else ""

def code_only(src: str) -> str:
    """Source with comment lines stripped (# for ps1, ' for vbs).
    Pins judge CODE: the scripts document the bugs they retired
    ("the old Read-Host trap", "the streamlit.exe trampoline"), and
    a comment mentioning history must never fail a no-regression pin.
    """
    return "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith(("#", "'")))


wdc, lsc, ssc = code_only(wd), code_only(ls), code_only(ss)
hdc = code_only(hd)

# ── 1. the watchdog's fleet gate ────────────────────────────────────────
check("watchdog exists", bool(wd), WD.name)
check("watchdog requires the marker (delete = disable)",
      f"if (-not (Test-Path $marker)) {{ exit 0 }}" in wd)
check("fleet gate checks the SCANNER pid lock",
      'Test-LockAlive "$scout\\scanner.lock"' in wd)
check("fleet gate checks the BOT pid lock",
      'Test-LockAlive "$botDir\\bot.lock"' in wd)
check("a recycled pid on a non-python exe is refused",
      "-match 'python'" in wd)
check("gate is ORDERED BEFORE the respawn",
      ordered(wdc, 'Test-LockAlive "$scout\\scanner.lock"',
              "start_detached.ps1"),
      "the heal must be unreachable while the fleet is down")
check("console detection is contract-shaped (streamlit run app.py)",
      "streamlit run app\\.py" in wd)
check("console detection checks the :8501 listener",
      ":8501" in wd)
check("zombie sweep kills BOTH halves (parent-child)",
      "ParentProcessId" in wd)
check("watchdog heals in the stack's OWN mode (visible stays visible)",
      "visible_stack.off" in wdc and "'-ShowWin'" in wdc,
      "a hidden heal of a visible stack looks like a vanished component")
check("respawn goes through the hardened helper, never a direct spawn",
      "start_detached.ps1" in wdc and "streamlit.exe" not in wdc
      and "python -m streamlit" not in wdc)

# ── 2. the launcher's re-arm ────────────────────────────────────────────
check("launcher exists", bool(ls), LS.name)
check("re-arm writes the marker ONLY on a fully-verified stack",
      re.search(r"if \(\$okAll\) \{[^}]*" + re.escape(MARKER_NAME), ls, re.S)
      is not None)
check("re-arm clears the stop lock (a start un-stops)",
      re.search(r"if \(\$okAll\) \{[^}]*stop_stack\.lock", ls, re.S)
      is not None)
check("re-arm and watchdog read the SAME marker path",
      MARKER_NAME in wd and MARKER_NAME in ls,
      "a path drift silently disarms the whole interlock")
check("no Read-Host trap (the old launcher parked on it)",
      "Read-Host" not in lsc)
check("no foreground console (the old 'this window is the dashboard')",
      'streamlit.exe" run app.py' not in ls and "& '.\\app.py'" not in ls)
check("no -RedirectStandardOutput (the inherited-pipe hold)",
      "-RedirectStandardOutput" not in ls)
check("all five components spawn through Start-Verified (the stack "
      "grew to five when hindsight_service joined, Sep 29)",
      all(f'Start-Verified "{c}"' in ls
          for c in ("volatility_engine", "background_scanner",
                    "bot_runner", "streamlit", "hindsight_service")))
check("VISIBLE is the default mode (visible_stack.off = hidden)",
      "visible_stack.off" in lsc and "$showWin = -not" in lsc,
      "her preference: windows she can see, not a background fleet")
check("-Hidden is a one-run override and never flips the preference",
      "if ($Hidden)" in lsc
      and "visible_stack.on" not in lsc
      and "Set-Content" not in lsc.split("if ($Hidden)")[0],
      "an override that writes the marker would steal her choice")
check("all five components receive the -ShowWin switch",
      lsc.count("-ShowWin:$showWin") == 5,
      f"got {lsc.count('-ShowWin:$showWin')}")
check("visible mode verifies the CONSOLE shape, not the tee wrapper",
      "streamlit run app.py" in lsc)
check("streamlit runs HEADLESS (never opens its own browser tab)",
      "--server.headless true" in hdc,
      "the Sep 15 double-tab bug: streamlit auto-opened AND the launcher opened")
check("the launcher is the single opener on BOTH console paths",
      lsc.count('Start-Process "http://localhost:8501"') == 2,
      "fresh start AND already-running must each open exactly one tab")

# ── 3. the stop path ────────────────────────────────────────────────────
check("stop script exists", bool(ss), SS.name)
check("STOP disarms the watchdog BEFORE the first kill pass",
      ordered(ss, "Remove-Item $marker", "Stop-Process"),
      "a resurrection mid-shutdown turns stop into restart")
check("STOP takes the launcher mutex (no start/stop race)",
      "Global\\KrakenLaunchStack" in ss)
check("STOP drops the stop lock",
      "Set-Content -Path $stopLock" in ss)
check("STOP verifies silence twice before declaring down",
      "$left1" in ss and "$left2" in ss)

# ── 4. the windowless click path ────────────────────────────────────────
check("VBS host exists", bool(vbs), VBS.name)
check("the click path runs the launcher hidden and transcribes it",
      "launch_stack.ps1" in vbs and "logs\\launch_stack.log" in vbs,
      "the checklist is the click's paper trail once no window exists")
check("the venv self-check survived the windowless move",
      "uv sync" in vbs)

# ── 5. the visible stack (the helper's -ShowWin mode) ──────────────────
check("helper exists", bool(hd), HD.name)
check("helper accepts -ShowWin",
      "[switch]$ShowWin" in hdc)
check("visible windows are TITLED per component",
      "$host.UI.RawUI.WindowTitle" in hdc and "$title" in hdc)
check("the wrapper CLAIMS its own visibility (hidden chains hide children)",
      "GetConsoleWindow" in hdc and "ShowWindow" in hdc,
      "-WindowStyle Normal is overridden by the parent's show-state")
check("live output is TEE'D: window AND logs\\<stem>_console.log",
      "_console.log" in hdc and "Add-Content -Path $ConLog" in hdc)
check("the tee wrapper propagates the child's exit code",
      "exit $LASTEXITCODE" in hdc,
      "without it the early-death capture reads a phantom success")
check("early-death evidence lands in the transcript she reads",
      '${LogStem}_console.log' in hdc)
check("hidden mode keeps separate stdout/stderr redirects",
      "-RedirectStandardOutput $outLog -RedirectStandardError $errLog" in hdc)
check("console shape lives in the helper too (alive-probe parity)",
      '"streamlit run app.py"' in hdc)


def shortcut_targets_vbs() -> bool:
    cands = []
    od = os.environ.get("OneDrive", "")
    if od:
        cands.append(Path(od) / "Desktop" / "Kraken.lnk")
    cands += [Path.home() / "OneDrive" / "Desktop" / "Kraken.lnk",
              Path.home() / "Desktop" / "Kraken.lnk"]
    for c in cands:
        if c.is_file():
            return "launch_kraken_hidden.vbs" in c.read_bytes().decode(
                "latin-1").lower()
    return False


check("the desktop Kraken shortcut targets the windowless VBS",
      shortcut_targets_vbs(),
      "retargeted to a .bat = visible consoles again on every click")

print(f"\nlifecycle contract: {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
