"""The contest book: every constant fitted before 2026-10-19, then tested after it.

Two questions:
  state      -> is the gate open right now, and what are this contest's fixed lines?
  calibrate  -> refit the book and every constant on anchors whose 22-day window ends
                before TRAIN_CUT, then replay the frozen setting on the anchors that
                start after it. The second table is out-of-sample and was not used
                for any choice.

Read-only. It places no orders and writes no account state.
The book trades only the challenge's liquid universe (a $2,400 order must fit 1%
of the pair's median hourly dollar volume), so a fill is never filtered after
the fact. See CONTEST_BOOK.md for the measurements and the honest rate.
"""

from __future__ import annotations

import bisect
import heapq
import json
import math
import statistics as st
from datetime import timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
ACCOUNT = BASE / "contest_account.json"
BOOK_CACHE = BASE / "scratch" / "_books_liquid_contest.json"
STATE = BASE / "contest_book_state.json"

# ── frozen setting: fitted on pre-2026-10-19 anchors only (CONTEST_BOOK.md §9) ──
# THE BOOK TRADES THE LIQUID UNIVERSE: only the challenge pairs where a $2,400
# order is <= 1% of the pair's median hourly dollar volume, so every fill is one
# the account could actually have taken. The previous book filled 4.9% of its
# signals and threw the rest away at entry.
TRAIN_CUT = "2026-10-19"
ORDER_USD = 2400.0      # the order the universe is sized for
SIZE_FRAC = 0.18        # of current balance, per position
SLOTS = 4               # concurrent positions (72% max deployed)
GATE = 0.00             # no breadth gate: on the liquid universe it did not earn one
BOOK_STOP = 0.02        # stand down at -2% from the anchor
PARTICIPATION = 0.01    # order <= 1% of the pair's median hourly dollar volume
WIN_MULT, DEATH_MULT = 1.12, 0.97
CONTEST_DAYS = 22

# THE CANDIDATES: the ten policies re-resolved on the liquid pairs. Membership
# is the pre-cutoff rule (>=100 trades, positive mean) and it now has teeth.
CANDIDATES = [
    "L.pullback.risk_on.chandelier.9.any.us",
    "L.ignition.risk_on.fixed.9.any.any",
    "L.dip.risk_off.fixed.9.any.us",
    "L.pullback.risk_on.breakeven.9.any.any",
    "L.dip.risk_off.chandelier.9.any.us",
    "L.pullback.risk_off.breakeven.9.any.any",
    "L.dip.trend.chandelier.6.any.us",
    "L.ignition.risk_on.breakeven.9.any.any",
    "L.pullback.trend.fixed.6.any.any",
    "L.range_reclaim.risk_off.fixed.9.any.us",
]

# THE SHIPPED BOOK: the six the pre-cutoff rule kept.
BOOK = [
    "L.ignition.risk_on.fixed.9.any.any",
    "L.dip.risk_off.fixed.9.any.us",
    "L.dip.risk_off.chandelier.9.any.us",
    "L.dip.trend.chandelier.6.any.us",
    "L.ignition.risk_on.breakeven.9.any.any",
    "L.range_reclaim.risk_off.fixed.9.any.us",
]


def _tape():
    import edge_engine as ee
    return ee.load_pairs()


def breadth_series(pairs=None):
    """Share of pairs closing above their 21-hour EMA, per bar."""
    import numpy as np
    import pandas as pd
    pairs = pairs or _tape()
    num = None
    den = 0
    for df in pairs.values():
        c = df["close"].astype(float)
        up = (c > c.ewm(span=21, adjust=False).mean()).astype(float)
        up[df.index < df.index[0] + pd.Timedelta(hours=24)] = np.nan
        num = up if num is None else num.add(up, fill_value=0.0)
        den += 1
    return (num / den).dropna()


def gate_open(b=None) -> tuple[bool, dict]:
    b = breadth_series() if b is None else b
    now = float(b.iloc[-1])
    return now >= GATE, {
        "breadth": round(now, 4),
        "gate": GATE,
        "open": bool(now >= GATE),
        "bar": str(b.index[-1]),
        "change_24h": round(now - float(b.iloc[-24]), 4),
        "change_72h": round(now - float(b.iloc[-72]), 4),
        "percentile": round(float((b <= now).mean()) * 100.0, 1),
        "mean": round(float(b.mean()), 4),
        "open_share_of_hours": round(float((b >= GATE).mean()) * 100.0, 1),
    }


def contest_lines() -> dict:
    try:
        acct = json.loads(ACCOUNT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        acct = {}
    tier = float(acct.get("tier") or 10000.0)
    bal = float(acct.get("current_balance") or tier)
    return {"anchor": tier, "balance": round(bal, 2),
            "win": round(tier * WIN_MULT, 2), "death": round(tier * DEATH_MULT, 2),
            "gap_to_win": round(tier * WIN_MULT - bal, 2),
            "headroom": round(bal - tier * DEATH_MULT, 2),
            "contest_id": acct.get("contest_id")}


def state() -> dict:
    open_, g = gate_open()
    doc = {"gate": g, "lines": contest_lines(), "config": {
        "size_frac": SIZE_FRAC, "slots": SLOTS, "book": len(BOOK),
        "book_stop": BOOK_STOP, "participation": PARTICIPATION,
        "order_usd": ORDER_USD, "liquid_universe": True,
        "fitted_through": TRAIN_CUT}}
    if open_:
        doc["action"] = (f"deploy {SIZE_FRAC*100:.0f}% x {SLOTS} slots on the "
                         f"liquid universe (breadth gate off, it did not earn "
                         f"one), stand down at -{BOOK_STOP*100:.0f}%")
    else:
        doc["action"] = (f"gate CLOSED at breadth {g['breadth']:.2f} < {GATE:.2f} — stand "
                         f"down. There is no measured edge in a month this narrow.")
    STATE.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


# ── replay engine ────────────────────────────────────────────────────────

def _load():
    import numpy as np
    import pandas as pd
    books = json.loads(BOOK_CACHE.read_text(encoding="utf-8"))
    sig = []
    for pid, tr in books.items():
        for t, xt, net, pair, hold in tr:
            sig.append((pd.Timestamp(t), pd.Timestamp(xt), float(net), pair, int(hold), pid))
    sig.sort(key=lambda s: s[0])
    ts = np.array([s[0].tz_convert(None).to_datetime64() if s[0].tzinfo
                   else s[0].to_datetime64() for s in sig])
    pairs = _tape()
    b = breadth_series(pairs)
    bt = np.asarray(b.index.tz_convert(None) if b.index.tz is not None
                    else b.index).astype("datetime64[ns]")
    bv = b.to_numpy(float)
    pos = np.searchsorted(bt, ts, side="right") - 1
    b_sig = np.where((pos >= 0) & (pos < len(bv)), bv[np.clip(pos, 0, len(bv) - 1)], 0.0)
    dvol = {}
    for p, df in pairs.items():
        v = (df["close"].astype(float) * df["volume"].astype(float)).to_numpy(float)
        dvol[p] = float(np.nanmedian(v)) if len(v) else 0.0
    # THE SYMBOL SPELLING IS NOT UNIFORM: the edge tape keys "BTC/USD", the
    # contest cache keys "BTC". An exact-key lookup silently priced every bare
    # symbol at zero liquidity and the participation rule then refused every
    # order in the book — a zero-trade replay, not a result.
    def _dv(sym: str) -> float:
        for key in (sym, f"{sym}/USD", sym.replace("/USD", "")):
            if key in dvol:
                return float(dvol[key])
        return 0.0

    dv_sig = np.array([_dv(s[3]) for s in sig])
    return sig, ts, b_sig, dv_sig, b, bt, bv


def _roll(anchor, size, slots, gate, stop, sig, idx, its, b_sig, dv_sig):
    end = anchor + timedelta(days=CONTEST_DAYS)
    start = 10000.0
    bal = start
    guard = start * (1 - stop) if stop else None
    k = bisect.bisect_left(its, anchor.tz_convert(None).to_datetime64())
    heap, busy = [], set()
    n = 0
    hit = died = stopped = None
    while k < len(idx):
        j = idx[k]
        k += 1
        t, xt, net, pair, _hold, _pid = sig[j]
        if t >= end:
            break
        while heap and heap[0][0] <= t:
            e = heapq.heappop(heap)
            bal += e[3]
            busy.discard(e[4])
            n += 1
            if hit is None and died is None and stopped is None:
                if bal <= start * DEATH_MULT:
                    died = e[0]
                elif bal >= start * WIN_MULT:
                    hit = e[0]
                elif guard is not None and bal <= guard:
                    stopped = e[0]
                    heap.clear()
        if hit or died or stopped:
            break
        if len(heap) >= slots or pair in busy or xt >= end:
            continue
        if gate and b_sig[j] < gate:
            continue
        order = bal * size
        if dv_sig[j] * PARTICIPATION < order:
            continue
        busy.add(pair)
        heapq.heappush(heap, (xt, len(heap), t, order * net / 100.0, pair, _hold))
    if not (hit or died or stopped):
        while heap:
            e = heapq.heappop(heap)
            bal += e[3]
            n += 1
            if bal <= start * DEATH_MULT:
                died = e[0]
                break
            if bal >= start * WIN_MULT:
                hit = e[0]
                break
            if guard is not None and bal <= guard:
                stopped = e[0]
                break
    return {"hit": hit, "died": died, "stopped": stopped, "n": n, "final": bal}


def _run(anchors, cfg, sig, idx, its, b_sig, dv_sig):
    size, slots, gate, stop = cfg
    res = [_roll(a, size, slots, gate, stop, sig, idx, its, b_sig, dv_sig) for a in anchors]
    w = [r for r in res if r["hit"]]
    d = [r for r in res if r["died"]]
    s = [r for r in res if r["stopped"]]
    return {"res": res, "w": len(w), "d": len(d), "s": len(s),
            "flat": len(res) - len(w) - len(d) - len(s),
            "med_n": st.median([r["n"] for r in w]) if w else 0,
            "med_days": st.median([(r["hit"] - a).days
                                   for r, a in zip(res, anchors) if r["hit"]]) if w else 0}


def _wilson(k, n):
    if not n:
        return 0.0, 0.0
    z, p = 1.96, k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h) * 100, min(1.0, c + h) * 100


def select_book(sig, cut):
    """Membership rule: profitable and active before the cut. Nothing else."""
    import pandas as pd
    c = pd.Timestamp(cut)
    if c.tz is None:
        c = c.tz_localize("UTC")
    keep = []
    print(f"{'policy':52} {'pre n':>6} {'pre mean%':>10}", flush=True)
    for pid in CANDIDATES:
        n = [s[2] for s in sig if s[5] == pid and s[0] < c]
        mean = sum(n) / len(n) if n else 0.0
        ok = len(n) >= 100 and mean > 0
        print(f"{pid:52} {len(n):>6} {mean:>10.4f}  {'keep' if ok else 'drop'}", flush=True)
        if ok:
            keep.append(pid)
    return keep


def calibrate():
    import numpy as np
    import pandas as pd
    sig, ts, b_sig, dv_sig, b, bt, bv = _load()
    cut = pd.Timestamp(TRAIN_CUT, tz="UTC")
    keep = select_book(sig, cut)
    ks = set(keep)
    idx = np.array([i for i, s in enumerate(sig) if s[5] in ks])
    its = ts[idx]
    first = sig[0][0].tz_localize(None).normalize()
    last_entry = sig[-1][0].tz_localize(None).normalize()
    TRAIN = [a.tz_localize("UTC") for a in pd.date_range(
        first, cut.tz_localize(None) - timedelta(days=CONTEST_DAYS), freq="D")]
    TEST = [a.tz_localize("UTC") for a in pd.date_range(
        cut.tz_localize(None), last_entry - timedelta(days=CONTEST_DAYS), freq="D")]
    if sorted(keep) != sorted(BOOK):
        print(f"MEMBERSHIP MOVED: rule keeps {sorted(keep)} vs shipped {sorted(BOOK)}", flush=True)
    print("book kept %d/%d candidates   signals %d"
              % (len(keep), len(CANDIDATES), len(idx)), flush=True)
    print(f"train anchors {len(TRAIN)} ({TRAIN[0]:%Y-%m-%d}..{TRAIN[-1]:%Y-%m-%d}, "
          f"windows end <= {TRAIN_CUT})", flush=True)
    print(f"test  anchors {len(TEST)} ({TEST[0]:%Y-%m-%d}..{TEST[-1]:%Y-%m-%d}, "
          f"windows start >= {TRAIN_CUT})", flush=True)

    rows = []
    # sizes above 18% put a $2,688+ order on the book at the win line,
    # above the $2,400 the universe was built for - not on the menu.
    for size in (0.06, 0.12, 0.18):
        for slots in (2, 3, 4):
            for gate in (0.0, 0.30, 0.40, 0.50):
                for stop in (0.0, 0.010, 0.015, 0.020):
                    r = _run(TRAIN, (size, slots, gate, stop), sig, idx, its, b_sig, dv_sig)
                    rows.append({"size": size, "slots": slots, "gate": gate,
                                 "stop": stop, **r})
    pool = [r for r in rows if r["d"] == 0] or rows
    pool.sort(key=lambda r: (-r["w"], r["d"], r["med_n"], -r["stop"]))
    print(f"\nfitted {len(rows)} configs on {len(TRAIN)} train anchors; "
          f"best 6 with zero deaths:", flush=True)
    print(f"{'size':>5} {'slots':>5} {'gate':>5} {'stop':>5} {'won':>8} {'died':>5} "
          f"{'flat':>5} {'medN':>5}", flush=True)
    for r in pool[:6]:
        print(f"{r['size']*100:>4.0f}% {r['slots']:>5} {r['gate']:>5.2f} "
              f"{r['stop']*100:>4.0f}% {r['w']:>3}/{len(TRAIN):<4} {r['d']:>5} "
              f"{r['flat']:>5} {r['med_n']:>5.0f}", flush=True)

    ch = (pool[0]["size"], pool[0]["slots"], pool[0]["gate"], pool[0]["stop"])
    frozen = (SIZE_FRAC, SLOTS, GATE, BOOK_STOP)
    print(f"\ntrain-chosen {ch[0]*100:.0f}% x {ch[1]}, gate {ch[2]:.2f}, stop {ch[3]*100:.0f}%   "
          f"module frozen at {frozen[0]*100:.0f}% x {frozen[1]}, gate {frozen[2]:.2f}, "
          f"stop {frozen[3]*100:.0f}%", flush=True)

    def show(label, anchors, cfg, detail=False):
        r = _run(anchors, cfg, sig, idx, its, b_sig, dv_sig)
        lo, hi = _wilson(r["w"], len(anchors))
        print(f"{label:24} {r['w']:>3}/{len(anchors):<3} won {r['d']:>3} died {r['s']:>3} "
              f"stopped {r['flat']:>3} flat  {r['w']/len(anchors)*100:>5.1f}%  "
              f"[{lo:.0f}-{hi:.0f}%]  medN {r['med_n']:.0f}", flush=True)
        if detail:
            for a, x in zip(anchors, r["res"]):
                o = (f"WON  {x['hit']:%m-%d %H:%M}" if x["hit"] else
                     f"DEAD {x['died']:%m-%d %H:%M}" if x["died"] else
                     f"STOP {x['stopped']:%m-%d %H:%M}" if x["stopped"] else "flat")
                print(f"   {a:%Y-%m-%d}  {o:18} trades {x['n']:>4}  "
                      f"final ${x['final']:>9,.0f}", flush=True)
        return r

    print("\nIN-SAMPLE (what the fit saw)", flush=True)
    show("frozen / train", TRAIN, frozen)
    print("\nOUT-OF-SAMPLE (untouched)", flush=True)
    show("frozen / test", TEST, frozen, True)
    print("\nindependent, non-overlapping contests", flush=True)
    blocks = [pd.Timestamp(d, tz="UTC") for d in ("2026-09-03", "2026-09-25", "2026-10-17")]
    for a in blocks:
        x = _roll(a, *frozen, sig, idx, its, b_sig, dv_sig)
        o = (f"WON  {x['hit']:%m-%d}" if x["hit"] else
             f"DEAD {x['died']:%m-%d}" if x["died"] else
             f"STOP {x['stopped']:%m-%d}" if x["stopped"] else "flat")
        print(f"   {a:%m-%d}->{a + timedelta(days=CONTEST_DAYS):%m-%d}  {o:12} "
              f"trades {x['n']:>4}  final ${x['final']:>9,.0f}", flush=True)
    print("\nbreadth by period", flush=True)
    for name, lo, hi in (("train", "2026-09-03", "2026-09-28"),
                         ("gap", "2026-09-28", "2026-10-19"),
                         ("test", "2026-10-19", "2026-11-19")):
        m = (bt >= np.datetime64(lo)) & (bt < np.datetime64(hi))
        v = bv[m]
        print(f"   {name:6} mean {v.mean():.3f}  open@.30 {100*(v>=.30).mean():>5.1f}%  "
              f"open@.40 {100*(v>=.40).mean():>5.1f}%", flush=True)


def main() -> int:
    import sys
    mode = (sys.argv[1] if len(sys.argv) > 1 else "state").lower()
    if mode == "calibrate":
        calibrate()
        return 0
    doc = state()
    g, ln = doc["gate"], doc["lines"]
    print(f"breadth {g['breadth']:.3f}  gate >={g['gate']:.2f}  "
          f"{'OPEN' if g['open'] else 'CLOSED'}  ({g['percentile']:.0f}%ile, "
          f"24h {g['change_24h']:+.3f}, open {g['open_share_of_hours']:.1f}% of hours)")
    print(f"anchor ${ln['anchor']:,.2f}  balance ${ln['balance']:,.2f}  "
          f"win ${ln['win']:,.2f} (gap ${ln['gap_to_win']:,.2f})  "
          f"death ${ln['death']:,.2f} (headroom ${ln['headroom']:,.2f})")
    print(f"book {len(BOOK)} policies, {SIZE_FRAC*100:.0f}% x {SLOTS} slots, "
          f"stop {BOOK_STOP*100:.0f}%, participation {PARTICIPATION*100:.0f}%, "
          f"fitted through {TRAIN_CUT}")
    print(f"action: {doc['action']}")
    print(f"state written -> {STATE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
