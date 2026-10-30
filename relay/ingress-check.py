#!/usr/bin/env python3
"""ingress-check.py - prove the relay is reachable FROM WHERE YOU ARE.

Runs anywhere Python 3 runs: a laptop, a phone in Termux, a friend's server, a
hotspot. No installs, no key, no admin. It answers one question: can this network
open mine.xmrjackpot.com:3333 and get a stratum job?

    python3 ingress-check.py                      # check the relay
    python3 ingress-check.py --json out.json      # also write the witness file
    python3 ingress-check.py --witness .relay/witness.json   # push to the pool PC

Exit codes: 0 PASS, 1 FAIL, 2 control network itself is broken (so the result says
nothing about the relay).

The control (1.1.1.1:443) is what makes a FAIL meaningful: if this network cannot
reach Cloudflare either, the witness is useless and says so instead of blaming the
relay. Output is deliberately small - verdict, timings, and the egress IP - so it
can be read over a phone or pasted into chat.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request

HOST = "mine.xmrjackpot.com"
PORT = 3333
CONTROL = ("1.1.1.1", 443)
LOGIN = (
    b'{"id":1,"jsonrpc":"2.0","method":"login",'
    b'"params":{"login":"witness","pass":"x","agent":"ingress-witness/1"}}\n'
)


def tcp(host: str, port: int, timeout: float) -> tuple[bool, float]:
    started = time.time()
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True, (time.time() - started) * 1000
    except OSError:
        return False, (time.time() - started) * 1000
    finally:
        s.close()


def stratum(host: str, port: int, timeout: float) -> tuple[bool, str]:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.sendall(LOGIN)
        raw = b""
        deadline = time.time() + timeout
        while time.time() < deadline and b"\n" not in raw and len(raw) < 8192:
            part = s.recv(4096)
            if not part:
                break
            raw += part
        if not raw:
            return False, "connected, no reply"
        reply = json.loads(raw.split(b"\n")[0])
        job = (reply.get("result") or {}).get("job")
        return (True, f"job {job.get('job_id', '?')}") if job else (False, "reply had no job")
    except Exception as exc:  # noqa: BLE001 - a witness must never crash
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        s.close()


def egress(timeout: float = 8.0) -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read(64).decode(errors="replace").strip()
        except Exception:  # noqa: BLE001
            continue
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", default=HOST)
    ap.add_argument("port", nargs="?", type=int, default=PORT)
    ap.add_argument("--timeout", type=float, default=6.0)
    ap.add_argument("--json", dest="json_out", help="write the witness JSON here")
    ap.add_argument("--witness", help="write the witness JSON straight into the monitor's file")
    args = ap.parse_args()

    control_ok, _ = tcp(*CONTROL, timeout=args.timeout)
    tcp_ok, connect_ms = tcp(args.host, args.port, timeout=args.timeout)
    stratum_ok, detail = stratum(args.host, args.port, timeout=args.timeout) if tcp_ok else (False, "no tcp")
    try:
        resolved = sorted({i[4][0] for i in socket.getaddrinfo(args.host, None, socket.AF_INET)})
    except Exception:  # noqa: BLE001
        resolved = []

    verdict = "PASS" if (control_ok and stratum_ok) else "FAIL"
    if not control_ok:
        verdict = "INCONCLUSIVE"

    print(f"network egress : {egress()}")
    print(f"control        : {'PASS' if control_ok else 'FAIL'}  {CONTROL[0]}:{CONTROL[1]}")
    print(f"dns            : {', '.join(resolved) or 'did not resolve'}")
    print(f"tcp {args.port}       : {'PASS' if tcp_ok else 'FAIL'}  {connect_ms:.0f} ms")
    print(f"stratum        : {'PASS' if stratum_ok else 'FAIL'}  {detail}")
    print(f"VERDICT        : {verdict}")

    record = {
        "verdict": verdict,
        "host": args.host,
        "port": args.port,
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "when_epoch": int(time.time()),
        "control": control_ok,
        "dns": resolved,
        "tcp": tcp_ok,
        "stratum": stratum_ok,
        "detail": detail,
        "egress": egress() if not control_ok else None,
    }
    blob = json.dumps(record, indent=2)
    for path in filter(None, (args.json_out, args.witness)):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
        print(f"witness written: {path}")

    return 0 if verdict == "PASS" else (2 if verdict == "INCONCLUSIVE" else 1)


if __name__ == "__main__":
    sys.exit(main())
