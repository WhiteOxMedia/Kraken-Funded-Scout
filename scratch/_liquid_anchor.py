"""The anchor test on the LIQUID-UNIVERSE book, same split as CONTEST_BOOK.md §7.

Train = anchors whose 22-day window ends <= 2026-10-19. Test = anchors starting
>= 2026-10-19. Membership and every constant chosen on train only.
Grid caps size at 18%: a 24% order at the $11,200 win line is $2,688, above the
$2,400 the universe was built for, so those configs would be filtered again.
"""
import statistics as st
from pathlib import Path
import numpy as np, pandas as pd
import contest_book as cb

cb.BOOK_CACHE = Path("scratch/_books_liquid_contest.json")
sig, ts, b_sig, dv_sig, b, bt, bv = cb._load()
cut = pd.Timestamp(cb.TRAIN_CUT, tz="UTC")
keep = cb.select_book(sig, cut)
ks = set(keep)
idx = np.array([i for i, s in enumerate(sig) if s[5] in ks])
its = ts[idx]
first = sig[0][0].tz_localize(None).normalize()
last_entry = sig[-1][0].tz_localize(None).normalize()
TRAIN = [a.tz_localize("UTC") for a in pd.date_range(
    first, cut.tz_localize(None) - pd.Timedelta(days=cb.CONTEST_DAYS), freq="D")]
TEST = [a.tz_localize("UTC") for a in pd.date_range(
    cut.tz_localize(None), last_entry - pd.Timedelta(days=cb.CONTEST_DAYS), freq="D")]
print(f"\nsignals {len(sig):,} on liquid pairs   book kept {len(keep)}/{len(cb.BOOK)}", flush=True)
print(f"train anchors {len(TRAIN)} ({TRAIN[0]:%m-%d}..{TRAIN[-1]:%m-%d})   "
      f"test anchors {len(TEST)} ({TEST[0]:%m-%d}..{TEST[-1]:%m-%d})", flush=True)

rows = []
for size in (0.06, 0.12, 0.18):
    for slots in (2, 3, 4):
        for gate in (0.0, 0.30, 0.40, 0.50):
            for stop in (0.0, 0.010, 0.015, 0.020):
                r = cb._run(TRAIN, (size, slots, gate, stop), sig, idx, its, b_sig, dv_sig)
                rows.append({"size": size, "slots": slots, "gate": gate,
                             "stop": stop, **r})
pool = [r for r in rows if r["d"] == 0] or rows
pool.sort(key=lambda r: (-r["w"], r["d"], r["med_n"], -r["stop"]))
print(f"\nfitted {len(rows)} configs on {len(TRAIN)} train anchors; best 6 zero-death:", flush=True)
print(f"{'size':>5} {'slots':>5} {'gate':>5} {'stop':>5} {'won':>8} {'died':>5} "
      f"{'flat':>5} {'medN':>5}", flush=True)
for r in pool[:6]:
    print(f"{r['size']*100:>4.0f}% {r['slots']:>5} {r['gate']:>5.2f} "
          f"{r['stop']*100:>4.0f}% {r['w']:>3}/{len(TRAIN):<4} {r['d']:>5} "
          f"{r['flat']:>5} {r['med_n']:>5.0f}", flush=True)

ch = (pool[0]["size"], pool[0]["slots"], pool[0]["gate"], pool[0]["stop"])
frozen = (cb.SIZE_FRAC, cb.SLOTS, cb.GATE, cb.BOOK_STOP)
print(f"\ntrain-chosen {ch[0]*100:.0f}% x {ch[1]}, gate {ch[2]:.2f}, stop {ch[3]*100:.0f}%"
      f"   module frozen at {frozen[0]*100:.0f}% x {frozen[1]}, gate {frozen[2]:.2f}, "
      f"stop {frozen[3]*100:.0f}%", flush=True)

# every order the frozen book can place fits the universe by construction
max_order = 10000.0 * cb.WIN_MULT * cb.SIZE_FRAC
unfit = int((dv_sig[idx] * cb.PARTICIPATION < max_order).sum())
print(f"largest frozen order ${max_order:,.0f}; signals whose pair could NOT fill it: "
      f"{unfit}  (0 = no fill was filtered after the fact)", flush=True)

def show(label, anchors, cfg, detail=False):
    r = cb._run(anchors, cfg, sig, idx, its, b_sig, dv_sig)
    lo, hi = cb._wilson(r["w"], len(anchors))
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
for a in (pd.Timestamp(d, tz="UTC") for d in ("2026-09-03", "2026-09-25", "2026-10-17", "2026-10-19")):
    x = cb._roll(a, *frozen, sig, idx, its, b_sig, dv_sig)
    o = (f"WON  {x['hit']:%m-%d}" if x["hit"] else
         f"DEAD {x['died']:%m-%d}" if x["died"] else
         f"STOP {x['stopped']:%m-%d}" if x["stopped"] else "flat")
    print(f"   {a:%m-%d}->{a + pd.Timedelta(days=cb.CONTEST_DAYS):%m-%d}  {o:12} "
          f"trades {x['n']:>4}  final ${x['final']:>9,.0f}", flush=True)

print("\nTHE TRAIN-CHOSEN CONFIG, ALSO ON TEST (it was not frozen)", flush=True)
show("train-chosen / train", TRAIN, ch)
show("train-chosen / test", TEST, ch, True)
print("\nindependent contests", flush=True)
for a in (pd.Timestamp(d, tz="UTC") for d in ("2026-09-03", "2026-09-25", "2026-10-17", "2026-10-19")):
    x = cb._roll(a, *ch, sig, idx, its, b_sig, dv_sig)
    o = (f"WON  {x['hit']:%m-%d}" if x["hit"] else
         f"DEAD {x['died']:%m-%d}" if x["died"] else
         f"STOP {x['stopped']:%m-%d}" if x["stopped"] else "flat")
    print(f"   {a:%m-%d}->{a + pd.Timedelta(days=cb.CONTEST_DAYS):%m-%d}  {o:12} "
          f"trades {x['n']:>4}  final ${x['final']:>9,.0f}", flush=True)

print("\nWHAT THE FOUR DROPPED POLICIES WERE WORTH (pre-cutoff), for the record", flush=True)
for pid in cb.BOOK:
    if pid in ks:
        continue
    n = [s[2] for s in sig if s[5] == pid and s[0] < cut]
    print(f"   {pid:52} n {len(n):>3}  mean {sum(n)/len(n):+.3f}%", flush=True)
