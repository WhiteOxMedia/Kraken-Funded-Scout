"""KRAKEN SCOUT — TRADING CONSOLE.

One merged dashboard (Scout + Bot) styled as a vintage studio mixing console:
walnut frame, brushed-metal faceplates, rotary dials, VU meters, LED readouts.

Modules:
  MASTER      — account dial, position channel strips, bot/scanner status
  TOURNAMENT  — the 54-asset elimination bracket + champion
  QUICK CHECK — single-asset deep scan using the CURRENT scanner logic
                (adaptive thresholds, momentum ceiling, liquidity filter)
  EXIT CHECK  — the scanner's exit logic on demand: trailing stop first,
                momentum 60% bar, exhaustion, DCA-tranche detection
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config import TradingConfig
from indicators import bollinger_bands, ema, rsi
from scanner import ScanResult, compute_position_sizing, estimate_timeframe, tournament_scan

try:
    import db
    from secrets_utils import setup_secrets
    import keyring
    from kraken_api import KrakenAPI
    _HAS_DB = True
except Exception:
    _HAS_DB = False

MST = ZoneInfo("America/Denver")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Kraken Scout Console", page_icon="🎛️", layout="wide")

ASSETS = [
    "BTC","ETH","LINK","BCH","DOGE","ARB","AAVE","AVAX","BNB","CRV",
    "DOT","FIL","GRASS","ADA","HBAR","INJ","KAITO","LDO","LTC","MOODENG",
    "NEAR","PNUT","SOL","S","TRUMP","UNI","WLD","XRP","ALGO","APT",
    "ATOM","ETC","JTO","JUP","ONDO","OP","POL","POPCAT","RENDER","STX",
    "SUI","TAO","TIA","TRX","WIF","AIXBT","ASTER","FARTCOIN","HYPE",
    "PENGU","PUMP","VIRTUAL","XPL","ZEC",
]

BOT_DIR = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot"

# ════════════════════════════════════════════════════════════════════════
#  CONSOLE CSS — walnut frame, faceplates, knobs, VU meters, LCD
# ════════════════════════════════════════════════════════════════════════

_WOOD = """
repeating-linear-gradient(92deg, rgba(0,0,0,0.13) 0 2px, rgba(0,0,0,0) 2px 7px),
repeating-linear-gradient(88deg, rgba(255,235,200,0.028) 0 1px, rgba(0,0,0,0) 1px 9px),
repeating-linear-gradient(90.5deg, rgba(0,0,0,0.09) 0 3px, rgba(0,0,0,0) 3px 23px),
linear-gradient(165deg, #6b4a2f 0%, #4a3220 28%, #5d4028 55%, #3a2716 80%, #2c1d10 100%)
"""

_NOISE = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E"
          "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E"
          "%3C/filter%3E%3Crect width='120' height='120' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Audiowide&family=Share+Tech+Mono&display=swap');

/* ── walnut console frame ─────────────────────────────────────────── */
.block-container {{ padding: 0.6rem 1.1rem 3rem; max-width: 1500px; }}
.stApp {{
  background: {_WOOD}, url("{_NOISE}") !important;
  background-blend-mode: normal, normal, normal, overlay !important;
}}
#MainMenu, footer, header {{ visibility: hidden; }}

h1, h2, h3 {{ font-family: 'Audiowide', sans-serif !important; color: #f0e2c8 !important;
  text-shadow: 0 2px 4px rgba(0,0,0,.6); }}

/* ── faceplate panels ─────────────────────────────────────────────── */
.faceplate {{
  position: relative;
  background:
    repeating-linear-gradient(0deg, rgba(255,255,255,0.016) 0 1px, rgba(0,0,0,0) 1px 3px),
    linear-gradient(180deg, #2b2b30 0%, #1d1d21 55%, #16161a 100%);
  border: 2px solid #0a0a0c;
  border-radius: 16px;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.10),
    inset 0 -3px 6px rgba(0,0,0,0.55),
    0 8px 20px rgba(0,0,0,0.55);
  padding: 16px 18px;
  margin: 10px 0;
  color: #d8ccb4;
}}
.faceplate::before, .faceplate::after,
.faceplate .screw-b::before, .faceplate .screw-b::after {{
  content: ''; position: absolute; width: 11px; height: 11px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #8a8a92, #3c3c42 55%, #131316);
  box-shadow: inset 0 1px 1px rgba(255,255,255,.35), 0 1px 2px rgba(0,0,0,.8);
}}
.faceplate::before {{ top: 7px; left: 7px; }}
.faceplate::after  {{ top: 7px; right: 7px; }}
.faceplate .screw-b::before {{ bottom: 7px; left: 7px; top: auto; }}
.faceplate .screw-b::after  {{ bottom: 7px; right: 7px; top: auto; }}

.plate-label {{
  font-family: 'Audiowide', sans-serif; font-size: 11px; letter-spacing: 3px;
  color: #b9a06d; text-transform: uppercase; margin-bottom: 10px;
  text-shadow: 0 1px 0 #000;
  border-bottom: 1px solid #3a352c; padding-bottom: 6px;
}}

/* ── LCD readouts ─────────────────────────────────────────────────── */
.lcd {{
  background: linear-gradient(180deg, #0c130a, #070d06);
  border: 2px solid #000; border-radius: 8px;
  box-shadow: inset 0 2px 8px rgba(0,0,0,.9), 0 1px 0 rgba(255,255,255,.06);
  color: #8affa0; font-family: 'Share Tech Mono', monospace;
  padding: 10px 14px; text-shadow: 0 0 9px rgba(120,255,150,.45);
  font-size: 15px; line-height: 1.55; overflow-x: auto;
}}
.lcd.amber {{ color: #ffc36b; text-shadow: 0 0 9px rgba(255,180,80,.5); }}
.lcd.red   {{ color: #ff7a6b; text-shadow: 0 0 9px rgba(255,90,70,.5); }}
.lcd.green {{ color: #4be38a; text-shadow: 0 0 9px rgba(53,224,106,.5); }}
.lcd .dim {{ color: #4e6b52; text-shadow: none; }}
.lcd.amber .dim {{ color: #6e5a3a; }}

/* ── LEDs ─────────────────────────────────────────────────────────── */
.led {{ display: inline-flex; align-items: center; gap: 6px; margin: 2px 10px 2px 0;
  font-family: 'Share Tech Mono', monospace; font-size: 12.5px; color: #cbbfa6; }}
.led i {{ width: 11px; height: 11px; border-radius: 50%; display: inline-block;
  background: #232327; box-shadow: inset 0 1px 2px rgba(0,0,0,.8); }}
.led.g i {{ background: #35e06a; box-shadow: 0 0 8px #35e06a, inset 0 1px 1px rgba(255,255,255,.4); }}
.led.a i {{ background: #ffb347; box-shadow: 0 0 8px #ffb347, inset 0 1px 1px rgba(255,255,255,.4); }}
.led.r i {{ background: #ff4040; box-shadow: 0 0 8px #ff4040, inset 0 1px 1px rgba(255,255,255,.4); }}
.led.off i {{ background: #2c2c30; }}

/* ── Dopamine: big score + celebration ────────────────────────────── */
@keyframes winpulse {{
  0%, 100% {{ text-shadow: 0 0 6px rgba(53,224,106,.55), 0 2px 4px rgba(0,0,0,.8); }}
  50%      {{ text-shadow: 0 0 22px rgba(53,224,106,.95), 0 0 40px rgba(53,224,106,.45); }}
}}
@keyframes losspulse {{
  0%, 100% {{ text-shadow: 0 0 6px rgba(255,90,70,.5), 0 2px 4px rgba(0,0,0,.8); }}
  50%      {{ text-shadow: 0 0 18px rgba(255,90,70,.9); }}
}}
@keyframes popin {{
  0%   {{ transform: scale(.85); opacity: 0; }}
  60%  {{ transform: scale(1.04); opacity: 1; }}
  100% {{ transform: scale(1); opacity: 1; }}
}}
@keyframes confetti-rise {{
  0%   {{ transform: translateY(0) rotate(0deg); opacity: 1; }}
  100% {{ transform: translateY(-46px) rotate(220deg); opacity: 0; }}
}}
.score-big {{ font-family: 'Audiowide', sans-serif; font-size: 42px; letter-spacing: 1px;
  animation: popin .5s ease-out; }}
.score-big.win  {{ color: #35e06a; animation: popin .5s ease-out, winpulse 2.2s ease-in-out infinite; }}
.score-big.loss {{ color: #ff7a6b; animation: popin .5s ease-out, losspulse 2.6s ease-in-out infinite; }}
.score-big.flat {{ color: #ffc36b; }}
.score-sub {{ font-family: 'Share Tech Mono', monospace; font-size: 13px; color: #b9a06d; }}
.score-stat {{ font-family: 'Share Tech Mono', monospace; font-size: 13px; color: #cbbfa6;
  padding: 6px 14px; text-align: center; }}
.score-stat b {{ display: block; font-family: 'Audiowide', sans-serif; font-size: 17px;
  color: #f0e2c8; margin-bottom: 1px; }}
.celebrate {{ position: relative; overflow: hidden; padding: 12px 18px; text-align: center;
  font-family: 'Audiowide', sans-serif; font-size: 19px; color: #35e06a;
  background: linear-gradient(90deg, rgba(53,224,106,.10), rgba(53,224,106,.22), rgba(53,224,106,.10));
  border: 1px solid rgba(53,224,106,.45); border-radius: 8px;
  animation: popin .45s ease-out, winpulse 2.2s ease-in-out infinite; }}
.celebrate .conf {{ position: absolute; font-size: 13px; animation: confetti-rise 1.6s ease-out forwards; }}
.win-pill, .loss-pill {{ display: inline-block; font-family: 'Share Tech Mono', monospace;
  font-size: 13px; padding: 1px 8px; border-radius: 10px; margin-right: 8px; }}
.win-pill  {{ color: #35e06a; background: rgba(53,224,106,.12); border: 1px solid rgba(53,224,106,.4); }}
.loss-pill {{ color: #ff7a6b; background: rgba(255,90,70,.10); border: 1px solid rgba(255,90,70,.35); }}
.feed-row {{ font-family: 'Share Tech Mono', monospace; font-size: 13.5px; color: #d8ccb4;
  padding: 4px 0; border-bottom: 1px dashed rgba(138,122,85,.25); animation: popin .4s ease-out; }}
.now-strip {{ font-family: 'Share Tech Mono', monospace; font-size: 14px; color: #e8d9b8;
  padding: 8px 14px; }}

/* ── rotary knobs ─────────────────────────────────────────────────── */
.knob-row {{ display: flex; gap: 18px; flex-wrap: wrap; justify-content: center; }}
.knob-wrap {{ text-align: center; width: 92px; }}
.knob {{
  width: 68px; height: 68px; margin: 0 auto; border-radius: 50%; position: relative;
  background:
    radial-gradient(circle at 32% 28%, #55555e, #232327 42%, #101013 78%, #060608);
  box-shadow: inset 0 2px 3px rgba(255,255,255,.22), inset 0 -4px 7px rgba(0,0,0,.8),
    0 5px 10px rgba(0,0,0,.65);
  border: 2px solid #08080a;
}}
.knob::before {{ /* tick arc */
  content: ''; position: absolute; inset: -9px; border-radius: 50%;
  background: conic-gradient(from -135deg, #8d7a55 0deg, #8d7a55 var(--arc), #33302a var(--arc), #33302a 270deg, transparent 270deg);
  -webkit-mask: radial-gradient(circle, transparent 62%, #000 63%);
          mask: radial-gradient(circle, transparent 62%, #000 63%);
}}
.knob i {{ /* pointer */
  position: absolute; left: 50%; top: 5px; width: 4px; height: 24px; margin-left: -2px;
  border-radius: 3px; background: #ffc36b; box-shadow: 0 0 7px rgba(255,180,80,.8);
  transform-origin: 50% 29px; transform: rotate(var(--deg));
}}
.knob-val {{ font-family: 'Share Tech Mono', monospace; font-size: 14px; color: #ffc36b;
  margin-top: 6px; text-shadow: 0 0 7px rgba(255,180,80,.4); }}
.knob-lab {{ font-family: 'Audiowide', sans-serif; font-size: 9px; letter-spacing: 1.5px;
  color: #a5946f; text-transform: uppercase; margin-top: 1px; }}

/* ── VU meters ────────────────────────────────────────────────────── */
.vu {{ display: flex; flex-direction: column; gap: 3px; }}
.vu .vu-title {{ font-family: 'Audiowide', sans-serif; font-size: 9.5px; letter-spacing: 2px;
  color: #a5946f; text-transform: uppercase; margin-bottom: 2px; }}
.vu .vu-bar {{ display: flex; gap: 3px; background: #101013; border: 2px solid #000;
  border-radius: 6px; padding: 5px 6px; box-shadow: inset 0 2px 6px rgba(0,0,0,.9); }}
.vu .vu-bar b {{ width: 13px; height: 22px; border-radius: 2px; background: #212125;
  box-shadow: inset 0 1px 1px rgba(0,0,0,.7); }}
.vu .vu-bar b.on {{ background: var(--c); box-shadow: 0 0 7px var(--c); }}
.vu .vu-read {{ font-family: 'Share Tech Mono', monospace; font-size: 12.5px; color: #d8ccb4; }}

/* ── channel strips (positions) ───────────────────────────────────── */
.strips {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.strip {{
  width: 148px; padding: 10px 8px; text-align: center; position: relative;
  background: linear-gradient(180deg, #26262b, #151519);
  border: 2px solid #0a0a0c; border-radius: 12px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 5px 12px rgba(0,0,0,.5);
  font-family: 'Share Tech Mono', monospace;
}}
.strip .s-name {{ font-family: 'Audiowide', sans-serif; font-size: 13px; color: #f0e2c8;
  letter-spacing: 1px; text-shadow: 0 1px 0 #000; }}
.strip .s-sub {{ font-size: 10.5px; color: #8d8271; margin: 2px 0 6px; }}
.strip .s-pnl {{ font-size: 16px; margin: 4px 0; }}
.strip .up   {{ color: #4be38a; text-shadow: 0 0 8px rgba(75,227,138,.45); }}
.strip .down {{ color: #ff6b5e; text-shadow: 0 0 8px rgba(255,90,70,.4); }}
.strip .s-fader {{ height: 10px; background: #0d0d10; border-radius: 5px; margin: 7px 4px 3px;
  box-shadow: inset 0 1px 3px #000; overflow: hidden; }}
.strip .s-filler {{ height: 100%; background: linear-gradient(90deg, #35e06a, #ffb347 70%, #ff4040);
  border-radius: 5px; }}

/* ── buttons ──────────────────────────────────────────────────────── */
.styled-button {{ text-align: center; margin: 6px 0; }}
div[data-testid="stButton"] > button {{
  font-family: 'Audiowide', sans-serif !important; letter-spacing: 1.5px;
  border-radius: 11px !important;
  background: linear-gradient(180deg, #3a3a41, #202024) !important;
  border: 2px solid #0a0a0c !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 4px 9px rgba(0,0,0,.55) !important;
  color: #d8ccb4 !important; padding: .35rem 1.1rem !important;
}}
div[data-testid="stButton"] > button:hover {{
  border-color: #ffb347 !important; color: #ffc36b !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 4px 12px rgba(255,179,71,.28) !important;
}}
div[data-testid="stButton"] > button[kind="primary"] {{
  background: linear-gradient(180deg, #c97b26, #8f5514) !important;
  color: #fff3dc !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.3), 0 4px 12px rgba(255,150,50,.4) !important;
}}
div[data-testid="stButton"] > button[kind="secondary"][data-urgent="1"],
.kill-btn button {{
  background: linear-gradient(180deg, #c93826, #7e1f12) !important;
  color: #ffe1dc !important; border-radius: 50% !important;
}}

/* ── tabs as console module buttons ───────────────────────────────── */
.stTabs {{ background: transparent; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 8px; border-bottom: 2px solid #0a0a0c;
  background: linear-gradient(180deg, rgba(20,18,14,.92), rgba(14,12,9,.92));
  padding: 8px 8px 0; border-radius: 14px 14px 0 0; }}
.stTabs [data-baseweb="tab"] {{
  font-family: 'Audiowide', sans-serif !important; font-size: 12.5px !important;
  letter-spacing: 2px; color: #8d8271 !important;
  background: linear-gradient(180deg, #2b2b30, #191920) !important;
  border: 2px solid #0a0a0c !important; border-radius: 10px 10px 0 0 !important;
  padding: 8px 20px !important; box-shadow: inset 0 1px 0 rgba(255,255,255,.09);
}}
.stTabs [aria-selected="true"] {{
  color: #ffc36b !important;
  background: linear-gradient(180deg, #4d3a1d, #33270f) !important;
  border-color: #ffb347 !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 0 14px rgba(255,179,71,.22) !important;
}}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
.stTabs [data-baseweb="tab-panel"] {{
  background: linear-gradient(180deg, rgba(24,22,18,.96), rgba(18,16,12,.96));
  border: 2px solid #0a0a0c; border-top: none; border-radius: 0 0 14px 14px;
  padding: 16px 18px; box-shadow: 0 8px 20px rgba(0,0,0,.45);
}}

/* ── misc streamlit overrides ─────────────────────────────────────── */
div[data-testid="stMetric"], div[data-testid="stMetricContainer"] {{
  background: linear-gradient(180deg, #232328, #16161a) !important;
  border: 1.5px solid #0a0a0c; border-radius: 10px; padding: 8px 12px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.07);
}}
div[data-testid="stMetricValue"] {{ font-family: 'Share Tech Mono', monospace !important;
  color: #ffc36b !important; text-shadow: 0 0 8px rgba(255,180,80,.3); }}
div[data-testid="stMetricLabel"] {{ font-family: 'Audiowide', sans-serif !important;
  font-size: 10px !important; letter-spacing: 1.5px; color: #a5946f !important; }}
div[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, #241c14, #17110b) !important;
  border-right: 3px solid #0d0a06;
}}
div[data-testid="stExpander"] {{ background: rgba(20,18,14,.5) !important;
  border: 1px solid #3a352c !important; border-radius: 10px !important; }}
.stSelectbox label, .stSlider label, .stMultiSelect label {{
  font-family: 'Audiowide', sans-serif !important; font-size: 10.5px !important;
  letter-spacing: 1.5px; color: #a5946f !important; }}
.main .stPlotlyChart {{ background: transparent !important; }}
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════
#  HTML COMPONENT BUILDERS
# ════════════════════════════════════════════════════════════════════════

def plate(label: str, inner_html: str, extra: str = "") -> str:
    return f'<div class="faceplate"><div class="screw-b"></div><div class="plate-label">{label}</div>{extra}{inner_html}</div>'

def knob(value: float, vmin: float, vmax: float, label: str, val_fmt=None) -> str:
    """Rotary knob; pointer sweeps -135..135 deg."""
    frac = 0.0 if vmax == vmin else max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    deg = -135 + frac * 270
    arc = frac * 270
    txt = val_fmt(value) if val_fmt else f"{value:,.0f}"
    return (f'<div class="knob-wrap"><div class="knob" style="--deg:{deg:.1f}deg;--arc:{arc:.1f}deg">'
            f'<i></i></div><div class="knob-val">{txt}</div><div class="knob-lab">{label}</div></div>')

def vu(value: float, vmin: float, vmax: float, title: str, read: str, segments: int = 14) -> str:
    """LED segment VU meter: green -> amber -> red."""
    frac = 0.0 if vmax == vmin else max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    lit = int(round(frac * segments))
    bars = []
    for i in range(segments):
        if i < lit:
            color = "#35e06a" if i < segments * 0.6 else ("#ffb347" if i < segments * 0.82 else "#ff4040")
            bars.append(f'<b class="on" style="--c:{color}"></b>')
        else:
            bars.append("<b></b>")
    return (f'<div class="vu"><div class="vu-title">{title}</div>'
            f'<div class="vu-bar">{"".join(bars)}</div>'
            f'<div class="vu-read">{read}</div></div>')

def led(label: str, color: str) -> str:
    return f'<span class="led {color}"><i></i>{label}</span>'

def lcd(lines: list[str], tone: str = "") -> str:
    return f'<div class="lcd {tone}">' + "<br>".join(lines) + "</div>"

def strip(name: str, sub: str, pnl_pct: float, pnl_usd: float, extra: str = "") -> str:
    cls = "up" if pnl_pct >= 0 else "down"
    # fader position: -10%..+10% maps to 0..100%
    frac = max(0.0, min(1.0, (pnl_pct + 10) / 20))
    return (f'<div class="strip"><div class="s-name">{name}</div>'
            f'<div class="s-sub">{sub}</div>'
            f'<div class="s-pnl {cls}">{pnl_pct:+.1f}%</div>'
            f'<div class="s-sub {cls}">${pnl_usd:+.2f}</div>'
            f'<div class="s-fader"><div class="s-filler" style="width:{frac*100:.0f}%"></div></div>'
            f'{extra}</div>')


# ════════════════════════════════════════════════════════════════════════
#  DATA HELPERS
# ════════════════════════════════════════════════════════════════════════

def _fmt_price(p: float | None) -> str:
    if p is None:
        return "—"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.8f}"

def _asset(pair: str) -> str:
    return pair.split("/")[0] if "/" in pair else pair

def _now_mst() -> datetime:
    return datetime.now(MST)

def get_kraken_client():
    if _HAS_DB:
        try:
            try:
                setup_secrets()  # optional — secrets.toml may not exist
            except Exception:
                pass  # keyring is the real credential source
            api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
            api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
            if api_key and api_secret:
                return KrakenAPI(api_key, api_secret)
        except Exception:
            pass
    import ccxt
    class _Bare:
        exchange = ccxt.kraken({"enableRateLimit": True})
        def fetch_ohlcv(self, pair, interval_minutes=60, limit=200):
            tf = {1:"1m",5:"5m",15:"15m",60:"1h",240:"4h",1440:"1d"}.get(interval_minutes,"1h")
            return self.exchange.fetch_ohlcv(pair, timeframe=tf, limit=limit)
        def fetch_price(self, pair):
            return float(self.exchange.fetch_ticker(pair)["last"])
    return _Bare()

@st.cache_resource
def _bridge():
    """signal_bridge module handle (sqlite, stdlib-only)."""
    import sys
    sys.path.insert(0, r"C:\Users\birds\Workspace\kraken_scout")
    import signal_bridge
    return signal_bridge

def scanner_status() -> bool:
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*background_scanner*'} | Select-Object -First 1 ProcessId"],
            capture_output=True, text=True, timeout=6)
        return bool(r.stdout.strip())
    except Exception:
        return False

def bot_status() -> tuple[str, str]:
    """Bot liveness from heartbeat file age (same rule as bot dashboard)."""
    from pathlib import Path
    import json as _json
    hb = Path(BOT_DIR) / "bot_heartbeat.txt"
    if not hb.exists():
        return "NOT RUNNING", "no heartbeat file"
    try:
        raw = hb.read_text().strip()
        payload = _json.loads(raw[raw.index("{"):])
        t = datetime.fromisoformat(payload["ts"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo("UTC"))
        age = (datetime.now(ZoneInfo("UTC")) - t).total_seconds() / 60
        status = payload.get("status", "trading")
        if age > 32:
            return "NOT RUNNING", f"stale {age:.0f}m"
        return status.upper(), f"{age:.0f}m ago"
    except Exception as exc:
        return "UNKNOWN", str(exc)[:40]

@st.cache_data(ttl=60)
def bot_positions() -> list[dict]:
    """Open positions from the bot's Postgres DB."""
    try:
        url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
        if not url:
            return []
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, pair, base_currency, entry_volume, entry_price, entry_timestamp "
                "FROM positions WHERE is_open = true ORDER BY id")).fetchall()
        return [dict(id=r[0], pair=r[1], base=r[2], vol=float(r[3]),
                     entry=float(r[4]), ts=str(r[5])) for r in rows]
    except Exception:
        return []


# ── Dopamine layer: scoreboard + win feed + fresh-win celebration ──────

@st.cache_data(ttl=30)
def closed_trades(limit: int = 30) -> list[dict]:
    """Recent closed trades with realized P&L from the bridge."""
    try:
        sb = _bridge()
        sb.init_db()
        import sqlite3
        from pathlib import Path as _P
        bridge_db = _P(_bridge().__file__).parent / "signals.db"
        conn = sqlite3.connect(bridge_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT pair, action, source, closed_at, pnl FROM signals "
            "WHERE status='closed' AND pnl IS NOT NULL AND closed_at IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fmt_ago(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=MST)
        mins = (_now_mst() - t).total_seconds() / 60
        if mins < 60:
            return f"{int(mins)}m ago"
        if mins < 1440:
            return f"{int(mins // 60)}h ago"
        return f"{int(mins // 1440)}d ago"
    except Exception:
        return "—"


def today_scoreboard(pnl_usd: float, day_start: float) -> str:
    """Big animated P&L scoreboard + win stats. Green pulse when winning."""
    trades = closed_trades()
    today_iso = _now_mst().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    todays = [t for t in trades if (t.get("closed_at") or "") >= today_iso]
    wins = [t for t in todays if t["pnl"] and t["pnl"] > 0]
    losses = [t for t in todays if t["pnl"] and t["pnl"] < 0]
    win_rate = (len(wins) / (len(wins) + len(losses)) * 100) if (wins or losses) else 0.0
    best = max((t["pnl"] for t in wins), default=0.0)
    streak = 0
    for t in trades:  # current win streak (most recent first)
        if t["pnl"] and t["pnl"] > 0:
            streak += 1
        else:
            break

    cls = "win" if pnl_usd > 0.50 else ("loss" if pnl_usd < -0.50 else "flat")
    emoji = "🟢" if cls == "win" else ("🔴" if cls == "loss" else "🟡")
    pct = (pnl_usd / day_start * 100) if day_start else 0.0
    stats = (
        f'<div class="score-stat"><b>{len(wins)}</b>WINS today</div>'
        f'<div class="score-stat"><b>{len(losses)}</b>losses</div>'
        f'<div class="score-stat"><b>{win_rate:.0f}%</b>win rate</div>'
        f'<div class="score-stat"><b>+${best:,.2f}</b>best win</div>'
        + (f'<div class="score-stat"><b>{streak}🔥</b>streak</div>' if streak >= 2 else
           f'<div class="score-stat"><b>{streak}</b>streak</div>')
    )
    return (
        f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;">'
        f'<div><div class="score-big {cls}">{emoji} {"+" if pnl_usd >= 0 else "-"}${abs(pnl_usd):,.2f}</div>'
        f'<div class="score-sub">TODAY · {"+" if pct >= 0 else ""}{pct:.2f}% {"— keep it rolling" if cls == "win" else "— circuit breaker protects the downside"}</div></div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:4px;">{stats}</div></div>'
    )


def fresh_win_banner() -> str | None:
    """Celebration banner when a trade closed profitably in the last 30 min."""
    trades = closed_trades(limit=10)
    for t in trades:
        if not t["pnl"] or t["pnl"] <= 0:
            continue
        try:
            closed = datetime.fromisoformat(t["closed_at"])
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=MST)
            if (_now_mst() - closed).total_seconds() <= 1800:
                asset = (t["pair"] or "?").split("/")[0]
                confetti = "".join(
                    f'<span class="conf" style="left:{8 + i * 9}%;animation-delay:{i * 0.12}s;">{"✦✧★"[i % 3]}</span>'
                    for i in range(10))
                return (f'<div class="celebrate">🏆 FRESH WIN — {asset} banked '
                        f'<b>+${t["pnl"]:,.2f}</b> ({_fmt_ago(t["closed_at"])}){confetti}</div>')
        except Exception:
            continue
    return None


def win_feed() -> str:
    """Last closed trades with green/red P&L pills — the dopamine tape."""
    trades = closed_trades(limit=8)
    if not trades:
        return lcd(["NO CLOSED TRADES YET — first win lands here 🎯"], tone="amber")
    rows = []
    for t in trades:
        p = t["pnl"] or 0.0
        pill = (f'<span class="win-pill">+${p:,.2f}</span>' if p > 0
                else f'<span class="loss-pill">-${abs(p):,.2f}</span>' if p < 0
                else '<span class="win-pill" style="color:#b9a06d;border-color:rgba(185,160,109,.4);">closed</span>')
        asset = (t["pair"] or "?").split("/")[0]
        rows.append(f'<div class="feed-row">{pill}<b>{asset}</b> '
                    f'<span class="dim">sold · {_fmt_ago(t["closed_at"])}</span></div>')
    return "".join(rows)


def now_strip(block: str, no_buy: bool, interval: int, thr: int) -> str:
    """Plain-English one-liner: what is the system doing right now."""
    try:
        n_pos = len(bot_positions())
    except Exception:
        n_pos = 0
    sc = scanner_status()
    if no_buy:
        doing = "🌙 cooldown — no new buys until midnight, exits still watched"
    elif not sc:
        doing = "⚠️ scanner is OFFLINE — double-click the Kraken shortcut"
    else:
        doing = f"🔭 scanner hunting every {interval} min (bar {thr}%)"
    holding = (f" · holding {n_pos} position{'s' if n_pos != 1 else ''}" if n_pos else " · wallet flat, waiting for the next signal")
    return (f'<div class="now-strip"><b>NOW:</b> {doing}{holding}'
            f' · exits checked every 5 min — you don\'t have to do anything</div>')

# ── Contest watch list (console-local file — NOT the bridge, NOT trades) ──
# Manual contest positions the auto-watch should ride for exits.
# Survives browser refreshes and restarts. {pair: {entry, high, added}}
WATCH_FILE = Path(__file__).parent / "contest_watch.json"

def _load_watch() -> dict:
    try:
        import json
        if WATCH_FILE.exists():
            return json.loads(WATCH_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_watch(watch: dict):
    import json
    try:
        WATCH_FILE.write_text(json.dumps(watch, indent=2))
    except Exception as exc:
        logger.debug("watch save failed: %s", exc)


# ── Console state (survives refreshes): challenge tier + auto-watch on/off ──
STATE_FILE = Path(__file__).parent / "console_state.json"


def _play_sound(kind: str = "ding"):
    """Alert noise for popups: tick/ding = good news, alarm = GET OUT NOW.
    Embedded as base64 so it works with zero external files served."""
    try:
        import base64
        fn = {"ding": "alert_ding.wav", "alarm": "alert_alarm.wav",
              "tick": "alert_tick.wav"}.get(kind)
        if not fn:
            return
        p = Path(__file__).parent / "assets" / fn
        if not p.exists():
            return
        b64 = base64.b64encode(p.read_bytes()).decode()
        st.markdown(f'<audio autoplay src="data:audio/wav;base64,{b64}"></audio>',
                    unsafe_allow_html=True)
    except Exception:
        pass


VOICE_PREFIX = "Mic check one, two. Testing, testing. "


def _speak(text: str, rate: int = 2):
    """Zira (Windows' built-in female voice) reads the popup OUT LOUD —
    rendered to WAV and amplified up to 3x before playing (volume 100
    alone leaves her quiet). Every message opens with the soundboard mic
    check. Fire-and-forget, never blocks."""
    try:
        script = Path(__file__).parent / "speak_loud.ps1"
        if not script.exists():
            return
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Text", VOICE_PREFIX + text, "-Rate", str(int(rate))],
            creationflags=0x08000000)  # hidden window
    except Exception:
        pass


def _load_console_state() -> dict:
    import json
    try:
        doc = json.loads(STATE_FILE.read_text())
        if isinstance(doc, dict):
            return doc
    except Exception:
        pass
    return {"tier": "$5,000", "watch_on": True}


def _save_console_state(state: dict):
    import json
    try:
        STATE_FILE.write_text(json.dumps(state, indent=1))
    except Exception as exc:
        logger.debug("console state save failed: %s", exc)


# ── Contest payouts: deposits from the Funded account ARE the results ───
# Kraken types these ledger entries as type='transfer', direction='in'
# (verified live: $135.72 Fri 8:10 PM, $115.92 Sat 8:08 AM).
PAYOUT_TRACK_FILE = Path(__file__).parent / "payout_track.json"


@st.cache_data(ttl=300)
def fetch_payouts(_kraken) -> list[dict]:
    """Recent USD transfers IN from the Kraken ledger — the Funded contest
    paying out. (Trades are type 'trade'; rewards are 'receive'; both excluded.)"""
    try:
        inner = getattr(_kraken, "exchange", None)
        if inner is None or not hasattr(inner, "fetch_ledger"):
            return []
        entries = inner.fetch_ledger(limit=50) or []
        out = []
        for e in entries:
            etype = str(e.get("type", "")).lower()
            direction = str(e.get("direction", "")).lower()
            amt = float(e.get("amount") or 0)
            if etype == "transfer" and direction == "in" and amt > 0:
                out.append({"id": str(e.get("id") or ""), "amount": amt,
                            "ts": int(e.get("timestamp") or 0)})
        out.sort(key=lambda x: -x["ts"])
        return out
    except Exception:
        return []


def _load_payout_store() -> dict:
    import json
    try:
        if PAYOUT_TRACK_FILE.exists():
            doc = json.loads(PAYOUT_TRACK_FILE.read_text())
            if isinstance(doc, dict) and isinstance(doc.get("payouts"), dict):
                return doc
    except Exception:
        pass
    return {"payouts": {}}


def track_payouts(ledger_payouts: list[dict]) -> tuple[str | None, list[dict]]:
    """Merge ledger payouts into the persistent store (so the total keeps
    growing even after the ledger rolls past 50 entries). Returns
    (celebration_banner_or_None, all_payouts_newest_first). First run just
    baselines what exists — no celebration spam on deploy."""
    import json
    try:
        store = _load_payout_store()
        payouts = store["payouts"]
        first_run = not payouts and not PAYOUT_TRACK_FILE.exists()
        incoming = ledger_payouts if first_run else [p for p in ledger_payouts
                                                     if p["id"] and p["id"] not in payouts]
        for p in incoming:
            payouts[p["id"]] = {"amount": p["amount"], "ts": p["ts"]}
        PAYOUT_TRACK_FILE.write_text(json.dumps(store, indent=1))
        banner = None
        if not first_run and incoming:
            total = sum(p["amount"] for p in incoming)
            banner = (f"💰 THE CONTEST JUST PAID YOU ${total:,.2f}! "
                      f"Real money — it's in your Kraken account right now.")
        all_payouts = sorted(payouts.values(), key=lambda x: -x["ts"])
        return banner, all_payouts
    except Exception as exc:
        logger.debug("payout tracking failed: %s", exc)
        return None, []


def _payout_date(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, MST).strftime("%b %d · %I:%M %p")
    except Exception:
        return "?"


@st.cache_data(ttl=60)
def wallet_snapshot(_kraken) -> dict:
    """THE source of truth: actual Kraken wallet.

    Returns {cash: float, holdings: {asset: {amount, usd}}, total: float}.
    Holdings skip cash currencies, dust (< $1), and unpriceable assets
    (no /USD or /USDT market — staked/locked tokens like ATOM21.S).
    """
    try:
        bal = _kraken.exchange.fetch_balance()
        cash = 0.0
        for cur in ("USD", "USDT", "EUR", "GBP"):
            v = bal.get(cur, {})
            cash += float(v.get("total", 0)) if isinstance(v, dict) else float(v or 0)

        holdings: dict[str, dict] = {}
        for cur, info in bal.get("total", {}).items():
            if cur in ("USD", "USDT", "EUR", "GBP", "ZFEE"):
                continue
            amt = float(info) if not isinstance(info, dict) else float(info.get("total", 0))
            if amt <= 0:
                continue
            px = 0.0
            for q in ("USD", "USDT"):
                try:
                    px = float(_kraken.exchange.fetch_ticker(f"{cur}/{q}").get("last", 0))
                    if px:
                        break
                except Exception:
                    continue
            usd = amt * px if px else 0.0
            if usd >= 1.0:  # skip dust and unpriceable
                holdings[cur] = {"amount": amt, "usd": usd}

        total = cash + sum(h["usd"] for h in holdings.values())
        return {"cash": cash, "holdings": holdings, "total": total}
    except Exception:
        return {"cash": 0.0, "holdings": {}, "total": 0.0}


@st.cache_data(ttl=60)
def account_value(_kraken) -> float:
    """Total account value in USD (cash + crypto at market)."""
    return wallet_snapshot(_kraken)["total"]


def reconcile(kraken) -> dict:
    """Cross-check DB records against the ACTUAL Kraken wallet.

    - verified: bot-DB positions the wallet actually backs (wallet amount used)
    - ghosts_bot: bot-DB positions whose asset is no longer on Kraken (stale records)
    - ghosts_bridge: bridge 'filled' signals with no wallet backing and no bot position
    - untracked: wallet assets no DB knows about (from failed rotations / manual buys)
    """
    wallet = wallet_snapshot(kraken)
    holdings = wallet["holdings"]
    bot = bot_positions()
    sb = _bridge()
    try:
        bridge_filled = sb.get_open_positions()
    except Exception:
        bridge_filled = []

    verified, ghosts_bot, ghosts_bridge = [], [], []
    tracked_bases: set[str] = set()

    for p in bot:
        held = holdings.get(p["base"], {}).get("amount", 0.0)
        if held > 0:
            vp = dict(p)
            vp["wallet_amount"] = held  # actual wallet amount is the truth
            verified.append(vp)
            tracked_bases.add(p["base"])
        else:
            ghosts_bot.append(p)

    live_bot_bases = {p["base"] for p in verified}
    for sig in bridge_filled:
        base = _asset(sig["pair"])
        held = holdings.get(base, {}).get("amount", 0.0)
        if held > 0 or base in live_bot_bases:
            tracked_bases.add(base)
        else:
            ghosts_bridge.append(sig)

    untracked = {a: h for a, h in holdings.items() if a not in tracked_bases}

    mismatch = bool(ghosts_bot or ghosts_bridge or untracked)
    return {
        "wallet": wallet,
        "verified": verified,
        "ghosts_bot": ghosts_bot,
        "ghosts_bridge": ghosts_bridge,
        "untracked": untracked,
        "status": "MISMATCH" if mismatch else "RECONCILED",
    }


def _close_bot_positions(ids: list[int]) -> int:
    """Close stale bot-DB positions (asset confirmed gone from Kraken)."""
    try:
        url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
        if not url or not ids:
            return 0
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        closed = 0
        with eng.begin() as conn:
            for pid in ids:
                conn.execute(text("UPDATE positions SET is_open = false WHERE id = :id"), {"id": pid})
                closed += 1
        return closed
    except Exception:
        return 0

@st.cache_data(ttl=60)
def live_price(pair: str) -> float:
    try:
        return float(get_kraken_client().fetch_price(pair))
    except Exception:
        return 0.0


def current_threshold() -> tuple[str, str, int, int, bool]:
    """(block_name, day_label, auto_trade_threshold, scan_interval, no_buy) right now."""
    from background_scanner import get_time_block, WEEKDAY_NAMES
    n = _now_mst()
    name, interval, _conf, thr, _desc, no_buy, _key = get_time_block(n.hour, n.weekday())
    return name, WEEKDAY_NAMES.get(n.weekday(), "?"), thr, interval, no_buy


@st.cache_data(ttl=90)
def _scalp_headline(_ex, rotation_key: str, sched: str) -> tuple[list[dict], int]:
    """RIGHT NOW ranking: the live signal pool, scalp-scored and clustered.

    Signals arrive in correlated waves — one market move wearing many
    tickers — and ranking them by conviction just presented a menu of
    near-identical 90% breakouts. This ranks the same pool the way the
    user plays: fastest $20 from the freshest entry wins, conviction is
    the entry ticket (60+), and signals fired in the same ~10-minute
    sweep collapse into ONE move (best-in-sweep by pace, then book depth).
    Returns (ranked candidates, total signals considered)."""
    import sqlite3
    from collections import Counter
    from background_scanner import _hours_to_scalp, _max_resolve_hours
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.db"))
        conn.row_factory = sqlite3.Row
        since = (_now_mst() - timedelta(minutes=45)).isoformat()
        rows = conn.execute(
            "SELECT pair, confidence, entry_price, timestamp FROM signals "
            "WHERE action='buy' AND timestamp >= ? AND confidence >= ? "
            "AND source LIKE 'scout%' AND source != 'scout_dca' "
            "ORDER BY timestamp DESC", (since, QC_SCALP_FLOOR)).fetchall()
        conn.close()
    except Exception:
        return [], 0
    total = len(rows)
    latest: dict[str, sqlite3.Row] = {}
    for r in rows:                      # newest first — keep the latest per pair
        latest.setdefault(r["pair"], r)

    cap = _max_resolve_hours()
    doc = None
    try:
        from volatility_engine import load_rotation_thresholds
        doc = load_rotation_thresholds(max_age_s=900) or {}
    except Exception:
        doc = {}

    cands = []
    for pair, r in latest.items():
        if pair.split("/")[0] not in ASSETS:
            continue
        if _recent_big_payout(pair) is not None:
            continue                    # exhausted move — QC would reject it too
        entry = float(r["entry_price"] or 0)
        if entry <= 0:
            continue
        pace = _hours_to_scalp(pair, entry)
        if pace is None or pace > cap:
            continue                    # too slow to the $20
        try:
            live = float(_ex.fetch_ticker(pair)["last"])
        except Exception:
            continue
        ran_pct = (live - entry) / entry * 100
        if ran_pct > QC_CHASE_PCT:
            continue                    # price already ran from the signal
        vol = float((doc.get("universe", {}).get(pair, {})
                     .get(sched, {}).get(rotation_key) or {}).get("median_volume_usd") or 0)
        try:
            bucket = int(datetime.fromisoformat(r["timestamp"]).timestamp() // 600)
        except Exception:
            bucket = 0
        cands.append({"asset": pair.split("/")[0], "pair": pair,
                      "conf": float(r["confidence"]), "pace": pace,
                      "ran": ran_pct, "vol": vol, "bucket": bucket})

    # collapse each sweep to its best expression (one market move = one pick)
    sizes = Counter(c["bucket"] for c in cands)
    best: dict[int, dict] = {}
    for c in cands:
        cur = best.get(c["bucket"])
        if cur is None or (c["pace"], -c["vol"]) < (cur["pace"], -cur["vol"]):
            best[c["bucket"]] = c
    ranked = sorted(best.values(), key=lambda c: (c["pace"], -c["vol"]))
    for c in ranked:
        c["cluster"] = sizes[c["bucket"]]
    return ranked, total


@st.cache_data(ttl=300)
def _fast_twenty(rotation_key: str, sched: str) -> list[dict]:
    """⚡ FAST $20 leaderboard: every coin ranked by how fast the $20 (+0.4%)
    comes at THIS rotation, from the Volatility Engine's own per-asset pace.
    PUMP was the example: 2.1%/rotation wiggle, $20 in ~0.8h — invisible to
    conviction gates, obvious to pace. Liquidity-floored so it never points
    at an untradeable book. Discovery only — the deep scan still judges."""
    try:
        from volatility_engine import load_rotation_thresholds
        doc = load_rotation_thresholds(max_age_s=900)
        if not doc:
            return []
        try:
            from background_scanner import (LIQ_ROT_FLOOR_WEEKDAY,
                                            LIQ_ROT_FLOOR_WEEKEND,
                                            SCALP_TARGET_PCT)
        except ImportError:
            LIQ_ROT_FLOOR_WEEKDAY, LIQ_ROT_FLOOR_WEEKEND, SCALP_TARGET_PCT = 15_000, 10_000, 0.4
        floor = LIQ_ROT_FLOOR_WEEKDAY if sched == "weekday" else LIQ_ROT_FLOOR_WEEKEND
        out = []
        for a in ASSETS:
            cell = (doc.get("universe", {}).get(f"{a}/USD", {})
                    .get(sched, {}).get(rotation_key) or {})
            avg = float(cell.get("avg_move") or 0)
            vol = float(cell.get("median_volume_usd") or 0)
            if avg <= 0 or vol < floor:
                continue
            out.append({"asset": a, "eta": (SCALP_TARGET_PCT / avg) * 4.0,
                        "avg": avg, "reliable": bool(cell.get("reliable"))})
        out.sort(key=lambda x: x["eta"])
        return out[:6]
    except Exception:
        return []


def volatility_snapshot() -> dict | None:
    """Live Volatility Engine data for the current rotation (read-only peek)."""
    try:
        from volatility_engine import load_rotation_thresholds
        doc = load_rotation_thresholds(max_age_s=0)
        if not doc:
            return None
        from background_scanner import rotation_key_for_hour, _schedule_for_dow
        n = _now_mst()
        rot = rotation_key_for_hour(n.hour)
        sched = _schedule_for_dow(n.weekday())
        cell = doc.get("median", {}).get(sched, {}).get(rot)
        if not cell:
            return None
        return {
            "rotation": rot,
            "schedule": sched,
            "data_weeks": doc.get("data_weeks", 0),
            **cell,
        }
    except Exception:
        return None


# ── Contest position sizing: the Kraken contest app only takes chunks of
# the balance (25% / 50% / 75% / 100%). Snap the strategy's computed size
# UP to the nearest allowed chunk — buy big, get out fast.
CONTEST_CHUNKS = (0.25, 0.50, 0.75, 1.00)

# ── Quick Check green-light conviction floor (Aug 24 replay evidence) ──
# The old gate demanded the rotation's median confidence bar — a trend-trust
# number. Replay of a week of signals vs actual price: sub-bar buys hit the
# $20 (+0.4%) in the resolve window 80% of the time vs 72% for bar-passing
# buys — the bar predicts NOTHING for scalps (and it swings 80→60 on a
# single engine refresh). The QC is a SCALP tool: the $20-pace gate
# (resolve rule) is the judge; conviction only needs a real setup (60%+).
# Desktop alerts keep their own 89% floor; the bot bridge keeps its
# per-asset bars — different games, different ledgers.
QC_SCALP_FLOOR = 60

# ── Chase guard (Aug 24, PENGU was the example) ─────────────────────
# Trend conviction is a rear-view mirror: it maxes out AFTER a move runs
# (PENGU scanned 100% at the top of a +5.6% leg, -$44 immediately; PUMP
# scanned 79% at the base, +$133). A green light must be FRESH: if live
# price is more than 2% above the last hour's low, the move is old and
# the entry pays for the part that already happened. "Wait for the next
# wiggle" is the right call, however confident the trend looks.
QC_CHASE_PCT = 2.0

# mirrors background_scanner.REENTRY_BLOCK_MIN (kept local: the console's
# cached scanner module may predate it — the QC must never crash or silently
# depend on a fresh scanner for this)
REENTRY_BLOCK_MIN = 60


def _recent_big_payout(pair: str) -> int | None:
    """Amount of the last BIG TAKE IT payout (≥$100) on this pair, if it was
    within REENTRY_BLOCK_MIN minutes. The scanner writes take_payouts.json;
    the QC refuses to green-light re-entries on a move that just paid big —
    it's exhausted, and the re-buy rides the giveback (PUMP, Aug 24: took
    the $140, re-entered 20 min later, rode -$70 back down)."""
    import json, os
    from datetime import datetime
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "take_payouts.json")
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        rec = doc.get(pair)
        if not rec:
            return None
        ts = datetime.fromisoformat(rec["ts"])
        age_min = (datetime.now(ts.tzinfo) - ts).total_seconds() / 60
        if age_min <= REENTRY_BLOCK_MIN:
            return int(rec.get("amount") or 0)
        return None
    except Exception:
        return None


def contest_chunk(position_size_usd, balance: float) -> tuple[float, float] | None:
    """(chunk_fraction, chunk_usd) — the size to pick in the contest app."""
    if not balance or balance <= 0 or not position_size_usd or position_size_usd <= 0:
        return None
    frac = position_size_usd / balance
    for chunk in CONTEST_CHUNKS:
        if frac <= chunk + 1e-9:
            return chunk, chunk * balance
    return 1.0, balance


def fetch_df(kraken, pair: str, cfg: TradingConfig):
    import pandas as pd
    ohlcv = kraken.fetch_ohlcv(pair, interval_minutes=cfg.candle_interval, limit=cfg.lookback_candles)
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def render_chart(df, signal, config: TradingConfig, height: int = 430):
    bb = bollinger_bands(df, config.bollinger_period, config.bollinger_std)
    ema_s = ema(df["close"], config.ema_short)
    ema_l = ema(df["close"], config.ema_long)
    rsi_s = rsi(df, config.rsi_period)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                 close=df["close"], name="Price",
                                 increasing_line_color="#4be38a", decreasing_line_color="#ff6b5e"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb["upper"], name="BB", line=dict(color="#5c5647", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb["lower"], name="BB lo", line=dict(color="#5c5647", width=1, dash="dot"), fill="tonexty", fillcolor="rgba(92,86,71,0.08)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema_s, name=f"EMA{config.ema_short}", line=dict(color="#ffb347", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema_l, name=f"EMA{config.ema_long}", line=dict(color="#4BA9FE", width=1.5)), row=1, col=1)
    if signal.entry_price:
        fig.add_hline(y=signal.entry_price, line_color="#4be38a", line_width=2,
                      annotation_text=f"ENTRY {_fmt_price(signal.entry_price)}", row=1, col=1)
    if signal.stop_loss:
        fig.add_hline(y=signal.stop_loss, line_dash="dash", line_color="#ff5252", line_width=2,
                      annotation_text=f"STOP {_fmt_price(signal.stop_loss)}", row=1, col=1)
    if signal.take_profit:
        fig.add_hline(y=signal.take_profit, line_dash="dash", line_color="#4BA9FE", line_width=2,
                      annotation_text=f"TP {_fmt_price(signal.take_profit)}", row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=rsi_s, name="RSI", line=dict(color="#d9a7ff", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#5c5647", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#5c5647", row=2, col=1)
    fig.update_layout(template="plotly_dark", height=height, margin=dict(l=10, r=10, t=34, b=10),
                      xaxis_rangeslider_visible=False, showlegend=True,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


# ════════════════════════════════════════════════════════════════════════
#  MASTER MODULE
# ════════════════════════════════════════════════════════════════════════

def render_master(cfg: TradingConfig, kraken):
    sb = _bridge()

    # ── Reconcile: DB records vs the ACTUAL Kraken wallet ────────────
    rec = reconcile(kraken)
    wallet = rec["wallet"]
    bal = wallet["total"]
    day_start = sb.get_daily_start_balance()
    pnl = (bal - day_start) if day_start else 0.0
    pnl_pct = (pnl / day_start * 100) if day_start else 0.0

    # killer breakers
    kill_pct = (min(0.0, pnl_pct) / -10.0) * 100 if pnl_pct < 0 else 0.0
    loss_room = max(0.0, (day_start * 0.90 - bal)) if day_start else 0.0

    c1 = st.container()  # single full-width column — Channel Bank removed (bot holdings, not contest)
    with c1:
        # ── THE score that matters: Funded-account payouts (real deposits)
        # plus the live P&L of watched contest positions. All automatic —
        # no manual entry; Kraken's contest dashboard owns the rest.
        payouts = st.session_state.get("payouts") or []
        pay_total = sum(p["amount"] for p in payouts)
        if payouts:
            pay_rows = [
                f"<span class='win-pill'>+${p['amount']:,.2f}</span> <span class='dim'>{_payout_date(p['ts'])}</span>"
                for p in payouts[:5]]
            pay_html = (
                f"<div class='score-big win' style='font-size:36px;'>${pay_total:,.2f}</div>"
                f"<div class='score-sub'>the contest has paid you so far — straight into your Kraken account</div>"
                + "<div style='height:10px'></div>" + "".join(
                    f'<div class="feed-row">{r}</div>' for r in pay_rows))
        else:
            pay_html = lcd(["Nothing from the contest yet — the moment it pays you, it shows up here",
                            "<span class='dim'>real money, counted automatically</span>"],
                           tone="amber")
        st.markdown(plate("💰 Money the Contest Paid You", pay_html), unsafe_allow_html=True)

        # ── live score of watched contest positions ─────────────────────
        cw = _load_watch()
        contest_rows = []
        total_pct = 0.0
        n_pos = 0
        for pair, w in cw.items():
            px = live_price(pair)
            entry = w.get("entry")
            if not px or not entry:
                continue
            pnl_pct_c = (px / entry - 1) * 100
            total_pct += pnl_pct_c
            n_pos += 1
            contest_rows.append(
                f"◆ {_asset(pair)} <span class='dim'>bought at {_fmt_price(entry)}, now {_fmt_price(px)}</span> "
                f"<span style='color:{'#4be38a' if pnl_pct_c >= 0 else '#ff6b5e'};'>{pnl_pct_c:+.1f}%</span>")
        if n_pos:
            big_cls = "win" if total_pct >= 0 else "loss"
            score_html = (
                f"<div class='score-big {big_cls}' style='font-size:36px;'>"
                f"{'+' if total_pct >= 0 else ''}{total_pct:.1f}%</div>"
                f"<div class='score-sub'>across {n_pos} contest position{'s' if n_pos != 1 else ''} right now</div>"
                + "<div style='height:10px'></div>" + lcd(contest_rows))
        else:
            score_html = lcd(["Nothing being watched — a green light on a QUICK CHECK deep scan puts it here",
                              "<span class='dim'>score starts the moment you enter a trade</span>"],
                             tone="amber")
        st.markdown(plate("My Contest Score — live", score_html), unsafe_allow_html=True)

        # ── reset: wipe the score (clears the watch list) ───────────────
        with st.popover("↺ RESET SCORE", help="Clears every watched contest position and zeroes the score"):
            st.caption(f"Wipes all {n_pos} watched position{'s' if n_pos != 1 else ''} "
                       f"and starts the score fresh. Can't be undone.")
            if st.button("🧹 WIPE IT — start fresh", type="primary", key="score_reset_btn"):
                _save_watch({})
                try:
                    st.toast("↺ Score reset — board is clean for the next run", icon="↺", duration=8)
                    _play_sound("tick")
                except Exception:
                    pass
                st.rerun()
        knobs = (
            knob(bal, 0, 1500, "Account Value", lambda v: f"${v:,.0f}") +
            knob(pnl, -100, 100, "Today P&L", lambda v: f"${v:+,.2f}") +
            knob(pnl_pct, -12, 12, "Day %", lambda v: f"{v:+.1f}%") +
            knob(max(0, 100 - kill_pct), 0, 100, "Breaker Room", lambda v: f"{v:.0f}%")
        )
        st.markdown(plate("Master Bus — Live Wallet", f'<div class="knob-row">{knobs}</div>'), unsafe_allow_html=True)
        st.markdown(plate("Kill Switch Bus", lcd([
            f"DAILY LOSS LIMIT <span class='dim'>.......</span> -{sb.MAX_DAILY_LOSS_PCT:.0f}% of day start",
            f"DAY START <span class='dim'>..........</span> ${day_start:,.2f}" if day_start else "DAY START <span class='dim'>..........</span> not snapshotted yet",
            f"CURRENT VALUE <span class='dim'>......</span> ${bal:,.2f}  <span class='dim'>(cash ${wallet['cash']:,.2f})</span>",
            f"ROOM TO LIMIT <span class='dim'>......</span> ${loss_room:,.2f}",
        ], tone="red" if kill_pct > 50 else "")), unsafe_allow_html=True)

    # (Channel Bank removed — bot spot holdings don't belong on the contest
    #  console. The reconcile data below still catches ghost/untracked wallet
    #  drift, and the bot's own logs own the position detail.)
    verified = rec["verified"]

    # ── Console Integrity: the cross-check itself ─────────────────────
    n_gbot, n_gbr, n_un = len(rec["ghosts_bot"]), len(rec["ghosts_bridge"]), len(rec["untracked"])
    status_led = led("DB ↔ KRAKEN RECONCILED", "g") if rec["status"] == "RECONCILED" else \
                 led(f"DB ↔ KRAKEN MISMATCH", "r")
    integrity_lines = [
        f"WALLET HOLDINGS <span class='dim'>......</span> {len(wallet['holdings'])} assets · "
        f"cash ${wallet['cash']:,.2f}",
        f"VERIFIED POSITIONS <span class='dim'>....</span> {len(verified)} "
        f"<span class='dim'>(DB records backed by real wallet balance)</span>",
    ]
    if n_gbot:
        integrity_lines.append(f"<span style='color:#ff6b5e;'>GHOST DB POSITIONS <span class='dim'>.....</span> {n_gbot} "
                               f"— sold on Kraken but still open in the bot DB</span>")
        for p in rec["ghosts_bot"]:
            integrity_lines.append(f"  <span class='dim'>ghost:</span> {p['base']} #{p['id']} @ {p['entry']:.4g}")
    if n_gbr:
        integrity_lines.append(f"<span style='color:#ff6b5e;'>GHOST BRIDGE SIGNALS <span class='dim'>...</span> {n_gbr} "
                               f"— bridge 'filled' with nothing on the exchange</span>")
        for s in rec["ghosts_bridge"]:
            integrity_lines.append(f"  <span class='dim'>ghost:</span> {_asset(s['pair'])} {s['signal_id']}")
    if n_un:
        integrity_lines.append(f"<span style='color:#ffc36b;'>UNTRACKED WALLET ASSETS <span class='dim'>..</span> {n_un} "
                               f"— held on Kraken, no DB record</span>")
        for a, h in sorted(rec["untracked"].items(), key=lambda kv: -kv[1]["usd"]):
            integrity_lines.append(f"  <span class='dim'>untracked:</span> {a} — {h['amount']:.4g} (${h['usd']:,.2f})")
    if rec["status"] == "RECONCILED":
        integrity_lines.append("<span class='dim'>Every DB record matches the wallet. Nothing stale.</span>")

    tone = "" if rec["status"] == "RECONCILED" else ("red" if (n_gbot or n_gbr) else "amber")
    st.markdown(plate("Console Integrity — DB vs Kraken Wallet",
                      status_led + "<div style='height:8px'></div>" + lcd(integrity_lines, tone=tone)),
                unsafe_allow_html=True)

    # one-click cleanup of ghost records (only closes records whose asset is
    # CONFIRMED gone from the Kraken wallet — never touches real positions)
    if n_gbot or n_gbr:
        if st.button("🧹 CLEAN GHOST RECORDS (close stale DB entries)", key="clean_ghosts", width="stretch"):
            closed_bridge = 0
            for s in rec["ghosts_bridge"]:
                try:
                    sb.mark_closed(s["signal_id"], 0.0)
                    closed_bridge += 1
                except Exception:
                    pass
            closed_bot = _close_bot_positions([p["id"] for p in rec["ghosts_bot"]])
            st.cache_data.clear()
            st.success(f"Cleaned: {closed_bot} bot-DB ghost(s), {closed_bridge} bridge ghost(s). "
                       f"Display is now in sync with the wallet.")
            st.rerun()

    # signal bridge activity
    try:
        recent = sb.get_recent_signals(12)
        lines = []
        for s in recent:
            icon = {"filled": "▶", "pending": "◇", "closed": "■", "rejected": "✕",
                    "expired": "◌", "cancelled": "⊘"}.get(s.get("status"), "·")
            lines.append(f"{icon} {s['timestamp'][11:19]} {_asset(s['pair']):<9} {s['action'].upper():<4} "
                         f"{s['confidence'] or 0:>3.0f}%  {s['status']:<10} {s['source']}")
        st.markdown(plate("Signal Bus (last 12)", lcd(lines or ["— no signals yet —"])), unsafe_allow_html=True)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════
#  TOURNAMENT MODULE
# ════════════════════════════════════════════════════════════════════════

def render_tournament(cfg: TradingConfig, scan_cfg: TradingConfig, kraken):
    from strategy import enable_deep_analysis, disable_deep_analysis

    # ── ⚡ RIGHT NOW — the scalp headline. No bracket run needed: the live
    # signal pool, scalp-ranked (fastest $20, freshest entry), and the
    # correlated wave collapsed to ONE pick. This is the answer to "all the
    # signals come at the same time" — they're one move; here's its best
    # expression. Zira announces when a NEW headline takes the seat.
    block, day, thr, _, no_buy = current_threshold()
    try:
        from background_scanner import rotation_key_for_hour, _schedule_for_dow
        _n = _now_mst()
        ranked, total = _scalp_headline(kraken.exchange,
                                        rotation_key_for_hour(_n.hour),
                                        _schedule_for_dow(_n.weekday()))
    except Exception:
        ranked, total = [], 0
    if no_buy:
        st.markdown(plate("⚡ RIGHT NOW", lcd([
            "🌙 COOLDOWN (8pm–midnight) — no new picks",
            "<span class='dim'>exit checks still run · new entries at midnight (R1 Asia)</span>",
        ], tone="amber")), unsafe_allow_html=True)
    elif ranked:
        h = ranked[0]
        if st.session_state.get("_headline_asset") != h["asset"]:
            st.session_state["_headline_asset"] = h["asset"]
            try:
                _speak(f"New pick! {h['asset']}! Twenty dollars in about {h['pace']:.1f} hours!")
            except Exception:
                pass
        runners = ranked[1:3]
        lines = [
            f"<span style='color:#4be38a;font-size:30px;font-family:Audiowide;'>⚡ RIGHT NOW: {h['asset']}</span>",
            f"$20 IN <span class='dim'>...........</span> ~{h['pace']:.1f}h"
            f"   SINCE SIGNAL <span class='dim'>...</span> +{h['ran']:.1f}%"
            f"   SETUP <span class='dim'>.........</span> {h['conf']:.0f}%",
        ]
        if runners:
            lines.append("<span class='dim'>runners-up: "
                         + " · ".join(f"{r['asset']} (~{r['pace']:.1f}h)" for r in runners)
                         + "</span>")
        lines.append(f"<span class='dim'>{total} signals in the last 45 min · "
                     f"{len(ranked)} distinct moves · wave collapsed to one pick"
                     + (f" · {h['cluster']} signals shared this move" if h.get("cluster", 1) > 1 else "")
                     + "</span>")
        st.markdown(plate("The Pick — right now", lcd(lines, tone="green")), unsafe_allow_html=True)
        if st.button(f"➡ CONFIRM {h['asset']} IN QUICK CHECK — the final yes/no",
                     type="primary", width="stretch", key="headline_confirm"):
            st.session_state["qc_pick_next"] = h["asset"]
            st.session_state["active_tab"] = "QUICK CHECK"
            st.rerun()
    else:
        st.markdown(plate("⚡ RIGHT NOW", lcd([
            "NO FRESH PICKS THIS MINUTE",
            "<span class='dim'>the scanner's hunting — a pick lands here the moment it clears "
            "the scalp gates (setup 60+ · fast $20 · fresh)</span>",
        ], tone="amber")), unsafe_allow_html=True)

    b1, b2, _ = st.columns([1, 1, 3])
    with b1:
        scan_clicked = st.button("▶ RUN BRACKET", type="primary", width="stretch", key="run_tourney")
    with b2:
        winners_clicked = st.button("🏆 WINNERS ONLY", width="stretch", key="winners_tourney",
                                    help="Battle all recent picks head-to-head — tournament champions, "
                                         "ticker movers, AND gainers. The winner of winners gets the call.")

    if scan_clicked or winners_clicked:
        disable_deep_analysis()
        is_winners = winners_clicked
        if is_winners and _HAS_DB:
            try:
                db.init_db()
            except Exception:
                pass
            winner_pairs = db.get_winner_pairs(limit=20)
            if len(winner_pairs) < 2:
                st.warning("Not enough tournament winners yet — run a full tournament first.")
                st.stop()
            scan_cfg = TradingConfig()
            scan_cfg.trading_pairs = [p if "/" in p else f"{p}/USD" for p in winner_pairs]
            scan_cfg.candle_interval = cfg.candle_interval
            scan_cfg.lookback_candles = cfg.lookback_candles
            scan_cfg.challenge_balance = cfg.challenge_balance
            scan_cfg.min_confidence = cfg.min_confidence
            scan_cfg.min_volume_usd_24h = cfg.min_volume_usd_24h
            label = f"RECENT PICKS ({len(winner_pairs)})"
        else:
            label = f"ALL {len(scan_cfg.trading_pairs)} ASSETS"

        st.markdown(plate("Bracket Feed", f'<div id="bracket"></div>'), unsafe_allow_html=True)
        terminal = st.empty()
        st.session_state["_match_log"] = []

        def _act(a):
            # Never short: "sell" = exit a held long / stay out if flat
            return {"buy": "BUY", "sell": "EXIT / AVOID"}.get(a, "HOLD")

        def _col(a):
            return {"buy": "#4be38a", "sell": "#ff6b5e"}.get(a, "#8d8271")

        def on_progress(m):
            st.session_state["_match_log"].append(m)
            log = st.session_state["_match_log"]
            a, b, w, l = _asset(m["pair_a"]), _asset(m["pair_b"]), _asset(m["winner"]), _asset(m["loser"])
            # winner's row color follows its action: buys green, sells/exit red, hold dim
            w_col = _col(m.get("a_action") if m.get("winner") == m.get("pair_a") else m.get("b_action"))
            if m["pair_b"] == "(bye)":
                row = (f'<div style="font-size:13px;margin:2px 0;">'
                       f'<span style="color:#ffc36b;">{a}</span> '
                       f'<span style="color:#6e675a;font-size:11px;">— BYE —</span></div>')
            else:
                row = (f'<div style="font-size:13px;margin:2px 0;font-family:Share Tech Mono,monospace;">'
                       f'<span style="color:{w_col};">✔ {w}</span>'
                       f'<span style="color:#6e675a;"> def. </span>'
                       f'<span style="color:#ff6b5e;text-decoration:line-through;">{l}</span>'
                       f'<span style="color:#6e675a;font-size:11px;">'
                       f' ({_act(m["a_action"])} {m["a_conf"]:.0f}% vs {_act(m["b_action"])} {m["b_conf"]:.0f}%)</span></div>')
            # rolling feed: current round header + the last 9 match results
            feed_rows = "".join(
                (f'<div style="font-size:13px;margin:2px 0;font-family:Share Tech Mono,monospace;">'
                 f'<span style="color:{_col(h["a_action"] if h.get("winner") == h.get("pair_a") else h.get("b_action"))};">✔ {_asset(h["winner"])}</span>'
                 f'<span style="color:#6e675a;"> def. </span>'
                 f'<span style="color:#ff6b5e;text-decoration:line-through;">{_asset(h["loser"])}</span></div>')
                if h["pair_b"] != "(bye)" else
                (f'<div style="font-size:13px;margin:2px 0;"><span style="color:#ffc36b;">'
                 f'{_asset(h["pair_a"])}</span> <span style="color:#6e675a;font-size:11px;">— BYE —</span></div>')
                for h in log[-9:-1]
            )
            html = f"""
            <div class="lcd" style="font-size:13px;">
              <span style="color:#ffc36b;">ROUND {m['round']}</span>
              <span class="dim"> · match {m['match']}/{m['total_matches']} · {len(log)} played · {results_ref[0]} alive</span>
              {feed_rows}
              <div style="border-top:1px solid #4e6b52;margin-top:5px;padding-top:4px;">{row}</div>
            </div>"""
            terminal.markdown(html, unsafe_allow_html=True)
            time.sleep(0.25)  # let the bracket visibly advance

        with st.spinner("Running tournament…"):
            try:
                from background_scanner import fetch_ohlcv_parallel, filter_illiquid
                from strategy import evaluate_strategy as _eval
                from scanner import _compare_signals
                from concurrent.futures import ThreadPoolExecutor, as_completed
                # NOTE: fetch_ohlcv_parallel speaks raw ccxt (timeframe= kwarg),
                # so pass the underlying exchange, not the KrakenAPI wrapper.
                data = fetch_ohlcv_parallel(kraken.exchange, scan_cfg.trading_pairs, max_workers=10)
                data, illiquid = filter_illiquid(data, scan_cfg.min_volume_usd_24h)
                if illiquid:
                    st.info("Liquidity filter dropped: " + ", ".join(_asset(p) for p, _ in illiquid))
                if not data:
                    st.error("No assets passed the fetch + liquidity filter — try again.")
                    return
                results: dict[str, ScanResult] = {}
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futs = {ex.submit(_eval, df, scan_cfg): pair for pair, df in data.items()}
                    for f in as_completed(futs):
                        try:
                            results[futs[f]] = ScanResult(pair=futs[f], signal=f.result(), ohlcv_df=data[futs[f]])
                        except Exception:
                            pass
                if not results:
                    st.error("No assets could be evaluated — try again.")
                    return
                # single-elimination bracket — same comparison as the auto-scanner
                import random
                pairs = list(results.keys())
                random.shuffle(pairs)
                history = []
                round_no = 1
                results_ref = [len(pairs)]  # alive count, shown in the bracket feed
                while len(pairs) > 1:
                    nxt = []
                    total = (len(pairs) + 1) // 2
                    for i in range(0, len(pairs), 2):
                        if i + 1 >= len(pairs):
                            nxt.append(pairs[i])
                            history.append({"round": round_no, "match": len(history) + 1,
                                           "total_matches": total, "pair_a": pairs[i], "pair_b": "(bye)",
                                           "winner": pairs[i], "loser": "(bye)",
                                           "a_action": results[pairs[i]].signal.action, "a_conf": results[pairs[i]].signal.confidence,
                                           "b_action": "hold", "b_conf": 0})
                            on_progress(history[-1])
                            continue
                        pa, pb = pairs[i], pairs[i + 1]
                        ra, rb = results[pa], results[pb]
                        # CONTEST MODE (original rules): honest bracket — a strong
                        # sell can win and displays as EXIT / AVOID. The bot's
                        # tournament (scanner side) uses long_only=True instead.
                        winner, loser, reason = _compare_signals(ra, rb, long_only=False)
                        nxt.append(winner.pair)
                        results_ref[0] -= 1  # one more asset eliminated
                        history.append({"round": round_no, "match": len(history) + 1,
                                       "total_matches": total, "pair_a": pa, "pair_b": pb,
                                       "winner": winner.pair, "loser": loser.pair,
                                       "a_action": ra.signal.action, "a_conf": ra.signal.confidence,
                                       "b_action": rb.signal.action, "b_conf": rb.signal.confidence,
                                       "reason": reason})
                        on_progress(history[-1])
                    pairs = nxt
                    round_no += 1
                champ_pair = pairs[0]
                # deep analysis on champion
                enable_deep_analysis()
                champ_sig = _eval(data[champ_pair], cfg)
                disable_deep_analysis()
                champion = ScanResult(pair=champ_pair, signal=champ_sig, ohlcv_df=data[champ_pair])
                st.session_state.scan_results = [champion]
                st.session_state.scan_cfg = cfg
                st.session_state.tournament_history = history
                st.session_state["_tourney_fresh"] = True  # toast once per run, not every rerun
                if _HAS_DB:
                    try:
                        db.log_tournament_winner(
                            pair=champion.pair, action=champ_sig.action,
                            confidence=champ_sig.confidence, price=champ_sig.price,
                            entry_price=champ_sig.entry_price, stop_loss=champ_sig.stop_loss,
                            take_profit=champ_sig.take_profit, risk_reward=champ_sig.risk_reward,
                            regime=champ_sig.regime, reasons=[r[:200] for r in champ_sig.reasons[:5]],
                            scan_type="winners" if is_winners else "full")
                    except Exception:
                        pass
            except Exception as exc:
                st.error(f"Tournament failed: {exc}")
        disable_deep_analysis()

    results: list[ScanResult] = st.session_state.get("scan_results", [])
    history = st.session_state.get("tournament_history", [])
    if history:
        with st.expander(f"Bracket Log ({len(history)} matches)"):
            rnd = 0
            for m in history:
                if m["round"] != rnd:
                    rnd = m["round"]
                    st.markdown(f"**Round {rnd}**")
                st.markdown(f"✔ **{_asset(m['winner'])}** def. {_asset(m['loser'])}")

    if not results:
        st.markdown(plate("Tournament", lcd([
            "PRESS ▶ RUN BRACKET TO START THE TOURNAMENT",
            "<span class='dim'>54 assets · single elimination · liquidity-filtered</span>",
        ], tone="amber")), unsafe_allow_html=True)
        return

    champ = results[0]
    sig = champ.signal
    # green = green, everything else = red (yellow counted as red)
    col = {"buy": "#4be38a"}.get(sig.action, "#ff6b5e")
    # Never short: "sell" = exit a held long / stay out if flat
    act = {"buy": "BUY", "sell": "EXIT / AVOID"}.get(sig.action, "HOLD")

    block, day, thr, _, no_buy = current_threshold()
    passes = sig.confidence >= thr and not no_buy

    lines = [
        f"<span style='color:{col};font-size:26px;font-family:Audiowide;'>{_asset(champ.pair)} — {act}</span>",
        f"CONFIDENCE <span class='dim'>.......</span> {sig.confidence:.0f}%"
        f"  <span class='dim'>(auto-trade bar: {thr}% → </span>"
        f"<span style='color:{'#4be38a' if passes else '#ff6b5e'};'>{'PASSES' if passes else 'BELOW BAR'}</span><span class='dim'>)</span>",
    ]
    if sig.action == "sell":
        lines.append("<span class='dim'>Bearish champion — never a short: exit if held, stay out if flat.</span>")
    if sig.entry_price:
        lines.append(f"ENTRY <span class='dim'>..........</span> {_fmt_price(sig.entry_price)}"
                     f"   STOP <span class='dim'>.........</span> {_fmt_price(sig.stop_loss)}"
                     f"   TP <span class='dim'>...........</span> {_fmt_price(sig.take_profit)}"
                     f"   R:R 1:{sig.risk_reward or 0:.1f}")
        sizing = compute_position_sizing(sig, st.session_state.get("scan_cfg", cfg))
        if sizing:
            lines.append(f"SIZE <span class='dim'>...........</span> ${sizing.position_size_usd:,.0f}"
                         f"   RISK <span class='dim'>.........</span> ${sizing.risk_amount_usd:,.0f}"
                         f"   TP PROFIT <span class='dim'>....</span> ${sizing.potential_profit_usd:,.0f}")
            _chunk = contest_chunk(sizing.position_size_usd, cfg.challenge_balance)
            if _chunk:
                lines.append(f"CONTEST BUY <span class='dim'>.......</span> "
                             f"<span style='color:#4be38a;'>{_chunk[0]:.0%} (${_chunk[1]:,.0f})</span>"
                             f" <span class='dim'>— pick this chunk in the app (25/50/75/100% only)</span>")
    lines.append(f"REGIME <span class='dim'>..........</span> {sig.regime or '—'}"
                 f"   VOLATILITY <span class='dim'>....</span> {sig.volatility_rank or 0:.0f}/100"
                 f"   <span class='dim'>{'🟢' if passes else '🔴'} {day} {block}</span>")
    # bracket stats: proof the whole field was played
    n_assets = len({m["pair_a"] for m in history if m["pair_b"] != "(bye)"} |
                   {m["pair_b"] for m in history if m["pair_b"] != "(bye)"}) or 1
    lines.append(f"FIELD <span class='dim'>...........</span> {n_assets} assets · {len(history)} matches played"
                 f"   <span class='dim'>single elimination — champion never lost a match</span>")
    st.markdown(plate("The Pick — today's best trade", lcd(lines, tone="green" if passes else "red")),
                unsafe_allow_html=True)

    with st.expander("Why this pick"):
        for r in sig.reasons[:10]:
            st.markdown(f"- {r}")
        st.plotly_chart(render_chart(champ.ohlcv_df, sig, cfg), width="stretch", key="champ_chart")

    # FLOW: a passing pick gets a one-click handoff to Quick Check — but the
    # full bracket and results STAY VISIBLE. Manual runs are for seeing the
    # tournament; nothing auto-bounces you off this page.
    _fresh = st.session_state.pop("_tourney_fresh", None)
    if sig.action == "buy" and passes:
        _champ_asset = _asset(champ.pair)
        if _champ_asset in ASSETS:
            st.session_state["qc_asset"] = _champ_asset
            st.session_state["qc_hint"] = (f"🏆 {_champ_asset} is today's best pick — {sig.confidence:.0f}% sure. "
                                           f"Hit DEEP SCAN for the final yes/no.")
            if _fresh:
                try:
                    st.toast(f"🏆 {_champ_asset} is today's best pick — confirm it below",
                             icon="🏆", duration=12)
                    _play_sound("ding")
                except Exception:
                    pass
            if st.button(f"➡ CONFIRM {_champ_asset} IN QUICK CHECK — the final yes/no",
                         type="primary", width="stretch", key="champ_next_btn"):
                st.session_state["active_tab"] = "QUICK CHECK"
                st.rerun()
    elif _fresh:
        _why = (f"{_asset(champ.pair)} is only {sig.confidence:.0f}% sure — needs the bar"
                if sig.action == "buy" else
                f"best survivor {_asset(champ.pair)} is a sell — nobody's buying right now")
        try:
            st.toast(f"⛔ NO GOOD PICK this time — {_why}. "
                     f"Don't enter. Try again later or check a coin yourself in Quick Check.",
                     icon="⛔", duration=15)
            _play_sound("alarm")
        except Exception:
            pass

    # champion history
    if _HAS_DB:
        try:
            winners = db.get_tournament_winners(limit=15)
            if winners:
                rows = []
                for w in winners:
                    icon = "🟢" if w.action == "buy" else "🔴" if w.action == "sell" else "⏸"
                    t = w.timestamp
                    if t:
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=ZoneInfo("UTC"))
                        t = t.astimezone(MST)
                        ts = t.strftime("%d %b %I:%M%p")
                    else:
                        ts = "—"
                    rows.append(f"{icon} {ts} · {_asset(w.pair):<9} {w.action.upper():<4} "
                                f"{w.confidence:>3.0f}%  TP {_fmt_price(w.take_profit)}")
                st.markdown(plate("Past Picks", lcd(rows)), unsafe_allow_html=True)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════
#  QUICK CHECK MODULE — deep scan with CURRENT scanner logic
# ════════════════════════════════════════════════════════════════════════

def render_quick_check(cfg: TradingConfig, kraken):
    sb = _bridge()
    block, day, thr, _, no_buy = current_threshold()

    # ⚡ FAST $20 leaderboard — pick a wiggle, then deep-scan it
    try:
        from background_scanner import rotation_key_for_hour, _schedule_for_dow
        _n = _now_mst()
        fast = _fast_twenty(rotation_key_for_hour(_n.hour), _schedule_for_dow(_n.weekday()))
    except Exception:
        fast = []
    if fast:
        st.markdown(plate("⚡ FAST $20 — quickest wiggles right now", lcd([
            "<span class='dim'>ranked by how fast the $20 comes this hour — click one, then DEEP SCAN</span>",
            "   ".join(f"<b>{f['asset']}</b> ~{f['eta']:.1f}h"
                      + ("" if f["reliable"] else " <span class='dim'>(thin data)</span>")
                      for f in fast),
        ])), unsafe_allow_html=True)
        _fb = st.columns(len(fast))
        for i, f in enumerate(fast):
            with _fb[i]:
                if st.button(f"⚡ {f['asset']}", key=f"fast{i}", use_container_width=True):
                    st.session_state["qc_pick_next"] = f["asset"]
                    st.rerun()

    # flow hint: arrived from the tournament with a champion preloaded
    qc_hint = st.session_state.pop("qc_hint", None)
    if qc_hint:
        st.markdown(plate("", lcd([f"<span style='color:#4be38a;font-size:16px;'>{qc_hint}</span>"])),
                    unsafe_allow_html=True)

    # a FAST $20 click lands here (BEFORE the selectbox renders)
    if st.session_state.get("qc_pick_next"):
        st.session_state["qc_asset"] = st.session_state.pop("qc_pick_next")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        asset = st.selectbox("Pick a coin", ASSETS, index=ASSETS.index("BTC"), key="qc_asset")
    with c2:
        clicked = st.button("🔍 DEEP SCAN", type="primary", width="stretch", key="qc_btn")
    with c3:
        st.markdown(f"<div style='height:26px'></div>"
                    f"<span class='led a'><i></i>{day} {block} · floor {QC_SCALP_FLOOR}%</span>",
                    unsafe_allow_html=True)

    # ── run the scan on click; PERSIST the result in session state so it
    # survives the WATCH-button rerun (buttons reset on every rerun)
    if clicked:
        pair = f"{asset}/USD"
        with st.spinner(f"Deep scanning {asset}…"):
            try:
                from strategy import evaluate_strategy as _eval, enable_deep_analysis, disable_deep_analysis
                enable_deep_analysis()
                df = fetch_df(kraken, pair, cfg)
                sig = _eval(df, cfg)
                disable_deep_analysis()
                px = float(df["close"].iloc[-1])
                st.session_state["qc_result"] = {"asset": asset, "sig": sig, "df": df, "px": px}

                # ── every scan talks back — never dead ──────────────────
                # THE SCALP GATE (Aug 24 redesign, PUMP was the example):
                # (1) $20-pace — the +0.4% must be reachable within the
                #     rotation's allowance (resolve rule; never past
                #     midnight). Too slow = no green light, ever.
                # (2) conviction floor 60% — a real setup, not the old
                #     median bar (replay: sub-bar buys paid $20 at 80% vs
                #     72% for bar-passers — the bar added nothing).
                # (3) never during the 20:00-24:00 cooldown.
                # (4) chase guard — no green if price already ran >2% off
                #     the last hour's base (PENGU: 100% conviction, +3.6%
                #     off base at the top, -$44; PUMP: 79% at the base,
                #     +$133). Fresh wiggles only.
                # (TP ETA stays as display info in the exit verdict.)
                # dup-safe keeps old consoles alive across restarts — if this
                # console predates a background_scanner update, the cached
                # module lacks the new name. Reload from disk; never crash
                # the scan over a stale module.
                import background_scanner as _bs
                if not hasattr(_bs, "_hours_to_scalp"):
                    import importlib
                    _bs = importlib.reload(_bs)
                _resolve_cap = _bs._max_resolve_hours
                _scalp_eta = _bs._hours_to_scalp
                _s_h = _scalp_eta(f"{asset}/USD", float(px))
                # chase: distance above the last hour's low — MUST be 4 x 15m
                # candles. The deep-scan df is 1h candles: using its lows made
                # the window 4 HOURS, catching stale overnight dips and blocking
                # fresh-base entries (PUMP sat +0.2% off its 1h base but read
                # +3.7% "chased" against a 4h-old low). Fetch 15m explicitly —
                # same measure as the bot's chase guard.
                try:
                    _c15 = kraken.fetch_ohlcv(pair, interval_minutes=15, limit=5)
                    _base = min(c[3] for c in _c15[-4:])
                    _chase = (float(px) - _base) / _base * 100 if _base > 0 else 0.0
                except Exception:
                    _chase = 0.0
                # re-entry guard: a pair that just paid a big TAKE IT is an
                # exhausted move — re-entering rides the giveback
                _payout = _recent_big_payout(pair)
                st.session_state["qc_result"]["pace_h"] = _s_h
                st.session_state["qc_result"]["chase_pct"] = _chase
                st.session_state["qc_result"]["payout_block"] = _payout
                if (sig.action == "buy" and not no_buy
                        and sig.confidence >= QC_SCALP_FLOOR
                        and _payout is None
                        and _chase <= QC_CHASE_PCT
                        and not (_s_h is not None and _s_h > _resolve_cap())):
                    # AUTO-WATCH on green light: the deep scan IS the entry.
                    # The user's system: green → all in, scalp to +$20, out,
                    # re-enter small, green again → buy more. Watch records
                    # full size so the $-counting is exact; adjust if they
                    # re-entered smaller.
                    _chunk = (1.00, cfg.challenge_balance)
                    _chunk_txt = f"buy 100% (${cfg.challenge_balance:,.0f}). "
                    try:
                        w = _load_watch()
                        if pair not in w:
                            w[pair] = {"entry": float(px), "high": float(px),
                                       "size": float(cfg.challenge_balance),
                                       "added": _now_mst().isoformat()}
                            _save_watch(w)
                            logger.info("AUTO-WATCH: %s added on green light (entry %s)", pair, px)
                            try:
                                st.toast(f"✅ GO — {asset}: {_chunk_txt}I count the $20s.",
                                         icon="✅", duration=20)
                                _play_sound("ding")
                                _speak(f"Go! {asset}! Buy 100 percent! I count the twenties!")
                            except Exception:
                                pass
                            # FLOW ADVANCE: position entered → take the user to the watch
                            st.session_state["last_green"] = {
                                "asset": asset, "pair": pair, "px": float(px),
                                "stop": sig.stop_loss, "tp": sig.take_profit,
                                "chunk": _chunk, "ts": time.time(),
                            }
                            st.session_state["active_tab"] = "EXIT CHECK"
                            st.rerun()
                        else:
                            try:
                                st.toast(f"✅ {asset} — already watching it", icon="✅", duration=12)
                                _play_sound("ding")
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.debug("auto-watch add failed: %s", exc)
                else:
                    if (sig.action == "buy" and not no_buy
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _payout is not None):
                        try:
                            st.toast(f"⛔ JUST PAID ${_payout} — {asset} is an exhausted move. "
                                     f"Let it build a fresh base before re-entering. Wait.",
                                     icon="⛔", duration=18)
                            _play_sound("alarm")
                            _speak(f"Too early! {asset} just paid! Wait for a fresh base!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _chase > QC_CHASE_PCT):
                        try:
                            st.toast(f"⛔ ALREADY RAN — {asset}: +{_chase:.1f}% off the last hour's base. "
                                     f"The move's old — you'd pay for the part that already happened. "
                                     f"Wait for the next wiggle.",
                                     icon="⛔", duration=18)
                            _play_sound("alarm")
                            _speak(f"Too late! {asset} already ran! Wait for the next wiggle!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _s_h is not None and _s_h > _resolve_cap()):
                        _slow_txt = (f"~{_s_h:.0f}h" if _s_h >= 3.5 else "3h+")
                        act_txt = f"too slow — {_slow_txt} to the $20"
                        try:
                            st.toast(f"⛔ TOO SLOW — {asset}: {act_txt}. Not a scalp. Skip it.",
                                     icon="⛔", duration=15)
                            _play_sound("alarm")
                            _speak(f"Too slow! {asset}! Skip it!", rate=1)
                        except Exception:
                            pass
                    elif sig.action == "buy" and no_buy:
                        try:
                            st.toast(f"⛔ COOLDOWN (8pm-midnight) — no new buys. {asset} waits.",
                                     icon="⛔", duration=15)
                            _play_sound("alarm")
                            _speak(f"Cooldown! No buys!", rate=1)
                        except Exception:
                            pass
                    else:
                        act_txt = {"buy": f"{sig.confidence:.0f}% — needs {QC_SCALP_FLOOR}% (weak setup)",
                                   "sell": "chart's red",
                                   "hold": "no setup"}[sig.action]
                        try:
                            st.toast(f"⛔ NO — {asset}: {act_txt}. Skip it.",
                                     icon="⛔", duration=15)
                            _play_sound("alarm")
                            _speak(f"No! {asset}! Skip it!", rate=1)
                        except Exception:
                            pass
            except Exception as exc:
                disable_deep_analysis()
                st.error(f"Scan failed for {asset}: {exc}")

    result = st.session_state.get("qc_result")
    if not result or result.get("asset") != asset:
        st.markdown(plate("Quick Check", lcd([
            "PICK A COIN AND HIT DEEP SCAN",
            "<span class='dim'>you'll get one clear answer: green light to enter, or red = skip it</span>",
        ])), unsafe_allow_html=True)
        return

    sig = result["sig"]
    df = result["df"]
    px = result["px"]
    pair = f"{asset}/USD"

    # liquidity check (same rule as tournament)
    try:
        qv = float((df["volume"].tail(24) * df["close"].tail(24)).sum())
    except Exception:
        qv = 0.0
    liquid = qv >= cfg.min_volume_usd_24h

    # green = green, everything else = red (yellow is counted as red)
    col = {"buy": "#4be38a"}.get(sig.action, "#ff6b5e")
    # Never short: "sell" = exit a held long / stay out if flat
    act = {"buy": "BUY", "sell": "EXIT / AVOID"}.get(sig.action, "HOLD")
    # scalp-gate verdict (Aug 24 redesign): the $20 pace decides, conviction
    # floors at QC_SCALP_FLOOR, cooldown always blocks, chase guard rejects
    # stale moves. The old median-bar LED is gone — it rejected 79% PUMP
    # (paid $20 x3) and passed 100% BTC (negative all day). Replay: the bar
    # predicts nothing for scalps.
    pace_h = result.get("pace_h")
    chase_pct = result.get("chase_pct", 0.0)
    payout_block = result.get("payout_block")
    from background_scanner import _max_resolve_hours as _rc
    pace_ok = pace_h is None or pace_h <= _rc()
    fresh = chase_pct <= QC_CHASE_PCT
    passes = (sig.action == "buy" and not no_buy
              and sig.confidence >= QC_SCALP_FLOOR and pace_ok and fresh
              and payout_block is None)
    momentum_capped = any("momentum ceiling" in r.lower() for r in sig.reasons)

    k = (
        knob(sig.confidence, 0, 100, "Confidence", lambda v: f"{v:.0f}%") +
        knob(sig.volatility_rank or 0, 0, 100, "Volatility", lambda v: f"{v:.0f}") +
        knob(qv / 1e6, 0, 500, "24h Volume", lambda v: f"${v:,.0f}M") +
        knob(sig.risk_reward or 0, 0, 5, "R:R", lambda v: f"1:{v:.1f}")
    )
    st.markdown(plate(f"Quick Check — {asset}", f'<div class="knob-row">{k}</div>'), unsafe_allow_html=True)

    _pace_txt = (f"$20 IN ~{pace_h:.1f}H" if pace_h is not None else "$20 PACE: NO DATA")
    _green_txt = ("SCALP GREEN LIGHT" if passes else
                  ("COOLDOWN — NO BUYS" if no_buy else
                   (f"NO GREEN — JUST PAID ${payout_block}, WAIT" if payout_block is not None else
                    (f"NO GREEN — ALREADY RAN +{chase_pct:.1f}%" if not fresh else
                     (f"NO GREEN — TOO SLOW ({pace_h:.1f}H)" if not pace_ok else
                      f"NO GREEN — NEEDS {QC_SCALP_FLOOR}%+")))))
    status_leds = (
        led("LIQUID" if liquid else f"ILLIQUID < ${cfg.min_volume_usd_24h/1e6:.0f}M", "g" if liquid else "r") +
        led(_pace_txt, "g" if pace_ok else "r") +
        led(f"FRESH +{chase_pct:.1f}% OFF BASE" if fresh else f"CHASED +{chase_pct:.1f}% — STALE MOVE",
            "g" if fresh else "r") +
        led(_green_txt, "g" if passes else "r") +
        led("MOMENTUM CEILING BLOCK" if momentum_capped else "ENTRY ALLOWED", "r" if momentum_capped else "g")
    )

    lines = [
        f"<span style='color:{col};font-size:26px;font-family:Audiowide;'>{asset} — {act} {sig.confidence:.0f}%</span>",
        f"LIVE <span class='dim'>............</span> {_fmt_price(px)}   REGIME <span class='dim'>......</span> {sig.regime or '—'}",
    ]
    if sig.action == "sell":
        lines.insert(1, "<span class='dim'>Bearish read — this is never a short: EXIT if you hold it, otherwise stay out.</span>")
    if sig.entry_price:
        sizing = compute_position_sizing(sig, cfg)
        lines.append(f"ENTRY <span class='dim'>...........</span> {_fmt_price(sig.entry_price)}"
                     f"   STOP <span class='dim'>........</span> {_fmt_price(sig.stop_loss)}"
                     f"   TP <span class='dim'>..........</span> {_fmt_price(sig.take_profit)}")
        if sizing:
            lines.append(f"SIZE <span class='dim'>............</span> ${sizing.position_size_usd:,.0f}"
                         f"   RISK <span class='dim'>........</span> ${sizing.risk_amount_usd:,.0f}"
                         f"   PROFIT IF TP <span class='dim'>..</span> ${sizing.potential_profit_usd:,.0f}")
            _chunk = contest_chunk(sizing.position_size_usd, cfg.challenge_balance)
            if _chunk:
                lines.append(f"CONTEST BUY <span class='dim'>......</span> "
                             f"<span style='color:#4be38a;'>{_chunk[0]:.0%} (${_chunk[1]:,.0f})</span>"
                             f" <span class='dim'>— pick this chunk in the app (25/50/75/100% only)</span>")
    st.markdown(plate("Readout", status_leds + "<div style='height:8px'></div>" + lcd(lines)), unsafe_allow_html=True)

    # ── Liquidity & Execution — advisory readout (contest-side info only) ──
    # Reads the live order book and shows what the bot's tier system would do.
    # NEVER writes to the bridge, never executes — manual console stays advisory.
    try:
        from execution_tiers import liquidity_snapshot
        _notional = sizing.position_size_usd if (sig.entry_price and sizing) else 400.0
        snap = liquidity_snapshot(kraken, pair, _notional)
        if snap.get("ok"):
            _b = snap["book"]
            _tn, _tname, _tcons = snap["tier"]
            _sc = snap["score"]
            _slip = snap.get("slippage_pct")
            _bd = snap.get("breakdown", {})
            _liq_leds = (
                led(f"HEALTH {_sc}/100", "g" if _sc >= 60 else ("a" if _sc >= 45 else "r")) +
                led(f"{_tname.upper()} · {_tcons:.0f}% OF BOOK", "g" if _tn <= 2 else ("a" if _tn <= 3 else "r")) +
                led(f"EST SLIP {_slip:.2f}%" if _slip is not None else "EST SLIP —",
                    "g" if (_slip or 0) <= 0.3 else "r") +
                led("BLACKLISTED" if snap.get("blacklisted") else "MARKET OPEN",
                    "r" if snap.get("blacklisted") else "off")
            )
            _liq_lines = [
                f"BOOK TOP-10 <span class='dim'>......</span> ${_b['ask_tob']:,.0f} asks · ${_b['bid_tob']:,.0f} bids"
                f"   SPREAD <span class='dim'>...</span> {_b['spread_bps']:.1f} bps",
                f"SIZE REQUESTED <span class='dim'>...</span> ${_notional:,.0f}"
                f"   T2 CAPPED <span class='dim'>......</span> ${snap['capped_usd']:,.0f} (20% of book)",
                f"METHOD <span class='dim'>............</span> {snap['method']}"
                f"   EST TIME <span class='dim'>....</span> {snap['est_time']}",
                f"SCORE BREAKDOWN <span class='dim'>....</span> spread {_bd.get('spread', '—')} · "
                f"depth {_bd.get('depth_1%', '—')}/{_bd.get('depth_2%', '—')}/{_bd.get('depth_5%', '—')} · "
                f"vol {_bd.get('volatility', '—')} · maker {_bd.get('maker', '—')}",
            ]
            st.markdown(plate("Liquidity & Execution — advisory",
                              _liq_leds + "<div style='height:8px'></div>" + lcd(_liq_lines)),
                        unsafe_allow_html=True)
        elif snap.get("reason"):
            st.markdown(plate("Liquidity & Execution — advisory",
                              lcd([f"BOOK UNAVAILABLE — {snap['reason']}"])), unsafe_allow_html=True)
    except Exception:
        pass

    why = [f"- {r}" for r in sig.reasons[:9]]
    st.markdown(plate("Why", "<br>".join(why).replace("-", "·", 1) if False else
                      "<div style='font-family:Share Tech Mono,monospace;font-size:13.5px;color:#d8ccb4;'>" +
                      "<br>".join(f"◦ {r}" for r in sig.reasons[:9]) + "</div>"), unsafe_allow_html=True)

    st.plotly_chart(render_chart(df, sig, cfg), width="stretch", key="qc_chart")

    # ── green light already auto-watched the coin — these are just tweaks ──
    w1, w2 = st.columns(2)
    with w1:
        already = pair in _load_watch()
        if already:
            st.caption(f"👀 {asset} is on the watch — AUTO-WATCH in EXIT CHECK "
                       f"will tell you when to sell. Nothing else to do.")
        else:
            if st.button(f"👁 WATCH {asset} anyway", key="qc_watch", width="stretch",
                         help="Green light adds coins automatically — this is for "
                              "entering on your own conviction"):
                w = _load_watch()
                entry_used = sig.entry_price or px
                w[pair] = {"entry": float(entry_used), "high": float(px), "added": _now_mst().isoformat()}
                _save_watch(w)
                st.success(f"◆ {asset} added to the exit auto-watch (entry {_fmt_price(entry_used)}). "
                           f"Flip on AUTO-WATCH in EXIT CHECK for 1-min exit alerts.")
    with w2:
        entry_adj = st.number_input("entry $", min_value=0.0, value=None,
                                    step=0.0001, key="qc_watch_entry",
                                    help="your actual fill — fixes the $-counting")
        size_adj = st.number_input("size $", min_value=0.0, value=None,
                                   step=100.0, key="qc_watch_size",
                                   help="what you actually bought — re-entered small? set it here")
        if st.button("✎ UPDATE", key="qc_watch_upd", width="stretch"):
            w = _load_watch()
            if pair not in w:
                w[pair] = {"entry": float(entry_adj or px), "high": float(px),
                           "added": _now_mst().isoformat()}
            if entry_adj:
                w[pair]["entry"] = float(entry_adj)
            if size_adj:
                w[pair]["size"] = float(size_adj)
            _save_watch(w)
            st.success(f"{asset}: entry {_fmt_price(w[pair]['entry'])}, "
                       f"size ${w[pair].get('size', 0):,.0f}")

    # ADVISORY ONLY — contest use. No buttons write to the bridge; the
    # auto-scanner is the sole source of live trading signals.
    st.markdown(plate("Advisory", lcd([
        "CONTEST ADVISORY — nothing sent to the Trader, bridge, or trade history.",
        f"<span class='dim'>Use these levels for your manual Funded-account trade. "
        f"The auto-scanner trades the spot account independently.</span>",
    ])), unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════
#  EXIT CHECK MODULE — the scanner's exit logic, on demand
# ════════════════════════════════════════════════════════════════════════

def _desktop_notify(title: str, message: str):
    """Windows balloon-tip desktop notification (same as the scanner's)."""
    try:
        import subprocess
        safe_title = title.replace("'", "''").replace('"', '`"')
        safe_msg = message.replace("'", "''").replace('"', '`"')
        ps_cmd = (
            f"[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
            f"$n = New-Object System.Windows.Forms.NotifyIcon; "
            f"$n.Icon = [System.Drawing.SystemIcons]::Warning; "
            f"$n.Visible = $true; "
            f"$n.ShowBalloonTip(15000, '{safe_title}', '{safe_msg}', "
            f"[System.Windows.Forms.ToolTipIcon]::Warning); "
            f"Start-Sleep -Seconds 15; $n.Dispose()"
        )
        subprocess.Popen(['powershell', '-WindowStyle', 'Hidden', '-Command', ps_cmd],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.debug("desktop notify failed: %s", exc)


# ── Contest exit profile: BUY BIG, GET OUT FAST ────────────────────────
# Same engine and data the bot uses — but tighter exits. The contest
# rewards speed, not patience. The bot keeps its own slower profile
# (8% profit guard, 75/60 sell bar) — this is for contest positions only.
CONTEST_TRAIL_PCT = 4.0   # profit guard pulls the plug 4% off the peak
CONTEST_SELL_BAR = 50     # sell as soon as the chart is 50% bearish


def _cloned_trailing(pair: str, live: float, entry: float | None, sb,
                     trail_pct: float = 8.0) -> tuple[float, bool]:
    """CLONED trailing-stop tool — advisory only, NEVER writes to the bridge.

    Reads the auto system's tracked high (read-only) to stay in sync for spot
    positions, tracks its own session max on top (so it works for any asset,
    including contest positions the bridge knows nothing about). Winner-only:
    only triggers when the tracked high is above the reference entry.
    trail_pct: 8.0 = the bot's patient guard · CONTEST_TRAIL_PCT = get out fast.
    """
    TRAIL_PCT = trail_pct
    bridge_high = None
    try:
        bridge_high = sb.get_trailing_high(pair)  # read-only peek at auto tracking
    except Exception:
        pass
    hs = st.session_state.setdefault("_manual_highs", {})
    high = max(bridge_high or 0.0, hs.get(pair, 0.0), live)
    hs[pair] = high
    ref = entry or bridge_high or high
    triggered = bool(ref and high > ref and live <= high * (1 - TRAIL_PCT / 100))
    return high, triggered


def _run_exit_logic(kraken, pair: str, cfg: TradingConfig, sb,
                    entry_override: float | None = None,
                    watch_entry: dict | None = None,
                    contest: bool = False) -> dict:
    """Run the scanner's exit LOGIC on one pair (cloned tools — read-only).

    Advisory only: does NOT write trailing highs, signals, or anything else
    to the bridge. The background scanner is the sole writer for live trading.

    contest=True → the fast contest profile (4% profit guard, 50% sell bar):
    buy big, get out fast. contest=False → mirrors the bot's own exits.

    entry_override: your manual/contest entry price (no bridge record exists).
    watch_entry: persistent contest-watch dict {entry, high} — the tracked
    high survives refreshes via contest_watch.json.
    """
    from strategy import evaluate_strategy as _eval
    df = fetch_df(kraken, pair, cfg)
    sig = _eval(df, cfg)
    live = float(df["close"].iloc[-1])

    entry = None
    try:
        for p in sb.get_open_positions():  # read-only (real spot positions)
            if p["pair"] == pair:
                entry = p.get("execution_price") or p.get("entry_price")
                break
    except Exception:
        pass
    if entry is None and entry_override:
        entry = float(entry_override)

    trail_pct = CONTEST_TRAIL_PCT if contest else 8.0
    if watch_entry is not None:
        # persistent contest tracking: high survives refreshes
        high = max(float(watch_entry.get("high") or 0.0), live)
        watch_entry["high"] = high
        ref = entry or high
        trail_hit = bool(ref and high > ref and live <= high * (1 - trail_pct / 100))
    else:
        high, trail_hit = _cloned_trailing(pair, live, entry, sb, trail_pct=trail_pct)

    is_momentum = any("MOMENTUM RIDER" in r for r in sig.reasons)
    if contest:
        sell_thr = CONTEST_SELL_BAR
    else:
        sell_thr = 60 if is_momentum else 75
    strategy_sell = sig.action == "sell" and sig.confidence >= sell_thr
    exhaustion = any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy"
    rider_exit = any("exit signal" in r.lower() for r in sig.reasons)

    reasons = []
    if trail_hit and high:
        reasons.append(f"down {(high-live)/high*100:.1f}% from the top")
    if strategy_sell:
        reasons.append(f"trend broke ({sig.confidence:.0f}% red)")
    if exhaustion:
        reasons.append("move's done")
    if rider_exit:
        reasons.append("out of steam")

    return {
        "pair": pair, "live": live, "high": high, "entry": entry, "signal": sig,
        "trail_hit": trail_hit, "is_momentum": is_momentum, "sell_thr": sell_thr,
        "reasons": reasons, "exit": bool(reasons), "contest": contest,
    }


def _light_check(kraken, cfg: TradingConfig, sb, held: dict, interval_s: int) -> bool:
    """One watch cycle: exit logic on every position, popups + sounds on ANY
    page, exited-position cleanup, flow advance. Renders nothing — the panel
    reads st.session_state['watch_status']. Dedupes by time so it runs at
    most once per interval no matter how often pages change. Returns True if
    a check actually ran."""
    if (time.time() - st.session_state.get("last_light_check", 0)) < (interval_s - 2):
        return False
    st.session_state["last_light_check"] = time.time()

    contest_watch = _load_watch()
    watch_status, alerts = [], {}
    for a, pair in held.items():
        try:
            v = _run_exit_logic(kraken, pair, cfg, sb)
            v["kind"] = "wallet"
            watch_status.append(v)
            if v["exit"]:
                alerts[pair] = "; ".join(v["reasons"])
        except Exception:
            continue
    for pair, w in contest_watch.items():
        if pair in held:
            continue
        try:
            v = _run_exit_logic(kraken, pair, cfg, sb,
                                entry_override=w.get("entry"),
                                watch_entry=w, contest=True)
            v["kind"] = "contest"
            watch_status.append(v)
            if v["exit"]:
                alerts[pair] = "; ".join(v["reasons"])
        except Exception:
            continue
    if contest_watch:
        _save_watch(contest_watch)  # persist updated tracked highs

    st.session_state["exit_alerts"] = alerts
    st.session_state["watch_status"] = watch_status

    # ── TAKE IT: the $20 call (user's scalp system) ──────────────────
    # Green → all in → out at +$20 → re-enter small → repeat. Every +$20
    # threshold crossed gets a popup (voice comes from the scanner).
    # Resets when back near entry — a re-entry starts a fresh count.
    try:
        if _load_console_state().get("scalp_on", True):
            take_level = st.session_state.setdefault("take_level", {})
            for v in watch_status:
                if v["kind"] != "contest":
                    continue
                pair = v["pair"]
                live = v.get("live")
                entry_px = v.get("entry")
                if not live or not entry_px:
                    continue
                size = float(contest_watch.get(pair, {}).get("size") or 2500.0)
                profit_usd = (live / entry_px - 1) * size
                level = int(profit_usd // 20) if profit_usd > 0 else 0
                if profit_usd < 10.0:
                    take_level.pop(pair, None)
                elif level > take_level.get(pair, 0):
                    take_level[pair] = level
                    asset = _asset(pair)
                    try:
                        st.toast(f"💰 TAKE IT — {asset} +${level * 20}",
                                 icon="💰", duration=15)
                        _play_sound("ding")
                        # voice comes from the scanner (server-side)
                    except Exception:
                        pass
    except Exception:
        pass

    # ── popups: fire on every page — this is the light ────────────────
    contest_rows = [v for v in watch_status if v["kind"] == "contest"]
    try:
        if any(v["exit"] for v in contest_rows):
            sellers = [f"{_asset(v['pair'])} ({v['reasons'][0]})"
                       for v in contest_rows if v["exit"]]
            st.toast("🚨 SELL NOW: " + " · ".join(sellers), icon="🚨", duration=20)
            _play_sound("alarm")
            # voice comes from the scanner (server-side) — no double-talking
        elif contest_rows:
            parts = []
            for v in contest_rows:
                if v.get("entry") and v.get("live"):
                    pct = (v["live"] / v["entry"] - 1) * 100
                    size = float(contest_watch.get(v["pair"], {}).get("size") or 2500.0)
                    usd = pct / 100.0 * size
                    parts.append(f"{_asset(v['pair'])} ${usd:+,.0f}")
                else:
                    parts.append(_asset(v["pair"]))
            st.toast("🟢 HOLD: " + " · ".join(parts), icon="✅", duration=10)
            _play_sound("tick")
        else:
            st.toast("🟢 no trades yet", icon="👀", duration=10)
            _play_sound("tick")
    except Exception:
        pass

    # exited contest positions drop off by themselves (Kraken's dashboard
    # owns the trade history — we don't keep a copy)
    exited = [v["pair"] for v in watch_status if v["kind"] == "contest" and v["exit"]]
    if exited:
        for p in exited:
            contest_watch.pop(p, None)
        _save_watch(contest_watch)
        st.session_state["flow_exit_at"] = time.time()  # advance later, after the red light has shown

    # flow advance: board cleared after an exit → back to hunting.
    # Waits one light cycle past the exit so the red light has been seen.
    _exit_at = st.session_state.get("flow_exit_at")
    if (_exit_at and not contest_watch and (time.time() - _exit_at) > 65):
        st.session_state["flow_exit_at"] = None
        st.session_state["active_tab"] = "TOURNAMENT"
        try:
            st.toast("✅ Trade done — now let's find the next one", icon="🎯", duration=12)
            _play_sound("ding")
            _speak("Trade done! Let's find the next one!")
        except Exception:
            pass
    return True


def _render_light_panel(interval: str):
    """The always-visible light panel — rendered from the last check."""
    watch_status = st.session_state.get("watch_status") or []
    any_alert = any(v["kind"] == "contest" and v["exit"] for v in watch_status)
    n_wallet = sum(1 for v in watch_status if v["kind"] == "wallet")
    lines = [f"<span class='dim'>{_now_mst().strftime('%I:%M %p')} · every {interval} · green = hold, red = sell</span>"]
    for v in watch_status:
        if v["kind"] != "contest":
            continue
        asset = _asset(v["pair"])
        entry = v.get("entry")
        live = v.get("live")
        size = float(_load_watch().get(v["pair"], {}).get("size") or 2500.0) if entry and live else 0.0
        pnl_html = ""
        if entry and live:
            pnl = (live / entry - 1) * 100
            usd = pnl / 100.0 * size
            pnl_html = (f' · <span style="color:{"#4be38a" if pnl >= 0 else "#ff6b5e"};">'
                        f'${usd:+,.0f}</span>')
        if v["exit"]:
            lines.append(f"<span style='color:#ff6b5e;font-size:16px;'>◆ {asset} — SELL NOW — {v['reasons'][0]}</span>")
        else:
            bail = v.get("high", 0) * (1 - CONTEST_TRAIL_PCT / 100) if v.get("high") else None
            bail_txt = f" · out at {_fmt_price(bail)}" if bail else ""
            lines.append(f"<span style='color:#4be38a;font-size:16px;'>◆ {asset}</span> {pnl_html}"
                         f"{bail_txt}")
    if n_wallet:
        lines.append(f"<span class='dim'>bot trades: {n_wallet} (watched separately)</span>")
    st.markdown(plate("MY TRADES", lcd(lines, tone="red" if any_alert else "green")),
                unsafe_allow_html=True)
    if any_alert:
        st.error("🚨 SELL NOW — on the contest app.")


def render_auto_watch(cfg: TradingConfig, kraken):
    """THE LIGHT — global (mounted on every page). A check fires whenever
    it's due: on any page navigation OR on the interval timer while you sit
    still. Popups + sounds land on every page either way."""
    sb = _bridge()
    wallet = wallet_snapshot(kraken)
    held = {a: f"{a}/USD" for a in sorted(wallet["holdings"].keys())}

    _cs = _load_console_state()
    watch_on = st.toggle("🔔 AUTO-WATCH — every minute: green ALL GOOD / red GET OUT NOW",
                         value=st.session_state.get("watch_on", _cs.get("watch_on", True)),
                         key="watch_toggle")
    if watch_on != _cs.get("watch_on", True):
        _cs["watch_on"] = watch_on
        _save_console_state(_cs)
    if watch_on:
        st.session_state["watch_on"] = True
        wcol1, wcol2 = st.columns([1, 3])
        with wcol1:
            interval = st.selectbox("Check every", ["1 min", "2 min", "5 min"], index=0, key="watch_interval")
        scalp_on = st.toggle("💰 TAKE IT — she calls every +$20",
                             value=st.session_state.get("scalp_on", _cs.get("scalp_on", True)),
                             key="scalp_toggle",
                             help="The scalp system: green → all in → out at +$20 → repeat. "
                                  "She counts the $20s out loud and pops a TAKE IT every "
                                  "threshold. Resets near entry.")
        if scalp_on != _cs.get("scalp_on", True):
            _cs["scalp_on"] = scalp_on
            _save_console_state(_cs)
        voice_on = st.toggle("🔊 HER VOICE — talks every minute, even with the browser closed",
                             value=st.session_state.get("voice_on_ui", _cs.get("voice_on", True)),
                             key="voice_toggle",
                             help="Zira announces your positions every minute from the scanner "
                                  "process — she keeps talking whether or not this browser "
                                  "tab is open.")
        if voice_on != _cs.get("voice_on", True):
            _cs["voice_on"] = voice_on
            _save_console_state(_cs)
        interval_s = {"1 min": 60, "2 min": 120, "5 min": 300}[interval]
        interval_ms = interval_s * 1000

        # check NOW if due — fires on every page change, popup follows you
        _light_check(kraken, cfg, sb, held, interval_s)
        _render_light_panel(interval)

        # ── SOLD buttons: the scalper's re-entry loop ──────────────────
        # Took the $20? Tap SOLD — the position clears and the next green
        # light re-adds it fresh at the new entry. One tap between rounds.
        _cw_sold = _load_watch()
        if _cw_sold:
            _scols = st.columns(min(len(_cw_sold), 4))
            for _i, (_sp, _sw) in enumerate(list(_cw_sold.items())):
                with _scols[_i % len(_scols)]:
                    if st.button(f"✔ SOLD {_asset(_sp)}", key=f"sold_{_sp.replace('/', '_')}",
                                 help="Clear it — you're out. Next green light re-adds fresh."):
                        _cw_sold.pop(_sp)
                        _save_watch(_cw_sold)
                        st.session_state.get("take_level", {}).pop(_sp, None)
                        try:
                            st.toast(f"✔ {_asset(_sp)} cleared — next green light re-adds it",
                                     icon="✔", duration=8)
                        except Exception:
                            pass
                        st.rerun()

        # ── contest watch list: live positions only, no history ────────
        contest_watch = _load_watch()
        n_contest = len(contest_watch)
        with st.expander(f"◆ My contest positions ({n_contest}) — add / remove", expanded=False):
            if contest_watch:
                for pair, w in contest_watch.items():
                    rm_col, info_col = st.columns([1, 5])
                    _size = w.get("size")
                    _size_txt = f" · ${_size:,.0f} size" if _size else ""
                    info_col.markdown(f"**◆ {_asset(pair)}** — entry {_fmt_price(w.get('entry'))}{_size_txt}")
                    if rm_col.button("✕", key=f"rm_{pair.replace('/', '_')}", help="Remove from watch"):
                        contest_watch.pop(pair)
                        _save_watch(contest_watch)
                        st.rerun()
            else:
                st.caption("Nothing being watched. A green light on a QUICK CHECK deep scan "
                           "puts the coin here automatically — or add one by hand:")
            add0, add1, add2, add3 = st.columns([1, 2, 1, 1])
            _chunk_opts = {"25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.00}
            new_chunk = add0.selectbox("Size", list(_chunk_opts), index=1, key="watch_add_size",
                                       help="How much you bought in the contest app — sets the $ size "
                                            "for P&L and scalp alerts")
            new_asset = add1.selectbox("Asset", ASSETS, key="watch_add_asset")
            new_entry = add2.number_input("Entry $ (blank = live)", min_value=0.0,
                                          value=None, step=0.0001, key="watch_add_entry")
            if add3.button("◆ ADD", type="primary", width="stretch", key="watch_add_btn"):
                npair = f"{new_asset}/USD"
                live = live_price(npair) or float(new_entry or 0)
                if live:
                    contest_watch[npair] = {
                        "entry": float(new_entry) if new_entry else live,
                        "high": live,
                        "size": _chunk_opts[new_chunk] * cfg.challenge_balance,
                        "added": _now_mst().isoformat(),
                    }
                    _save_watch(contest_watch)
                    st.success(f"◆ {new_asset} added to the watch (entry "
                               f"{_fmt_price(contest_watch[npair]['entry'])}, "
                               f"${contest_watch[npair]['size']:,.0f} size)")
                    st.rerun()

        st.caption("Leave this tab open — the watch runs in your browser. "
                   "It just watches and alerts. It can't touch your money.")

        # timer: keeps the checks coming while you sit still on one page
        @st.fragment(run_every=interval_ms)
        def _light_tick():
            if _light_check(kraken, cfg, sb, held, interval_s):
                try:
                    st.rerun(scope="app")  # refresh the global panel
                except TypeError:
                    st.rerun()

        _light_tick()
    else:
        st.session_state["watch_on"] = False
        st.session_state.pop("exit_alerts", None)


def _time_to_goal(pair: str, live: float, tp):
    """ETA to the take-profit using the Volatility Engine's rotation pace
    (same engine the bot uses). Returns (pct_remaining, hours) or None."""
    if not tp or not live or tp <= live:
        return None
    remaining_pct = (tp - live) / live * 100
    if remaining_pct <= 0:
        return None
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        if not doc:
            return None
        n = _now_mst()
        rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
        if rot is None:
            return None
        sched = "weekend" if n.weekday() >= 5 else "weekday"
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot)
                or doc.get("median", {}).get(sched, {}).get(rot))
        avg = float(cell.get("avg_move") or 0) if cell else 0
        if avg <= 0:
            return None
        sessions = remaining_pct / avg
        return remaining_pct, sessions * 4.0
    except Exception:
        return None


def _fmt_hours(hours: float) -> str:
    if hours < 1:
        return "under an hour"
    if hours < 2:
        return "about an hour"
    if hours < 48:
        return f"about {hours:.0f} hours"
    return f"about {hours / 24:.0f} days"


def render_exit_check(cfg: TradingConfig, kraken):
    sb = _bridge()
    # Source of truth: what the WALLET actually holds (reconciled), falling
    # back to the full asset list when flat. Stale bridge records can't leak in.
    wallet = wallet_snapshot(kraken)
    held = {a: f"{a}/USD" for a in sorted(wallet["holdings"].keys())}

    # Full asset list — pick any coin to check (held bot coins float to top
    # for convenience, but the contest workflow is: check any coin, any time).
    held_keys = list(held.keys())
    all_options = held_keys + [a for a in ASSETS if a not in held_keys]
    asset = st.selectbox(
        "Check a coin",
        all_options,
        index=0,  # first held asset when holding, else first of the list
        format_func=lambda a: f"{a}  (held)" if a in held_keys else a,
        key="ec_asset",
    )
    pair = f"{asset}/USD"

    c1, c2 = st.columns([1, 4])
    with c1:
        clicked = st.button("🚪 SHOULD I SELL?", type="primary", width="stretch", key="ec_btn")

    # count the manual checks — the light already does this every minute
    _cs_ec = _load_console_state()
    _n_checks = int(_cs_ec.get("exit_checks", 0))
    if clicked:
        _n_checks += 1
        _cs_ec["exit_checks"] = _n_checks
        _save_console_state(_cs_ec)
    if _n_checks:
        st.caption(f"you've asked {_n_checks} time{'s' if _n_checks != 1 else ''} — she checks "
                   f"every minute on her own, so you don't have to")

    st.caption("Same brain the auto-watch uses — answers in plain English. "
               "Contest rules: quick 4% profit guard, sells at 50% bearish (buy big, get out fast).")

    if not clicked:
        st.markdown(plate("Should I Sell?", lcd([
            "PICK A COIN AND PRESS THE BUTTON",
            "<span class='dim'>you'll get one clear answer: hold or sell</span>",
        ])), unsafe_allow_html=True)
        return

    with st.spinner(f"Checking {asset}…"):
        try:
            from strategy import evaluate_strategy as _eval
            df = fetch_df(kraken, pair, cfg)
            sig = _eval(df, cfg)
            live = float(df["close"].iloc[-1])
        except Exception as exc:
            st.error(f"Check failed for {asset}: {exc}")
            return

    # entry from bridge if it's a real spot position (read-only; contest
    # positions aren't in the bridge and stay reference-free)
    entry = None
    try:
        for p in sb.get_open_positions():
            if p["pair"] == pair:
                entry = p.get("execution_price") or p.get("entry_price")
                break
    except Exception:
        pass

    # 1. trailing stop — CLONED tool: session-local tracking, read-only bridge
    #    peek. Manual checks never write to the bridge. CONTEST profile:
    #    quick 4% guard (the bot's patient 8% is for spot, not the contest).
    high, trail_hit = _cloned_trailing(pair, live, entry, sb, trail_pct=CONTEST_TRAIL_PCT)

    # 2. contest sell bar — lower than the bot's (75/60): get out fast
    is_momentum = any("MOMENTUM RIDER" in r for r in sig.reasons)
    sell_thr = CONTEST_SELL_BAR
    strategy_sell = sig.action == "sell" and sig.confidence >= sell_thr
    exhaustion = any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy"
    rider_exit = any("exit signal" in r.lower() for r in sig.reasons)

    should_exit = trail_hit or strategy_sell or exhaustion or rider_exit
    reasons = []
    if trail_hit and high:
        reasons.append(f"down {(high-live)/high*100:.1f}% from the top")
    if strategy_sell:
        reasons.append(f"trend broke ({sig.confidence:.0f}% red)")
    if exhaustion:
        reasons.append("move's done")
    if rider_exit:
        reasons.append("out of steam")

    # DCA-style dip signal (advisory display only)
    if entry and sig.action == "buy":
        dip = (entry - live) / entry * 100
        if dip >= 2.5:
            reasons_note = f"<span style='color:#ffc36b;'>DIP +{dip:.1f}% WITH BUY SIGNAL — classic add-to-position zone</span>"
        else:
            reasons_note = f"<span class='dim'>Add-zone arms at -2.5% (now {dip:+.1f}%)</span>"
    else:
        reasons_note = ""

    # readout
    k = (
        knob(sig.confidence, 0, 100, "Signal", lambda v: f"{v:.0f}%") +
        knob((sig.indicators or {}).get("rsi") or 50, 0, 100, "RSI", lambda v: f"{v:.0f}") +
        knob(high, live * 0.9, live * 1.15, "Tracked High", lambda v: _fmt_price(v)) +
        knob(-((entry - live) / entry * 100) if entry else 0, -10, 10, "Pos P&L %", lambda v: f"{v:+.1f}%")
    )
    st.markdown(plate(f"Exit Check — {asset}", f'<div class="knob-row">{k}</div>'), unsafe_allow_html=True)

    verdict_lines = [
        f"NOW <span class='dim'>................</span> {_fmt_price(live)}" +
        (f"   PAID <span class='dim'>............</span> {_fmt_price(entry)}" if entry else ""),
        f"GET OUT AT <span class='dim'>..........</span> "
        + (f"🔴 NOW — down {CONTEST_TRAIL_PCT:.0f}% from the top" if trail_hit
           else f"🟢 {_fmt_price(high * (1 - CONTEST_TRAIL_PCT / 100))}"
           f" <span class='dim'>(top was {_fmt_price(high)})</span>"),
    ]
    if sig.action == "sell" and sig.confidence < sell_thr:
        gap = sell_thr - sell_thr + (sell_thr - sig.confidence)
        verdict_lines.append(
            f"HOW SURE <span class='dim'>............</span> {sig.confidence:.0f}% red · "
            f"sell at {sell_thr}% · <span style='color:#4be38a;'>{gap:.0f} points to go</span>")
    else:
        verdict_lines.append(
            f"HOW SURE <span class='dim'>............</span> {sig.confidence:.0f}% · "
            f"sells at {sell_thr}% red")
    # ── ETA to the goal: same Volatility Engine the bot uses ─────────
    goal_eta = _time_to_goal(pair, live, sig.take_profit)
    if goal_eta and not should_exit:
        pct_left, hours = goal_eta
        verdict_lines.append(
            f"HITS GOAL IN <span class='dim'>........</span> "
            f"<span style='color:#4be38a;'>{_fmt_hours(hours)}</span> <span class='dim'>(goal {_fmt_price(sig.take_profit)}, {pct_left:.1f}% away)</span>")
    elif not should_exit and not sig.take_profit:
        verdict_lines.append("HITS GOAL IN <span class='dim'>........</span> <span class='dim'>no goal on the chart</span>")

    if reasons:
        verdict_lines.append('<span style="color:#ff6b5e;">SELL NOW</span>')
        for r in reasons:
            verdict_lines.append(f"  ✕ {r}")
    else:
        verdict_lines.append('<span style="color:#4be38a;">HOLD</span>')
    if reasons_note:
        verdict_lines.append(reasons_note)
    verdict_lines.append("<span class='dim'>advice only — nothing sells by itself</span>")

    tone = "red" if should_exit else "green"
    st.markdown(plate("Verdict", lcd(verdict_lines, tone=tone)), unsafe_allow_html=True)

    st.plotly_chart(render_chart(df, sig, cfg), width="stretch", key="ec_chart")

    # ── manual check alerts: IN-APP ONLY ──────────────────────────────
    # Windows desktop alerts are BUY-NOW calls only (never a sell popup).
    if should_exit:
        try:
            st.toast(f"🚨 GET OUT NOW — SELL {asset}: {reasons[0]}", icon="🚨", duration=20)
            _play_sound("alarm")
            _speak(f"Get out now! Sell {asset}! Get out now!")
        except Exception:
            pass
    else:
        # HOLD — she answers the real question: how long to the goal?
        try:
            _pnl_txt = ""
            if entry:
                _pnl_txt = f"You're up {(live/entry-1)*100:.1f} percent. "
            if goal_eta:
                pct_left, hours = goal_eta
                _speak(f"Still riding! {asset}! {_pnl_txt}"
                       f"Needs {pct_left:.1f} percent more to hit the goal. "
                       f"At tonight's pace, roughly {_fmt_hours(hours)}!")
            else:
                _speak(f"Still riding! {asset}! {_pnl_txt}No clear target on the chart — "
                       f"I'll yell the moment it's time to sell!")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    cfg = TradingConfig()

    # ── sidebar: channel settings ────────────────────────────────────
    st.sidebar.markdown("### 🎛️ Console Settings")
    _cs = _load_console_state()
    _tiers = ["$1,000", "$5,000", "$10,000"]
    _tier_default = _cs.get("tier", "$5,000")
    tier = st.sidebar.selectbox(
        "Contest account balance",
        _tiers,
        index=_tiers.index(_tier_default) if _tier_default in _tiers else 1,
        help="The strategy sizes positions as % of the account — this must match "
             "your real contest balance or the position sizes will be wrong.",
        key="challenge_tier")
    if tier != _cs.get("tier"):
        _cs["tier"] = tier
        _save_console_state(_cs)
    cfg.challenge_balance = float(tier.replace("$", "").replace(",", ""))
    candle_opts = {"15 min": 15, "1 hour": 60, "4 hours": 240, "1 day": 1440}
    cfg.candle_interval = candle_opts[st.sidebar.selectbox("Candle timeframe", list(candle_opts), index=1)]
    cfg.risk_per_trade_pct = st.sidebar.slider("Risk per trade (%)", 0.5, 2.0, 1.0, 0.1)
    cfg.risk_reward_ratio = st.sidebar.slider("Risk : Reward", 1.5, 4.0, 2.0, 0.5)
    cfg.atr_multiplier = st.sidebar.slider("ATR stop multiplier", 1.0, 3.0, 1.5, 0.1)
    cfg.min_confidence = st.sidebar.slider("Min confidence (%)", 20, 80, 40, 5)

    st.sidebar.markdown("---")

    # ── status LEDs (contest-relevant only; kill switch + bot controls
    #    live on the desktop shortcuts, not here) ──────────────────────
    sb = _bridge()
    sc = scanner_status()
    status_html = (
        led("SCANNER " + ("RUNNING" if sc else "OFFLINE"), "g" if sc else "r") + "<br>" +
        led("KILL SWITCH ON DESKTOP", "off")
    )
    st.sidebar.markdown(plate("Status", status_html), unsafe_allow_html=True)

    st.sidebar.caption("Launch everything from the desktop shortcuts.\nConsole = Scout + Trader in one board.")

    # ── header nameplate ─────────────────────────────────────────────
    n = _now_mst()
    block, day, thr, interval, no_buy = current_threshold()
    vol = volatility_snapshot()
    led_row = led(f"{day} {block}", 'a') + led(f"BAR {thr}%", 'a') + led(f'SCAN/{interval}m', 'off')
    if no_buy:
        led_row += led("COOLDOWN — NO BUYS", 'r')
    if vol:
        led_row += led(f"NOISE FLOOR {vol['thresholds']['standard']:.1f}%", 'a')
        led_row += led(f"VOL DATA {vol['data_weeks']:.1f}w", 'off')
    head = (
        f"<div style='display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;'>"
        f"<div>"
        f"<div style='font-family:Audiowide;font-size:24px;color:#f0e2c8;letter-spacing:3px;"
        f"text-shadow:0 2px 5px rgba(0,0,0,.7);'>KRAKEN SCOUT · TRADING CONSOLE</div>"
        f"<div style='font-family:Share Tech Mono,monospace;font-size:12px;color:#b9a06d;letter-spacing:2px;'>"
        f"SCOUT ⇄ TRADER · ONE BOARD · {n.strftime('%a %I:%M %p')} MST</div></div>"
        f"<div>{led_row}</div>"
        f"</div>"
    )
    st.markdown(plate("", head), unsafe_allow_html=True)

    # ── FIRST CHECK on open: ready to trade, or WAIT? ─────────────────
    try:
        _sb_boot = _bridge()
        _ks_on = _sb_boot.is_kill_switch_active()
    except Exception:
        _ks_on = False
    if not st.session_state.get("boot_check_done"):
        st.session_state["boot_check_done"] = True
        if _ks_on:
            try:
                st.toast("⛔ WAIT — the kill switch is TRIGGERED. "
                         "No buys will fire until you reset it (desktop RESET shortcut).",
                         icon="⛔", duration=20)
                _play_sound("alarm")
                _speak("Wait! The kill switch is triggered! No buys until you reset it!", rate=1)
            except Exception:
                pass
        else:
            try:
                st.toast("✅ ALL SYSTEMS GO — kill switch clear, watcher armed, ready to hunt.",
                         icon="✅", duration=15)
                _speak("Hello there! My systems are online and ready to make you "
                       "a ton of money today! Ready to have some fun!")
            except Exception:
                pass
    if _ks_on:
        # stays visible on every page until the switch is reset
        st.error("⛔ WAIT — the kill switch is TRIGGERED. No buys will fire until you reset it "
                 "(desktop RESET shortcut). Exits and the watch keep working.")

    # ── NOW strip: plain-English what's happening (beginner-friendly) ──
    st.markdown(plate("", now_strip(block, no_buy, interval, thr)), unsafe_allow_html=True)

    # ── beginner guide ─────────────────────────────────────────────────
    with st.expander("🆕 New here? What does this console do? (30-second tour)"):
        st.markdown(
            "**The app walks you through the whole thing — just follow it from screen to screen.**\n\n"
            "**The loop:**\n"
            "1. **TOURNAMENT** — coins battle it out. The winner gets sent to Quick Check for you.\n"
            "2. **QUICK CHECK** — the final yes/no. Green light = enter the trade on the contest app "
            "(it tells you exactly how much to buy). Then the app moves you along.\n"
            "3. **The light** — checks your trade every minute. Green = all good, keep holding. "
            "Red = GET OUT NOW, sell it on the app.\n"
            "4. Sold? The board clears and the app takes you back to hunting the next one.\n\n"
            "- **MASTER** — your score: contest payouts that landed + how your current trades are doing.\n"
            "- **EXIT CHECK** — 'should I sell?' on any coin, anytime.\n\n"
            "**House rules:** only long, never short. Red never means short — it means *sell it if "
            "you've got it, stay out if you don't.* Anything not green counts as red."
        )

    # ── global exit alert banner (from Auto-Watch — visible on every tab) ──
    exit_alerts = st.session_state.get("exit_alerts") or {}
    if exit_alerts:
        alert_rows = "".join(
            f"<div style='color:#ff6b5e;font-size:15px;'>🚨 SELL: <b>{_asset(p)}</b> — {r}</div>"
            for p, r in exit_alerts.items()
        )
        st.markdown(plate("🚨 GET OUT NOW",
                          f"<div style='font-family:Share Tech Mono,monospace;'>{alert_rows}</div>"
                          "<div style='margin-top:6px;color:#b9a06d;font-size:12px;'>"
                          "sell on the contest app — nothing sells by itself</div>"),
                    unsafe_allow_html=True)

    # ── console modules ──────────────────────────────────────────────
    kraken = get_kraken_client()
    scan_cfg = TradingConfig()
    scan_cfg.candle_interval = cfg.candle_interval
    scan_cfg.lookback_candles = cfg.lookback_candles

    # ── payout check: GLOBAL — the celebration pops on ANY page ────────
    try:
        _p_banner, _p_all = track_payouts(fetch_payouts(kraken))
        st.session_state["payouts"] = _p_all
        if _p_banner:
            try:
                st.toast(_p_banner, icon="💰", duration=20)
                _play_sound("ding")
                _speak(f"The contest just paid you {sum(p['amount'] for p in _p_all):,.2f} dollars! "
                       f"Real money in your account!")
            except Exception:
                pass
            st.markdown(plate("", f'<div class="celebrate">{_p_banner}</div>'),
                        unsafe_allow_html=True)
    except Exception:
        pass

    # ── THE LIGHT: auto-watch strip, always mounted (runs on every page) ──
    render_auto_watch(cfg, kraken)

    # ── JUST ENTERED banner: entry details while you type them into the app ──
    _lg = st.session_state.get("last_green")
    if _lg and (time.time() - _lg.get("ts", 0)) < 1800:
        _chunk_txt = (f"type into the contest app: buy {_lg['chunk'][0]:.0%} (${_lg['chunk'][1]:,.0f})"
                      if _lg.get("chunk") else "go enter it in the contest app")
        st.markdown(plate("",
            f"<div class='celebrate'>✅ YOU'RE IN — {_lg['asset']}! {_chunk_txt}<br>"
            f"<span style='font-size:14px;color:#d8ccb4;'>you paid {_fmt_price(_lg['px'])} · "
            f"bail-out price {_fmt_price(_lg.get('stop'))} · cash-out price {_fmt_price(_lg.get('tp'))}</span><br>"
            f"<span style='font-size:13px;color:#b9a06d;'>the light below has your back — "
            f"green = all good, red = get out now</span></div>"), unsafe_allow_html=True)
    elif _lg and (time.time() - _lg.get("ts", 0)) >= 1800:
        st.session_state.pop("last_green", None)

    # ── flow pages: state-driven so the workflow can ADVANCE ITSELF ────
    # TOURNAMENT (pick) → QUICK CHECK (confirm + enter) → EXIT CHECK (watch)
    # → back to TOURNAMENT when the board clears. Yellow is not a thing:
    # anything not green reads red.
    FLOW_PAGES = ["MASTER", "TOURNAMENT", "QUICK CHECK", "EXIT CHECK"]
    active_page = st.session_state.get("active_tab", "MASTER")
    page_bar = st.columns(len(FLOW_PAGES))
    for _i, _name in enumerate(FLOW_PAGES):
        if page_bar[_i].button(_name,
                               type="primary" if _name == active_page else "secondary",
                               use_container_width=True,
                               key=f"flow_{_name.replace(' ', '_')}"):
            st.session_state["active_tab"] = _name
            st.rerun()
    st.markdown("<div class='score-sub' style='text-align:center;margin-bottom:2px;'>"
                "the loop: pick a winner ▶ double-check it ▶ enter the trade ▶ ride the green light "
                "▶ red means get out — then do it again</div>", unsafe_allow_html=True)

    if active_page == "MASTER":
        render_master(cfg, kraken)
    elif active_page == "TOURNAMENT":
        render_tournament(cfg, scan_cfg, kraken)
    elif active_page == "QUICK CHECK":
        render_quick_check(cfg, kraken)
    else:
        render_exit_check(cfg, kraken)


if __name__ == "__main__":
    main()
