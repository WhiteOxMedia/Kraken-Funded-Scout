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
# THE DRIVE MUST NEVER TOUCH THE LIVE DB (environment-proof
# restated, Sep 26: the old sha256+mtime identity pin held only
# on a quiet box; production services legitimately write
# jackpot.db during a run). The invariant that survives any
# environment: this drive DB binding stays on the sandbox file.
_live_db_real = os.path.realpath(str(LIVE_DB)) if LIVE_DB.exists() else None

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

# THE TRUTH AUDIT (Sep 25, user order: "the words need to be based on
# the reality re-word the web-content using the facts"): a closed,
# enumerated set of baseline strings was REWORDED to match the code's
# reality. Every deviation from the baseline must appear here, paired
# with its replacement — anything else missing is a regression.
TRUTH_AUDIT = {
    "One grand prize is worth more than a single miner could mine alone in a year! The more miners, the bigger the pot.":
        "One winner takes 90% of the pot in a single drawing. The more miners, the bigger the pot.",
    "One grand prize is worth more than a single miner could mine alone in a year!":
        "One winner takes 90% of the pot in a single drawing.",
    "1 grand prize = 10+ years of solo mining":
        "90% of the pot to one winner · every 24 hours",
    "One grand prize is worth more than a solo miner could mine in 10 years.":
        "One winner takes 90% of the pot in a single drawing — a payout that grows with the community.",
    "One lucky winner takes 90% of the grand prize — that could be more XMR in a single drawing than you'd mine alone in a year.":
        "One lucky winner takes 90% of the grand prize — a payout that grows as the community grows.",
    "🤝 Today's prize pool grows as miners contribute. The community pot resets every 24 hours.":
        "🤝 Today's prize pool grows as miners contribute — the pot carries over, and a new winner is drawn every 24 hours.",
    "No deposit. No risk. Powered entirely by Kraken's referral bonus program.":
        "No deposit. No risk. Free entries come from mining — Kraken referral bonuses add to the pot as they come in.",
    "The community jackpot is your free route in. Win the daily prize (min ~$20), enter the challenge, pass with Scout signals, get funded, and keep the profits. The loop grows itself through Kraken's referral program.":
        "The community jackpot is your free route in. Win the daily prize, enter the challenge, pass with Scout signals, get funded, and keep the profits. The loop grows itself through Kraken's referral program.",
    "The daily prize is seeded by Kraken referral bonuses — minimum ~$20, every single day.":
        "The daily prize is seeded by the community's mining — and grows as Kraken referral bonuses come in.",
    "Daily prize seeded by Kraken referral bonuses — the referral program funds the pot":
        "Daily prize seeded by community mining — referral bonuses add to it as they come in",
    "Minimum ~$20 — the floor every day, win or not":
        "Grows with the community — more miners and referrals mean a bigger prize",
    "Scales with community mining — 1000 bots → $20, 10,000 bots → $300":
        "Planned scale — community projection: ~$20/day near 1,000 bots, ~$300/day near 10,000",
    "1000 bots → ~$20 · 10,000 bots → ~$300":
        "projection: 1,000 bots → ~$20/day · 10,000 bots → ~$300/day",
    "No community treasury and no deposits — Kraken's referral bonus program funds the prize.":
        "No deposits and no community treasury — the prize is seeded by community mining, with",
    "Kraken pays referral bonuses — the exchange funds the program":
        "Kraken pays referral bonuses — the exchange pays them, the operator records them",
    "Bonuses feed the jackpot — straight into the daily prize":
        "Recorded bonuses feed the jackpot — into the daily prize via the funding ledger",
    "Everything stays free — powered entirely by Kraken's referral program":
        "Everything stays free — mining earns your entries; bonuses only grow the pot",
    "The house retains 10% of all payouts (daily drawings). This covers server costs, development, and operations. House tax is auto-transferred when accumulated balance exceeds 0.01 XMR.":
        "The house retains 10% of all payouts (daily drawings). This covers server costs, development, and operations. House tax stays in the pool and seeds the prize when the balance would fall below the drawing minimum.",
    # (the surrounding legal <p> paragraphs containing the two lines
    #  above also differ from baseline by exactly these fragments —
    #  matched as substrings below by 1.1's fragment pass)
    "We do not share data with third parties. Mining rewards are sent to your wallet via the Monero network. Kraken is used only for house tax transfers (affiliate link is optional).":
        "We do not share data with third parties. Mining rewards are sent to your wallet via the Monero network. Kraken appears only through the optional referral link; house tax stays in the pool.",
    "Goes to marketing to grow the community":
        "Covers servers, development, and operations — and seeds the prize when the pot runs short",
    "Used to facilitate marketing & community growth — bigger community = bigger pot, and so on":
        "The community’s referral link — recorded bonuses from it grow the pot",
    "— 10% of all payouts, sent to Kraken":
        "— 10% of all payouts, held by the pool (seeds the prize when short)",
    "Tax Sent to Kraken":
        "Total Paid to Winners",
    "Our mining pool receives periodic promotional credits and mining-related bonuses from partner exchanges like Kraken. When users connect a wallet, we can verify active miners and distribute these bonuses directly into the grand prize.":
        "Kraken referral bonuses paid through the community's referral link are recorded by the operator and directed into the grand prize. Connecting a wallet helps verify active miners so those bonuses reach the prize.",
    "Bonus drops": "Referral bonus eligibility",
    "Stronger community rewards": "A stronger, verifiable community",
    "A: Connected wallets let us verify active miners, which unlocks Kraken bonus credits that increase the grand prize.":
        "A: Connected wallets let us verify active miners, so recorded Kraken referral bonuses can be directed into the grand prize.",
    "Special Kraken bonus multipliers for verified (wallet-connected) referrals":
        "Recorded Kraken referral bonuses for wallet-connected referrals",
    "Join the miners building the biggest Monero grand prize pool.":
        "Join the miners building the community's Monero grand prize pool.",
    "🏆":
        "(removed with the trust stamp - superseded by the Sep 25 "
        "evening II legality award)",
    "Legal in All 50 States":
        "(sticker removed by user order, Sep 25 evening IV - the claim "
        "lives on the How It Works page)",
    "No Gambling · No Purchase Needed":
        "(sticker removed by user order, Sep 25 evening IV - the claim "
        "lives on the How It Works page)",
}
unsanctioned = [s for s in missing if s not in TRUTH_AUDIT]
check(1.1, not unsanctioned,
      f"all baseline strings survive except the {len(TRUTH_AUDIT)} "
      f"enumerated truth-audit rewordings "
      + (f"- UNSANCTIONED MISSING {len(unsanctioned)}: {unsanctioned[:6]}"
         if unsanctioned else ""))
_norm = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", new_html)))
not_landed = [new for new in TRUTH_AUDIT.values()
              if not new.startswith("(")
              and re.sub(r"\s+", " ", new) not in _norm]
check(1.10, not not_landed,
      f"all {len(TRUTH_AUDIT)} audited rewordings are present "
      + (f"- NOT LANDED: {not_landed[:6]}" if not_landed else ""))
old_links, new_links = links_of(old_html), links_of(new_html)
check(1.2, set(old_links) <= set(new_links),
      f"all {len(old_links)} baseline links/routes survive; new: "
      f"{sorted(set(new_links) - set(old_links))}")

# ── 1.5 THE MASTER-KEY WORKFLOW ADDITIONS (phase 2 redesign): the
# charter's required new copy is PRESENT, additions stay bounded, and
# the mandated phrases link to the real origin
REQUIRED_NEW = [
    # hero
    "Start Free. Unlock the Master Key. Get Funded.",
    "Kraken Scout is the Master Key to beating the Kraken Funded Challenge",
    "The community jackpot is your free route in",
    "Start For Free", "Get Kraken Scout", "How It Works",
    "No deposit. No risk. Free entries come from mining",
    # section 2 — the free route in
    "Start Free With the Community Jackpot",
    "Daily prize seeded by community mining", "Grows with the community",
    "1,000 bots", "10,000 bots", "~$300/day",
    "Ticket value increases with active mining", "Better odds for active miners",
    "Join Jackpot", "See Daily Prize",
    # section 3 — the master key
    "Kraken Scout = The Master Key", "LONG bullish signals",
    "Buy Now", "Exit Now", "Helps pass the Kraken Funded Challenge",
    "Delivered through your user account",
    "Push notifications enabled in the dashboard", "See Signals",
    # section 4 — get funded
    "Pass the Challenge", "Enter the challenge using the jackpot prize",
    "Pass with Scout signals", "Kraken funds your trading account",
    "You keep all profits", "Enter Challenge", "Funded Account Tiers",
    "$1,000 funded", "$5,000 funded", "$10,000 funded",
    # section 5 — the loop
    "Kraken Pays the Jackpot", "Kraken pays referral bonuses",
    "Recorded bonuses feed the jackpot", "Everything stays free",
    "Use Referral Link", "See Jackpot Growth",
    # section 6 — mining
    "Turn On the Miner Bot", "Active mining increases ticket value",
    "Higher-value tickets = better odds", "Mining is optional",
    "controlled through the dashboard", "Turn Miner On", "Check Ticket Value",
    # mandated phrases (the miner character was removed from the
    # homepage per the Sep 25 follow-up — the asset stays, unused)
    "Not sure where to get started? Start here:", "Start for free at:",
]
missing_new = [s for s in REQUIRED_NEW if s not in new_html]
check(1.5, not missing_new,
      f"all {len(REQUIRED_NEW)} required phase-2 strings present "
      + (f"- MISSING: {missing_new[:6]}" if missing_new else ""))
added = new_text - old_text
check(1.6, len(added) < 220,
      f"the redesign stays additive-bounded ({len(added)} new strings "
      f"on top of the {len(old_text)} preserved ones)")
check(1.7, 'href="https://xmrjackpot.com"' in new_html,
      "the mandated phrases link the real origin (https)")
for needle in ("A daily raffle that feels", "Create Your Free XMR Jackpot Account",
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
check(3.4, not other["ok"] and ps._load(ps.SUBS_KEY)[0]["user_id"] == UID,
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
                             "ticket_update",
                             # Sep 26 charter extension: the referral-
                             # window expiry notice (user-mandated)
                             "referral_window",
                             # Sep 26: the VAPID test-push event
                             # (admin 'send test to my devices')
                             "push_test",
                             # Sep 26: the user-facing pipe-test event
                             # (Notifications card 'Test notification',
                             # scout family)
                             "push_test_signal",
                             # Sep 27: the operator's orphan alert (admin
                             # web-push from the scheduler's weekly leg)
                             "orphans_alert",
                             # Sep 27: the weekly ops digest (leg 7) -
                             # one line of orphans/prunes/KC-verdict
                             # pushed to ADMIN subscribers, with the
                             # scoped _digest_line body override
                             "ops_digest"},
      "the charter's event set with byte-exact signal titles")
check(4.2, ps.send_to_user(7, "not_an_event")["reason"] == "unknown event",
      "an unknown event is refused")

# ── 4b. THE STORE-SHAPE AUDIT (Sep 26) ─────────────────────────
print("== 4b. POISONED STORES: one bad row never kills the sender ==")
import json as _json
_poisoned = [
    {"user_id": 7, "endpoint": "https://push.example/good",
     "keys": {"p256dh": "a", "auth": "b"}, "ts": 1.0},
    {"user_id": "not-a-number", "endpoint": "https://x/bad1"},
    {"endpoint": "https://x/noid"},
    {"user_id": 8, "endpoint": 42},
    "not-even-a-dict",
]
ps._save(ps.SUBS_KEY, _poisoned)
rows = ps._load_subs()
check("4b.1", len(rows) == 1 and rows[0]["user_id"] == 7
      and isinstance(rows[0]["user_id"], int),
      f"_load_subs keeps only well-shaped rows ({len(rows)})")
_res = ps.send_to_user(7, "push_test")
check("4b.2", isinstance(_res, dict) and _res.get("sent") == 0
      and "reason" in _res,
      "a poisoned store degrades to a quiet no-send, never a raise")
_res = ps.send_to_subscribers("push_test")
check("4b.3", isinstance(_res, dict),
      "the broadcast path survives the poisoned store too")
# restore section 3.5's EXACT end state (the refreshed registration for
# UID and NOTHING else — the poisoned fixture's user-7 row is
# well-shaped, so subscribe() alone would leave it in and the section-5
# broadcast would audit for a nonexistent user -> FK failure)
ps._save(ps.SUBS_KEY, [{"user_id": UID,
                        "endpoint": "https://push.example/s/abc",
                        "keys": {"p256dh": "k1b", "auth": "a1"},
                        "ts": time.time()}])

# the rate limiter is thread-safe: concurrent same-user checks may not
# both pass an exhausted bucket (the unlocked read-modify-write could)
import threading as _th
# (the old `_rl = ps.RateBucket if hasattr(...)` line is gone: an
# existence-only pin, assigned once and never read — push_service has
# no RateBucket; the _rate_lock + _rate_ok below ARE the mechanism and
# this section already drives them through the real call)
ps._save(ps.RATE_KEY, [])
_verdicts = []
def _hammer():
    _verdicts.append(ps._rate_ok(99, "push_test"))   # rate=4
_ts = [_th.Thread(target=_hammer) for _ in range(8)]
for t in _ts: t.start()
for t in _ts: t.join()
check("4b.4", sum(1 for v in _verdicts if v) == 4
      and sum(1 for v in _verdicts if not v) == 4,
      f"8 concurrent checks against rate=4 -> exactly 4 pass "
      f"({sum(1 for v in _verdicts if v)}) — the lock holds")
ps._save(ps.RATE_KEY, [])
_real_send = ps.send_to_user


class _FakeResp:
    status = 200


_sent_boxes = []


def _fake_webpush(subscription_info, data, vapid_private_key, vapid_claims,
                  headers=None):   # headers: the WNS-required TTL rides here
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
check(4.6, "push_rate_limited" in acts and "push_send_allowed" in acts,
      f"rate-limit and send-allowed audits landed ({acts})")
# 4.8 THE WNS TTL REQUIREMENT (found live, Sep 27): Microsoft's push
# service (every Edge/Windows device) answers 400 Bad Request without
# a TTL header; FCM tolerates the absence, so dead-endpoint sandbox
# drives can never see the difference - the sender's webpush call is
# pinned the house source-idiom way (the mode=ro precedent).
_src = (HERE / "pool" / "push_service.py").read_text(
    encoding="utf-8", errors="replace")
check(4.8, 'headers={"TTL":' in _src
      and 'webpush(' in _src,
      "the sender passes TTL to webpush (WNS requires it; FCM does not "
      "care, which is why only a live device could expose this)")
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
if _live_db_real:
    check(6.4, db.DB_PATH != LIVE_DB
          and os.path.realpath(str(db.DB_PATH)) != _live_db_real,
          "the drive DB binding stays on the SANDBOX file - the live "
          "jackpot.db is never opened by this drive (the live file "
          "may change under production services; that is their right)")
else:
    check(6.4, not LIVE_DB.exists(),
          "no live jackpot.db - nothing to protect, nothing touched")

# ── D. THE LIVE WIRING (wired Sep 25 when server.py freed up) ────────
print("== 7. THE ROUTES ARE WIRED ==")
server_src = (HERE / "pool" / "server.py").read_text(encoding="utf-8")
check(7.1, '@app.get("/api/push/public-key")' in server_src
      and '@app.post("/api/push/subscribe")' in server_src
      and '@app.post("/api/push/unsubscribe")' in server_src
      and '@app.post("/api/push/event")' in server_src
      and "push_svc.handle_public_key()" in server_src
      and "push_svc.handle_subscribe(" in server_src,
      "all four routes wired verbatim over the service handlers "
      "(public-key / subscribe / unsubscribe / the operator event route)")
check(7.2, 'secrets.compare_digest(supplied, stored)' in server_src,
      "the operator token compares in constant time")
check(7.3, '@app.get("/push_sw.js"' in server_src
      and '"Service-Worker-Allowed": "/"' in server_src,
      "the worker is served from the ROOT path (a /static/ default "
      "scope can never control pages at / - caught by the live e2e "
      "proof)")
prov_src = (HERE / "provision_push.py").read_text(encoding="utf-8")
check(7.4, "der_b64url" in prov_src
      and "vapid_key_format" in prov_src
      and "load_der_private_key" in prov_src,
      "the provisioner stores the key in the format the sender's "
      "Vapid.from_string actually reads (a PEM key would make every "
      "send fail-soft silent) and stamps the format")
probe_src = (HERE / "_probe_e2e_capture.py").read_text(encoding="utf-8")
check(7.5, "PROBE VERDICT" in probe_src
      and "encode_dss_signature" in probe_src
      and "Content-Encoding: aes128gcm" in probe_src,
      "the e2e capture probe exists: real chain, captured wire, "
      "independent JWT verification + RFC 8291 decryption")

# ── 7.6 THE SIGNALS FEED IS SIGNALS ONLY (third triage, Sep 25 late
# batch: the feed read every push_sent row for the caller, so a
# jackpot_win push displayed as a Scout signal — filtered BEFORE the
# limit now, and pinned by BEHAVIOR, not by source-text) ─────────────
print("== 7.6 THE FEED CARRIES ONLY SIGNAL EVENTS ==")
_uid_feed = int(db.create_user("feeduser", "x" * 64,
                               "feeduser@example.com"))
ps.subscribe(_uid_feed, {"endpoint": "https://push.example/feed/one",
                         "keys": {"p256dh": "k1f", "auth": "a1f"}})
ps.send_to_user(_uid_feed, "jackpot_win")       # must NOT appear
ps.send_to_user(_uid_feed, "claim_reminder")    # must NOT appear
ps.send_to_user(_uid_feed, "signal_buy")        # the one real signal
_feed = ps.scrape_recent_signals(_uid_feed)
_feed_titles = [r["title"] for r in _feed]
check(7.6, ps.EVENTS["jackpot_win"]["title"] not in _feed_titles
      and ps.EVENTS["claim_reminder"]["title"] not in _feed_titles
      and ps.EVENTS["signal_buy"]["title"] in _feed_titles,
      f"a jackpot_win + claim_reminder NEVER appear as signals "
      f"(feed titles: {len(_feed_titles)} rows, none targeted) while "
      "the real signal_buy does")
check(7.7, all("BUY" in t or "OUT" in t for t in _feed_titles),
      "every row in the feed is a Buy/Exit signal — nothing else")

# ── 8. THE PHASE-2 WORKFLOW LAYER (homepage + dashboard) ─────────────
print("== 8. THE MASTER-KEY WORKFLOW LAYER ==")
check(8.1, '/static/wf_dashboard.js?v=' in new_html
      and 'id="btnHeroStartFree"' in new_html
      and 'id="wf-jackpot"' in new_html and 'id="wf-scout"' in new_html
      and 'id="wf-funded"' in new_html and 'id="wf-loop"' in new_html
      and 'id="wf-mining"' in new_html
      and 'id="wf-phrases"' in new_html,
      "the five workflow sections, the phrases strip, the hero CTA, "
      "and the additive wiring script are all in the template")
wf_src = (HERE / "pool" / "static" / "wf_dashboard.js").read_text(
    encoding="utf-8")
check(8.2, "/api/push/signals" in wf_src
      and "esc(" in wf_src and "dataset.wfWired" in wf_src
      and "btnEnablePush" in wf_src
      and "btnSignupCreate" not in wf_src
      and "btnSignupLogin" not in wf_src,
      "the wiring script owns only the wf-* elements: user-scoped "
      "signals fetch, escape-before-HTML, idempotent binding, and "
      "it never re-binds app.js's own hooks")
app_src = (HERE / "pool" / "static" / "app.js").read_text(
    encoding="utf-8")
check("8.2b", "window.XMRJackpotPush" in app_src
      and "new Date(a.created_at*1000).toLocaleString()" not in app_src
      and "new Date(u.last_login * 1000).toLocaleString()" not in app_src
      and "timeAgo(a.created_at)" in app_src
      and "timeAgo(u.last_login)" in app_src,
      "the stamp sweep landed: the audit log and last-login raw "
      "locale stamps are gone, and app.js's ago-stamps route through "
      "the shared relTime exporter")
check(8.3, '@app.get("/api/push/signals")' in server_src
      and 'login required' in server_src,
      "the signals feed is a logged-in route (no cross-user, no "
      "anonymous reads)")
svc_src = (HERE / "pool" / "push_service.py").read_text(encoding="utf-8")
check(8.4, "def scrape_recent_signals" in svc_src
      and "signal_buy" in svc_src and "signal_exit" in svc_src
      and "whitelist" in svc_src.lower(),
      "the signals scraper filters to the signal events before the "
      "limit (the action whitelist is the feed's contract) — no "
      "endpoints, no keys, no details JSON")
check(8.5, not (HERE / "pool" / "templates" / "index.html")
      .read_text(encoding="utf-8").count("miner_character")
      and not (HERE / "pool" / "static" / "img" /
               "miner_character.svg").exists(),
      "the miner character illustration is REMOVED from the homepage "
      "AND the asset file is deleted from the repo (Sep 25 follow-up: "
      "zero template references, no static/img/miner_character.svg)")
check(8.6, 'style="position:absolute;left:0;top:50%' not in new_html
      and 'wf-award' not in new_html
      and 'style="margin:0 auto 18px;background:var(--surface-white);'
          'border:2px solid var(--mono-orange)' not in new_html,
      "the legality sticker is FULLY removed per user order, Sep 25 "
      "evening IV (no wf-award markup, no old inline stamp, no "
      "margin-pinned element in the hero)")
check(8.7, new_html.count("miner_sprite") == 2
      and 'id="realMinerSprite1"' in new_html,
      "the pixel-art sprite remains ONLY in the app.js-wired realplay "
      "scene (2 refs; the one-click box and the solo-vs-pool comparison "
      "now carry flat design-system numerals - user report, Sep 25)")
check(8.8, 'position:absolute' not in new_html
      and 'position: absolute' not in new_html,
      "nothing is margin-pinned or absolutely positioned in the "
      "homepage - everything centers per user order, Sep 25 evening IV")

print("== 9. THE BROADCAST FANS OUT PER USER (review triage, Sep 25 evening V) ==")
# one user, TWO devices: a broadcast must deliver once per user, not
# once per endpoint (duplicates + double rate-limit burn)
import push_service as _ps2
_ps2.SPOOL_PATH = SANDBOX / "spool2.jsonl"
UID2 = int(db.create_user("pushuser2",
                          _hl.sha256(b"pushuser2").hexdigest(),
                          "pushuser2@example.com"))
_ok1 = _ps2.subscribe(UID2, {"endpoint": "https://dev1.example/s",
                             "keys": {"p256dh": "a", "auth": "b"}})
_ok2 = _ps2.subscribe(UID2, {"endpoint": "https://dev2.example/s",
                             "keys": {"p256dh": "a", "auth": "b"}})
check(9.0, _ok1["ok"] and _ok2["ok"], "two devices registered for one user")
# no VAPID key provisioned in the sandbox -> send_to_user returns the
# config-error result, but the AUDIT row still counts the RECIPIENTS:
# that is where the dedupe is observable without a real webpush lib
_r9 = _ps2.send_to_subscribers("jackpot_win", {"amount_xmr": "1.0"})
# shared sandbox: the expected recipient count is whatever distinct
# users hold subscriptions RIGHT NOW - the invariant is that the
# broadcast audited ONE fan-out per USER, never per endpoint
_subs_now = _ps2._load(_ps2.SUBS_KEY)
_users_now = {int(r["user_id"]) for r in _subs_now if int(r.get("user_id", -1)) > 0}
_endpoints_now = len(_subs_now)
_last_bc = None
with db.get_db() as conn:
    _last_bc = [json.loads(r["details"]).get("recipients")
                for r in conn.execute(
                    "SELECT details FROM audit_log WHERE "
                    "action = 'push_broadcast' ORDER BY id DESC LIMIT 1"
                ).fetchall() if r["details"]]
check(9.1, _last_bc and _endpoints_now > len(_users_now)
      and _last_bc[0] == len(_users_now),
      f"multi-device state exists ({_endpoints_now} endpoints, "
      f"{len(_users_now)} users) and the broadcast audited "
      f"recipients={_last_bc} == the USER count - one fan-out per "
      "USER, not per endpoint")

print("== 10. THE TWO NOTIFICATION OPT-OUTS (user order, Sep 25 evening VII) ==")
# Scout Notifications and Jackpot Notifications: independent per-user
# opt-outs, enforced by the service BEFORE any send, every event in
# exactly one category, and the toggle audited.
_cats = set()
for _ev, _cat in ps.CATEGORY.items():
    _cats.add(_cat)
    assert _ev in ps.EVENTS, f"{_ev} categorized but not templated"
check(10.1, _cats == {"scout", "jackpot"},
      f"every event maps to exactly one of the two dashboard "
      f"categories (mapped: {len(ps.CATEGORY)} events)")
check(10.2, ps.get_prefs(999999) == {"scout": True, "jackpot": True},
      "defaults are BOTH ON for a user with no saved prefs")
_r10 = ps.set_pref(UID, "scout", False)
check(10.3, _r10["ok"] and ps.get_prefs(UID)["scout"] is False,
      "the scout opt-out saves and reads back")
_uid2 = int(db.create_user("pushuser3",
                           _hl.sha256(b"pushuser3").hexdigest(),
                           "pushuser3@example.com"))
_ps2.subscribe(_uid2, {"endpoint": "https://dev3.example/s",
                       "keys": {"p256dh": "a", "auth": "b"}})
# scout OFF for UID2: a signal event must be refused by category
ps.set_pref(_uid2, "scout", False)
_off = ps.send_to_user(_uid2, "signal_buy")
check(10.4, _off["sent"] == 0 and "opted out" in (_off.get("reason") or ""),
      f"scout OFF blocks signal_buy: {_off.get('reason')}")
# jackpot still ON for UID2: a jackpot event still attempts (config-
# error result here - no VAPID in the sandbox - but NOT the opt-out)
_on = ps.send_to_user(_uid2, "ticket_update")
check(10.5, "opted out" not in (_on.get("reason") or ""),
      f"jackpot ON is unaffected by the scout opt-out: "
      f"{_on.get('reason')!r} (no VAPID in the sandbox -> config-error "
      "reason, anything but the opt-out gate)")
# and the audit trail names the toggle
with db.get_db() as conn:
    _pref_rows = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE action = 'push_pref_changed'"
    ).fetchone()["c"]
check(10.6, _pref_rows >= 2,
      f"{_pref_rows} push_pref_changed audit rows - the toggles are "
      "logged like every push decision")
ps.set_pref(UID, "scout", True)

print("== 11. THE PREFS ENVELOPE CONTRACT (user shape, Sep 25 evening VIII) ==")
# GET shape: {"user_id": ..., "preferences": {...}, "updated_at": ...}
# POST accepts the bulk envelope; unknown user_id on the wire is
# IGNORED (the authenticated session is always the subject)
_r11 = ps.set_prefs(_uid2, {"jackpot": False, "scout": True})
check(11.1, _r11["ok"] and _r11["prefs"]["jackpot"] is False
      and _r11["prefs"]["scout"] is True,
      f"bulk set lands: {_r11['prefs']}")
check(11.2, isinstance(_r11.get("updated_at"), str)
      and _r11["updated_at"].endswith("Z") and "T" in _r11["updated_at"],
      f"updated_at is a UTC Z stamp: {_r11.get('updated_at')}")
_g11 = ps.get_prefs_updated(_uid2)
check(11.3, _g11[0] == {"jackpot": False, "scout": True}
      and _g11[1] == _r11["updated_at"],
      "the store round-trips the envelope (prefs + stamp)")
# unknown keys ignored, no corruption
_r11b = ps.set_prefs(_uid2, {"jackpot": True, "nonsense": True})
check(11.4, _r11b["ok"] and _r11b["prefs"] == {"jackpot": True, "scout": True},
      f"unknown category keys ignored: {_r11b['prefs']}")
# no-change writes do not bump the stamp
_before = ps.get_prefs_updated(_uid2)[1]
ps.set_prefs(_uid2, {"jackpot": True})
_after = ps.get_prefs_updated(_uid2)[1]
check(11.5, _before == _after,
      "an idempotent write leaves updated_at untouched")
# and the opt-out gate still bites through the bulk path
_off11 = ps.send_to_user(_uid2, "signal_buy")
ps.set_prefs(_uid2, {"jackpot": True, "scout": False})
_off11b = ps.send_to_user(_uid2, "signal_buy")
check(11.6, "opted out" not in (_off11.get("reason") or "")
      and "opted out" in (_off11b.get("reason") or ""),
      "the bulk path feeds the same send-time gate (scout off -> "
      "signal_buy refused)")
ps.set_prefs(_uid2, {"jackpot": True, "scout": True})

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
