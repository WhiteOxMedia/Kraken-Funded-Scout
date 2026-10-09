"""Test THE XMR JACKPOT PLATE (Sep 25, user order: "Wire
scout_jackpot_view into the Scout console as a jackpot plate — pool,
countdown, own tickets — using the safe whitelist only, in a clean
app.py surface").

THE WIRING UNDER TEST: app.py renders monero_project's
scout_jackpot_view.py — THE SAFE PUBLIC VIEW — directly (one brain:
the committed sibling module, no vendored copy to drift, the dirty
server worktree never touched). The plate shows the pool, the
countdown, and HER OWN tickets, and NOTHING else:

  1. THE WHITELIST — community_view() passes only SAFE_STATS_KEYS even
     when the engine's stats carry more (forbidden keys are injected
     and must not surface);
  2. THE COUNTDOWN — next_drawing_at/seconds_remaining roll from the
     last drawing + the interval, in a sandboxed DB;
  3. HER OWN TICKETS — miner_view's shape: registered/pending/lifetime,
     claim labels (won/claimed/expired), empty and bogus wallets fail
     soft, and another miner's tickets never cross the wall;
  4. THE PLATE — app.py's pure builder renders the numbers and the
     non-whitelisted fields (real_miners, p2pool_hashrate) cannot leak,
     the claim label is entity-escaped (it crosses from a DB), the
     wallet never appears in the html, and the unregistered line says
     the installer's byte-exact phrase;
  5. THE CONTRACT — the transport reads the committed sibling (git
     says so), the whitelist is the SAME object (no second list to
     rot), the wallet lives in a local file that is never rendered,
     and the not-wired path degrades to a quiet line, not a crash;
  6. LIVE — the real deployment answers through the same call the
     plate makes, and its keys obey the whitelist.

SANDBOX: the pool database module is bound to a temp DB_PATH before
any pool import (the DB_PATH seam) — nothing here reads or writes the
live jackpot.db. app.py is imported with SCOUT_PREFS_FILE sandboxed
(the test_apptest pattern) and only its pure builders are graded.
"""
import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MONERO = HERE.parent / "monero_project"
POOL = MONERO / "pool"

sys.path.insert(0, str(HERE))

results = []


def check(n, ok, why=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + f"{n}  {why}")


# ── sandbox BEFORE any pool import: the DB_PATH seam ─────────────────
_sbx = tempfile.mkdtemp(prefix="jp_plate_sbx_")
sys.path.insert(0, str(POOL))
import database as db  # the POOL's database (scout root has no such name)

db.DB_PATH = Path(_sbx) / "jackpot.db"
db.init_db()
import jackpot_engine as engine
import scout_jackpot_view as sjv

# app.py import, prefs sandboxed (the test_apptest pattern)
_prefs_dir = tempfile.mkdtemp(prefix="jp_plate_prefs_")
_scout_prefs_real = os.environ.get("SCOUT_PREFS_FILE")
os.environ["SCOUT_PREFS_FILE"] = str(Path(_prefs_dir) / "prefs.json")   # leak-audit: ok — restored in the suite's tail finally
import app as app_mod

WALLET = "4" + "x" * 95          # well-formed-shaped local sandbox wallet
WALLET_MARK = "ZQ7MARKER"        # a needle proving the wallet never renders

print("== 1. THE WHITELIST: community_view passes only SAFE_STATS_KEYS ==")
db.set_setting("jackpot_balance", "2.5")
db.set_setting("drawing_interval_hours", "24")
db.set_setting("house_tax_percent", "10")
db.set_setting("free_tickets_per_shares", "100")

_v = sjv.community_view()
_sanctioned = sjv.SAFE_STATS_KEYS | {"next_drawing_at", "seconds_remaining",
                                     "community_contribution"}
check(1.1, set(_v) <= _sanctioned,
      f"every key is whitelist or sanctioned countdown/contribution: {sorted(set(_v) - sjv.SAFE_STATS_KEYS)}")

# the injection drill: dirty stats with forbidden fields — the view
# must filter them no matter what the engine grows
_real_stats = engine.get_pool_stats


def _dirty_stats():
    d = dict(_real_stats())
    d["real_miners"] = 7
    d["p2pool_hashrate"] = 999.0
    d["secret_wallet_seed"] = "seed-secret-do-not-show"
    return d


engine.get_pool_stats = _dirty_stats
try:
    _vd = sjv.community_view()
finally:
    engine.get_pool_stats = _real_stats
check(1.2, "real_miners" not in _vd and "p2pool_hashrate" not in _vd
      and "secret_wallet_seed" not in _vd and set(_vd) <= _sanctioned,
      "the injected forbidden keys (real_miners, p2pool_hashrate, seed) are filtered")
check(1.3, abs(float(_v["jackpot_xmr"]) - 2.5) < 1e-9
      and "jackpot_usd" in _v,
      f"the pool number rides the settlement model's clean base: jackpot_xmr={_v['jackpot_xmr']}")
check(1.4, _v["house_tax_percent"] == 10 and _v["drawing_interval_hours"] == 24,
      "the whitelist's settings fields round-trip")

print("== 1b. THE FUNDING VIEW: an allowlist of its own ==")
_fplan = sjv.funding_view()
_fsanctioned = sjv.FUNDING_VIEW_KEYS
check(1.5, set(_fplan) <= _fsanctioned and "cap_usd" not in _fplan,
      f"funding_view carries only its allowlist ({sorted(_fplan)}) — the "
      f"internal cap constant is dropped from the display payload")
import jackpot_funding as _jf
_real_compute = _jf.compute_daily_jackpot


def _dirty_plan():
    d = dict(_real_compute())
    d["ledger_rows"] = 999
    d["internal_note"] = "do-not-display"
    return d


_jf.compute_daily_jackpot = _dirty_plan
try:
    _fd = sjv.funding_view()
finally:
    _jf.compute_daily_jackpot = _real_compute
check(1.6, "ledger_rows" not in _fd and "internal_note" not in _fd
      and set(_fd) <= _fsanctioned,
      "a new field appearing in compute_daily_jackpot is NOT auto-exposed "
      "(same discipline as the pool whitelist)")
check(1.7, float(_fplan.get("referral_velocity_usd") or 0) == 0.0
      and float(_fplan.get("daily_jackpot_usd") or 0) == 0.0
      and _fplan.get("capped") is False,
      "an empty ledger plans $0.00 — the formula reads only real receipts")


def _boom(*_a, **_k):
    raise RuntimeError("boom")


_jf.compute_daily_jackpot = _boom
try:
    _fb = sjv.funding_view()
finally:
    _jf.compute_daily_jackpot = _real_compute
check(1.8, _fb == {},
      "a funding-model problem degrades to {} — never a broken plate")

print("== 2. THE COUNTDOWN: last drawing + interval, rolled forward ==")
_now = time.time()
check(2.1, abs(_v["next_drawing_at"] - (_now + 24 * 3600)) < 10
      and 0 <= _v["seconds_remaining"] <= 24 * 3600,
      f"no drawing ever -> the interval horizon: seconds_remaining={_v['seconds_remaining']}")


def _mk_drawing(status, drawn_at=None, claim=None, winner=None, deadline=None):
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at, claim_status, claim_deadline) "
            "VALUES (0.5, 0.05, ?, ?, ?, ?, ?)",
            (status, winner, drawn_at, claim, deadline))
        return cur.lastrowid


_d1 = _mk_drawing("drawn", drawn_at=_now - 3600)
_v2 = sjv.community_view()
check(2.2, abs(_v2["next_drawing_at"] - (_now - 3600 + 24 * 3600)) < 10,
      f"a drawing an hour ago -> next in ~23h: seconds_remaining={_v2['seconds_remaining']}")
_d2 = _mk_drawing("drawn", drawn_at=_now - 25 * 3600)
_v3 = sjv.community_view()
check(2.3, abs(_v3["next_drawing_at"] - (_now - 3600 + 24 * 3600)) < 10
      and _v3["next_drawing_at"] > _now,
      "a past-due drawing rolls forward, never negative")
check(2.4, abs(_v3["seconds_remaining"]
              - max(0, _v3["next_drawing_at"] - time.time())) <= 2
      and _v3["seconds_remaining"] >= 0,
      "seconds_remaining is next_drawing_at - now, clamped at zero")

print("== 3. HER OWN TICKETS: miner_view's shape in the sandbox ==")
db.get_or_create_miner(WALLET)
with db.get_db() as conn:
    _mid = conn.execute("SELECT id FROM miners WHERE wallet_address = ?",
                        (WALLET,)).fetchone()["id"]
db.add_tickets(_mid, "free", 3)
db.add_tickets(_mid, "paid", 1, cost_xmr=0.001)
_mv = sjv.miner_view(WALLET)
check(3.1, _mv["registered"] and _mv["pending_tickets"] == 4
      and _mv["total_tickets"] == 4,
      f"registered with 3 free + 1 paid, all unassigned: {_mv}")
_d3 = _mk_drawing("pending")
with db.get_db() as conn:
    conn.execute(
        "UPDATE tickets SET drawing_id = ? "
        "WHERE id = (SELECT MIN(id) FROM tickets WHERE miner_id = ? "
        "AND drawing_id IS NULL)", (_d3, _mid))
_mv2 = sjv.miner_view(WALLET)
check(3.2, _mv2["pending_tickets"] == 3 and _mv2["total_tickets"] == 4,
      f"an assigned ticket leaves the pending count: pending={_mv2['pending_tickets']}")
_lbls = {}
for st_lbl in ("pending", "claimed", "expired"):
    _dd = _mk_drawing("drawn", drawn_at=_now, claim=st_lbl, winner=_mid,
                      deadline=_now + 7 * 86400)
    _lbls[st_lbl] = (sjv.miner_view(WALLET).get("claim") or {}).get("label", "")
check(3.3, "won" in _lbls["pending"] and "claim open" in _lbls["pending"]
      and _lbls["claimed"] == "claimed"
      and "expired" in _lbls["expired"] and "returned" in _lbls["expired"],
      f"claim labels speak English, not schema: {_lbls}")
check(3.4, sjv.miner_view("")["registered"] is False
      and sjv.miner_view("4" + "q" * 95)["registered"] is False
      and sjv.miner_view("")["pending_tickets"] == 0,
      "an empty or unknown wallet is 'not registered', never a crash")
_other = "8" + "y" * 95
db.get_or_create_miner(_other)
with db.get_db() as conn:
    _omid = conn.execute("SELECT id FROM miners WHERE wallet_address = ?",
                         (_other,)).fetchone()["id"]
db.add_tickets(_omid, "free", 9)
check(3.5, sjv.miner_view(WALLET)["total_tickets"] == 4
      and sjv.miner_view(_other)["pending_tickets"] == 9,
      "another miner's nine tickets never cross into her view (no enumeration)")
_d5 = _mk_drawing("drawn", drawn_at=_now, claim="pending", winner=_mid,
                  deadline=_now + 7 * 86400)
_mv3 = sjv.miner_view(WALLET)
_kc = _mv3.get("kraken_challenge") or {}
check(3.6, _mv3.get("claim", {}).get("status") == "pending"
      and isinstance(_kc.get("steps"), list) and _kc.get("steps")
      and (_kc.get("phrases") or {}).get("free", "").startswith(
          "Start for free at: "),
      "THE CLAIMABLE SHAPE: miner_view carries kraken_challenge (guide "
      "steps + the charter's free phrase) exactly when the latest claim "
      "is pending")
check(3.7, "kraken_challenge" not in sjv.miner_view(""),
      "an unregistered view carries no challenge — the loop's entry is "
      "a win, nothing else opens it")

print("== 4. THE PLATE: the builder renders the numbers, nothing else ==")
_nowts = time.time()
_view = {"jackpot_xmr": 2.5, "jackpot_usd": 807.5, "active_miners": 2,
         "total_miners": 5, "mining_active": True,
         "next_drawing_at": int(_nowts) + 3600, "seconds_remaining": 3600,
         "pending_tickets": 7, "total_shares": 123,
         "house_tax_percent": 10, "drawing_interval_hours": 24,
         "free_tickets_per_shares": 100, "total_hashrate": 42.0}
_mvplate = {"registered": True, "pending_tickets": 3, "total_tickets": 4,
            "claim": {"drawing_id": 1, "status": "pending",
                      "label": "won — claim open at xmrjackpot.com",
                      "deadline": _nowts + 7 * 86400}}
_html = app_mod._jackpot_plate(_view, _mvplate, _nowts)
check(4.1, "2.5000 XMR" in _html and "≈ $808" in _html and "1h 00m" in _html
      and "THE POOL IS MINING" in _html and "2 of 5 miners active" in _html
      and "tickets in the next drawing: <b>3</b>" in _html
      and "lifetime entries: 4" in _html
      and "won — claim open at xmrjackpot.com" in _html,
      "pool, countdown, miners, her tickets, claim — all on one plate")
_html_dirty = app_mod._jackpot_plate(
    dict(_view, real_miners=7, p2pool_hashrate=999.0,
         secret_wallet_seed="seed-secret-do-not-show"), _mvplate, _nowts)
check(4.2, "999" not in _html_dirty and "real_miners" not in _html_dirty
      and "p2pool" not in _html_dirty
      and "seed-secret" not in _html_dirty and _html_dirty == _html,
      "the non-whitelisted fields CANNOT leak: the html is byte-identical")
_evil = {"registered": True, "pending_tickets": 1, "total_tickets": 1,
         "claim": {"drawing_id": 9, "status": "pending",
                   "label": "<script>alert(1)</script> won", "deadline": None}}
_line = app_mod._jp_ticket_line(_evil)
check(4.3, "&lt;script&gt;" in _line and "<script>" not in _line,
      "a DB-borne claim label is entity-escaped before the plate's html")
_mvline = app_mod._jp_ticket_line({"registered": False,
                                   "pending_tickets": 0,
                                   "total_tickets": 0, "claim": None})
check(4.4, "Not sure where to get started? Start here: xmrjackpot.com" in _mvline,
      "the unregistered line says the mandated phrase")
_spec = importlib.util.spec_from_file_location(
    "jp_installer_probe", MONERO / "installer" / "installer.py")
_inst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inst)
check(4.5, _inst.GETTING_STARTED in _mvline
      and _inst.GETTING_STARTED == ("Not sure where to get started? "
                                    "Start here: xmrjackpot.com"),
      "byte-exact against the installer's own constant (one source)")
_html_quiet = app_mod._jackpot_plate(dict(_view, mining_active=False),
                                     _mvplate, _nowts)
check(4.6, "the pool is quiet" in _html_quiet
      and "THE POOL IS MINING" not in _html_quiet,
      "mining_active=False reads quiet, not a false green")
_html_blank = app_mod._jackpot_plate(
    dict(_view, next_drawing_at=0, seconds_remaining=0), _mvplate, _nowts)
check(4.7, "--:--" in _html_blank and "0h 00m" in _html_blank,
      "a missing countdown reads --:-- instead of inventing a time")
_fv = {"referral_velocity_usd": 1.79, "miner_yield_usd": 0.01,
       "raw_usd": 1.80, "daily_jackpot_usd": 1.80,
       "daily_jackpot_xmr": 0.00557, "capped": False, "xmr_usd": 323.0}
_html_f = app_mod._jackpot_plate(_view, _mvplate, _nowts, _fv)
check(4.8, "today's pot plan: $1.79 referrals + $0.01 mining "
      "→ <b>$1.80</b> planned" in _html_f,
      "the funding line speaks the charter formula from funding_view's "
      "dict only")
check(4.9, "the $20 charter cap bound"
      in app_mod._jackpot_plate(_view, _mvplate, _nowts,
                                dict(_fv, capped=True,
                                     daily_jackpot_usd=20.0)),
      "a capped day says so in plain English")
check(4.10, "today's pot plan" not in _html,
      "a missing funding view leaves the plate unchanged (no invented "
      "zeros, no empty promise of a plan)")
_fv_evil = dict(_fv, referral_velocity_usd="<script>alert(1)</script>")
_html_evil = app_mod._jackpot_plate(_view, _mvplate, _nowts, _fv_evil)
check(4.11, "<script>" not in _html_evil
      and "&lt;script&gt;" in _html_evil,
      "a DB-borne component value is entity-escaped before the plate's html")
_kcfix = {"steps": ["1. Open your Kraken account through the community "
                    "referral link (shown below when configured).",
                    "2. Complete the $1K Kraken Challenge — deposit and "
                    "place the trades the challenge asks for."],
          "referral_link": "https://kraken.example/r/community",
          "phrases": {"getting_started":
                      "Not sure where to get started? Start here: "
                      "xmrjackpot.com",
                      "free": "Start for free at: https://xmrjackpot.com"}}
_html_kc = app_mod._jackpot_plate(_view, dict(_mvplate, kraken_challenge=_kcfix),
                                  _nowts)
check(4.12, "THE KRAKEN CHALLENGE" in _html_kc
      and "Complete the $1K Kraken Challenge" in _html_kc
      and "<a href='https://kraken.example/r/community'" in _html_kc
      and "Start for free at: https://xmrjackpot.com" in _html_kc,
      "the guide renders when miner_view carries kraken_challenge: "
      "steps, an anchored referral link, the charter phrase")
check(4.13, "THE KRAKEN CHALLENGE" not in _html,
      "no claim, no challenge — the guide is absent from the plain plate")
_html_js = app_mod._jackpot_plate(
    _view, dict(_mvplate, kraken_challenge=dict(
        _kcfix, referral_link="javascript:alert(1)")), _nowts)
check(4.14, "javascript:" not in _html_js
      and "javascript:alert(1)" not in _html_js.replace("&quot;", ""),
      "a javascript: referral link is never anchored — only clean "
      "http(s) URLs get an href")
_html_evilstep = app_mod._jackpot_plate(
    _view, dict(_mvplate, kraken_challenge=dict(
        _kcfix, steps=["<script>alert(1)</script>"])), _nowts)
check(4.15, "<script>" not in _html_evilstep
      and "&lt;script&gt;" in _html_evilstep,
      "DB-borne guide steps are entity-escaped before the plate's html")

print("== 5. THE CONTRACT: committed sibling, one whitelist, fail-soft ==")
_st = subprocess.run(["git", "-C", str(MONERO), "status", "--porcelain",
                      "pool/scout_jackpot_view.py"],
                     capture_output=True, text=True, timeout=30)
check(5.1, _st.returncode == 0 and _st.stdout.strip() == "",
      "the imported scout_jackpot_view is the COMMITTED file (no dirty drift)")
check(5.2, Path(sjv.__file__).resolve() == (POOL / "scout_jackpot_view.py").resolve()
      and app_mod._JP_SAFE_KEYS is sjv.SAFE_STATS_KEYS,
      "app imports the sibling module itself and the whitelist is the SAME object")
_src = (HERE / "app.py").read_text(encoding="utf-8")
check(5.3, "SAFE_STATS_KEYS = {" not in _src
      and "from scout_jackpot_view import" in _src,
      "no second whitelist lives in app.py — the view module's is the only one")
_wtmp = Path(_prefs_dir) / "tickets.txt"
_wtmp.write_text("4" + WALLET_MARK + "x" * 80, encoding="utf-8")
_saved_tf = app_mod._JP_TICKETS_FILE
app_mod._JP_TICKETS_FILE = _wtmp
try:
    _wread = app_mod._jp_wallet()
finally:
    app_mod._JP_TICKETS_FILE = _saved_tf
check(5.4, WALLET_MARK in _wread and WALLET_MARK not in _html,
      "the wallet is read from jackpot_tickets.txt and NEVER appears in the plate html")
check(5.5, "_JP_VIEW_OK = False" in _src
      and "the jackpot view is not wired" in _src,
      "the not-wired path degrades to a quiet amber line, never a crash")
_ast = ast.parse(_src)
_fn = {n.name: n for n in ast.walk(_ast)
       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
_rm_calls = [n.func.id for n in ast.walk(_fn["render_master"])
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
check(5.6, "_render_jackpot_plate" in _rm_calls
      and "_plate_call(\"jackpot-plate\"" in _src
      and "_plate_render(\"jackpot-plate\")" in _src,
      "the plate is mounted on MASTER and brackets its cache-strip slot")
check(5.7, "scout_jackpot_copy" not in _src
      and _src.count("scout_jackpot_view") >= 2,
      "no vendored copy of the view module exists in the scout tree")

print("== 6. LIVE: the real deployment answers through the same call ==")
_lv = sjv.community_view()
check(6.1, isinstance(_lv, dict) and len(_lv) > 0
      and set(_lv) <= _sanctioned,
      f"the live view obeys the whitelist: {sorted(set(_lv) - sjv.SAFE_STATS_KEYS)}")
check(6.2, isinstance(_lv.get("seconds_remaining"), int)
      and _lv["seconds_remaining"] >= 0
      and "jackpot_xmr" in _lv,
      f"the countdown is a real non-negative int against the live pool: "
      f"jackpot_xmr={_lv.get('jackpot_xmr')}")

# leak-audit: ok — the suite's one honest env restore: the prefs seam
# (set at import, before app.py read it once) is handed back so the
# process ends on the operator's real environment, not a sandbox
_passed, _failed = results.count(True), results.count(False)
try:
    print(f"RESULT: {_passed} passed, {_failed} failed")
finally:
    if _scout_prefs_real is None:
        os.environ.pop("SCOUT_PREFS_FILE", None)
    else:
        os.environ["SCOUT_PREFS_FILE"] = _scout_prefs_real


def main():
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    sys.exit(main())
