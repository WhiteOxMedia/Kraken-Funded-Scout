"""i18n.py — language negotiation and region inference for the miner pages.

Two separate jobs, deliberately kept apart:

LANGUAGE. The pages a miner needs in order to start (download, status) exist in
six languages. A visitor gets the language their browser asks for; an explicit
choice in the selector wins over the header forever after, because a miner who
picked English on a Spanish phone does not want to be bounced back on every
click. Everything unsupported falls back to English — never to a guess.

REGION. The site sits behind Cloudflare, so every request already carries the
visitor's country (`CF-IPCountry`) and the edge that served it (`CF-Ray`, whose
suffix is a three-letter colo like SLC or FRA). That is enough to say "you are
in Europe and the relay is in Los Angeles" without buying a GeoIP database,
without a JavaScript latency probe, and without storing anything about the
visitor.

What region inference is NOT allowed to do: claim that mining is blocked,
banned or illegal somewhere. No jurisdiction bans Monero mining and no public
source lists "countries where XMR is blocked", so the page gives latency advice
and nothing else. A guess about someone's legal situation, rendered on a public
page, is worse than no answer.

One relay exists (Los Angeles). The region map therefore answers "how far are
you from the only endpoint there is", and is written so that adding a second
relay means adding a row here, not rewriting the pages.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Ordered for the selector: English first, then the biggest Monero-mining
# language communities we can serve honestly.
LANGUAGES = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "ru": "Русский",
    "de": "Deutsch",
    "fr": "Français",
}
DEFAULT_LANG = "en"
LANG_COOKIE = "xmrj_lang"
CATALOG_DIR = Path(__file__).resolve().parent / "templates" / "i18n"
APP_CATALOG_DIR = Path(__file__).resolve().parent / "templates" / "app_i18n"


# ─── Language negotiation ─────────────────────────────────────────────

def _tags(header: str) -> list[tuple[float, int, str]]:
    """Accept-Language -> [(q, order, tag)], best first.

    q-values are honoured because a browser that says `es;q=0.9, en;q=1.0`
    means English. Tags are matched on their base (`pt-BR` -> `pt`), which is
    what a six-language site can honestly support; a region-specific variant
    we do not translate (pt-PT vs pt-BR) still gets its language.
    """
    out: list[tuple[float, int, str]] = []
    for i, part in enumerate((header or "").split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        tag = tag.strip().lower()
        if not tag or tag == "*":
            continue
        q = 1.0
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        out.append((q, i, tag))
    out.sort(key=lambda row: (-row[0], row[1]))
    return out


def negotiate(header: str | None, cookie: str | None = None) -> str:
    """The language to serve: an explicit choice, else the header, else English."""
    if cookie in LANGUAGES:
        return cookie
    for _q, _order, tag in _tags(header or ""):
        base = tag.split("-")[0]
        if base in LANGUAGES:
            return base
    return DEFAULT_LANG


def page_path(lang: str, page: str) -> str:
    """Canonical URL for one page in one language (English is unprefixed)."""
    return f"/{page}" if lang == DEFAULT_LANG else f"/{lang}/{page}"


# ─── Region inference ─────────────────────────────────────────────────

REGION_NAMES = ("north-america", "latin-america", "europe",
                "africa", "asia", "oceania", "global")

_EUROPE = set("""AL AD AT BY BE BA BG HR CY CZ DK EE FI FR DE GR HU IS IE IT
XK LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SE CH TR UA GB
VA""".split())
_ASIA = set("""AF AM AZ BH BD BT BN KH CN GE HK IN ID IR IQ IL JP JO KZ KP KR
KW KG LA LB MO MY MV MN MM NP OM PK PS PH QA SA SG LK SY TW TJ TH TL TM AE UZ
VN YE""".split())
_AFRICA = set("""DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA
GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS
SD TZ TG TN UG ZM ZW""".split())
_LATAM = set("""AR BO BR CL CO CR CU DO EC SV GT HT HN JM MX NI PA PY PE PR BZ
TT UY VE""".split())
_OCEANIA = set("""AU FJ PF GU KI MH FM NR NC NZ PW PG WS SB TO TV VU""".split())
_NORTH_AMERICA = {"US", "CA", "GL", "BM", "PM"}

_COUNTRY_REGION = {}
for _cc in _EUROPE:
    _COUNTRY_REGION[_cc] = "europe"
for _cc in _ASIA:
    _COUNTRY_REGION[_cc] = "asia"
for _cc in _AFRICA:
    _COUNTRY_REGION[_cc] = "africa"
for _cc in _LATAM:
    _COUNTRY_REGION[_cc] = "latin-america"
for _cc in _OCEANIA:
    _COUNTRY_REGION[_cc] = "oceania"
for _cc in _NORTH_AMERICA:
    _COUNTRY_REGION[_cc] = "north-america"

# The one relay, and the region it sits in. A second relay turns this into a
# list and the advice becomes "nearest of these" without changing the pages.
RELAY_CITY = "Los Angeles"
RELAY_REGION = "north-america"


def region_of(country: str | None, colo: str | None = None) -> dict:
    """Country code -> {country, region, colo, near}. Unknown stays unknown."""
    cc = (country or "").strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        cc = ""
    region = _COUNTRY_REGION.get(cc, "global")
    return {
        "country": cc,
        "region": region,
        "colo": (colo or "").strip().upper()[:3],
        # "same region as the relay" is a latency statement, not a legal one.
        "near": region == RELAY_REGION,
    }


def region_from_headers(headers) -> dict:
    """Read the two headers Cloudflare attaches; nothing else is consulted.

    `CF-Ray` looks like `a4fbd5d5781e8a29-SLC`; the suffix is the edge. It is
    used only to make the advice specific when a country is missing (Tor, a
    proxy, a header stripped by a local dev run).
    """
    country = headers.get("cf-ipcountry")
    ray = headers.get("cf-ray") or ""
    colo = ray.rsplit("-", 1)[-1] if "-" in ray else ""
    return region_of(country, colo)


# ─── Catalog ──────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def catalog(lang: str) -> dict:
    """One language's strings. A broken or missing file is not fatal."""
    path = CATALOG_DIR / f"{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=None)
def app_catalog(lang: str) -> dict:
    """The homepage/dashboard UI catalog; unsupported languages use English."""
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG
    path = APP_CATALOG_DIR / f"{lang}.json"
    try:
        strings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        strings = {}
    return strings if isinstance(strings, dict) else {}


@lru_cache(maxsize=None)
def _english() -> dict:
    return catalog(DEFAULT_LANG)


def t(lang: str, key: str, **fmt) -> str:
    """Translate one key, falling back to English, then to the key itself.

    `{name}` placeholders are filled with `fmt`. A caller that forgets an
    argument gets the unformatted string rather than a 500: a page with a
    visible placeholder is a bug report, an exception is an outage.
    """
    text = catalog(lang).get(key) or _english().get(key) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def app_t(lang: str, key: str, **fmt) -> str:
    """Translate app UI strings, falling back to the English app catalog."""
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG
    text = app_catalog(lang).get(key) or app_catalog(DEFAULT_LANG).get(key) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def missing_keys(lang: str) -> list[str]:
    """Keys English has that this language does not — used by the parity test."""
    return sorted(set(_english()) - set(catalog(lang)))
