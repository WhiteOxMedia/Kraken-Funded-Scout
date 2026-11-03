"""Test THE ADVISORY BRAIN + FIRE-TIME RULE (user spec, Sep 5).

THE ARCHITECTURE (the user's rules, verbatim intent):
  FIRE-TIME RULE: the deep scan MUST run at fire-time. Fail/null/invalid
    -> NO popup, NO entry, NO LEDGER WRITE (the JUP guarantee, kept).
  ENTRY-TIME RULE: once the scan ran, the brain's NUMBER is advisory —
    it sizes (the bucket table), it never blocks. The scan's DIRECTION
    still gates (a SELL read is an invalid buy diagnostic).
  SIZE SCALER: >=85 -> 1.00x, 75-84 -> 0.75x, 65-74 -> 0.50x,
    55-64 -> 0.33x, 45-54 -> 0.20x, <45 -> 0.10x (minimum viable).
  THE POPUP CAP: no more than the dial's BUY popups per 90 min.
  SNAPSHOT DATASET: every fired + every buried signal logs its full
    fire-time feature vector (the scorer's dataset).

The old brain-judged popup floor (Sep 1) and the HEAD-TO-HEAD CHALLENGER
(Sep 4) retired with this change — no floor to clear, no floor to
challenge. The QC mirrors: the number sizes the green, never blocks it.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


bs_src = open("background_scanner.py", encoding="utf-8").read()
ap_src = open("app.py", encoding="utf-8").read()

# ── 1. the floor is REALLY gone (no-old-name assertions — the rename lesson)
# (Sep 10 update: the DISCIPLINE SEAM brought a journal relief back under
# the "brain-floor" name — gate_relief() eases the popup floor for gates
# that bury PAYING candidates. The pin now demands the relief exists,
# fail-closed, not that the string is absent.)
check("the brain-floor journal relief exists (the discipline seam)",
      '"brain-floor"' in bs_src and "gate_relief" in bs_src)
check("the challenger mark never sets (retired with the floor)",
      "_challenger = True" not in bs_src)
check("no >=89 mover desktop floor (the tournament's own champion bar "
      "at champion-vs-champion is a different, standing gate)",
      "the 89% mover desktop floor" not in bs_src
      and "desktop floor 89" not in bs_src
      and "_deep_sig.confidence) >= 89" not in bs_src)
check("no confidence-number deep veto (bridge or desktop)",
      "_deep.confidence < 60" not in bs_src
      and "_deep_sig.confidence) >= 89" not in bs_src)
check("the QC never blocks on the number",
      "sig.confidence >= QC_SCALP_FLOOR" not in ap_src)
check("the QC's weak-setup rejection is gone",
      "weak setup" not in ap_src)

# ── 2. THE FIRE-TIME RULE (fail-closed, both ledgers) ────────────────
check("the bridge stage fails closed with no ledger write",
      "no ledger write (the fire-time rule)" in bs_src)
check("the bridge deep read is exception-safe (a raise = None, not open)",
      "except Exception:\n                        _deep, _df = None, None" in bs_src)
check("the desktop fail-closed stands (availability is mandatory)",
      'no unverified popups (fail-closed; quick said' in bs_src)
check("the contra-read is a NOTE, not a gate (the deep-veto is deleted)",
      '"deep-contranote"' in bs_src
      and "the deep-veto gate is deleted" in bs_src)
check("the contra-read says the fire lands on the ledger too",
      "the fire lands" in bs_src)

# ── 3. THE ADVISORY SIZE SCALER ──────────────────────────────────────
check("the scanner imports the ONE bucket table",
      "from hindsight_scorer import brain_size_mult" in bs_src)
check("the champion path scales the spend",
      "_spend = _spend * _brain_mult" in bs_src)
check("the mover path scales the spend",
      "_m_spend = _m_spend * _m_mult" in bs_src)
check("the scaler runs BEFORE the worth floor (a scaled entry must still pay)",
      bs_src.find("_spend = _spend * _brain_mult")
      < bs_src.find("_worth_w = float(_spend)"))
check("the QC scales its spend (the QC agrees, never disagrees)",
      "_spend_usd = _spend_usd * _qc_mult" in ap_src)
check("the QC speaks the sizing",
      "sizing at {_qc_mult:.2f}" in ap_src)
check("the popups carry the sizing mark (title + voice + message)",
      "× SIZE" in bs_src and "BRAIN SIZES IT" in bs_src
      and "sizing at {_brain_mult:.2f}" in bs_src)

# ── 4. THE POPUP CAP (machinery kept, Sep 9: enforcement retired per the
# no-money-cap ruling — _popup_cap is defined and live-dialed, never called)
check("the cap dial reads live (default 3, floor 1)",
      "POPUP_CAP_DEFAULT = 3" in bs_src and "def _popup_cap" in bs_src)
check("the cap counts fired popups inside the window",
      "def _recent_fired_popups" in bs_src
      and "POPUP_CAP_WINDOW_MIN = 90.0" in bs_src)
check("the champion path no longer checks the cap (retired Sep 9)",
      '"popup-cap"' not in bs_src)
check("the mover path no longer checks the cap (retired Sep 9)",
      "MOVER POPUP CAP" not in bs_src)

# ── 5. THE SNAPSHOT DATASET ──────────────────────────────────────────
check("the snapshot log + helper exist",
      "SIGNAL_SNAPSHOTS_FILE" in bs_src and "def _log_signal_snapshot" in bs_src)
check("the fire-time snapshot runs at the top of the gates",
      "def _begin_fire_snapshot" in bs_src)
check("every suppression lands in the dataset (the hook in _log_suppression)",
      "kind\": \"suppressed\"" in bs_src)
check("every fired popup lands in the dataset (champion + mover + sure-thing)",
      bs_src.count('"kind": "fired"') == 3)

# ── 6. behavioral: the scanner functions do what the source says ─────
import background_scanner as bs
from hindsight_scorer import brain_size_mult

check("the module table is the ONE source (scanner + QC import it)",
      brain_size_mult(90) == 1.0 and brain_size_mult(50) == 0.20)

_cap = bs._popup_cap()
check("_popup_cap reads the live dial (int >= 1)", isinstance(_cap, int) and _cap >= 1, _cap)

# the cap counter: fresh fired records count, stale ones don't
tmp = Path(tempfile.mkdtemp())
snapfile = tmp / "signal_snapshots.jsonl"
old_snap = bs.SIGNAL_SNAPSHOTS_FILE
# GUARDED SWAP (testleaks, Sep 23): the swap lives inside the try whose
# finally hands the production path back — a crash mid-suite can never
# strand the sandbox pointer (the PULSE_JSON pattern).
try:
    bs.SIGNAL_SNAPSHOTS_FILE = str(snapfile)
    now = datetime.now().astimezone()
    recs = [
        {"ts": (now - timedelta(minutes=10)).isoformat(), "pair": "A/USD", "kind": "fired"},
        {"ts": (now - timedelta(minutes=30)).isoformat(), "pair": "B/USD", "kind": "fired"},
        {"ts": (now - timedelta(minutes=200)).isoformat(), "pair": "C/USD", "kind": "fired"},
        {"ts": (now - timedelta(minutes=5)).isoformat(), "pair": "D/USD", "kind": "suppressed"},
    ]
    with open(snapfile, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    n = bs._recent_fired_popups()
    check("_recent_fired_popups: 2 fresh fired count (stale + suppressed don't)",
          n == 2, n)
    # _log_signal_snapshot appends + survives junk
    bs._log_signal_snapshot({"kind": "fired", "pair": "E/USD", "brain": 42.0})
    lines_in = snapfile.read_text().strip().splitlines()
    check("_log_signal_snapshot appends (5 records now)", len(lines_in) == 5, len(lines_in))
    got = json.loads(lines_in[-1])
    check("the snapshot record carries its fields + a ts stamp",
          got["pair"] == "E/USD" and got.get("ts"), got)

    # the suppression hook: _SNAPSHOT_CTX set -> the burial lands in the dataset
    old_sup = bs.SUPPRESSION_LOG_FILE
    bs.SUPPRESSION_LOG_FILE = str(tmp / "supp.jsonl")
    try:
        ctx = bs._begin_fire_snapshot(
            type("X", (), {"fetch_ohlcv": lambda self, pair=None, timeframe=None,
                           limit=None: [
                [1700000000000 + i * 300000, 100.0, 100.5, 99.5, 100.2, 100.0]
                for i in range(60)]})(), "TEST/USD", 77.0)
        check("_begin_fire_snapshot computes features from the 5m fetch",
              isinstance(ctx.get("features"), dict)
              and "vol_burst" in ctx["features"], list((ctx.get("features") or {}).keys())[:3])
        check("the context is stashed for the suppression hook",
              bs._SNAPSHOT_CTX.get("pair") == "TEST/USD")
        bs._log_suppression("TEST/USD", "test-gate", "the detail",
                            quick=77.0, brain=42.0)
        sup_lines = [l for l in bs.SUPPRESSION_LOG_FILE and
                     Path(bs.SUPPRESSION_LOG_FILE).read_text().splitlines() if l.strip()]
        check("the suppression log still writes (never raises)", len(sup_lines) == 1)
        snap_lines = [l for l in snapfile.read_text().splitlines() if l.strip()]
        hook = json.loads(snap_lines[-1])
        check("the burial lands in the dataset with features + gate",
              hook.get("kind") == "suppressed" and hook.get("gate") == "test-gate"
              and isinstance(hook.get("features"), dict), hook.get("gate"))
        # a broken feature fetch never blocks the funnel
        bs._SNAPSHOT_CTX.clear()
        bs._log_suppression("OTHER/USD", "test-gate-2", "no context")
        check("no context -> suppression logs anyway (no crash)",
              len([l for l in Path(bs.SUPPRESSION_LOG_FILE).read_text().splitlines()
                   if l.strip()]) == 2)
    finally:
        bs.SUPPRESSION_LOG_FILE = old_sup
finally:
    bs.SIGNAL_SNAPSHOTS_FILE = old_snap

# the QC's verdict chain: the number is not among the blockers
check("the QC's green text names the scan's direction, not a number floor",
      "THE SCAN READS" in ap_src and "NO GREEN — NEEDS" not in ap_src)
check("the QC's green is reachable at any brain number (passes drops conviction)",
      "and goal_ok\n              and trend" in ap_src.replace(
          "passes = (sig.action == \"buy\" and not no_buy\n", ""))

# ── 7. the seam to the scorer ────────────────────────────────────────
import hindsight_scorer as hs
check("the scorer's dataset files are the scanner's (ONE seam)",
      hs.SNAPSHOTS_FILE == bs.SIGNAL_SNAPSHOTS_FILE)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_advisorybrain_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)


def main():
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
