# XMR Jackpot — Hostinger Horizons Review Bundle

**Purpose:** Hostinger Horizons review of the site's structure and styling.
Review and correct ONLY the frontend files in this zip. Everything
server-side (the jackpot engine, wallets, the database, the miner
infrastructure) stays exactly where it is.

**Date:** October 10, 2026 (regenerated from the live template + current statics)

---

## What is in the zip

| File | Role |
|---|---|
| `index.html` | The complete site — a single-page app (landing, signup, how-to, play, winners, user dashboard, admin). All pages live in this one file; navigation shows/hides `<section>` blocks. |
| `admin_panel.html` | The admin panel's markup fragment (injected into index.html's admin section by app.js at runtime for authenticated admins). |
| `static/style.css` | The entire design system: tokens (CSS custom properties), all component styles, the getmonero-org redesign layer, responsive rules, reduced-motion support. |
| `static/app.js` | The site's application logic: navigation, auth flows, dashboard population, forms, jackpot/leaderboard rendering (v28). |
| `static/wf_dashboard.js` | Additive workflow wiring for the dashboard tiles/panels — status row, Scout Signals feed, the shared prefs stamp (v6). |
| `static/push_client.js` | Web-push registration, the prefs toggles (If-Match optimistic concurrency, undo), Test-notification buttons, the ONE shared relative-time renderer (v16). |
| `static/push_sw.js` | The push service worker (notification display; same-origin navigation only). |
| `static/bundle_card.js` | The Kraken Scout bundle download card (stable URL + SHA256). (v1) |
| `static/push_report_admin.js` | Admin: daily push report (sends/blocks/rate-limits per category). (v1) |
| `static/push_decisions_admin.js` | Admin: the live suppression/send decisions ledger (audit-derived). (v2) |
| `static/push_vapid_admin.js` | Admin: VAPID provisioning status/actions. (v1) |
| `static/admin_cold_wallet.js` | Admin: cold-wallet surface. (v1) |
| `static/kraken_referral_admin.js` | Admin: Kraken referral projection config + run controls. (v6) |
| `static/referral_projection.js` | Dashboard: the possible-future-jackpot projection card. (v1) |
| `static/img/*.png` | The miner sprite artwork used by the comparison section. |

## Notes for the review

1. **The design system is token-driven.** Colors, fonts, and spacing flow
   from CSS custom properties at the top of `style.css` (--mono-orange,
   --text, --surface-white, --bg-deep, etc.). Flat corners (radius 0),
   Page Gray background, Monero Orange primaries, slate text. Restyling
   via the tokens is safe; please keep the token names stable.
2. **All text content is contractual.** Every visible string is
   preserved byte-for-byte across redesigns and verified by an automated
   drive. If a correction changes wording, flag it explicitly — text
   changes need sign-off, style changes do not.
3. **Class hooks are load-bearing.** app.js and wf_dashboard.js address
   elements by id and class. Renames will break bindings — prefer
   styling changes over structural ones, and list any structural
   suggestions separately.
4. **This is a static preview of a live app.** The pages render fully,
   but the numbers (jackpot, stats, tickets) read `/api/...` endpoints
   that are not part of this zip — in a static preview they will show
   placeholders/zero states by design. That is expected, not a defect.
5. **Service worker scope.** push_sw.js is served from the site root in
   production (route `/push_sw.js`) so its scope covers all pages. In
   this static zip it sits beside the others for completeness.

## What is NOT in this zip (deliberately)

- No server code, no database, no credentials, no environment files.
- No API keys or tokens exist in any included file (scanned before
  packaging). The only "password" strings are visible UI labels on the
  site's own login/signup forms.

## How to preview

Unzip and open `index.html` in a browser (or serve the folder with any
static file server). Everything visual works; live data endpoints
degrade quietly to placeholder states.

## Desired output from the review

For each finding: the file, the problem in plain language, and either a
corrected file or a minimal diff/patch. Prefer corrections that keep the
file inventory and class/id names unchanged.
