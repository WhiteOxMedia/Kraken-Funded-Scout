"""THE RECOVERY SCENARIOS (ops tool, Sep 29): hold / add / exit what-ifs
for any watch pair, priced LIVE, any time — the operator's "what happens
if I..." desk, built the day the INJ ghost proved how expensive an
unpriced guess is.

  uv run python -X utf8 ops/recovery_scenarios.py
  uv run python -X utf8 ops/recovery_scenarios.py --pairs INJ,SOL
  uv run python -X utf8 ops/recovery_scenarios.py --pair INJ/USD --targets 7.5,8.0
  uv run python -X utf8 ops/recovery_scenarios.py --pair INJ/USD --add-usd 400
  uv run python -X utf8 ops/recovery_scenarios.py --prices '{"INJ/USD": 7.65}'
  uv run python -X utf8 ops/recovery_scenarios.py --json

What each scenario answers (all arithmetic rides the platform's own
helpers — contest_account.pnl_net_usd / max_entry_usd / entry_size_usd —
so the what-if can never disagree with the book the console keeps):

  HOLD   the position's live P&L (gross and net), % from entry, minutes
         held, how far the live price still is from the row's goal, and
         the coin's profiled worst-window exit (the window drawdown the
         3% guard uses) so holding has a priced worst case, not a hope.
  EXIT   at the live price (and at every --target): the net after the
         0.08% round trip, and the balance the ledger would move to IF
         the sale were booked. Nothing is booked here.
  ADD    the platform's own guard arithmetic at the live price: the
         coin's profiled window drawdown, the largest entry the 3% guard
         allows right now, the min(guard, account) size — or the
         operator's --add-usd — plus the blended entry and the net of
         exiting the whole blended book at the row's original goal.
         Every add line repeats the gate's truth: an add DCA's your
         entry only if the trend comes back.

READ-ONLY, DELIBERATELY: this tool loads the watch book through the
scanner's tombstone-free loader, reads the account state, and SELECTs
the bridge cooldown — it writes nothing, nowhere, ever. A scenario can
no more move the balance than a thought can. (The SOLD button and the
auto-close remain the only paths that book a trade.)

Live prices come from Kraken's public ticker (no keys, no trading
endpoint); --prices pins a price for an offline or reproducible run and
the tool labels those rows SIMULATED PRICE. A pair whose price cannot
be fetched prints UNPRICED rather than inventing a number — a what-if
built on a stale price is a lie with decimals.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── the platform's own arithmetic — never re-derived here ────────────
import contest_account as ca

GOAL_FALLBACK_PCT = 1.5   # a row without goal_pct: the standing floor
HELD_UNDATED = "added stamp unreadable"


# ── seams (each one rebindable in the pin drive — no live IO needed) ──

def watch_book() -> dict:
    """The tombstone-free watch book, via the scanner's own loader —
    the exact view the exit machinery prices (None markers filtered)."""
    from background_scanner import _load_contest_watch
    return _load_contest_watch()


def fetch_live_price(pair: str) -> float | None:
    """Kraken PUBLIC ticker — no keys, no order endpoint, read-only by
    construction. Returns None on any failure (the caller prints
    UNPRICED rather than guessing)."""
    try:
        import ccxt
        ex = ccxt.kraken({"enableRateLimit": True})
        return float(ex.fetch_ticker(pair)["last"])
    except Exception:
        return None


def bridge_reentry_gate(pair: str) -> tuple[bool, str]:
    """The signal bridge's re-entry verdict for RIGHT NOW: (open, note).
    is_in_cooldown counts the fresh-buy 30-min window AND the 4-hour
    loser cooldown (status includes closed) — the same gate a real
    re-buy would face seconds after this what-if prints."""
    try:
        from signal_bridge import is_in_cooldown
        if is_in_cooldown(pair):
            return False, "bridge cooldown holds (30-min window / 4h loser lock)"
        return True, "bridge gate open"
    except Exception as exc:   # fail-open: the note says why, not a fake pass
        return True, f"bridge gate unreadable ({exc}) — assume open, verify"


def account_balance() -> float:
    """The contest account's current balance, READ-ONLY (load_state)."""
    try:
        return float(ca.load_state().get("current_balance") or 0.0)
    except Exception:
        return 0.0


# ── the three scenarios (pure: row + price in, verdict dict out) ─────

def _held_minutes(row: dict, now: datetime) -> float | None:
    added = row.get("added")
    if not added:
        return None
    try:
        return (now - datetime.fromisoformat(str(added))).total_seconds() / 60.0
    except Exception:
        return None


def scenario_hold(row: dict, live: float, now: datetime | None = None) -> dict:
    """HOLD at the live price: what the book is worth right now, what
    the goal still needs, and what the coin's profiled worst window
    would leave of it."""
    now = now or datetime.now(ca.MST)
    entry = float(row.get("entry") or 0.0)
    size = float(row.get("size") or 0.0)
    goal_pct = float(row.get("goal_pct") or GOAL_FALLBACK_PCT)
    if entry <= 0 or size <= 0 or live <= 0:
        return {"scenario": "hold", "error": "row missing entry/size"}
    gross, cost, net = ca.pnl_net_usd(entry, live, size)
    target = entry * (1.0 + goal_pct / 100.0)
    dd_pct = ca.window_drawdown_pct(str(row.get("pair") or ""))
    dd_exit = entry * (1.0 - dd_pct / 100.0)
    _, _, dd_net = ca.pnl_net_usd(entry, dd_exit, size)
    held = _held_minutes(row, now)
    return {
        "scenario": "hold",
        "entry": round(entry, 6), "live": round(live, 6),
        "size_usd": round(size, 2),
        "pct_from_entry": round((live / entry - 1.0) * 100.0, 2),
        "pnl_gross": round(gross, 2), "cost_rt": round(cost, 2),
        "pnl_net": round(net, 2),
        "held_min": (round(held) if held is not None else None),
        "goal_pct": goal_pct,
        "target_price": round(target, 6),
        "goal_needs_pct_from_live": round((target / live - 1.0) * 100.0, 2),
        "window_dd_pct": dd_pct,
        "window_worst_exit": round(dd_exit, 6),
        "window_worst_net": round(dd_net, 2),
    }


def scenario_exit(row: dict, price: float, balance: float,
                  now: datetime | None = None) -> dict:
    """EXIT at `price`: the net after the round trip and the balance the
    ledger WOULD move to if booked (this tool never books it)."""
    now = now or datetime.now(ca.MST)
    entry = float(row.get("entry") or 0.0)
    size = float(row.get("size") or 0.0)
    if entry <= 0 or size <= 0 or price <= 0:
        return {"scenario": "exit", "error": "row missing entry/size"}
    gross, cost, net = ca.pnl_net_usd(entry, price, size)
    held = _held_minutes(row, now)
    return {
        "scenario": "exit",
        "exit_price": round(price, 6),
        "pct_from_entry": round((price / entry - 1.0) * 100.0, 2),
        "pnl_gross": round(gross, 2), "cost_rt": round(cost, 2),
        "pnl_net": round(net, 2),
        "balance_if_booked": round(float(balance or 0.0) + net, 2),
        "held_min": (round(held) if held is not None else None),
    }


def scenario_add(row: dict, live: float, balance: float,
                 add_usd: float | None = None) -> dict:
    """ADD at the live price, through the platform's OWN guard:
    window_drawdown_pct -> max_entry_usd -> entry_size_usd. With
    --add-usd the operator's number is priced TOO, and the guard's
    verdict on it is stated, not suggested."""
    entry = float(row.get("entry") or 0.0)
    size = float(row.get("size") or 0.0)
    goal_pct = float(row.get("goal_pct") or GOAL_FALLBACK_PCT)
    if entry <= 0 or size <= 0 or live <= 0:
        return {"scenario": "add", "error": "row missing entry/size"}
    dd_pct = ca.window_drawdown_pct(str(row.get("pair") or ""))
    guard_cap = ca.max_entry_usd(balance, dd_pct)
    guard_size = ca.entry_size_usd(balance, None, dd_pct)
    chosen = guard_size if add_usd is None else float(add_usd)
    add_size = min(chosen, float(balance or 0.0))
    out: dict = {
        "scenario": "add",
        "live": round(live, 6),
        "window_dd_pct": dd_pct,
        "guard_max_entry_usd": round(guard_cap, 2),
        "guard_size_usd": round(guard_size, 2),
        "requested_add_usd": (round(add_usd, 2) if add_usd is not None else None),
        "add_size_usd": round(add_size, 2),
        "within_guard": (add_usd is None or add_usd <= guard_cap + 1e-9),
        "balance": round(float(balance or 0.0), 2),
    }
    if add_size > 0:
        new_size = size + add_size
        blended = (entry * size + live * add_size) / new_size
        target = entry * (1.0 + goal_pct / 100.0)
        _, _, net_at_goal = ca.pnl_net_usd(blended, target, new_size)
        out.update({
            "blended_entry": round(blended, 6),
            "blended_vs_original_entry_pct": round(
                (blended / entry - 1.0) * 100.0, 2),
            "blended_exit_at_original_goal_net": round(net_at_goal, 2),
        })
    return out


# ── presentation ──────────────────────────────────────────────────────

def _fmt_usd(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def _sign(v: float) -> str:
    return "+" if v >= 0 else "-"


def render_row(pair: str, row: dict, live: float | None, simulated: bool,
               balance: float, targets: list[float], add_usd: float | None,
               now: datetime) -> list[str]:
    out = [f"\n── {pair} " + ("─" * max(4, 46 - len(pair)))]
    if not isinstance(row, dict) or not row:
        out.append("  (empty/tombstoned row — nothing to price)")
        return out
    entry = float(row.get("entry") or 0.0)
    out.append(f"  watch row: entry {entry:.4f} · size "
               f"{_fmt_usd(float(row.get('size') or 0.0))} · goal "
               f"+{float(row.get('goal_pct') or GOAL_FALLBACK_PCT):.1f}%"
               + (f" · added {str(row.get('added'))[:16]}"
                  if row.get("added") else ""))
    if live is None:
        out.append("  UNPRICED — no live price and no --prices override; "
                   "a what-if on a stale price is a lie with decimals")
        return out
    tag = " (SIMULATED PRICE)" if simulated else ""
    out.append(f"  live {live:.4f}{tag} · balance {_fmt_usd(balance)}")

    hold = scenario_hold(row, live, now)
    if "error" not in hold:
        out.append(f"  HOLD   net {_sign(hold['pnl_net'])}{abs(hold['pnl_net']):,.2f} "
                   f"({hold['pct_from_entry']:+.2f}%) · held "
                   f"{hold['held_min'] if hold['held_min'] is not None else HELD_UNDATED} min"
                   f"{'' if hold['held_min'] is not None else ''}")
        out.append(f"         goal +{hold['goal_pct']:.1f}% needs "
                   f"{hold['goal_needs_pct_from_live']:+.2f}% from live "
                   f"(target {hold['target_price']:.4f}) · profiled worst "
                   f"window -{hold['window_dd_pct']:.1f}% -> "
                   f"{_sign(hold['window_worst_net'])}{abs(hold['window_worst_net']):,.2f} net")

    ex = scenario_exit(row, live, balance, now)
    if "error" not in ex:
        out.append(f"  EXIT   at live: net {_sign(ex['pnl_net'])}{abs(ex['pnl_net']):,.2f} "
                   f"-> balance would be {_fmt_usd(ex['balance_if_booked'])} if booked "
                   f"(nothing is booked here)")
    for t in targets:
        ext = scenario_exit(row, t, balance, now)
        if "error" not in ext:
            out.append(f"         at {t:.4f}: net {_sign(ext['pnl_net'])}{abs(ext['pnl_net']):,.2f} "
                       f"-> balance {_fmt_usd(ext['balance_if_booked'])}")

    add = scenario_add(row, live, balance, add_usd)
    if "error" not in add:
        verdict = ("the guard allows it" if add["within_guard"]
                   else f"OVER the guard's {_fmt_usd(add['guard_max_entry_usd'])} cap")
        out.append(f"  ADD    guard: window dd -{add['window_dd_pct']:.1f}% "
                   f"-> max entry {_fmt_usd(add['guard_max_entry_usd'])} · "
                   f"guard size {_fmt_usd(add['guard_size_usd'])}"
                   + (f" · requested {_fmt_usd(add_usd)} ({verdict})"
                      if add_usd is not None else ""))
        if add.get("add_size_usd"):
            out.append(f"         adding {_fmt_usd(add['add_size_usd'])}: blended entry "
                       f"{add['blended_entry']:.4f} ({add['blended_vs_original_entry_pct']:+.2f}% "
                       f"vs original) · whole book out at the original goal: "
                       f"{_sign(add['blended_exit_at_original_goal_net'])}"
                       f"{abs(add['blended_exit_at_original_goal_net']):,.2f} net")
        out.append("         an add averages your entry only if the trend "
                   "comes back — the guard prices the risk, not the hope")

    open_gate, gate_note = bridge_reentry_gate(pair)
    out.append(f"  RE-ENTRY gate: {'OPEN' if open_gate else 'HOLDS'} — {gate_note}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="hold / add / exit what-ifs for watch pairs, priced "
                    "live — strictly read-only")
    ap.add_argument("--pairs", default="",
                    help="comma list; default = every live watch row")
    ap.add_argument("--pair", default="", help="single pair convenience")
    ap.add_argument("--targets", default="",
                    help="extra exit prices, comma list (e.g. 7.5,8.0)")
    ap.add_argument("--add-usd", type=float, default=None,
                    help="price a specific add size instead of the guard's")
    ap.add_argument("--prices", default="",
                    help='JSON {"PAIR": price} to pin prices (offline/repro)')
    ap.add_argument("--json", action="store_true", help="machine output")
    args = ap.parse_args()

    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    if args.pair:
        pairs.append(args.pair.strip().upper())
    targets = [float(t) for t in args.targets.split(",") if t.strip()]
    pinned: dict = {}
    if args.prices:
        try:
            pinned = {k.upper(): float(v)
                      for k, v in json.loads(args.prices).items()}
        except Exception:
            print("--prices must be JSON like {\"INJ/USD\": 7.65}")
            return 2

    book = watch_book()
    if pairs:
        rows = {p: book.get(p) for p in pairs}
    else:
        rows = dict(book)
    rows = {p: r for p, r in rows.items() if isinstance(r, dict) and r}
    if not rows:
        if pairs:
            missing = ", ".join(pairs)
            print(f"{missing}: not in the watch book right now — the "
                  "desk prices what the book holds (rows land when a "
                  "BUY popup auto-tracks)")
        elif args.json:
            print(json.dumps({"generated_at": datetime.now(ca.MST).isoformat(),
                              "balance": account_balance(), "read_only": True,
                              "pairs": {}, "note": "watch book is empty"},
                             indent=1))
        else:
            print("watch book is empty — nothing to scenario "
                  "(rows land when a BUY popup auto-tracks)")
        return 0

    now = datetime.now(ca.MST)
    balance = account_balance()
    priced: dict[str, float | None] = {}
    for p in rows:
        if p in pinned:
            priced[p] = pinned[p]
        else:
            priced[p] = fetch_live_price(p)

    if args.json:
        doc: dict = {
            "generated_at": now.isoformat(),
            "balance": balance,
            "read_only": True,
            "pairs": {},
        }
        for p, row in rows.items():
            live, sim = priced.get(p), p in pinned
            entry: dict = {"live": live, "simulated_price": sim}
            if live:
                hold = scenario_hold(row, live, now)
                ex = scenario_exit(row, live, balance, now)
                add = scenario_add(row, live, balance, args.add_usd)
                entry.update({
                    "hold": hold,
                    "exit_at_live": ex,
                    "exit_targets": [scenario_exit(row, t, balance, now)
                                     for t in targets],
                    "add": add,
                })
                entry["reentry_gate"] = bridge_reentry_gate(p)
            else:
                entry["error"] = "unpriced"
            doc["pairs"][p] = entry
        print(json.dumps(doc, indent=1, default=str))
        return 0

    print(f"RECOVERY SCENARIOS — {now.strftime('%Y-%m-%d %H:%M')} MST · "
          f"balance {_fmt_usd(balance)} · READ-ONLY (nothing here books, "
          "sells, or writes)")
    for p, row in rows.items():
        live = priced.get(p)
        for line in render_row(p, row, live, p in pinned, balance,
                               targets, args.add_usd, now):
            print(line)
    unpriced = [p for p, v in priced.items() if v is None]
    if unpriced:
        print("\nUNPRICED (ticker failed, no override): "
              + ", ".join(unpriced) + " — re-run or pin with --prices")
    print("\nthe arithmetic is the platform's own (pnl_net_usd / "
          "max_entry_usd / entry_size_usd); the SOLD button and the "
          "auto-close remain the only paths that book a trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
