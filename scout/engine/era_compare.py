"""era_compare.py — the re-base's before/after instrument, promoted from
the Sep-22 scratch (_era_compare.py) into a scheduled nightly diagnostic.

Splits the closed-trade book at CUTOFF (2026-09-21, the ratchet re-base,
commit fc00c9c — trades sold on/after ran under the new ratchet) and
grades each era through the verdict-cost study's own grade_trades(), so
its numbers can never disagree with the nightly report. The framing is
the feature: the summary carries `readable` — False until the post-era
holds MIN_POST_TRADES closed trades — and top_line() speaks nothing
until the data can support the comparison. The scanner runs this ONCE
on/after the Sep-28 week-one date (date-gated, restart-proof: a scanner
restart during Sep 28 still fires it that night), writes one report
section to logs/era_compare_report.md, and the night's cooldown review
speaks the headline. Fail-soft everywhere: a broken candle store or an
empty book is a missing report, never an exception.
"""
import datetime
import json
from collections import defaultdict
from pathlib import Path as _Path

import _verdict_study as vs   # the single grading source (grade_trades/_sim)

CUTOFF = "2026-09-21"          # the re-base day (fc00c9c)
TARGET_DATE = "2026-09-28"     # the week-one read: one-shot on/after this MST date
MIN_POST_TRADES = 10           # below this the post-era supports no comparison
STATE_PATH = _Path(__file__).resolve().parent / "era_compare_state.json"
REPORT_PATH = _Path(__file__).resolve().parent / "logs" / "era_compare_report.md"

ERAS = ("pre", "post")


def grade_eras() -> dict:
    """The study's grading, split at the cutoff. post = trades sold on or
    after CUTOFF (the new ratchet ran their exits)."""
    eras = {e: [] for e in ERAS}
    for g in (vs.grade_trades() or []):
        eras["post" if str(g.get("date") or "") >= CUTOFF else "pre"].append(g)
    return eras


def _stl(rows) -> list:
    return [g for g in rows if "stayed too long" in str(g.get("verdict") or "")]


def _class_costs(rows) -> dict:
    by = defaultdict(lambda: {"n": 0, "left": 0.0})
    for g in rows:
        k = str(g.get("verdict") or "?")
        by[k]["n"] += 1
        by[k]["left"] += float(g.get("left") or 0.0)
    return dict(by)


def _era_block(rows) -> dict:
    stl = _stl(rows)
    return {
        "trades": len(rows),
        "days": len({str(g.get("date") or "") for g in rows}),
        "stl_n": len(stl),
        "stl_left": round(sum(float(g.get("left") or 0.0) for g in stl), 2),
        "classes": {k: {"n": v["n"], "left": round(v["left"], 2)}
                    for k, v in sorted(_class_costs(rows).items(),
                                       key=lambda kv: -kv[1]["left"])},
    }


def summary() -> dict:
    """The whole readout, numbers and honesty together. `readable` gates
    every comparison — the scratch tool's WHAT-THE-DATA-CAN-PROVE section,
    reduced to the flag the voice actually needs."""
    eras = grade_eras()
    pre, post = _era_block(eras["pre"]), _era_block(eras["post"])
    s = {
        "generated": datetime.datetime.now().astimezone().isoformat(),
        "cutoff": CUTOFF,
        "target": TARGET_DATE,
        "min_post_trades": MIN_POST_TRADES,
        "readable": post["trades"] >= MIN_POST_TRADES,
        "pre": pre,
        "post": post,
    }
    s["lines"] = _report_lines(s)
    return s


def _avg_stl(era: dict) -> float:
    return (era["stl_left"] / era["stl_n"]) if era["stl_n"] else 0.0


def top_line(s: dict | None = None) -> str | None:
    """One speakable sentence for the cooldown review — the honest
    headline. None until the post-era can support the comparison (the
    review then says the armed-state line instead)."""
    s = s or summary()
    if not s.get("readable"):
        return None
    pre, post = s["pre"], s["post"]
    pre_avg, post_avg = _avg_stl(pre), _avg_stl(post)
    direction = ("fell" if post_avg < pre_avg
                 else "grew" if post_avg > pre_avg else "held")
    return (f"the re-base week-one read: stayed too long costs "
            f"{post_avg:,.0f} dollars a trade in the new era against "
            f"{pre_avg:,.0f} in the old — {direction} — "
            f"{post['stl_n']} trades against {pre['stl_n']}")


def _report_lines(s: dict) -> list:
    out = [
        f"cutoff {s['cutoff']} (the ratchet re-base) · generated "
        f"{s['generated'][:16]}",
        f"pre-era: {s['pre']['trades']} trades over {s['pre']['days']} days — "
        f"stayed-too-long {s['pre']['stl_n']} trades, "
        f"${s['pre']['stl_left']:,.2f} left "
        f"(avg ${_avg_stl(s['pre']):,.2f}/trade)",
        f"post-era: {s['post']['trades']} trades on {s['post']['days']} day(s) — "
        f"stayed-too-long {s['post']['stl_n']} trades, "
        f"${s['post']['stl_left']:,.2f} left "
        f"(avg ${_avg_stl(s['post']):,.2f}/trade)",
    ]
    for era in ("post", "pre"):
        if s[era]["classes"]:
            out.append(f"{era}-era class costs:")
            for k, v in s[era]["classes"].items():
                out.append(f"  {k:<46}{v['n']:>3}  ${v['left']:>9,.2f}")
    if not s["readable"]:
        out.append(f"NOT READABLE YET: the post-era holds {s['post']['trades']} "
                   f"trades against the {s['min_post_trades']}-trade bar — "
                   "no comparison is supported; this report records, it "
                   "does not compare.")
    return out


def write_report():
    """One section prepended to logs/era_compare_report.md (newest first,
    one run per night — the verdict-cost report's shape). Returns the
    path or None; never raises. Does NOT mark the one-shot done — the
    scheduler owns that state, so a manual run can never consume it."""
    try:
        s = summary()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        header = f"\n\n## ERA COMPARE — {s['generated'][:16]}\n"
        body = "\n".join(s["lines"]) + "\n"
        old = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
        REPORT_PATH.write_text(header + body + old, encoding="utf-8")
        return REPORT_PATH
    except Exception:
        return None


# ── THE ONE-SHOT SCHEDULER (date-gated, restart-proof) ────────────────
def due(now: datetime.datetime | None = None) -> bool:
    """True once the target date has arrived and this run has not been
    marked done. Date-gated, not time-gated: a scanner restart during the
    day cannot miss the window, and after mark_done it never re-runs."""
    now = now or datetime.datetime.now()
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    return (str(st.get("reported_on") or "") != TARGET_DATE
            and now.strftime("%Y-%m-%d") >= TARGET_DATE)


def mark_done(now: datetime.datetime | None = None) -> None:
    now = now or datetime.datetime.now()
    try:
        STATE_PATH.write_text(json.dumps({
            "reported_on": TARGET_DATE,
            "reported_at": now.astimezone().isoformat(),
        }, indent=1), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    _s = summary()
    print("\n".join(_s["lines"]))
    print("\ntop_line:", top_line(_s))
