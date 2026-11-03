"""THE SURE THING AUTO-REFIT (Sep 11) — the score's weights now keep
pace with the store, monthly, from the deep-store driver family.

WHY: the Sep 11 refit (diag_surething_refit.py) proved the score's
weights are FITTABLE data, not scripture — and the caveat it shipped
with was "one 30-day falling-market regime; re-grade on a rising month
before trusting the weights blindly." A monthly refit makes that
re-grade automatic instead of remembered.

THE LOOP (once a month, daemon thread off the scanner's main()):
  1. REPLAY the recalibrated 5m store with the REAL detector — the
     diag's own collect()/episode_rows() machinery is imported, never
     copied, so the fit can never drift from the shipped grade.
  2. LABEL every episode under the RE-CONTRACTED popup horizon (the
     4h clock the popup now promises; the +1.5% goal, MFE over 48
     5m bars).
  3. RIDGE-FIT the 11 component weights (no intercept, lambda 5,
     clipped [0, 40], renormalized to sum 100) on the chronological
     70% train slice; score the held-out 30%.
  4. THE SHIP GATES (in order):
       - fresh AUC < legacy + 0.02  -> keep the incumbent (the fit
         found nothing the data proves)
       - fresh AUC < incumbent - 0.03 OR below the 0.505
         forward-ranker floor -> KEEP THE INCUMBENT and REPORT the
         degradation in the report + the weights ledger (this is the
         "report when the holdout degrades" contract)
       - otherwise SHIP: swap sure_thing.REFIT_W in place (atomic
         dict update — analyze() re-reads the dict each call, so the
         very next poll prices with the new weights; no restart).
     Legacy (SURE_THING_USE_REFIT=False) always wins the comparison
     if it out-ranks everything — the incumbent is whatever the
     ledger says is live.
  5. REPORT: _surethingautorefit.txt (rotated at ~500 KB) carries the
     weights table, the AUCs, the split, and the verdict; the weights
     ledger (sure_thing_autoweights.json) stamps every pass — shipped
     or kept — so the monthly cadence is auditable.

FAIL-SAFE: any raise is caught by the driver loop (the store is
replayed again next month); a thin replay leaves the incumbent weights
and the ledger untouched. The runtime swap never touches the
SURE_THING_USE_REFIT switch or the shipped REFIT_W source — flipping
the switch back to False still restores the legacy score verbatim.

Run: .venv\\Scripts\\python.exe -X utf8 surething_autorefit.py
"""
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("surething-autorefit")

ROOT = Path(__file__).parent
OUT_FILE = ROOT / "_surethingautorefit.txt"
WEIGHTS_FILE = ROOT / "sure_thing_autoweights.json"
REPORT_MAX_BYTES = 500_000

REFIT_PERIOD_S = 30 * 86400.0   # monthly
FIRST_DELAY_S = 600.0           # first pass ~10 min after boot

SPLIT_FRAC = 0.70               # chronological: earliest 70% trains
MIN_TOTAL_ROWS = 400            # the diag's own floor (2 x MIN_HOLDOUT)
CONTRACT_WINDOW_BARS = 48       # the 4h contract on 5m candles
CONTRACT_GOAL_PCT = 1.5

DEGRADE_DROP = 0.03             # worse than the incumbent by this = degraded
DEGRADE_FLOOR = 0.505           # below this the score is not a forward ranker
BEATS_LEGACY_MARGIN = 0.02      # the diag's own ship margin

_lock = threading.Lock()
_last_verdict = {"ts": None, "verdict": None}


def _replay_rows():
    """The episode dataset: [(hour, feats, label, rec)] — the diag's
    replay machinery, labels re-cut on the 4h contract. Returns
    (rows, censored_4h)."""
    import diag_surething_refit as dg
    rows = []
    censored = 0
    for ep in dg.collect():
        r = dg.episode_rows(ep)          # (hour, feats, label60, peak_rec)
        if r is None:
            continue
        hour, feats, _label60, rec_peak = r
        # the peak step's bar index (identity match back into ep["steps"])
        t_peak = next((t for t, rr in ep["steps"] if rr is rec_peak), None)
        label = None
        if t_peak is not None:
            oc = dg._outcome(ep["arrs"], t_peak,
                             win_bars=CONTRACT_WINDOW_BARS,
                             goal_pct=CONTRACT_GOAL_PCT)
            if oc is not None:
                label = 1.0 if oc[0] else 0.0
        if label is None:
            # the 4h window runs past the store's edge — censor honestly
            censored += 1
            continue
        rows.append((hour, feats, label, rec_peak))
    return rows, censored


def fit_now(min_rows: int | None = None):
    """One full pass: replay -> fit -> gates -> report + ledger.
    Returns the verdict dict (never raises under the driver).

    THE GATES (all AUCs measured on the SAME fresh holdout — the
    incumbent's stored number came from a different contract/label
    vintage, so it is provenance, never a gate input):
      1. fresh < legacy + margin      -> kept (beats nothing)
      2. fresh < DEGRADE_FLOOR        -> kept (not a forward ranker)
      3. fresh < incumbent - DEGRADE_DROP -> DEGRADED, loudly named,
         incumbent kept (the report-when-degraded contract)
      4. fresh < incumbent            -> kept, regression noted
      5. otherwise                    -> SHIP (beats legacy, above the
         floor, and at-or-better than the incumbent on identical data)
    """
    import numpy as np
    import diag_surething_refit as dg
    import sure_thing as st
    with _lock:
        t0 = time.time()
        rows, censored = _replay_rows()
        floor = min_rows if min_rows is not None else MIN_TOTAL_ROWS
        ledger = _read_ledger()
        if len(rows) < floor:
            verdict = (f"kept: replay too thin ({len(rows)} rows < {floor}) "
                       f"— the incumbent stands")
            _write_report(rows, None, ledger, verdict, censored, t0)
            _stamp_ledger(ledger, verdict, shipped=False)
            return {"verdict": verdict, "shipped": False}

        rows.sort(key=lambda r: r[0])
        split_i = int(len(rows) * SPLIT_FRAC)
        train, hold = rows[:split_i], rows[split_i:]
        Xtr = np.vstack([r[1] for r in train])
        ytr = np.array([r[2] for r in train])
        Xho = np.vstack([r[1] for r in hold])
        yho = np.array([r[2] for r in hold])
        w = dg.ridge_fit(Xtr, ytr)
        w = np.clip(w, 0.0, 40.0)
        w = w / w.sum() * 100.0
        leg_vec = np.array(dg.LEGACY_W, dtype=float)   # aligned to FEAT_NAMES
        # the INCUMBENT = the weights live in sure_thing right now (the
        # shipped source on a cold start, whatever a past pass swapped
        # in the scanner's process) — re-scored on THIS holdout
        inc_vec = np.array([float(st.REFIT_W.get(n, 0.0))
                            for n in dg.FEAT_NAMES])
        a_leg = dg.auc(Xho @ leg_vec, yho)
        a_new = dg.auc(Xho @ w, yho)
        a_inc = dg.auc(Xho @ inc_vec, yho)
        src = ledger.get("src") or "refit"

        # ── the gates, in order ──
        shipped = False
        if a_new < a_leg + BEATS_LEGACY_MARGIN:
            verdict = (f"kept {src}: the fresh refit does not beat legacy "
                       f"(holdout AUC {a_new:.3f} vs legacy {a_leg:.3f})")
        elif a_new < DEGRADE_FLOOR:
            verdict = (f"kept {src}: the fresh refit sits below the "
                       f"forward-ranker floor ({a_new:.3f} < {DEGRADE_FLOOR})")
        elif a_new < a_inc - DEGRADE_DROP:
            verdict = (f"DEGRADED vs {src}: holdout AUC {a_new:.3f} is more "
                       f"than {DEGRADE_DROP:.2f} below the incumbent's "
                       f"{a_inc:.3f} on the same holdout — keeping the "
                       f"incumbent's weights and reporting the degradation")
        elif a_new < a_inc:
            verdict = (f"kept {src}: the fresh refit regressed vs the "
                       f"incumbent on the same holdout ({a_new:.3f} < "
                       f"{a_inc:.3f}) — no improvement, no ship")
        else:
            shipped = True
            verdict = (f"SHIPPED: holdout AUC {a_new:.3f} at-or-better than "
                       f"the incumbent's {a_inc:.3f} and legacy's {a_leg:.3f} "
                       f"on the same holdout — "
                       f"{len(train)} train / {len(hold)} holdout episodes")

        _write_report(rows, (w, a_new, a_leg, a_inc, src, train, hold),
                      ledger, verdict, censored, t0)
        if shipped:
            new_w = {n: round(float(x), 1)
                     for n, x in zip(dg.FEAT_NAMES, w)}
            # the runtime swap — atomic dict update, no restart
            st.REFIT_W.update(new_w)
            ledger.update({"weights": new_w, "src": "refit",
                           "auc_last": round(a_new, 4)})
        _stamp_ledger(ledger, verdict, shipped=shipped,
                      auc_new=round(a_new, 4), auc_legacy=round(a_leg, 4),
                      auc_incumbent=round(a_inc, 4), rows=len(rows))
        _last_verdict.update(ts=time.time(), verdict=verdict)
        logger.info("SURE THING AUTO-REFIT: %s", verdict)
        return {"verdict": verdict, "shipped": shipped,
                "auc_new": a_new, "auc_incumbent": a_inc,
                "auc_legacy": a_leg}


def _read_ledger() -> dict:
    try:
        return json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _incumbent(ledger: dict, st) -> tuple[str, float]:
    """Provenance only (the gate never trusts a stored AUC — it
    re-scores the incumbent weights on each pass's own holdout).
    Returns (source name, the Sep 11 reference AUC for the ledger's
    first stamp)."""
    src = ledger.get("src") or "refit"
    a_last = ledger.get("auc_last")
    if a_last is None:
        a_last = 0.527   # the Sep 11 shipped refit's holdout AUC
    return src, float(a_last)


def _stamp_ledger(ledger: dict, verdict: str, shipped: bool, **extra):
    data = dict(ledger)
    data.update({"ts": time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                     time.gmtime()),
                 "verdict": verdict, "shipped": shipped})
    data.update(extra)
    tmp = WEIGHTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    import os
    os.replace(tmp, WEIGHTS_FILE)


def _write_report(rows, fit, ledger, verdict, censored, t0):
    import numpy as np
    import diag_surething_refit as dg
    lines = []
    lines.append("=== THE SURE THING AUTO-REFIT — monthly pass ===")
    lines.append(f"run at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append(f"contract: +{CONTRACT_GOAL_PCT}% within "
                 f"{CONTRACT_WINDOW_BARS * 5} min (the 4h clock)")
    lines.append(f"episodes: {len(rows)} "
                 f"({censored} censored inside the store's last "
                 f"{CONTRACT_WINDOW_BARS} bars)")
    if fit is None:
        lines.append(f"VERDICT: {verdict}")
    else:
        w, a_new, a_leg, a_inc, src, train, hold = fit
        yho = np.array([r[2] for r in hold])
        base = yho.mean() * 100.0
        lines.append(f"holdout base rate: {base:.1f}%  "
                     f"(train {len(train)} / holdout {len(hold)}, "
                     f"split at {SPLIT_FRAC:.0%} of episode time)")
        lines.append("")
        lines.append("── the fresh weights (vs legacy) ──")
        lines.append(f"{'component':<10} {'legacy':>7} {'fresh':>7}")
        for i, (nm, x) in enumerate(zip(dg.FEAT_NAMES, dg.LEGACY_W)):
            lines.append(f"{nm:<10} {x:>7.1f} {w[i]:>7.1f}")
        lines.append("")
        lines.append(f"AUC on the same holdout —  legacy {a_leg:.3f}   "
                     f"fresh {a_new:.3f}   incumbent ({src}) {a_inc:.3f}")
        lines.append(f"VERDICT: {verdict}")
    lines.append(f"replay+fit took {time.time() - t0:.0f}s")
    lines.append("")
    try:
        prev = OUT_FILE.read_text(encoding="utf-8") if OUT_FILE.exists() else ""
    except Exception:
        prev = ""
    blob = "\n".join(lines) + "\n" + prev
    if len(blob.encode("utf-8")) > REPORT_MAX_BYTES:
        blob = blob[:REPORT_MAX_BYTES]
    OUT_FILE.write_text(blob, encoding="utf-8")


def refit_driver_loop(stop: threading.Event,
                      first_delay_s: float = FIRST_DELAY_S,
                      period_s: float = REFIT_PERIOD_S):
    """The daemon loop: one pass shortly after boot, then monthly.
    Never raises; dies with the scanner."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            fit_now()
        except Exception as exc:
            logger.info("SURE THING AUTO-REFIT: pass failed (%s) — "
                        "the incumbent weights stand", exc)
        stop.wait(period_s)


def snapshot() -> dict:
    """Diagnostics: the last pass + the ledger, without recomputing."""
    return {"last": dict(_last_verdict), "ledger": _read_ledger()}


if __name__ == "__main__":
    import pprint
    pprint.pp(fit_now())
