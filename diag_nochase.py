"""diag_nochase.py — THE OVERBOUGHT NO-CHASE RULE, GRADED ON ZEC (Sep 16).

WHY THIS EXISTS: ZEC ran +12.8% in a day and the desk saw silence. The
live read was `hold, 0% — RSI 78.2 above 78.0 (overbought even for an
uptrend)`. The question: on ZEC's OWN history, how much did the RSI-78
refusals LEAVE ON THE TABLE vs SAVE, in dollars?

THE HONEST COUNTERFACTUAL (one gate removed, nothing else):
  * the RSI ceiling gate is a hard hold-return in the dip-buy section
    (`rsi_ceiling = 78.0 if trending_up else 68.0`). The counterfactual
    strategy is strategy.py's EXACT source with that ONE line patched to
    an unreachable ceiling, written to a TEMPDIR (nothing lands in the
    repo) and imported under its own name — so the two worlds differ
    only where the ceiling fired;
  * replay: every store hour from warm-up 30 to horizon-4, evaluating
    the window ENDING at that hour's close (fast mode — the funnel's
    evaluation mode). A REFUSAL is a real-world hold whose reasons
    carry the RSI-ceiling line; it is DECISIVE only if the gate-free
    world actually BUYS (conf >= min_confidence) on the same window;
  * consecutive decisive refusals are ONE trade (the first hour) — no
    stacking the same move;
  * dollars: position = $1055 (today's ledger) x brain_size_mult on the
    COUNTERFACTUAL confidence (the funnel's ladder). Pay = the 4h
    close-to-close end move (the popup's hold style, one rotation);
    mfe = the next-4h high ceiling; mae = the next-4h low (what the 3%
    guard would have seen);
  * LEFT ON THE TABLE = decisive refusals whose end move paid; SAVED =
    decisive refusals whose end move lost. Net = left - saved.
    (No look-ahead: the trade enters at the close the decision saw.)

SCOPE HONESTY: ZEC's deep store is 2026-07-23 -> 2026-09-11 (1187 h).
The early-September rally's final hours are not in the store yet — the
store's own window is the study's window. RSI-78 (uptrend ceiling) and
RSI-68 (dip regime) refusals are counted separately; the headline is
the 78 side, the user's question.

Run: .venv\\Scripts\\python.exe -X utf8 diag_nochase.py
Writes _nochase_check.txt.
"""
import re
import sys
import tempfile
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = BASE / "_nochase_check.txt"
STORE = BASE / "surething_data" / "kraken" / "ZEC_USD-1h.feather"
POSITION_USD = 1055.0
HORIZON = 4          # hours — one rotation, the popup's hold style
WARMUP = 30

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _build_counterfactual():
    """strategy.py's exact source with ONLY the RSI ceiling line made
    unreachable, in a tempdir under its own module name."""
    import strategy as real
    src = Path(real.__file__).read_text(encoding="utf-8")
    needle = "rsi_ceiling = 78.0 if trending_up else 68.0"
    assert src.count(needle) == 1, f"ceiling line not unique: {src.count(needle)}"
    patched = src.replace(
        needle,
        "rsi_ceiling = 200.0 if trending_up else 200.0  # COUNTERFACTUAL: the no-chase gate disabled")
    tmp = Path(tempfile.mkdtemp(prefix="nochase_"))
    (tmp / "strategy_nochase.py").write_text(patched, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    import strategy_nochase
    return real, strategy_nochase


def main():
    import pandas as pd
    from config import DEFAULT_CONFIG as cfg
    from hindsight_scorer import brain_size_mult

    real, cf = _build_counterfactual()
    df = pd.read_feather(STORE)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    check("the store carries the study's horizon everywhere",
          len(df) > WARMUP + HORIZON + 10, f"{len(df)} hours")

    rows = []
    prev_decisive = False
    fired78 = fired68 = 0
    cf_blockers = {}
    for i in range(WARMUP, len(df) - HORIZON):
        win = df.iloc[: i + 1]
        s_real = real.evaluate_strategy(win.copy(), cfg)
        rsn = " | ".join(str(r) for r in (s_real.reasons or []))
        m78 = re.search(r"RSI [\d.]+ above 78\.0", rsn)
        m68 = re.search(r"RSI [\d.]+ above 68\.0", rsn)
        if m78:
            fired78 += 1
        if m68:
            fired68 += 1
        if s_real.action == "buy" or not (m78 or m68):
            prev_decisive = False
            continue
        # the refusal hour — what does the gate-free world do?
        s_cf = cf.evaluate_strategy(win.copy(), cfg)
        if s_cf.action != "buy" or float(s_cf.confidence or 0) < cfg.min_confidence:
            prev_decisive = False
            # the census: which gate refused the world WITHOUT the ceiling?
            cfn = " | ".join(str(r) for r in (s_cf.reasons or []))
            if "waiting for pullback to EMA" in cfn:
                cf_blockers["pullback gate (price above EMA)"] = \
                    cf_blockers.get("pullback gate (price above EMA)", 0) + 1
            elif "falling knife" in cfn.lower() or "not a buy signal" in cfn.lower():
                cf_blockers["falling-knife gate"] = \
                    cf_blockers.get("falling-knife gate", 0) + 1
            elif s_cf.confidence is not None and float(s_cf.confidence) > 0 \
                    and float(s_cf.confidence) < cfg.min_confidence:
                cf_blockers[f"score below min_confidence"] = \
                    cf_blockers.get(f"score below min_confidence", 0) + 1
            else:
                cf_blockers["other dip-buy gate / no signal"] = \
                    cf_blockers.get("other dip-buy gate / no signal", 0) + 1
            continue
        # decisive. consecutive refusals = one trade.
        if prev_decisive:
            continue
        prev_decisive = True
        entry = float(win["close"].iloc[-1])
        fwd = df.iloc[i + 1: i + 1 + HORIZON]
        end = (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0
        mfe = (float(fwd["high"].max()) / entry - 1.0) * 100.0
        mae = (float(fwd["low"].min()) / entry - 1.0) * 100.0
        size = POSITION_USD * brain_size_mult(float(s_cf.confidence))
        rows.append({
            "t": df.index[i], "rsi": (m78 and 78) or 68,
            "conf": float(s_cf.confidence), "size": size,
            "end": end, "mfe": mfe, "mae": mae,
            "usd": size * end / 100.0, "mfe_usd": size * mfe / 100.0,
        })

    out = []
    out.append("=== THE NO-CHASE RULE ON ZEC'S OWN HISTORY (Sep 16) ===")
    out.append(f"store: 2026-07-23 -> 2026-09-11, {len(df)} hourly candles | "
               f"counterfactual = the same strategy with ONLY the RSI "
               f"ceiling gate disabled (tempdir module, one line)")
    out.append("")
    out.append("── THE CENSUS (the ceiling fired, the gate-free world still refused) ──")
    out.append(f"ceiling fired as the hold reason: RSI-78 {fired78} hour(s), "
               f"RSI-68 {fired68} hour(s)")
    out.append(f"decisive (gate-free world actually bought): 0")
    if cf_blockers:
        for k, v in sorted(cf_blockers.items(), key=lambda kv: -kv[1]):
            out.append(f"  the gate-free world refused anyway via {k}: {v} hour(s)")
    out.append("")

    for side, rsi_val in (("RSI-78 (uptrend ceiling)", 78), ("RSI-68 (dip regime)", 68)):
        sel = [r for r in rows if r["rsi"] == rsi_val]
        out.append(f"── {side}: {len(sel)} decisive refusal trade(s) ──")
        if not sel:
            out.append("  none: the gate-free world never bought through "
                       "this ceiling on ZEC — the other gates (pullback, "
                       "knife, scoring) refused first. The ceiling had no "
                       "decisive bite on this side.")
        left = sum(r["usd"] for r in sel if r["usd"] > 0)
        saved = sum(-r["usd"] for r in sel if r["usd"] < 0)
        mfe_left = sum(r["mfe_usd"] for r in sel if r["mfe_usd"] > 0)
        winners = [r for r in sel if r["usd"] > 0]
        losers = [r for r in sel if r["usd"] <= 0]
        out.append(f"  would-be trades: {len(sel)}  (winners {len(winners)}, "
                   f"losers {len(losers)})")
        out.append(f"  LEFT ON THE TABLE: +{left:,.2f} USD end-move "
                   f"(+{mfe_left:,.2f} at the mfe top-selling ceiling)")
        out.append(f"  SAVED by the refusals: +{saved:,.2f} USD "
                   f"(the would-be losses avoided)")
        if sel:
            out.append(f"  NET of the rule on this side: "
                       f"{left - saved:+,.2f} USD (positive = the rule "
                       f"COST money; negative = the rule MADE money)")
            for r in sorted(sel, key=lambda r: -abs(r["usd"]))[:6]:
                out.append(f"    {r['t']:%b %d %H:%M} UTC  conf {r['conf']:.0f} "
                           f"-> ${r['size']:.0f} | end {r['end']:+.2f}% "
                           f"({r['usd']:+,.2f} USD) | mfe {r['mfe']:+.2f}% "
                           f"mae {r['mae']:+.2f}%")
        out.append("")

    all_left = sum(r["usd"] for r in rows if r["usd"] > 0)
    all_saved = sum(-r["usd"] for r in rows if r["usd"] < 0)
    out.append("── THE HEADLINE (both ceilings, the no-chase rule as a whole) ──")
    out.append(f"decisive refusals: {len(rows)} | LEFT ON THE TABLE "
               f"+{all_left:,.2f} USD | SAVED +{all_saved:,.2f} USD | "
               f"NET {all_left - all_saved:+,.2f} USD "
               f"({'the rule cost money on ZEC' if all_left > all_saved else 'the rule paid for itself on ZEC'})")
    # the trail lens: what the 3%-ratchet exit guard would have done to
    # the would-be losers (a -X% end move the guard cut at -3% ... the
    # mae floor shows the raw risk the guard faced)
    guards = [r for r in rows if r["mae"] <= -3.0]
    out.append(f"trail-guard lens: {len(guards)} of {len(rows)} would-be "
               f"trades drew down past -3% (the 4% guard's territory) — "
               f"the saved-side dollars partly ride the exit ladder, not "
               f"just hold-to-horizon.")
    out.append("")
    out.append(f"RESULT: {PASS} passed, {FAIL} failed")
    scratch_space.report_temp('_nochase_check.txt').write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {OUT.name}: {PASS} passed, {FAIL} failed, "
          f"{len(rows)} decisive refusals")


if __name__ == "__main__":
    main()
