"""_dud_exit_reprice.py — WHAT LEFT-ON-THE-TABLE BECOMES ON THE SHIPPED EXIT.

The ledger's dud counterfactual used to price a buried dud as "buy at the burial
close, sell at the last in-window close". The regrade (_veto_exit_regrade.py)
showed that is not the trade the machine takes: the shipped stack takes a goal
bank, an armed trail turn or a hard stop, and that stack is worth several tenths
of a point per trade with a capped tail.

This re-prices every dud the ledger has actually recorded, under both bases, on
the same 1h candles the ledger reads:

  old  entry at the burial close, exit at the last in-window close
  new  the shipped stack (discipline._dud_exit): goal bank / armed trail /
       hard stop / window end, net of the measured round trip

Size per dud is the ledger's own `balance * goal / 100`; the balance is taken
from the row's `balance_after` (the row does not store the intra-rotation
balance, so levels are approximate while the old-vs-new delta is exact — both
bases use the identical size). The payer side is unchanged by construction: a
payer is booked at the goal, which is exactly what the goal bank banks.

Read-only: exchange candles + discipline_state.json. Writes nothing but its own
JSON.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discipline as dc                                     # noqa: E402

STATE = ROOT / "discipline_state.json"
OUT = ROOT / "scratch" / "_dud_exit_reprice.json"


def sched_for(day: datetime) -> str:
    return "weekend" if day.weekday() in (5, 6) else "weekday"


def hold_pct(exchange, pair, when, window_end, toll):
    """The OLD basis: entry at the burial close, exit at the last in-window close."""
    from datetime import timedelta
    try:
        since = int(when.timestamp() * 1000) - 3_600_000
        ohl = exchange.fetch_ohlcv(pair, timeframe="1h", since=since, limit=12)
    except Exception:
        return None
    if not ohl:
        return None
    entry = last = 0.0
    for c in ohl:
        try:
            ct = datetime.fromtimestamp(c[0] / 1000, tz=dc.MST)
            close = float(c[4])
        except (TypeError, ValueError, IndexError):
            continue
        if not close:
            continue
        if ct <= when < ct + timedelta(hours=1):
            entry = close
        elif when <= ct < window_end:
            last = close
    if not entry or not last:
        return None
    return (last / entry - 1.0) * 100.0 - toll


def load_supp_index():
    """Every suppression on disk, once: {pair: [(ts, gate)]} live + archive.

    discipline._suppressions_in_rotation reads ONE file; the live journal only
    holds the recent tail, so a rotation from a week ago needs the archive. The
    archive is 18 MB, so it is parsed ONCE and bucketed instead of per rotation.
    """
    idx: dict[str, list] = {}
    for name in ("popup_suppressions.jsonl", "popup_suppressions.archive.jsonl"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = datetime.fromisoformat(str(d.get("ts", "")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dc.MST)
                pair = str(d.get("pair") or "")
                if not pair:
                    continue
                idx.setdefault(pair, []).append((ts.astimezone(dc.MST),
                                                 str(d.get("gate") or "?")))
            except Exception:
                continue
    for v in idx.values():
        v.sort()
    return idx


def supp_in_window(idx, start, end):
    """The LAST suppression per pair inside [start, end) — the ledger's own rule."""
    out = {}
    for pair, rows in idx.items():
        hit = None
        for ts, gate in rows:
            if start <= ts < end:
                hit = (ts, gate)
        if hit:
            out[pair] = {"ts": hit[0], "gate": hit[1]}
    return out


def toll_of(pair):
    try:
        import spread_table as st
        return float(st.roundtrip_pct(pair))
    except Exception:
        return dc.DUD_TOLL_FALLBACK_PCT


def main() -> int:
    import ccxt
    rows = json.loads(STATE.read_text(encoding="utf-8")).get("history") or []
    rot_rows = [r for r in rows if str(r.get("rot") or "") not in ("", "DAY", "None")]
    ex = ccxt.kraken({"enableRateLimit": True})
    supp_idx = load_supp_index()
    print(f"suppression index: {len(supp_idx)} pairs", file=sys.stderr)
    out_rows = []
    for r in rot_rows[-26:]:
        duds = r.get("system_duds") or []
        if not duds:
            continue
        day = datetime.fromisoformat(str(r.get("date"))[:10]).replace(hour=12, tzinfo=dc.MST)
        start, end = dc._rot_window(str(r.get("rot")), day)
        if start is None:
            continue
        supp = supp_in_window(supp_idx, start, end)
        balance = float(r.get("balance_after") or 0.0)
        rec = {"date": r.get("date"), "rot": r.get("rot"),
               "payers": len(r.get("system_pairs") or []),
               "payers_usd": round(sum(float(p.get("usd") or 0)
                                       for p in (r.get("system_pairs") or [])), 2),
               "duds": {}, "dud_old": 0.0, "dud_new": 0.0,
               "priced": 0, "unpriced": 0, "reasons": {}}
        for d in duds:
            pair = d.get("pair") or ""
            entry = supp.get(pair) or {}
            ts = entry.get("ts")
            if not ts:
                rec["unpriced"] += 1
                continue
            goal = float(dc._rotation_goal_pct(pair, str(r.get("rot")), sched_for(ts)))
            size = balance * goal / 100.0
            toll = toll_of(pair)
            old = hold_pct(ex, pair, ts, end, toll)
            walk = dc._dud_exit(ex, pair, ts, end, goal)
            if old is None or walk is None:
                rec["unpriced"] += 1
                continue
            old_usd = size * old / 100.0
            new_usd = size * float(walk["pct"]) / 100.0
            rec["priced"] += 1
            rec["dud_old"] += old_usd
            rec["dud_new"] += new_usd
            rec["reasons"][walk["reason"]] = rec["reasons"].get(walk["reason"], 0) + 1
            rec["duds"][pair] = {"old_usd": round(old_usd, 2),
                                 "new_usd": round(new_usd, 2),
                                 "old_pct": round(old, 3),
                                 "new_pct": round(float(walk["pct"]), 3),
                                 "exit": walk["reason"]}
        rec["dud_old"] = round(rec["dud_old"], 2)
        rec["dud_new"] = round(rec["dud_new"], 2)
        rec["left_old"] = round(rec["payers_usd"] + rec["dud_old"], 2)
        rec["left_new"] = round(rec["payers_usd"] + rec["dud_new"], 2)
        out_rows.append(rec)

    tot = {"payers_usd": round(sum(r["payers_usd"] for r in out_rows), 2),
           "dud_old": round(sum(r["dud_old"] for r in out_rows), 2),
           "dud_new": round(sum(r["dud_new"] for r in out_rows), 2),
           "duds_priced": sum(r["priced"] for r in out_rows),
           "duds_unpriced": sum(r["unpriced"] for r in out_rows),
           "rotations": len(out_rows)}
    tot["left_old"] = round(tot["payers_usd"] + tot["dud_old"], 2)
    tot["left_new"] = round(tot["payers_usd"] + tot["dud_new"], 2)
    diffs = [d["new_pct"] - d["old_pct"] for r in out_rows for d in r["duds"].values()]
    tot["per_dud_pct_shift"] = round(statistics.fmean(diffs), 3) if diffs else None
    tot["duds_improved"] = sum(1 for x in diffs if x > 1e-9)
    tot["duds_hurt"] = sum(1 for x in diffs if x < -1e-9)
    payload = {"rule": "dud side repriced on the shipped exit stack",
               "basis": {"old": "entry at burial close, exit at last in-window close",
                         "new": "goal bank / armed trail / hard stop / window end"},
               "totals": tot, "rotations": out_rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"{'date':<12}{'rot':<7}{'payers':>7}{'payer $':>10}{'duds':>6}"
          f"{'dud old':>10}{'dud new':>10}{'left old':>11}{'left new':>11}  exits")
    for r in out_rows:
        print(f"{str(r['date'])[:10]:<12}{r['rot']:<7}{r['payers']:>7}"
              f"{r['payers_usd']:>10.0f}{r['priced']:>6}"
              f"{r['dud_old']:>10.0f}{r['dud_new']:>10.0f}"
              f"{r['left_old']:>11.0f}{r['left_new']:>11.0f}  {r['reasons']}")
    print("-" * 100)
    print(f"{'TOTAL':<12}{'':<7}{'':>7}{tot['payers_usd']:>10.0f}"
          f"{tot['duds_priced']:>6}{tot['dud_old']:>10.0f}{tot['dud_new']:>10.0f}"
          f"{tot['left_old']:>11.0f}{tot['left_new']:>11.0f}")
    print(f"\nduds repriced {tot['duds_priced']}, unpriced {tot['duds_unpriced']}; "
          f"per-dud shift {tot['per_dud_pct_shift']:+.3f} pts "
          f"({tot['duds_improved']} better, {tot['duds_hurt']} worse)")
    print(f"left on the table: {tot['left_old']:+.2f} -> {tot['left_new']:+.2f} "
          f"({tot['left_new'] - tot['left_old']:+.2f})")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
