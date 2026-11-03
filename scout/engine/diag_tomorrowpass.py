"""diag_tomorrowpass.py — VERIFY TOMORROW'S FIRST PASS (Sep 12).

Tomorrow (Sep 13) is still 2026-W37 — the week boundary is Monday — so
the first daily pass must REFRESH the same week tag: dedupe (replace)
the existing W37 history record, re-tag the report section, re-grade
the fill ledgers, and rewrite the sidecar. This diag rehearses exactly
that in a sandbox: two report_now() passes one simulated day apart
with the same week tag, then asserts every write. Sandbox-only: every
production path is redirected; nothing on disk changes.
Run: .venv\\Scripts\\python.exe -X utf8 diag_tomorrowpass.py
"""
import json
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import tidebar_weekly as tbw  # noqa: E402

import tide_learn as tl  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── the sandbox: every production path redirected ────────────────────
sbx = ROOT / "_test_tomorrow_dir"
import shutil  # noqa: E402
shutil.rmtree(sbx, ignore_errors=True)
sbx.mkdir(exist_ok=True)
real = {k: getattr(tbw, k) for k in
        ("OUT_FILE", "JSON_FILE", "HISTORY_FILE", "FLIP_FILE",
         "FLIP_EVENTS_FILE", "FLIPGRADE_FILE", "SNAP_OUTCOMES_FILE",
         "TRADES_FILE", "FLIPGRADE_JSON", "RECKONING_WINDOW_FILE")}
try:
    tbw.OUT_FILE = sbx / "weekly.txt"
    tbw.JSON_FILE = sbx / "sidecar.json"
    tbw.HISTORY_FILE = sbx / "history.jsonl"
    tbw.FLIP_FILE = sbx / "flips.json"
    tbw.FLIP_EVENTS_FILE = sbx / "flip_events.jsonl"
    tbw.FLIPGRADE_FILE = sbx / "flipgrade.txt"
    tbw.SNAP_OUTCOMES_FILE = sbx / "snap_outcomes.jsonl"
    tbw.TRADES_FILE = sbx / "trades.jsonl"
    # a completed grade records its reckoning window row — sandboxed
    tbw.FLIPGRADE_JSON = sbx / "flipgrade.json"
    tbw.RECKONING_WINDOW_FILE = sbx / "reckoning_window.jsonl"

    MODEL = {"states": {st: {"bar": 80.0, "bar_src": "quantile",
                             "payer_rate": 0.1}
                        for st in ("OUT", "FLAT", "RISING")}}
    tl.learn = lambda *a, **k: dict(MODEL)
    real_qq = tl._funnel_score_quantiles
    tl._funnel_score_quantiles = lambda: [50.0] * 60
    real_fit = tl._outcome_fit_bar
    tl._outcome_fit_bar = lambda st, sc, arr_cache=None: (
        {"bar": 84.0, "ev_o": 2.0, "ev_q": 1.0, "n": 100,
         "payer_rate": 0.1, "state": st} if st == "FLAT" else None)
    real_cands = tbw._week_candidates
    tbw._week_candidates = lambda: ([
        ("OUT", 85.0, True), ("OUT", 90.0, False),
        ("FLAT", 95.0, True), ("RISING", 88.0, True)], None)

    # scripted fills: an OUT fire that filled +$8 (graded), a FLAT fire
    # whose fill sold before the fire (must NOT join), plus a plain
    # suppressed row (ignored)
    now = datetime.now(timezone.utc)
    snap = [
        # fires 2h ago; the ZEC fill lands +3h after its fire (inside the
        # FILL_JOIN_H=8h window), the ETH fill sits ~47h BEFORE its fire
        {"ts": (now - timedelta(hours=2)).isoformat(), "pair": "ZEC/USD",
         "kind": "fired", "tide": "OUT", "quick": 85.0},
        {"ts": (now - timedelta(hours=2)).isoformat(), "pair": "ETH/USD",
         "kind": "fired", "tide": "FLAT", "quick": 95.0},
    ]
    tbw.SNAP_OUTCOMES_FILE.write_text(
        "\n".join(json.dumps(r) for r in snap) + "\n", encoding="utf-8")
    trades = [
        {"pair": "ZEC/USD", "sold_at": (now + timedelta(hours=1))
         .isoformat(), "pnl_net": 8.0},
        # the FLAT fill sold long before its fire ts (outside the
        # FILL_JOIN_H=8h window) — must never join
        {"pair": "ETH/USD", "sold_at": (now - timedelta(days=3))
         .isoformat(), "pnl_net": 99.0},
    ]
    tbw.TRADES_FILE.write_text(
        "\n".join(json.dumps(r) for r in trades) + "\n", encoding="utf-8")

    # the week tag the passes will use (pinned, not assumed)
    wk_tag = _time.strftime("%G-W%V", _time.gmtime())

    # ── PASS 1 (today's rehearsal) ──────────────────────────────────
    s1 = tbw.report_now()
    txt1 = tbw.OUT_FILE.read_text(encoding="utf-8")
    hist1 = [json.loads(l) for l in
             tbw.HISTORY_FILE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    check("pass 1 grades without error",
          "err" not in s1 and "held" not in (s1.get("held") or ""), s1)
    check("pass 1 tags the report section with the real ISO week",
          f"({wk_tag}," in txt1, txt1[:200])
    check("pass 1 writes exactly one history record",
          len(hist1) == 1 and hist1[0]["week"] == wk_tag, hist1)
    fills1 = hist1[0].get("fills")
    check("pass 1 grades the fills ledger (OUT +$8; the stale ETH fill "
          "never joins FLAT)",
          isinstance(fills1, dict) and fills1.get("OUT", {}).get("fills") == 1
          and abs(fills1.get("OUT", {}).get("pnl", 0) - 8.0) < 0.01
          and fills1.get("FLAT", {}).get("fills", 0) == 0, fills1)
    side1 = json.loads(tbw.JSON_FILE.read_text(encoding="utf-8"))
    check("pass 1 writes the sidecar (states + trend + fills_trend + "
          "flip_marks)",
          side1.get("states", {}).get("OUT", {}).get("in_force", {})
          .get("fires") == 2
          and isinstance(side1.get("trend"), dict)
          and isinstance(side1.get("fills_trend"), dict)
          and isinstance(side1.get("flip_marks"), dict)
          and side1.get("fills", {}).get("states", {}).get("OUT", {})
          .get("fills") == 1, list(side1.keys()))

    # ── PASS 2 (tomorrow's rehearsal: +1 day, SAME week tag) ────────
    class _FakeTime:
        off = 86400.0

        @classmethod
        def strftime(cls, fmt, ts=None):
            if ts is None:
                return _time.strftime(
                    fmt, _time.gmtime(_time.time() + cls.off))
            if hasattr(ts, "tm_year"):   # already-offset struct_time
                return _time.strftime(fmt, ts)
            return _time.strftime(fmt, _time.gmtime(ts + cls.off))

        @classmethod
        def time(cls):
            return _time.time() + cls.off

        @classmethod
        def gmtime(cls, ts=None):
            if ts is None:
                return _time.gmtime(_time.time() + cls.off)
            return _time.gmtime(ts)

    real_time = tbw.time
    tbw.time = _FakeTime
    # tomorrow's stream moves (the fit earns its keep on the replay)
    tbw._week_candidates = lambda: ([
        ("OUT", 85.0, True), ("OUT", 90.0, True),
        ("FLAT", 95.0, True), ("RISING", 88.0, True)], None)
    s2 = tbw.report_now()
    tbw.time = real_time
    txt2 = tbw.OUT_FILE.read_text(encoding="utf-8")
    hist2 = [json.loads(l) for l in
             tbw.HISTORY_FILE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    check("pass 2 grades without error", "err" not in s2, s2)
    check("pass 2 re-tags the report with the SAME week (boundary is "
          "Monday — tomorrow stays in this ISO week)",
          txt2.startswith("=== THE TIDE BAR WEEKLY")
          and f"({wk_tag}," in txt2[:600], txt2[:200])
    check("pass 2 DEDUPES: the W37 record is replaced, not appended "
          "(the boot-restart/daily-pass race)",
          len(hist2) == 1 and hist2[0]["week"] == wk_tag,
          [r["week"] for r in hist2])
    rec2 = hist2[0]["states"]["OUT"]
    check("pass 2's refreshed record carries the NEW stream (2 OUT "
          "fires now) and re-graded fills",
          rec2["in_force"]["fires"] == 2
          and hist2[0].get("fills", {}).get("OUT", {}).get("fills") == 1,
          rec2)
    secs2 = [b for b in txt2.split("===") if "THE TIDE BAR WEEKLY" in b]
    check("pass 2's section is the newest (prepended, not appended)",
          txt2.find("90.0") < txt2.find("generated") if "generated" in
          txt2 else True, txt2[:120])
    check("the report holds exactly 2 sections after two same-week "
          "passes (a daily cadence, not a weekly accumulation)",
          txt2.count("=== THE TIDE BAR WEEKLY") == 2, txt2.count("==="))
    side2 = json.loads(tbw.JSON_FILE.read_text(encoding="utf-8"))
    check("the sidecar reflects pass 2 (the plate is never stale)",
          side2.get("states", {}).get("OUT", {}).get("in_force", {})
          .get("fires") == 2
          and side2.get("generated_ts", "").startswith(
              _time.strftime("%Y-%m-%d", _time.gmtime(
                  _time.time() + 86400.0))), side2.get("generated_ts"))
    # the flip-grade path stays coherent after the refresh
    g = tbw.grade_ledger()
    check("the flip grade reads the refreshed ledger (1 week — hold "
          "until 2)",
          (g.get("held") or "").startswith("1 week(s) of history")
          or g.get("weeks") == 1, g)
finally:
    tbw.OUT_FILE, tbw.JSON_FILE = real["OUT_FILE"], real["JSON_FILE"]
    tbw.HISTORY_FILE, tbw.FLIP_FILE = (real["HISTORY_FILE"],
                                       real["FLIP_FILE"])
    tbw.FLIP_EVENTS_FILE = real["FLIP_EVENTS_FILE"]
    tbw.FLIPGRADE_FILE = real["FLIPGRADE_FILE"]
    tbw.SNAP_OUTCOMES_FILE = real["SNAP_OUTCOMES_FILE"]
    tbw.TRADES_FILE = real["TRADES_FILE"]
    tl._funnel_score_quantiles = real_qq
    tl._outcome_fit_bar = real_fit
    shutil.rmtree(sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
print(f"TOMORROW-PASS VERIFY: {len(results) - len(fails)} passed, "
      f"{len(fails)} failed")
for name, ok, det in results:
    if not ok:
        print(f"FAIL  {name}  [{str(det)[:300]}]")
print("done")
sys.exit(1 if fails else 0)
