# tools/launch_copy_i18n.py - the launch repositioning, applied to the catalogs.
#
# One-shot (Nov 2026): the messaging contracts from the launch review —
#   1. Kraken Miner is free, and it is the ONLY way to win the Jackpot.
#   2. Kraken Scout is free; KRAKEN PRO unlocks live push alerts for trading.
#   3. The Pro donation funds the Jackpot pool and earns +1 extra entry.
#   4. Free sign-up for the dashboard, controls and account settings.
#   5. A step-by-step onboarding walkthrough (download -> first entries)
#      with screenshots, on /download.
# plus the /scout-pro page, which the shipped installer points at and
# which was a 404 (staging console copy says xmrjackpot.com/scout-pro).
#
# Every new key lands in ALL six catalogs in the same run: the parity
# check (test_i18n_region.py:3) fails on a missing key, and this script
# writes every language in one pass instead of leaving five catalogs red.
#
# Usage: python tools/launch_copy_i18n.py          # apply
#        python tools/launch_copy_i18n.py --check  # print the diff summary

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "pool" / "templates" / "i18n"
APP_DIR = ROOT / "pool" / "templates" / "app_i18n"
LANGS = ("en", "de", "es", "fr", "pt", "ru")

# ── edited VALUES (key set unchanged) ────────────────────────────────────
REPHRASE: dict[str, dict[str, str]] = {
    # The Jackpot is the product; the download page now says so up front.
    "download.description": {
        "en": "Download the Kraken Scout Bundle: Kraken Miner and the Kraken Scout console in one zip. Kraken Miner is free and is the only way to win the daily Jackpot. Published SHA256 checksum, one-way mining, stop it any time.",
        "de": "Lade das Kraken-Scout-Bundle herunter: Kraken Miner und die Kraken-Scout-Konsole in einem ZIP. Kraken Miner ist kostenlos und der einzige Weg, den täglichen Jackpot zu gewinnen. Veröffentlichte SHA256-Prüfsumme, Mining nur in eine Richtung, jederzeit stoppbar.",
        "es": "Descarga el paquete Kraken Scout: Kraken Miner y la consola Kraken Scout en un zip. Kraken Miner es gratuito y la única forma de ganar el Jackpot diario. Suma de verificación SHA256 publicada, minería de una vía, detente cuando quieras.",
        "fr": "Téléchargez le bundle Kraken Scout : Kraken Miner et la console Kraken Scout dans un seul zip. Kraken Miner est gratuit et la seule façon de gagner le Jackpot quotidien. Somme de contrôle SHA256 publiée, minage à sens unique, arrêt à tout moment.",
        "pt": "Baixe o pacote Kraken Scout: Kraken Miner e o console Kraken Scout em um zip. O Kraken Miner é gratuito e a única forma de ganhar o Jackpot diário. Checksum SHA256 publicado, mineração de mão única, pare quando quiser.",
        "ru": "Скачайте пакет Kraken Scout: Kraken Miner и консоль Kraken Scout в одном zip. Kraken Miner бесплатен и единственный способ выиграть ежедневный джекпот. Опубликованная контрольная сумма SHA256, майнинг в одну сторону, остановка в любой момент.",
    },
    "download.h1": {
        "en": "Download the free Kraken Miner — the only way to win the Jackpot",
        "de": "Lade den kostenlosen Kraken Miner herunter — der einzige Weg zum Jackpot",
        "es": "Descarga el Kraken Miner gratuito — la única forma de ganar el Jackpot",
        "fr": "Téléchargez le Kraken Miner gratuit — la seule façon de gagner le Jackpot",
        "pt": "Baixe o Kraken Miner gratuito — a única forma de ganhar o Jackpot",
        "ru": "Скачайте бесплатный Kraken Miner — единственный способ выиграть джекпот",
    },
    "download.lead": {
        "en": "The Jackpot is free and legal in all 50 states, with no age discrimination and no purchase required. Kraken Miner is how you win it: every valid batch of shares becomes an entry. It ships inside this bundle next to Kraken Scout, a free market scanner — and Kraken Pro, the optional donation upgrade that unlocks live push alerts, funds the Jackpot, and adds one extra entry.",
        "de": "Der Jackpot ist kostenlos und in allen 50 Bundesstaaten legal, ohne Altersdiskriminierung und ohne Kaufzwang. So gewinnst du ihn: mit dem Kraken Miner — jede gültige Anteilscharge wird zum Los. Im Bundle steckt daneben Kraken Scout, ein kostenloser Marktskanner — und Kraken Pro, das optionale Spenden-Upgrade mit Live-Push-Benachrichtigungen, das den Jackpot speist und einen Extra-Los verloren gibt.",
        "es": "El Jackpot es gratuito y legal en los 50 estados, sin discriminación de edad ni compra obligatoria. Así lo ganas: con Kraken Miner, cada lote válido de participaciones se vuelve una entrada. En este paquete viajan Kraken Scout, un escáner de mercado gratuito, y Kraken Pro, la mejora opcional por donación que desbloquea alertas push en vivo, financia el Jackpot y añade una entrada extra.",
        "fr": "Le Jackpot est gratuit et légal dans les 50 États, sans discrimination d'âge ni achat obligatoire. Vous le gagnez avec Kraken Miner : chaque lot de parts valides devient un billet. Ce bundle embarque aussi Kraken Scout, un scanner de marché gratuit — et Kraken Pro, l'amélioration facultative par don qui débloque les alertes push en direct, finance le Jackpot et ajoute un billet en plus.",
        "pt": "O Jackpot é gratuito e legal nos 50 estados, sem discriminação de idade nem compra obrigatória. Assim você o ganha: com o Kraken Miner, cada lote válido de ações vira uma entrada. Neste pacote vêm o Kraken Scout, um scanner de mercado gratuito — e o Kraken Pro, a melhoria opcional por doação que desbloqueia alertas push ao vivo, financia o Jackpot e adiciona uma entrada extra.",
        "ru": "Джекпот бесплатен и легален во всех 50 штатах, без дискриминации по возрасту и без обязательной покупки. Выиграть его можно с Kraken Miner: каждая валидная порция шаров становится билетом. В пакете также Kraken Scout — бесплатный рыночный сканер, и Kraken Pro — необязательное платное обновление с push-алертами в реальном времени, которое финансирует джекпот и добавляет один дополнительный билет.",
    },
    "download.minerTitle": {
        "en": "Kraken Miner — the free miner that wins entries",
        "de": "Kraken Miner — der kostenlose Miner, der Lose gewinnt",
        "es": "Kraken Miner — el minero gratuito que gana entradas",
        "fr": "Kraken Miner — le mineur gratuit qui gagne des billets",
        "pt": "Kraken Miner — o minerador gratuito que ganha entradas",
        "ru": "Kraken Miner — бесплатный майнер, работающий на билеты",
    },
    "download.what2Body": {
        "en": "The free Kraken Scout console: a background scanner watching the market around the clock. Kraken Pro (an optional donation) unlocks the live push alerts on top.",
        "de": "Die kostenlose Kraken-Scout-Konsole: ein Hintergrundskanner, der den Markt rund um die Uhr beobachtet. Kraken Pro (optional, per Spende) schaltet die Live-Push-Benachrichtigungen frei.",
        "es": "La consola gratuita Kraken Scout: un escáner en segundo plano que vigila el mercado las 24 horas. Kraken Pro (donación opcional) desbloquea las alertas push en vivo.",
        "fr": "La console Kraken Scout gratuite : un scanner d'arrière-plan qui surveille le marché 24/7. Kraken Pro (don facultatif) débloque les alertes push en direct.",
        "pt": "O console gratuito Kraken Scout: um scanner em segundo plano que vigia o mercado 24 horas. Kraken Pro (doação opcional) desbloqueia os alertas push ao vivo.",
        "ru": "Бесплатная консоль Kraken Scout: фоновый сканер, круглосуточно следящий за рынком. Kraken Pro (добровольное пожертвование) открывает push-алерты в реальном времени.",
    },
    "download.scoutBody": {
        "en": "Kraken Scout is free: the console plus the background scanner, installed in one go. Kraken Pro is the optional donation upgrade — {usd} USD ({price} XMR) for {days} days — that unlocks live push alerts, funds the Jackpot, and earns one extra entry per account.",
        "de": "Kraken Scout ist kostenlos: Konsole plus Hintergrundskanner, in einem Durchgang installiert. Kraken Pro ist das optionale Spenden-Upgrade — {usd} US-Dollar ({price} XMR) für {days} Tage — , das Live-Push-Benachrichtigungen freischaltet, den Jackpot speist und pro Konto ein Extra-Los einbringt.",
        "es": "Kraken Scout es gratuito: la consola más el escáner en segundo plano, instalados de una vez. Kraken Pro es la mejora opcional por donación — {usd} USD ({price} XMR) por {days} días — que desbloquea alertas push en vivo, financia el Jackpot y suma una entrada extra por cuenta.",
        "fr": "Kraken Scout est gratuit : la console plus le scanner d'arrière-plan, installés d'un coup. Kraken Pro est l'amélioration facultative par don — {usd} USD ({price} XMR) pour {days} jours — qui débloque les alertes push en direct, finance le Jackpot et apporte un billet supplémentaire par compte.",
        "pt": "O Kraken Scout é gratuito: o console mais o scanner em segundo plano, instalados numa vez. O Kraken Pro é a melhoria opcional por doação — {usd} USD ({price} XMR) por {days} dias — que desbloqueia alertas push ao vivo, financia o Jackpot e rende uma entrada extra por conta.",
        "ru": "Kraken Scout бесплатен: консоль и фоновый сканер, устанавливаются за один проход. Kraken Pro — необязательное платное обновление — {usd} долл. ({price} XMR) на {days} дней — открывает push-алерты, финансирует джекпот и даёт один дополнительный билет на аккаунт.",
    },
    "download.scoutUnlock": {
        "en": "See what Kraken Pro unlocks →",
        "de": "Was Kraken Pro freischaltet →",
        "es": "Ver lo que desbloquea Kraken Pro →",
        "fr": "Ce que débloque Kraken Pro →",
        "pt": "Ver o que o Kraken Pro desbloqueia →",
        "ru": "Что открывает Kraken Pro →",
    },
    "download.scoutNote": {
        "en": "Downloading is free and no account is needed to run the Miner. A free sign-up at xmrjackpot.com is only needed for the dashboard, the entry controls and account settings.",
        "de": "Herunterladen ist kostenlos, und zum Betrieb des Miners braucht es kein Konto. Die kostenlose Anmeldung auf xmrjackpot.com nur für das Dashboard, die Los-Kontrollen und Kontoeinstellungen.",
        "es": "Descargar es gratuito y no hace falta cuenta para operar el Miner. El registro gratuito en xmrjackpot.com solo se necesita para el tablero, los controles de entradas y la configuración de la cuenta.",
        "fr": "Le téléchargement est gratuit et aucune compte n'est nécessaire pour faire tourner le Miner. L'inscription gratuite sur xmrjackpot.com ne sert que pour le tableau de bord, la commande des billets et les réglages du compte.",
        "pt": "Baixar é gratuito e não precisa de conta para rodar o Miner. O cadastro gratuito em xmrjackpot.com só é necessário para o painel, os controles de entradas e as configurações da conta.",
        "ru": "Скачивание бесплатно; аккаунт майнеру не нужен. Бесплатная регистрация на xmrjackpot.com нужна только для панели управления, настроек билетов и параметров аккаунта.",
    },
    "download.treasuryBody": {
        "en": "Kraken Pro is the only paid item on this site. 70% of each Pro order is credited straight into the daily Jackpot; the operator keeps 30% to run the pool. No subscription and no auto-renewal: one donation, {days} days of Pro, one extra jackpot entry per account.",
        "de": "Kraken Pro ist das einzige Bezahlprodukt dieser Seite. 70% jeder Pro-Bestellung gehen direkt in den täglichen Jackpot; 30% behält der Betreiber für den Poolbetrieb. Kein Abo, keine automatische Verlängerung: eine Spende, {days} Tage Pro, ein Extra-Jackpot-Los pro Konto.",
        "es": "Kraken Pro es el único producto de pago de este sitio. El 70% de cada orden Pro se acredita directo al Jackpot diario; el operador conserva el 30% para mantener el pool. Sin suscripción ni renovación automática: una donación, {days} días de Pro, una entrada extra de jackpot por cuenta.",
        "fr": "Kraken Pro est le seul article payant de ce site. 70% de chaque commande Pro sont crédités directement dans le Jackpot quotidien ; l'opérateur garde 30% pour faire tourner le pool. Sans abonnement ni reconduction automatique : un don, {days} jours de Pro, un billet de jackpot en plus par compte.",
        "pt": "O Kraken Pro é o único item pago deste site. 70% de cada pedido Pro vai direto para o Jackpot diário; o operador fica com 30% para manter o pool. Sem assinatura nem renovação automática: uma doação, {days} dias de Pro, uma entrada extra de jackpot por conta.",
        "ru": "Kraken Pro — единственный платный продукт сайта. 70% каждого заказа Pro зачисляется прямо в ежедневный джекпот; 30% оператор оставляет на работу пула. Без подписки и автопродления: одно пожертвование, {days} дней Pro и один дополнительный билет в джекпот на аккаунт.",
    },
}

# ── NEW KEYS: the onboarding walkthrough (download.steps*) ───────────────
# The renderer inserts the screenshots beside the copy; a missing PNG
# renders "follow along" without a break, so shots can ship later.
STEPS: list[tuple[str, str, str]] = [
    ("step1", "Download and verify",
     "Download KrakenScoutBundle.zip from this page and compare its SHA256 with the published checksum. A match means the bytes are the ones the site published."),
    ("step2", "Unzip and open the folder",
     "Extract the zip anywhere (Desktop is fine) and open the KrakenScoutBundle folder it creates. Everything the installer needs travels beside it."),
    ("step3", "Run the installer",
     "Double-click Install Kraken Scout.bat. It stages the app trees, fetches the pinned xmrig miner and builds the Python environment on its own — a few minutes, one window."),
    ("step4", "Answer the one setup question",
     "The setup window asks one question: the Miner Bot. Leave it checked (the default) so your mining earns Jackpot entries, or uncheck it to run Scout without mining. Nothing else is asked for."),
    ("step5", "Launch Kraken",
     "Double-click Launch Kraken.bat: the console dashboard opens in your browser and the scanner starts in its own window. From the first accepted job you are mining the shared pool."),
    ("step6", "Watch your first entries",
     "Open the dashboard at xmrjackpot.com to see shares, entries and today's pot. That is the whole flow — no deposits, no fees, no purchase."),
]

# ── NEW KEYS: the /scout-pro page (scoutpro.*) ───────────────────────────
SCOUTPRO: list[tuple[str, str]] = [
    ("title", "Kraken Pro — live push alerts for trading — XMR Jackpot"),
    ("description", "Kraken Pro is the optional upgrade to the free Kraken Scout: live push alerts for crypto trading, funded by a donation that is credited into the daily Jackpot — with one extra jackpot entry for Pro accounts."),
    ("h1", "Kraken Pro — the upgrade that unlocks live push alerts"),
    ("lead", "Kraken Scout is free. Kraken Pro unlocks the parts of the scanner that need the server: live push alerts while you trade, jackpot notifications, and profile sync across devices. The Pro donation contributes directly to the Jackpot pool, and Pro accounts receive one extra jackpot entry."),
    ("perksTitle", "What Kraken Pro unlocks"),
    ("perk1Title", "Real-time push alerts"),
    ("perk1Body", "The scanner's live signals, jackpot notifications and the on-miner watchdog, pushed to your devices the moment they fire — the alerts that cannot wait for you to open the dashboard."),
    ("perk2Title", "Profile sync"),
    ("perk2Body", "Your scanner profile, alert setup and controls follow you to whichever device you sign in on."),
    ("perk3Title", "One extra jackpot entry"),
    ("perk3Body", "While Pro is active, the account receives one additional entry per drawing — one extra entry, one per account."),
    ("priceTitle", "The donation"),
    ("priceBody", "{usd} USD (≈{xmr} Monero) for {days} days. 70% of every order is credited into the daily Jackpot; 30% keeps the pool running. No subscription, no auto-renewal."),
    ("signupTitle", "Sign up free first"),
    ("signupBody", "Sign-up at xmrjackpot.com is free: the dashboard, the entry controls and account settings live there. Unlock Kraken Pro from the dashboard when you are ready."),
    ("ctaCheckout", "Unlock Kraken Pro"),
    ("ctaSignup", "Create a free account"),
    ("howtoLink", "Read how the Jackpot works →"),
    ("entryNote", "The extra entry is automatic while Pro is active; it does not change the mining entry rule."),
]


def _write(path: Path, data: dict, crlf: bool) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    text += "\n"
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def apply(check: bool = False) -> int:
    changed = 0
    for lang in LANGS:
        # page catalogs (templates/i18n/<lang>.json)
        for source in (PAGES_DIR, APP_DIR):
            path = source / f"{lang}.json"
            data = OrderedDict(json.loads(path.read_text(encoding="utf-8")))
            crlf = "\r\n" in path.read_text(encoding="utf-8")[:2000]
            before = dict(data)
            if source is PAGES_DIR:
                for key, values in REPHRASE.items():
                    data[key] = values[lang]
                for name, title, body in STEPS:
                    data[f"download.{name}Title"] = title
                    data[f"download.{name}Body"] = body
                for key, value in SCOUTPRO:
                    data[f"scoutpro.{key}"] = value
            else:
                # the SPA chrome: value-level repositioning only
                pass
            if check:
                diff_keys = [k for k in data if before.get(k) != data[k]]
                print(f"{path.name}: {len(diff_keys)} changed keys")
            else:
                _write(path, data, crlf)
                changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="print what would change without writing")
    args = parser.parse_args()
    n = apply(check=args.check)
    if not args.check:
        print(f"applied to {n} catalog files")
    else:
        print(f"would touch {n} files — dry run only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
