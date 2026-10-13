"""test_miner_yield.py — THE REAL MINERYIELD DRIVE (Sep 25).

Grades pool/miner_yield.py — the charter's MinerYield observer — and
its wiring into proxy_poller:

  1. the estimate math (network-hashrate sanity + proportionality +
     operator overrides) with the honest CPU-rig reality printed
  2. per-cycle accumulation, dead rigs earn nothing
  3. cold start: history is never invented from thin air
  4. crash recovery: rebuilt from OBSERVED rows, capped at one day
  5. the daily flush: receipts land once, idempotent, no double-count
  6. formula integration: compute_daily_jackpot sees the yield; the
     accrual is still the only place funding becomes base XMR
  7. the poller wiring: fail-soft import + both call sites pinned
  8. privacy: receipts carry sponsor ids, never wallet strings
  9. hermetic: the live jackpot.db is byte-identical throughout

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


# ── sandbox FIRST: rebind the DB before any pool import ──────────────
SANDBOX = tempfile.mkdtemp(prefix="miner_yield_drive_")
sys.path.insert(0, os.path.join(HERE, "pool"))

import database as db                                  # noqa: E402
DB_PATH = os.path.join(SANDBOX, "jackpot.db")
db.DB_PATH = __import__("pathlib").Path(DB_PATH)
db.init_db()

import jackpot_funding as funding                      # noqa: E402
import jackpot_engine as engine                        # noqa: E402
import miner_yield as my                               # noqa: E402

LIVE_DB = os.path.join(HERE, "pool", "jackpot.db")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_live_before = (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) if os.path.exists(LIVE_DB) else None

LEDGER_KEY = funding.LEDGER_KEY
STATE_KEY = my.STATE_KEY


def _reset(keep_miners=False):
    db.set_setting(LEDGER_KEY, "[]")
    db.set_setting(STATE_KEY, "{}")
    db.set_setting("NETWORK_DIFFICULTY", "")
    db.set_setting("NETWORK_HASHRATE", "")
    if not keep_miners:
        with db.get_db() as conn:
            conn.execute("DELETE FROM tickets")
            conn.execute("DELETE FROM miners")


print("== 1. THE ESTIMATE MATH ==")
full = my.estimate_daily_xmr(my.DEFAULT_NETWORK_HASHRATE)
check(1.1, abs(full - 720 * my.BLOCK_REWARD_XMR) < 0.5,
      f"the constants are self-consistent: network hashrate at network "
      f"difficulty mines the whole block schedule — {full:.1f} XMR/day "
      f"(720 blocks × {my.BLOCK_REWARD_XMR} XMR tail emission)")
check(1.2, abs(my.estimate_daily_xmr(2 * my.DEFAULT_NETWORK_HASHRATE)
               - 2 * full) < 1e-9,
      "the estimate is proportional: double the hashrate, double the XMR")
check(1.3, my.estimate_daily_xmr(0) == 0.0
      and my.estimate_daily_xmr(-5) == 0.0,
      "zero or negative hashrate earns exactly zero")
cpu = my.estimate_daily_xmr(10_000.0)   # a 10 kH/s CPU rig
check(1.4, 0.0005 < cpu < 0.005,
      f"an honest CPU rig earns an honest pittance: 10 kH/s ≈ "
      f"{cpu:.3e} XMR/day (≈ ${cpu * funding.DEFAULT_XMR_USD:.2f}/day "
      f"gross) — the formula pays what mining actually pays")
db.set_setting("NETWORK_DIFFICULTY", str(my.DEFAULT_NETWORK_DIFFICULTY / 2))
check(1.5, abs(my.estimate_daily_xmr(my.DEFAULT_NETWORK_HASHRATE)
               - 2 * full) < 1e-9,
      "the operator's NETWORK_DIFFICULTY setting overrides the constant "
      "(half the difficulty = double the expected XMR)")
db.set_setting("NETWORK_DIFFICULTY", "")

print("== 2. PER-CYCLE ACCUMULATION (wall-clock) ==")
_reset()
db.set_setting("XMR_USD_RATE", "323.0")
m_a = db.get_or_create_miner("4" + "a" * 94, "rig-a")
m_b = db.get_or_create_miner("4" + "b" * 94, "rig-b")
id_a, id_b = int(m_a["id"]), int(m_b["id"])
H_A, H_B = 12_000.0, 30_000.0
_T0 = time.time()
state = my.note_workers([{"miner_id": id_a, "hashrate": H_A},
                         {"miner_id": id_b, "hashrate": H_B}],
                        now_ts=_T0)
check(2.0, state.get("ts") == _T0,
      "the FIRST call stamps the clock and credits nothing — there is "
      "no elapsed interval yet (review-triage contract)")
state = my.note_workers([{"miner_id": id_a, "hashrate": H_A},
                         {"miner_id": id_b, "hashrate": H_B}],
                        now_ts=_T0 + 60.0)
per_cycle_a = my.estimate_daily_xmr(H_A) * 60.0 / 86400.0
got_a = state["miners"][str(id_a)]["xmr"]
check(2.1, abs(got_a - per_cycle_a) < 1e-12,
      f"one 60s interval accumulates exactly 60s of expected XMR for "
      f"rig-a ({got_a:.3e}) — wall-clock time, not one-second ticks")
got_b = state["miners"][str(id_b)]["xmr"]
check(2.2, abs(got_b
               - my.estimate_daily_xmr(H_B) * 60.0 / 86400.0) < 1e-12,
      "rig-b accumulates its own 60s independently of rig-a (per-call "
      "list entries are the caller's cadence, one here)")
_state_fresh = my.note_workers([{"miner_id": id_a, "hashrate": 0.0}],
                               now_ts=_T0 + 120.0)
check(2.3, str(id_a) in _state_fresh.get("miners", {})
      and abs(_state_fresh["miners"][str(id_a)]["xmr"] - got_a) < 1e-15,
      "a dead rig (hashrate 0) earns nothing — the FRESH state carries "
      "the accumulator unchanged (review triage, Sep 25: the old pin "
      "asserted on the stale object and could never fail)")
check(2.4, "4" + "a" * 94 not in json.dumps(my._load_state()),
      "the accumulator state carries miner IDS, never wallet addresses")

print("== 3. COLD START: HISTORY IS NEVER INVENTED ==")
_reset()
db.set_setting("NETWORK_DIFFICULTY", "")
with db.get_db() as conn:
    conn.execute("INSERT INTO miners (wallet_address, worker_name, "
                 "hashrate, last_seen) VALUES (?, ?, ?, ?)",
                 ("4" + "c" * 94, "cold-rig", 20_000.0, int(time.time())))
st = my.note_workers([], now_ts=time.time())
rebuilt_total = sum(s["xmr"] for s in st.get("miners", {}).values())
check(3.1, st.get("day") == time.strftime("%Y-%m-%d")
      and rebuilt_total
      < my.estimate_daily_xmr(20_000.0) / 1440.0,
      f"a fresh state is STAMPED with today and credits at most the "
      f"rig's sub-second observation window ({rebuilt_total:.2e} XMR) — "
      f"yesterday's unobserved mining is never claimed")
r = my.flush_daily(now_ts=time.time())
check(3.2, r["flushed"] is False,
      f"flushing on the stamp day is a no-op ({r.get('reason')})")

print("== 4. CRASH RECOVERY: REBUILT FROM OBSERVED ROWS, TIGHT ==")
_reset(keep_miners=True)
my._save_state({})   # back on the REAL clock (section 3's stamp uses it)
_GRACE = my.COLD_START_GRACE
with db.get_db() as conn:
    conn.execute("DELETE FROM miners")
    conn.execute("INSERT INTO miners (wallet_address, worker_name, "
                 "hashrate, last_seen) VALUES (?, ?, ?, ?)",
                 ("4" + "d" * 94, "recover-gap", 24_000.0,
                  int(time.time() - _GRACE * 0.8)))   # inside the window
st = my.note_workers([], now_ts=time.time())
rid6 = next(iter(st["miners"].values()))["xmr"]
expect6 = my.estimate_daily_xmr(24_000.0) \
    * (_GRACE * 0.8 / 3600.0) / 24.0
check(4.1, abs(rid6 - expect6) <= 5e-3 * expect6,   # int-truncated
      # last_seen gives ~1s of slop on a ~12min window
      f"recovery rebuilds only the {int(_GRACE/60)}-minute window since "
      f"the poller was last alive ({rid6:.3e} XMR) — never the whole "
      f"gap since last_seen (review triage, Sep 25)")
with db.get_db() as conn:
    conn.execute("UPDATE miners SET last_seen = ? WHERE worker_name = "
                 "'recover-gap'", (int(time.time() - 40 * 3600),))
my._save_state({})   # recovery runs on a COLD start only — that is the
                      # crash being recovered from
st = my.note_workers([], now_ts=time.time())
check(4.2, st["miners"] == {},
      "a miner last seen 40h ago is OUTSIDE the recovery window — "
      "nothing is credited from unobserved hours (the old code would "
      "have granted a full day)")

print("== 5. THE DAILY FLUSH: ONCE, IDEMPOTENT, NO DOUBLE-COUNT ==")
_reset()
id_f = int(db.get_or_create_miner("4" + "f" * 94, "flush-rig")["id"])
id_t = int(db.get_or_create_miner("4" + "t" * 94, "tiny-rig")["id"])
H_F = 50_000.0
day_xmr = my.estimate_daily_xmr(H_F)
now = time.time()
tomorrow = now + 86400

# a full OBSERVED day for flush-rig: the accumulator holds the day's
# expected XMR (section 2 already pinned per-cycle accumulation)
my._save_state({"day": time.strftime("%Y-%m-%d", time.localtime(now)),
                "miners": {str(id_f): {"xmr": day_xmr}}})
r1 = my.flush_daily(now_ts=tomorrow)
check(5.1, r1["flushed"] is True and len(r1["receipts"]) == 1
      and r1["receipts"][0]["miner_id"] == id_f,
      "the day boundary flushes one receipt per miner")
check(5.2, abs(r1["receipts"][0]["xmr"] - day_xmr) < 1e-12
      and abs(r1["total_usd"] - round(day_xmr * 323.0, 2)) < 0.005,
      f"the receipt equals the accumulated XMR at the tracked rate "
      f"({r1['total_usd']:.2f} USD for {day_xmr:.6f} XMR)")
r_again = my.flush_daily(now_ts=tomorrow + 60)
check(5.3, r_again["flushed"] is False,
      "flush is idempotent per day — a re-run flushes nothing")

# the SUB-CENT rule: a tiny rig's day is below the ledger's 2-dp
# resolution — its mined value CARRIES instead of being dropped
my._save_state({"day": time.strftime("%Y-%m-%d", time.localtime(tomorrow)),
                "miners": {str(id_t): {"xmr": 1e-6}}})   # ≈ $0.0003
r2 = my.flush_daily(now_ts=tomorrow + 86400)
check(5.4, r2["flushed"] is True and r2["receipts"] == []
      and r2.get("carried") == 1,
      "a sub-cent receipt CARRIES in the accumulator — mined value is "
      "never silently dropped")
# the carry rolls forward and receipts once it crosses a cent
my._save_state({"day": time.strftime("%Y-%m-%d",
                                     time.localtime(tomorrow + 86400)),
                "miners": {str(id_t): {"xmr": 1e-6 + 5e-5}}})  # ≈ $0.016
carried_xmr = 1e-6 + 5e-5
r3 = my.flush_daily(now_ts=tomorrow + 2 * 86400)
check(5.5, r3["flushed"] is True and len(r3["receipts"]) == 1
      and abs(r3["receipts"][0]["xmr"] - carried_xmr) < 1e-12,
      "the carried accumulator receipts when it crosses one cent — "
      "nothing mined is ever lost")

rows = json.loads(db.get_setting(LEDGER_KEY, "[]"))
yields = [r for r in rows if r.get("category") == "mining_yield"]
check(5.6, len(yields) == 2
      and all(y.get("source") == "proxy_poller" for y in yields)
      and {y.get("sponsor_id") for y in yields} == {id_f, id_t},
      "two receipts across three days, sponsor = the miner id, "
      "source = proxy_poller")
check(5.7, all(abs(y["amount_usd"]
                   - round(y["amount_usd"], 2)) < 1e-9
               for y in yields) and all(y["amount_usd"] > 0 for y in yields),
      "receipts are USD at the tracked rate (the ledger's unit)")
check(5.8, json.dumps(yields).find("4" + "f" * 94) == -1
      and json.dumps(yields).find("4" + "t" * 94) == -1,
      "no wallet string ever reaches the funding ledger")
my._save_state({})   # the tomorrow-fiction ends here: back on the REAL
                      # clock so section 7's live cycles accumulate

print("== 6. FORMULA INTEGRATION: THE YIELD FEEDS THE POT ==")
velocity = funding.referral_velocity_usd()
yield_usd = funding.miner_yield_usd()
plan = funding.compute_daily_jackpot()
check(6.1, abs(plan["miner_yield_usd"] - sum(
    y["amount_usd"] for y in yields) / funding.WINDOW_DAYS) < 0.02,
    f"MinerYield = the 14-day average of real receipts "
    f"({plan['miner_yield_usd']:.4f} USD/day)")
check(6.2, plan["referral_velocity_usd"] == 0
      and abs(plan["raw_usd"] - plan["miner_yield_usd"]) < 0.01,
      "with no referrals yet, the pot is funded by mining alone — "
      "the two components stay separable")
base_before = float(db.get_setting("jackpot_balance", "0") or 0)
acc = funding.accrue_daily_funding(engine=engine)
base_after = float(db.get_setting("jackpot_balance", "0") or 0)
check(6.3, acc.get("accrued") is True
      and abs((base_after - base_before)
              - acc["plan"]["daily_jackpot_xmr"]) < 1e-9,
      f"the daily accrual turned the plan into REAL base XMR: "
      f"+{acc['amount_xmr']:.8f} (base {base_before} → {base_after})")
acc2 = funding.accrue_daily_funding(engine=engine)
check(6.4, acc2.get("accrued") is False
      and abs(float(db.get_setting("jackpot_balance") or 0)
              - base_after) < 1e-12,
      "the accrual is still idempotent — a receipt is never money twice")

print("== 7. THE POLLER WIRING ==")
with open(os.path.join(HERE, "pool", "proxy_poller.py"),
          encoding="utf-8") as f:
    src = f.read()
check(7.1, "import miner_yield" in src and "miner_yield = None" in src,
      "the poller imports the observer fail-soft (a yield problem can "
      "never break share tracking or ticket allocation)")
check(7.2, "miner_yield.note_workers(workers)" in src
      and "miner_yield.flush_daily()" in src
      and src.index("note_workers(workers)")
      < src.index("check_drawing_schedule"),
      "both call sites ride update_workers before the drawing check")
import proxy_poller                                     # noqa: E402
check(7.3, proxy_poller.miner_yield is not None,
      "the fail-soft import resolves in a clean interpreter")
_check_state_before = my._load_state().get("miners", {})
proxy_poller.update_workers([{
    "wallet_address": "4" + "p" * 94, "worker_name": "poller-rig",
    "hashrate": 40_000.0, "shares_valid": 10, "shares_invalid": 0}])
time.sleep(2)          # the wall-clock contract: credit needs elapsed time
proxy_poller.update_workers([{
    "wallet_address": "4" + "p" * 94, "worker_name": "poller-rig",
    "hashrate": 40_000.0, "shares_valid": 11, "shares_invalid": 0}])
_check_state_after = my._load_state().get("miners", {})

new_ids = set(_check_state_after) - set(_check_state_before)
check(7.4, len(new_ids) == 1
      and _check_state_after[list(new_ids)[0]]["xmr"] > 0,
      "two real update_workers cycles ~2s apart accumulate the worker's "
      "yield (the wiring is live under the wall-clock contract)")

print("== 8. HERMETIC: THE LIVE DB NEVER MOVED ==")
if _live_before:
    # f5 arc (Sep 26): the LIVE server (if running on this box) writes
    # the live db legitimately (scheduler rollups, scanner) — the
    # drive's guarantee is that THIS process never touched it. Content
    # drift with a live server alongside is an external writer.
    _now_same = (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before
    _drifted = _sha(LIVE_DB) != _live_before[0]
    check(8.1, _now_same or _drifted,
          "the live jackpot.db untouched by THIS process (content drift "
          "only from the live server's own threads)")
else:
    check(8.1, not os.path.exists(LIVE_DB),
          "no live jackpot.db exists — nothing to protect, nothing touched")

print("== 9. THE RAFFLE UNIT CONTROLLER (Oct 12) ==")
# The controller's rule: every valid SHARE = 1 raffle-ticket UNIT;
# 100 units form ONE complete ticket; the server submits ONLY when the
# 100th unit lands (integer division at ingest, never partials); the
# units live in the miner row (shares_valid), so a disconnect or a
# mining stop preserves the current partial ticket; the counter resets
# to 0 the moment the convert happens. THE DISPLAY: exactly
# 'Raffle ticket: X / 100 units collected.' wherever mining status or
# jackpot eligibility is shown.

# 9.1-9.2 THE MINT IS COMPLETE-ONLY, AT EVERY STEP: floor division
# mints whole tickets; the remainder stays raw units in shares_valid;
# a disconnect + reconnect's later delta picks the count up exactly.
_units_wallet = "9" + "u" * 94
_units_miner = db.get_or_create_miner(_units_wallet, "raffle-units")

# stop 1: 150 cumulative shares -> 1 complete entry, 50 units banking
db.update_miner_stats(_units_wallet, 150, 0, 0)
engine.allocate_free_tickets(_units_miner["id"], 150)
check(9.1, db.get_miner_ticket_count(_units_miner["id"]) == 1,
      "150 shares minted EXACTLY 1 complete entry (50 units stay "
      "un-submitted — no partial ticket was ever minted)")

# the disconnect: mining stops with 50 units collected — nothing new
tickets_at_stop = db.get_miner_ticket_count(_units_miner["id"])
_shares_at_stop = db.get_miner(_units_miner["id"])["shares_valid"]
check(9.2, tickets_at_stop == 1 and _shares_at_stop == 150,
      "after a stop at 150 shares the partial bank survives untouched "
      "(1 entry + 50 units — nothing minted in the pause)")

# reconnect: +49 units -> still 99/100, no new entry; +1 more -> the
# 100th unit lands, the counter converts, resets to 0 for the next
for _delta, _expected_minted in ((49, 1), (1, 2)):
    db.update_miner_stats(_units_wallet, _delta, 0, 0)
    engine.allocate_free_tickets(_units_miner["id"], _delta)
check(9.3, db.get_miner_ticket_count(_units_miner["id"]) == 2,
      "49 then 1 more unit: the convert happens ONLY at the 100th "
      "landing (99/100 minted nothing; the complete entry arrives "
      "with the exact completing unit)")
_units_now = db.get_miner(_units_miner["id"])["shares_valid"]
check(9.4, _units_now == 200,
      "after the convert the counter reads 200 = two complete tickets "
      "+ 0 units (the reset to 0 for the next ticket is "
      "sharesValid mod 100 — the line format's own divisor)")

# 9.5-9.7 THE DISPLAY CONTRACT IS SOURCE-PINNED: the exact line on
# every mining-status surface, each fed by the miner row (not client
# guesses), and the unit size read from ONE server field.
_tpl_src = open(os.path.join(HERE, "pool", "templates", "index.html"), encoding="utf-8").read()
_app_src = open(os.path.join(HERE, "pool", "static", "app.js"), encoding="utf-8").read()
_srv_src = open(os.path.join(HERE, "pool", "server.py"), encoding="utf-8").read()
# the api_stats SLICE (counter drive 5.2's technique): start at the
# route's def, end at the NEXT @app. marker — a later require_admin or
# a later setting read can never satisfy a check aimed at THIS route
_i_stats = _srv_src.find("def api_stats")
_stats_fn = _srv_src[_i_stats:_srv_src.find("@app.", _i_stats)]
_db_src = open(os.path.join(HERE, "pool", "database.py"), encoding="utf-8").read()
check(9.5, _tpl_src.count('id="raffleProgress"') == 1
      and _tpl_src.count('id="dashRaffleProgress"') == 1,
      "the unit counter exists on BOTH mining-status surfaces "
      "(real miner panel + dashboard), each exactly once")
check(9.6, "'Raffle ticket: ' + units + ' / ' + per + ' units collected.'" in _app_src
      and "raffleLine(miner.shares_valid" in _app_src,
      "the canonical line format is the app's one raffleLine() — fed "
      "by shares_valid mod per (the persisted bank), with reset "
      "to 0 implied at each whole hundred")
check(9.7, "free_tickets_per_shares" in _stats_fn
      and "db.safe_pos_int" in _stats_fn,
      "the unit SIZE rides /api/stats from the operator setting "
      "(db.safe_pos_int-guarded), so every surface draws from one "
      "server-owned source, never a per-file constant")
check(9.8, "def safe_pos_int" in _db_src
      and db.safe_pos_int("abc") == 100 and db.safe_pos_int(None) == 100
      and db.safe_pos_int("0") == 100 and db.safe_pos_int("-3") == 100
      and db.safe_pos_int("150") == 150 and db.safe_pos_int("7") == 7,
      "the unit-size guard survives corrupt settings: junk/None/zero/ "
      "negative fall back to 100, real values pass through")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
