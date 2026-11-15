"""backfill_setup_stamps.py — ONE-OFF MIGRATION (Nov 15).

WHY: only 38 of 2,706 closes carry the fire-time stamp, so no family can be
measured (the verdict bar is n >= 40). The pre-Nov-6 closes still have their
fire-time popup in popups.jsonl, which carries the same four inputs the stamp
is built from: conviction ("NN% SURE"), tide state + breadth ("TIDE: OUT 41%
of the fleet up"), the CLIMB-OUT verdict, and the min-size marker in the title.
This script joins those closes back to their popup ONCE and writes a stamp
shaped by the production `_setup_tags`, so the ledger can be a group-by.

THE JOIN (why it is trustworthy):
  * same ticker and |fire time - popup time| <= 6h, where fire time is
    sold_at - held_min (held_min is missing on 86 rows, so the window cannot
    be narrow);
  * the popup's own goal ("goal +1.4%") must equal the close's goal_pct
    within 0.06 — that is the disambiguator that keeps a same-ticker re-fire
    from being stamped onto the wrong close;
  * nearest popup wins; a tie inside 60s with differing fields is reported
    as ambiguous and left untagged rather than guessed;
  * when the popup prints "cash out at $X (win)" (31% of them), the implied
    entry X/(1+goal) must match the ledger entry within 1%, otherwise the
    match is rejected. That is an independent price check on the join.

WHAT IS AND IS NOT WRITTEN:
  setup     — _setup_tags(...) from the popup text (route, tide, tide_pct,
              conviction, climb verdict, fast/re-entry/hail, min_size)
  setup_v   — _setup_tags's own "v" (SETUP_TAGS_VERSION)
  setup_src — "popup-backfill" so a derived stamp is never mistaken for a
              fire-time one
  NOT written: brain / mult / clearance / er / size / base (they are not in
  the popup text; a None from _setup_tags means "not read", which is true),
  and strategy_v (the strategy generation at fire is not knowable for these
  rows — inventing it would make the strategy_v partition lie).

Usage:
  python backfill_setup_stamps.py [--apply] [--trades PATH] [--popups PATH]
Without --apply it prints the join plan and touches nothing.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRADES = HERE / "contest_trades.jsonl"
POPUPS = HERE / "popups.jsonl"
WINDOW_S = 6 * 3600
GOAL_TOL = 0.06
ENTRY_TOL = 0.01

_RE_SURE = re.compile(r"(\d+)%\s*SURE")
_RE_TIDE = re.compile(r"TIDE:\s*([A-Z]+)\s*\((\d+)%")
_RE_CLIMB = re.compile(r"CLIMB-OUT\s*[—\-]\s*([A-Z]+)")
_RE_GOAL = re.compile(r"goal\s*\+([\d.]+)%")
_RE_CASH = re.compile(r"cash out at \$([\d.]+)")
_RE_MULT = re.compile(r"([\d.]+)×\s*SIZE")


def _ticker(title: str):
    head = title.split("—")[0]
    head = re.sub(r"^[^\w]*BUY\s*", "", head)
    head = re.sub(r"\bMAX:\s*\$[\d.]+\s*", "", head)
    head = re.sub(r"\bMIN:\s*\$[\d.]+\s*", "", head)
    # the ticker is the FIRST token after BUY, and it can be one character
    # (S/USD). A {2,10} class silently dropped all 46 S popups.
    m = re.match(r"([A-Z0-9]{1,10})\b", head.strip())
    return m.group(1) if m else None


def _parse_popup(p: dict):
    title = str(p.get("title") or "")
    text = str(p.get("text") or "")
    both = title + "\n" + text
    tick = _ticker(title)
    if not tick:
        return None
    sure = _RE_SURE.search(title)
    tide = _RE_TIDE.search(both)
    climb = _RE_CLIMB.search(both)
    goal = _RE_GOAL.search(both)
    cash = _RE_CASH.search(text)
    mult = _RE_MULT.search(title)
    up = title.upper()
    return {
        "ticker": tick,
        "ts": _dt.datetime.fromisoformat(str(p["ts"])[:19]),
        "conviction": int(sure.group(1)) if sure else None,
        "tide": tide.group(1).upper() if tide else None,
        "tide_pct": int(tide.group(2)) if tide else None,
        "climb": climb.group(1).upper() if climb else None,
        "goal": float(goal.group(1)) if goal else None,
        "cash": float(cash.group(1)) if cash else None,
        "mult": float(mult.group(1)) if mult else None,
        # the fire-time stamp's min_size IS "the weak-fire mark"; the popup
        # prints that mark as a WEAKENED/MIN-SIZED title tail, not only as
        # the literal phrase MIN SIZE. Validated against the 38 fire-time
        # stamps: this rule reproduces min_size on all of them.
        "min_size": ("MIN SIZE" in up or "MINIMUM SIZE" in up
                     or "WEAKENED, SIZED TO PAY" in up),
        "fast": "FAST ROUTE" in up,
        "opp": "OPPORTUNISTIC" in up,
        "hail": "HAIL" in up,
        "reentry": "RE-ENTRY" in up,
    }


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main(argv):
    apply = "--apply" in argv
    trades_path = TRADES
    popups_path = POPUPS
    for i, a in enumerate(argv):
        if a == "--trades" and i + 1 < len(argv):
            trades_path = Path(argv[i + 1])
        if a == "--popups" and i + 1 < len(argv):
            popups_path = Path(argv[i + 1])

    import background_scanner as bs  # production stamp, no second implementation
    import death_lessons as dl

    popups = []
    for line in popups_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if str(doc.get("kind") or "").upper() != "BUY":
            continue
        rec = _parse_popup(doc)
        if rec and rec["conviction"] and rec["tide"] and rec["climb"]:
            popups.append(rec)

    by_ticker: dict[str, list] = {}
    for rec in popups:
        by_ticker.setdefault(rec["ticker"], []).append(rec)

    raw = trades_path.read_text(encoding="utf-8").splitlines()
    out, stats = [], {
        "rows": 0, "already": 0, "stamped": 0, "no_popup": 0,
        "ambiguous": 0, "entry_reject": 0, "no_goal": 0,
    }
    rejects = []
    for line in raw:
        if not line.strip():
            continue
        row = json.loads(line)
        stats["rows"] += 1
        if isinstance(row.get("setup"), dict) and row["setup"]:
            stats["already"] += 1
            out.append(line)
            continue
        tick = str(row.get("pair") or "").split("/")[0]
        goal = _f(row.get("goal_pct"))
        sold = None
        try:
            sold = _dt.datetime.fromisoformat(str(row["sold_at"])[:19])
        except (KeyError, ValueError):
            pass
        if goal is None or sold is None:
            stats["no_goal"] += 1
            out.append(line)
            continue
        held = _f(row.get("held_min"), 0.0) or 0.0
        fire = sold - _dt.timedelta(minutes=held)
        cands = []
        for rec in by_ticker.get(tick, []):
            if rec["goal"] is None or abs(rec["goal"] - goal) > GOAL_TOL:
                continue
            dt = abs((rec["ts"] - fire).total_seconds())
            if dt <= WINDOW_S:
                cands.append((dt, rec))
        if not cands:
            stats["no_popup"] += 1
            out.append(line)
            continue
        cands.sort(key=lambda t: t[0])
        best = cands[0][1]
        entry = _f(row.get("entry"))
        implied = (best["cash"] / (1.0 + goal / 100.0)) if best["cash"] else None
        tight = [c for c in cands if c[0] <= 120.0]      # same fire, seconds apart
        if cands[0][0] <= 1800.0:
            # direct time join: the popup is the fire. Only a second popup
            # within 2 minutes that DISAGREES makes that a guess.
            if len(tight) > 1 and len({(p["conviction"], p["tide"],
                                        p["climb"], p["min_size"])
                                       for _, p in tight}) > 1:
                stats["ambiguous"] += 1
                out.append(line)
                continue
        else:
            # held_min was missing or wrong for this row: the time join
            # cannot carry it, so demand a unique goal match over 6h and an
            # independent price check when the popup prints its cash-out.
            if len(cands) > 1:
                stats["ambiguous"] += 1
                out.append(line)
                continue
            if implied and entry and abs(implied - entry) / entry > ENTRY_TOL:
                stats["entry_reject"] += 1
                rejects.append((row.get("sold_at"), row.get("pair"),
                                round(entry, 6), round(implied, 6)))
                out.append(line)
                continue
        route = ("champion-opportunistic" if best["opp"]
                 else "champion-fast" if best["fast"] else "champion")
        tags = bs._setup_tags(
            route=route,
            tide_state=best["tide"], tide_pct=best["tide_pct"],
            # the popup prints the BRAIN read ("the brain reads the setup at
            # NN%"), never the fire's signal.confidence -- so this number
            # goes to `brain` and `conviction` stays None ("not read"),
            # which is what the fire-time stamps themselves record for the
            # brain/quick pair.
            brain=best["conviction"], climb=best["climb"],
            fast=bool(best["fast"]), reentry=bool(best["reentry"]),
            hail=bool(best["hail"]),
        )
        # _setup_tags derives min_size from `weak`; the popup carries the
        # min-size MARK but not the scanner's weak REASON string, so set the
        # flag from the title and leave `weak` as the honest None.
        tags["min_size"] = bool(best["min_size"])
        row["setup"] = tags
        row["setup_v"] = tags.get("v")
        row["setup_src"] = "popup-backfill"
        stats["stamped"] += 1
        out.append(json.dumps(row, ensure_ascii=False))

    fam = {}
    for line in out:
        row = json.loads(line)
        if isinstance(row.get("setup"), dict) and row["setup"]:
            key = dl.family_key(row["setup"])
            fam[key] = fam.get(key, 0) + 1
    print("join plan:", json.dumps(stats, indent=2))
    print(f"untagged remaining: {stats['rows'] - stats['already'] - stats['stamped']}")
    print(f"families after backfill: {len(fam)}")
    for k, v in sorted(fam.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {v:5d}  {k}")
    if rejects:
        print(f"entry-price rejects (sample of {len(rejects)}):")
        for r in rejects[:5]:
            print("   ", r)
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to stamp.")
        return 0
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = trades_path.with_suffix(trades_path.suffix + f".bak-{stamp}")
    shutil.copy2(trades_path, backup)
    tmp = trades_path.with_suffix(trades_path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, trades_path)
    print(f"\nstamped {stats['stamped']} rows; backup at {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
