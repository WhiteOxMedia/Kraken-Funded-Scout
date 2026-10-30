"""ops_status.py — THE ONE-COMMAND OPS RECEIPT (Sep 27).

One line per surface, every read-only:

  bundle version   the app.js?v=N the served template carries
  listener PID     who owns :3094 (empty = nobody = the server is down)
  http health      GET /api/stats status code
  scheduler        heartbeat age + the operator dial (SCHEDULER_ENABLED)
  last orphan scan age + totals (the weekly leg's marker)
  scheduled task   CodebuffWeeklyOrphanReport Last/Next Run + Last Result
  dead-man marker  the wrapper's task_sweep_report_last.txt age
  guard factor     the scout sibling's GF latest + history day-span
                   (read-only peek; the series' health is the scout
                   drives' contract — visible here, never alertable here)

Exit code is the alert (the tools/sweep_db.py --report contract):
  0 all-clear, 1 anything needs a human (server down, heartbeat stale,
  orphan build-up, task failure, dead-man stale).  --json for machines,
  --line for the whole receipt on ONE line (ALERT/OK prefix).

STRICTLY READ-ONLY: the DB opens through the mode=ro URI (SQLite itself
refuses every write), netstat/schtasks/urllib only observe.
"""
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "pool" / "templates" / "index.html"
DB_PATH = ROOT / "pool" / "jackpot.db"
DEADMAN = ROOT / "task_sweep_report_last.txt"
PORT = 3094
TASK_NAME = "CodebuffWeeklyOrphanReport"
# the sibling checkout's guard-factor series (the sizing brain the
# studies read): a read-only peek so the series stays visible on the
# ops receipt. Rooted beside this checkout like every cross-repo path.
SCOUT_STATE = ROOT.parent / "kraken_scout" / "profit_guard_state.json"
HEARTBEAT_STALE_S = 5400          # the loop ticks HOURLY (0..3600s sweep);
                                  # 1.5x clears the ceiling, catches a dead thread
DEADMAN_MAX_AGE_S = 8 * 86400     # the shared horizon (scheduler + registrar)


def bundle_version(template_path=TEMPLATE) -> str:
    try:
        m = re.search(r"app\.js\?v=(\d+)",
                      Path(template_path).read_text(encoding="utf-8"))
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def listener_pid(port=PORT) -> str:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, errors="replace", timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    # the port is a full netstat column: followed by whitespace -
    # ":1" must not match ":139" nor an IPv6 [::1] address literal
    pat = re.compile(rf":{port}\s")
    for line in out.splitlines():
        if "LISTENING" in line.upper() and pat.search(line):
            parts = line.split()
            if parts:
                return parts[-1]
    return ""


def http_health(port=PORT) -> str:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/stats", timeout=5) as r:
            return str(r.status)
    except Exception:
        return "down"


def _ro_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (key,)).fetchone()
    return row[0] if row else default


def db_state(db_path=DB_PATH) -> dict:
    out = {"heartbeat_age_s": None, "scheduler_enabled": None,
           "orphan_scan_age_s": None, "orphan_total": None,
           "orphan_edges": None}
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro",
                               uri=True)
        try:
            hb = _ro_setting(conn, "SCHEDULER_HEARTBEAT")
            if hb:
                out["heartbeat_age_s"] = round(time.time() - float(hb), 0)
            out["scheduler_enabled"] = _ro_setting(conn, "SCHEDULER_ENABLED")
            raw = _ro_setting(conn, "SCHEDULER_LAST_ORPHANS")
            if raw:
                d = json.loads(raw)
                if isinstance(d, dict):
                    out["orphan_scan_age_s"] = round(
                        time.time() - float(d.get("ts") or 0), 0)
                    out["orphan_total"] = d.get("total")
                    out["orphan_edges"] = d.get("edges")
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError):
        pass
    return out


def deadman_age(marker=DEADMAN):
    try:
        return round(time.time()
                     - float(Path(marker).read_text(encoding="ascii").strip()),
                     0)
    except (OSError, ValueError):
        return None


def _short_day(iso: str) -> str:
    """'2026-09-27' -> '0927' (the one-line receipt stays tiny); a
    string that is not a plain ISO day passes through unchanged."""
    parts = str(iso).split("-")
    return f"{parts[1]}{parts[2]}" if len(parts) == 3 else str(iso)


def guard_series(state_path=SCOUT_STATE) -> dict:
    """The scout sibling's guard-factor series, read-only: the latest
    factor and the day-span the history covers. STRICTLY visibility —
    absence (no sibling checkout, no state file, no series yet) is an
    honest NONE, never an alert: the series' health is pinned by the
    scout's own drives (test_profitguard 9b.*), not by this receipt."""
    out = {"gf_latest": None, "gf_span": None, "gf_days": 0}
    try:
        doc = json.loads(Path(state_path).read_text(encoding="utf-8"))
        days = sorted({str(r.get("day")) for r in
                       (doc.get("history") or [])
                       if isinstance(r, dict) and r.get("day")})
        if days:
            out["gf_span"] = f"{days[0]}..{days[-1]}"
            out["gf_days"] = len(days)
        if doc.get("factor") is not None:
            out["gf_latest"] = doc.get("factor")
    except (OSError, ValueError):
        pass
    return out


def task_result(task_name=TASK_NAME) -> dict:
    out = {"last_result": None, "last_run": None, "next_run": None}
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
            capture_output=True, text=True, errors="replace", timeout=20)
        text = (r.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in text.splitlines():
        if line.startswith("Last Result:"):
            val = line.split(":", 1)[1].strip()
            out["last_result"] = int(val) if val.isdigit() else val
        elif line.startswith("Last Run Time:"):
            out["last_run"] = line.split(":", 1)[1].strip()
        elif line.startswith("Next Run Time:"):
            if out["next_run"] is None:
                out["next_run"] = line.split(":", 1)[1].strip()
    return out


def collect() -> dict:
    st = db_state()
    task = task_result()
    pid = listener_pid()
    return {
        "bundle_version": bundle_version(),
        "listener_pid": pid,
        "http_health": http_health(),
        "scheduler_enabled": st["scheduler_enabled"],
        "heartbeat_age_s": st["heartbeat_age_s"],
        "orphan_scan_age_s": st["orphan_scan_age_s"],
        "orphan_total": st["orphan_total"],
        "orphan_edges": st["orphan_edges"],
        "task_last_result": task["last_result"],
        "task_last_run": task["last_run"],
        "task_next_run": task["next_run"],
        "deadman_age_s": deadman_age(),
        **guard_series(),
    }


def alerts(d: dict) -> list:
    out = []
    if not d["listener_pid"] or d["http_health"] != "200":
        out.append("server down or unhealthy")
    if d["scheduler_enabled"] == "1" and (
            d["heartbeat_age_s"] is None
            or d["heartbeat_age_s"] > HEARTBEAT_STALE_S):
        out.append("scheduler heartbeat stale")
    if d["orphan_total"] is None:
        out.append("no orphan scan on record")
    elif d["orphan_total"] > 0:
        out.append(f"orphans present: {d['orphan_total']} row(s)")
    if d["task_last_result"] not in (None, 0):
        out.append(f"scheduled task Last Result: {d['task_last_result']}")
    if d["deadman_age_s"] is None or d["deadman_age_s"] > DEADMAN_MAX_AGE_S:
        out.append("dead-man marker stale (the task stopped reporting)")
    return out


def one_line(d: dict) -> str:
    """THE ONE-LINE RECEIPT: the four operator fields (bundle version,
    listener PID, heartbeat age, last orphan scan) in a stable,
    grep-able order. The ALERT/OK prefix carries the exit-code verdict
    so a cron log line reads without parsing."""
    problems = alerts(d)
    head = "ALERT" if problems else "OK"
    pid = d["listener_pid"] or "-"
    hb = d["heartbeat_age_s"]
    hb_s = f"{hb:.0f}s" if hb is not None else "NONE"
    oa = d["orphan_scan_age_s"]
    oa_s = f"{oa / 86400:.1f}d" if oa is not None else "NONE"
    gf = d.get("gf_latest")
    gf_s = f"{gf:g}" if isinstance(gf, (int, float)) else "NONE"
    span = d.get("gf_span")
    span_s = "-".join(_short_day(p) for p in span.split("..")) \
        if span else "NONE"
    return (f"{head} v{d['bundle_version']} pid={pid} "
            f"http={d['http_health']} hb={hb_s} "
            f"orphans={d['orphan_total']}@{oa_s} "
            f"gf={gf_s} span={span_s}")


def main(argv) -> int:
    as_json = "--json" in argv
    as_line = "--line" in argv
    d = collect()
    problems = alerts(d)
    if as_json:
        print(json.dumps(dict(d, alerts=problems), indent=1))
    elif as_line:
        print(one_line(d))
    else:
        print(f"bundle version : app.js?v={d['bundle_version']}")
        print(f"listener       : {'PID ' + d['listener_pid'] if d['listener_pid'] else 'NOT LISTENING'}"
              f"  (/api/stats -> {d['http_health']})")
        hb = d["heartbeat_age_s"]
        print(f"scheduler      : enabled={d['scheduler_enabled']}"
              f", heartbeat {f'{hb:.0f}s ago' if hb is not None else 'NONE'}")
        oa = d["orphan_scan_age_s"]
        print(f"last orphan scan: "
              f"{f'{oa/86400:.1f}d ago' if oa is not None else 'NONE'}"
              f"  total={d['orphan_total']} edges={d['orphan_edges']}")
        print(f"scheduled task : last={d['task_last_run']} "
              f"next={d['task_next_run']} "
              f"Last Result={d['task_last_result']}")
        dm = d["deadman_age_s"]
        print(f"dead-man marker: "
              f"{f'{dm/3600:.1f}h old' if dm is not None else 'MISSING'}")
        gf = d.get("gf_latest")
        gs = d.get("gf_span")
        gd = d.get("gf_days") or 0
        print(f"guard factor   : "
              f"{f'{gf:g}' if isinstance(gf, (int, float)) else 'NONE'}"
              f"  series={f'{gs} ({gd} day(s))' if gs else 'NONE'}")
        if problems:
            print("ALERTS:")
            for p in problems:
                print(f"  ! {p}")
        else:
            print("all clear")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
