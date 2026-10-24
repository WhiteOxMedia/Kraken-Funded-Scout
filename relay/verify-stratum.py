#!/usr/bin/env python3
"""verify-stratum.py - prove a stratum endpoint actually answers, as a miner would.

Sends one p2pool/xmrig-style `login` and insists on a JSON reply carrying a job
or a status, so a relay that merely accepts the TCP connection (or a Cloudflare
error page that speaks HTTP on the port) fails instead of looking healthy.

    python verify-stratum.py 203.0.113.10 3333          # relay IP, before DNS moves
    python verify-stratum.py mine.xmrjackpot.com 3333   # after the cutover
    python verify-stratum.py 127.0.0.1 3333 --timeout 4 # the pool itself
    python verify-stratum.py 203.0.113.10 3333 --external  # prove inbound from the internet

Exit code 0 = PASS, 1 = FAIL. Safe to run repeatedly: the probe never hashes.

--external asks check-host.net to open a TCP connection from nodes in several
countries. The local probe cannot do that job: run it from the pool PC and the
packet goes out over WireGuard and comes back through haproxy, so it proves the
tunnel works while saying nothing about whether the VPS is reachable from the
internet (a provider firewall can still drop 3333). check-host proves inbound
reachability, the local probe proves stratum answers; a cutover needs both.
"""
import argparse
import json
import socket
import sys
import time
import urllib.parse
import urllib.request

LOGIN = {
    "id": 1,
    "jsonrpc": "2.0",
    "method": "login",
    "params": {"login": "relay-check", "pass": "x", "agent": "relay-check/1.0"},
}


def probe(host: str, port: int, timeout: float) -> tuple[bool, str]:
    started = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return False, f"cannot connect to {host}:{port} - {type(exc).__name__}: {exc}"

    try:
        sock.sendall((json.dumps(LOGIN) + "\n").encode())
        sock.settimeout(timeout)
        buf = b""
        while b"\n" not in buf and len(buf) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError as exc:
        return False, f"connected but no stratum reply - {type(exc).__name__}: {exc}"
    finally:
        sock.close()

    elapsed_ms = int((time.time() - started) * 1000)
    if not buf:
        return False, f"connected but the server closed without replying ({elapsed_ms} ms)"

    line = buf.split(b"\n", 1)[0].decode("utf-8", "replace")
    try:
        reply = json.loads(line)
    except ValueError:
        head = line[:120].replace("\n", " ")
        return False, f"reply is not JSON - looks like HTTP or garbage: {head!r}"

    if not isinstance(reply, dict):
        return False, f"reply is not a JSON object: {line[:120]!r}"
    if reply.get("error") is not None:
        return False, f"server returned an error: {reply['error']!r}"

    result = reply.get("result")
    if not isinstance(result, dict):
        return False, f"reply has no result object: {line[:120]!r}"

    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    status = result.get("status")
    if not job and status is None:
        return False, f"result carries neither a job nor a status: {line[:120]!r}"

    detail = f"status={status!r}"
    if job:
        detail += f" job_id={job.get('job_id')!r} blob_bytes={len(str(job.get('blob', '')))}"
    return True, f"stratum answered in {elapsed_ms} ms ({detail})"


CHECKHOST = "https://check-host.net"


def _checkhost_json(path: str) -> dict:
    request = urllib.request.Request(
        CHECKHOST + path, headers={"Accept": "application/json", "User-Agent": "relay-check/1.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def probe_external(host: str, port: int, nodes: int = 4, wait: float = 20.0) -> tuple[bool, str]:
    """Ask check-host.net nodes to open the socket, then report what each one saw."""
    target = urllib.parse.quote(f"{host}:{port}", safe=":")
    try:
        request = _checkhost_json(f"/check-tcp?host={target}&max_nodes={nodes}")
    except OSError as exc:
        return False, f"cannot reach check-host.net - {type(exc).__name__}: {exc}"

    request_id = request.get("request_id")
    if not request_id:
        return False, f"check-host rejected the request: {request!r}"

    deadline = time.time() + wait
    results: dict = {}
    while time.time() < deadline:
        time.sleep(3)
        try:
            results = _checkhost_json(f"/check-result/{request_id}")
        except OSError:
            continue
        if results and all(value is not None for value in results.values()):
            break

    if not results:
        return False, f"no answer from check-host in {wait:.0f}s (report {request_id})"

    opened, failed, skipped = [], [], []
    for node, value in sorted(results.items()):
        name = node.split(".")[0]
        first = value[0] if isinstance(value, list) and value else value
        if isinstance(first, dict) and "time" in first:
            opened.append(f"{name}={first['time']:.2f}s")
        elif isinstance(first, dict) and "error" in first:
            # A node that cannot resolve the name tells us nothing about the port.
            if "unknown host" in str(first["error"]).lower():
                skipped.append(name)
            else:
                failed.append(f"{name}={first['error']}")
        else:
            failed.append(f"{name}={first!r}")

    checked = len(opened) + len(failed)
    report = f"{len(opened)}/{checked} external nodes connected"
    if opened:
        report += " (" + ", ".join(opened) + ")"
    if failed:
        report += " | failed: " + ", ".join(failed)
    if skipped:
        report += " | skipped: " + ", ".join(skipped)
    report += f"  [report {request_id}]"
    return bool(opened) and not failed, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that a stratum port answers a login.")
    parser.add_argument("host")
    parser.add_argument("port", type=int, nargs="?", default=3333)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--external",
        action="store_true",
        help="also have check-host.net nodes in other countries open the port",
    )
    args = parser.parse_args()

    ok, message = probe(host=args.host, port=args.port, timeout=args.timeout)
    print(("PASS  " if ok else "FAIL  ") + message)

    if args.external:
        reachable, report = probe_external(args.host, args.port)
        print(("PASS  " if reachable else "FAIL  ") + "inbound: " + report)
        ok = ok and reachable

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
