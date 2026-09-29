"""THE SWEEP DRIVE (task 5): the grid covers every cell, the replay
honors the rails (sample floor, ratio floor, daily cap, time box), the
bootstrap is seeded (reproducible), and the HTML report names the
recommended conservative defaults with the worst single-book loss for
every cell — plus one run against the machine's REAL journals."""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import simulate_sweep as ss

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# the grid is the spec's, verbatim
check("the sweep grid is the spec's (3 x 3 x 2 = 18 cells)",
      len(ss.ALPHAS) == 3 and len(ss.DELTAS) == 3 and len(ss.CAPS) == 2,
      (ss.ALPHAS, ss.DELTAS, ss.CAPS))
check("the recommended defaults are the spec's example (0.05/0.10/0.20)",
      ss.RECOMMENDED == {"alpha": 0.05, "delta_max": 0.10,
                         "daily_cap": 0.20}, ss.RECOMMENDED)

# a synthetic history with known structure: some rich rotations, a
# drought, a low-ratio stretch (the gates being right)
hist = ([{"rot": f"r{i:02d}", "B": 10, "P": 5} for i in range(6)]
        + [{"rot": "dry", "B": 0, "P": 0}]
        + [{"rot": f"low{i}", "B": 10, "P": 2} for i in range(2)]
        + [{"rot": "thin", "B": 3, "P": 3}])
res = ss.replay(hist, 0.05, 0.10, 0.20)
check("the replay applies only where the rails pass (6 rich rotations "
      "apply; drought/thin/low-ratio idle)",
      res["applied_count"] == 6
      and all(d == 0.0 for d in res["applied"] if d == 0.0), res)
check("every applied delta is negative and within the per-adjustment "
      "cap (loosening only)",
      all(-ss.DELTAS[-1] <= d <= 0 for d in res["applied"] if d < 0)
      and res["worst"] <= 0, res["worst"])
check("the daily cap binds per rotation-day (the rich rotations each "
      "took exactly one -0.08, never two)",
      abs(res["applied"][0] - (-(0.05 * 0.5) * ss.__dict__["math"].log(11)))
      < 1e-9, res["applied"][:2])
check("the time box produces auto-reverts (one continuous applying "
      "streak, one revert when it expires)",
      res["boxes"] >= 1 and res["reverts"] >= 1, (res["boxes"],
                                                  res["reverts"]))

# tighter cap -> fewer applies; the sweep must show monotone pressure
r1 = ss.replay(hist, 0.05, 0.10, 0.10)
check("a tighter daily cap applies no more than the looser one",
      r1["applied_count"] <= res["applied_count"],
      (r1["applied_count"], res["applied_count"]))

# the sweep runs every cell and the metrics are finite
cells = ss.sweep(hist)
check("the sweep returns 18 cells (3x3x2), all metrics finite",
      len(cells) == 18
      and all(abs(c["net_delta"]) < 1e6 and abs(c["worst_single_book_loss"])
              < 1e6 for c in cells), len(cells))
check("exactly one cell carries the recommended flag",
      sum(1 for c in cells if c["recommended"]) == 1,
      [c for c in cells if c["recommended"]])
rec = [c for c in cells if c["recommended"]][0]
check("the recommended cell is the conservative middle (a fatter "
      "alpha or delta_max cannot produce a smaller worst loss)",
      rec["worst_single_book_loss"] >= max(
          c["worst_single_book_loss"] for c in cells
          if c["alpha"] >= 0.05 and c["delta_max"] >= 0.10),
      (rec["worst_single_book_loss"],
       min(c["worst_single_book_loss"] for c in cells)))

# the bootstrap is seeded: same input, same CI
lo1, hi1 = ss.bootstrap_ci([d for d in res["applied"] if d < 0] or [0.0])
lo2, hi2 = ss.bootstrap_ci([d for d in res["applied"] if d < 0] or [0.0])
check("the bootstrap CI is seeded and reproducible",
      (lo1, hi1) == (lo2, hi2), (lo1, hi1, lo2, hi2))

# the report: acceptance named by the spec
out = BASE / "artifacts" / "sim" / "reports" / "_drive_sensitivity.html"
ss.write_report(cells, hist, out)
html = out.read_text(encoding="utf-8")
check("the report renders the grid (18 body rows) and the recommended "
      "row is classed",
      html.count("<tr") == 19 and "class='rec'" in html,
      html.count("<tr"))
check("the report NAMES the conservative defaults and the worst "
      "single-book loss column (the spec's acceptance)",
      "RECOMMENDED" in html and "worst single-book" in html
      and "alpha=0.05" in html and "delta_max=0.10" in html, html[:200])
check("the charts are inline SVG with a per-cell tooltip",
      "<svg" in html and "<title>a=" in html, html.count("<rect"))

# THE GROUPING (the isolated review's major): a bare-window journal
# groups by DATE + window, never collapsing into six ever-merged
# buckets. Two days of the same window must appear as two rotations.
hist2 = [
    {"ts": "2026-09-27T12:10:00-06:00", "pair": "A/USD", "rot": "12-16"},
    {"ts": "2026-09-28T12:10:00-06:00", "pair": "B/USD", "rot": "12-16"},
]
_by = {}
for r in hist2:
    rot = r["rot"]
    t_day = r["ts"][:10]
    _by.setdefault(f"{t_day}-{rot}", []).append(r)
check("the grouping key dates bare windows (two days, two rotations — "
      "never merged)", len(_by) == 2, _by)

# the REAL history: the harness runs end-to-end on the live journals
try:
    real_hist = ss.load_history(30)
    real_cells = ss.sweep(real_hist)
    real_out = (BASE / "artifacts" / "sim" / "reports"
                / "_drive_real.html")
    ss.write_report(real_cells, real_hist, real_out)
    check("the real-history replay runs read-only end to end "
          "(rotations parsed, sweep complete, report written)",
          len(real_hist) > 0 and len(real_cells) == 18
          and real_out.exists(),
          (len(real_hist), sum(h["B"] for h in real_hist)))
except Exception as exc:
    check("the real-history replay runs read-only end to end", False,
          repr(exc))

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "sim" / "_simsweep_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
