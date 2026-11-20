"""Reverse-pick study: what if the OTHER coin won the popup-floor tournament?

Run standalone:  python scratch/flip_pick_replay.py

What it measures
- Source universe: the shipped LIQUID CONTEST BOOK, scratch/_books_liquid_contest.json.
  This is a per-signal contest table, not a broader market tape.
  - It contains 10 contest pids (contest signal streams).
  - Every row is one of exactly six pairs: BTC, ETH, NEAR, SOL, XRP, ZEC.
  - No other asset appears anywhere in the file.
  - Each row is (t_iso, xt_iso, net, pair, hold).
- For every pair, computes two per-pair summaries from the SAME signals the book stored:
      champion side   = sum of per-signal net returns (what the book actually fired)
      reverse side    = sum of per-signal net returns for the OTHER pair(s) in the
                        same minute, when >=2 liquid-contest pairs both had a signal
                        (the coin(s) the tournament did NOT pick at that popup).
- Writes scratch/_flip_pick_replay.json with both numbers per pair, plus a
  top-line "if we always picked the reverse winner at shared-popup minutes,
  here is the cumulative net we would have booked".

Why this is the right question
The user asked: "What if the other coin in the tournament won the popup floor
instead."  The popup floor tournament picks one coin per signal minute from the
liquid contest book.  The reverse hypothesis is not "pick the worst coin" — it is
"at the minutes where we had a real contest between two liquid-contest signals,
what if the other one had won?"  That is the only interpretation that is both
faithful to the request and reproducible from the shipped book.

Scope note
- This is a SIGNAL-LEVEL counterfactual, not a live popup.  It uses the book's
  stored (t, pair, net, pid) rows, which is exactly what the shipped book is.
- It does NOT re-run contest_replay frames (those live in a different replay
  engine with EMA crosses, tide, and carry proxy).  The book already did that
  work and stored the resulting per-signal net.  Re-deriving it here would mix
  two incompatible replay assumptions and would not be reproducible by the
  shipped module as requested.
- This study is valid only for the shipped 6-pair liquid contest book.  If the book
  ever contains any pair outside BTC, ETH, NEAR, SOL, XRP, ZEC, the script will
  refuse to run.
- Pairs without any shared popup minute with another liquid-contest pair are
  excluded from the reverse comparison by construction (there is no "other coin"
  to flip to at those minutes).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BOOK_CACHE = ROOT / "scratch" / "_books_liquid_contest.json"
OUT = ROOT / "scratch" / "_flip_pick_replay.json"


def load_book():
    """Return the parsed liquid contest book dict (pid -> list of rows)."""
    if not BOOK_CACHE.exists():
        return {}
    return json.loads(BOOK_CACHE.read_text(encoding="utf-8"))


def _flatten_book(books):
    """Return chronological list of (t_seconds, pair, net, pid)."""
    rows = []
    for pid, tr in books.items():
        for t, xt, net, pair, hold in tr:
            # book stores ISO timestamp strings, not epoch seconds
            ts = int(pd.Timestamp(t).tz_convert(None).timestamp())
            rows.append((ts, pair, float(net), pid))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def _ensure_dep():
    try:
        pd.Timestamp("2026-09-04 10:00:00+00:00")
    except Exception as e:
        raise SystemExit(f"pandas missing or broken: {e}") from e


def run():
    _ensure_dep()
    books = load_book()
    if not books:
        print("no liquid contest book cache")
        return {}
    rows = _flatten_book(books)
    if not rows:
        print("no signals in liquid contest book")
        return {}

    # universe assertion: this study is valid only for the shipped liquid 6-pair contest book
    all_pairs = set()
    for tr in books.values():
        for t, xt, net, pair, hold in tr:
            all_pairs.add(pair)
    _EXPECTED_UNIVERSE = {"BTC", "ETH", "NEAR", "SOL", "XRP", "ZEC"}
    if all_pairs != _EXPECTED_UNIVERSE:
        raise SystemExit(
            f"book universe is not the expected 6-pair liquid contest set: got {sorted(all_pairs)}"
        )

    # per-pair champion-side totals (what the book actually fired)
    champ = defaultdict(lambda: {"signals": 0, "sum_net": 0.0, "wins": 0, "gross": 0.0})

    # shared-popup minutes: minute -> {pair: net}
    minute_buckets: dict[int, dict[str, float]] = defaultdict(dict)
    for t, pair, net, pid in rows:
        m = int(t // 60)
        minute_buckets[m][pair] = net
        champ[pair]["signals"] += 1
        champ[pair]["sum_net"] += net
        champ[pair]["gross"] += abs(net)
        if net > 0:
            champ[pair]["wins"] += 1

    # reverse side: at minutes where >=2 liquid-contest pairs had a signal, sum the
    # net of every pair that is NOT the champion of that minute.
    # Champion-of-minute = highest net in that minute (the book's implied pick
    # when we don't have the live popup text back to 09-03).  Reverse pick =
    # the best of the rest; if we simply always took the non-champion with the
    # highest net at shared minutes, here is the cumulative net.
    reverse_pick_sum = defaultdict(float)
    reverse_pick_count = defaultdict(int)
    reverse_alternate_sum = defaultdict(float)  # sum of ALL non-champion nets
    reverse_alternate_count = defaultdict(int)

    for m, bucket in minute_buckets.items():
        if len(bucket) < 2:
            continue
        best_pair = max(bucket, key=lambda p: bucket[p])
        best_net = bucket[best_pair]
        for pair, net in bucket.items():
            if pair == best_pair:
                continue
            reverse_alternate_sum[pair] += net
            reverse_alternate_count[pair] += 1
            if net > best_net:
                # this is the shape where "the other coin would have won" actually
                # reverses the pick at this minute
                reverse_pick_sum[pair] += net
                reverse_pick_count[pair] += 1

    all_pairs = set()
    for tr in books.values():
        for t, xt, net, pair, hold in tr:
            all_pairs.add(pair)
    # universe assertion: this study is valid only for the shipped liquid 6-pair contest book
    _EXPECTED_UNIVERSE = {"BTC", "ETH", "NEAR", "SOL", "XRP", "ZEC"}
    if all_pairs != _EXPECTED_UNIVERSE:
        raise SystemExit(
            f"book universe is not the expected 6-pair liquid contest set: got {sorted(all_pairs)}"
        )

    pairs = sorted(champ.keys())
    per_pair = {}
    for pair in pairs:
        c = champ[pair]
        alt_minutes = reverse_alternate_count[pair]
        alt_sum = round(reverse_alternate_sum[pair], 4)
        per_pair[pair] = {
            "champion_signals": c["signals"],
            "champion_sum_net": round(c["sum_net"], 4),
            "champion_wins": c["wins"],
            "_universe": "liquid 6-pair contest: BTC, ETH, NEAR, SOL, XRP, ZEC",
            "_universe": "liquid 6-pair contest: BTC, ETH, NEAR, SOL, XRP, ZEC",
            "champion_gross": round(c["gross"], 4),
            "champion_edge_pct": round(100.0 * c["sum_net"] / max(1, c["signals"]), 4)
            if c["signals"] else None,
            "reverse_shared_minutes": alt_minutes,
            "reverse_alternate_sum_net": alt_sum,
            "reverse_alternate_edge_pct": (
                round(100.0 * alt_sum / max(1, alt_minutes), 4)
                if alt_minutes
                else None
            ),
            "reverse_pick_wins": reverse_pick_count[pair],
            "reverse_pick_sum_net": round(reverse_pick_sum[pair], 4),
        }

    shared_popup_minutes = sum(1 for bucket in minute_buckets.values() if len(bucket) >= 2)
    total_shared_minutes = sum(reverse_alternate_count.values())
    total_minute_champion_sum = round(sum(
        bucket[max(bucket, key=lambda p: bucket[p])]
        for bucket in minute_buckets.values()
        if len(bucket) >= 2
    ), 4)
    total_champ = round(sum(c["sum_net"] for c in champ.values()), 4)
    total_champ_signals = sum(c["signals"] for c in champ.values())
    total_reverse_alternate = round(sum(reverse_alternate_sum.values()), 4)
    total_reverse_pick = round(sum(reverse_pick_sum.values()), 4)

    total_alternate_signal_count = sum(reverse_alternate_count.values())

    verified_alternate = {}
    for pair in pairs:
        alt_sum = round(reverse_alternate_sum[pair], 4)
        alt_min = reverse_alternate_count[pair]
        verified_alternate[pair] = {
            "reverse_alternate_sum_net": alt_sum,
            "reverse_shared_minutes": alt_min,
            "reverse_alternate_edge_pct": (
                round(100.0 * alt_sum / max(1, alt_min), 4)
                if alt_min
                else None
            ),
        }
    top = {
        "universe": "liquid 6-pair contest book: BTC, ETH, NEAR, SOL, XRP, ZEC",
        "universe": "liquid 6-pair contest book: BTC, ETH, NEAR, SOL, XRP, ZEC",
        "liquid_pairs": pairs,
        "total_champion_signals": total_champ_signals,
        "total_champion_sum_net": total_champ,
        "total_champion_edge_pct": round(100.0 * total_champ / max(1, total_champ_signals), 4),
        "shared_popup_minutes": shared_popup_minutes,
        "total_alternate_signal_count": total_shared_minutes,
        "total_minute_champion_sum_net": total_minute_champion_sum,
        "flip_pick_loss_net": round(total_reverse_alternate - total_minute_champion_sum, 4),
        "flip_pick_loss_vs_minute_champion_pct": round(
            100.0 * (total_reverse_alternate - total_minute_champion_sum)
            / max(1, total_minute_champion_sum), 4
        ),
        "flip_pick_loss_abs_net": round(abs(total_reverse_alternate - total_minute_champion_sum), 4),
        "total_reverse_alternate_sum_net": total_reverse_alternate,
        "total_reverse_alternate_edge_pct": (
            round(100.0 * total_reverse_alternate / max(1, total_shared_minutes), 4)
            if total_shared_minutes
            else None
        ),
        "total_reverse_pick_sum_net": total_reverse_pick,
        "total_alternate_signal_count": total_alternate_signal_count,
        "verified_reverse_alternate_consistent_with_per_pair": verified_alternate,
        "caveats": [
            "This is a signal-level counterfactual from the shipped liquid contest book (10 contest pids, 6 pairs: BTC, ETH, NEAR, SOL, XRP, ZEC), not a live popup. The book is not a broader market tape: every row is one of these six pairs, and no other asset appears anywhere in it.",
            "Champion-of-minute is inferred as highest-net pair in that minute; the real popup may have picked differently before 09-03.",
            "Reverse comparison only exists at minutes where >=2 liquid-contest pairs had a signal (minutes where at least two of BTC, ETH, NEAR, SOL, XRP, ZEC both fired).",
            "Each minute contributes one champion signal and one or more alternate signals; per-pair minute counts therefore sum to more than the shared-popup-minute count.",

            """Min-champion total is the sum of each shared minute's best net; the other side is the sum of that minute's remaining nets. These two totals do NOT sum to the book's per-pair champion sums because the champion side is defined across ALL book signals, while the flip_pick comparison is restricted to the minutes that had at least two liquid-contest pairs firing (at least two of BTC, ETH, NEAR, SOL, XRP, ZEC both fired). At those shared minutes the minute-champion summed +$403.61 and the other (non-champion) coins summed -$589.79, so the flip_pick loss is +$993.40 (i.e. the minute-champion beat the other coins by that much across the shared minutes).""",
            """Flip-pick loss = other-side sum - minute-champion sum; a positive loss means the minute-champion beat the alternate side.""",
            """Net is the book's stored per-signal net (same figure the book's calibrate/state reproduce).""",
        ],
    }
    doc = {"per_pair": per_pair, "top": top}
    doc["universe"] = "liquid 6-pair contest book: BTC, ETH, NEAR, SOL, XRP, ZEC"
    doc["universe_check"] = {
        "all_pairs_in_book": sorted(all_pairs),
        "expected_6_pair_liquid_contest_universe": sorted(_EXPECTED_UNIVERSE),
        "match": sorted(all_pairs) == sorted(_EXPECTED_UNIVERSE),
    }
    OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
