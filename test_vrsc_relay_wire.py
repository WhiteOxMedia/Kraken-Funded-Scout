"""test_vrsc_relay_wire.py — THE ONE COMMAND STAYS ONE COMMAND (Oct 28).

relay/up-vrsc.py + relay/bootstrap-relay.sh --with-vrsc must keep wiring the
Verus relay exactly how relay/README.md describes it:

    verus miner -> VPS:VRSC_PORT (haproxy) -> wg0 -> pool PC (vrsc_relay.py
                 --db pool/jackpot.db -> upstream)

What this file pins, WITHOUT ssh, WITHOUT a socket, WITHOUT a VPS:

  1. THE PIECES EXIST   every file the one command touches on the pool PC.
  2. THE DRIVER'S PLAN IS DETERMINISTIC
     - relay/vrsc-relay.cmd regenerates with args baked in, keeps the
       LISTENING-line guard (run.md's zombie lesson), and cds by ABSOLUTE
       path (the Startup copy runs from the Startup folder, where %~dp0 lies);
     - remote_commands is exactly two commands in one order: scp the
       bootstrap, then ssh run it with --with-vrsc (the order IS the
       one-command promise);
     - the probe targets the VPS on the SAME port the relay serves, never
       a second number to keep in your head;
     - the firewall rule is scoped to 10.66.66.1 — nothing outside wg0
       learns the port exists.
  3. THE BOOTSTRAP LANE IS REAL
     - `bash -n` parses the script (SKIPPED, and named as such, when bash
       is absent so the drive stays applicable);
     - --with-vrsc parses, defaults VRSC_PORT=3957 and emits the haproxy
       lane through the EXISTING peer: ${HOME_ADDR}:${VRSC_PORT} backend,
       ufw 3957/tcp — no new service, no second stack, no second peer;
     - the Monero lane's own sections stand untouched.
  4. NO DRIFT   up-vrsc's default VPS is the front already deployed
     (relay/monitor-relay.py), and both new tools compile clean.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELAY = HERE / "relay"
POOL = HERE / "pool"

passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


# ── the driver, imported like a tool, not executed ───────────────────────
_spec = importlib.util.spec_from_file_location("up_vrsc", RELAY / "up-vrsc.py")
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)


class Args:
    """parse_args()' defaults, without argparse touching a tty."""
    vps = up.DEFAULT_VPS
    port = up.DEFAULT_PORT
    upstream = up.DEFAULT_UPSTREAM
    ssh_port = 22
    ssh_key = RELAY / ".relay-ssh-stub"


print("== 1. THE PIECES EXIST ==")
for i, f in enumerate((
        POOL / "vrsc_relay.py", POOL / "database.py", POOL / "field_crypto.py",
        POOL / "jackpot_engine.py", RELAY / "bootstrap-relay.sh",
        RELAY / "verify-stratum.py", RELAY / "up-vrsc.py"), start=1):
    check(i, f.is_file(), f"the one command's piece exists: {f.relative_to(HERE)}")

print("\n== 2. THE DRIVER'S PLAN IS DETERMINISTIC ==")
cmd_lf = up.relay_cmd_body(Args.port, Args.upstream, str(HERE)).replace("\r\n", "\n")
check(2.1, 'findstr ":%d "' % Args.port in cmd_lf and "LISTENING" in cmd_lf,
      "vrsc-relay.cmd guards on the LISTENING line (only one copy may hold "
      "the port — the zombie case, not the pid)")
check(2.2, ('cd /d "%s"' % HERE) in cmd_lf,
      "the cd is ABSOLUTE — the Startup copy runs from the Startup folder, "
      "where %~dp0 would lie")
check(2.3, "--db pool\\jackpot.db" in cmd_lf,
      "the relay writes THE LIVE ledger (pool/jackpot.db), not a copy")
check(2.4, '--stratum "%s"' % Args.upstream in cmd_lf
      and "--listen-port %d" % Args.port in cmd_lf,
      "port and upstream are baked at generation, never left to drift")

cmds = up.remote_commands(Args())
check(2.5, len(cmds) == 2 and cmds[0][0] == "scp" and cmds[1][0] == "ssh",
      "the remote plan is exactly TWO commands: scp the bootstrap, then ssh "
      "run it")
check(2.6, "--with-vrsc" in cmds[1][-1]
      and "VRSC_PORT=%d" % Args.port in cmds[1][-1],
      "the ssh step carries the runtime knob: VRSC_PORT + --with-vrsc")

probe = up.probe_command(Args())
check(2.7, probe[-1] == str(Args.port) and probe[-2] == Args.vps,
      "the probe names the VPS and the SAME port the relay serves — never a "
      "second number to keep in your head")

fw = up.firewall_command_string(Args.port)
check(2.8, "-RemoteAddress 10.66.66.1" in fw and str(Args.port) in fw,
      "the firewall rule is scoped to the tunnel address: nothing outside "
      "wg0 learns 3957 exists")

print("\n== 3. THE BOOTSTRAP LANE IS REAL ==")
proc = subprocess.run(["bash", "-n", str(RELAY / "bootstrap-relay.sh")],
                      capture_output=True, text=True)
if proc.returncode == 0:
    check(3.1, True, "bash -n parses bootstrap-relay.sh")
else:
    why = (proc.stderr or "").strip()[:120] or "returncode %d" % proc.returncode
    if proc.returncode == 1 and "bash" in (proc.stderr or "").lower():
        check(3.1, False, "SKIPPED: bash is absent on this box, -n could NOT "
                          "run: %r" % why)
    else:
        check(3.1, False, "bootstrap-relay.sh does NOT parse: %r" % why)

script = (RELAY / "bootstrap-relay.sh").read_text(encoding="utf-8", errors="replace")
check(3.2, "--with-vrsc) WITH_VRSC=" in script, "the flag parses into WITH_VRSC")
check(3.3, 'VRSC_PORT="${VRSC_PORT:-3957}"' in script,
      "the lane's default port is VRSC_PORT:3957")
check(3.4, "frontend vrsc_in" in script and "bind *:%s" in script
      and "default_backend verus_relay" in script,
      "a NEW frontend, emitted with the lane port substituted in: one more "
      "bind on the haproxy that already fronts 3333")
check(3.5, "backend verus_relay" in script
      and "server vrsc-relay-pc ${HOME_ADDR}:%s check" in script,
      "the backend rides the EXISTING wg peer (${HOME_ADDR}) — no second "
      "peer, no second stack")
ufw_zone = script.split('ufw allow "${VRSC_PORT}/tcp"')[0][-300:]
check(3.6, 'ufw allow "${VRSC_PORT}/tcp"' in script
      and "WITH_VRSC" in ufw_zone,
      "ufw opens the lane only inside the WITH_VRSC gate (the gate reads "
      "within 300 chars before the lane rule)")
check(3.7, "pool/vrsc_relay.py runs on the POOL PC" in script
      and "XMRJ_VRSC_STRATUM=" in script,
      "the NEXT notes say where the process lives and which env carries the "
      "cutover")
check(3.8, "frontend stratum_in" in script and "backend p2pool" in script,
      "the Monero lane is untouched by the wiring (the 3333 path stands alone)")

print("\n== 4. NO DRIFT ==")
mon = (RELAY / "monitor-relay.py").read_text(encoding="utf-8", errors="replace")
check(4.1, 'EXPECTED_IP = "%s"' % up.DEFAULT_VPS in mon,
      "up-vrsc's default VPS IS the deployed front (monitor-relay.py's)")
cmp_proc = subprocess.run(
    [sys.executable, "-m", "py_compile", str(POOL / "vrsc_relay.py"),
     str(RELAY / "up-vrsc.py")], capture_output=True, text=True)
check(4.2, cmp_proc.returncode == 0,
      "vrsc_relay.py and up-vrsc.py compile clean: %r"
      % ((cmp_proc.stderr or "").strip()[:100] or "clean"))

print("\nRESULT: PASS %d  FAIL %d" % (passed, failed))
sys.exit(1 if failed else 0)
