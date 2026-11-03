"""diag_hodholds.py — THE HOLDS-RAN HOUR-OF-DAY SPLIT (Sep 19).

QUESTION (user, Sep 19): the brain-holds that ran at the newest vetoed
hours — do they cluster in a SPECIFIC rotation window? The fleet's
rotations are 4h Denver-local (background_scanner.rotation_key_for_hour),
so the hour-of-day split uses the SAME 4h buckets the fleet trades on:
00-04, 04-08, ... 20-24 America/Denver.

METHOD: replay every captured veto hour through the grade's machinery
(the closed-bar guard included — final numbers only), take the
brain-HOLD rows (the veto's confirmations AND its refused money when
they run), and bucket by Denver-local rotation window: n, ran-rate,
dollars per window. Three views: all graded rows, the newest day (the
"newest vetoed hours"), and band-35 alone (the follow-through gate's
own band, whose 9-of-12 holds-ran prompted this split).

Run: .venv\\Scripts\\python.exe -X utf8 diag_hodholds.py
Writes _hodholds.txt. Read-only; CPU-only replay.
"""
from __future__ import annotations

import json
import sys
import time
import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(__file__).parent / "_hodholds.txt"
DENVER = ZoneInfo("America/Denver")


def rot_window(ts_epoch: float) -> str:
    """The Denver-local 4h rotation window a timestamp lives in — the
    same clock the fleet's rotation_key_for_hour trades on."""
    h = _dt.datetime.fromtimestamp(ts_epoch, DENVER).hour
    start = (h // 4) * 4
    return f"{start:02d}-{(start + 4) % 24:02d} MT"


def bucket_holds(rows: list[dict]) -> dict[str, dict]:
    """Pure bucketing: hold rows -> per-window aggregates. Pinnable."""
    out: dict[str, dict] = {}
    for r in rows:
        w = rot_window(r["ts"])
        b = out.setdefault(w, {"n": 0, "ran": 0, "usd": 0.0, "pairs": []})
        b["n"] += 1
        if r.get("ran"):
            b["ran"] += 1
        b["usd"] += r.get("usd", 0.0)
        b["pairs"].append(r.get("pair", "?"))
    for b in out.values():
        b["usd"] = round(b["usd"], 2)
        b["rate"] = round(b["ran"] / b["n"], 2) if b["n"] else 0.0
    return out


def _table(title: str, rows: list[dict], pos: float) -> list[str]:
    buckets = bucket_holds(rows)
    tot_n = len(rows)
    tot_ran = sum(1 for r in rows if r.get("ran"))
    lines = ["", title,
             f"  holds: {tot_n}, ran {tot_ran} "
             f"({(tot_ran / tot_n * 100 if tot_n else 0):.0f}%)",
             f"  {'window':<12}{'n':>4}{'ran':>5}{'rate':>7}{'$':>10}   pairs that ran"]
    for w in sorted(buckets):
        b = buckets[w]
        ran_pairs = [p for r, p in zip(
            [x for x in rows if rot_window(x["ts"]) == w], b["pairs"])
            if r.get("ran")]
        lines.append(f"  {w:<12}{b['n']:>4}{b['ran']:>5}{b['rate']:>7.2f}"
                     f"{b['usd']:>+10.2f}   {', '.join(sorted(set(ran_pairs))) or '-'}")
    return lines


def main() -> int:
    import diag_directiongrade as dg
    dg.POSITION_USD = dg._position_usd()   # the live contest size, not the fallback

    # the notes feed, deduped, replayed with the closed-bar guard
    rows: list[dict] = []
    if dg.NOTES.exists():
        for line in dg.NOTES.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and d.get("pair"):
                    rows.append(d)
            except Exception:
                continue
    seen: set[tuple] = set()
    notes = []
    for d in rows:
        try:
            ts = time.mktime(time.strptime(str(d["ts"])[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        key = (str(d["pair"]), int(ts // 3600))
        if key in seen:
            continue
        seen.add(key)
        d = dict(d)
        d["_ts"] = float(ts)
        notes.append(d)
    notes.sort(key=lambda x: x["_ts"])

    import glob as _glob
    by_pair = {}
    for p in sorted(_glob.glob(str(dg.BASE / dg.STORES_GLOB))):
        stem = Path(p).name.replace("-1h.feather", "")
        by_pair[stem] = Path(p)
        by_pair[stem.replace("_", "/")] = Path(p)

    graded = []
    for d in notes:
        store = by_pair.get(d["pair"])
        if store is None:
            continue
        g = dg._grade_one(store, d["_ts"])
        if g is None:
            continue
        g["pair"] = d["pair"]
        g["ts"] = d["_ts"]
        g["usd"] = round(dg.POSITION_USD * g["end"] / 100.0, 2)
        g["band"] = dg._band_of(float(g["brain_conf"]))
        graded.append(g)

    now = time.time()
    holds_all = [g for g in graded if g["brain_action"] != "buy"]
    holds_today = [g for g in holds_all if (now - g["ts"]) <= 24 * 3600.0]
    holds_b35 = [g for g in holds_all if g["band"] == "35"]

    lines = ["THE HOLDS-RAN HOUR-OF-DAY SPLIT — do the brain-holds that ran",
             "cluster in a specific rotation window? (4h Denver-local, the",
             f"fleet's own rotation clock | position ${dg.POSITION_USD:,.2f})"]

    lines += _table("VIEW 1 — ALL GRADED VETOED HOURS (Sep 16-19):",
                    holds_all, dg.POSITION_USD)
    lines += _table("VIEW 2 — THE NEWEST VETOED HOURS (last 24h):",
                    holds_today, dg.POSITION_USD)
    lines += _table("VIEW 3 — BAND-35 ONLY (the gate's own band):",
                    holds_b35, dg.POSITION_USD)

    # the cluster verdict: the window with the most ran rows, vs uniform
    buckets = bucket_holds(holds_all)
    tot_ran = sum(1 for r in holds_all if r.get("ran"))
    if buckets and tot_ran:
        top_w, top_b = max(buckets.items(), key=lambda kv: kv[1]["ran"])
        share = top_b["ran"] / tot_ran
        uniform = 1.0 / len(buckets)
        lines += ["",
                  f"CLUSTER CHECK: {top_b['ran']} of {tot_ran} ran-holds sit in "
                  f"{top_w} ({share * 100:.0f}% — uniform would be "
                  f"{uniform * 100:.0f}% across {len(buckets)} windows); "
                  f"that window's ran-rate {top_b['rate']:.2f} vs overall "
                  f"{(tot_ran / max(len(holds_all), 1)):.2f}",
                  "CAVEAT: " + (
                      "the sample is thin (n=" + str(len(holds_all)) + ") — read "
                      "this as a lead, not a law; the 4h window shape matches "
                      "the fleet's rotation clock, so a real cluster means the "
                      "veto leaks in a specific part of the day" )]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
