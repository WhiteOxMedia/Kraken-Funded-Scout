"""test_deploy_check.py — THE STALENESS VERDICT, PINNED (Oct 22).

CHARTER: deploy_check.py answers one question in one command — is the
running process the commit in this checkout? — and its EXIT CODE is the
answer (0 current, 1 stale, 2 cannot tell). A verdict script is only
worth having if its verdicts are pinned, so this drive stands up ONE
stub origin, re-points its state per case, and drives the real checker
against it.

  1. SAME COMMIT: exit 0, "UP TO DATE". A dirty worktree is reported but
     stays exit 0 unless --strict, which flips it to 1.
  2. BEHIND: HEAD~1 -> exit 1 "STALE", and the cause names the restart.
  3. UNKNOWN COMMIT: a hash git has never seen -> exit 1 "MISMATCH",
     because that is a different tree, not a behind tree.
  4. NO ENDPOINT: pre-/version code answers with the SPA HTML -> exit 2
     "UNKNOWN", naming that exact cause; a WAF 403 says "refused", not
     "missing", and the checker sends a non-stock User-Agent.
  5. UNREACHABLE: a dropped connection -> exit 2, and NO asset probes
     (a dead host must cost one timeout, not seventeen).
  6. READ-ONLY: every request the checker makes is a GET; no write or
     kill call appears in it.
  7. ASSET BYTES: a served file that differs from the checkout is its
     own failure — truncated, edited in place, 404, and --no-assets.

COST MODEL (why the shape matters): every subprocess is ~0.35s of
interpreter startup plus a full round of asset fetches, so the matrix
runs IN-PROCESS against the one server and a single real subprocess
proves the CLI wiring. That took this drive from ~44s to ~2s.
"""
import contextlib
import io
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "deploy_check.py"
sys.path.insert(0, str(HERE))
import deploy_check  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", str(HERE), *args],
                             capture_output=True, text=True, timeout=20,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


HEAD = _git("rev-parse", "HEAD")
BEHIND = _git("rev-parse", "HEAD~1")
BRANCH = _git("rev-parse", "--abbrev-ref", "HEAD") or "main"


class StubOrigin:
    """ONE origin for the whole drive. Cases re-point its state instead
    of standing up a server per check: a new ThreadingHTTPServer per case
    would multiply the very cost this fixture exists to avoid."""

    def __init__(self, drop: bool = False):
        self.mode = "json"            # json | html | html403 | blocked
        self.payload = {}
        self.override = {}            # asset name -> bytes
        self.missing = set()          # asset names that 404
        self.static_hits = 0
        self.last_ua = None
        self._drop = drop
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def reset(self, payload=None):
        self.mode = "json"
        self.override = {}
        self.missing = set()
        self.payload = payload if payload is not None else payload_for(HEAD)

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if origin._drop:
                    # accepted then dropped, no bytes: urllib raises
                    # RemoteDisconnected, which the checker must read as
                    # "unreachable" — not as a missing route
                    self.close_connection = True
                    try:
                        self.connection.close()
                    except OSError:
                        pass
                    return
                origin.last_ua = self.headers.get("User-Agent")
                if origin.mode == "blocked":
                    self.send_response(403)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>blocked</html>")
                    return
                if origin.mode == "html403" and self.path.startswith("/api/"):
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail":"forbidden"}')
                    return
                if self.path.startswith("/static/"):
                    origin.static_hits += 1
                    name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
                    if name in origin.missing:
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"detail":"not found"}')
                        return
                    body = origin.override.get(name)
                    if body is None:
                        try:
                            body = (HERE / "pool" / "static" / name).read_bytes()
                        except OSError:
                            self.send_response(404)
                            self.end_headers()
                            return
                    ct = "text/css" if name.endswith(".css") else "application/javascript"
                elif origin.mode == "html":
                    # the pre-/version app: EVERY non-static path lands on
                    # the SPA fallback, /api/version included
                    body, ct = b"<html><body>the dashboard</body></html>", "text/html; charset=utf-8"
                elif origin.mode == "html403" and self.path.startswith("/version"):
                    body, ct = b"<html><body>the dashboard</body></html>", "text/html; charset=utf-8"
                else:
                    body = json.dumps(origin.payload).encode()
                    ct = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.end_headers()
                self.wfile.write(body)

        return Handler


def payload_for(commit, branch=None, started=None):
    return {"commit": commit,
            # a git-less build reports null/empty here too
            "commit_short": commit[:12] if commit else None,
            "branch": branch or BRANCH,
            "commit_time": "2026-10-21T09:28:22-06:00", "describe": "stub",
            "dirty": False, "source": "git",
            "started_at": started if started is not None else time.time(),
            "started_at_iso": "2026-10-22T00:00:00Z",
            "uptime_seconds": 12.5, "pid": 4242}


def run(*extra, url=None):
    """Check the stub IN THIS PROCESS — no interpreter startup, which is
    what made this drive expensive. Returns (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = deploy_check.main(["--url", url or ORIGIN.url, *extra])
    return code, buf.getvalue()


def run_cli(*extra):
    """The one real subprocess: proves argv -> main -> sys.exit wiring."""
    p = subprocess.run([sys.executable, str(SCRIPT), "--url", ORIGIN.url, *extra],
                       capture_output=True, text=True, timeout=90, cwd=str(HERE))
    return p.returncode, p.stdout, p.stderr


ORIGIN = StubOrigin()
ORIGIN.reset(payload_for(HEAD))

print("== 1. SAME COMMIT ==")
code, out, err = run_cli()
check(1.1, HEAD and code == 0, f"CLI exit {code} for HEAD; stderr={err[:200]}")
check(1.2, "UP TO DATE" in out, f"status line missing from CLI output: {out[:300]}")
check(1.3, HEAD[:12] in out, "the live commit is not printed")

code_json, out_json = run("--json")
doc = json.loads(out_json) if out_json.strip().startswith("{") else {}
check(1.4, set(("repo", "live", "status", "cause", "next_step", "exit", "assets")) <= set(doc),
      f"--json shape: {sorted(doc)}")
check(1.5, (doc.get("live") or {}).get("commit") == HEAD, "--json live commit mismatch")

# the worktree here is dirty (untracked drives); that must NOT read as stale
# unless the caller asks for it
code_strict, _ = run("--strict")
is_dirty = bool(_git("status", "--porcelain"))
check(1.6, (code_strict == 1) if is_dirty else (code_strict == 0),
      f"--strict exit {code_strict} with dirty={is_dirty}")
check(1.7, ("dirty" in out.lower()) or not is_dirty,
      "a dirty worktree was not surfaced in the human output")

print("== 2. BEHIND ==")
ORIGIN.reset(payload_for(BEHIND or HEAD))
code, out = run()
if not BEHIND:
    check(2.1, code == 2, "no HEAD~1 available; expected UNKNOWN")
else:
    check(2.1, code == 1, f"exit {code} for a behind process")
    check(2.2, "STALE" in out and "behind" in out.lower(), f"out: {out[:300]}")
    check(2.3, "restart" in out.lower(), "the cause/next step does not name a restart")

print("== 3. UNKNOWN COMMIT ==")
ORIGIN.reset(payload_for("0" * 40))
code, out = run()
check(3.1, code == 1, f"exit {code} for an unknown commit")
check(3.2, "MISMATCH" in out and "not in this checkout" in out, f"out: {out[:300]}")

# a git-less build reports commit=null BY DESIGN. It cannot name what it
# runs, which is UNKNOWN — never "a different tree"
ORIGIN.reset(payload_for(None))
code, out = run()
check(3.3, code == 2, f"exit {code} for a process that cannot name its commit")
check(3.4, "reports no commit" in out and "DEPLOY_COMMIT" in out,
      f"the null-commit cause/advice is wrong: {out[:400]}")

# and an empty string must not be mistaken for a real hash either
ORIGIN.reset(payload_for(""))
code, out = run()
check(3.5, code == 2, f"exit {code} for an empty commit string")

# DEPLOY_COMMIT is usually an abbreviated hash: an equal prefix is the
# same commit, not "0 commits behind"
ORIGIN.reset(payload_for(HEAD[:12]))
code, out = run()
check(3.6, code == 0 and "UP TO DATE" in out,
      f"an abbreviated but identical commit read as {out[:200]}")

print("== 4. NO ENDPOINT ==")
ORIGIN.reset()
ORIGIN.mode = "html"
code, out = run()
check(4.1, code == 2, f"exit {code} for a pre-/version origin")
check(4.2, "UNKNOWN" in out and "predates /version" in out, f"out: {out[:300]}")

# the real public surface: /version answers 200 HTML (SPA fallback) and
# /api/version 403s — the informative failure must win, not the 403
ORIGIN.mode = "html403"
code, out = run()
check(4.3, code == 2, f"exit {code} for the mixed 200-HTML/403 shape")
check(4.4, "predates /version" in out,
      f"the 403 masked the informative diagnosis: {out[:300]}")

# a WAF/UA 403 is NOT "the route is missing" — it must say so, because
# Cloudflare 403s the stock urllib UA and the wrong message sends the
# reader chasing a cache problem that does not exist
ORIGIN.mode = "blocked"
code, out = run()
check(4.5, code == 2, f"exit {code} when the edge refuses the client")
check(4.6, "refused this client" in out, f"out: {out[:300]}")
check(4.7, "Python-urllib" not in (ORIGIN.last_ua or ""),
      f"stock UA sent: {ORIGIN.last_ua!r}")

print("== 5. UNREACHABLE ==")
DEAD = StubOrigin(drop=True)
code, out = run(url=DEAD.url)
check(5.1, code == 2, f"exit {code} on a dropped connection")
check(5.2, "unreachable" in out.lower(), f"no 'unreachable' diagnosis: {out[:300]}")
check(5.3, DEAD.static_hits == 0,
      f"a dead host still triggered {DEAD.static_hits} asset probes "
      f"(one timeout, not seventeen)")
check(5.4, "something is listening" in out,
      f"an unreachable host was told to restart instead of to check the "
      f"address: {out[:300]}")
DEAD.stop()

print("== 6. READ-ONLY ==")
src = SCRIPT.read_text(encoding="utf-8")
# structural, not a keyword hunt: every request the checker makes is a
# GET (the version probe and the per-asset fetches); one non-GET anywhere
# would mean it can mutate
req_calls = len(re.findall(r"urllib\.request\.Request\(", src))
get_calls = len(re.findall(r'method="GET"', src))
verb_lits = re.findall(r'method="(POST|PUT|PATCH|DELETE)"', src)
writes = [w for w in ("os.remove", "os.unlink", ".unlink(", ".write_text(",
                      ".write_bytes(", "shutil.rmtree",
                      "taskkill", "systemctl", "pkill", "Stop-Process")
          if w in src]
check(6.1, req_calls >= 1 and get_calls == req_calls and not verb_lits,
      f"requests={req_calls} GETs={get_calls} other verbs={verb_lits}")
check(6.2, not writes, f"mutating calls in the checker: {writes}")

print("== 7. ASSET BYTES ==")
ORIGIN.reset(payload_for(HEAD))
code, out = run()
check(7.1, code == 0 and "match the checkout" in out and "<--" not in out,
      f"a fully synced tree did not read as clean: exit {code} / {out[:300]}")
check(7.2, "assets" in out.lower(), "asset section missing from the human output")

# a half-copied deploy: the commit matches, one file on disk is older
_target = "app.js"
_local = (HERE / "pool" / "static" / _target).read_bytes()
ORIGIN.override = {_target: _local[:-40]}          # truncated copy
code, out = run()
check(7.3, code == 1, f"a truncated asset did not make the check fail: exit {code}")
check(7.4, "STALE — served assets differ" in out and _target in out, f"out: {out[:400]}")
check(7.5, "truncated copy" in out, f"the truncation cause is not named: {out[:400]}")

# same size, different bytes: edited in place and never recopied
_same_size = _local[:-1] + (b" " if _local[-1:] != b" " else b"\n")
assert len(_same_size) == len(_local)
ORIGIN.override = {_target: _same_size}
code, out = run()
check(7.6, code == 1 and "edited in place" in out,
      f"same-size drift not distinguished: {out[:400]}")

# a missing asset on the origin is a partial deploy, not a 404 page
ORIGIN.override = {}
ORIGIN.missing = {_target}
code, out = run()
check(7.7, code == 1 and "404" in out, f"a 404 asset did not fail: exit {code} / {out[:400]}")
ORIGIN.missing = set()

# behind still reads as behind, with asset drift appended not replacing it
ORIGIN.reset(payload_for(BEHIND or HEAD))
code, out = run()
if BEHIND:
    check(7.8, code == 1 and "behind" in out.lower(), f"out: {out[:400]}")
else:
    check(7.8, code == 2, "no HEAD~1 available")
ORIGIN.reset(payload_for(HEAD))

# --no-assets must not touch /static at all
ORIGIN.static_hits = 0
code, out = run("--no-assets")
check(7.9, code == 0 and ORIGIN.static_hits == 0,
      f"--no-assets still fetched {ORIGIN.static_hits} assets (exit {code})")
check("7.10", "skipped (--no-assets)" in out, f"out: {out[:300]}")

# an unreadable endpoint plus matching assets narrows the cause to memory
ORIGIN.mode = "html403"
code, out = run()
check(7.11, code == 2 and "only the code in memory is old" in out,
      f"the asset evidence did not sharpen the verdict: {out[:400]}")
ORIGIN.mode = "json"

ORIGIN.stop()

n_fail = results.count(False)
print(f"\nRESULT: {len(results) - n_fail} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
