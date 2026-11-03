"""health_check.py — THE ONE-PASS HEALTH COMMAND (Sep 13).

WHY THIS EXISTS: every incident began with "is it even running, and is
the paperwork intact?" answered by five ad-hoc commands. This is the
one command: scanner, bot, volatility engine, console — and every
ledger's integrity — verified in a single pass, SAFE TO RUN ANYTIME:
strictly read-only (no restarts, no writes, no kills), stdlib only,
exit-coded (0 healthy / 1 findings) for schedulers and .bat wrappers.

Process checks ride the launcher's own semantics (launch_stack.ps1):
    scanner   background_scanner.py + scanner.lock pid alive
    bot       bot_runner in the sibling trading-bot dir + bot.lock
    vol x2    volatility_engine.py (two engines are the shipped shape)
    console   something LISTENING on :8501 (the launcher's own test)
Ledger checks: JSON parses, JSONL parses line-by-line (a bad line is
named, never silently skipped), append-only ledgers keep their dedupe
contract (no duplicate (state, action, week) real events), and every
consumer-pid file names a live process (a stale pid is a finding, the
scanner guard's own lesson). Bare `python health_check.py` prints the
human report; `--json` prints the machine dict for schedulers.
Run: .venv\\Scripts\\python.exe -X utf8 health_check.py [--json]
"""
import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOT_DIR = ROOT.parent / "kraken_xrp_trading_bot"

# the ledger paperwork, with the integrity contract each must hold:
#   json   — the whole file parses as one JSON document
#   jsonl  — every line parses (blank lines allowed); a duplicate
#            (state, action, week) triple among REAL flip events is a
#            finding (sims are excluded — they seed deliberately)
LEDGERS = {
    "tide_bar_flip_events.jsonl": "flip_events",
    "_tidebarweekly_history.jsonl": "history",
    "contest_trades.jsonl": "contest_trades",
    "popup_suppressions.jsonl": "suppressions",
    "signal_snapshots.jsonl": "signal_snapshots",
    "_flipgrade.json": "json",
    "_tidebarweekly.json": "json",
    "console_state.json": "json",
    "contest_account.json": "json",
    "payout_track.json": "json",
    "scanner_state.json": "json",
    "execution_state.json": "json",
    "discipline_state.json": "json",
    "rotation_lockout.json": "json",
}

# Lazily-written ledgers: a reader creates them on first use, so their
# ABSENCE is the healthy shape until the machinery first fires. A finding
# only when present-but-corrupt (check_ledgers) — never for missing.
EXPECTED_ABSENT = {
    "tide_bar_flips.json",
}

_pid_cache = {}
_cmdline_cache = {}


def _pid_alive(pid: int) -> bool:
    """The scanner guard's lesson, reused: os.kill(pid, 0) is WRONG on
    Windows (WinError 87 for any non-child). tasklist is the reliable
    cross-parent liveness check; results are cached per pass."""
    if pid in _pid_cache:
        return _pid_cache[pid]
    alive = False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5)
        alive = "python" in r.stdout.lower()
    except Exception:
        alive = False
    _pid_cache[pid] = alive
    return alive


def _port_listening(port: int) -> bool:
    """The launcher's own console test: something LISTENING on the
    port. Bind-probe is read-only and race-free enough for a check."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        # connecting is the honest test that the console ANSWERS
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _cmdline_matches(pid: int, needle: str) -> bool:
    """True when the pid's command line contains needle — a stale pid
    file naming an unrelated python process must NOT pass as ours.
    wmic is NOT on this machine's PATH (a prior session proved it);
    PowerShell CIM is the reliable reader. Results cached per pass."""
    if pid in _cmdline_cache:
        return needle.lower() in _cmdline_cache[pid]
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            capture_output=True, text=True, timeout=15)
        _cmdline_cache[pid] = (r.stdout or "")
        return needle.lower() in _cmdline_cache[pid]
    except Exception:
        return False


def check_processes(root: Path = ROOT, bot_dir: Path = BOT_DIR,
                    probe=None):
    """Liveness of the five components. Returns [(name, ok, detail)].
    probe(name) overrides a single component for the selftest."""
    out = []

    def _locked_pid(lock: Path):
        try:
            return int(lock.read_text().strip())
        except Exception:
            return None

    def _default(name):
        if name == "scanner":
            lock = root / "scanner.lock"
            pid = _locked_pid(lock)
            if pid is None:
                return False, "no scanner.lock (scanner not running)"
            if not _pid_alive(pid):
                return False, f"stale scanner.lock: pid {pid} is dead"
            if not _cmdline_matches(pid, "background_scanner"):
                return False, (f"pid {pid} is alive but is not the "
                               "scanner (recycled pid)")
            return True, f"pid {pid} alive, command line verified"
        if name == "bot":
            lock = bot_dir / "bot.lock"
            pid = _locked_pid(lock) if lock.exists() else None
            if pid is None:
                return False, "no bot.lock (bot not running)"
            if not _pid_alive(pid):
                return False, f"stale bot.lock: pid {pid} is dead"
            return True, f"pid {pid} alive"
        if name == "vol":
            # the shipped shape: TWO engines. Find them by command
            # line among live python processes (tasklist then wmic).
            live = _live_pythons()
            vols = [p for p in live
                    if _cmdline_matches(p, "volatility_engine")]
            if len(vols) >= 2:
                return True, f"{len(vols)} engines alive {vols}"
            if len(vols) == 1:
                return False, f"only 1 volatility engine alive {vols} (2 ship)"
            return False, "no volatility engine alive"
        if name == "console":
            if _port_listening(8501):
                return True, "answering on :8501"
            return False, "nothing listening on :8501"
        return False, f"unknown component {name}"

    probe = probe or _default
    for name in ("scanner", "bot", "vol", "console"):
        try:
            ok, detail = probe(name)
        except Exception as exc:
            ok, detail = False, f"check raised: {exc!r}"
        out.append((name, bool(ok), detail))
    return out


def _live_pythons():
    """Pids of live python processes (tasklist, the guard's tool)."""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5)
        out = []
        for line in (r.stdout or "").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) > 1 and parts[1].isdigit():
                out.append(int(parts[1]))
        return out
    except Exception:
        return []


def check_ledgers(root: Path = ROOT, ledgers=None):
    """Integrity of the paperwork. Returns [(name, ok, detail)]."""
    ledgers = ledgers if ledgers is not None else LEDGERS
    out = []
    for fname, kind in ledgers.items():
        p = root / fname
        if not p.exists():
            if fname in EXPECTED_ABSENT:
                out.append((fname, True,
                            "absent — no real flip yet (lazy ledger)"))
            else:
                out.append((fname, False, "missing"))
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append((fname, False, f"unreadable: {exc}"))
            continue
        if kind == "json":
            try:
                json.loads(raw)
                out.append((fname, True, "parses"))
            except json.JSONDecodeError as exc:
                out.append((fname, False,
                            f"corrupt JSON at char {exc.pos}"))
            continue
        # jsonl: every line must parse — a bad line is NAMED here,
        # never silently skipped the way the loaders' fail-open does
        bad, n = [], 0
        seen_real = set()
        dup_real = 0
        for ln, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            n += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append((ln, str(exc)[:60]))
                continue
            if kind == "flip_events" and not rec.get("sim"):
                key = (str(rec.get("state") or ""),
                       str(rec.get("action") or ""),
                       str(rec.get("week") or ""))
                if key in seen_real:
                    dup_real += 1
                seen_real.add(key)
        if bad:
            out.append((fname, False,
                        f"{len(bad)} unparseable line(s): "
                        f"first at {bad[0][0]} ({bad[0][1]})"))
        elif dup_real:
            out.append((fname, False,
                        f"{dup_real} duplicate real event(s) — "
                        "the dedupe contract is broken"))
        else:
            extra = (f", dedupe contract holds over {n} records"
                     if kind == "flip_events" else f", {n} records")
            out.append((fname, True, "parses" + extra))
    return out


def run_health(root: Path = ROOT, bot_dir: Path = BOT_DIR,
               probe=None, ledgers=None) -> dict:
    """One pass. Returns {'processes': [...], 'ledgers': [...],
    'healthy': bool}. Nothing here writes, restarts, or kills."""
    procs = check_processes(root, bot_dir, probe=probe)
    ledgs = check_ledgers(root, ledgers)
    return {"processes": procs, "ledgers": ledgs,
            "healthy": all(ok for _, ok, _ in procs + ledgs)}


def render(report: dict) -> str:
    """The human report: one line per check, a verdict per section."""
    lines = ["=== SCOUT HEALTH — one pass, read-only ==="]
    for name, ok, detail in report["processes"]:
        mark = "OK  " if ok else "DEAD"
        lines.append(f"  [{mark}] {name:9s} {detail}")
    n_ok = sum(1 for _, ok, _ in report["ledgers"] if ok)
    n_absent = sum(1 for n, ok, _ in report["ledgers"]
                   if ok and "absent" in _)
    lines.append(f"  ledgers: {n_ok}/{len(report['ledgers'])} intact"
                 + (f" ({n_absent} lazily absent)" if n_absent else ""))
    for name, ok, detail in report["ledgers"]:
        if not ok:
            lines.append(f"  [BAD ] {name:36s} {detail}")
    lines.append("")
    lines.append("HEALTHY — all clear" if report["healthy"]
                 else "FINDINGS PRESENT — see [DEAD]/[BAD] above")
    return "\n".join(lines)


BAD_SNIPPETS = {
    "flip_events": '{"state": "OUT", "action": "flip", "week": "2026-W39"}\nnot json at all\n',
    "json": '{"key": "value"\n',
}
GOOD_FLIPS = (
    '{"state": "OUT", "action": "flip", "week": "2026-W39"}\n'
    '{"state": "OUT", "action": "flip_back", "week": "2026-W44"}\n')
DUP_FLIPS = (
    '{"state": "OUT", "action": "flip", "week": "2026-W39"}\n'
    '{"state": "OUT", "action": "flip", "week": "2026-W39"}\n'
    '{"sim": true, "state": "OUT", "action": "flip", "week": "2026-W39"}\n')


def selftest(root: Path = ROOT) -> tuple:
    """Sandbox-provable: corrupt JSON, a bad JSONL line, duplicate
    real flip events, a missing file, and the all-clean path."""
    import tempfile
    sbx = Path(tempfile.mkdtemp())
    bads = []
    try:
        (sbx / "a.json").write_text(BAD_SNIPPETS["json"], encoding="utf-8")
        (sbx / "b.jsonl").write_text(BAD_SNIPPETS["flip_events"],
                                     encoding="utf-8")
        (sbx / "c.jsonl").write_text(GOOD_FLIPS, encoding="utf-8")
        (sbx / "d.jsonl").write_text(DUP_FLIPS, encoding="utf-8")
        rep = run_health(
            root=sbx,
            ledgers={"a.json": "json", "b.jsonl": "flip_events",
                     "c.jsonl": "flip_events", "d.jsonl": "flip_events",
                     "missing.json": "json"})
        by = {n: (ok, d) for n, ok, d in rep["ledgers"]}
        if by["a.json"][0] or "corrupt JSON" not in by["a.json"][1]:
            bads.append(f"corrupt json not caught: {by['a.json']}")
        if by["b.jsonl"][0] or "unparseable line" not in by["b.jsonl"][1]:
            bads.append(f"bad jsonl line not caught: {by['b.jsonl']}")
        if not by["c.jsonl"][0]:
            bads.append(f"clean jsonl flagged: {by['c.jsonl']}")
        if by["d.jsonl"][0] or "duplicate" not in by["d.jsonl"][1]:
            bads.append(f"duplicate real event not caught: {by['d.jsonl']}")
        if by["missing.json"][0] or by["missing.json"][1] != "missing":
            bads.append(f"missing file not caught: {by['missing.json']}")
        if rep["healthy"]:
            bads.append("a report with findings came back healthy")
        # the all-clean path must flip the verdict
        (sbx / "ok.json").write_text('{"k": 1}', encoding="utf-8")
        rep2 = run_health(root=sbx, probe=lambda name: (True, "probe"),
                          ledgers={"ok.json": "json"})
        if not rep2["healthy"]:
            bads.append("all-clean world not healthy")
        dead = run_health(root=sbx,
                          probe=lambda name: (False, "probe dead"),
                          ledgers={"ok.json": "json"})
        if dead["healthy"]:
            bads.append("a dead process passed as healthy")
        return ("; ".join(bads) if bads
                else "corrupt/bad-line/dup/missing caught, "
                     "clean world healthy, probe-dead fails"), not bads
    except Exception as exc:
        return f"selftest raised: {exc!r}", False
    finally:
        import shutil
        shutil.rmtree(sbx, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Scout one-pass health check")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable report for schedulers")
    ap.add_argument("--selftest", action="store_true",
                    help="run the sandbox selftest and exit")
    args = ap.parse_args()
    if args.selftest:
        d, ok = selftest()
        print(f"selftest: {'PASS' if ok else 'FAIL'} — {d}")
        return 0 if ok else 1
    report = run_health()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
