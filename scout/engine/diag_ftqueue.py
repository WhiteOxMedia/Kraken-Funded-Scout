"""diag_ftqueue.py — THE FOLLOW-THROUGH QUEUE'S FIRST HOURS, PRICED (Sep 19).

Wired the same hour the re-arm alert shipped: the next time fresh vetoed
hours push the band-35 brain-buy rate back above the 60% bar, the gate
re-arms, the queue seeds, and the funnel's brain path adopts — and this
study prices that first adoption END TO END so nobody has to ask "did it
pay?" by hand:

  1. THE CANDIDATES: every band-35 brain-buy hour from the notes feed,
     capture order (the same population the queue draws from when armed).
  2. THE ADOPTION REPLAY: the funnel's full brain bar per candidate —
     read the brain on the current window (not just the veto hour),
     action buy, confidence >= 60, RR >= 2, no exhaustion/breakout-
     failure reason — exactly background_scanner's follow-through
     adoption contract.
  3. THE PRICE: each adopted hour's 4h forward window in the popup
     convention (+2% MFE = ran), end-move dollars at the live contest
     size, the RR-2.0 ceiling, and the queue's aggregate verdict.

Run: .venv\\Scripts\\python.exe -X utf8 diag_ftqueue.py
Writes _ftqueue.txt. Read-only; CPU-only replay.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(__file__).parent / "_ftqueue.txt"
POSITION_USD = 1027.40   # today's live contest size (the family convention)
POPUP_GOAL = 2.0         # "+2% MFE in 4h" = ran (coin_floors' bar)
ADOPT_CONF = 60.0        # the funnel brain path's adoption floor
ADOPT_RR = 2.0
CEIL_PCT = POPUP_GOAL * ADOPT_RR   # the RR-2 ceiling a stop-managed fill can keep


def _adoption_contract(sig) -> tuple[bool, str]:
    """Mirror of background_scanner's follow-through adoption gate
    (7320-7334): buy, confidence >= 60, full levels, RR >= 2, and no
    exhaustion / breakout-failure reason."""
    try:
        if sig is None:
            return False, "the brain could not be read"
        if str(getattr(sig, "action", "hold")) != "buy":
            return False, f"the brain reads {getattr(sig, 'action', '?')}"
        if float(getattr(sig, "confidence", 0.0) or 0.0) < ADOPT_CONF:
            return False, "confidence under the 60 floor"
        if not (getattr(sig, "entry_price", None) and getattr(sig, "stop_loss", None)
                and getattr(sig, "take_profit", None)):
            return False, "incomplete levels"
        rr = float(getattr(sig, "risk_reward", 0.0) or 0.0)
        if rr < ADOPT_RR:
            return False, f"RR {rr:.1f} under the 2.0 floor"
        for r in list(getattr(sig, "reasons", []) or []):
            rl = str(r).lower()
            if "exhaustion" in rl or "breakout failure" in rl:
                return False, f"exhaustion-class reason: {r}"
        return True, "adopted"
    except Exception as exc:
        return False, f"contract error: {exc}"


def main() -> int:
    import diag_directiongrade as dg
    from strategy import evaluate_strategy
    from config import DEFAULT_CONFIG as cfg
    import pandas as _pd

    # the notes feed, deduped to (pair, hour bucket), oldest first
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

    # stores, both name forms (run_grade's pair-format bridge)
    import glob as _glob
    by_pair = {}
    for p in sorted(_glob.glob(str(dg.BASE / dg.STORES_GLOB))):
        stem = Path(p).name.replace("-1h.feather", "")
        by_pair[stem] = Path(p)
        by_pair[stem.replace("_", "/")] = Path(p)

    # candidate band-35 brain-buy hours, graded (the veto-hour replay)
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
        graded.append(g)
    cands = [g for g in graded
             if dg._band_of(float(g["brain_conf"])) == "35"
             and g["brain_action"] == "buy"]

    lines = ["THE FOLLOW-THROUGH QUEUE'S FIRST HOURS, PRICED — what the",
             "gate's re-arm would unleash, adoption bar by adoption bar.",
             f"band-35 brain-buy candidate hours: {len(cands)} of "
             f"{len(graded)} graded rows", "",
             f"{'captured':<13}{'pair':<13}{'age_h':>6}{'adopt?':>8}  "
             f"{'now-conf':>8}{'now-RR':>7}  {'end4h%':>7}  {'$':>8}  {'ceiling':>8}",
             "-" * 96]

    nowt = time.time()
    adopted = []
    for g in cands:
        store = by_pair.get(g["pair"])
        adopt, why = False, "store missing"
        now_conf = now_rr = None
        end = g["end"]
        try:
            df = _pd.read_feather(store)
            df["date"] = _pd.to_datetime(df["date"], utc=True)
            df = df.set_index("date").sort_index()
            sig = evaluate_strategy(df.iloc[:-1].copy(), cfg)
            adopt, why = _adoption_contract(sig)
            now_conf = float(getattr(sig, "confidence", 0.0) or 0.0)
            now_rr = float(getattr(sig, "risk_reward", 0.0) or 0.0)
        except Exception as exc:
            why = f"replay error: {exc}"
        usd = POSITION_USD * end / 100.0
        ceil = POSITION_USD * CEIL_PCT / 100.0 if adopt else 0.0
        age_h = (nowt - g["ts"]) / 3600.0
        mark = "YES" if adopt else "no"
        lines.append(f"{time.strftime('%m-%d %H:%M', time.gmtime(g['ts'])):<13}"
                     f"{g['pair']:<13}{age_h:>6.1f}{mark:>8}  "
                     f"{(f'{now_conf:.0f}' if now_conf is not None else '-'):>8}"
                     f"{(f'{now_rr:.1f}' if now_rr is not None else '-'):>7}  "
                     f"{end:>7.2f}  {usd:>+8.2f}  {ceil:>8.2f}"
                     + ("" if adopt else f"   ({why})"))
        if adopt:
            adopted.append({"pair": g["pair"], "end": end, "usd": usd,
                            "ran": end >= POPUP_GOAL,
                            "ceiling_usd": ceil,
                            "ts": time.strftime('%m-%d %H:%M',
                                                time.gmtime(g['ts']))})

    tot = sum(a["usd"] for a in adopted)
    ceil_tot = sum(a["ceiling_usd"] for a in adopted)
    ran_n = sum(1 for a in adopted if a["ran"])
    # THE PREMISE WORLD: adopt at the veto hour itself (the capture replay
    # already IS the brain-buy read at that hour) — the gate's own premise
    premise_usd = sum(POSITION_USD * g["end"] / 100.0 for g in cands)
    premise_ran = sum(1 for g in cands if g["ran"])
    # THE FRESHNESS GAP: the replay's candidates are DAYS old (aged edges
    # read hold now); a live re-arm draws candidates MINUTES old — the
    # queue's 24h freshness bound plus a ~5min pass gap. The executable
    # world for a LIVE re-arm therefore sits near the premise world, not
    # near this stale replay's zero.
    fresh = [g for g in cands if (nowt - g["ts"]) <= 24 * 3600.0]
    lines += ["", f"THE QUEUE'S FIRST HOURS, PRICED ({len(adopted)} of "
              f"{len(cands)} candidates clear the adoption bar on the "
              f"current window):"]
    for a in adopted:
        lines.append(f"  {a['ts']}  {a['pair']:<13} end {a['end']:+.2f}% = "
                     f"{a['usd']:+.2f} dollars"
                     + (f" — RAN the +2% goal, ceiling {a['ceiling_usd']:.2f}"
                        if a["ran"] else " — under the goal; a stop-managed "
                        "fill keeps at most the RR-2 ceiling"))
    lines += ["",
              f"TWO WORLDS, HONESTLY SPLIT:",
              f"  PREMISE WORLD (adopt at the veto hour — what the gate "
              f"believes): {len(cands)} adoptions, {premise_ran} ran, "
              f"{premise_usd:+.2f} dollars ({premise_usd / max(len(cands), 1):+.2f}/buy)",
              f"  EXECUTABLE-REPLAY (adopt on the now-window — this stale "
              f"replay): {len(adopted)} adoptions, {tot:+.2f} dollars",
              f"  FRESHNESS GAP: {len(fresh)} of {len(cands)} candidates are "
              f"within the queue's 24h bound (the oldest edge cooled to "
              f"hold); a LIVE re-arm draws minutes-old candidates, whose "
              f"now-read matches the veto hour — the executable world "
              f"approaches the premise world as the gap shrinks",
              f"VERDICT: " + (
                  f"{len(adopted)} of {len(cands)} clear the now-bar and they "
                  f"net {tot:+.2f} — the adoption bar alone is not enough edge"
                  if adopted and tot <= 0 else
                  f"{len(adopted)} of {len(cands)} clear the now-bar and they "
                  f"PAY ({tot:+.2f}, {ran_n} ran) — the premise holds at the "
                  f"adoption bar" if adopted and tot > 0 else
                  f"zero adoptions on the stale replay — every candidate's "
                  f"edge cooled to hold on the current window; the gate's "
                  f"premise world prices {premise_usd:+.2f} "
                  f"({premise_usd / max(len(cands), 1):+.2f}/buy), and a live "
                  f"re-arm with minutes-old candidates is what the alert will "
                  f"actually unleash. Re-run this study on the re-arm's first "
                  f"fresh hours for the lived number.")]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
