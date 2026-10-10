"""Test THE RISK CAP LABEL (user confusion, Oct 9: "the popup said spend
$166 but further down it said spend $1000 — confusing?").

THE RECORD: the popup body showed the advisory pipeline's intermediate —
the 3% guard's pre-sizing ceiling — under a `SPEND:` label, while the
title/ENTER line (the actual recommendation after the brain and tide
scalers) carried the final smaller number. Two dollar figures, one
labeled spend, one not: the desk can't tell which to act on. THE CURE:
the guard-capped line relabeled `RISK CAP: $… pre-sizing — THE 3% GUARD:
… (the recommended spend is the title/ENTER line)`, on the champion AND
mover paths. This suite pins the pairing so a future edit can't
reintroduce the two-money confusion:
  - both guard sites carry the RISK CAP … pre-sizing label
  - neither guard site emits a bare `SPEND:` from its cap line
  - the genuinely-recommended SPEND lines (small-coin tranche, big
    book, Hail Mary) keep their labels
  - the explanatory tail names the title/ENTER line as the spend

THE MAX/MIN PAIR (user decree, Oct 10: "always display both the maximum
and minimum viable spend amounts"): both paths also pin
  - champion title/voice build the pair from _base_spend (the sized base
    captured BEFORE any weaken clamp) — MAX x1.0, MIN x0.10
  - mover snapshot _m_base_spend before its clamps; MIN rides _m_bal*0.02
  - the SIZE YOUR OWN fallback reads exactly
    "SIZE YOUR OWN — sizing unavailable" and the voice says so too
"""
import sys
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


import scratch_space   # scratch policy: nothing volatile lands in the repo root

bs = Path("background_scanner.py").read_text(encoding="utf-8")

# ── THE CHAMPION GUARD SITE ─────────────────────────────────────────
g_i = bs.find("if _spend > _risk_cap:")
g_seg = bs[g_i:g_i + 500] if g_i > 0 else ""
check("champion guard site exists (the _spend > _risk_cap merge)",
      g_i > 0 and "_spend = _risk_cap" in g_seg)
check("champion guard line is labeled RISK CAP (not SPEND)",
      'f"  RISK CAP: ${_spend:,.0f} pre-sizing — THE 3% GUARD' in g_seg
      and "SPEND: ${_spend:,.0f} — THE 3% GUARD" not in g_seg)
check("champion guard line carries the explanatory tail",
      "(the recommended spend is the title/ENTER line)" in g_seg)

# ── THE MOVER GUARD SITE ────────────────────────────────────────────
m_i = bs.find("if _m_guard_capped:")
m_seg = bs[m_i:m_i + 500] if m_i > 0 else ""
check("mover guard-capped site exists", m_i > 0)
check("mover guard line is labeled RISK CAP (not SPEND)",
      'f"  RISK CAP: ${_m_spend:,.0f} pre-sizing — THE 3% GUARD' in m_seg
      and "SPEND: ${_m_spend:,.0f} — THE 3% GUARD" not in m_seg)
check("mover guard line carries the explanatory tail",
      "(the recommended spend is the title/ENTER line)" in m_seg)

# ── THE RECOMMENDATION SURFACES KEEP SPEND ─────────────────────────
check("small-coin tranche keeps SPEND (it IS the recommendation)",
      '  SPEND: {_spend / (float(_bal)' in bs)
check("small-coin line names the book's real carry (the user ask, Oct 9: "
      "'right next to where it says Buy x now spend $ include how much "
      "the book will carry')",
      bs.count("the book will carry it: ${_carry:,.0f} ") == 1
      and bs.count("the book will carry it: ${_m_carry:,.0f} ") == 1
      and bs.count("f\"(20% of the visible book — small coin)") == 2)
check("big-book line keeps SPEND 100% and carries its own carry figure",
      'SPEND: 100% = ${_bal' in bs
      and 'will carry it: "' in bs          # champion big-book line's carry
      and 'will carry it: ${_m_carry:,.0f} (20% of the visible book)' in bs)
check("the old ambiguous wording is gone (carries the spend, not the book)",
      "SMALL COIN: this book carries" not in bs
      and "sized down — the book carries it" not in Path("app.py").read_text(encoding="utf-8"))
check("hail-mary opportunity line keeps SPEND (its number IS final)",
      "SPEND: ${_spend:,.0f} — OPPORTUNISTIC" in bs)

# ── NO OTHER GUARD-CAPPED SPEND LEAKS ──────────────────────────────
check("no bare SPEND: $… — THE 3% GUARD pairing remains anywhere",
      "SPEND: ${_spend:,.0f} — THE 3% GUARD" not in bs
      and "SPEND: ${_m_spend:,.0f} — THE 3% GUARD" not in bs)
check("both RISK CAP sites count = 2 (champion + mover)",
      bs.count("RISK CAP: ${_spend:,.0f} pre-sizing")
      + bs.count("RISK CAP: ${_m_spend:,.0f} pre-sizing") == 2)

# ── THE MAX/MIN PAIR (user decree, Oct 10) ──────────────────────────
check("champion: _base_spend captured before any weaken clamp",
      "_base_spend = _spend" in bs
      and bs.find("_base_spend = _spend") < bs.find("def _min_size_weaken"))
check("champion: the min floor rides 0.10x of the base (not the weakened spend)",
      "_spend = max(_spend, _base_spend * 0.10)" in bs)
check("champion title leads with the MAX/MIN pair",
      'f"MAX: ${_base_spend:,.0f} — MIN: ${(_base_spend * 0.10):,.0f}"' in bs)
check("champion voice speaks the range",
      '"the range is max {_max_spend_say}"' in bs
      and '", min {_min_spend_say} dollars — "' in bs)
check("champion: the pair never leaks '-' figures (base or nothing)",
      "MIN: ---" not in bs)
check("mover: the base is snapshotted before the weaken clamps",
      "_m_base_spend = _m_spend" in bs
      and bs.find("_m_base_spend = _m_spend") < bs.find("MOVER MIN-SIZE FIRE"))
check("mover title carries the MAX/MIN pair",
      'f" — MAX: ${_m_base_spend:,.0f} — MIN: ${(_m_bal * 0.02):,.0f}"' in bs)
check("mover voice speaks the range",
      '"The range is max {_m_base_spend:,.0f}"' in bs
      and '", min {(_m_bal * 0.02):,.0f} dollars — "' in bs)
check("the mover min-size clamp still lands (the floor is applied, not just shown)",
      "_m_spend = max(_m_spend, _m_bal * 0.02)" in bs)
check("sizing-unavailable fallback reads exactly (title + voice, both paths)",
      bs.count("SIZE YOUR OWN — sizing unavailable") >= 2
      and "Sizing unavailable — size your own! " in bs)

# (the live-journal spot-check was CUT: the fleet sink rule
#  (test_sink_hygiene 1.1) rightly flags any drive reading the LIVE
#  popups.jsonl — the label contract is fully pinned in source above,
#  and the next guard-capped fire journals the proof on its own)

scratch_space.report_temp("_riskcaplabel_check.txt").write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
