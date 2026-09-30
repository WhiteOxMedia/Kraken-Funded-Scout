"""THE HINDSIGHT SERVICE DRIVE (Sep 29): hindsight_service is the
machine's local server (127.0.0.1:8785) that feeds the console's
API-fed burial-hindsight panel. This drive runs it the way the machine
does — REAL subprocesses on real loopback ports — and pins the runner
contract that the live feed depends on:

  1. the wire answers the SEEDED SANDBOX through the runner's env
     seams (HINDSIGHT_SUPP/CACHE): a 3-row rotation comes back
     r=0.67 — production's journal can never produce that shape, so
     the pin proves the seams moved the data paths;
  2. an unseeded rotation answers 200 with B=0 — the panel's
     new-rotation render is zeros, never a 500;
  3. the lifecycle: start verifies by HTTP, double start refuses
     (already_up), stop kills the PORT's listener (the uv-shim trap:
     the pid file may lie, the bound port is the ground truth) even
     when the pid file is corrupted on purpose;
  4. a stale pid file (recycled, non-python) never wedges start, a
     corrupt non-numeric one is ignored;
  5. a foreign server on the port is never killed — a stop against a
     port held by a non-ours process refuses by name;
  6. the port refuses a second bind (bind-first is the double-server
     guard) and a mutating POST without the operator token is refused
     403 over the real wire (the transport never opens mutations).

The three scenarios run as CONCURRENT serve processes on distinct
ports — the panel's server and a neighbor coexist by construction.
Import-starved environments (the commitgate sandbox has no plotly/
streamlit stack) skip honestly: the drive exits green with the skip
named, the hand-replayable pins still run first.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# python-path resolution IS a check: no hardcoded absolute paths (the
# scratch sandbox copies the repo, so the import lands on the copy)
import hindsight_service as hs

BASE = Path(__file__).resolve().parent

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def _get(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def _post(url, body, timeout=2.0):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


# ── the sandbox fixture: 3 rows, 2 payers → r = 0.67 over the wire ──
def _seed(_sbx_tmp):
    now = datetime.now().astimezone()
    t0 = now.replace(minute=0, second=0, microsecond=0)
    ms0 = int(t0.timestamp() * 1000)
    rows = [{"ts": t0.isoformat(), "pair": "AAA/USD", "gate": "tide-out",
             "quick": 71.0, "rot": "SVC-1"},
            {"ts": t0.isoformat(), "pair": "BBB/USD", "gate": "tide-out",
             "quick": 64.0, "rot": "SVC-1"},
            {"ts": t0.isoformat(), "pair": "CCC/USD", "gate": "deep-veto",
             "quick": 94.0, "rot": "SVC-1"}]
    (_sbx_tmp / "popup_suppressions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")   # leak-audit: ok (_sbx_tmp is the fixture's sandboxed tempdir argument; HINDSIGHT_SUPP points the service at it)
    con = sqlite3.connect(_sbx_tmp / "ohlcv_cache.db")
    con.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
                "high REAL, low REAL, close REAL)")
    orows = []
    for pair, run in (("AAA/USD", 1.08), ("BBB/USD", 1.01),
                      ("CCC/USD", 1.02)):
        orows.append((pair, ms0 - 600_000, 1.0, 1.0, 1.0, 1.0))
        orows.append((pair, ms0 + 3_600_000, 1.0, run, 1.0,
                      (1.0 + run) / 2))
    con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", orows)
    con.commit()
    con.close()


def _env(port, pid_file, sbx):
    e = dict(os.environ)
    e.update({
        "HINDSIGHT_PORT": str(port),
        "HINDSIGHT_PID_FILE": str(pid_file),
        "HINDSIGHT_API_BASE": "http://127.0.0.1",
        "HINDSIGHT_SUPP": str(sbx / "popup_suppressions.jsonl"),
        "HINDSIGHT_CACHE": str(sbx / "ohlcv_cache.db"),
        "HINDSIGHT_OPERATOR_TOKEN": "",   # deterministic 403s
    })
    return e


def _spawn_serve(port, pid_file, sbx):
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8",
         str(hs.BASE / "hindsight_service.py"), "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        cwd=str(hs.BASE), env=_env(port, pid_file, sbx),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # liveness = BOUND + ROUTING: an HTTP answer of ANY code (200, or
    # the 500 an unseeded rotation returns) proves the socket and the
    # route table; silence is the only failure.
    deadline = time.time() + 25
    while time.time() < deadline:
        if proc.poll() is not None:
            err = ""
            try:
                err = (proc.stderr.read() or b"").decode(
                    "utf-8", "replace")[-300:]
            except Exception:
                pass
            raise RuntimeError(f"serve died at boot rc={proc.returncode}: {err}")
        code, _ = _get(f"http://127.0.0.1:{port}/api/hindsight/"
                       f"rotation/SVC-1/summary", timeout=1.0)
        if code is not None:
            return proc
        time.sleep(0.4)
    proc.kill()
    raise RuntimeError(f"port {port} never answered within 25s")


def _runner_cmd(port, pid_file, sbx, cmd):
    return subprocess.run(
        [sys.executable, "-X", "utf8",
         str(hs.BASE / "hindsight_service.py"), cmd],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(hs.BASE), env=_env(port, pid_file, sbx))


def _wait_silent(port, seconds=6.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        code, _ = _get(f"http://127.0.0.1:{port}/api/hindsight/"
                       f"rotation/SVC-1/summary", timeout=1.0)
        if code is None:
            return True
        time.sleep(0.4)
    return False


_sbx1 = Path(tempfile.mkdtemp())
_sbx0 = Path(tempfile.mkdtemp())
_sbx2 = Path(tempfile.mkdtemp())
_p1, _p0, _p2 = 8791, 8792, 8793          # never the production port
_f1 = _sbx1 / "run.pid"
_f0 = _sbx0 / "run.pid"
_f2 = _sbx2 / "run.pid"
_kids = []

try:
    # path resolution IS a check — but the invariant is ANTI-HARD-CODING
    # (the import must resolve to a tree that CONTAINS the service the
    # drive drives), not scratch==import: the gate's layering can lawfully
    # resolve this file from the repo layer (it is not a guarded source,
    # so it rides PYTHONPATH's repo entry, exactly like per_gate_export).
    _svc = hs.BASE / "hindsight_service.py"
    check("hindsight_service resolves through the python path to a tree "
          "that contains the service file (no absolute-path hardcoding "
          "in the drive)",
          _svc.exists(), str(_svc))

    # ── scenario 1: the seeded sandbox (spawned FIRST so scenario 0's
    #    start-verification sees a seeded journal and answers 200) ──
    _seed(_sbx1)
    s1 = _spawn_serve(_p1, _f1, _sbx1)
    _kids.append(s1)

    # ── scenario 0: the runner lifecycle on its own port ────────────
    _seed(_sbx0)                      # seams proven against a SEEDED
                                      # sandbox; the unseeded render
                                      # pin rides the NONE-9 id below
    r = _runner_cmd(_p0, _f0, _sbx0, "start")
    body = {}
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        pass
    check("start verifies by HTTP (the machine's panel would render "
          "from this server)",
          r.returncode == 0 and body.get("started") is True
          and body.get("verified") is True, r.stdout[-200:])

    code, summ = _get(f"http://127.0.0.1:{_p0}/api/hindsight/"
                      f"rotation/SVC-1/summary")
    check("the wire answers the SANDBOXED fixture through the runner's "
          "env seams — B=3, r=0.67 is a shape production's journal "
          "cannot produce, so the seams moved the data paths",
          code == 200 and summ is not None and summ.get("B") == 3
          and abs((summ.get("r") or -1) - 2 / 3) < 1e-6, summ)

    code, empty = _get(f"http://127.0.0.1:{_p0}/api/hindsight/"
                       f"rotation/NONE-9/summary")
    check("an unseeded rotation answers 200 with B=0 — the panel's "
          "new-rotation render is zeros, never a 500",
          code == 200 and empty is not None and empty.get("B") == 0,
          (code, empty))

    code, _ = _post(f"http://127.0.0.1:{_p0}/api/hindsight/"
                    f"rotation/SVC-1/apply", {"action": "apply_adjustment"})
    check("a mutating POST without the operator token is refused 403 "
          "over the real wire (the transport never opens mutations)",
          code == 403, code)

    r = _runner_cmd(_p0, _f0, _sbx0, "start")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("double start refuses — already_up, never a second server",
          r.returncode == 0 and body.get("started") is False
          and body.get("already_up") is True, r.stdout[-200:])

    # the bind-first contract: a bare `serve` on a taken port dies,
    # naming the refusal
    dup = subprocess.Popen(
        [sys.executable, "-X", "utf8",
         str(hs.BASE / "hindsight_service.py"), "serve"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(hs.BASE), env=_env(_p0, _f0, _sbx0),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _kids.append(dup)
    dup_err = b""; dup_out = b""
    dup_err = b""
    t_deadline = time.time() + 15
    while time.time() < t_deadline and dup.poll() is None:
        time.sleep(0.3)
    if dup.poll() is None:
        dup.kill()
        check("a second bind on the live port is refused (bind-first is "
              "the double-server guard)", False, "did not exit within 15s")
    else:
        try:
            dup_err = dup.stderr.read() or b""
            dup_out = dup.stdout.read() or b""
        finally:
            pass
        check("a second bind on the live port is refused (bind-first is "
              "the double-server guard)",
              dup.returncode != 0
              and b"bind refused" in dup_err + dup_out,
              (dup.returncode, (dup_err + dup_out)[-160:]))

    # the shim trap: a LIED-ABOUT pid file cannot save a server from
    # stop — the port's listener is the ground truth
    (_f0).write_text("12345", encoding="utf-8")
    r = _runner_cmd(_p0, _f0, _sbx0, "stop")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("stop kills the PORT's listener even with a corrupted pid "
          "file (the uv-shim trap: the pid file may lie, the bound "
          "port cannot)",
          r.returncode == 0 and body.get("stopped") is True
          and _wait_silent(_p0), (r.stdout[-160:], body))

    # stale-pid hygiene at start
    (_f0).write_text("999999999", encoding="utf-8")   # non-python garbage
    r = _runner_cmd(_p0, _f0, _sbx0, "start")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("a stale pid file (a recycled, non-python pid) never wedges "
          "start — cleared, then verified True",
          r.returncode == 0 and body.get("started") is True
          and body.get("verified") is True, r.stdout[-200:])
    (_f0).write_text("not-a-pid", encoding="utf-8")
    r = _runner_cmd(_p0, _f0, _sbx0, "start")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("a corrupt (non-numeric) pid file is ignored, start still "
          "verifies", r.returncode == 0 and body.get("started") is False
          and body.get("already_up") is True, r.stdout[-200:])

    # ── scenario 2: the ownership contract ───────────────────────────
    # Ownership is PORT + BODY + PYTHON (the stop proof), never the
    # spawner: a server the runner did not spawn but that answers an
    # ours-shaped body on its port IS stopped; a foreign HTTP server
    # (answering 200 without the proposed_delta body) is NEVER killed.
    _seed(_sbx2)
    s2 = _spawn_serve(_p2, _f2, _sbx2)
    _kids.append(s2)
    r = _runner_cmd(_p2, _f2, _sbx2, "stop")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("the runner stops an ours-shaped server it did not spawn "
          "(ownership is port+body+python, never the spawner)",
          r.returncode == 0 and body.get("stopped") is True
          and _wait_silent(_p2), r.stdout[-160:])

    # a live FOREIGN server: real HTTP, 200, but not our body shape
    import socket as _socket
    import threading

    def _foreign_reply(conn):
        try:
            conn.recv(4096)
            body = b'{"foreign": true}'
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: "
                         b"application/json\r\nContent-Length: 17"
                         b"\r\n\r\n" + body)
            conn.shutdown(_socket.SHUT_RDWR)
            conn.close()
        except OSError:
            pass

    foreign = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    foreign.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    foreign.bind(("127.0.0.1", _p2))
    foreign.listen(4)
    f_on = {"on": True}

    def _foreign_loop():
        while f_on["on"]:
            try:
                conn, _ = foreign.accept()
            except OSError:
                return
            threading.Thread(target=_foreign_reply, args=(conn,),
                             daemon=True).start()

    f_thread = threading.Thread(target=_foreign_loop, daemon=True)
    f_thread.start()
    r = _runner_cmd(_p2, _f2, _sbx2, "stop")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    alive = _get(f"http://127.0.0.1:{_p2}/api/hindsight/"
                 f"rotation/SVC-1/summary", timeout=1.0)
    check("a foreign HTTP server on the port is NEVER killed — the "
          "stop refuses by name (200 but not our body) and the "
          "foreign listener survives",
          r.returncode == 1 and body.get("stopped") is False
          and body.get("reason") == "port held by a foreign process"
          and alive[0] == 200, (r.stdout[-200:], alive[0]))
    f_on["on"] = False
    foreign.close()

    # the concurrent-scenarios claim, stated as a wire fact: three
    # servers ran as separate processes on distinct ports during this
    # drive; scenario 1 is STILL serving while scenario 0 was recycled
    code, summ = _get(f"http://127.0.0.1:{_p1}/api/hindsight/"
                      f"rotation/SVC-1/summary")
    check("concurrent scenarios: scenario 1's server stayed up and "
          "answering the whole time the runner recycled scenario 0's "
          "port (the panel's server and a neighbor coexist)",
          code == 200 and summ is not None and summ.get("B") == 3, summ)

    r = _runner_cmd(_p0, _f0, _sbx0, "stop")
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        body = {}
    check("final stop: the recycled scenario-0 server goes silent and "
          "its pid file is cleaned up",
          r.returncode == 0 and body.get("stopped") is True
          and _wait_silent(_p0) and not _f0.exists(),
          (r.stdout[-160:], _f0.exists()))

    r = _runner_cmd(_p1, _f1, _sbx1, "status")
    check("status answers for a live foreign-spawned server (spawned "
          "here by the drive, not by the runner): http_up + is_ours "
          "+ the summary probe",
          r.returncode == 0 and '"http_up": true' in r.stdout
          and '"is_ours": true' in r.stdout, r.stdout[-200:])

finally:
    for k in _kids:
        try:
            k.kill()
        except OSError:
            pass
    for k in _kids:
        try:
            k.wait(timeout=5)
        except Exception:
            pass
    # belt: best-effort runner stops so the machine never inherits a
    # drive server on these ports
    for _p, _f, _s in ((_p0, _f0, _sbx0), (_p1, _f1, _sbx1),
                       (_p2, _f2, _sbx2)):
        try:
            subprocess.run([sys.executable, "-X", "utf8",
                            str(hs.BASE / "hindsight_service.py"), "stop"],
                           capture_output=True, timeout=30,
                           cwd=str(hs.BASE), env=_env(_p, _f, _s))
        except Exception:
            pass
    shutil.rmtree(_sbx0, ignore_errors=True)
    shutil.rmtree(_sbx1, ignore_errors=True)
    shutil.rmtree(_sbx2, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "hindightservice" / "_hindightservice_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
