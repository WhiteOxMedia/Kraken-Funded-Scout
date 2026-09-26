"""test_kraken_referral_unit.py — normalization, formula, signing,
provider honesty + posture (setup_kraken.py's read), the notification
leg (sandbox, hermetic)."""
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

print("== 4b. PROVIDER POSTURE (setup_kraken.py's read) ==")
# hermetic: the OS keyring must not leak into tier assertions on boxes
# where a real pair is provisioned — the fake below replaces it
kr._keyring_creds = lambda: None
db.set_setting("KRAKEN_API_KEY", "")
db.set_setting("KRAKEN_API_SECRET", "")
db.set_setting("KRAKEN_REFERRAL_ENDPOINT", "")
db.set_setting("KRAKEN_REFERRAL_MODE", "")
p = kr.provider_posture()
check("4.10", p["credentials_tier"] == "" and p["mode"] == "unconfigured"
      and p["missing"] == ["KRAKEN_API_KEY", "KRAKEN_API_SECRET",
                           "KRAKEN_REFERRAL_ENDPOINT"],
      "fresh box: nothing provisioned, missing names all three")
db.set_setting("KRAKEN_API_KEY", "abcd1234efgh")
p = kr.provider_posture()
check(4.11, p["credentials_tier"] == "" and p["settings_partial_pair"] is True
      and p["missing"] == ["KRAKEN_API_SECRET", "KRAKEN_REFERRAL_ENDPOINT"]
      and p["settings_key_tail"] == "…efgh" and p["api_key_tail"] == "",
      "half a settings pair is flagged, not mixed; missing names the gap")
SEC = "dGVzdC1zZWNyZXQ="
db.set_setting("KRAKEN_API_SECRET", SEC)
db.set_setting("KRAKEN_REFERRAL_ENDPOINT", "https://example.com/0/private/ReferralStatus")
p = kr.provider_posture()
check(4.12, p["credentials_tier"] == "settings" and p["mode"] == "live"
      and p["projection_live"] is True and p["missing"] == []
      and p["api_key_tail"] == "…efgh" and p["api_secret_tail"] == "…" + SEC[-4:],
      "complete settings pair + endpoint -> live posture with tails")
check(4.13, SEC not in str(p) and "abcd1234efgh" not in str(p),
      "posture never carries secret material, tails only")
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")
p = kr.provider_posture()
check(4.14, p["mode"] == "simulate" and p["projection_live"] is False,
      "operator simulate override shows in the posture")
db.set_setting("KRAKEN_REFERRAL_MODE", "")
db.set_setting("KRAKEN_REFERRAL_ENDPOINT", "")
p = kr.provider_posture()
check(4.15, p["mode"] == "unconfigured"
      and p["missing"] == ["KRAKEN_REFERRAL_ENDPOINT"],
      "endpoint blanking flips back fail-closed, naming only the gap")
# the settings pair from 4.12 must be out of the picture: settings
# tier wins by design, so blank it to exercise the keyring fallback
db.set_setting("KRAKEN_API_KEY", "")
db.set_setting("KRAKEN_API_SECRET", "")
_fake_store = {"kraken_api_key": "kring-key-9999",
               "kraken_api_secret": "kring-sec-8888"}
kr._keyring_creds = lambda: ({"key": _fake_store["kraken_api_key"],
                              "secret": _fake_store["kraken_api_secret"]}
                             if _fake_store.get("kraken_api_key")
                             and _fake_store.get("kraken_api_secret") else None)
p = kr.provider_posture()
check("4.16", p["credentials_tier"] == "keyring" and p["keyring_pair"] is True
      and p["mode"] == "unconfigured"
      and p["missing"] == ["KRAKEN_REFERRAL_ENDPOINT"]
      and p["api_key_tail"] == "…9999" and p["settings_key_tail"] == "",
      "keyring tier resolves (settings empty) and still needs only the endpoint")
kr._keyring_creds = lambda: None

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
