"""test_static_listener_audit.py — THE STALE-PANEL AUDIT, PINNED (Sep 26).

CHARTER: no per-user UI surface may go silently stale on login or
logout. The push ecosystem has two broadcast events — app.js dispatches
xmrjackpot:auth (login/register AND logout), push_client.js dispatches
xmrjackpot:prefs (every applied envelope) — and every surface whose
data depends on WHO is logged in must either LISTEN to them or be a
deliberate, classified exception. This drive reads every pool/static
JS file and pins the audit so it is a CONTRACT, not folklore:

  1. THE TRANSMITTERS EXIST: app.js carries the auth dispatch (>=2
     sites: the login/register path AND the logout path) and
     push_client.js carries the prefs announcement (>=1).
  2. EVERY file fetching a per-user endpoint (/api/push/preferences,
     /api/push/signals, /api/auth/me, /api/profile/stats) must LISTEN
     to xmrjackpot:auth or xmrjackpot:prefs — or be explicitly
     classified below as a dispatcher (it IS the event's source and
     refreshes itself inline).
  3. THE CLASSIFIED EXCEPTIONS ARE CONSCIOUS: identity-independent
     surfaces (public/global endpoints) and admin-gated cards (their
     sessions self-heal through 401/403 on the next poll — pinned:
     every admin card polls) are named, with the reason in the table.
     A new file lands here only by editing this drive — the decision
     cannot happen by accident.
  4. the behaviors behind this audit are EXECUTED elsewhere: the stamp
     scenarios (test_wf_dashboard_stamp) and the envelope scenarios
     (test_push_client_envelope) prove the listeners actually render,
     refetch, and clear; this drive pins that the WIRING EXISTS on
     every surface that needs it.

Fetch extraction recognizes both call shapes: fetch("/api/...") and
fetch(API + "/api/...") (app.js's variable base). Prints
"RESULT: N passed, M failed".
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "pool" / "static"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


PER_USER_ENDPOINTS = (
    "/api/push/preferences",
    "/api/push/signals",
    "/api/auth/me",
    "/api/profile/stats",
)

# ── THE AUDIT TABLES (deliberate classifications — edit consciously) ──
# dispatchers: the files that TRANSMIT the events; they own the auth
# transition and refresh their own surfaces inline right after it.
DISPATCHERS = {
    "app.js": "the xmrjackpot:auth source (login/register + logout); "
              "refreshes its own surfaces inline after the transition",
    "push_client.js": "the xmrjackpot:prefs source (every applied "
                      "envelope) and an xmrjackpot:auth listener itself",
}

# identity-independent surfaces: their endpoints carry no session
# semantics, so login/logout cannot stale them.
IDENTITY_INDEPENDENT = {
    "referral_projection.js": "/api/jackpot/projection is global "
                              "(mode/source/seed), identical for every "
                              "visitor",
    "bundle_card.js": "/api/downloads/bundle-info is public static "
                      "info, no session semantics",
    "refblock.js": "/api/kraken/rewards-today is public read-only "
                   "(the funding-ledger referral count, no session, "
                   "identical for every visitor); fail-open fetch only",
}

# admin-gated cards: data is global, ACCESS is per-session; the next
# poll hits 401/403 and the card's own failure path heals (the kraken
# card removes itself on 403). Pinned: each must poll.
ADMIN_CARDS = {
    "admin_cold_wallet.js",
    "push_vapid_admin.js",
    "push_decisions_admin.js",
    "push_report_admin.js",
    "kraken_referral_admin.js",
}

# ── extract the facts from every file ────────────────────────────────
files = sorted(STATIC.glob("*.js"))
fetch_re = re.compile(
    r'fetch\(\s*(?:API\s*\+\s*)?"(/[^"\s]+)"')          # both call shapes
auth_listen_re = re.compile(r'addEventListener\("xmrjackpot:auth"')
prefs_listen_re = re.compile(r'addEventListener\("xmrjackpot:prefs"')
auth_dispatch_re = re.compile(r'dispatchEvent\(\s*new CustomEvent\(\s*[\'"]xmrjackpot:auth')
prefs_dispatch_re = re.compile(r'dispatchEvent\(\s*new CustomEvent\(\s*[\'"]xmrjackpot:prefs')
interval_re = re.compile(r"setInterval\(")

facts = {}
for f in files:
    src = f.read_text(encoding="utf-8")
    facts[f.name] = {
        "src": src,
        "fetches": set(fetch_re.findall(src)),
        "listens_auth": bool(auth_listen_re.search(src)),
        "listens_prefs": bool(prefs_listen_re.search(src)),
        "dispatches_auth": len(auth_dispatch_re.findall(src)),
        "dispatches_prefs": len(prefs_dispatch_re.findall(src)),
        "polls": bool(interval_re.search(src)),
    }

print("== 1. THE TRANSMITTERS EXIST ==")
check(1.1, facts.get("app.js", {}).get("dispatches_auth", 0) >= 2,
      f"app.js dispatches xmrjackpot:auth at >=2 sites "
      f"(login/register AND logout): "
      f"{facts.get('app.js', {}).get('dispatches_auth', 0)}")
check(1.2, facts.get("push_client.js", {}).get("dispatches_prefs", 0) >= 1,
      "push_client.js announces every applied envelope on "
      "xmrjackpot:prefs")
check(1.3, facts.get("push_client.js", {}).get("listens_auth", False),
      "push_client.js also LISTENS for xmrjackpot:auth (re-reads after "
      "login/logout) — the card is both ends of the contract")

print("== 2. EVERY PER-USER SURFACE LISTENS (or is a classified dispatcher) ==")
uncovered = []
for name, f in sorted(facts.items()):
    per_user = sorted(set(f["fetches"]) & set(PER_USER_ENDPOINTS))
    if not per_user:
        continue
    covered = f["listens_auth"] or f["listens_prefs"] or name in DISPATCHERS
    if not covered:
        uncovered.append((name, per_user))
check(2.1, not uncovered,
      f"every file fetching a per-user endpoint listens or is "
      f"classified: uncovered={uncovered or 'NONE'}")

print("== 3. THE CLASSIFICATIONS ARE REAL (not folklore) ==")
check(3.1, facts.get("wf_dashboard.js", {}).get("listens_prefs", False)
      and facts.get("wf_dashboard.js", {}).get("listens_auth", False),
      "wf_dashboard.js (the Scout panel) listens to BOTH events — the "
      "per-user surface with the most identity-dependent rows")
check(3.2, all(facts.get(n, {}).get("polls", False) for n in ADMIN_CARDS
               if n in facts),
      "every admin-gated card polls, so a dead session self-heals "
      "through its own 401/403 failure path within one interval")
check(3.3, "removes" in facts.get("kraken_referral_admin.js", {}).get("src", "")
      or "403" in facts.get("kraken_referral_admin.js", {}).get("src", ""),
      "the kraken card's failure path is documented in its own source "
      "(non-admins: the card removes itself)")
check(3.4, not (set(facts) - set(IDENTITY_INDEPENDENT) - set(DISPATCHERS)
                - ADMIN_CARDS - {"wf_dashboard.js", "push_sw.js"}
                - {n for n, f in facts.items() if f["listens_auth"]
                   or f["listens_prefs"]}),
      "every static file is either classified above, a listener, or "
      "the service worker (no events, no session) — a NEW file "
      "fetching per-user data fails check 2.1 until it listens")

print("== 4. THE CONTRIBUTION THANKS IS RENDERED VERBATIM ==")
# the member dashboard greets a member whose Kraken Referral bonus was
# PAID: the exact line (user's wording, Sep 26), the payload key the
# server ships, and the banner id the dashboard manages idempotently.
_app = facts.get("app.js", {}).get("src", "")
check(4.1, "Thanks, you\\'ve contributed to the jackpot $" in _app
      and "thanks.amount.toFixed(2)" in _app,
      "the greeting line renders byte-exact with the amount (the JS "
      "source carries the escaped apostrophe; rendered it is you've)")
check(4.2, "kraken_thanks" in _app and "krakenThanksBanner" in _app,
      "the banner reads the server's kraken_thanks payload and removes "
      "before insert (idempotent across profile refreshes)")

print("== 5. THE ORPHANS ADMIN CARD RENDERS THE SCHEDULER'S BLOCK ==")
# the weekly orphan leg's surface: app.js fetches the scheduler status
# and renders the orphans block - an alert banner on any nonzero total,
# the all-clear on zero, and the operator's OFF dial honored.
_panel = (HERE / "pool" / "templates" / "admin_panel.html").read_text(
    encoding="utf-8")
check(5.1, "adminOrphansBody" in _panel and "adminOrphansStamp" in _panel
      and "Orphan Report" in _panel,
      "the admin panel carries the Orphan Report card (injected HTML, "
      "admin-gated by the panel endpoint)")
check(5.2, "/admin/scheduler/status" in _app
      and "fetchAdminOrphans()" in _app
      and "setInterval(fetchAdminOrphans, 60000)" in _app,
      "app.js fetches the scheduler status for the card and polls it "
      "(admin-gated cards self-heal through 401/403, per the audit table)")
check(5.3, "last.total" in _app and "orphaned row(s)" in _app
      and "sweep_db.py --report" in _app,
      "any nonzero last.total renders the alert banner naming the "
      "planning command - an admin acts on the receipt, not the count")
check(5.4, "All clear - 0 violating rows" in _app
      and "SCHEDULER_ORPHAN_ENABLED=0" in _app,
      "zero renders the all-clear line and the operator OFF dial is "
      "rendered, not hidden")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
