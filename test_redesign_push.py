"""test_redesign_push.py — THE REDESIGN + PUSH DRIVE (Sep 25).

Grades the two charter surfaces:

  A. THE VISUAL REDESIGN (getmonero-org design system)
     1. TEXT PRESERVATION: every visible string and every link/route
        from the BASELINE commit (1db928b) is still present in the
        redesigned template — tags stripped, entities unescaped, the
        only additions being the two charter cards + the tagline
     2. THE DESIGN SYSTEM: page gray / monero orange / link orange /
        slate / white tokens, radius 0, the arcade font retired, and
        EVERY selector the old stylesheet carried still exists (the
        page's hooks cannot lose their styles)
  B. THE NATIVE PUSH SYSTEM
     3. subscriptions: https-only, per-user, no cross-user takeover,
        refresh-in-place, operator-provisioned key (empty by default)
     4. sends: templated payloads only (the signal titles byte-exact),
        per-user rate limits with audit rows, fail-soft dead endpoints
     5. the spool: scout emit -> spool -> drain -> sent/dropped/kept,
        the whitelist filtering, the unbounded-growth guard
  C. hermetic: the live jackpot.db is byte-identical throughout.

House conventions: check(n, cond, why), RESULT line, sys.exit(1).
"""
import hashlib
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
passed = failed = 0


def check(n, cond, why=""):
    global passed, failed
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


# ── sandbox FIRST (before any pool import) ───────────────────────────
SANDBOX = Path(tempfile.mkdtemp(prefix="redesign_push_drive_"))
sys.path.insert(0, str(HERE / "pool"))

import database as db                                    # noqa: E402
db.DB_PATH = SANDBOX / "jackpot.db"
db.init_db()

BASELINE_REV = "1db928b"
TPL = HERE / "pool" / "templates" / "index.html"
CSS = HERE / "pool" / "static" / "style.css"


def _gitshow(rev, path):
    r = subprocess.run(
        ["git", "-C", str(HERE), "show", f"{rev}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30)
    return r.stdout if r.returncode == 0 else ""


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


LIVE_DB = HERE / "pool" / "jackpot.db"
_live_before = (( _sha(LIVE_DB), os.path.getmtime(LIVE_DB))
                if LIVE_DB.exists() else None)

# ── A1. TEXT PRESERVATION ────────────────────────────────────────────
print("== 1. TEXT PRESERVATION: every visible string survives ==")
old_html = _gitshow(BASELINE_REV, "pool/templates/index.html")
new_html = TPL.read_text(encoding="utf-8")
check(1.0, bool(old_html), "the baseline commit is readable")


def visible_strings(doc):
    doc = re.sub(r"<script\b.*?</script>", " ", doc, flags=re.S | re.I)
    doc = re.sub(r"<style\b.*?</style>", " ", doc, flags=re.S | re.I)
    doc = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", doc)
    text = html_mod.unescape(text)
    return {ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 3}


def links_of(doc):
    # normalize asset cache-busters (?v=N): bumping a static version is
    # visual surface, NOT a route change — the promise is that no
    # baseline link/route DISAPPEARS
    return sorted({re.sub(r"\?v=\d+", "", l)
                   for l in re.findall(r'(?:href|src)="([^"]+)"', doc)})


old_text = visible_strings(old_html)
new_text = visible_strings(new_html)
missing = sorted(s for s in old_text if s not in new_text)
check(1.1, not missing,
      f"all {len(old_text)} baseline visible strings survive "
      + (f"- MISSING: {missing[:6]}" if missing else ""))
old_links, new_links = links_of(old_html), links_of(new_html)
check(1.2, set(old_links) <= set(new_links),
      f"all {len(old_links)} baseline links/routes survive; new: "
      f"{sorted(set(new_links) - set(old_links))}")
for needle in ("Legal in All 50 States", "No Gambling · No Purchase Needed",
               "A daily raffle that feels", "Create Your Free XMR Jackpot Account",
               "Your wallet is receive-only"):
    check(1.3, needle in new_html and needle in old_html,
          f"spot check byte-exact: {needle[:40]!r}")
check(1.4, "Monero Means Money" in new_html
      and "Monero Means Money" not in old_html,
      "the optional tagline is NEW (visual-only charter surface)")
check(1.5, "Jackpot Notifications" in new_html
      and "The Kraken Funded Challenge" in new_html
      and "$20" in new_html and "$1,000" in new_html,
      "the push-registration and challenge surfaces are present")

# ── A2. THE DESIGN SYSTEM ────────────────────────────────────────────
print("== 2. THE DESIGN SYSTEM: tokens, flatness, selectors ==")
old_css = _gitshow(BASELINE_REV, "pool/static/style.css")
new_css = CSS.read_text(encoding="utf-8")
check(2.1, all(tok in new_css for tok in
               ("#EDEEF0", "#D26E2B", "#CE5E14", "#4C4C4C", "#FFFFFF")),
      "page gray, monero orange, link orange, slate, white all present")
check(2.2, "--radius:         0px" in new_css
      and not re.search(r"border-radius:\s*[1-9]", new_css)
      and not re.search(r"border-radius:\s*0?\.\d+", new_css),
      "flat corners everywhere (radius token 0, no nonzero radii)")
check(2.3, "Open Sans" in new_css and "Hind" in new_css
      and "Press Start" not in new_css and "Inter:wght" not in new_css,
      "the arcade fonts are retired; Open Sans + Hind carry the page")
check(2.4, not re.search(r"linear-gradient", new_css)
      and "screenShake { from { transform: none" in new_css,
      "minimal decoration: no gradients in the system stylesheet, "
      "arcade motion reduced to informative-only")
check(2.5, "@media (max-width: 900px)" in new_css
      and "@media (max-width: 500px)" in new_css
      and "@media (prefers-reduced-motion" in new_css,
      "responsive + reduced-motion support")
old_selectors = set(re.findall(
    r"^\s*((?:[.#][a-zA-Z0-9_-]+[^\{]*)\{)", old_css, flags=re.M))
missing_sel = []
for sel_block in sorted(old_selectors):
    sel = sel_block[:-1].strip()
    parts = [p.strip() for p in sel.split(",")]
    if not all(p in new_css for p in parts):
        missing_sel.append(sel)
check(2.6, not missing_sel,
      f"every old selector still styled ({len(old_selectors)} blocks)"
      + (f" - MISSING: {missing_sel[:8]}" if missing_sel else ""))

# ── B3. SUBSCRIPTIONS ────────────────────────────────────────────────
print("== 3. THE PUSH SERVICE: subscriptions ==")
import push_service as ps                                # noqa: E402
ps.SPOOL_PATH = SANDBOX / "spool.jsonl"
# a REAL sandbox user: audit_log.user_id carries an FK to users — push
# audits are user-scoped rows, so every push fixture uses its real id
import hashlib as _hl                                    # noqa: E402
UID = int(db.create_user("pushuser",
                         _hl.sha256(b"pushuser").hexdigest(),
                         "pushuser@example.com"))
check(3.1, ps.public_key() == "",
      "no operator key provisioned -> the public key endpoint serves "
      "an empty string (the client degrades quietly)")
bad = ps.subscribe(1, {"endpoint": "http://insecure.example/x",
                       "keys": {"p256dh": "a", "auth": "b"}})
check(3.2, not bad["ok"],
      "a non-https endpoint is refused")
ok = ps.subscribe(UID, {"endpoint": "https://push.example/s/abc",
                        "keys": {"p256dh": "k1", "auth": "a1"}})
check(3.3, ok["ok"] and len(ps._load(ps.SUBS_KEY)) == 1,
      "one https registration lands, keyed to its user")
other = ps.subscribe(UID + 1, {"endpoint": "https://push.example/s/abc",
                               "keys": {"p256dh": "k2", "auth": "a2"}})
check(3.4, not other["ok"] and json.loads(db.get_setting(
        ps.SUBS_KEY, "[]"))[0]["user_id"] == UID,
      "another user CANNOT take over the endpoint (never send to the "
      "wrong user)")
ps.subscribe(UID, {"endpoint": "https://push.example/s/abc",
                   "keys": {"p256dh": "k1b", "auth": "a1"}})
rows = ps._load(ps.SUBS_KEY)
check(3.5, len(rows) == 1 and rows[0]["keys"]["p256dh"] == "k1b",
      "the same user re-registering refreshes in place (no dupes)")

# ── B4. SENDS: templates, rate limits, audits, fail-soft ─────────────
print("== 4. SENDS: templated, limited, audited, fail-soft ==")
check(4.1, ps.EVENTS["signal_buy"]["title"] == "📈 BUY NOW"
      and ps.EVENTS["signal_exit"]["title"] == "📉 GET OUT NOW"
      and set(ps.EVENTS) == {"jackpot_win", "claim_reminder",
                             "challenge_unlock", "signal_buy",
                             "signal_exit", "miner_status",
                             "ticket_update"},
      "the charter's event set with byte-exact signal titles")
check(4.2, ps.send_to_user(7, "not_an_event")["reason"] == "unknown event",
      "an unknown event is refused")
_real_send = ps.send_to_user


class _FakeResp:
    status = 200


_sent_boxes = []


def _fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
    _sent_boxes.append((subscription_info["endpoint"], json.loads(data)))
    return _FakeResp()


import types as _types
_fake_mod = _types.ModuleType("pywebpush")
_fake_mod.webpush = _fake_webpush
_fake_mod.WebPushException = type("WebPushException", (Exception,), {})
sys.modules["pywebpush"] = _fake_mod
db.set_setting("VAPID_PRIVATE_KEY", "test-key-nonsecret")

r1 = ps.send_to_user(UID, "jackpot_win")
check(4.3, r1["sent"] == 1 and _sent_boxes
      and _sent_boxes[0][1]["title"] == ps.EVENTS["jackpot_win"]["title"]
      and _sent_boxes[0][1]["url"] == "/",
      "one send, payload built from the template only (title/tag/url)")
check(4.4, all(k in ("title", "body", "tag", "url", "amount_xmr",
                     "deadline", "signal") for k in _sent_boxes[0][1]),
      "no free-form content can ride the payload")
for i in range(10):
    ps.send_to_user(UID, "ticket_update")
sent_now = sum(1 for e, _p in _sent_boxes
               if e == "https://push.example/s/abc"
               and _p["tag"] == "ticket-update")
check(4.5, sent_now == ps.EVENTS["ticket_update"]["rate"],
      f"the per-user rate limit stops at the event class's cap "
      f"({sent_now} sends, not 11)")
with db.get_db() as conn:
    acts = [r[0] for r in conn.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'push%'")]
check(4.6, "push_rate_limited" in acts and "push_sent" in acts,
      f"rate-limit and send audits landed ({acts})")
check(4.7, ps.send_to_user(999999, "jackpot_win")["reason"]
      == "no subscription",
      "a user with no subscription is a quiet no-op, never an error")

# ── B5. THE SPOOL: scout emit -> drain ───────────────────────────────
print("== 5. THE SPOOL: emit, drain, drop, cap ==")
sys.path.insert(0, str(HERE / "scout"))
import push_client as pc                                 # noqa: E402
pc.SPOOL = SANDBOX / "scout_spool.jsonl"
pc.SERVER = "http://127.0.0.1:1"          # nothing listens: spool path
check(5.1, pc.emit("not_real")["ok"] is False,
      "an unknown event never enters the pipeline")
e1 = pc.emit("signal_buy", {"signal": "LONG", "wallet": "leak-me"})
check(5.2, e1 == {"ok": True, "via": "spool"}
      and "leak-me" not in pc.SPOOL.read_text(encoding="utf-8")
      and '"signal":"LONG"' in pc.SPOOL.read_text(encoding="utf-8"),
      "a dead server spools the event; extras are whitelisted (a wallet "
      "string cannot ride)")
handed = ps.drain_spool(pc.SPOOL)
check(5.3, handed == 1 and pc.SPOOL.read_text(encoding="utf-8").strip()
      == "",
      "one drain delivers the spooled signal and empties the spool")
_rows = [{"event": "garbage_event", "ts": time.time()},
         {"event": "signal_buy", "ts": time.time(), "tries": 51},
         {"event": "signal_buy", "ts": time.time()}]
pc.SPOOL.write_text(
    "\n".join(json.dumps(r) for r in _rows) + "\n", encoding="utf-8")
handed2 = ps.drain_spool(pc.SPOOL)
left = [l for l in pc.SPOOL.read_text(encoding="utf-8").splitlines()
        if l.strip()]
check(5.4, handed2 == 1 and len(left) == 0,
      "garbage events are dropped, past-cap rows are dropped (a stale "
      "signal is not a signal), the deliverable drains")
big = [{"event": "signal_buy", "ts": time.time()} for _ in range(600)]
pc.SPOOL.write_text("\n".join(json.dumps(r) for r in big),
                    encoding="utf-8")
pc.emit("signal_buy")
lines = [l for l in pc.SPOOL.read_text(encoding="utf-8").splitlines()
         if l.strip()]
check(5.5, len(lines) <= 501,
      f"the spool is capped ({len(lines)} rows) - it can never grow "
      f"unbounded")

# ── C. WIRING + HERMETICITY ──────────────────────────────────────────
print("== 6. WIRING + HERMETIC ==")
poller_src = (HERE / "pool" / "proxy_poller.py").read_text(encoding="utf-8")
check(6.1, "push_service" in poller_src and "drain_spool()" in poller_src,
      "the poller's loop drains the spool (the wallet-independent "
      "heartbeat is the push system's reader)")
client_src = (HERE / "pool" / "static" / "push_client.js").read_text(
    encoding="utf-8")
check(6.2, "/api/push/subscribe" in client_src
      and "/api/push/public-key" in client_src
      and 'getElementById("btnEnablePush")' in client_src,
      "the browser client points at the service's routes and its own "
      "button only")
sw_src = (HERE / "pool" / "static" / "push_sw.js").read_text(
    encoding="utf-8")
check(6.3, 'url.charAt(0) !== "/"' in sw_src,
      "the service worker navigates same-origin only")
if _live_before:
    check(6.4, (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before,
          "the live jackpot.db is sha256+mtime identical throughout")
else:
    check(6.4, not LIVE_DB.exists(),
          "no live jackpot.db - nothing to protect, nothing touched")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
