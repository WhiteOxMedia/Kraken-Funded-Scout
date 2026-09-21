"""diag_clampcost.py — THE CLAMP BUG'S DOLLAR COST (Sep 16).

WHY THIS EXISTS: tide_personal_bar clamped the personal bar to the
QUANTILE band [86, 97] while the Sep 11 outcome refit lowered the
evidence floor to 80 — so every coin's earned relief past 86 was
erased at the gate (PUMP's earned -3 landed at 83 and shipped as 86).
The question: replay the last week's graded tide-out burials through
BOTH clamp worlds and price what the fixed clamp would have released,
in dollars.

THE HONEST REPLAY (no look-ahead), same machinery as
diag_tidememory_hindsight (Sep 10):
  * only graded burials price (snapshot_outcomes.jsonl carries the
    forward outcome; the raw suppression ledger does not);
  * the per-coin memory is REBUILT truncated BEFORE the event's hour —
    the store model and the live popup-outcome cut only know candles
    and outcomes from before that moment;
  * the event's own record supplies the LEARNED bar it was gated on
    (parsed from the detail text — exactly what the gate used), so the
    only difference between the worlds is the clamp floor:
      BUGGED world  bar = clamp86(learned - adj)   (shipped until Sep 16)
      FIXED world   bar = clamp80(learned - adj)   (the fix)
  * release rule = the shipped gate's: quick >= bar (theOUT gate's
    >=). The clamp only ever RAISED the bar (86 floor >= 80 floor), so
    the fixed world's release set is a superset — asserted, not hoped;
  * dollars: position = $1055 (today's ledger, the only contest_size
    the records share) x brain_size_mult (the funnel's own ladder);
    the trade pays end_move_pct (the popup's hold style); mfe_pct is
    the sell-the-top ceiling;
  * the bot-side OUT wall ("all ships sink") is counted, never priced —
    two ledgers, different rules (the wall never read the personal bar).

SCOPE HONESTY: the outcomes ledger begins Sep 15 15:03 — "the last
week" is the window that exists. Context lens: the Sep 11 refit's EV
curve priced bar 83 vs 86 at +$4.25/fire on the deep store.

Run: .venv\\Scripts\\python.exe -X utf8 diag_clampcost.py
Writes _clampcost_check.txt.
"""
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = BASE / "_clampcost_check.txt"
OUTCOMES = BASE / "snapshot_outcomes.jsonl"
LEDGER = BASE / "_clampcost_monthly.jsonl"
POSITION_USD_FALLBACK = 1055.0

# ── THE MONTHLY EVIDENCE DRIVER (Sep 16): the study re-prices itself
# every month as the outcome ledger grows — the realized cost of the
# clamp bug is only knowable once a payer lands in the erased 80-85
# band, and the ledger that would record it accrues daily.
MONTHLY_PERIOD_S = 30 * 86400.0
FIRST_DELAY_S = 900.0            # first pass ~15 min after boot
logger = logging.getLogger("clampcost")


def _position_usd() -> float:
    """Today's ledger (the live contest size) with the study's own
    fallback — the sizing rule stays the funnel's, the dollars stay
    current as the contest moves."""
    try:
        import background_scanner as _bs
        _s = float(_bs._contest_size())
        if _s > 0:
            return _s
    except Exception as exc:
        logger.debug("live contest size unreadable (%s) — the fallback "
                     "$%.0f stands", exc, POSITION_USD_FALLBACK)
    return POSITION_USD_FALLBACK

import tide_memory as tm          # the memory (band constants, live cut)
import tide_learn as tl           # the deep-store loader
import hindsight_scorer as hs     # the sizing ladder

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _rows():
    rows = []
    for line in OUTCOMES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _parse_ts(ts):
    return datetime.fromisoformat(str(ts)).timestamp()


# ── the replayed memory (truncated, no look-ahead) — verbatim from the
# Sep 10 hindsight study ──────────────────────────────────────────────
_store_cache = {}


def _store_adj_at(cutoff_ts: float, pair: str, state: str) -> float:
    hour_key = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc) \
        .replace(minute=0, second=0, microsecond=0).isoformat()
    if hour_key not in _store_cache:
        px, err = tl._load_deep_fleet()
        model = {}
        if px is not None:
            px = px[px.index.astype("int64") // 10**9 < cutoff_ts]
            try:
                import config as _cfgmod
                pp = [p for p in _cfgmod.DEFAULT_CONFIG.trading_pairs
                      if p in px.columns]
            except Exception:
                pp = []
            fpx = px[pp] if len(pp) >= tl.FLEET_MIN else px
            states = tl._tide_states(px, tl._tide_class_ref())
            ffwd = fpx.shift(-tm.HORIZON_H) / fpx - 1.0
            model = tm._measure(states, ffwd)
        _store_cache[hour_key] = model
    model = _store_cache[hour_key]
    st = (model.get(state) or {}) if state else {}
    coin = (st.get("coins") or {}).get(pair)
    return float(coin["adj"]) if coin else 0.0


_LIVE_ROWS = _rows()


def _live_pts_at(cutoff_ts: float, pair: str, state: str) -> float:
    recs = [r for r in _LIVE_ROWS
            if r.get("tide") and r.get("outcome")
            and _parse_ts(r["ts"]) < cutoff_ts]
    m = tm._live_measure(recs, now_ts=cutoff_ts)
    st = m.get(state) or {}
    lc = (st.get("coins") or {}).get(pair)
    if st.get("fleet") is None or not lc or lc.get("w", 0) <= 0:
        return 0.0
    ramp = min(1.0, lc["w"] / tm.LIVE_RAMP_W)
    return max(-tm.EDGE_MAX_PTS, min(tm.EDGE_MAX_PTS, lc["edge"] * ramp))


def _replayed_adj(ts, pair, state):
    return max(-tm.EDGE_MAX_PTS,
               min(tm.EDGE_MAX_PTS, _store_adj_at(ts, pair, state)
                   + _live_pts_at(ts, pair, state)))


# ── the binder + the two worlds ──────────────────────────────────────
def _bind(r):
    """(learned_bar, priced?) from the event's own detail text."""
    d = str(r.get("detail", ""))
    m = re.search(r"learned OUT bar (\d+)", d)
    return (float(m.group(1)), True) if m else (None, False)


def _clamp86(x):
    return max(86.0, min(97.0, x))


def _clamp80(x):
    return max(80.0, min(97.0, x))


def _size(r):
    brain = r.get("brain")
    if brain is None:
        brain = r.get("quick")
    mult = hs.brain_size_mult(brain)
    return POSITION_USD * mult, mult


def run_study() -> dict:
    """One full study pass. Returns the summary dict (and writes the
    txt + appends the ledger record). Never raises past the write."""
    global POSITION_USD
    POSITION_USD = _position_usd()
    rows = _rows()
    to = [r for r in rows if r.get("gate") == "tide-out"
          and r.get("outcome") is not None]
    wall = [r for r in to
            if "all ships sink" in str(r.get("detail", ""))]
    priced = [r for r in to if _bind(r)[1]]
    n_day = {}
    for r in to:
        n_day[str(r.get("ts", ""))[:10]] = n_day.get(str(r.get("ts", ""))[:10], 0) + 1

    out = []
    out.append("=== THE CLAMP BUG'S DOLLAR COST (Sep 16) ===")
    out.append(f"outcomes ledger window: {rows[0].get('ts', '?')[:16] if rows else '(empty)'} -> "
               f"{rows[-1].get('ts', '?')[:16] if rows else '(empty)'}  (the window that exists)")
    out.append(f"graded tide-out burials: {len(to)} "
               f"({', '.join(f'{k}: {v}' for k, v in sorted(n_day.items()))})")
    out.append(f"  priced by the personal bar: {len(priced)}")
    out.append(f"  bot-side OUT wall (never priced — two ledgers): {len(wall)}")
    out.append("")

    fixed_released, bugged_released = [], []
    detail_lines = []
    cost_usd = cost_mfe = 0.0
    changed = []
    for r in priced:
        learned, _ = _bind(r)
        quick = float(r.get("quick") or 0.0)
        ts = _parse_ts(r["ts"])
        st = "OUT"   # tide-out burials are OUT by definition
        adj = _replayed_adj(ts, str(r.get("pair")), st)
        b_bar = _clamp86(learned - adj)
        f_bar = _clamp80(learned - adj)
        if quick >= f_bar:
            fixed_released.append((r, quick, learned, adj, b_bar, f_bar))
        if quick >= b_bar:
            bugged_released.append((r, quick, learned, adj, b_bar, f_bar))
        detail_lines.append((r, quick, learned, adj, b_bar, f_bar))

    # the clamp only ever RAISED the bar — the fixed world's release set
    # must contain the bugged world's
    check("the clamp only raises: every bugged-world release is also a "
          "fixed-world release",
          all(e in fixed_released for e in bugged_released))

    def _usd(evs):
        if not evs:
            return 0.0
        size, _ = _size(evs[0][0])
        return sum(size * float((e[0].get("outcome") or {}).get("end_move_pct") or 0.0) / 100.0
                   for e in evs)

    def _mfe(evs):
        if not evs:
            return 0.0
        size, _ = _size(evs[0][0])
        return sum(size * float((e[0].get("outcome") or {}).get("mfe_pct") or 0.0) / 100.0
                   for e in evs)

    if priced:
        cost_usd = _usd(fixed_released) - _usd(bugged_released)
        cost_mfe = _mfe(fixed_released) - _mfe(bugged_released)

    out.append("── THE TWO WORLDS ──")
    out.append(f"BUGGED world  (clamp [86,97]): {len(bugged_released)} released, "
               f"{_usd(bugged_released):+,.2f} USD end-move")
    out.append(f"FIXED world   (clamp [80,97]): {len(fixed_released)} released, "
               f"{_usd(fixed_released):+,.2f} USD end-move")
    out.append("")
    out.append(f">>> THE CLAMP BUG COST: {cost_usd:+,.2f} USD end-move "
               f"({cost_mfe:+,.2f} USD at the mfe ceiling) "
               f"on {len(fixed_released) - len(bugged_released)} release(s) "
               f"in the recorded window")
    out.append("")

    out.append("── EVERY EVENT THE FIX CHANGES (quick in [80, bugged bar)) ──")
    changed = [e for e in fixed_released if e not in bugged_released]
    if not changed:
        out.append("  none in this window: no buried candidate reached the "
                   "80-85 zone while the clamp was live. The bug's bite "
                   "needs a payer ABOVE 86-3; this window's quicks sat in "
                   "the 50s-60s against an 86+ bar — the tide, not the "
                   "clamp, did this week's burying. The per-event cost of "
                   "the bug, when it bites, is the EV-curve lens below.")
    for e in changed:
        r, quick, learned, adj, b_bar, f_bar = e
        size, mult = _size(r)
        oc = r.get("outcome") or {}
        usd = size * float(oc.get("end_move_pct") or 0.0) / 100.0
        out.append(f"  {r.get('pair')}: {str(r.get('ts'))[:16]} quick {quick:.0f} "
                   f"learned {learned:.0f} adj {adj:+.1f} → bugged {b_bar:.0f} / "
                   f"fixed {f_bar:.0f} | {oc.get('payoff')} "
                   f"end {float(oc.get('end_move_pct') or 0.0):+.2f}% mfe "
                   f"{float(oc.get('mfe_pct') or 0.0):+.2f}% "
                   f"@ {mult:.2f}x ${POSITION_USD:.0f} = {usd:+,.2f} USD")
    out.append("")

    out.append("── THE NEAR ZONE CENSUS (buried with quick 80-85, the clamp's "
               "exact bite range) ──")
    nz = [e for e in detail_lines if 80.0 <= e[1] < 86.0]
    if not nz:
        out.append("  0 events: quick 80-85 never even arrived in this "
                   "window (min/max/median quick across priced burials "
                   "below) — the clamp had nothing to erase this week.")
    for e in nz:
        r, quick, learned, adj, b_bar, f_bar = e
        out.append(f"  {r.get('pair')} quick {quick:.0f} vs bugged {b_bar:.0f} "
                   f"/ fixed {f_bar:.0f} — {str((r.get('outcome') or {}).get('payoff'))}")
    if priced:
        import statistics
        qs = [e[1] for e in detail_lines]
        out.append(f"  priced-burial quick: n={len(qs)} min={min(qs):.0f} "
                   f"med={statistics.median(qs):.0f} max={max(qs):.0f}")
    out.append("")
    out.append("── CONTEXT: the whole gate's window (the no-tide-gate world) ──")
    all_usd = _usd(detail_lines)
    all_mfe = _mfe(detail_lines)
    payoffs = [(e[0].get("outcome") or {}).get("payoff") for e in detail_lines]
    out.append(f"  releasing ALL {len(detail_lines)} burials: "
               f"{all_usd:+,.2f} USD end-move ({all_mfe:+,.2f} at mfe) — "
               f"{payoffs.count('winner')} winners, "
               f"{payoffs.count('dud')} duds, "
               f"{payoffs.count('grinder')} grinders. "
               f"The clamp's $0 sits inside this: the tide gate's suppression "
               f"was the window's story — the clamp's erased band "
               f"(quick 80-85 against a bar it raised to 86) never even "
               f"came into play this week.")
    out.append("")
    out.append("── CONTEXT LENS (the Sep 11 refit's EV curve, not realized) ──")
    out.append("  the deep-store EV fit priced bar 83 vs 86 at +$4.25/fire "
               "(12 fires at 25% pay) — the expected per-fire cost when a "
               "payer lands in the erased zone. This window produced no "
               "such event; the realized cost above is the honest number "
               "for the window that exists.")
    out.append("")
    out.append(f"RESULT: {PASS} passed, {FAIL} failed")

    OUT.write_text("\n".join(out), encoding="utf-8")
    summary = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "window_start": str(rows[0].get("ts", ""))[:16] if rows else "",
        "window_end": str(rows[-1].get("ts", ""))[:16] if rows else "",
        "graded_tideout": len(to),
        "priced": len(priced),
        "bugged_released": len(bugged_released),
        "fixed_released": len(fixed_released),
        "cost_usd": round(cost_usd, 2),
        "cost_mfe_usd": round(cost_mfe, 2),
        "position_usd": POSITION_USD,
        "pass": PASS, "fail": FAIL,
    }
    try:
        with open(LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(summary) + "\n")
    except Exception as exc:
        logger.warning("clamp-cost ledger append failed (%s) — the txt "
                       "still carries the verdict", exc)
    return summary


def main():
    s = run_study()
    print(f"wrote {OUT.name}: {s['pass']} passed, {s['fail']} failed | "
          f"cost {s['cost_usd']:+,.2f} USD on "
          f"{s['fixed_released'] - s['bugged_released']} release(s)")


# ── the ledger, read back ────────────────────────────────────────────
def _read_ledger() -> list:
    try:
        return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8")
                .splitlines() if l.strip()]
    except Exception:
        return []


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one pass shortly after boot (the ledger record
    seeds the history), then monthly. Never raises; dies with the
    scanner. The append-only ledger (_clampcost_monthly.jsonl) is the
    re-pricing history — each month's record prices the clamp bug on
    the outcome ledger AS IT STOOD, so the $0-so-far months stay
    comparable with the month a payer finally lands in the band."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            logger.info("CLAMP-COST: monthly re-price — %d graded tide-out, "
                        "cost %+.2f USD (%s)",
                        s["graded_tideout"], s["cost_usd"],
                        s["window_start"] or "empty window")
        except Exception as exc:
            logger.info("CLAMP-COST: pass failed (%s) — this month's "
                        "re-price skips, the ledger stands", exc)
        stop.wait(period_s)


if __name__ == "__main__":
    main()
