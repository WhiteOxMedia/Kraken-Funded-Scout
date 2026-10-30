"""up-vrsc.py — ONE COMMAND to bring the Verus relay up (Oct 28).

WHY THIS EXISTS: the Verus branch's miners cannot reach Monero routes, and
pool/vrsc_relay.py has to run somewhere with both the live jackpot ledger AND
a public address. Hand-driving the pieces is five steps across two machines
(netstat guard, detached start, logon persistence, scp, remote bootstrap,
firewall rule, probe) and every one of them drifts the moment someone learns
a faster way. This script IS the procedure, so the runbook says
`python relay/up-vrsc.py` and means one command with no variants to remember.

TOPOLOGY (deliberately the Monero mirror, one ledger shared with the pool):

    verus miner -> VPS:${VRSC_PORT} (haproxy) -> wg0 -> pool PC (vrsc_relay.py
                 --db pool/jackpot.db -- upstream na.luckpool.net:3956)

The relay PROCESS runs on the pool PC, not on the VPS, on purpose and against
its own docstring's first instinct: the docstring wanted the ledger "mounted"
to the VPS box, which no mechanism in this kit provides, and an unmerged copy
of jackpot.db counting entries on the VPS means Verus miners' raffle entries
quietly vanish until someone remembers to merge it home — before every daily
draw, forever. Here the relay writes the LIVE ledger, the same file the pool
server (and xmrig-proxy's Monero entries) writes; the VPS is just one more
public lane over the tunnel that already exists. That is also why the
acceptance risk is a failure class the kit already owns for 3333 (a WireGuard
drop takes the lane down until the pool PC is back), and not a new one.

WHAT ONE COMMAND DOES, in order:
  1. (re)writes relay/vrsc-relay.cmd with the current --port/--stratum so the
     guard, the log and the baked args can never drift from this run;
  2. starts the relay detached on the pool PC — or adopts one already
     LISTENING, because only one copy may hold the port;
  3. ships relay/bootstrap-relay.sh to the VPS and runs it with --with-vrsc,
     which adds the one haproxy lane (VPS:${PORT} -> ${HOME_ADDR}:${PORT})
     and opens ${PORT}/tcp in ufw; idempotent, so re-running is cheap;
  4. copies vrsc-relay.cmd into Startup (logon persistence), like
     relay-monitor.cmd already does for the monitor;
  5. checks the Windows firewall rule the tunnelled lane needs and prints the
     elevated command when it is missing (rule creation needs admin; making
     it silently or demanding elevation from a script that might run
     headless are both worse than saying it);
  6. probes the public lane with relay/verify-stratum.py — the same probe
     rule as the 3333 front: a PASS from the pool PC proves the tunnel and
     the backend, not the internet, so --external remains the last word.

IT DOES NOT DO (by design, each is one deliberate manual step):
   - the XMRJ_VRSC_STRATUM cutover (the pool host names the relay at boot so
     the generated .bat carries the answer; printed below, applied by you);
   - DNS (no new record: mine.xmrjackpot.com already answers on the VPS, one
     port number differs);
   - anything foreign to the kit. --dry-run prints the exact plan.

USAGE
    python relay/up-vrsc.py                # the one command (probe included)
    python relay/up-vrsc.py --dry-run      # the exact plan, nothing runs
    python relay/up-vrsc.py --no-probe     # skip the verify leg
    python relay/up-vrsc.py --no-autostart # don't copy into Startup
    XMRJ_VRSC_VPS=1.2.3.4 python relay/up-vrsc.py    # another VPS front
    python relay/up-vrsc.py --port 3957 --stratum na.luckpool.net:3956
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL = REPO / "pool"
RELAY_DIR = REPO / "relay"
CMD_NAME = "vrsc-relay.cmd"
LOG_FILE = REPO / ".relay" / "logs" / "vrsc-relay.log"
DEFAULT_VPS = "155.94.157.66"          # the deployed front (relay/monitor-relay.py)
DEFAULT_PORT = 3957
DEFAULT_UPSTREAM = "na.luckpool.net:3956"
FIREWALL_RULE = "vrsc stratum via relay"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" \
    / "Start Menu" / "Programs" / "Startup"


# ── the log: stdout only; the relay itself owns the file log ──────────────
def _log(*parts):
    print("[up-vrsc]", " ".join(str(p) for p in parts), flush=True)


def die(msg, exit_code=1):
    _log("ERROR:", msg)
    sys.exit(exit_code)


# ── pure helpers (test_vrsc_relay_wire.py drives these without a socket) ──
def parse_netstat(text, port):
    """Which PIDs LISTEN on :port. Only LISTENING counts: run.md's zombie
    lesson (ALIVE with its socket gone) is exactly what a bare tabular pass
    over netstat would miss."""
    pids = []
    for raw in (text or "").splitlines():
        cols = raw.split()
        if (len(cols) >= 4 and cols[0].upper() == "TCP"
                and cols[3].upper() == "LISTENING"
                and cols[1].endswith(":%d" % port)):
            pids.append(cols[-1])
    return pids


def relay_cmd_body(port, upstream, repo_root):
    """The guarded entry point. Absolute cd: the Startup copy runs from the
    Startup folder, where %~dp0 would be a lie."""
    return "\r\n".join([
        "@echo off",
        "rem vrsc-relay.cmd - REGENERATED by relay/up-vrsc.py; do not edit by hand.",
        "rem The guard listens on the LISTENING line, not the pid (run.md's zombie note).",
        'cd /d "%s"' % repo_root,
        'if not exist ".relay\\logs" mkdir ".relay\\logs"',
        'netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1'.replace(
            "%PORT%", str(port)),
        "if %errorlevel%==0 (",
        "  echo vrsc relay already LISTENING on %d - not starting a second copy." % port,
        "  exit /b 0",
        ")",
        'echo [%date% %time%] vrsc relay starting >> ".relay\\logs\\vrsc-relay.log"',
        'python pool\\vrsc_relay.py --listen-port %s --stratum "%s" '
        '--db pool\\jackpot.db >> ".relay\\logs\\vrsc-relay.log" 2>&1' % (port, upstream),
        "",
    ])


def remote_commands(args):
    """The two shipped commands, pure for tests/ordering assertions: the scp
    that refreshes the bootstrap copy, then the ssh that runs it with
    --with-vrsc. Exit codes propagate; nothing is captured."""
    key = str(args.ssh_key)
    target = "root@%s" % args.vps
    scp = ["scp", "-P", str(args.ssh_port), "-i", key,
           str(RELAY_DIR / "bootstrap-relay.sh"), "root@%s:/root/bootstrap-relay.sh" % args.vps]
    remote = "VRSC_PORT=%d bash /root/bootstrap-relay.sh --with-vrsc" % args.port
    ssh = ["ssh", "-p", str(args.ssh_port), "-i", key,
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, remote]
    return [scp, ssh]


def probe_command(args):
    """relay/verify-stratum.py against the PUBLIC lane. Local-run caveat
    applies (it proves the tunnel + backend by coming back through haproxy)."""
    return [sys.executable, str(RELAY_DIR / "verify-stratum.py"),
            args.vps, str(args.port)]


def firewall_command_string(port):
    """The exact elevated rule the lane needs — the 3333 rule's sibling,
    scoped to the tunnel address so nothing else learns 3957 is open."""
    return ("New-NetFirewallRule -DisplayName %r "
            "-Direction Inbound -Protocol TCP -LocalPort %d "
            "-RemoteAddress 10.66.66.1 -Action Allow" % (FIREWALL_RULE, port))


# ── the steps ─────────────────────────────────────────────────────────────
def _run(argv, **kw):
    _log("$", subprocess.list2cmdline(argv))
    proc = subprocess.run(argv, cwd=str(REPO), **kw)
    if proc.returncode != 0:
        die("command failed with exit code %d: %s"
            % (proc.returncode, subprocess.list2cmdline(argv)),
            proc.returncode or 1)
    return proc


def local_netstat(port):
    proc = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
    return parse_netstat(proc.stdout, port)


def write_entry_point(args):
    """(Re)write vrsc-relay.cmd so the run's args and the file agree even
    when the relay was already running (the next logon picks the new args)."""
    body = relay_cmd_body(args.port, args.upstream, str(REPO))
    cmd_path = RELAY_DIR / CMD_NAME
    cmd_path.write_text(body, encoding="utf-8")
    _log("entry point refreshed:", cmd_path.name)
    return cmd_path


def start_local_relay(args):
    """Adopt or start. The guard inside vrsc-relay.cmd is second to this
    check: netstat is scanned HERE first so an already-running relay is
    adopted silently, not raced."""
    if local_netstat(args.port):
        _log("relay already LISTENING on :%d - adopted" % args.port)
        return
    if not (POOL / "vrsc_relay.py").is_file():
        die("pool/vrsc_relay.py is missing; nothing to bring up")
    subprocess.Popen(["cmd", "/c", "start", "vrsc-relay", "/min",
                      str(RELAY_DIR / CMD_NAME)])
    for _ in range(30):                                  # ~15 s
        if local_netstat(args.port):
            _log("relay LISTENING on :%d" % args.port)
            return
        time.sleep(0.5)
    tail = ""
    if LOG_FILE.is_file():
        tail = "\n".join(LOG_FILE.read_text(encoding="utf-8",
                                            errors="replace").splitlines()[-12:])
    die("relay did NOT come up on :%d within 15 s. vrsc-relay.log tail:\n%s"
        % (args.port, tail or "(no log yet)"))


def autostart(args):
    if args.no_autostart:
        _log("autostart skipped (--no-autostart)")
        return
    if not STARTUP_DIR.is_dir():
        _log("Startup folder not found; logon persistence NOT installed "
             "(copy vrsc-relay.cmd there by hand)")
        return
    probe = STARTUP_DIR / CMD_NAME
    shutil.copy2(RELAY_DIR / CMD_NAME, probe)
    _log("logon persistence installed:", probe)


def firewall_step(port):
    """Detect, never create: rule creation needs admin and the exact command
    is the deliverable. Detection failing is a soft report, not an abort."""
    probe = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-NetFirewallRule -DisplayName '%s' -ErrorAction "
         "Stop).Enabled" % FIREWALL_RULE],
        capture_output=True, text=True)
    if probe.returncode == 0 and "True" in (probe.stdout or ""):
        _log("firewall rule present ('%s')" % FIREWALL_RULE)
        return
    _log("firewall: rule '%s' MISSING. Run once in an ELEVATED PowerShell:"
         % FIREWALL_RULE)
    _log("    " + firewall_command_string(port))
    _log("    (until then the VPS cannot reach 3957 through the tunnel)")


def probe_step(args):
    if args.no_probe:
        _log("probe skipped (--no-probe)")
        return
    if not (RELAY_DIR / "verify-stratum.py").is_file():
        die("relay/verify-stratum.py is missing; cannot prove the lane")
    _run(probe_command(args))
    _log("remember: the --external probe (foreign network) still decides the "
         "cutover; this one only proved the tunnel leg")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Bring up the Verus relay: local process + VPS front, one command.")
    ap.add_argument("--vps", default=os.environ.get("XMRJ_VRSC_VPS", DEFAULT_VPS),
                    help="the public front to ship --with-vrsc to "
                         "(default XMRJ_VRSC_VPS or %s)" % DEFAULT_VPS)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="public lane and relay listen port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                    help="the Verus stratum shares forward to "
                         "(default %s)" % DEFAULT_UPSTREAM)
    ap.add_argument("--ssh-port", type=int, default=22)
    ap.add_argument("--ssh-key", type=Path,
                    default=REPO / ".relay" / "ssh" / "relay_admin",
                    help="the deploy key (default .relay/ssh/relay_admin)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip relay/verify-stratum.py at the end")
    ap.add_argument("--no-autostart", action="store_true",
                    help="do not copy vrsc-relay.cmd into the Startup folder")
    ap.add_argument("--no-relay-start", action="store_true",
                    help="ship the front, leave the local relay alone")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact plan; run nothing")
    return ap.parse_args(argv)


def main():
    args = parse_args()

    bootstrap = RELAY_DIR / "bootstrap-relay.sh"
    for src in (bootstrap, POOL / "vrsc_relay.py",
                RELAY_DIR / "verify-stratum.py"):
        if not src.is_file():
            die("missing piece: %s" % src)
    if args.dry_run:
        print("== up-vrsc plan (nothing executed) ==")
        print(" 1. %s" % ("refresh %s (port %d -> %s, db pool/jackpot.db)"
              % (CMD_NAME, args.port, args.upstream)))
        print(" 2. start pool/vrsc_relay.py detached on this PC (or adopt a listener)")
        for i, argv in enumerate(remote_commands(args), start=3):
            print(" %d. %s" % (i, subprocess.list2cmdline(argv)))
        print(" 5. copy %s into Startup (unless --no-autostart)" % CMD_NAME)
        print(" 6. firewall rule check; if missing, print:")
        print("      " + firewall_command_string(args.port))
        print(" 7. %s" % subprocess.list2cmdline(probe_command(args)))
        print(" 8. you: set XMRJ_VRSC_STRATUM=%s:%d on the pool host"
              % (args.vps, args.port))
        return 0
    if not args.ssh_key.is_file():
        die("deploy key %s not found. Install it once (relay/fix-ssh-access.sh "
            "or relay/README.md 'First login') or pass --ssh-key" % args.ssh_key)

    write_entry_point(args)
    if not args.no_relay_start:
        start_local_relay(args)
    else:
        _log("local relay untouched (--no-relay-start)")

    for argv in remote_commands(args):
        _run(argv)
    _log("VPS front updated: :%d/tcp -> wg0 -> this PC (idempotent; re-run safe)"
         % args.port)

    autostart(args)
    firewall_step(args.port)
    probe_step(args)

    _log("")
    _log("UP. the manual remainder (printed, not assumed):")
    _log("  cutover : set XMRJ_VRSC_STRATUM=%s:%d on the POOL HOST at boot "
         "(the Verus .bat reads it at generation)" % (args.vps, args.port))
    _log("            mine.xmrjackpot.com:%d needs no new DNS record." % args.port)
    _log("            the --external probe from a foreign network is the proof.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
