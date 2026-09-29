"""THE FLEET SWEEP (ops tool, Sep 29): run every drive in the repo once,
sequentially, and write a health report the operator can open any time.

  uv run python -X utf8 ops/fleet_sweep.py                  # all 157
  uv run python -X utf8 ops/fleet_sweep.py test_tide test_canary
  uv run python -X utf8 ops/fleet_sweep.py --timeout 120

Outputs (artifacts/fleet_sweep/):
  fleet_sweep_<stamp>.html   the human report (self-contained, dark)
  fleet_sweep_latest.html    the same content at a stable name
  fleet_sweep_last.json      machine-readable results for trend diffs

CLASSIFICATION: a drive that fails is RED until ops/fleet_sweep_classes.json
says otherwise. That reviewed file maps a drive to {"status": "FLAKY"|
"STALE PIN"|..., "note": "...", "verified": "YYYY-MM-DD"} — debt the
fleet has already triaged (load transients, pins awaiting an update).
Classified failures render AMBER with their note and still list the
failing checks; UNCLASSIFIED failures render RED. Exit code: 0 when
every drive is green or only classified failures remain, 1 on any
unclassified failure — the commit gate stays the enforcement layer;
this tool is the operator's eyes.

Read-only on the repo: it runs the drives (which manage their own
sandboxes) and writes only into artifacts/fleet_sweep/.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "artifacts" / "fleet_sweep"
CLASSES_FILE = Path(__file__).resolve().parent / "fleet_sweep_classes.json"

_VERDICT_RE = re.compile(
    r"RESULT: \d+ passed, \d+ failed|\d+/\d+ passed|ALL PASS|DONE")
_FAIL_LINE_RE = re.compile(r"^\s*(FAIL\b.*)$", re.MULTILINE)


def discover_drives(only: list[str]) -> list[Path]:
    drives = sorted(ROOT.glob("test_*.py"))
    if not only:
        return drives
    want = set()
    for o in only:
        o = o.strip()
        for cand in (o, f"test_{o}", f"{o}.py", f"test_{o}.py"):
            if (ROOT / cand).exists():
                want.add((ROOT / cand).resolve())
                break
        else:
            print(f"  ?? no such drive: {o} (skipped)")
    return [d for d in drives if d.resolve() in want]


def load_classes() -> dict:
    try:
        data = json.loads(CLASSES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def run_one(drive: Path, timeout: int) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(drive)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return {"drive": drive.name, "rc": -1, "verdict": "TIMEOUT",
                "failed": -1, "secs": round(time.time() - t0, 1),
                "status": "RED", "cls_status": "", "cls_note": "",
                "fails": ["timeout — the drive hung past the budget"]}
    secs = round(time.time() - t0, 1)
    verdict = _VERDICT_RE.findall(out)
    verdict = verdict[-1] if verdict else ""
    failed = -1
    m = re.search(r"(\d+) failed", verdict)
    if m:
        failed = int(m.group(1))
    elif "/" in verdict:
        m2 = re.search(r"/(\d+) passed", verdict)
        failed = 0 if (m2 and verdict.split("/")[0] == m2.group(1)) else -1
    elif verdict in ("ALL PASS", "DONE"):
        failed = 0
    fails = [l.strip()[:110]
             for l in _FAIL_LINE_RE.findall(out)[:6]]
    return {"drive": drive.name, "rc": rc, "verdict": verdict or "no-verdict",
            "failed": failed, "secs": secs, "status": "", "cls_status": "",
            "cls_note": "", "fails": fails}


def classify(res: dict, classes: dict) -> dict:
    """RED when the drive failed and no reviewed classification covers
    it; the class renders AMBER with its note (and its verified date,
    so stale classifications are visible as stale)."""
    bad = res["rc"] != 0 or (0 <= res["failed"] != 0)
    cls = classes.get(res["drive"], {})
    res["cls_status"] = str(cls.get("status") or "")
    res["cls_note"] = str(cls.get("note") or "")
    if not bad:
        res["status"] = "GREEN"
    elif res["cls_status"]:
        res["status"] = res["cls_status"]
    else:
        res["status"] = "RED"
    return res


_CSS = """
body{background:#101418;color:#cfd8dc;font-family:'Share Tech Mono',
 ui-monospace,Consolas,monospace;margin:0;padding:24px}
h1{color:#e8d5a3;font-size:20px;margin:0 0 4px}
.sub{color:#7a8a99;font-size:12px;margin-bottom:18px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.card{background:#171d24;border:1px solid #232b33;border-radius:6px;
 padding:10px 16px;min-width:110px}
.card .n{font-size:26px;font-weight:bold}
.card .l{font-size:11px;color:#7a8a99}
.g .n{color:#7fc97f}.r .n{color:#e07b7b}.a .n{color:#d9b96a}
table{border-collapse:collapse;width:100%;font-size:12px}
th{color:#8fa3bf;text-align:left;border-bottom:1px solid #232b33;
 padding:6px 8px}
td{padding:4px 8px;border-bottom:1px solid #1a2129;vertical-align:top}
tr.G td:first-child{color:#7fc97f}tr.RED td:first-child{color:#e07b7b}
tr.AMBER td:first-child{color:#d9b96a}
.f{color:#e07b7b;font-size:11px;display:block;margin-top:2px}
.legend{margin-top:16px;font-size:11px;color:#7a8a99}
"""


def _row(i, r, classes: dict) -> str:
    name = html.escape(r["drive"].replace(".py", ""))
    verdict = html.escape(r["verdict"])
    fails = ""
    if r["status"] != "GREEN" and r["fails"]:
        fails = "<span class='f'>" + "<br>".join(
            html.escape(f) for f in r["fails"]) + "</span>"
    note = html.escape(r["cls_note"]) if r["cls_note"] else ""
    if r["cls_status"] and r["status"] != "GREEN":
        verified = str(classes.get(r["drive"], {}).get("verified") or "")
        note = (f"[{html.escape(r['cls_status'])}"
                + (f", verified {html.escape(verified)}" if verified else "")
                + f"] {note}")
    return (f"<tr class='{r['status']}'>"
            f"<td>{i}</td><td>{name}</td><td>{r['status']}</td>"
            f"<td>{verdict}{fails}</td><td>{r['secs']}s</td>"
            f"<td>{note}</td></tr>")


def render(results: list[dict], wall_s: float, when: str,
           classes: dict) -> str:
    n = len(results)
    green = sum(1 for r in results if r["status"] == "GREEN")
    amber = sum(1 for r in results if r["status"] not in ("GREEN", "RED"))
    red = sum(1 for r in results if r["status"] == "RED")
    checks = sum(int(m.group(1)) for r in results
                 if r["status"] == "GREEN"
                 for m in [re.search(r"(\d+) passed", r["verdict"])] if m)
    rows = "".join(_row(i, r, classes)
                   for i, r in enumerate(results, 1))
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Fleet health — {when}</title><style>{_CSS}</style></head><body>
<h1>⚡ FLEET HEALTH — {html.escape(when)}</h1>
<div class='sub'>kraken_scout · {n} drives · wall {wall_s:.0f}s ·
generated by ops/fleet_sweep.py</div>
<div class='cards'>
<div class='card g'><div class='n'>{green}</div><div class='l'>GREEN</div></div>
<div class='card a'><div class='n'>{amber}</div><div class='l'>CLASSIFIED (flaky / stale pin)</div></div>
<div class='card r'><div class='n'>{red}</div><div class='l'>RED — unclassified</div></div>
<div class='card'><div class='n'>{checks}</div><div class='l'>CHECKS PASSED</div></div>
</div>
<table><tr><th>#</th><th>drive</th><th>status</th><th>verdict</th>
<th>time</th><th>note</th></tr>{rows}</table>
<div class='legend'>GREEN = exit 0, no failed checks · AMBER = a failure
the fleet has already triaged (see ops/fleet_sweep_classes.json, with
its verified date) · RED = an unclassified failure: treat as a real
regression until proven otherwise. The commit gate remains the
enforcement layer; this report is the operator's eyes.</div>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="run every drive; write the fleet health report")
    ap.add_argument("drives", nargs="*", help="subset (test_foo | foo)")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    drives = discover_drives(args.drives)
    classes = load_classes()
    print(f"⚡ fleet sweep: {len(drives)} drive(s), timeout {args.timeout}s")
    results: list[dict] = []
    t0 = time.time()
    for d in drives:
        r = run_one(d, args.timeout)
        r = classify(r, classes)
        mark = {"GREEN": "✅", "RED": "⛔"}.get(r["status"], "amber")
        print(f"  {mark} {r['drive']:<38} {r['status']:<8} "
              f"{r['verdict']:<34} {r['secs']}s", flush=True)
        results.append(r)
    wall = time.time() - t0
    when = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = render(results, wall, when, classes)
    (OUT_DIR / f"fleet_sweep_{stamp}.html").write_text(report,
                                                      encoding="utf-8")
    (OUT_DIR / "fleet_sweep_latest.html").write_text(report,
                                                    encoding="utf-8")
    (OUT_DIR / "fleet_sweep_last.json").write_text(
        json.dumps({"when": when, "wall_s": round(wall, 1),
                    "results": results}, indent=1) + "\n",
        encoding="utf-8")

    unclassified = sum(1 for r in results if r["status"] == "RED")
    print(f"\n{len(results)} drives · wall {wall:.0f}s · "
          f"unclassified RED: {unclassified}")
    print(f"report: artifacts/fleet_sweep/fleet_sweep_latest.html")
    return 1 if unclassified else 0


if __name__ == "__main__":
    raise SystemExit(main())
