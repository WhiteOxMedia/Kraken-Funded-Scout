#!/usr/bin/env python3
"""relay-backend-status.py - one line about haproxy's stratum backend.

Runs ON THE VPS (deployed to /usr/local/bin/relay-backend-status.py) and talks to
haproxy's runtime socket, because only haproxy knows whether its backend check is
passing. The monitor cannot infer that from the outside: a DOWN backend and a
blocked firewall look identical from the internet, and "haproxy is active" says
nothing about whether it can reach the pool.

    p2pool/pool-pc status=UP check=OK lastchg=12s weight=1/1

Exit codes: 0 backend UP, 1 backend DOWN (or missing), 2 socket unreadable.
Read-only: it issues `show stat` and nothing else.
"""

from __future__ import annotations

import socket
import sys
import time

SOCKET = "/run/haproxy/admin.sock"
BACKEND = "p2pool"


def main() -> int:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(SOCKET)
    except OSError as exc:
        print(f"socket {SOCKET} unreadable: {exc}")
        return 2

    with s:
        s.sendall(b"show stat\n")
        chunks = []
        while True:
            part = s.recv(65536)
            if not part:
                break
            chunks.append(part)

    rows = [ln for ln in b"".join(chunks).decode(errors="replace").splitlines() if ln]
    if not rows:
        print("runtime socket returned nothing (stats socket missing from haproxy.cfg?)")
        return 2

    header = rows[0].lstrip("# ").split(",")
    index = {name: i for i, name in enumerate(header)}
    for line in rows[1:]:
        fields = line.split(",")
        if len(fields) < len(header) or fields[index["pxname"]] != BACKEND:
            continue
        if fields[index["svname"]] != "FRONTEND":
            status = fields[index["status"]]
            check = fields[index["check_status"]] or "-"
            changed = fields[index["lastchg"]] or "?"
            print(f"{fields[index['pxname']]}/{fields[index['svname']]} "
                  f"status={status} check={check or '-'} lastchg={changed}s")
            return 0 if status == "UP" else 1

    print(f"backend {BACKEND} not present in haproxy stats")
    return 1


if __name__ == "__main__":
    sys.exit(main())
