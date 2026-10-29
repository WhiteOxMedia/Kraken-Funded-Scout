"""test_version_endpoint.py — THE DEPLOYED-IDENTITY SURFACE, PINNED (Oct 22).

CHARTER: the origin here is a workstation, so "is the live site running
the commit I just made?" used to mean walking mtimes and sha256s by hand
against the served bytes. /version answers it in one read-only JSON
request: the commit this PROCESS was started from, the branch, the
commit time, and the process start time. The snapshot resolves once at
import — no git call and no file read per request — and the response is
deliberately uncached, because a cached version stamp is worse than no
stamp at all.

This drive pins the YARDSTICK, not the browser:

  1. THE ROUTES ANSWER: /version and /api/version both return 200 JSON.
  2. THE SNAPSHOT IS THE CHECKOUT: commit/branch equal git rev-parse
     against THIS repo, and commit_short is the full hash's prefix.
  3. READ-ONLY: mutating verbs are refused (405) — no body, no params.
  4. NO PER-REQUEST WORK: the handler only reads the module snapshot;
     two requests report one identical stamp.
  5. UNCACHED: Cache-Control is no-store.
  6. THE OLD SURFACE IS INTACT: GET / still serves the template bytes,
     and the pinned security headers still stamp this route.
  7. DEGRADES WITHOUT GIT: DEPLOY_COMMIT/DEPLOY_BRANCH still yield a
     usable stamp tagged source="env".
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# hermetic DB, exactly like the other server-importing drives: the app
# module touches database on import, and the gate must never read or
# write the live jackpot ledger.
_tmp = Path(tempfile.mkdtemp(prefix="ver_ep_"))
import database as db
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

from fastapi.testclient import TestClient  # noqa: E402

import server as srv_mod  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


client = TestClient(srv_mod.app)

required = ("commit", "commit_short", "branch", "commit_time",
            "describe", "dirty", "source", "started_at",
            "started_at_iso", "uptime_seconds", "pid")

print("== 1. THE ROUTES ANSWER ==")
r = client.get("/version")
r_api = client.get("/api/version")
check(1.1, r.status_code == 200 and r_api.status_code == 200,
      f"status codes /version={r.status_code} /api/version={r_api.status_code}")
check(1.2, r.headers.get("content-type", "").startswith("application/json"),
      f"content-type {r.headers.get('content-type')!r}")
# identity fields only: uptime_seconds is legitimately per-request
body = r.json()
doc = r_api.json()
check(1.3, {k: doc[k] for k in required if k != "uptime_seconds"}
      == {k: body[k] for k in required if k != "uptime_seconds"},
      "the two routes disagree outside the per-request uptime field")

print("== 2. THE SNAPSHOT IS THE CHECKOUT ==")
check(2.1, all(k in body for k in required),
      f"missing keys: {sorted(set(required) - set(body))}")

try:
    want_commit = subprocess.run(
        ["git", "-C", str(HERE), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=15, check=False,
    ).stdout.strip()
    want_branch = subprocess.run(
        ["git", "-C", str(HERE), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=15, check=False,
    ).stdout.strip()
except (OSError, subprocess.SubprocessError):
    want_commit = want_branch = ""

check(2.2, want_commit and body["commit"] == want_commit,
      f"reported {body['commit']!r} vs checkout {want_commit!r}")
check(2.3, want_branch and body["branch"] == want_branch,
      f"reported {body['branch']!r} vs checkout {want_branch!r}")
check(2.4, isinstance(body["commit_short"], str)
      and want_commit.startswith(body["commit_short"])
      and len(body["commit_short"]) == 12,
      f"commit_short {body['commit_short']!r} is not a 12-char prefix of HEAD")

print("== 3. READ-ONLY ==")
for verb in ("post", "put", "patch", "delete"):
    resp = client.request(verb.upper(), "/version", json={})
    check(f"3.{verb}", resp.status_code == 405, f"{verb.upper()} -> {resp.status_code}")

print("== 4. NO PER-REQUEST WORK ==")
check(4.1, "subprocess" not in __import__("inspect").getsource(srv_mod.version),
      "handler shells out to git on the request path")
check(4.2, "open(" not in __import__("inspect").getsource(srv_mod.version),
      "handler reads a file on the request path")
again = client.get("/version").json()
check(4.3, again["commit"] == body["commit"]
      and again["started_at"] == body["started_at"],
      "the stamp drifted between two requests (snapshot is not module-level)")
check(4.4, again["uptime_seconds"] >= body["uptime_seconds"],
      "uptime went backwards")

print("== 5. UNCACHED ==")
cc = (r.headers.get("cache-control") or "").lower()
check(5.1, "no-store" in cc, f"Cache-Control {cc!r}")

print("== 6. THE OLD SURFACE IS INTACT ==")
root = client.get("/")
tpl = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
# The shell is the template bytes plus the site-level JSON-LD the server
# stamps into the head, minus the /download-only block. Nothing else is
# assembled on the request path.
canonical = ('<link rel="canonical" href="' + srv_mod.SITE_ORIGIN + '/">\n')
shell = root.text.replace(srv_mod._site_jsonld() + "\n", "", 1)
shell = shell.replace(canonical, "", 1)
# Oct 29 i18n rework: every homepage variant (English root included)
# now stamps runtime pieces the template does not carry — the
# <script id="appI18n"> per-language catalog in the head and the
# region-guidance card in the landing and dashboard sections (the
# TestClient sends no Accept-Language header, so the card carries the
# "unknown region" copy). Both strips work by PAYLOAD SHAPE rather
# than exact bytes: the catalog varies with the language's strings,
# and the card's tail '</p></div>' is unique inside it (the header
# div closes early).
shell = re.sub(r'<script type="application/json" id="appI18n">'
                r'[\s\S]*?</script>\n?', "", shell, count=1)
shell = re.sub(r'<div class="card" data-region-guidance="true"'
               r'[\s\S]*?</p></div>', "", shell)
# The template carries live tokens now (entry unit, payout split, draw
# cadence, claim window, Pro price, live miner count, the winners rows),
# so "verbatim" means the RENDERED template, not the file bytes. Same
# reader the route uses; the JSON-LD block is stripped the same way.
expected = srv_mod._SCHEMA_BLOCK_RE.sub("", srv_mod._template_html())
check(6.1, root.status_code == 200 and shell == expected,
      "GET / no longer serves the template (plus site JSON-LD and its "
      "canonical) verbatim")
check("6.1c", canonical in root.text and root.text.count('rel="canonical"') == 1,
      "the root carries exactly one self-referencing canonical")
check(6.2, r.headers.get("x-frame-options") == "DENY"
      and r.headers.get("x-content-type-options") == "nosniff",
      "the pinned security headers stopped stamping this route")
check(6.3, "frame-ancestors" in (r.headers.get("content-security-policy") or ""),
      "CSP missing on /version")

print("== 7. DEGRADES WITHOUT GIT ==")
_saved = {k: os.environ.get(k) for k in ("DEPLOY_COMMIT", "DEPLOY_BRANCH")}
try:
    os.environ["DEPLOY_COMMIT"] = "f" * 40
    os.environ["DEPLOY_BRANCH"] = "artifact-without-git"
    snap = srv_mod._deploy_snapshot()
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
check(7.1, snap["source"] == "env" and snap["commit"] == "f" * 40,
      f"env override ignored: {snap['commit']!r} source={snap['source']!r}")
check(7.2, snap["branch"] == "artifact-without-git",
      f"branch override ignored: {snap['branch']!r}")
check(7.3, srv_mod._deploy_snapshot()["source"] == "git",
      "env override leaked into the next snapshot (module env not restored)")

# a deploy that ships without .git must still BOOT: _git() has to fail
# soft (None), never raise, or the whole app dies at import
_real_root = srv_mod._REPO_ROOT
try:
    srv_mod._REPO_ROOT = _tmp          # a directory with no .git anywhere
    bare = srv_mod._deploy_snapshot()
finally:
    srv_mod._REPO_ROOT = _real_root
check(7.4, bare["commit"] is None and bare["branch"] is None,
      f"no-git snapshot invented identity: {bare['commit']!r}/{bare['branch']!r}")
check(7.5, bare["dirty"] is None and bare["source"] == "git",
      f"no-git snapshot dirty/source wrong: {bare['dirty']!r}/{bare['source']!r}")
check(7.6, srv_mod._deploy_snapshot()["commit"] == want_commit,
      "the real repo root did not come back after the no-git probe")

n_fail = results.count(False)
print(f"\nRESULT: {len(results) - n_fail} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
