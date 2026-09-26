"""test_kraken_referral_unit.py — normalization, formula, signing,
provider honesty, the notification leg (sandbox, hermetic)."""
import sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
_tmp = Path(tempfile.mkdtemp(prefix="kr_unit_"))
import database as db; db.DB_PATH = _tmp / "jackpot.db"; db.init_db()
import kraken_referral as kr

results = []
def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))

print("== 1. STATUS NORMALIZATION ==")
rid, st, paid, pend, expl, lu = kr.normalize_kraken_response(
    {"referral_id": "R1", "status": "Paid", "total_paid": "40.50"})
check(1.1, rid == "R1" and st == "Paid" and paid == 40.5 and expl is None,
      "paid row normalizes")
rid, st, paid, pend, expl, lu = kr.normalize_kraken_response(
    {"referralId": "R2", "referral_status": "pending", "pending_usd": 25})
check(1.2, st == "Pending" and pend == 25.0 and expl == 25.0,
      "pending with explicit amount")
rid, st, *_ = kr.normalize_kraken_response({"id": "R3", "status": "active"})
check(1.3, st == "Eligible", "active -> Eligible")
rid, st, paid, pend, expl, lu = kr.normalize_kraken_response(
    {"status": "quantum", "total_paid": "not-a-number"})
check(1.4, st == "unknown" and paid == 0.0, "unknown status + garbage money tolerated")
rid, st, *_ = kr.normalize_kraken_response(None)
check(1.5, st == "unknown", "non-dict payload -> unknown, no crash")

print("== 2. EXPECTED BONUS FORMULA ==")
cons, best, est = kr.compute_expected_bonus("Paid", 40.0, 0.0)
check(2.1, cons == 40.0 and best == 40.0 and est == 0, "Paid floor")
cons, best, est = kr.compute_expected_bonus("Pending", 0.0, 25.0, explicit_amount=25.0)
check(2.2, cons == 25.0 and best == 25.0 and est == 0, "explicit wins, no estimate flag")
import jackpot_funding as _f
_f.record_funding("referral_bonus", 70.0, source="unit-seed")  # 70/14d -> 5.0/day
cons, best, est = kr.compute_expected_bonus("Eligible", 0.0, 0.0, confidence=0.6)
check(2.3, est == 1 and abs(cons - 3.0) < 0.01 and abs(best - 5.0) < 0.01,
      f"Eligible estimate = 14d-velocity*conf: cons={cons} best={best}")
cons, best, est = kr.compute_expected_bonus("Ineligible", 0.0, 0.0)
check(2.4, cons == 0.0 and best == 0.0, "Ineligible -> zero")
cons, best, est = kr.compute_expected_bonus("Pending", 0.0, 0.0, confidence=0.0)
cons, best, est = kr.compute_expected_bonus("Pending", 0.0, 0.0, confidence=0.0)
check(2.5, est == 1 and cons == 0.0 and abs(best - 5.0) < 0.01,
      "confidence=0 -> conservative floor of 0 (no fabricated money)")

print("== 3. KRAKEN SIGNING ==")
sig = kr.sign_request("ab" * 43 + "k=", "/0/private/ReferralStatus", 1727000000, "referral_id=R1")
sig2 = kr.sign_request("ab" * 43 + "k=", "/0/private/ReferralStatus", 1727000000, "referral_id=R2")
check(3.1, len(sig) == 128 and sig != sig2, "HMAC-SHA512 hex, body-sensitive")
check(3.2, kr.sign_request("short", "/p", 1, "b") == kr.sign_request("short===", "/p", 1, "b"),
      "b64 padding tolerance is deterministic")

print("== 4. PROVIDER HONESTY ==")
check(4.1, kr.provider_mode() == "unconfigured", "no creds -> unconfigured")
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")
check(4.2, kr.provider_mode() == "simulate", "explicit simulate")
db.set_setting("KRAKEN_REFERRAL_MODE", "")
check(4.3, kr.fetch_user_status(1, "SIM-PAID-001")["ok"] is False
      and kr.fetch_user_status(1, "X")["reason"] == "unconfigured",
      "unconfigured refuses to fabricate")

print("== 5. THE NOTICE (referral_window) ==")
ev = kr.push_svc.EVENTS["referral_window"]
cat = kr.push_svc.CATEGORY["referral_window"]
check(5.1, ev["rate"] == 2 and cat == "jackpot" and ev["tag"] == "referral-window",
      "event registered, jackpot family, capped at 2/h")
res = kr.notify_window_closing()
check(5.2, res.get("sent") == 0 and res.get("reason") in
      ("no subscription", "VAPID key not provisioned"),
      f"notice path runs to its honest terminal state: {res}")

print("== 6. AGGREGATION READS ==")
check(6.1, kr.latest_projection() is None, "no projection before any run")
p = kr.run_worker()                        # unconfigured: discovers, zero users
check(6.2, p["processed"] == 0 and p["errors"] == 0 and p["mode"] == "unconfigured"
      and p["source"] == "kraken_referral_unconfigured_v1",
      "empty run is a clean, honestly-labeled zero")
lp = kr.latest_projection()
check(6.3, lp and lp["possible_future_jackpot"] >= 0 and lp["referral_seed"] == 0.0,
      "projection row persisted even for the zero run")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
