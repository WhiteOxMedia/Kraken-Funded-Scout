"""One-off baseline + comparison (scratch convention _*.py).

Tonight (2026-09-21) the re-based ratchet and the journal release went
live — but ZERO trades have closed under them, so no comparison exists
yet. First run freezes the baseline; run it again after a real week
(or `--compare`) and it prints the deltas:

  - stayed-too-long: the study's cost, then vs now
  - deep-veto: the gate's burial cost over the trailing week (the
    burial ledger's own hindsight), then vs now
  - ratchet engagement: how often the live dial tightens now
  - dial integrity: hashes of the ratchet + journal-release dials —
    if they changed mid-week, the comparison is NOT clean and says so

Read-only; the snapshot is a scratch sidecar JSON.
"""
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SNAP = Path(__file__).parent / "_baseline_post_rebase.snapshot.json"
CUTOFF = "2026-09-21"   # the re-base day


def _dial_hashes():
    """Hash the two dials' source so silent mid-week changes surface."""
    bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
    m = re.search(r"def _ratchet_trail_pct.*?return CONTEST_TRAIL_PCT",
                  bs_src, re.S)
    ratchet = hashlib.sha256(m.group(0).encode()).hexdigest()[:12] if m else "missing"
    d_src = Path("discipline.py").read_text(encoding="utf-8")
    dials = {}
    for name in ("SEAM_RELEASE_SLACK", "SEAM_RELIEF_MAX_PTS", "SEAM_RELIEF_DECAY_H"):
        mm = re.search(rf"{name}\s*=\s*([0-9.]+)", d_src)
        dials[name] = float(mm.group(1)) if mm else None
    jr = hashlib.sha256(json.dumps(dials, sort_keys=True).encode()).hexdigest()[:12]
    return {"ratchet_src": ratchet, "journal_release_dials": dials,
            "journal_release_hash": jr}


def _class_costs():
    import _verdict_study as vs
    per = defaultdict(lambda: {"n": 0, "usd": 0.0})
    for g in vs.grade_trades():
        k = g["verdict"].split(" - ")[0]
        per[k]["n"] += 1
        per[k]["usd"] += g["left"]
    return {k: dict(v) for k, v in per.items()}


def _ratchet_engagement():
    import _verdict_study as vs
    graded = vs.grade_trades()
    n = sum(1 for g in graded if (g["peak"] / g["entry"] - 1.0) * 100.0 >= 1.0)
    return {"engaged_of": len(graded), "live_band": n}


def _deepveto_week_cost():
    """deep-veto burial cost over the trailing 7 days, from the ledger."""
    import discipline as dc
    st = dc.load_state()
    cut = (datetime.now(dc.MST) - timedelta(days=7)).strftime("%Y-%m-%d")
    usd = n = 0
    for h in (st.get("burial_ledger") or []):
        if str(h.get("date") or "") < cut:
            continue
        g = (h.get("gates") or {}).get("deep-veto") or {}
        usd += float(g.get("usd") or 0.0)
        n += int(g.get("n") or 0)
    return {"n": n, "usd": round(usd, 2), "window_from": cut}


def snapshot():
    import discipline as dc
    snap = {
        "taken": datetime.now(dc.MST).isoformat(timespec="minutes"),
        "class_costs": _class_costs(),
        "ratchet": _ratchet_engagement(),
        "deepveto_week": _deepveto_week_cost(),
        "relief_now": {g: dc.journal_release_pts(g)
                       for g in ("deep-veto", "tide-out")},
        "dials": _dial_hashes(),
    }
    SNAP.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"baseline frozen at {snap['taken']}")
    print(f"  stayed too long: {snap['class_costs'].get('stayed too long')}")
    print(f"  deep-veto burials (trailing week): {snap['deepveto_week']}")
    print(f"  ratchet live-band engagement: {snap['ratchet']}")
    print(f"  relief now: {snap['relief_now']}")
    print("re-run this script in a week for the comparison.")


def compare():
    import discipline as dc
    if not SNAP.exists():
        print("no baseline snapshot — run without --compare first")
        return 1
    old = json.loads(SNAP.read_text(encoding="utf-8"))
    now_cls = _class_costs()
    now_dv = _deepveto_week_cost()
    now_rat = _ratchet_engagement()
    dials_now = _dial_hashes()

    print(f"baseline: {old['taken']}   now: "
          f"{datetime.now(dc.MST).isoformat(timespec='minutes')}")
    if dials_now != old.get("dials"):
        print("\n⚠ DIAL DRIFT — the ratchet or journal-release dials changed "
              "since the baseline; this comparison is NOT a clean A/B.")
        print(f"  baseline dials: {old.get('dials')}")
        print(f"  current  dials: {dials_now}")
    stl_o = old["class_costs"].get("stayed too long", {"n": 0, "usd": 0.0})
    stl_n = now_cls.get("stayed too long", {"n": 0, "usd": 0.0})
    print(f"\nstayed too long:  ${stl_o['usd']:,.2f} ({stl_o['n']})  ->  "
          f"${stl_n['usd']:,.2f} ({stl_n['n']})"
          f"   [{stl_n['usd'] - stl_o['usd']:+,.2f}]")
    dvo, dvn = old["deepveto_week"], now_dv
    print(f"deep-veto burials (7d): ${dvo['usd']:,.2f} ({dvo['n']})  ->  "
          f"${dvn['usd']:,.2f} ({dvn['n']})"
          f"   [{dvn['usd'] - dvo['usd']:+,.2f}]")
    print(f"ratchet live-band engagement: {old['ratchet']['live_band']}/"
          f"{old['ratchet']['engaged_of']}  ->  {now_rat['live_band']}/"
          f"{now_rat['engaged_of']}")
    print(f"\nverdict: {'shrunk' if stl_n['usd'] < stl_o['usd'] else 'grew or held'} "
          f"for stayed-too-long; "
          f"{'shrunk' if dvn['usd'] < dvo['usd'] else 'grew or held'} "
          f"for deep-veto burials")
    return 0


if __name__ == "__main__":
    sys.exit(compare() if "--compare" in sys.argv else snapshot())
