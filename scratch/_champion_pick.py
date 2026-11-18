"""Pick the champion the way the mission asks: buy low AND sell high.

`_timing_tournament.py` now promotes only rows whose entry timing and exit
quality both sit in the top third of the clean rows (CHAMPION_*_Q).  A full
re-run costs the whole stage ladder, so this script applies the SAME rule to
the last tournament artifact already on disk and writes the champion the live
path can read:

    scratch/edge_champions.json

It also prices the rule against the alternative: the best clean row by the old
rank, the best by the timing-gated rank, and the mean forward return of each
selection's top ten.  That is the only honest way to say "better timed" - a
different champion is not an improvement until its own forward record is
better than the one it replaced.

Usage: python scratch/_champion_pick.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import _timing_tournament as tt                        # noqa: E402

ART = ROOT / "scratch" / "edge_timing_tournament.json"
OUT = ROOT / "scratch" / "edge_champions.json"

CONFIG_KEYS = ("id", "entry_max", "mover", "sell_high", "size", "slots",
               "breadth", "bank_r", "trim_rollover")


def _key(r: dict) -> tuple:
    return tuple(str(r.get(k)) for k in CONFIG_KEYS)


def _rows(doc: dict) -> list[dict]:
    """Every priced row in the artifact, one per config, finalists preferred."""
    out, seen = [], set()
    for r in list(doc.get("finalists") or []) + list(doc.get("stage1_rows") or []):
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _cfg(r: dict) -> dict:
    c = {k: r.get(k) for k in CONFIG_KEYS}
    c["risk_cap"] = r.get("risk_cap_pct", r.get("risk_cap"))
    return c


def _evidence(r: dict) -> dict:
    return {"weeks_12": r.get("weeks_12"),
            "forward_ret_pct": r.get("forward_ret_pct"),
            "per_trade_points": r.get("per_trade_points"),
            "win_rate": r.get("win_rate"),
            "best_week": r.get("best_week"),
            "trades_per_week": r.get("trades_per_week"),
            "entry_timing_score": r.get("entry_timing_score"),
            "sell_high_score": r.get("sell_high_score"),
            "timing_score": r.get("timing_score")}


def _mean(xs):
    ys = [x for x in xs if x is not None]
    return round(statistics.mean(ys), 3) if ys else None


def main() -> int:
    doc = json.loads(ART.read_text(encoding="utf-8"))
    rows = _rows(doc)
    clean = [r for r in rows if tt.clean(r)]
    if not clean:
        print("no clean rows in this artifact - nothing to pick")
        return 1
    # THE CONTENDER SET, exactly as the tournament defines it
    promoted_all = [r for r in clean if (r.get("weeks_12") or 0) >= 1]
    contenders = promoted_all or clean
    bars = tt.timing_bars(contenders)
    gated = [r for r in contenders if tt.timing_ok(r, bars)]
    print(f"{len(rows)} rows, {len(clean)} clean, {len(promoted_all)} with a "
          f"+12% week, {len(gated)}/{len(contenders)} buy low AND sell high "
          f"(entry >= {bars['entry']}, exit >= {bars['exit']})")

    old_rank = sorted(contenders, key=tt.rank_key, reverse=True)
    new_rank = sorted(gated or contenders, key=tt.rank_key, reverse=True)
    old_top, new_top = old_rank[:10], new_rank[:10]

    # the promotion rule the tournament uses: a +12% week AND the timing bars;
    # with nothing promoted yet the timing-first shortlist is what ships
    promoted = [r for r in promoted_all if tt.timing_ok(r, bars)]
    status = "promoted" if promoted else (
        "timing-first shortlist (no +12% week on this tape)" if gated
        else "none")
    champ = (max(promoted, key=tt.rank_key) if promoted
             else (max(gated, key=tt.rank_key) if gated else None))
    old_champ = doc.get("champion")
    out = {
        "rule": {"promotion": "+12% week, clean chain, AND entry timing + sell "
                              "quality both in the top third of clean rows",
                 "entry_quantile": tt.CHAMPION_ENTRY_Q,
                 "exit_quantile": tt.CHAMPION_EXIT_Q,
                 "bars": bars,
                 "source": ART.name,
                 "swapped_for_timing": bool(
                     champ is not None and old_champ is not None
                     and _key(champ) != _key(old_champ))},
        "champion_status": status,
        "counts": {"rows": len(rows), "clean": len(clean),
                   "with_12pct_week": len(promoted_all),
                   "contenders": len(contenders),
                   "timing_gated": len(gated), "promoted": len(promoted)},
        "champion": ({"config": _cfg(champ), "evidence": _evidence(champ)}
                     if champ else None),
        "previous_champion": ({"config": _cfg(old_champ),
                               "evidence": _evidence(old_champ)}
                              if old_champ else None),
        "top_by_timing": [{"config": _cfg(r), "evidence": _evidence(r)}
                          for r in new_top],
        "top_by_old_rank": [{"config": _cfg(r), "evidence": _evidence(r)}
                            for r in old_top],
        "selection_comparison": {
            "old_top10_mean_forward_pct": _mean(
                [r.get("forward_ret_pct") for r in old_top]),
            "timing_top10_mean_forward_pct": _mean(
                [r.get("forward_ret_pct") for r in new_top]),
            "old_top10_mean_per_trade": _mean(
                [r.get("per_trade_points") for r in old_top]),
            "timing_top10_mean_per_trade": _mean(
                [r.get("per_trade_points") for r in new_top]),
            "old_top10_mean_win_rate": _mean([r.get("win_rate") for r in old_top]),
            "timing_top10_mean_win_rate": _mean(
                [r.get("win_rate") for r in new_top]),
            "old_top10_weeks_12": sum((r.get("weeks_12") or 0)
                                      for r in old_top),
            "timing_top10_weeks_12": sum((r.get("weeks_12") or 0)
                                         for r in new_top),
            "gated_mean_forward_pct": _mean([r.get("forward_ret_pct")
                                             for r in gated]),
            "ungated_mean_forward_pct": _mean([r.get("forward_ret_pct")
                                               for r in contenders
                                               if not tt.timing_ok(r, bars)]),
            "gated_mean_win_rate": _mean([r.get("win_rate") for r in gated]),
            "ungated_mean_win_rate": _mean(
                [r.get("win_rate") for r in contenders
                 if not tt.timing_ok(r, bars)]),
        },
        "ordering": doc.get("ordering"),
    }
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")

    if champ:
        print("champion:", json.dumps(_cfg(champ)))
        print("  evidence:", json.dumps(_evidence(champ)))
    c = out["selection_comparison"]
    print("top10 forward: old %s%% -> timing %s%%" %
          (c["old_top10_mean_forward_pct"], c["timing_top10_mean_forward_pct"]))
    print("top10 per-trade points: old %s -> timing %s" %
          (c["old_top10_mean_per_trade"], c["timing_top10_mean_per_trade"]))
    print("top10 win rate: old %s -> timing %s" %
          (c["old_top10_mean_win_rate"], c["timing_top10_mean_win_rate"]))
    print("gated vs ungated (same field): forward %s%% vs %s%%, win %s vs %s"
          % (c["gated_mean_forward_pct"], c["ungated_mean_forward_pct"],
             c["gated_mean_win_rate"], c["ungated_mean_win_rate"]))
    print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
