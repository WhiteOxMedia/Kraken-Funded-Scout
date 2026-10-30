#!/usr/bin/env python3
"""monitor-relay.py - say PASS until the stratum relay actually breaks.

Probes, ordered by what fails first when something goes wrong:

  1. pool     127.0.0.1:3333              the xmrig-proxy the relay forwards to
  2. public   mine.xmrjackpot.com:3333    what a miner really connects to
  3. dns      mine -> expected IP         a mispointed or hijacked record
  4. tunnel   VPS: wg handshake age + haproxy state  (needs the admin key)

Verdicts only on stdout. Every probe's detail goes to .relay/logs/monitor.jsonl,
and alerts fire on a CHANGE of state, never once per cycle - a flapping tunnel
should not produce a popup every 60 seconds.

    python relay/monitor-relay.py               # forever, 60 s cycles
    python relay/monitor-relay.py --once        # one cycle, exit 1 on any ALERT
    python relay/monitor-relay.py --external    # + check-host probe every 15 min
    python relay/monitor-relay.py --external-every 300 --interval 30

Why not reuse verify-stratum.py wholesale? Its --external path demands *every*
node connect, and a single check-host node occasionally times out while three
others answer (ro1 did exactly that twice on 2026-10-24). Here the external probe
only alerts when NO node can connect, which is the actual failure mode.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY = ROOT / ".relay" / "ssh" / "relay_admin"
LOGDIR = ROOT / ".relay" / "logs"
STATUS = ROOT / ".relay" / "relay-status.json"

PUBLIC_HOST = "mine.xmrjackpot.com"
PUBLIC_PORT = 3333
EXPECTED_IP = "155.94.157.66"
POOL_ADDR = ("127.0.0.1", 3333)
VPS = "155.94.157.66"
WG_MAX_HANDSHAKE_AGE = 180        # keepalive is 25 s; 3 min means the peer is gone

LOGIN = (
    b'{"id":1,"jsonrpc":"2.0","method":"login",'
    b'"params":{"login":"relay-monitor","pass":"x","agent":"relay-monitor/1"}}\n'
)


def log(event: dict) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with (LOGDIR / "monitor.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def probe_stratum(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """One p2pool login. Must come back with a job; a bare TCP connect is not enough."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.sendall(LOGIN)
        chunks, total = [], 0
        deadline = time.time() + timeout
        while time.time() < deadline and total < 8192:
            part = s.recv(4096)
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if b"\n" in part:
                break
        raw = b"".join(chunks)
        if not raw:
            return False, "connected, no reply"
        reply = json.loads(raw.split(b"\n")[0])
        result = reply.get("result") or {}
        if "job" in result:
            return True, f"job {result['job'].get('job_id', '?')}"
        return False, f"no job in reply ({sorted(reply)[:4]})"
    except json.JSONDecodeError:
        return False, "reply was not JSON (proxy or wrong listener)"
    except Exception as exc:  # noqa: BLE001 - report the reason, never crash
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        s.close()


def probe_dns(host: str, expected: str) -> tuple[bool, str]:
    try:
        ips = sorted({i[4][0] for i in socket.getaddrinfo(host, None, socket.AF_INET)})
    except Exception as exc:  # noqa: BLE001
        return False, f"resolve failed: {type(exc).__name__}"
    if ips == [expected]:
        return True, expected
    return False, f"resolves to {', '.join(ips)}, expected {expected}"


def ssh(cmd: str, timeout: float = 12.0) -> str:
    if not KEY.exists():
        raise FileNotFoundError(KEY)
    out = subprocess.run(
        ["ssh", "-i", str(KEY), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         "-o", f"ConnectTimeout={int(timeout)}", "-o", "IdentitiesOnly=yes", f"root@{VPS}", cmd],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    return (out.stdout + out.stderr).strip()


REMOTE_PROBE = "; ".join([
    'echo "HAPROXY=$(systemctl is-active haproxy 2>/dev/null || echo unknown)"',
    'echo "HANDSHAKE=$(wg show wg0 latest-handshakes 2>/dev/null | awk \'{print $2}\' | tail -1)"',
    '/usr/local/bin/relay-backend-status.py 2>&1 | tail -1',
    'echo "RC=$?"',
])


def probe_tunnel() -> tuple[bool, str]:
    """Three layers on the VPS, reported so a failure names the layer.

    haproxy process -> backend health check (the tunnel + pool) -> WireGuard age.
    Only haproxy knows whether its own check is passing; a DOWN backend and a
    blocked firewall look identical from the internet, which is why this probe
    reads the runtime socket instead of guessing.
    """
    try:
        state = ssh(REMOTE_PROBE)
    except FileNotFoundError:
        return False, "skipped: no admin key at .relay/ssh/relay_admin"
    except Exception as exc:  # noqa: BLE001 - VPS unreachable is a real finding
        return False, f"ssh failed: {type(exc).__name__}"

    values: dict[str, str] = {}
    backend = ""
    for line in state.splitlines():
        line = line.strip()
        if line.startswith("HAPROXY=") or line.startswith("HANDSHAKE=") or line.startswith("RC="):
            key, _, value = line.partition("=")
            values[key] = value
        elif line.startswith("p2pool/"):
            backend = line

    haproxy = values.get("HAPROXY", "unknown")
    handshake = values.get("HANDSHAKE", "")
    age = int(time.time()) - int(handshake) if handshake.isdigit() else None

    if haproxy != "active":
        return False, f"haproxy {haproxy} on the VPS"
    if backend and "status=UP" not in backend:
        fields = dict(part.split("=", 1) for part in backend.split()[1:] if "=" in part)
        return False, (f"haproxy up but backend is {fields.get('status', '?')} "
                       f"(check {fields.get('check', '?')}) - tunnel or pool, not haproxy")
    if age is None:
        return False, "no WireGuard peer handshake on the VPS"
    if age > WG_MAX_HANDSHAKE_AGE:
        return False, f"WireGuard handshake {age}s old (peer down)"
    tail = backend.split(" ", 1)[-1] if backend else "backend status unknown (stats socket missing)"
    return True, f"haproxy active, {tail}, handshake {age}s old"


def check_host(target: str, port: int) -> tuple[int, int]:
    """(connected, failed) for one TCP port, measured from check-host's nodes."""
    headers = {"Accept": "application/json", "User-Agent": "relay-monitor"}
    started = None
    try:
        started = json.load(urllib.request.urlopen(urllib.request.Request(
            f"https://check-host.net/check-tcp?host={target}&port={port}&max_nodes=6",
            headers=headers), timeout=20))
        rid = started.get("request_id") if isinstance(started, dict) else None
        if not rid:
            # three checks in one cycle is enough to hit their rate limit; say so
            # plainly instead of leaking a KeyError into a verdict
            raise RuntimeError(f"checker refused the request ({str(started)[:80]})")
        data: dict = {}
        for _ in range(15):
            time.sleep(3)
            data = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://check-host.net/check-result/{rid}", headers=headers), timeout=20))
            if all(v is not None for v in data.values()):
                break
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - the checker itself can be down
        raise RuntimeError(f"checker unavailable: {type(exc).__name__}: {exc}") from exc

    ok = bad = 0
    for value in data.values():
        entry = (value[0] if isinstance(value, list) else value) or {}
        bad += 1 if entry.get("error") else 0
        ok += 0 if entry.get("error") else 1
    return ok, bad


CONTROL_HOST, CONTROL_PORT = "1.1.1.1", 443


def fetch_witness(path: str | None, url: str | None, remote: str | None) -> str:
    """Pull the witness JSON from a file, an HTTPS endpoint, or another host.

    Three transports because the second vantage can take three shapes:
      file    - a laptop on a hotspot, run inside this repo
      url     - a Worker or any static host that answers with the same JSON
      remote  - a second VPS, read over ssh with the existing admin key
    """
    if url:
        req = urllib.request.Request(url, headers={"User-Agent": "relay-monitor"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    if remote:
        target, _, remote_path = remote.partition(":")
        if not remote_path:
            raise ValueError("remote witness must be user@host:/path")
        out = subprocess.run(
            ["ssh", "-i", str(KEY), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes", target, f"cat {remote_path}"],
            capture_output=True, text=True, timeout=40)
        if out.returncode != 0:
            raise RuntimeError((out.stderr or "ssh failed").strip()[:120])
        return out.stdout
    return pathlib.Path(path or "").read_text(encoding="utf-8")


def probe_witness(path: str | None, max_age: int, url: str | None = None,
                  remote: str | None = None) -> tuple[bool, str]:
    """A second opinion from a network that is not ours.

    `relay/ingress-check.py --witness <path>` writes this file from anywhere: a
    phone hotspot, a friend's VPS, a laptop on another ISP. It is the only check
    here that can distinguish 'the relay died' from 'this house can no longer
    reach its own VPS', because it does not share our routing.

    A stale file is reported, never trusted: an old PASS is not evidence of
    anything today. Only fresh PASS/FAIL moves the verdict.
    """
    try:
        data = json.loads(fetch_witness(path, url, remote))
    except FileNotFoundError:
        return False, ("skipped: no witness - run 'python3 relay/ingress-check.py "
                       "--witness .relay/witness.json' on another network, or pass "
                       "--witness-url / --witness-remote")
    except Exception as exc:  # noqa: BLE001 - a witness must never break the monitor
        return False, f"skipped: witness unreadable ({type(exc).__name__}: {str(exc)[:80]})"

    age = max(0, int(time.time() - data.get("when_epoch", 0)))
    where = data.get("egress") or "another network"
    if age > max_age:
        return False, f"skipped: witness is {age // 60} min old (limit {max_age // 60})"
    verdict = data.get("verdict")
    if verdict == "PASS":
        return True, f"PASS from {where} {age // 60} min ago"
    if verdict == "INCONCLUSIVE":
        return False, f"skipped: witness network ({where}) could not reach the control either"
    return False, f"FAIL from {where} {age // 60} min ago ({data.get('detail', 'no detail')})"


def probe_external(host: str = PUBLIC_HOST, port: int = PUBLIC_PORT,
                   local_public_ok: bool = True) -> tuple[bool, str]:
    """check-host.net from several countries, with two controls so a failure is
    classified instead of merely reported.

    Both controls must run ON THE CHECKER'S NODES, never from this PC: connecting
    from here to check-host's own hosts only proves our egress works (we already
    know it does - we just fetched their API over HTTPS). Two questions:

      1. can those nodes reach CONTROL_HOST:443 as a whole?  -> is the checker sane
      2. can those nodes reach THIS VPS on tcp/22?           -> is our IP blocked

    Decision order:
      relay connects ............................ PASS
      local public probe also failed ........... real outage (the public probe alerts;
                                                 this one stays quiet to avoid double-paging)
      control unreachable ...................... SKIP, checker infrastructure
      VPS 22 unreachable, control fine ......... SKIP, scanner-range filtering of our IP
      VPS 22 fine, 3333 dead ................... ALERT, genuine ingress fault

    The 2026-10-24 anomaly was case 4: tcpdump showed zero SYNs arriving on a fresh
    8080 listener while the public probe answered in ~230 ms.
    """
    try:
        ok, bad = check_host(host, port)
    except RuntimeError as exc:
        return False, f"skipped: {exc}"
    if ok:
        return True, f"{ok}/{ok + bad} nodes connected"

    if not local_public_ok:
        return False, (f"skipped: 0/{bad} nodes connected, but the public probe failed "
                       f"too - real outage, not an external-only problem")

    time.sleep(2)   # the checker rate-limits; space the secondary probes out
    try:
        ctrl_ok, ctrl_bad = check_host(CONTROL_HOST, CONTROL_PORT)
    except RuntimeError as exc:
        return False, f"skipped: {exc}"
    if ctrl_ok == 0:
        return False, (f"skipped: checker nodes cannot reach {CONTROL_HOST}:{CONTROL_PORT} "
                       f"either (0/{ctrl_bad}) - checker infrastructure, nothing to do with us")

    time.sleep(2)
    try:
        ssh_ok, ssh_bad = check_host(VPS, 22)
    except RuntimeError as exc:
        return False, f"skipped: {exc}"

    # To blame port 3333 specifically, a real sample of nodes must reach this IP on
    # another port. One node getting through is exactly what partial scanner
    # filtering looks like, and two separate checks are minutes apart, so a thin
    # comparison proves nothing.
    need = max(2, (ssh_ok + ssh_bad) // 2)
    if ssh_ok == 0:
        return False, (f"skipped: {ctrl_ok}/{ctrl_ok + ctrl_bad} nodes reach {CONTROL_HOST} "
                       f"but 0/{ssh_bad} reach this VPS on any port - scanner-range "
                       f"filtering upstream, not a relay fault")
    if ssh_ok < need:
        return False, (f"skipped: thin sample - only {ssh_ok}/{ssh_ok + ssh_bad} nodes reach "
                       f"tcp/22 (need {need} to blame 3333); consistent with partial "
                       f"scanner-range filtering")

    return False, (f"0/{bad} nodes connected on 3333 while {ssh_ok}/{ssh_ok + ssh_bad} reach "
                   f"tcp/22 and {ctrl_ok} reach {CONTROL_HOST} - genuine ingress fault on 3333")


def popup(title: str, text: str) -> None:
    """Visible alert with no installs. Fire and forget: a modal must not stop probing."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.MessageBox]::Show("
        f"'{text}','{title}',[System.Windows.Forms.MessageBoxButtons]::OK)"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 - an alerting failure must not kill the monitor
        pass


ALERTS = {
    "pool": ("ALERT: Backend tunnel is down", "the local pool on 127.0.0.1:3333 stopped answering"),
    "public": ("ALERT: Public relay is down", "mine.xmrjackpot.com:3333 does not return a stratum job"),
    "dns": ("ALERT: Public relay is down", "mine.xmrjackpot.com does not resolve to the relay"),
    "tunnel": ("ALERT: Backend tunnel is down", "the WireGuard/haproxy path on the VPS is unhealthy"),
    "external": ("ALERT: External relay unreachable", "no external node could open 3333"),
    "witness": ("ALERT: External relay unreachable",
                "an independent vantage could not reach the relay"),
}


def split_addr(text: str, default_port: int) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return (host, int(port)) if host else (text, default_port)


def cycle(args, state: dict, streak: dict, external_due: bool) -> bool:
    checks: dict[str, tuple[bool, str]] = {}
    # order matters: the most specific failing layer is the one that gets to alert
    checks["pool"] = probe_stratum(*split_addr(args.pool, 3333))
    checks["tunnel"] = probe_tunnel()
    checks["public"] = probe_stratum(*split_addr(args.public, PUBLIC_PORT))
    checks["dns"] = probe_dns(split_addr(args.public, PUBLIC_PORT)[0], args.expect_ip)
    if args.witness or args.witness_url or args.witness_remote:
        checks["witness"] = probe_witness(args.witness, args.witness_max_age,
                                          url=args.witness_url, remote=args.witness_remote)
    if args.external and external_due:
        checks["external"] = probe_external(*split_addr(args.public, PUBLIC_PORT),
                                            local_public_ok=checks["public"][0])

    # One root cause, one alert: a dead pool explains a dead tunnel, and a dead
    # tunnel explains a dead public probe. Only the most specific layer speaks.
    suppress: set[str] = set()
    if not checks["pool"][0]:
        suppress |= {"tunnel", "public", "witness"}
    elif not checks["tunnel"][0] and not checks["tunnel"][1].startswith("skipped"):
        suppress |= {"public"}
    if not checks["public"][0]:
        suppress |= {"witness"}

    healthy = True
    for name, (ok, detail) in checks.items():
        if detail.startswith("skipped"):
            log({"probe": name, "ok": None, "detail": detail})
            continue
        threshold = 1 if args.once else args.alert_after
        if name in suppress and not ok:
            log({"probe": name, "ok": ok, "detail": detail + " (suppressed: a more specific probe failed)"})
            state[name] = ok
            continue
        log({"probe": name, "ok": ok, "detail": detail})
        if ok:
            streak[name] = 0
            if state.get(name) is False:
                print(f"RECOVERED: {name} is answering again ({detail})")
                if not args.no_popup:
                    popup("relay recovered", f"{name}: {detail}")
            state[name] = True
            continue

        # hysteresis: one bad sample is noise (a scanner node timing out, a single
        # missed keepalive), two in a row is a fault worth waking someone for
        healthy = False
        streak[name] = streak.get(name, 0) + 1
        if state.get(name) is not False and streak[name] >= threshold:
            headline, meaning = ALERTS[name]
            print(f"{headline} [{name}] {detail} (failed {streak[name]} cycles)")
            if not args.no_popup:
                popup(headline, f"{meaning}\n\n{name}: {detail}")
            state[name] = False
        elif state.get(name) is not False:
            print(f"pending: {name} failed once ({detail}) - alerting after {threshold} consecutive",
                  flush=True)

    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({
        "healthy": healthy,
        "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": {k: {"ok": v[0], "detail": v[1]} for k, v in checks.items()},
    }, indent=2), encoding="utf-8")

    def label(entry: tuple[bool, str]) -> str:
        ok, detail = entry
        if ok:
            return "PASS"
        return "SKIP" if detail.startswith("skipped") else "FAIL"

    summary = " ".join(f"{k}={label(v)}" for k, v in checks.items())
    print(f"{'PASS ' if healthy else 'ALERT'} {summary}", flush=True)
    return healthy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one cycle, exit 1 if anything failed")
    ap.add_argument("--interval", type=int, default=60, help="seconds between cycles (default 60)")
    ap.add_argument("--external", action="store_true", help="also probe check-host nodes")
    ap.add_argument("--external-every", type=int, default=900,
                    help="seconds between external probes (default 900)")
    ap.add_argument("--pool", default="127.0.0.1:3333",
                    help="local pool target (default 127.0.0.1:3333)")
    ap.add_argument("--public", default=f"{PUBLIC_HOST}:{PUBLIC_PORT}",
                    help="public relay target (default mine.xmrjackpot.com:3333)")
    ap.add_argument("--expect-ip", default=EXPECTED_IP,
                    help=f"IP the public name must resolve to (default {EXPECTED_IP})")
    ap.add_argument("--no-popup", action="store_true",
                    help="log alerts but do not raise a message box (tests, unattended runs)")
    ap.add_argument("--alert-after", type=int, default=2,
                    help="consecutive failing cycles before an alert (default 2)")
    ap.add_argument("--witness", default=str(ROOT / ".relay" / "witness.json"),
                    help="verdict file written by relay/ingress-check.py on another network")
    ap.add_argument("--witness-max-age", type=int, default=86400,
                    help="seconds before a witness result is considered stale (default 86400)")
    ap.add_argument("--witness-url", help="HTTPS URL returning the witness JSON (Worker, paste host)")
    ap.add_argument("--witness-remote", help="user@host:/path to read the witness JSON over ssh")
    ap.add_argument("--no-witness", action="store_true", help="ignore the witness file")
    args = ap.parse_args()

    if args.no_witness:
        args.witness = None
    state: dict[str, bool | None] = {}
    streak: dict[str, int] = {}
    last_external = 0.0
    while True:
        external_due = args.external and (time.time() - last_external >= args.external_every)
        if external_due:
            last_external = time.time()
        healthy = cycle(args, state, streak, external_due)
        if args.once:
            return 0 if healthy else 1
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
