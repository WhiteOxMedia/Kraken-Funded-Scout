r"""diag_fbw3.py - would FLIP_BACK_WEEKS=3 have saved FLAT's re-arm?

A counterfactual question about the regime machinery's own constant.
Arc 3 of diag_flipgrade.py (FLAT: the fit re-armed fast, bled again,
re-flipped) re-armed after TWO contract-legal cf-winning weeks - the
shipped FLIP_BACK_WEEKS=2. The counterfactual replay re-walks the
same records with the gate constant at 3 - and the answer is sharper
than a delay: the arc's recovery was EXACTLY two weeks, so FBW=3
denies the re-arm entirely (no third winning week ever comes).

READ-ONLY: the trail, the flips ledger, and the weekly txt are never
touched (every decision walk is in-memory; the trail's bytes are
checked identical at the end).
Run: .venv\Scripts\python.exe -X utf8 diag_fbw3.py
"""
import json
from pathlib import Path

import tidebar_weekly as tbw

ROOT = Path(__file__).parent
fi, fo = 82, 46   # the fit's fires live / benched (the arc-3 stream)
PASS = 0


def check(name, cond, detail=""):
    global PASS
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        print("      detail:", str(detail)[:600])
    PASS += bool(cond)


def wk(tag, live, other, src="quantile", state="FLAT"):
    return {"week": tag, "generated_ts": f"{tag} pass",
            "states": {state: {
                "n": fi,
                "in_force": {"fires": fi, "payers": 3, "rate": 0.3,
                             "usd": round(fi * live, 2),
                             "usd_per_fire": round(live, 2)},
                "other": {"fires": fo, "payers": 3, "rate": 0.3,
                          "usd": round(fo * other, 2),
                          "usd_per_fire": round(other, 2)},
                "other_src": src}}}


# arc 3 (diag_flipgrade's seed), re-declared VERBATIM - the arc's own
# stream is the object of study, including its synthetic W37 (the real
# ledger's W37 FLAT slice is one-sided, which would reset the streak
# and miss the arc's opening flip).
recs = [wk("2026-W37", 3.00, 3.40),
        wk("2026-W38", 2.90, 3.50),
        wk("2026-W39", 2.80, 3.65),
        wk("2026-W40", 2.40, 3.55, src="outcome-fitted"),
        wk("2026-W41", 2.40, 3.45, src="outcome-fitted"),
        wk("2026-W42", 3.30, 4.55),
        wk("2026-W43", 3.20, 4.35),
        wk("2026-W44", 3.10, 4.45)]

_trail_bytes = (ROOT / "tide_bar_flip_events.jsonl").read_bytes()


def walk(flip_back_weeks):
    """Re-run the shipped gate on the arc with the given constant.
    Read-only: _evaluate_flips mutates only its own local copy."""
    real_fb, real_fw = tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS
    tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS = flip_back_weeks, 3
    try:
        events, fl = [], {}
        for i, rec in enumerate(recs):
            evs_w, fl = tbw._evaluate_flips(recs[:i + 1], fl)
            e = evs_w.get("FLAT")
            if e:
                events.append(dict(e, week=rec["week"]))
        return events
    finally:
        tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS = real_fb, real_fw

print("=== THE FLIP-BACK CONSTANT, REPLAYED (FLAT's arc 3) ===")
print()

evs2 = walk(2)
check("at FBW=2 the arc fires flip / flip-back / re-flip",
      [(e["action"], e["week"]) for e in evs2]
      == [("flip", "2026-W39"), ("flip_back", "2026-W41"),
          ("flip", "2026-W44")], evs2)

evs3 = walk(3)
check("at FBW=3 the flip is unchanged (W39) and the RE-ARM NEVER "
      "COMES (the recovery was exactly two weeks)",
      [(e["action"], e["week"]) for e in evs3]
      == [("flip", "2026-W39")], evs3)

# ── the dollars: the FBW=2 re-arm's governed window is W42-W44 ─────
# (the re-arm fired W41's close; the fit streamed live until the
# W44 re-flip). Realized = the fit in charge; counterfactual = the
# quantile that FBW=3 would have kept shipping all three weeks.
fit_w = [recs[i]["states"]["FLAT"]["in_force"] for i in (5, 6, 7)]
q_w = [recs[i]["states"]["FLAT"]["other"] for i in (5, 6, 7)]
ff = sum(x["fires"] for x in fit_w)
fu = sum(x["usd"] for x in fit_w)
qf = sum(x["fires"] for x in q_w)
qu = sum(x["usd"] for x in q_w)
fp, qp = round(fu / ff, 2), round(qu / qf, 2)
gap = round(qp - fp, 2)
eff_foregone = round(gap * ff, 2)
raw_edge = round(fu - qu, 2)

check("the re-arm's window realized the fit at $3.20/fire vs the "
      "quantile's $4.45/fire (COST on the contract axis)",
      fp == 3.20 and qp == 4.45 and gap > 0, (fp, qp, gap))
check("the shipped gate constants are restored (FBW=2, FW=3)",
      tbw.FLIP_BACK_WEEKS == 2 and tbw.FLIP_WEEKS == 3,
      (tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS))
check("the audit trail was never touched (byte-identical)",
      (ROOT / "tide_bar_flip_events.jsonl").read_bytes() == _trail_bytes)

print()
print("-- THE COUNTERFACTUAL, IN DOLLARS --")
print("  shipped (FBW=2): the fit re-armed at W41 on a +0.26 pool")
print("    then streamed live W42-W44 (the second bleed) until the")
print("    re-flip at W44:")
print(f"      realized (the fit):    ${fu:+,.2f} over {ff} fires "
      f"(${fp:+.2f}/fire)")
print(f"      counterfactual (quantile): ${qu:+,.2f} over {qf} fires "
      f"(${qp:+.2f}/fire)")
print(f"    the re-arm COST ${gap:+.2f}/fire of efficiency - on the")
print(f"    contract axis, the same exposure fired at the quantile's")
print(f"    rate instead earns ${eff_foregone:,.2f} more. THE VERDICT:")
print("    the extra contract week would have saved the re-arm")
print(f"    ENTIRELY - the recovery was exactly two weeks, so FBW=3")
print("    never re-arms at all (no third winning week; the pool")
print("    agreed). The whole W42-W44 window is avoided cost.")
print()
print("  RAW-TOTALS CAVEAT (the verdict's known tension): the")
print(f"    re-armed fit's fat fire count earned ${raw_edge:+,.2f} MORE")
print("    raw dollars over the window than the quantile's thin")
print("    stream would have - on raw totals the premature re-arm")
print("    PAID; on $/fire (the contract, because the size ladder")
print("    scales exposure per fire) it COST. The extra week trades")
print("    that raw-dollar surplus for efficiency - the same call")
print("    the flip gate makes every week.")

print(f"\n{'ALL GREEN' if PASS == 5 else 'FAILURES PRESENT'} - {PASS}/5 checks")
raise SystemExit(0 if PASS == 5 else 1)
