"""THE CHARTER DRIVE (Sep 25, user order: the full Monero Project
charter) — the self-adjusting jackpot, the 30-day win limit, the
Kraken Challenge referral loop, and the phrases, pinned over the REAL
engine and REAL funding model, one sandboxed DB, no network, no RPC.

THE RULES UNDER TEST (charter, byte for byte):
  DailyJackpot = min(20 USD, ReferralVelocity + MinerYield)
  ReferralVelocity = rolling 14-day average of referral bonuses paid
  MinerYield = rolling 14-day average of XMR mined by active Miner Bot users
  ... and: a user may win once every 30 days (they keep mining, keep
  tickets, keep participating — only the claim waits) ...

THE MONEY TRUTH (no phantom funds, no double-count): the funding
ledger is a RECEIPT book in USD; the DAILY ACCRUAL is the one place
funding becomes base XMR (db.add_funding_base via the engine hook),
idempotent per calendar day; claims/expiry keep their exact
settlement semantics underneath.

Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db

_tmp = tempfile.mkdtemp(prefix="jackpot_charter_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()
# THE RATE IS PINNED (Oct 26): the pool now converts at the LIVE Kraken
# ticker rate, so any check that asserts dollars must fix the rate it
# converts at — otherwise these exact assertions would pass or fail with
# the market instead of with the formula they are grading.
db.set_setting("XMR_USD_RATE", "323.0")

import jackpot_engine as engine
import jackpot_funding as funding
import kraken_challenge as challenge
import scout_jackpot_view as sjv

engine.get_next_block_hash = lambda: "cd" * 32
_Blocked = types.ModuleType("monero_wallet")
_Blocked.rpc_available = lambda: False
sys.modules["monero_wallet"] = _Blocked

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


def _mk_drawing(status, drawn_at=None, claim=None, winner=None, deadline=None):
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at, claim_status, claim_deadline) "
            "VALUES (0.093, 0.0093, ?, ?, ?, ?, ?)",
            (status, winner, drawn_at, claim, deadline))
        return cur.lastrowid


print("== 1. THE FORMULA, byte for byte ==")
funding._save_ledger([])                              # clean slate
_w = time.time()
# 14 days of referral bonuses: 5/day for 7 days = 35 total
for d in range(7):
    funding.record_funding("referral_bonus", 5.0, sponsor_id=None,
                           source=f"drive day -{d}")
# 14 days of mining: 0.005 XMR/day = 1.615 USD/day at rate 323
for d in range(14):
    funding.record_funding("mining_yield", 0.005 * funding.xmr_usd_rate(),
                           sponsor_id=None, source="miner bot")
_plan = funding.compute_daily_jackpot(_w)
check(1.1, abs(_plan["referral_velocity_usd"] - 2.5) < 1e-6,
      f"ReferralVelocity = 35 over 14 days = {_plan['referral_velocity_usd']} (avg 2.5)")
check(1.2, abs(_plan["miner_yield_usd"] - 1.615) < 0.01,
      f"MinerYield = 0.005 XMR/day valued at rate = {_plan['miner_yield_usd']} (avg ~1.615)")
check(1.3, abs(_plan["raw_usd"] - 4.115) < 0.02
      and abs(_plan["daily_jackpot_usd"] - 4.12) < 0.05
      and not _plan["capped"],
      f"DailyJackpot = min(20, 2.5+1.615) = {_plan['daily_jackpot_usd']} (uncapped)")
check(1.4, abs(_plan["daily_jackpot_xmr"] - _plan["daily_jackpot_usd"] / funding.xmr_usd_rate()) < 1e-8,
      f"the XMR figure converts at the tracked rate: {_plan['daily_jackpot_xmr']} XMR")

# the cap: flood the ledger so raw >> 20
for _ in range(3):
    funding.record_funding("referral_bonus", 500.0, sponsor_id=None,
                           source="cap drill")
_plan_c = funding.compute_daily_jackpot(_w)
check(1.5, _plan_c["daily_jackpot_usd"] == 20.0 and _plan_c["capped"] is True,
      f"the $20 ceiling holds: {_plan_c['daily_jackpot_usd']} (capped={_plan_c['capped']})")

# the window: a bonus 15 days old leaves the average
funding.record_funding("referral_bonus", 140.0, sponsor_id=None,
                       source="stale")
_stale_ts = _w - 15 * 86400
_rows = funding._load_ledger()
_rows[-1]["ts"] = _stale_ts
funding._save_ledger(_rows)
_plan_w = funding.compute_daily_jackpot(_w)
# the honest window drill: age EVERYTHING out, then seed a fresh
# 14-day spread with one stale 140.0 bonus on day -15 — the average
# must see only the fresh 14 days
funding._save_ledger([])
for d in range(7):
    funding.record_funding("referral_bonus", 5.0, sponsor_id=None,
                           source="window drill")
for d in range(14):
    funding.record_funding("mining_yield", 0.005 * funding.xmr_usd_rate(),
                           sponsor_id=None, source="window drill")
_rows = funding._load_ledger()
_stale = dict(_rows[-1])                             # APPEND a copy as the
_stale["ts"] = _w - 15 * 86400                       # stale row (do not
_stale["id"] = 1 + max(int(r.get("id", 0)) for r in _rows)
_stale["category"] = "referral_bonus"                # mutate a fresh one)
_stale["amount_usd"] = 140.0
_rows.append(_stale)
funding._save_ledger(_rows)
_plan_w = funding.compute_daily_jackpot(_w)
check(1.6, abs(_plan_w["referral_velocity_usd"] - 2.5) < 0.01
      and not _plan_w["capped"]
      and abs(_plan_w["daily_jackpot_usd"] - 4.12) < 0.05,
      f"the 15-day-old $140 bonus is OUTSIDE the window: velocity reads "
      f"{_plan_w['referral_velocity_usd']:.2f} (the fresh 14 days only)")

print("== 2. THE DAILY ACCRUAL: money once, idempotent, real ==")
db.set_setting("jackpot_balance", "0.0")
_a1 = funding.accrue_daily_funding(_w)
check(2.1, _a1["accrued"] is True
      and abs(engine.get_jackpot_balance()
              - _a1["plan"]["daily_jackpot_xmr"]) < 1e-9,
      f"the accrual landed the formula's XMR as REAL base: "
      f"{engine.get_jackpot_balance():.8f}")
_a2 = funding.accrue_daily_funding(_w + 60)          # same day, later
check(2.2, _a2["accrued"] is False
      and abs(engine.get_jackpot_balance()
              - _a1["plan"]["daily_jackpot_xmr"]) < 1e-9,
      "a same-day re-run is a NO-OP (no double-count)")
_tomorrow = _w + 86400
for _r in funding._load_ledger():                    # age out the window
    if _r.get("category"):
        _r["ts"] = _tomorrow - (86400 * 14 - 3600)
funding._save_ledger(funding._load_ledger())
_a3 = funding.accrue_daily_funding(_tomorrow)
check(2.3, _a3["accrued"] is True and _a3["day"] != _a1["day"],
      "a NEW day accrues again (the daily self-adjustment)")
# the cap drill, honestly: flood fresh 14-day rows far past $20
for _ in range(3):
    funding.record_funding("referral_bonus", 500.0, sponsor_id=None,
                           source="cap drill")
_wk = _tomorrow + 86400
_a4 = funding.accrue_daily_funding(_wk)
check(2.4, _a4.get("accrued") is True
      and _a4.get("plan", {}).get("capped") is True
      and abs(_a4.get("plan", {}).get("daily_jackpot_xmr", 0)
              - 20.0 / funding.xmr_usd_rate()) < 1e-8,
      f"the cap carries into the accrual: "
      f"{_a4.get('plan', {}).get('daily_jackpot_xmr', 0):.8f} XMR = $20 at rate")

print("== 3. THE 30-DAY WIN LIMIT (draw + claim) ==")
with db.get_db() as conn:                            # the accrual is a
    conn.execute("DELETE FROM audit_log WHERE action IN "  # noise here
                 "('funding_accrual', 'winner_limit_skip')")
    conn.execute("DELETE FROM drawings")            # fresh draw slate
    conn.execute("UPDATE tickets SET drawing_id = NULL")
db.set_setting("jackpot_balance", "0.10")   # a REAL settle-able pot:
db.set_setting("min_jackpot_xmr", "0.01")   # each drawing pays from
_w1 = "4" + "a" * 95                        # real base (the charter's
_w2 = "4" + "b" * 95                        # own no-phantom rule) —
_m1 = db.get_or_create_miner(_w1, worker_name="solo-x")["id"]
_m2 = db.get_or_create_miner(_w2, worker_name="other-x")["id"]
# draw 1, DETERMINISTIC: solo is alone in the ring (100 tickets, other
# has none yet) — a certain win, no flake, no cascading gates
db.set_setting("jackpot_balance", "0.10")
db.add_tickets(_m1, "free", 100)
_d1 = engine.run_drawing()
check(3.1, _d1.get("status") == "drawn" and _d1.get("winner") == _w1,
      f"draw 1: solo wins alone in the ring ({_d1.get('status')})")
# now solo is inside the 30-day window — other ENTERS now (charter: he
# keeps earning tickets), and 99 fresh solo tickets vs his 1: the gate
# must skip solo's WHOLE block (the first draft shifted once and handed
# the win to solo's own next ticket; the drive caught it). The base
# rises to 0.30 so the REAL earmark model can back a second pending
# drawing (balance = base - 0.093 pending must clear the minimum).
db.set_setting("jackpot_balance", "0.30")
db.add_tickets(_m2, "free", 1)
db.add_tickets(_m1, "free", 99)
_d2 = engine.run_drawing()
check(3.2, _d2.get("status") == "drawn" and _d2.get("winner") != _w1,
      f"draw 2: the recent winner is SKIPPED — {str(_d2.get('winner') or '?')[:12]}... won instead")
with db.get_db() as conn:
    _skips = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action "
                          "= 'winner_limit_skip'").fetchone()["c"]
check(3.3, _skips >= 1, "the skip is AUDITED (winner_limit_skip)")
# claim path: solo's old win (drawing 1) still claimable — the LIMIT
# is about new WINS, not the claim of an existing one. But if the
# flake made OTHER the draw-1 winner (settled + gated above), claim
# THAT win instead — the pin is 'a pending pre-limit win claims fine'.
_dk = _d1["drawing_id"]
_claimer = _d1.get("winner")
_claimer_w = _w1 if _claimer == _w1 else _w2
with db.get_db() as conn:
    conn.execute("UPDATE drawings SET winner_wallet = ? WHERE id = ?",
                 (_claimer_w, _dk))
_r = engine.process_winner_claim(_dk, _claimer_w)
check(3.4, _r.get("status") == "ok",
      "the FIRST claim (inside his own 30-day window) still goes through — "
      "the claim-side gate counts OTHER wins, not the one being claimed")
# THE CLAIM-SIDE GATE, now REAL (review triage, Sep 25: the charter
# ordered 'Reject claim if within 30-day cooldown' and the code carried
# only a comment — the old fixtures masked it because every claim was a
# miner's FIRST): a miner with a SECOND fresh win inside the window is
# refused at claim time, audited, and retriable after the cooldown.
_others = [m for m in (_m1, _m2) if m != _claimer]
_other_miner = _others[0]
_dg = _mk_drawing("drawn", drawn_at=time.time(), claim="pending",
                  winner=_other_miner, deadline=time.time() + 7 * 86400)
_wg = _w1 if _other_miner == _m1 else _w2
with db.get_db() as conn:
    conn.execute("UPDATE drawings SET winner_wallet = ? WHERE id = ?",
                 (_wg, _dg))
_r2 = engine.process_winner_claim(_dg, _wg)
check(3.5, _r2.get("status") == "error"
      and "30-day" in _r2.get("reason", ""),
      f"a claimant whose OTHER win is inside the window is REFUSED at "
      "claim time (the charter's claim-time cooldown, enforced)")
_dg_refused = _dg
with db.get_db() as conn:
    _ref = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action "
                        "= 'claim_refused_30_day_limit'").fetchone()["c"]
check(3.6, _ref >= 1,
      "the refusal is AUDITED (claim_refused_30_day_limit)")
with db.get_db() as conn:
    _dg_state = conn.execute("SELECT claim_status FROM drawings "
                             "WHERE id = ?", (_dg_refused,)).fetchone()
check(3.7, _dg_state and _dg_state["claim_status"] == "pending",
      "the refused claim stays pending — retriable after the cooldown, "
      "never destroyed")

# THE ALL-GATED ROUND, TIMED OUT OF EXISTENCE (second review triage,
# Sep 25 evening batch: the clock fix was claimed in the morning's
# commit message but lost in a patch bounce — and NO DRIVE EXERCISED
# the refire, so the gap lived all day. This pin closes exactly that.)
_all_gated = [m for m in (_m1, _m2)]   # both are now inside the window
db.set_setting("jackpot_balance", "0.50")   # the earlier claims drained it;
                                            # each pending drawn earmarks
                                            # 0.0837, so 0.50 clears them all
db.add_tickets(_m1, "free", 10)             # fresh tickets: the section's
                                            # earlier ones settled already
_d3 = engine.run_drawing()             # every holder gated -> cancelled
check(3.8, _d3.get("status") == "skipped"
      and "cancelled" in _d3.get("reason", ""),
      f"the all-gated round is CANCELLED ({_d3.get('reason', '')[:60]})")
with db.get_db() as conn:
    _c = conn.execute("SELECT drawn_at FROM drawings "
                      "WHERE status = 'cancelled' AND winner_id IS NULL "
                      "ORDER BY id DESC LIMIT 1").fetchone()
check(3.9, _c and _c["drawn_at"] is not None and _c["drawn_at"] > 0,
      "the cancelled round IS STAMPED (drawn_at written) — the "
      "scheduler's clock can read it")
_sched = engine.check_drawing_schedule()
check(3.10, _sched.get("status") == "waiting"
      and 0 < _sched.get("next_drawing_in", -1) < 24 * 3600,
      f"the scheduler WAITS after a cancellation ({_sched.get('status')}, "
      f"{_sched.get('next_drawing_in', 0):.0f}s) — no refire loop, no "
      "drawing-per-cycle")
with db.get_db() as conn:
    _nc = conn.execute("SELECT COUNT(*) c FROM drawings").fetchone()["c"]
engine.check_drawing_schedule()
with db.get_db() as conn:
    _nc2 = conn.execute("SELECT COUNT(*) c FROM drawings").fetchone()["c"]
check(3.11, _nc2 == _nc,
      f"a second schedule tick creates NO drawing ({_nc} -> {_nc2}) — "
      "the infinite-creation loop is dead, pinned by count")

print("== 4. THE KRAKEN CHALLENGE LOOP ==")
challenge.set_referral_link("")                      # not configured yet
_g = challenge.guide_lines()
check(4.1, len(_g) == 5 and any("ask the community admin" in l for l in _g),
      "the guide renders; an unset referral link is NAMED, not faked")
challenge.set_referral_link("https://www.kraken.com/?ref=community")
_g2 = challenge.guide_lines()
check(4.2, any("kraken.com/?ref=community" in l for l in _g2),
      "the operator-configured link rides the guide")
_r1 = challenge.record_referral_bonus(_m2, 25.0)
check(4.3, _r1["ok"] is True, f"the bonus is credited once: {_r1}")
_r2 = challenge.record_referral_bonus(_m2, 25.0)
check(4.4, _r2["ok"] is False and "already" in _r2["reason"],
      "a SAME-MONTH re-credit is refused (no double-count)")
_vel = funding.referral_velocity_usd()
check(4.5, _vel > 0, f"the bonus feeds ReferralVelocity: {_vel:.2f}/day now")
_w3 = "4" + "c" * 95                               # charlie: tickets, no
_m3 = db.get_or_create_miner(_w3, worker_name="charlie-x")["id"]  # wins
db.add_tickets(_m3, "free", 2)
_check = sjv.miner_view(_w3)
check(4.6, (sjv.miner_view(_w3).get("kraken_challenge") is None),
      "the guide rides ONLY the claimable shape — a non-claimable miner "
      "sees no challenge nudge (loop entry = a win)")
with db.get_db() as conn:
    _last = conn.execute("SELECT id FROM drawings WHERE status='drawn' "
                         "AND claim_status='pending' ORDER BY id DESC "
                         "LIMIT 1").fetchone()
if _last:
    with db.get_db() as conn:
        conn.execute("UPDATE drawings SET winner_wallet = ? WHERE id = ?",
                     (_w2, _last["id"]))
    _win_view = sjv.miner_view(_w2)
    _kv = _win_view.get("kraken_challenge") or {}
    check(4.7, _kv.get("steps") and "kraken.com" in _kv.get("referral_link", "")
          and _kv["phrases"]["free"] == "Start for free at: "
          "https://xmrjackpot.com",
          "THE WINNER sees the guide + link + the mandated free phrase")
    check(4.8, _kv["phrases"]["getting_started"]
          == "Not sure where to get started? Start here: xmrjackpot.com",
          "the getting-started phrase rides the view byte-exact")

print("== 5. THE PHRASES, verbatim (charter mandate) ==")
check(5.1, challenge.GETTING_STARTED
      == "Not sure where to get started? Start here: xmrjackpot.com",
      "GETTING_STARTED is byte-exact")
check(5.2, challenge.FREE_PHRASE == "Start for free at: https://xmrjackpot.com",
      "FREE_PHRASE is byte-exact")

print("== 6. THE ACCOUNTING STILL STANDS (1b22a3e intact) ==")
with db.get_db() as conn:
    _pend = conn.execute("SELECT COALESCE(SUM(jackpot_amount-house_tax),0) s "
                         "FROM drawings WHERE status='drawn' AND "
                         "COALESCE(claim_status,'pending')='pending'").fetchone()["s"]
check(6.1, abs(engine.get_jackpot_balance()
              - max(0, float(db.get_setting("jackpot_balance", "0")) - _pend)) < 1e-9,
      "the balance model reads through unchanged (base minus pending "
      "earmarks) after the charter's edits")
_r = engine.process_winner_claim(999999, _w1)
check(6.2, _r.get("status") == "error", "a bogus claim is refused as before")
_bd = _mk_drawing("drawn", drawn_at=time.time() - 8 * 86400, claim="pending",
                  winner=_m1, deadline=time.time() - 86400)
_exp = engine.check_expired_claims() or []
check(6.3, any(e.get("drawing_id") == _bd for e in _exp),
      "expiry still sweeps, releases, and logs — the earmark model untouched")

print("== 7. THE SCOUT PRO DONATION IS JACKPOT MONEY (Oct 24) ==")
_base0 = float(db.get_setting("jackpot_balance", "0"))
_vel0 = funding.referral_velocity_usd()
_d = funding.credit_donation(4242, 0.01, sponsor_id=7)
check(7.1, _d.get("credited") is True
      and abs(_d.get("amount_xmr", 0) - 0.01) < 1e-12
      and abs(_d.get("jackpot_xmr", 0) - 0.007) < 1e-12
      and abs(_d.get("treasury_xmr", 0) - 0.003) < 1e-12,
      "a paid unlock is credited in full and split 70/30 "
      f"({_d.get('jackpot_xmr')} / {_d.get('treasury_xmr')})")
check(7.2, abs(float(db.get_setting("jackpot_balance", "0"))
              - (_base0 + 0.007)) < 1e-9,
      "the pot itself moves by the jackpot's 70% — the base is real "
      "money, not a receipt that never lands")
_d2 = funding.credit_donation(4242, 0.01, sponsor_id=7)
check(7.3, _d2.get("credited") is False,
      "the same checkout can never fund the pot twice")
check(7.4, abs(float(db.get_setting("jackpot_balance", "0"))
              - (_base0 + 0.007)) < 1e-9,
      "a replayed status poll leaves the balance untouched")
_rows = [r for r in funding._load_ledger()
        if r.get("source") == "scout_pro_donation:4242"]
check(7.5, len(_rows) == 1
      and _rows[0].get("category") == funding.DONATION_CATEGORY
      and abs(float(_rows[0].get("amount_xmr") or 0) - 0.01) < 1e-12,
      "one receipt, filed under its own category with its XMR stamped")
check(7.6, abs(funding.referral_velocity_usd() - _vel0) < 1e-9,
      "a donation never inflates ReferralVelocity — the daily formula "
      "cannot double-count money already in the base")
check(7.7, funding.credit_donation(4243, 0).get("credited") is False,
      "a zero or absent amount is refused")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
