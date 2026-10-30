"""deploy_check.py — IS THE RUNNING PROCESS THE COMMIT I JUST MADE? (Oct 22).

One command, read-only, and the exit code is the answer:

    python deploy_check.py                              # the local origin
    python deploy_check.py --url https://xmrjackpot.com  # through Cloudflare
    python deploy_check.py --json                        # machine form

    0  the live process IS this checkout's HEAD and every served
       /static asset is byte-identical to the file on disk
    1  STALE — behind, ahead, diverged, serving another tree, or serving
       asset bytes that are NOT in this checkout (a half-copied deploy)
    2  CANNOT TELL — endpoint absent, host unreachable, git unreadable

WHY THIS EXISTS: the origin is a workstation, so "did my deploy actually
land?" was answered by hand — curl the page, hash it, compare mtimes,
guess. /version (pool/server.py) made the process self-report the commit
it STARTED FROM; this script is the other half: it diffs that claim
against git HEAD and names the cause when they disagree.

THE ONE DISTINCTION IT IS BUILT ON: a live commit equal to HEAD proves
the deploy landed; a live commit BEHIND HEAD proves the process was
never restarted; a live commit that git has never seen proves the
process is serving some OTHER tree. Those are three different repairs,
so they get three different messages.

THE SECOND ONE: a commit is not the deploy. Static files are read from
disk per request while the module is loaded once at boot, so a process
can report the right commit and still serve stale JS from a pool/static/
that was only half copied. Every asset the template references is
fetched (identity encoding, no query rewriting) and sha256-compared
against the checkout; a mismatch escalates an otherwise-clean verdict.

STRICTLY READ-ONLY: one HTTP GET, a handful of `git rev-parse` /
`git status` reads. No writes, no restarts, no cache purges — the script
diagnoses, a human decides.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The app itself refuses to start on a guessed port: APP_PORT is required
# (production binds it behind the Cloudflare tunnel). A hard loopback :80
# default answered "unreachable" on every bare run, because nothing can
# ever listen there. Follow the same env the server does; without APP_PORT
# the operator is presumably checking the public origin, which really is
# :80 at the edge (review, Oct 29).
_APP_PORT = (os.environ.get("APP_PORT") or "").strip()
DEFAULT_URL = (f"http://127.0.0.1:{int(_APP_PORT)}/"
               if _APP_PORT.isdigit() else "http://xmrjackpot.com/")
TIMEOUT = 10
_VERSION_PATHS = ("/version", "/api/version")
# Cloudflare 403s the stock Python-urllib UA outright (observed on
# xmrjackpot.com: default UA -> 403 on every path, browser UA -> 200),
# which would turn "the route is not live yet" into "the site is
# blocked" and send the reader hunting a WAF that is not the problem.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "MoneroPoolDeployCheck/1.0")
# Every script/stylesheet the page actually loads, in page order. Read
# off the template rather than hard-coded, so a new module enters the
# check the moment it is referenced (the template is the contract).
_ASSET_RE = re.compile(r"/static/([A-Za-z0-9_.\-]+\.(?:js|css))(?:\?v=(\d+))?")


def _git(root: Path, *args: str) -> str | None:
    """"One read-only git query, None on any failure (never raises)."""
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _git_ok(root: Path, *args: str) -> bool:
    """Boolean git predicate (merge-base --is-ancestor, cat-file -e)."""
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=20,
                              check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def repo_state(root: Path = ROOT) -> dict:
    commit = _git(root, "rev-parse", "HEAD")
    porcelain = _git(root, "status", "--porcelain")
    ct = _git(root, "log", "-1", "--format=%ct")
    return {
        "commit": commit,
        "commit_short": commit[:12] if commit else None,
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit_time": _git(root, "log", "-1", "--format=%cI"),
        "commit_epoch": int(ct) if ct and ct.isdigit() else None,
        "describe": _git(root, "describe", "--tags", "--always"),
        "dirty": None if porcelain is None else bool(porcelain),
        "root": str(root),
    }


def _base(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def fetch_live(url: str, timeout: float = TIMEOUT) -> dict:
    """Ask the running process who it is. Tries /version then /api/version;
    a process older than the endpoint answers with the SPA fallback HTML,
    which is itself the diagnosis (not an error)."""
    base = _base(url)
    last = {"ok": False, "reason": "no response", "url": base}
    # a 200 that is not JSON is the MOST informative failure (it means
    # the app answered but predates the route); keep it even if the
    # fallback path later 403s, or the diagnosis degrades to "403" and
    # sends the reader looking in the wrong place.
    rich = None
    for path in _VERSION_PATHS:
        req = urllib.request.Request(
            base + path, method="GET",
            headers={"Accept": "application/json",
                     "User-Agent": USER_AGENT,
                     "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                status = resp.status
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                last = {"ok": False,
                        "reason": f"HTTP {e.code} at {path} — the edge or the "
                                  f"app refused this client; a 403 is not proof "
                                  f"the route is missing (WAF/UA gate)",
                        "url": base + path, "http": e.code, "blocked": True}
            else:
                last = {"ok": False, "reason": f"HTTP {e.code} at {path}",
                        "url": base + path, "http": e.code}
            # HTTPError carries a file-like body; without close() each
            # refusal leaks a socket until GC (17 requests per run)
            e.close()
            continue
        except (urllib.error.URLError, OSError) as e:
            # host-level failure: the top of the loop returns here rather
            # than trying the next path, and assets are skipped entirely.
            # A dead host used to cost 2 version timeouts PLUS 16 asset
            # timeouts (~35s) to learn one fact.
            return {"ok": False, "reason": f"unreachable: {e}",
                    "url": base + path, "unreachable": True}

        try:
            doc = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            rich = {"ok": False, "reason": f"not JSON at {path} "
                    f"({len(raw)} bytes, {hdrs.get('content-type', '?')}) "
                    f"— the running code predates /version, or this is not the app",
                    "url": base + path, "http": status,
                    "content_type": hdrs.get("content-type")}
            continue
        if not isinstance(doc, dict) or "commit" not in doc:
            return {"ok": False, "reason": f"unexpected JSON shape at {path}",
                    "url": base + path, "http": status}
        return {"ok": True, "doc": doc, "url": base + path, "http": status,
                "cf_cache": hdrs.get("cf-cache-status"),
                "cache_control": hdrs.get("cache-control"),
                "server": hdrs.get("server")}
    return rich or last


def template_assets(root: Path = ROOT) -> list:
    """[(name, version)] for every /static/*.js|css the template loads.
    Page order, deduped — the served surface as the browser sees it."""
    try:
        text = (root / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
    except OSError:
        return []
    out, seen = [], set()
    for name, ver in _ASSET_RE.findall(text):
        if name in seen:
            continue
        seen.add(name)
        out.append((name, ver))
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_asset(base: str, name: str, version: str | None,
                timeout: float = TIMEOUT) -> dict:
    """One GET for a served asset. Identity encoding: a gzip-decoded
    body would hash differently from the file on disk and read as a
    false mismatch."""
    url = f"{base}/static/{name}" + (f"?v={version}" if version else "")
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "*/*", "User-Agent": USER_AGENT,
                 "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {"ok": True, "url": url, "http": resp.status,
                    "bytes": len(raw), "sha": _sha256(raw),
                    "content_type": resp.headers.get("Content-Type")}
    except urllib.error.HTTPError as e:
        reason = f"HTTP {e.code}"
        e.close()
        return {"ok": False, "url": url, "http": e.code, "reason": reason}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "url": url, "reason": f"unreachable: {e}"}


def compare_assets(base: str, root: Path = ROOT,
                   timeout: float = TIMEOUT) -> list:
    """Byte-compare every template-referenced asset against the checkout.
    A commit that matches but assets that do not is a half-copied deploy:
    the process was restarted over a stale pool/static/."""
    rows = []
    for name, ver in template_assets(root):
        local_path = root / "pool" / "static" / name
        try:
            local = local_path.read_bytes()
        except OSError:
            local = None
        live = fetch_asset(base, name, ver, timeout=timeout)
        row = {"name": name, "version": ver, "live_bytes": live.get("bytes"),
               "live_sha": live.get("sha"),
               "local_bytes": len(local) if local is not None else None,
               "local_sha": _sha256(local) if local is not None else None}
        if local is None:
            row.update(ok=False, reason="checkout has no such file")
        elif not live.get("ok"):
            row.update(ok=False, reason=live.get("reason", "no response"))
        elif live.get("content_type", "").startswith("text/html"):
            # the SPA fallback answered: the static mount is not serving
            # this file at all
            row.update(ok=False, reason="served the app HTML, not the asset")
        elif live["sha"] != row["local_sha"]:
            row.update(ok=False, reason="bytes differ")
        else:
            row.update(ok=True, reason="match")
        rows.append(row)
    return rows


def _asset_detail(rows: list, limit: int = 4) -> str:
    bad = [r for r in rows if not r["ok"]]
    parts = []
    for r in bad[:limit]:
        if r.get("live_bytes") is None or r.get("local_bytes") is None:
            parts.append(f"{r['name']} ({r['reason']})")
        elif r["live_bytes"] != r["local_bytes"]:
            parts.append(f"{r['name']} ({r['reason']}: {r['local_bytes']}b here, "
                         f"{r['live_bytes']}b served — truncated copy)")
        else:
            parts.append(f"{r['name']} ({r['reason']} at the same size — "
                         f"edited in place, not deployed)")
    if len(bad) > limit:
        parts.append(f"+{len(bad) - limit} more")
    return "; ".join(parts)


def apply_assets(verdict: tuple, rows: list) -> tuple:
    """Let asset evidence escalate (never soften) the identity verdict.
    A half-copied deploy is its own failure even when the commit matches."""
    if not rows:
        return verdict
    code, status, cause, next_step = verdict
    bad = [r for r in rows if not r["ok"]]
    if not bad:
        if code == 2:
            return (code, status,
                    f"{cause} — but all {len(rows)} served assets match the "
                    f"checkout, so only the code in memory is old",
                    next_step)
        return verdict
    detail = _asset_detail(rows)
    if code == 0:
        return (1, "STALE — served assets differ from the checkout",
                f"commit matches but {len(bad)}/{len(rows)} assets do not: {detail}",
                "recopy pool/static/ to the origin and restart — the running "
                "process is serving a mixed tree")
    if code == 1:
        return (code, status, f"{cause}; also {len(bad)}/{len(rows)} assets "
                               f"differ: {detail}", next_step)
    return (code, status, f"{cause}; {len(bad)}/{len(rows)} assets differ: {detail}",
            next_step)


def _fmt_ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)\
            .strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return "?"


def _fmt_age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def classify(repo: dict, live: dict, strict: bool = False) -> tuple:
    """(exit_code, status, cause, next_step) — the whole judgement."""
    if not repo.get("commit"):
        return (2, "UNKNOWN", "git cannot read this checkout",
                "run from the repo root, or pass --repo")
    if not live.get("ok"):
        if live.get("unreachable"):
            # nothing answered at all: "restart it" is the wrong advice for
            # a host that may simply be the wrong address
            return (2, "UNKNOWN", live.get("reason", "unreachable"),
                    "confirm the host and port are right and that something "
                    "is listening; a dead origin cannot be diffed")
        return (2, "UNKNOWN", live.get("reason", "no version endpoint"),
                "restart the origin process so it serves the new /version "
                "route, then re-run")

    doc = live["doc"]
    lc, lb = doc.get("commit"), doc.get("branch")
    started = doc.get("started_at")
    started_s = _fmt_ts(started) if started else "?"
    uptime = doc.get("uptime_seconds")

    if not lc:
        # /version reports null when the process was built without git and
        # without DEPLOY_COMMIT. That is "cannot name what it runs" — an
        # honest UNKNOWN. Calling it a mismatch accused a correct deploy of
        # serving some other tree, with advice to go fix the wrong thing.
        return (2, "UNKNOWN",
                "the live process reports no commit (built without git and "
                "without DEPLOY_COMMIT)",
                "set DEPLOY_COMMIT when deploying without .git, or launch the "
                "process from a real checkout")

    # DEPLOY_COMMIT is commonly set to an abbreviated hash; git's own
    # abbreviation rule is "unique prefix", so a 7+ char prefix that the
    # checkout's HEAD starts with IS the same commit — comparing strings
    # exactly would report a correct deploy as "0 commits behind".
    same_commit = lc == repo["commit"] or (
        isinstance(lc, str) and len(lc) >= 7 and repo["commit"].startswith(lc))

    if same_commit:
        if repo.get("dirty") and strict:
            return (1, "STALE — checkout dirty",
                    "the commit matches but the worktree has uncommitted or "
                    "untracked files the running process may already include",
                    "commit or discard the drift, then restart")
        if repo.get("dirty"):
            return (0, "UP TO DATE — checkout dirty",
                    "same commit, but the worktree carries uncommitted or "
                    "untracked changes (a process started from it may differ)",
                    "commit the drift if it should be part of the deploy")
        if lb and repo.get("branch") and lb != repo["branch"]:
            return (0, "UP TO DATE",
                    f"same commit, different branch label (live {lb!r}, "
                    f"checkout {repo['branch']!r})",
                    "none — the commit is identical")
        return (0, "UP TO DATE", "live process is this checkout's HEAD",
                "none — the deploy landed")

    root = Path(repo.get("root") or ROOT)
    if not _git_ok(root, "cat-file", "-e", f"{lc}^{{commit}}"):
        # git has never seen this commit: a different tree, branch, or host
        return (1, "MISMATCH — unknown commit",
                f"live commit {str(lc)[:12]} is not in this checkout",
                "the origin is serving a different tree/branch — point it at "
                "this checkout and restart, or check --url is the right host")

    if _git_ok(root, "merge-base", "--is-ancestor", lc, repo["commit"]):
        behind = _git(root, "rev-list", "--count", f"{lc}..{repo['commit']}") or "?"
        before = (repo.get("commit_epoch") and started
                  and float(started) < repo["commit_epoch"])
        cause = ("the origin was never restarted after the commit landed"
                 if before else
                 "the origin is running an older checkout, or was restarted "
                 "before the latest commits")
        return (1, f"STALE — {behind} commit(s) behind",
                f"live {str(lc)[:12]} started {started_s} "
                f"(up {_fmt_age(float(uptime)) if uptime else '?'}); "
                f"HEAD is {repo['commit_short']}. {cause}",
                "restart the origin process; no rebuild is needed")

    if _git_ok(root, "merge-base", "--is-ancestor", repo["commit"], lc):
        return (1, "AHEAD — server has commits this checkout lacks",
                f"live {str(lc)[:12]} is a descendant of HEAD {repo['commit_short']}",
                "pull/fetch this checkout, or point --repo at the tree that "
                "was deployed")

    return (1, "DIVERGED",
            f"live {str(lc)[:12]} and HEAD {repo['commit_short']} share no "
            f"ancestry in this checkout",
            "confirm which tree should be live before changing anything")


def render(repo: dict, live: dict, verdict: tuple, assets=None) -> None:
    code, status, cause, next_step = verdict
    print(f"repo    : {repo.get('branch') or '?'} @ {repo.get('commit_short') or '?'}"
          f"  ({repo.get('describe') or 'no describe'})"
          f"  committed {repo.get('commit_time') or '?'}")
    if live.get("ok"):
        d = live["doc"]
        up = d.get("uptime_seconds")
        print(f"live    : {d.get('branch') or '?'} @ {d.get('commit_short') or '?'}"
              f"  started {_fmt_ts(d.get('started_at'))}"
              f"  up {_fmt_age(float(up)) if up else '?'}"
              f"  pid {d.get('pid')}")
        print(f"endpoint: {live.get('url')} ({live.get('http')})"
              f"  cf-cache-status={live.get('cf_cache') or '-'}"
              f"  cache-control={live.get('cache_control') or '-'}")
        if (live.get("cf_cache") or "").upper() in ("HIT", "STALE", "REVALIDATED"):
            print("warning : the version response came from the edge cache — "
                  "purge it, or the answer describes the cache, not the origin")
    else:
        print(f"live    : NO VERSION ENDPOINT — {live.get('reason')}")
    if assets is None:
        print("assets  : skipped (--no-assets)"
              if not live.get("unreachable") else
              "assets  : skipped (host unreachable — same host as /version)")
    elif not assets:
        print("assets  : none referenced by the template")
    else:
        ok = sum(1 for r in assets if r["ok"])
        print(f"assets  : {ok}/{len(assets)} match the checkout"
              + ("" if ok == len(assets) else "  <-- " + _asset_detail(assets)))
    print(f"status  : {status}")
    print(f"cause   : {cause}")
    print(f"next    : {next_step}")
    print(f"RESULT  : {'OK' if code == 0 else ('STALE' if code == 1 else 'UNKNOWN')}"
          f" (exit {code})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Compare the running process's /version against git HEAD.")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"origin base url (default {DEFAULT_URL}); "
                         f"the public domain works too")
    ap.add_argument("--repo", default=str(ROOT),
                    help="checkout to compare against (default: this script's)")
    ap.add_argument("--timeout", type=float, default=TIMEOUT)
    ap.add_argument("--strict", action="store_true",
                    help="treat a dirty worktree as STALE (exit 1)")
    ap.add_argument("--no-assets", action="store_true",
                    help="skip the served-asset byte comparison")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)

    root = Path(args.repo).resolve()
    repo = repo_state(root)
    live = fetch_live(args.url, timeout=args.timeout)
    assets = None if (args.no_assets or live.get("unreachable")) else \
        compare_assets(_base(args.url), root, timeout=args.timeout)
    verdict = apply_assets(classify(repo, live, strict=args.strict), assets or [])

    if args.json:
        print(json.dumps({"repo": {k: v for k, v in repo.items() if k != "root"},
                          "live": live.get("doc") if live.get("ok") else None,
                          "endpoint": {k: v for k, v in live.items() if k != "doc"},
                          "assets": None if assets is None else {
                              "checked": len(assets),
                              "matched": sum(1 for r in assets if r["ok"]),
                              "rows": assets},
                          "status": verdict[1], "cause": verdict[2],
                          "next_step": verdict[3], "exit": verdict[0]},
                         indent=1, default=str))
    else:
        render(repo, live, verdict, assets)
    return verdict[0]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
