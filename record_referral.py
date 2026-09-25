"""record_referral.py — THE REFERRALVELOCITY SEAM (Sep 25).

The charter's funding formula needs BOTH components to be real:

    DailyJackpot = min(20 USD, ReferralVelocity + MinerYield)

MinerYield is observed automatically (proxy_poller -> miner_yield).
ReferralVelocity can NOT be automated honestly: the fact that Kraken
PAID a referral bonus is an off-box fact only the OPERATOR knows.
This CLI is the honest seam — it records Kraken's paid bonus through
kraken_challenge.record_referral_bonus (the ONE brain for referral
credits: idempotent per winner+calendar-month, receipt into
jackpot_funding, the daily accrual then raises the real base).

It writes NOTHING to pool/server.py and touches no route — the pool
server's files are another thread's work and stay untouched.

COMMANDS
--------
  winners              list recent drawing winners (READ-ONLY) with
                       their miner id, worker name, payout and 30-day
                       gate state — the menu for --winner tokens
  show                 the funding picture (READ-ONLY): both formula
                       components, the computed daily plan, recent
                       ledger receipts
  record AMOUNT WINNER record Kraken's PAID bonus for a winner.
                       AMOUNT in USD. WINNER = miner id | wallet
                       address | worker name (ambiguous names are
                       refused with candidates). Writes require
                       --yes; without it the plan is printed and
                       nothing is written.

SAFETY
------
  - every write rides record_referral_bonus's idempotence (one bonus
    per winner per calendar month — a re-run cannot double-count)
  - a miner with no win inside 30 days is refused unless --force
    (a referral bonus follows a win; anything else wants eyes)
  - the ledger stores category, amount, and the opaque sponsor id —
    never a wallet address, never a key
  - the mandated phrases live in kraken_challenge/installer only —
    this tool references, never quotes

EXIT CODES  0 ok · 1 refused or error · 2 usage
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                       # noqa: E402
import jackpot_funding as funding           # noqa: E402
import kraken_challenge as kc               # noqa: E402


# ── winner resolution ─────────────────────────────────────────────────
def resolve_winner(token):
    """Resolve a winner token (miner id | wallet | worker name) to a
    miner row. Returns (miner_dict, None) or (None, candidates) when
    the token is ambiguous, or (None, []) when nothing matches."""
    token = (token or "").strip()
    if not token:
        return None, []
    if token.isdigit():
        with db.get_db() as conn:
            row = conn.execute("SELECT * FROM miners WHERE id = ?",
                               (int(token),)).fetchone()
        return (dict(row), None) if row else (None, [])
    with db.get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM miners WHERE wallet_address = ? OR "
            "worker_name = ?", (token, token)).fetchall()]
    if not rows:
        return None, []
    if len(rows) == 1:
        return rows[0], None
    return None, rows                       # ambiguous — operator picks


def recent_winners(limit=10):
    """Drawn drawings, newest first, joined to their miner."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT d.id AS drawing_id, d.jackpot_amount, d.house_tax, "
            "d.drawn_at, m.id AS miner_id, m.worker_name "
            "FROM drawings d JOIN miners m ON m.id = d.winner_id "
            "WHERE d.status = 'drawn' AND d.winner_id IS NOT NULL "
            "ORDER BY d.drawn_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


# ── commands ──────────────────────────────────────────────────────────
def cmd_winners(args):
    rows = recent_winners(args.limit)
    if not rows:
        print("no drawn winners yet — the pot pays first, then the loop begins")
        return 0
    print(f"{'drawing':>8}  {'miner':>6}  {'worker':<20} "
          f"{'payout XMR':>12}  {'drawn':<16}  30d gate")
    for r in rows:
        payout = float(r["jackpot_amount"] or 0) - float(r["house_tax"] or 0)
        drawn = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["drawn_at"]))
        gated = "won recently" if kc.miner_won_30d(r["miner_id"]) else "-"
        print(f"{r['drawing_id']:>8}  {r['miner_id']:>6}  "
              f"{(r['worker_name'] or '')[:20]:<20}  {payout:>12.6f}  "
              f"{drawn:<16}  {gated}")
    print("\nrecord Kraken's paid bonus with:  "
          "record_referral.py record AMOUNT MINER_ID --yes")
    return 0


def cmd_show(args):
    plan = funding.compute_daily_jackpot()
    ref = funding.referral_velocity_usd()
    mng = funding.miner_yield_usd()
    print("THE FUNDING PICTURE (the charter's formula, live)")
    print(f"  ReferralVelocity : ${ref:.4f} /day  (14-day average of "
          f"referral bonuses paid)")
    print(f"  MinerYield       : ${mng:.4f} /day  (14-day average of XMR "
          f"mined by Miner Bot users)")
    print(f"  raw              : ${plan['raw_usd']:.4f} /day")
    print(f"  DailyJackpot     : ${plan['daily_jackpot_usd']:.2f} "
          f"= {plan['daily_jackpot_xmr']:.8f} XMR"
          f"{'  [CAPPED at $20]' if plan['capped'] else ''}")
    print(f"  rate             : ${plan['xmr_usd']:.2f} / XMR")
    rows = funding._window_rows()
    if rows:
        print(f"\n  receipts in the 14-day window ({len(rows)}):")
        for r in sorted(rows, key=lambda x: x.get("ts", 0))[-10:]:
            day = time.strftime("%m-%d", time.localtime(r.get("ts", 0)))
            print(f"    {day}  {r.get('category', '?'):>14}  "
                  f"${float(r.get('amount_usd', 0)):>8.2f}  "
                  f"sponsor #{r.get('sponsor_id', '?')}  "
                  f"{r.get('source', '')[:40]}")
    else:
        print("\n  no receipts in the window yet — both components are "
              "still $0")
    return 0


def cmd_record(args):
    if args.amount is None or args.amount <= 0:
        print("refused: AMOUNT must be a positive number of USD")
        return 1
    miner, candidates = resolve_winner(args.winner)
    if miner is None:
        if candidates:
            print(f"refused: '{args.winner}' is ambiguous — "
                  f"{len(candidates)} miners share that name:")
            for c in candidates:
                print(f"    miner id {c['id']}  wallet …{c['wallet_address'][-8:]}  "
                      f"worker '{c['worker_name']}'")
            print("  pick one by miner id")
        else:
            print(f"refused: no miner matches '{args.winner}' — "
                  f"see `winners` for the menu")
        return 1
    mid = int(miner["id"])

    won_recently = kc.miner_won_30d(mid)
    if not won_recently and not args.force:
        print(f"refused: miner {mid} has no win inside 30 days — a "
              f"referral bonus follows a win. Re-check the winner, or "
              f"pass --force if this bonus is real.")
        return 1

    month = time.strftime("%Y-%m")
    print("THE PLAN")
    print(f"  miner          : #{mid}  worker '{miner['worker_name']}'")
    print(f"  bonus (Kraken) : ${args.amount:.2f}")
    print(f"  credit key     : {mid}:{month}  (one bonus per winner per "
          f"calendar month — idempotent)")
    print(f"  ledger         : jackpot_funding receipt 'referral_bonus', "
          f"then the daily accrual raises the real base")
    if not args.yes:
        print("\ndry plan only — add --yes to write it")
        return 0

    result = kc.record_referral_bonus(mid, args.amount,
                                      source=args.source)
    if not result.get("ok"):
        print(f"refused by the challenge module: {result.get('reason')} "
              f"(nothing double-counted)")
        return 1
    print(f"recorded: ledger row #{result['ledger_id']} "
          f"(key {result['key']})")
    plan = funding.compute_daily_jackpot()
    print(f"  ReferralVelocity now ${plan['referral_velocity_usd']:.4f}/day "
          f"→ DailyJackpot ${plan['daily_jackpot_usd']:.2f} "
          f"({plan['daily_jackpot_xmr']:.8f} XMR)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="record_referral.py",
        description="Record Kraken's paid referral bonus into the "
                    "jackpot funding ledger (ReferralVelocity).")
    sub = p.add_subparsers(dest="cmd")

    w = sub.add_parser("winners", help="list recent winners (read-only)")
    w.add_argument("--limit", type=int, default=10)
    w.set_defaults(func=cmd_winners)

    s = sub.add_parser("show", help="the funding picture (read-only)")
    s.set_defaults(func=cmd_show)

    r = sub.add_parser("record", help="record a paid bonus (writes)")
    r.add_argument("amount", type=float, help="bonus amount in USD")
    r.add_argument("winner", help="miner id | wallet | worker name")
    r.add_argument("--yes", action="store_true",
                   help="actually write (without it: dry plan)")
    r.add_argument("--force", action="store_true",
                   help="allow a miner with no win inside 30 days")
    r.add_argument("--source", default="kraken_challenge",
                   help="source note stored on the receipt")
    r.set_defaults(func=cmd_record)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
