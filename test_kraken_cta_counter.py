"""test_kraken_cta_counter.py — THE KRAKEN CTA CLICK COUNTER, PINNED (Oct 10).

CHARTER: the public Kraken referral CTAs (the Oct 10 accessibility
pass: hero badge, wf-funded, signup Step Zero, how-to step 1, footer
box, wallet modal, hero top) each carry data-cta-slot and fire one
fire-and-forget POST to /api/cta/kraken-click; the server records the
click in the kraken_cta_click ledger (no session needed — the counter
MEASURES interest, it never spins up a login); the admin referral
card renders a metrics row reading /api/admin/kraken-cta-stats.

This drive pins the YARDSTICK, not the browser:

  1. THE LEDGER READS: record/get round-trip in a hermetic temp DB.
  2. THE SLOT TABLE IS THE CONTRACT: server slot set covers every
     data-cta-slot the page actually ships (cross-reads index.html),
     and every slot the JS sender can emit.
  3. THE SENDER EXISTS: app.js carries the delegated invite.kraken.com
     click listener with sendBeacon + keepalive fetch fallback, and
     index.html carries one data-cta-slot per invite link (no naked
     referral link left uncounted).
  4. THE ADMIN ROW EXISTS: kraken_referral_admin.js fetches the stats
     endpoint and renders slot/all-time/30d/today columns.
  5. THE ENDPOINTS EXIST: server.py carries both routes and the
     refuse-spoofs url check (only OUR referral url counts).
"""
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
_tmp = Path(tempfile.mkdtemp(prefix="kr_cta_"))
import database as db
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

results = []
def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))

print("== 1. THE LEDGER READS (hermetic DB) ==")
db.record_cta_click("hero-badge", "https://invite.kraken.com/JDNW/29bygijw", ip_address="1.2.3.4")
db.record_cta_click("footer-box", "https://invite.kraken.com/JDNW/29bygijw", ip_address="5.6.7.8")
db.record_cta_click("footer-box", "https://invite.kraken.com/JDNW/29bygijw", ip_address="5.6.7.8")
s = {r["slot"]: r for r in db.get_cta_click_summary(days=30)}
check(1.1, "hero-badge" in s and "footer-box" in s, "both slots recorded")
check(1.2, s["footer-box"]["total"] == 2 and s["hero-badge"]["total"] == 1, "counts per slot")
check(1.3, s["footer-box"]["today"] == 2 and s["footer-box"]["in_window"] == 2,
      "today + 30d windows count")
check(1.4, s["footer-box"]["last_at"] is not None, "last click timestamp present")

print("== 2. THE SLOT TABLE IS THE CONTRACT ==")
srv = (HERE / "pool" / "server.py").read_text(encoding="utf-8")
slot_block = re.search(r"_KRAKEN_CTA_SLOTS = frozenset\(\{(.*?)\}\)", srv, re.S)
srv_slots = set(re.findall(r'"([a-z0-9-]+)"', slot_block.group(1))) if slot_block else set()
html = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
page_slots = set(re.findall(r'data-cta-slot="([a-z0-9-]+)"', html))
check(2.1, bool(srv_slots), "server slot table present")
check(2.2, page_slots <= srv_slots,
      f"every shipped data-cta-slot is server-known (page extras: {page_slots - srv_slots})")
check(2.3, "unattributed" in srv_slots, "unmapped clicks still land (the JS default)")

print("== 3. THE SENDER EXISTS ==")
app = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(3.1, "invite.kraken.com" in app and "data-cta-slot" in app,
      "delegated click listener targets the referral links")
check(3.2, "sendBeacon" in app and "keepalive" in app,
      "fire-and-forget: beacon + keepalive fetch fallback")
check(3.3, app.count("/api/cta/kraken-click") >= 2,
      "both send paths hit the same endpoint")
naked = [ln for ln in html.splitlines()
         if "invite.kraken.com" in ln and "data-cta-slot" not in ln]
check(3.4, not naked, f"no naked referral link left uncounted ({len(naked)})")

print("== 4. THE ADMIN ROW EXISTS ==")
adm = (HERE / "pool" / "static" / "kraken_referral_admin.js").read_text(encoding="utf-8")
check(4.1, "/api/admin/kraken-cta-stats" in adm, "the metrics row fetches the stats endpoint")
check(4.2, "30d" in adm and "today" in adm and "all-time" in adm,
      "the funnel columns render (all-time / 30d / today)")
check(4.3, "no clicks recorded yet" in adm, "empty-state line pinned")

print("== 5. THE ENDPOINTS EXIST ==")
check(5.1, '@app.post("/api/cta/kraken-click")' in srv, "public click route present")
check(5.2, '@app.get("/api/admin/kraken-cta-stats")' in srv, "admin stats route present")
# (CodeRabbit minor on ddcdd1e, cured — the first version sliced from
# the route's name to EOF, so ANY later require_admin call would have
# satisfied it; the slice now ends at the route's next @app. marker,
# so the gate must live INSIDE the stats function body itself)
_i = srv.find("@app.get(\"/api/admin/kraken-cta-stats\")")
_stats_fn = srv[_i:srv.find("@app.", _i + 1)]
check(5.3, "def api_admin_kraken_cta_stats" in _stats_fn
      and "require_admin(request)" in _stats_fn,
      "the stats route is admin-gated (gate inside its own body, not "
      "any later route's)")
check(5.4, "unrecognized CTA url" in srv,
      "spoof urls refused — the ledger records OUR referral link only")

n_fail = results.count(False)
print(f"\nRESULT: {results.count(True) - 0} passed, {n_fail} failed" if False
      else f"RESULT: {len(results) - n_fail} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
