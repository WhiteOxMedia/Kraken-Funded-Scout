r"""Test THE ONE-FLOW BUNDLE (Sep 25, user order: "Build the NSIS or
PowerShell bundle that installs Kraken Scout + optional Miner Bot in
one flow, ending on the checkbox UI").

THE FLOW UNDER TEST: build_bundle.ps1 stages the COMMITTED trees of
both repos plus the xmrig payload; install.ps1 preflights uv, robocopys
the payload into %LOCALAPPDATA%\KrakenScout, builds the venv, and ends
on installer_ui.py - THE CHECKBOX (the Miner Bot choice, default ON,
the installer's byte-exact phrase on the OFF path). The opt-out
guarantee is the audited contract from 136c9b7 and it now binds every
new surface: no registry, no scheduled tasks, no Startup, no services,
no duplicate of the mandated phrase anywhere (references, not quotes).

LIVE: the drive builds a real bundle from the real repos, stages it
into a temp target with -SkipVenv (robocopy + xmrig branch, no
machine-wide state), and proves the staged monero tree imports its own
installer module and the pool view resolves under the name contract.

SANDBOX: everything lands in temp dirs; the live staging uses
-SkipVenv so no winget/uv call can fire; nothing here touches the
production install_config.json or jackpot.db.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE / "installer"
PS = "powershell"

results = []


def check(n, ok, why=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + f"{n}  {why}")


def _new_venv(root: Path) -> Path:
    venv.create(str(root / ".venv"), with_pip=False)
    return root / ".venv" / "Scripts" / "python.exe"


def _ps(script: Path, *args: str, timeout=240):
    return subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, text=True, timeout=timeout)


print("== 1. THE SOURCES: the opt-out guarantee binds every new surface ==")
_surfaces = {n: (INSTALLER / n).read_text(encoding="utf-8")
             for n in ("install.ps1", "build_bundle.ps1",
                       "Install Kraken Scout.bat", "Uninstall Kraken Scout.bat")}
_banned = ("New-ItemProperty", "Set-ItemProperty", "HKCU:", "HKLM:",
           "schtasks", "reg add", "REG ADD", "shell:Startup",
           "New-Service", "sc create", "Register-ScheduledTask")
_bad = {n: [b for b in _banned if b in s] for n, s in _surfaces.items()}
check(1.1, not any(_bad.values()), f"no persistence calls in any new surface: {_bad}")
check(1.2, not any("Not sure where to get started? Start here:" in s
                   for s in _surfaces.values()),
      "no surface QUOTES the mandated phrase - references only (one source: the installer constant)")

print("== 2. THE FLOW: the checkbox is the last step, by shape ==")
_ins = _surfaces["install.ps1"]
_steps = [s for s in ("[1/4]", "[2/4]", "[3/4]", "[4/4]", "installer_ui.py") if s in _ins]
check(2.1, len(_steps) == 5, f"the flow's steps stand in order: {_steps}")
check(2.2, _ins.rfind("& $PyExe $UiPy") > _ins.index("[4/4]"),
      "the checkbox is INVOKED after the venv step - the flow ENDS there")
check(2.3, "opt_in" not in _ins and "--opt-in" not in _ins.replace("--opt-in --wallet <address>", ""),
      "install.ps1 itself never registers - only installer_ui.py (the human's click) does")
check(2.4, "var_optin = tk.BooleanVar(value=True)" in
      (INSTALLER / "installer_ui.py").read_text(encoding="utf-8"),
      "the flow ends on the real checkbox UI, whose default is CHECKED (the protocol)")
check(2.5, "-SkipVenv" in _ins and "-Target" in _ins and "-NoUi" in _ins,
      "the drive's seams exist: -Target / -NoUi / -SkipVenv")
check(2.6, "monero_project" in _ins and "THE NAME CONTRACT" in _ins,
      "the staged tree keeps its name - app.py's sibling import depends on it")

print("== 3. THE BUILDER: committed trees ship, nothing else ==")
_bb = _surfaces["build_bundle.ps1"]
check(3.1, "archive --format=tar -o $TarF HEAD" in _bb
      and "System32\\tar.exe" in _bb and "rev-parse" in _bb,
      "both payloads ship from git archive HEAD via the pinned Windows tar "
      "(MSYS tar reads C:\\... as a remote host; PS pipes corrupt streams) - "
      "worktree drift cannot ride along")
check(3.2, ".venv" in _bb and "Remove-Item" in _bb.split(".venv")[1][:120],
      "the scout venv is stripped from the archive before shipping")
check(3.3, "payload\\monero_project" in _bb,
      "the monero payload lands under its contract name")
check(3.4, "xmrig-6.26.0" in _bb and "xmrig.exe" in _bb,
      "the xmrig payload rides the bundle when present locally")
check(3.5, "install.ps1" in _bb and "README.txt" in _bb
      and "Install Kraken Scout.bat" in _bb and "Uninstall Kraken Scout.bat" in _bb,
      "the bundle root carries the one-click wrappers, install.ps1, and the README")

print("== 4. LIVE: build a real bundle from the real repos ==")
_tmp = Path(tempfile.mkdtemp(prefix="jp_bundle_live_"))
_r = _ps(INSTALLER / "build_bundle.ps1", timeout=600)
check(4.1, _r.returncode == 0, f"builder rc={_r.returncode} {_r.stderr[-300:] if _r.returncode else ''}")
_bundle = HERE / "dist" / "KrakenScoutBundle"
_scout = _bundle / "payload" / "scout"
_monero = _bundle / "payload" / "monero_project"
check(4.2, (_scout / "app.py").exists() and (_scout / "pyproject.toml").exists()
      and (_scout / "start_detached.ps1").exists()
      and (_scout / "Launch Scout.bat").exists(),
      "the scout tree is complete (app, pyproject/uv.lock, the fleet's own launchers)")
check(4.3, not (_scout / ".venv").exists()
      and (_monero / "installer" / "installer.py").exists()
      and (_monero / "installer" / "installer_ui.py").exists()
      and not (_monero / "pool").exists(),
      "no venv and no pool in the payload; the installer half is what ships")
check(4.4, (_bundle / "install.ps1").exists()
      and (_bundle / "Install Kraken Scout.bat").exists()
      and (_bundle / "Uninstall Kraken Scout.bat").exists()
      and (_bundle / "README.txt").exists(),
      "the bundle root carries the entry points and the README")
_xmrig = _bundle / "payload" / "xmrig-6.26.0"
check(4.5, (_xmrig / "xmrig.exe").exists(),
      "the xmrig payload is in the bundle (local copy rode along)")
_readme = (_bundle / "README.txt").read_text(encoding="utf-8")
check(4.6, "Install Kraken Scout.bat" in _readme
      and "xmrjackpot.com" in _readme
      and "Not sure where to get started? Start here:" not in _readme,
      "the README names the entries and REFERENCES the start-here note")

print("== 5. LIVE: the real install.ps1 -SkipVenv stages a temp target ==")
_target = _tmp / "target"
_r2 = _ps(_bundle / "install.ps1", "-Target", str(_target), "-SkipVenv", "-NoUi", timeout=300)
check(5.1, _r2.returncode == 0, f"installer rc={_r2.returncode} {_r2.stderr[-300:] if _r2.returncode else ''}")
check(5.2, (_target / "scout" / "app.py").exists()
      and (_target / "monero_project" / "installer" / "installer_ui.py").exists()
      and (_target / "xmrig" / "xmrig-6.26.0" / "xmrig.exe").exists(),
      "scout + monero_project + xmrig all staged under the target")
_rc_txt = _r2.stdout or ""
check(5.3, "STAGED" in _rc_txt and "installer_ui.py" in _rc_txt,
      "with -NoUi the flow ends by naming the checkbox UI as the remaining step")

# the staged monero tree must WORK: its own venv-less import proves the
# installer resolves and its miner values are CUSTOMER-neutral (the pool
# shipped here until Oct 25; it is operator-only now).
_vpy = _new_venv(_target / "scout")
_r3 = subprocess.run([str(_vpy), "-X", "utf8", "-c",
                      "import sys; sys.path.insert(0, r'" + str(_target / "monero_project" / "installer") + "'); "
                      "import installer; assert installer.GETTING_STARTED.endswith('xmrjackpot.com'); "
                      "assert installer.PUBLIC_STRATUM == 'mine.xmrjackpot.com:3333'; "
                      "assert installer.PLACEHOLDER_WALLET != ''; "
                      "assert not (sys.path.__contains__(r'" + str(_target / "monero_project" / "pool") + "')); "
                      "print('staged-tree-ok')"],
                     capture_output=True, text=True, timeout=120)
check(5.4, _r3.returncode == 0 and "staged-tree-ok" in _r3.stdout,
      f"the staged tree imports its own installer with neutral miner values: {_r3.stdout.strip() or _r3.stderr[-200:]}")
_check_src = (_target / "monero_project" / "installer" / "installer.py").read_text(encoding="utf-8")
check(5.5, '"Not sure where to get started? "' in _check_src
      and '"Start here: xmrjackpot.com"' in _check_src,
      "the staged installer still carries the byte-exact phrase (the one "
      "source; its two adjacent string literals are pinned - the runtime "
      "proof is 5.4's GETTING_STARTED assertion)")
_rec = _target / "monero_project" / "installer" / "install_config.json"
check(5.6, not _rec.exists(),
      "staging wrote no opt-in record - the choice belongs to the checkbox step")

print("== 6. THE UNINSTALLER: self-deleting, opt-out through the CLI ==")
_un = _surfaces["Uninstall Kraken Scout.bat"]
check(6.1, "--opt-out" in _un and "installer.py" in _un,
      "the change-of-heart path records the opt-out through the installer's own CLI")
check(6.2, "rmdir /s /q" in _un and "ping" in _un and "start" in _un,
      "the folder removal is the whole story (self-deleting via the deferred cmd)")
check(6.3, "reg add" not in _un.lower() and "schtasks" not in _un.lower(),
      "no registry writes, no scheduled tasks - deleting the folder IS the uninstall")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
