"""i18n_pages.py — the miner-facing pages, rendered from the translation catalog.

Two pages, six languages, one template each. The copy lives in
`templates/i18n/<lang>.json` and never in this file, so a translator changes
text without touching markup, and the parity test can insist that every
language carries every key.

Why these pages and not the whole site: the dashboard, the game and the winners
table are one English product surface, and pretending otherwise would mean six
half-translated copies of an app that changes weekly. The surfaces a
non-English-speaking miner actually gets stuck on are the download and the
relay status — those are the two that exist in every language here. Nothing
else is advertised as translated, and the download page says in each language
that the steps and winners pages are English-only.

Rendering rules that matter:

* `hreflang` alternates are emitted on every language, including English, and
  `x-default` points at English. That is what makes `/es/download` and
  `/download` the same page in two languages rather than two rival pages.
* The language selector links to `/lang/<code>?next=<that language's page>`,
  so a click both remembers the choice and lands on the translated URL with no
  second redirect.
* Everything interpolated is escaped. Probe details come from our own monitor,
  but a public page is a bad place to start trusting strings.
"""
from __future__ import annotations

from html import escape

import i18n

CARD_OPEN = '<div class="card">'

# The template's own type stack, so a standalone page does not fall back to the
# browser's serif while the app around it uses Inter/Press Start 2P.
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;'
    '700;800;900&family=Press+Start+2P&display=swap" rel="stylesheet">'
)


def _pages_paths(page: str) -> list[tuple[str, str]]:
    return [(code, i18n.page_path(code, page)) for code in i18n.LANGUAGES]


def _alternates(origin: str, page: str) -> str:
    rows = [
        f'<link rel="alternate" hreflang="{code}" href="{origin}{path}">'
        for code, path in _pages_paths(page)
    ]
    rows.append(
        f'<link rel="alternate" hreflang="x-default" '
        f'href="{origin}{i18n.page_path(i18n.DEFAULT_LANG, page)}">')
    return "\n".join(rows)


def _lang_switch(lang: str, page: str) -> str:
    links = []
    for code, _path in _pages_paths(page):
        target = f"/lang/{code}?next={i18n.page_path(code, page)}"
        current = ' aria-current="true" style="font-weight:700;"' if code == lang else ""
        links.append(
            f'<a href="{target}" hreflang="{code}" lang="{code}"'
            f' title="{escape(i18n.LANGUAGES[code])}"{current}>{code.upper()}</a>')
    return ('<nav aria-label="' + escape(i18n.t(lang, "common.langLabel"))
            + '" style="font-size:0.85em;color:var(--text-mute);margin:0 0 18px;">'
            + ' <span style="opacity:0.6;">/</span> '.join(links) + "</nav>")


def _shell(lang: str, origin: str, page: str, title: str, description: str,
           body: str, chrome: dict | None = None, robots: str = "") -> str:
    """The page around the copy: site chrome, head, canonical, alternates.

    `chrome` carries the template's navbar, footer and stylesheet href. A page
    rendered without them is not a translated page - it is a different, older
    looking site, which is exactly what the first version of this renderer
    shipped.
    """
    chrome = chrome or {}
    canonical = origin + i18n.page_path(lang, page)
    robots_meta = ('\n<meta name="robots" content="' + robots + '">') if robots else ""
    style = chrome.get("style") or "/static/style.css"
    return (
        f'<!doctype html>\n<html lang="{lang}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f'<meta name="description" content="{escape(description)}">\n'
        f'<link rel="canonical" href="{canonical}">\n'
        + _alternates(origin, page) + "\n"
        + FONTS + "\n"
        + f'<link rel="stylesheet" href="{escape(style)}">' + robots_meta + "\n"
        "</head>\n<body>\n<div id=\"app\">\n"
        + (chrome.get("nav") or "") + "\n"
        '<main style="max-width:900px;margin:0 auto;padding:22px 18px 56px;">\n'
        + _lang_switch(lang, page) + "\n"
        + body + "\n"
        "</main>\n"
        + (chrome.get("footer") or "") + "\n"
        "</div>\n</body>\n</html>\n"
    )


def _region_card(lang: str, region: dict) -> str:
    """Region guidance: latency advice only, never a legality claim."""
    country = (region or {}).get("country") or ""
    region_key = (region or {}).get("region") or "global"
    near = bool((region or {}).get("near"))
    if not country:
        guidance = i18n.t(lang, "download.regionUnknown")
    else:
        guidance = i18n.t(lang, "download.regionKnown", country=country)
        guidance += " " + (
            i18n.t(lang, "download.regionNear") if near else
            i18n.t(lang, "download.regionFar",
                   region=i18n.t(lang, "region.name." + region_key)))
    if not country and region_key != "global" and (region or {}).get("colo"):
        # No country, but Cloudflare told us which edge served the request.
        guidance += " " + i18n.t(
            lang, "download.regionFar",
            region=i18n.t(lang, "region.name." + region_key))
    return (
        CARD_OPEN + '\n<h2>' + escape(i18n.t(lang, "download.regionTitle")) + "</h2>\n"
        + '<p style="font-size:0.95em;">' + escape(guidance) + "</p>\n"
        + '<p class="seo-meta" style="font-size:0.8em;">'
        + escape(i18n.t(lang, "download.privacy")) + "</p>\n</div>"
    )


def _owner_strip(lang: str) -> str:
    """Two labels, one line each: a partner's term must never read as ours.

    The download page sells Scout, which runs Kraken's challenge — the only
    place on the site where a partner's product and ours sit in the same
    sentence. This strip states which half is whose and how a partner bonus
    reaches the pot, in every language, before the chooser.
    """
    cell = ('<div style="flex:1 1 300px;border:1px solid {border};'
            'border-radius:10px;padding:10px 12px;background:{bg};">'
            '<div style="font-size:0.7em;color:var(--text-mute);'
            'letter-spacing:.06em;text-transform:uppercase;'
            'margin-bottom:4px;">{label}</div>'
            '<div style="font-size:0.85em;line-height:1.6;">{body}</div>'
            "</div>")
    kraken = cell.format(border="rgba(124,124,255,.28)",
                         bg="rgba(124,124,255,.06)", label="Kraken",
                         body=escape(i18n.t(lang, "download.ownerKraken")))
    pool = cell.format(border="rgba(255,196,0,.3)",
                       bg="rgba(255,196,0,.05)", label="XMR Jackpot",
                       body=escape(i18n.t(lang, "download.ownerPool")))
    return (CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.ownerTitle"))
            + "</h2>\n"
            '<div style="display:flex;gap:12px;flex-wrap:wrap;">\n'
            + kraken + "\n" + pool + "\n</div>\n"
            '<p style="font-size:0.85em;color:var(--text-mute);'
            'line-height:1.6;margin:10px 0 0;">'
            + escape(i18n.t(lang, "download.ownerMeet")) + "</p>\n</div>\n")


def render_download(lang: str, origin: str, region: dict | None = None,
                    bundle: dict | None = None, qr_svg: str = "",
                    chrome: dict | None = None,
                    offer: dict | None = None) -> str:
    """The download page: two products, the endpoint, the fixes."""
    b = bundle or {}
    region = region or {}
    offer = offer or {}
    size = b.get("size") or "—"
    version = b.get("version") or "—"
    built = b.get("built") or "—"
    sha = b.get("sha256") or "—"
    meta = i18n.t(lang, "download.meta", size=size, version=version,
                  built=built, sha=sha)

    items = "".join(
        "<li><strong>" + escape(i18n.t(lang, f"download.what{n}Title"))
        + "</strong> — " + escape(i18n.t(lang, f"download.what{n}Body")) + "</li>\n"
        for n in (1, 2))   # no pool-view card: the pool is operator-only (Oct 25)

    # TWO downloads, said out loud. They are different artifacts - the miner
    # installer (.bat, no Scout) and the Kraken Scout bundle (.zip) - and a page
    # that offers only the bundle leaves the visitor who wants just a miner with
    # no button at all.
    chooser = (
        CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.chooseTitle"))
        + "</h2>\n"
        '<div style="display:flex;gap:16px;flex-wrap:wrap;">\n'
        '  <div style="flex:1 1 280px;border:1px solid var(--bg-cobble);'
        'border-radius:12px;padding:16px;">\n'
        '    <h3 style="margin-top:0;">'
        + escape(i18n.t(lang, "download.minerTitle")) + "</h3>\n"
        '    <p style="font-size:0.95em;">'
        + escape(i18n.t(lang, "download.minerBody")) + "</p>\n"
        '    <p><a class="btn btn-gold" href="/api/miner-download">'
        + escape(i18n.t(lang, "download.minerCta")) + "</a></p>\n"
        '    <p class="seo-meta" style="font-size:0.8em;">'
        + escape(i18n.t(lang, "download.minerNote")) + "</p>\n"
        "  </div>\n"
        '  <div style="flex:1 1 280px;border:1px solid var(--gold);'
        'border-radius:12px;padding:16px;">\n'
        '    <h3 style="margin-top:0;">'
        + escape(i18n.t(lang, "download.scoutTitle")) + "</h3>\n"
        '    <p style="font-size:0.95em;">'
        + escape(i18n.t(lang, "download.scoutBody",
                         price=offer.get("price", "0.01"),
                         usd=offer.get("usd", "95.00"),
                         days=offer.get("days", 30))) + "</p>\n"
        '    <p><a class="btn btn-gold" href="/downloads/KrakenScoutBundle.zip" '
        "download>" + escape(i18n.t(lang, "download.cta")) + "</a>\n"
        '    <a class="btn btn-outline" href="/#dashboard">'
        + escape(i18n.t(lang, "download.scoutUnlock")) + "</a></p>\n"
        '    <p class="seo-meta" style="font-size:0.8em;">'
        + escape(i18n.t(lang, "download.scoutNote")) + "</p>\n"
        '    <p class="seo-meta" style="font-size:0.8em;">' + escape(meta)
        + "</p>\n"
        "  </div>\n</div>\n</div>\n"
        # Where the money goes, said plainly: the Scout Pro donation is
        # credited to the pot once per order and is the only paid path.
        # No XMRJ or treasury rail moves XMR into the pot (see B28).
        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.treasuryTitle"))
        + "</h2>\n<p style=\"font-size:0.95em;\">"
        + escape(i18n.t(lang, "download.treasuryBody")) + "</p>\n</div>\n")

    body = (
        "<h1>" + escape(i18n.t(lang, "download.h1")) + "</h1>\n"
        '<p class="lead">' + escape(i18n.t(lang, "download.lead")) + "</p>\n"
        + _owner_strip(lang)
        + chooser
        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.whatTitle")) + "</h2>\n"
        "<ul>\n" + items + "</ul>\n</div>\n"

        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.endpointTitle"))
        + "</h2>\n"
        '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;">\n'
        '  <div style="background:#fff;padding:10px;border-radius:12px;'
        'border:1px solid var(--bg-cobble);line-height:0;">'
        + (qr_svg or '<span style="color:var(--text-mute);font-size:0.8em;">'
                     'QR unavailable</span>') + "</div>\n"
        '  <div style="font-size:0.9em;color:var(--text-mute);max-width:320px;'
        'line-height:1.8;">\n'
        "    <b>" + escape(i18n.t(lang, "download.qrLabel")) + "</b><br>\n"
        '    <code style="color:var(--diamond-bright);">'
        "stratum+tcp://mine.xmrjackpot.com:3333</code><br>\n"
        '    <a href="' + i18n.page_path(lang, "status")
        + '" style="color:var(--diamond-bright);">'
        + escape(i18n.t(lang, "download.statusLink")) + " →</a>\n"
        "  </div>\n</div>\n</div>\n"

        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.verifyTitle"))
        + "</h2>\n<p>" + escape(i18n.t(lang, "download.verifyBody")) + "</p>\n"
        "<pre><code>certutil -hashfile KrakenScoutBundle.zip SHA256   # Windows\n"
        "shasum -a 256 KrakenScoutBundle.zip               # macOS / Linux</code></pre>\n"
        "<p>" + escape(i18n.t(lang, "download.verifyNote")) + "</p>\n</div>\n"

        + _region_card(lang, region) + "\n"

        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.capabilityTitle"))
        + "</h2>\n<p>" + escape(i18n.t(lang, "download.capability1"))
        + "</p>\n<p>" + escape(i18n.t(lang, "download.capability2")) + "</p>\n</div>\n"

        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.beforeTitle"))
        + "</h2>\n<ul>\n"
        + "".join("<li>" + escape(i18n.t(lang, f"download.before{n}")) + "</li>\n"
                  for n in (1, 2, 3))
        + "</ul>\n<p>\n"
        '<a href="/how-to-play">'
        + escape(i18n.t(lang, "download.linksRead")) + "</a> ·\n"
        '<a href="/winners">' + escape(i18n.t(lang, "download.linksWinners"))
        + "</a>\n</p>\n"
        + '<p class="seo-meta" style="font-size:0.8em;">'
        + escape(i18n.t(lang, "download.englishOnlyNote")) + "</p>\n</div>\n"

        + CARD_OPEN + "\n<h2>" + escape(i18n.t(lang, "download.troubleTitle"))
        + "</h2>\n<ul>\n"
        + "".join("<li>" + escape(i18n.t(lang, f"download.trouble{n}")) + "</li>\n"
                  for n in (1, 2, 3))
        + "</ul>\n</div>\n"
    )
    return _shell(lang, origin, "download",
                  i18n.t(lang, "download.title"),
                  i18n.t(lang, "download.description"), body, chrome)


def render_status(lang: str, origin: str, snap: dict, verdict: str,
                  stats: dict, chrome: dict | None = None) -> str:
    """The relay status page: one row per probe, same facts as the JSON twin."""
    labels = {"operational": "status.operational",
              "degraded": "status.degraded",
              "unknown": "status.unknown"}
    colours = {"operational": "var(--emerald)", "degraded": "var(--gold)",
               "unknown": "var(--text-mute)"}
    headline = i18n.t(lang, labels[verdict])

    age = snap.get("age_s")
    if age is None:
        updated = i18n.t(lang, "status.noData")
    elif age < 90:
        updated = i18n.t(lang, "status.updatedSeconds", n=age)
    else:
        updated = i18n.t(lang, "status.updatedMinutes", n=age // 60)
    if verdict == "unknown" and snap.get("available"):
        updated += " — " + i18n.t(lang, "status.stale", n=300)

    rows = []
    for name in ("pool", "tunnel", "public", "dns", "witness", "external"):
        entry = (snap.get("checks") or {}).get(name) or {}
        ok = entry.get("ok")
        dot = {True: "var(--emerald)", False: "var(--gold)"}.get(
            ok, "var(--bg-cobble-hi)")
        state = {True: i18n.t(lang, "status.ok"),
                 False: i18n.t(lang, "status.fail")}.get(
                     ok, i18n.t(lang, "status.none"))
        detail = escape(str(entry.get("detail") or "")[:160])
        rows.append(
            '<div style="display:flex;gap:10px;align-items:baseline;'
            'padding:8px 0;border-bottom:1px solid var(--bg-cobble);">'
            f'<span style="width:9px;height:9px;border-radius:50%;background:{dot};'
            'flex:0 0 auto;display:inline-block;"></span>'
            '<span style="min-width:150px;font-weight:600;">'
            + escape(i18n.t(lang, "status.probe." + name)) + "</span>"
            f'<span style="color:var(--text-mute);font-size:0.85em;">{state}'
            + (f" · {detail}" if detail else "") + "</span></div>")

    miners = (stats or {}).get("real_miners", (stats or {}).get("active_miners", 0))
    try:
        hashrate = f"{float((stats or {}).get('total_hashrate') or 0):,.0f} H/s"
    except (TypeError, ValueError):
        hashrate = "—"

    body = (
        "<h1>" + escape(i18n.t(lang, "status.h1")) + "</h1>\n"
        '<p style="color:var(--text-mute);font-size:0.9em;margin:0 0 20px;">'
        '<code>stratum+tcp://mine.xmrjackpot.com:3333</code> · '
        + escape(updated) + "</p>\n"
        + CARD_OPEN + '\n<div class="card-header" style="color:'
        + colours[verdict] + ';">' + escape(headline) + "</div>\n"
        + "".join(rows) + "\n</div>\n"
        '<div class="grid-3" style="margin-bottom:18px;">\n'
        '  <div class="stat-card"><div class="stat-value gold">' + escape(str(miners or 0))
        + '</div><div class="stat-label">' + escape(i18n.t(lang, "status.miners"))
        + "</div></div>\n"
        '  <div class="stat-card"><div class="stat-value diamond">' + escape(hashrate)
        + '</div><div class="stat-label">' + escape(i18n.t(lang, "status.hashrate"))
        + "</div></div>\n"
        '  <div class="stat-card"><div class="stat-value">' + escape(verdict.upper())
        + '</div><div class="stat-label">' + escape(i18n.t(lang, "status.verdictLabel"))
        + "</div></div>\n</div>\n"
        '<p style="font-size:0.9em;">\n'
        '<a href="' + i18n.page_path(lang, "download") + '">'
        + escape(i18n.t(lang, "status.downloadLink")) + "</a> ·\n"
        '<a href="/api/relay-status">' + escape(i18n.t(lang, "status.jsonLink"))
        + "</a> ·\n"
        '<a href="/">' + escape(i18n.t(lang, "common.home")) + "</a>\n</p>\n"
        '<p style="color:var(--text-mute);font-size:0.8em;">'
        + escape(i18n.t(lang, "status.footnote")) + "</p>\n"
    )
    return _shell(lang, origin, "status",
                  i18n.t(lang, "status.title"),
                  i18n.t(lang, "status.h1"), body, chrome, robots="noindex")
