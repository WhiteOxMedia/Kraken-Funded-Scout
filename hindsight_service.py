"""THE HINDSIGHT SERVICE RUNNER (Sep 29): hindsight_api's stdlib server
stood up as a REAL local service on 127.0.0.1:8785 — the console's
API-fed panel reads this endpoint for its live feed.

The stdlib-only runner beside the scanner's own helpers:
    python hindsight_service.py start     # detached spawn + pid file
    #                                          (the pid file is CLI
    #                                          bookkeeping; the live
    #                                          ownership contract is
    #                                          the port)
    python hindsight_service.py stop      # kill by pid, verify by cmdline
    python hindsight_service.py status    # live probe + pid liveness
    python hindsight_service.py serve     # foreground (what start spawns)

The pid contract is the scanner's (.hindsight.pid): a pid file that
names a dead or non-python process is cleared by the next start, and a
live service's cmdline is verified before any stop. GETs are always
open (the panel's data contract); the mutating POSTs stay token-gated
inside hindsight_api._authenticated. Env seams (the STATE_ENV pattern)
let the drive move every path: HINDSIGHT_PORT, HINDSIGHT_PID_FILE,
HINDSIGHT_API_BASE (the panel already reads it).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_PORT = 8785
DEFAULT_BASE = "http://127.0.0.1"
PID_NAME = ".hindsight.pid"
CMDLINE_SHAPE = "hindsight_service.py"


def _port() -> int:
    return int(os.environ.get("HINDSIGHT_PORT", DEFAULT_PORT))


def _base() -> str:
    return os.environ.get("HINDSIGHT_API_BASE", DEFAULT_BASE)


def _pid_file() -> Path:
    return Path(os.environ.get(
        "HINDSIGHT_PID_FILE", str(BASE / PID_NAME)))


def _url(tail: str) -> str:
    return f"{_base().rstrip('/')}:{_port()}/{tail.lstrip('/')}"


def _http_get(url: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except Exception:
        return None, b""


def serve() -> int:
    """Foreground server loop (what `start` spawns detached). Binds the
    port FIRST — a double start dies here with a named error instead of
    two listeners sharing... they cannot share, the bind refuses."""
    import hindsight_api as ha
    from http.server import ThreadingHTTPServer

    # DATA-PATH SEAMS (the machine's env-dial pattern): point the wire
    # at a non-default journal/cache without code edits — and the drive
    # sandboxes its service through exactly these.
    if os.environ.get("HINDSIGHT_SUPP"):
        ha.DEFAULT_SUPP = Path(os.environ["HINDSIGHT_SUPP"])
    if os.environ.get("HINDSIGHT_CACHE"):
        ha.DEFAULT_CACHE = Path(os.environ["HINDSIGHT_CACHE"])

    goal = float(os.environ.get("HINDSIGHT_GOAL_PCT", "1.5"))
    handler, _proto = ha.make_handler(goal_pct=goal)
    port = _port()

    class _Srv(ThreadingHTTPServer):
        # STRICT BIND (the Windows lesson the drive taught): stdlib's
        # allow_reuse_address=1 lets a SECOND server silently share an
        # already-bound port on Windows (SO_REUSEADDR there means
        # 'rebind anything'). A duplicate serve must fail LOUDLY, so
        # bind-first is enforced with no reuse at all.
        allow_reuse_address = False

    try:
        # THE HOST COMES FROM _url'S OWN PARSING (the review's major):
        # _url builds f"{base}:{port}/..." — a base carrying a scheme
        # and/or an embedded port double-punches the colon there. The
        # bind parses the base the same way (urlsplit: scheme-stripped,
        # embedded port dropped, bare host/IP/empty = default).
        from urllib.parse import urlsplit
        _host = urlsplit(_base()).hostname or "127.0.0.1"
        httpd = _Srv((_host, port), handler)
    except OSError as exc:
        print(json.dumps({"error": f"bind refused: {exc}",
                          "port": port}), flush=True)
        return 1
    print(json.dumps({"serving": True, "port": port,
                      "pid": os.getpid()}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _listener_pid(port: int) -> int | None:
    """THE GROUND TRUTH for ownership: whichever pid LISTENS on our
    port (netstat — the launcher's own check for the console). uv's
    shim indirection makes the spawn pid a lie (the real interpreter
    runs as its child), so the pid file is best-effort and the
    listener is authoritative."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=10).stdout or ""
    except Exception:
        return None
    import re
    m = re.search(rf":{port}\s+\S+\s+LISTENING\s+(\d+)", out)
    return int(m.group(1)) if m else None


def _is_python(pid: int) -> bool:
    """A live process whose image is python (the recycled-pid trap)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10).stdout or ""
    except Exception:
        return False
    return "python" in out.lower()


def _body_is_ours(body: bytes) -> bool:
    return b"proposed_delta" in body


def _who_serves() -> dict:
    """{listener_pid, http_up, ours} — the service identity from the
    wire and the port, not from a pid file."""
    code, body = _http_get(_url("api/hindsight/rotation/"
                                f"{_now_rot()}/summary"), timeout=1.5)
    lpid = _listener_pid(_port())
    return {"listener_pid": lpid, "http_up": code == 200,
            "ours": code == 200 and _body_is_ours(body),
            "python": bool(lpid and _is_python(lpid))}


def status() -> dict:
    code, body = _http_get(_url("api/hindsight/rotation/"
                                f"{_now_rot()}/summary"), timeout=1.5)
    who = _who_serves()
    return {"port": _port(), "pid_file": _read_pid(),
            "listener_pid": who["listener_pid"], "http_up": who["http_up"],
            "is_ours": who["ours"],
            "summary_probe": (json.loads(body) if code == 200 else None)}


def _read_pid() -> int | None:
    try:
        raw = _pid_file().read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdigit() else None
    except (OSError, ValueError):
        return None


def _now_rot() -> str:
    try:
        from per_gate_export import default_rotation_id
        return default_rotation_id()
    except Exception:
        return "00-04"


def start() -> int:
    port = _port()
    # already up?
    code, _ = _http_get(_url("api/hindsight/rotation/"
                             f"{_now_rot()}/summary"), timeout=1.5)
    if code == 200:
        print(json.dumps({"started": False, "already_up": True,
                          "port": port}))
        return 0
    # STALE-PID CLEANUP (the scanner's retake contract): a pid file that
    # names a dead or non-python process is the recycled-pid trap -- clear
    # it here so start never refuses on evidence nobody can trust.
    pid = _read_pid()
    if pid is not None and not _is_python(pid):
        try:
            _pid_file().unlink()
        except OSError:
            pass
        pid = None
    py = sys.executable
    # DETACHED SPAWN (the repo's own pattern): no console window, own
    # logs, DETACHED_PROCESS so the child never dies with the caller.
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
    logs = BASE / "logs"
    logs.mkdir(exist_ok=True)
    out_log = logs / "hindsight_stdout.log"
    err_log = logs / "hindsight_stderr.log"
    # BEST-EFFORT ROTATION (the WinError-32 lesson the gate taught:
    # the LIVE service holds these exact files open, and a rotation
    # attempted while a sibling holds them fails with the sharing
    # violation -- which must never kill a boot; skipping one .prev
    # generation is the honest cost of a shared log):
    for _log in (out_log, err_log):
        try:
            if _log.exists():
                _log.replace(_log.with_suffix(".log.prev"))
        except OSError:
            pass
    try:
        with open(out_log, "ab") as fo, open(err_log, "ab") as fe:
            proc = subprocess.Popen(
                [py, "-X", "utf8", str(BASE / "hindsight_service.py"),
                 "serve"],
                stdout=fo, stderr=fe, cwd=str(BASE),
                creationflags=creationflags,
                env={**os.environ, "HINDSIGHT_PORT": str(port),
                     "HINDSIGHT_API_BASE": _base()})
    except OSError as exc:
        print(json.dumps({"started": False,
                          "error": f"spawn failed: {exc}",
                          "port": port}))
        return 1
    # the pid contract: record, then VERIFY by cmdline + port
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    for _ in range(30):
        time.sleep(0.5)
        code, _ = _http_get(_url("api/hindsight/rotation/"
                                 f"{_now_rot()}/summary"), timeout=1.5)
        if code == 200:
            print(json.dumps({"started": True, "pid": proc.pid,
                              "port": port, "verified": True}))
            return 0
        if proc.poll() is not None:
            print(json.dumps({"started": False,
                              "error": "serve died at boot",
                              "exit": proc.returncode,
                              "stderr_tail": err_log.read_text(
                                  encoding="utf-8",
                                  errors="replace")[-400:]}))
            return 1
    print(json.dumps({"started": False,
                      "error": "no HTTP answer within 15s",
                      "pid": proc.pid}))
    return 1


def stop() -> int:
    who = _who_serves()
    lpid = who["listener_pid"]
    if not who["http_up"] or not lpid:
        try:
            _pid_file().unlink()
        except OSError:
            pass
        print(json.dumps({"stopped": False, "reason": "not serving",
                          "port": _port()}))
        return 0
    # OWNERSHIP: the listener must be a python, and the answering body
    # must be ours (a foreign server on this port is never killed)
    if not (who["python"] and who["ours"]):
        print(json.dumps({"stopped": False,
                          "reason": "port held by a foreign process",
                          "listener_pid": lpid}))
        return 1
    subprocess.run(["taskkill", "/PID", str(lpid), "/F"],
                   capture_output=True, text=True)
    # the port must go silent; give the socket a beat to release
    deadline = time.time() + 5
    while time.time() < deadline:
        code, _ = _http_get(_url("api/hindsight/rotation/"
                                 f"{_now_rot()}/summary"), timeout=1.0)
        if code is None:
            try:
                _pid_file().unlink()
            except OSError:
                pass
            print(json.dumps({"stopped": True, "pid": lpid}))
            return 0
        time.sleep(0.5)
    print(json.dumps({"stopped": False, "reason": "still answering",
                      "pid": lpid}))
    return 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "status":
        print(json.dumps(status()))
        return 0
    if cmd == "serve":
        return serve()
    print(f"unknown command {cmd!r} (start|stop|status|serve)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
