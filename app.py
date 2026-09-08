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
# THE ADAPTIVE TTL SYSTEM (user spec, Sep 5): the seven hot fetchers route
# through this engine — base/min/max TTLs per fetcher, a volatility-regime
# multiplier (calm 1.5x / normal 1.0x / volatile 0.5x / extreme 0.25x),
# event-driven invalidation, cross-cache rules, and the freshness plate.
from adaptive_cache import ac_cached, ac_invalidate, freshness_html

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
/* header is TRANSPARENT, not hidden — hiding it also hides the sidebar's
   collapsed-state expand control (it renders inside stHeader), which made
   the sidebar impossible to reopen once collapsed (user report, Sep 1).
   Only the toolbar junk is hidden; the toggle stays visible. */
#MainMenu, footer {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent !important; background-image: none !important; }}
div[data-testid="stToolbar"], div[data-testid="stDecoration"] {{ visibility: hidden; }}
/* THE REOPEN ARROW (Streamlit 1.62: stExpandSidebarButton, top-left once
   the sidebar is collapsed) is hover-revealed by default — an invisible
   button on the walnut background. Forced ALWAYS visible + styled. */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {{
  visibility: visible !important;
  opacity: 1 !important;
  z-index: 999999 !important;
  background: rgba(36,28,20,.95) !important;
  border: 1px solid #3a352c !important; border-left: none !important; border-top: none !important;
  border-radius: 0 0 10px 0 !important;
  min-width: 34px !important; min-height: 34px !important;
}}
[data-testid="stExpandSidebarButton"] * {{
  visibility: visible !important;
  opacity: 1 !important;
  color: #ffc36b !important;
}}

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

@st.cache_data(ttl=20)
def scanner_status_core() -> bool:
    # the PowerShell spawn (this fired TWICE per rerun — sidebar + NOW
    # strip — and each spawn is ~0.5-2s of the page freezing on every
    # click); ttl=20 = the ADAPTIVE MIN, the burst shield
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*background_scanner*'} | Select-Object -First 1 ProcessId"],
            capture_output=True, text=True, timeout=6)
        return bool(r.stdout.strip())
    except Exception:
        return False


def scanner_status() -> bool:
    """Through the ADAPTIVE cache: base 60s, 20-120s by regime — tightens
    when markets move so the heartbeat feels real, loosens when calm.
    A >10% book-depth change (the cross-cache rule) refreshes it early."""
    return ac_cached("scanner_status", "heartbeat", scanner_status_core)

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

@st.cache_data(ttl=10)
def bot_positions_core() -> list[dict]:
    """Open positions from the bot's Postgres DB. ttl=10 = the ADAPTIVE
    MIN (the burst shield); the adaptive layer sets the cadence (30s base,
    10-45s — tightens when the bot is trading or vol spikes)."""
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


def bot_positions() -> list[dict]:
    """BOT'S BOOK through the ADAPTIVE cache — this runs in now_strip on
    EVERY rerun and a live Neon round-trip per rerun was part of the page
    choppiness. A position ENTER/EXIT (the cross-cache rule) refreshes
    qc_liquidity; a >0.8% price move refreshes THIS (from ticker_px)."""
    return ac_cached("bot_positions", "open",
                     bot_positions_core, copy_result=True)


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


def _derive_goal(pair: str, kraken=None) -> tuple[float, float]:
    """(goal_pct, window_min) for THIS coin — the same derivation the
    green light uses. Shared by every entry path (green light, WATCH-
    anyway, unified entry control) so a manual entry carries a real goal
    instead of the 1.5% fallback.
    (Sep 4, user rule: "target each market separately — what were those
    assets doing at this time all week? use 24 hour candles, scan 1
    week; the goal should reach before the rotation happens ideally"):
    the goal is the asset's OWN week (median daily range from 7 daily
    candles; the engine's per-rotation cell as fallback), and the window
    is bounded by the NEXT ROTATION BOUNDARY — crossing it is the
    exception the scanner's boundary warning + forced full exit scan
    cover."""
    goal = 1.5
    try:
        import background_scanner as _bs
        if not hasattr(_bs, "_market_goal_pct"):
            import importlib
            _bs = importlib.reload(_bs)
        if kraken is not None and hasattr(_bs, "_market_goal_pct"):
            g = _bs._market_goal_pct(kraken, pair)
        else:
            g = _bs._window_goal_pct(pair)
        if g:
            goal = float(g)
    except Exception:
        pass
    window = 180.0
    try:
        import background_scanner as _bs2
        if hasattr(_bs2, "_rotation_window_min"):
            window = float(_bs2._rotation_window_min())
    except Exception:
        window = float(max(60.0, min(180.0, goal * 60.0)))
    return float(goal), float(window)



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

# ONE VOICE ENGINE (user rule, Aug 27: "I never asked for them to be
# separate"): the console does NOT speak directly — a browser page is dead
# when the tab is closed, so a console-spoken message vanishes the moment
# you walk away. Instead the console DROPS every utterance into a speech
# queue that the SCANNER (always on, browser or not) speaks out loud.
# One voice, one vocabulary, one engine.
SPEECH_QUEUE_DIR = Path(__file__).parent / "speech_queue"


def _speak(text: str, rate: int = 2):
    """Queue Zira's line for the SCANNER to speak — the one engine that
    works with the browser closed. Fire-and-forget, never blocks, never
    depends on this tab being alive."""
    try:
        SPEECH_QUEUE_DIR.mkdir(exist_ok=True)
        import json as _json, uuid as _uuid
        name = f"{time.time():.6f}_{_uuid.uuid4().hex[:6]}.json"
        tmp = SPEECH_QUEUE_DIR / (name + ".tmp")
        tmp.write_text(_json.dumps({"text": text, "rate": int(rate),
                                    "ts": time.time()}), encoding="utf-8")
        os.replace(tmp, SPEECH_QUEUE_DIR / name)
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


@st.cache_data(ttl=180)
def fetch_payouts_core(_kraken) -> list[dict]:
    """Recent USD transfers IN from the Kraken ledger — the Funded contest
    paying out. (Trades are type 'trade'; rewards are 'receive'; both excluded.)
    The SIGNED ledger API call — the biggest source of the page freezing
    on clicks. ARCHIVAL (the adaptive spec): the multiplier is ignored, the
    cadence stays 180s whatever the market does. A payout celebration may
    land up to 3 minutes late; payouts are rare."""
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


def fetch_payouts(_kraken) -> list[dict]:
    """The payout read through the ADAPTIVE layer — ARCHIVAL: base 180s,
    the volatility multiplier ignored (history is history at any vol)."""
    return ac_cached("fetch_payouts", "ledger",
                     lambda: fetch_payouts_core(_kraken), copy_result=True)


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
def _live_price_cached(pair: str) -> float:
    """The raw read — RAISES on failure (st.cache_data never caches an
    exception, so a failed read retries fresh on the next call instead of
    sticking at $0 for the whole TTL — the Sep 5 silent-rejection bug)."""
    return float(get_kraken_client().fetch_price(pair))


def live_price(pair: str) -> float:
    """0.0 on failure (the callers' contract) — but the failure is never
    cached: the next call reads fresh."""
    try:
        return float(_live_price_cached(pair))
    except Exception:
        return 0.0


@st.cache_data(ttl=8)
def _ticker_px_cached(pair: str) -> float:
    """The raw ticker read — RAISES on failure (never cached: a failed
    read retries fresh, the Sep 5 lesson). ttl=8s is the ADAPTIVE MIN
    (the burst shield); the adaptive layer above decides the real cadence
    (30s base, 8-45s by volatility regime)."""
    _k = get_kraken_client()
    if hasattr(_k, "fetch_ticker"):
        _t = _k.fetch_ticker(pair)
    else:
        _t = _k.exchange.fetch_ticker(pair)
    return float((_t or {}).get("last") or 0.0)


def _ticker_px(pair: str) -> float:
    """One ticker read through the ADAPTIVE cache (base 30s, 8-45s by
    regime) — the 60s light cycle and the bot book share it instead of
    each fetching fresh on every rerun. Every real fetch samples the
    volatility model; a >0.8% move invalidates bot_positions (the
    cross-cache rule)."""
    try:
        return ac_cached("ticker_px", pair,
                         lambda: float(_ticker_px_cached(pair)))
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
    Signals older than HEADLINE_MAX_AGE_MIN never headline — a scalp's
    entry edge lives for minutes (Aug 25: user acted on a 20-minute-old
    signal with no idea it was old).
    Returns (ranked candidates with age_min, total signals considered)."""
    import sqlite3
    from collections import Counter
    from background_scanner import _hours_to_scalp, _max_resolve_hours
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.db"))
        conn.row_factory = sqlite3.Row
        since = (_now_mst() - timedelta(minutes=HEADLINE_MAX_AGE_MIN)).isoformat()
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
        # NO DOWNWARD TREND PICKS (user directive, Aug 25) — same EMA knife
        # test as the QC, so the card never crowns what the QC would block.
        # Fail-open on data errors (the QC still gates).
        # (BUGFIX Sep 2 — pyflakes-found: this check read `live` BEFORE it
        # was fetched; the NameError was swallowed by the except below, so
        # the knife NEVER ran on the card path. The fetch now happens first.)
        try:
            _oh = _ex.fetch_ohlcv(pair, timeframe="1h", limit=30)
            if _oh and len(_oh) >= 25:
                import pandas as _pd
                from indicators import ema as _ema
                _s = _pd.Series([c[4] for c in _oh])
                _es = float(_ema(_s, 9).iloc[-1])
                _el = float(_ema(_s, 21).iloc[-1])
                if _es < _el and live < _el:
                    continue            # falling knife — never headlines
        except Exception:
            pass
        ran_pct = (live - entry) / entry * 100  # displayed on the card —
        # calibration showed chases hit at the same rate (76-80%), so it
        # warns, it doesn't exclude
        vol = float((doc.get("universe", {}).get(pair, {})
                     .get(sched, {}).get(rotation_key) or {}).get("median_volume_usd") or 0)
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            bucket = int(ts.timestamp() // 600)
            age_min = (_now_mst() - ts).total_seconds() / 60
        except Exception:
            bucket, age_min = 0, 0.0
        cands.append({"asset": pair.split("/")[0], "pair": pair,
                      "conf": float(r["confidence"]), "pace": pace,
                      "ran": ran_pct, "vol": vol, "bucket": bucket,
                      "age_min": max(age_min, 0.0)})

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
# single engine refresh).
# ADVISORY BRAIN (user rule, Sep 5): the conviction floor is RETIRED as a
# blocker — the deep read's number SIZES the entry (hindsight_scorer's
# bucket table, the same one every popup uses); the scan's DIRECTION still
# gates. QC_SCALP_FLOOR stays defined for the history queries + the
# tournament context (line 1031 reads past winners above it).
QC_SCALP_FLOOR = 60
# ONE BUY VOICE (user rule, Aug 31, sanctioned: "I only want brain buy
# signals for the voice" + "less entries but more money"): the card SHOWS
# the pick (the ⚡ plate + CHECK button) but does not SPEAK it. The
# champion alert — which fires whenever the deep scan RUNS and agrees on
# direction (the number sizes the entry since Sep 5) — is the only
# unsolicited buy voice. Flip to True to restore the card's voice.
CARD_PICK_VOICE_ENABLED = False

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

# A scalp signal is FRESH for minutes, not hours. Calibration (Aug 25,
# diag_calibrate.py, 261 signals): entering at +15 min hits 80%, +30 min
# 81%, +45 min 82% — barely decays. The cap keeps the headline honest
# without throwing away live picks: 30 minutes.
HEADLINE_MAX_AGE_MIN = 30


def _trend_state(df, cfg, px: float) -> str:
    """UP / BASING / FALLING on the scan timeframe (1h candles).

    FALLING = confirmed knife: short EMA under the long EMA AND price below
    both — still making lows. That's the stuck-case guard (the PENGU lesson:
    a fast wiggle in a falling trend means getting stuck bleeds — no $20 came,
    -$143). UP and BASING pass: in an uptrend a stuck trade gets rescued by
    the next push; at a base (price back over the long EMA, short still under
    — recovering) it's the PUMP-at-the-bottom entry, not a knife."""
    try:
        _es = float(ema(df["close"], cfg.ema_short).iloc[-1])
        _el = float(ema(df["close"], cfg.ema_long).iloc[-1])
        if _es < _el and px < _el:
            return "FALLING"
        if _es > _el:
            return "UP"
        return "BASING"
    except Exception:
        return "UP"  # can't compute → fail-open (never crash the scan)


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


@st.cache_data(ttl=20)
def _fetch_df_core(pair: str, interval: int, lookback: int):
    """The raw candle read. ttl=20 = the ADAPTIVE MIN (the burst shield);
    the adaptive layer sets the cadence (45s base, 20-90s by regime —
    tightens during volatility so charts feel live)."""
    import pandas as pd
    ohlcv = get_kraken_client().fetch_ohlcv(pair, interval_minutes=interval, limit=lookback)
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df

def _fetch_df_cached(pair: str, interval: int, lookback: int):
    """Candles through the ADAPTIVE cache (base 45s, 20-90s by regime) —
    the light cycle re-runs every minute and used to re-fetch OHLCV per
    position each time (the whole page froze while it ran). Every caller
    gets its OWN copy of the frame (copy_result): the live-price seeding
    in the exit paths can never poison the cache. Chart overlays render
    FROM this frame every rerun — a candle refresh refreshes them too
    (the spec's cross-cache rule, inherent)."""
    return ac_cached("fetch_df", f"{pair}|{interval}|{lookback}",
                     lambda: _fetch_df_core(pair, interval, lookback),
                     copy_result=True)

def fetch_df(kraken, pair: str, cfg: TradingConfig):
    return _fetch_df_cached(pair, cfg.candle_interval, cfg.lookback_candles)


@st.cache_data(ttl=20)
def _qc_liquidity_core(pair: str, notional: float) -> dict:
    """The raw book readout. ttl=20 = the ADAPTIVE MIN."""
    try:
        from execution_tiers import liquidity_snapshot as _ls
        return _ls(get_kraken_client(), pair, float(notional))
    except Exception as exc:
        return {"ok": False, "reason": f"book unavailable: {exc}"}


def _qc_liquidity(pair: str, notional: float) -> dict:
    """The QC's advisory book readout through the ADAPTIVE cache (base
    60s, 20-120s — liquidity matters more during volatility, so it
    tightens) — the scan result stays on screen while you fix numbers
    below it, and the order book used to re-fetch on every one of those
    reruns. A >10% depth change (the cross-cache rule) refreshes
    scanner_status; a bot position change refreshes THIS."""
    return ac_cached("qc_liquidity", pair,
                     lambda: _qc_liquidity_core(pair, notional))


@st.cache_data(ttl=60)
def _past_winners_core(limit: int = 15) -> list[dict]:
    """The raw champions read (a live Neon query). ttl=60 = the ADAPTIVE
    floor; STATIC data — the volatility multiplier is ignored (the
    adaptive spec: history can be a minute old at any vol)."""
    try:
        if not _HAS_DB:
            return []
        ws = db.get_tournament_winners(limit=limit)
        return [{"pair": w.pair, "action": w.action,
                 "confidence": float(w.confidence or 0),
                 "take_profit": w.take_profit, "timestamp": w.timestamp}
                for w in ws]
    except Exception:
        return []


def _past_winners(limit: int = 15) -> list[dict]:
    """Past champions for the TOURNAMENT plate through the ADAPTIVE cache
    (60s base, STATIC — the multiplier is ignored). Busts on a new
    champion (ac_invalidate) — see the tournament render."""
    return ac_cached("past_winners", int(limit),
                     lambda: _past_winners_core(limit), copy_result=True)


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

# ════════════════════════════════════════════════════════════════════════
#  THE CARD — the flow itself (Aug 25 redesign)
#  MASTER shows one card and nothing else on the main screen:
#  a PICK when something's fresh, the WATCH when you're in a trade,
#  a WAITING card while she hunts. Why / When / How long — plain English.
# ════════════════════════════════════════════════════════════════════════

def _out_by_txt(base, eta_h: float) -> str:
    """'out by 11:15' — patience = 2x the usual $20 time, min 45 minutes,
    snapped to 5-minute marks. base = the datetime the clock starts from."""
    mins = max(eta_h * 60.0, 30.0)
    patience = max(2.0 * mins, 45.0)
    out_by = base + timedelta(minutes=round(patience / 5.0) * 5)
    return out_by.strftime("%I:%M").lstrip("0")


def _eta_txt(eta_h: float) -> str:
    mins = eta_h * 60.0
    if mins < 60:
        return f"about {max(5, round(mins / 5.0) * 5)} minutes"
    return f"about {eta_h:.1f} hours"


def _tide_line() -> str:
    """One lcd line: the tide, inside every card state (user rule, Aug 26)."""
    try:
        import background_scanner as _bs
        if not hasattr(_bs, "_fleet_tide"):
            import importlib
            _bs = importlib.reload(_bs)
        pct, state = _bs._fleet_tide()
        if state:
            col = {"RISING": "#4be38a", "FLAT": "#ffc36b", "OUT": "#ff6b5e"}[state]
            hint = {"RISING": "the market carries greens — take them",
                    "FLAT": "no tide to carry you — pickier, smaller",
                    "OUT": "ALL SHIPS SINK TOGETHER — don't enter, don't hold"}[state]
            return (f"TIDE <span class='dim'>.............</span> "
                    f"<span style='color:{col};'>{state}</span> "
                    f"<span class='dim'>({pct:.0f}% of the fleet up) — {hint}</span>")
    except Exception:
        pass
    return ""


def _pick_card(cfg: TradingConfig, kraken):
    """The one card on MASTER. States: PICK / WATCH / WAITING / COOLDOWN."""
    block, day, thr, _, no_buy = current_threshold()
    cw = _load_watch()

    # ── COOLDOWN ────────────────────────────────────────────────────
    if no_buy:
        st.markdown(plate("🌙 COOLDOWN", lcd([
            "8pm to midnight — no new picks.",
            "<span class='dim'>she still watches what you hold · new picks at midnight</span>",
            _tide_line(),
        ], tone="amber")), unsafe_allow_html=True)
        return

    # ── WATCH: you're in a trade — she tracks the goal ──────────────
    if cw:
        for pair, w in cw.items():
            entry = float(w.get("entry") or 0)
            size = float(w.get("size") or 5000.0)
            px = live_price(pair)
            if not entry or not px:
                continue
            usd = (px / entry - 1.0) * size
            pct = (px / entry - 1.0) * 100
            try:
                goal = float(w.get("goal_pct") or 0) or 1.5
            except Exception:
                goal = 1.5
            try:
                added = datetime.fromisoformat(w["added"])
            except Exception:
                added = _now_mst()
            try:
                _win = float(w.get("window_min") or 0) or max(60.0, min(180.0, goal * 60.0))
            except Exception:
                _win = 90.0
            overdue = _now_mst() > added + timedelta(minutes=_win)
            color = "#4be38a" if usd >= 0 else "#ff6b5e"
            _goal_col = "#4be38a" if pct >= goal else "#ffc36b"
            rows = [
                f"<span style='color:{color};font-size:34px;font-family:Audiowide;'>"
                f"{_asset(pair)} {'+' if usd >= 0 else '-'}${abs(usd):,.0f}</span>"
                + (f"  <span style='color:{_goal_col};'>· GOAL HIT</span>" if pct >= goal else ""),
                f"WHY <span class='dim'>............</span> you're in it — entered at {_fmt_price(entry)}. "
                f"She's watching it the whole time.",
            ]
            if overdue:
                rows.append(f"<span style='color:#ff6b5e;'>HOW LONG <span class='dim'>.........</span> "
                            f"TIME'S UP — it hasn't paid its {goal:.1f}% by the window's end. "
                            f"Get out unless she just called something.</span>")
            else:
                rows.append(f"HOW LONG <span class='dim'>.........</span> goal +{goal:.1f}% — "
                            f"now {pct:+.1f}%. If it hasn't paid by the window's end, get out.")
            rows.append("<span class='dim'>walk around the house — she'll call you</span>")
            rows.append(_tide_line())
            st.markdown(plate("👁 WATCHING", lcd(rows, tone="green" if usd >= 0 else "red")),
                        unsafe_allow_html=True)
        return

    # ── PICK: something fresh to look at ────────────────────────────
    try:
        from background_scanner import rotation_key_for_hour, _schedule_for_dow
        _n = _now_mst()
        ranked, total = _scalp_headline(kraken.exchange,
                                        rotation_key_for_hour(_n.hour),
                                        _schedule_for_dow(_n.weekday()))
    except Exception:
        ranked, total = [], 0
    if ranked:
        # ROTATION LOCKOUT (user rule, Aug 30): an asset that already fired
        # this rotation never takes the PICK seat — the card must never
        # point at something the QC would reject as a re-run. EXCEPTION —
        # CONFIRMATION WINDOW (user rule, Aug 31): a pick whose alert fired
        # within ROTATION_CONFIRM_WINDOW_MIN stays on the card so CHECK
        # works on the very green light that set the lockout (the 'Already
        # ran' collision — the card was hiding the pick the alert just made).
        try:
            import background_scanner as _bs6
            if not hasattr(_bs6, "ROTATION_CONFIRM_WINDOW_MIN"):
                import importlib
                _bs6 = importlib.reload(_bs6)
            _cwin = getattr(_bs6, "ROTATION_CONFIRM_WINDOW_MIN", 0.0)
            ranked = [h for h in ranked
                      if _bs6.rotation_gate(h["asset"], confirm_window_min=_cwin)[0]]
        except Exception:
            pass
    if ranked:
        h = ranked[0]
        # Zira announces when a NEW young pick takes the seat
        if st.session_state.get("_headline_asset") != h["asset"]:
            st.session_state["_headline_asset"] = h["asset"]
            if h["age_min"] <= 10 and CARD_PICK_VOICE_ENABLED:
                # (voice gated by CARD_PICK_VOICE_ENABLED — the card shows,
                # the champion alert speaks; user rule, Aug 31)
                # VOICE-FIRST, ONE STANDARD (user rule, Aug 27: "why are
                # they not all the same?"): the card's browser voice speaks
                # the SAME complete call as the server alert voice — coin,
                # $20 time, SIZE if the book is thin, tide, out-by.
                try:
                    import background_scanner as _bsv
                    if not hasattr(_bsv, "_fleet_tide"):
                        import importlib
                        _bsv = importlib.reload(_bsv)
                    _tpv, _tsv = _bsv._fleet_tide()
                    _tide_say = "The tide is rising! " if _tsv == "RISING" else ""
                    _size_say = ""
                    try:
                        from execution_tiers import liquidity_snapshot as _lsv
                        _balv = float(cfg.challenge_balance)
                        _snv = _lsv(kraken.exchange, h["pair"], _balv)
                        if _snv.get("ok"):
                            _dep = float(_snv["book"]["ask_tob"])
                            _carry = _dep * 0.20
                            if _balv > _carry:
                                _fitv = [c * _balv for c in (0.25, 0.50, 0.75, 1.00)
                                         if c * _balv <= _carry]
                                _spendv = max(_fitv) if _fitv else 0.0
                                if _spendv > 0:
                                    _size_say = f"Small size only — {_spendv:,.0f} dollars! "
                                else:
                                    _size_say = "Too thin — this book can't carry the trade! "
                            else:
                                _size_say = f"Big book — all {_balv:,.0f} dollars in fine! "
                        else:
                            _size_say = "Book unreadable — your call on size! "
                    except Exception:
                        _size_say = "Book unreadable — your call on size! "
                    _out_say = _out_by_txt(_now_mst(), h["pace"])
                    _speak(f"New pick! {h['asset']}! Twenty dollars in about "
                           f"{h['pace'] * 60:.0f} minutes! {_size_say}{_tide_say}"
                           f"If it hasn't paid by {_out_say}, get out!")
                except Exception:
                    pass
        age_txt = f"{h['age_min']:.0f} minutes ago" if h["age_min"] >= 1 else "just now"
        try:
            import background_scanner as _bsg
            if not hasattr(_bsg, "_market_goal_pct"):
                import importlib
                _bsg = importlib.reload(_bsg)
            # (Sep 4) the asset's OWN week of daily candles; the engine
            # cell is the fallback
            if hasattr(_bsg, "_market_goal_pct"):
                _card_goal = _bsg._market_goal_pct(kraken, f"{h['asset']}/USD")
            else:
                _card_goal = _bsg._window_goal_pct(f"{h['asset']}/USD")
        except Exception:
            _card_goal = 1.5
        rows = [
            f"<span style='color:#4be38a;font-size:40px;font-family:Audiowide;'>⚡ {h['asset']}</span>",
            f"WHY <span class='dim'>............</span> the engine expects +{_card_goal:.1f}% in this "
            f"window at this coin's pace. It's fresh: only +{h['ran']:.1f}% above where the signal "
            f"caught it, and the setup is real ({h['conf']:.0f}% sure).",
            f"WHEN <span class='dim'>...........</span> the signal came in {age_txt}"
            + ("" if h["age_min"] < 1 else " — move quick, wiggles don't wait"),
            f"HOW LONG <span class='dim'>.........</span> goal +{_card_goal:.1f}% — or she says out. "
            f"If it hasn't paid by the window's end, get out.",
            f"<span class='dim'>{total} signals · {len(ranked)} real moves · this is the best one — "
            f"one click for the final yes/no</span>",
            _tide_line(),
        ]
        st.markdown(plate("⚡ THE PICK", lcd(rows, tone="green")), unsafe_allow_html=True)
        if st.button(f"➡ CHECK {h['asset']} — the final yes/no",
                     type="primary", width="stretch", key="card_confirm"):
            st.session_state["qc_pick_next"] = h["asset"]
            st.session_state["qc_auto_scan"] = True   # scan fires on arrival
            st.session_state["active_tab"] = "QUICK CHECK"
            try:
                st.rerun(scope="app")
            except TypeError:
                st.rerun()
        return

    # ── WAITING: nothing fresh — she's hunting ──────────────────────
    st.markdown(plate("⏳ WAITING", lcd([
        "Nothing fresh right now — she's hunting.",
        "<span class='dim'>the next pick lands here the moment it's real · want to move yourself? "
        "check a coin in QUICK CHECK or run the TOURNAMENT</span>",
        _tide_line(),
    ], tone="amber")), unsafe_allow_html=True)


def render_master(cfg: TradingConfig, kraken):
    # THE CARD — the flow itself. One card, nothing else on the main screen.
    @st.fragment(run_every=30_000)
    def _card_tick():
        _pick_card(cfg, kraken)
    _card_tick()

    # THE DISCIPLINE SCORE (user spec, Sep 4: "Did I leave money on the
    # table?") — the closing rotation's grade, side-by-side with the
    # contest results, with the lesson learned and the one insight
    st.markdown(_discipline_plate(), unsafe_allow_html=True)

    # 🚫 WHAT THE FUNNEL BURIED (user rule, Sep 4, the POPCAT lesson: the
    # scanner's suppression verdicts used to print to the console window
    # only — a buried payer took three forensics scripts to name its
    # gate). The suppression log is now persistent and shown here: every
    # gate, its numbers, newest first.
    _render_suppression_strip()

    # TODAY'S FACTS — the system grades itself from real records (user
    # directive: MASTER is for self-correction on facts and figures —
    # signals fired, twenties banked, what the bot rejected — no guessing)
    st.markdown(plate("📐 TODAY'S FACTS — the system grades itself",
                      lcd(_today_facts())), unsafe_allow_html=True)

    # the bones — every tool and number still there, one click down
    with st.expander("📐 THE BONES — money score, breaker, integrity"):
        render_master_bones(cfg, kraken)


def _render_suppression_strip():
    """🚫 MONEY LEFT ON THE TABLE — the popups the funnel buried, newest
    first (popup_suppressions.jsonl, written by the scanner at every
    suppression: brain-floor, tide-out, worth-floor, stale-move, deep-veto,
    downward-trend, too-thin, death-line, daily-lock, fail-closed). The raw
    record — the discipline plate's 'left by the gates' number is the paid
    subset of exactly this."""
    try:
        import json as _json
        from pathlib import Path as _P
        f = _P(__file__).parent / "popup_suppressions.jsonl"
        if not f.exists():
            return
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return
        rows = []
        for l in reversed(lines[-14:]):  # newest first
            try:
                d = _json.loads(l)
            except Exception:
                continue
            ts = str(d.get("ts", ""))[11:16]
            gate = str(d.get("gate", "?"))
            gcol = ("#ff6b5e" if gate in ("tide-out", "death-line", "daily-lock")
                    else "#ffc36b")
            nums = []
            if d.get("quick") is not None:
                nums.append(f"quick {float(d['quick']):.0f}%")
            if d.get("brain") is not None:
                nums.append(f"brain {float(d['brain']):.0f}%")
            if d.get("floor") is not None:
                nums.append(f"floor {float(d['floor']):.0f}%")
            nums_txt = f" <span class='dim'>· {' · '.join(nums)}</span>" if nums else ""
            rows.append(
                f"<div style='padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.08);"
                f"font-family:Share Tech Mono,monospace;font-size:12.5px;color:#d8ccb4;'>"
                f"<span class='dim'>{ts}</span>&nbsp;&nbsp;<b>{_asset(str(d.get('pair', '?')))}</b>"
                f"&nbsp;&nbsp;<span style='color:{gcol};'>{gate}</span>{nums_txt}<br>"
                f"<span class='dim'>{str(d.get('detail', ''))[:130]}</span></div>")
        if rows:
            with st.expander(f"🚫 MONEY LEFT ON THE TABLE — the popups the funnel "
                             f"buried (last {len(rows)}, newest first)"):
                st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("suppression strip failed: %s", exc)


def _discipline_plate() -> str:
    """THE DISCIPLINE SCORE plate: the score (dopamine-styled), the
    side-by-side with the contest results, the money-left breakdown,
    the lesson learned, and the one insight — the spec's four parts in
    order. Reads discipline_state.json (written by the scanner at every
    rotation rollover); absent state = the panel hasn't run yet."""
    try:
        import discipline as _disc
        _st = _disc.load_state()
        _h = _st.get("history") or []
        _last = _h[-1] if _h else None
        if not _last:
            return plate("🎓 DISCIPLINE — did I leave money on the table?",
                         "<div class='dim' style='padding:8px 4px;'>"
                         "The first rotation close writes the first score.<br>"
                         "Goals vs reality vs you — graded every 4 hours, "
                         "learning its own weights.</div>")
        s = float(_last.get("score") or 0)
        _empty = bool(_last.get("empty_fire"))
        col = ("#ffc36b" if _empty else
               "#4be38a" if s >= 80 else "#ffc36b" if s >= 60 else "#ff6b5e")
        grade = ("SILENT ROTATION — nothing fired, nothing graded (capped)" if _empty else
                 "SNIPER — nothing slipped" if s >= 85 else
                 "SOLID — tight rotation" if s >= 65 else
                 "CAREFUL — money slipped" if s >= 40 else
                 "UNDISCIPLINED — the table kept it")
        # the streak: consecutive scores >= 70 — an empty-fire capped
        # score never feeds the streak (a silent rotation is not a
        # disciplined one, Sep 7)
        streak = 0
        for row in reversed(_h):
            if float(row.get("score") or 0) >= 70 and not row.get("empty_fire"):
                streak += 1
            else:
                break
        pnl = float(_last.get("realized_pnl") or 0)
        left = float(_last.get("total_left") or 0)
        sys_left = float(_last.get("system_left") or 0)
        user_left = max(left - sys_left, 0.0)
        top_gate = str(_last.get("top_gate") or "")
        pnl_col = "#4be38a" if pnl > 0 else ("#ff6b5e" if pnl < 0 else "#9a9a9a")
        # recent history strip (a capped empty-fire score reads amber)
        strip = " ".join(
            f"<span style='color:{'#ffc36b' if r.get('empty_fire') else '#4be38a' if float(r.get('score') or 0) >= 70 else '#ff6b5e'};'>"
            f"{float(r.get('score') or 0):.0f}</span>"
            for r in _h[-10:])
        lessons = _st.get("lessons") or []
        lesson = lessons[-1].get("lesson", "") if lessons else ""
        html = f"""
<div style='font-family:Audiowide,monospace;'>
  <div style='display:flex;align-items:center;gap:16px;padding:6px 2px;'>
    <div style='font-size:64px;color:{col};line-height:1;'>{s:.0f}</div>
    <div style='flex:1;'>
      <div style='color:{col};font-size:18px;'>🎓 {grade}</div>
      <div class='dim' style='font-size:12px;'>
        rotation {_last.get('rot')} of {_last.get('date')} ·
        streak <span style='color:#4be38a;'>{streak}</span> in a row ≥70 ·
        recent: {strip}
      </div>
    </div>
    <div style='text-align:right;'>
      <div style='color:{pnl_col};font-size:22px;'>${pnl:+,.0f}</div>
      <div class='dim' style='font-size:11px;'>realized this rotation</div>
      <div style='color:{'#ff6b5e' if left > 1 else '#4be38a'};font-size:14px;'>
        ${left:,.0f} left on the table</div>
      {f"<div style='color:#ffc36b;font-size:12px;'>${sys_left:,.0f} by the gates (theoretical)"
       + (f" · {top_gate}" if top_gate else "")
       + f" — user left ${user_left:,.0f}</div>" if sys_left > 1 else
       (f"<div class='dim' style='font-size:11px;'>user left ${user_left:,.0f}</div>"
        if left > 1 else "")}
    </div>
  </div>
  <div class='dim' style='font-size:12px;padding:2px 4px 6px;'>
    🧠 learned: {lesson}
  </div>
</div>"""
        return plate("🎓 DISCIPLINE — did I leave money on the table?", html)
    except Exception as exc:
        logger.debug("discipline plate failed: %s", exc)
        return ""


@st.cache_data(ttl=120)
def _today_facts() -> list[str]:
    """MASTER's self-correction plate: today's numbers, auto-counted —
    TIER-AWARE (user rule, Sep 1: 'everything should auto adjust when the
    contest is selected'): the death line, buffer, and pace all come from
    the contest account (1k/5k/10k), and the banked number counts GOALS,
    not twenties."""
    import sqlite3
    day = _now_mst().strftime("%Y-%m-%d")
    n_sig = n_rej = 0
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.db"))
        n_sig = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE action='buy' AND timestamp LIKE ? "
            "AND source != 'bot_strategy'", (day + "%",)).fetchone()[0]
        n_rej = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE action='buy' AND timestamp LIKE ? "
            "AND status='rejected'", (day + "%",)).fetchone()[0]
        conn.close()
    except Exception:
        pass
    goals_n = 0
    banked = 0
    try:
        import json
        tp = json.loads((Path(__file__).parent / "take_payouts.json").read_text())
        todays = [r for r in tp.values() if str(r.get("ts", "")).startswith(day)]
        goals_n = len(todays)
        banked = sum(int(r.get("amount") or 0) for r in todays)
    except Exception:
        pass
    cw = _load_watch()
    n_open = len(cw)
    # the contest account — every number here scales with the tier
    try:
        import contest_account as _ca_f
        _acct = _ca_f.load_state()
        _line = _ca_f.death_line(_acct)
        _buf = _ca_f.buffer(_acct)
        _pace = _ca_f.daily_pace_usd(_acct)
        _tier = _ca_f.TIERS[_acct["tier"]]["label"]
    except Exception:
        _line, _buf, _pace, _tier = 0.0, 0.0, 0.0, "?"
    _pace_col = "#4be38a" if banked >= _pace else "#ffc36b"
    return [
        f"SIGNALS FIRED <span class='dim'>......</span> {n_sig}"
        f"   BOT PASSED ON <span class='dim'>.....</span> {n_rej}"
        f"   GOALS BANKED <span class='dim'>......</span> "
        f"<span style='color:#4be38a;'>{goals_n} (${banked:,.0f})</span>",
        f"☠ {_tier} LINE <span class='dim'>......</span> ${_line:,.0f}"
        f"   BUFFER <span class='dim'>..........</span> ${_buf:,.0f}"
        f"   PACE <span class='dim'>............</span> "
        f"<span style='color:{_pace_col};'>${banked:,.0f} of ${_pace:,.0f}/day</span>"
        f"   LIVE <span class='dim'>........</span> {n_open}",
        f"<span class='dim'>graded from the real records — signals.db + the goal ledger + "
        f"the contest account. Every number scales with the selected tier. "
        f"This is how the system learns.</span>",
    ]


def render_master_bones(cfg: TradingConfig, kraken):
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
                    _speak("Score reset! The board is clean for the next run!")
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

    # (the RIGHT NOW headline moved to MASTER — the card IS the flow now;
    #  this tab is the BACKUP PICKER: run the same signal process manually)

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
                        ac_invalidate("past_winners")  # the new champion shows immediately
                        _past_winners_core.clear()
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
            # NOTE: the champion only takes over QUICK CHECK when you click
            # the button — visiting this page used to silently switch your
            # QC coin selection on every render (part of the choppiness).
            if _fresh:
                try:
                    st.toast(f"🏆 {_champ_asset} is today's best pick — confirm it below",
                             icon="🏆", duration=12)
                    _speak(f"{_champ_asset} is today's best pick! Confirm it below!")
                    _play_sound("ding")
                except Exception:
                    pass
            if st.button(f"➡ CONFIRM {_champ_asset} IN QUICK CHECK — the final yes/no",
                         type="primary", width="stretch", key="champ_next_btn"):
                st.session_state["qc_pick_next"] = _champ_asset
                st.session_state["qc_auto_scan"] = True   # scan fires on arrival
                st.session_state["qc_hint"] = (f"🏆 {_champ_asset} is today's best pick — "
                                               f"{sig.confidence:.0f}% sure. Scanning it now…")
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
            _speak("No good pick this time. Don't enter. Try again later.")
            _play_sound("alarm")
        except Exception:
            pass

    # champion history
    winners = _past_winners()
    if winners:
        rows = []
        for w in winners:
            icon = "🟢" if w["action"] == "buy" else "🔴" if w["action"] == "sell" else "⏸"
            t = w["timestamp"]
            if t:
                if t.tzinfo is None:
                    t = t.replace(tzinfo=ZoneInfo("UTC"))
                t = t.astimezone(MST)
                ts = t.strftime("%d %b %I:%M%p")
            else:
                ts = "—"
            rows.append(f"{icon} {ts} · {_asset(w['pair']):<9} {w['action'].upper():<4} "
                        f"{w['confidence']:>3.0f}%  TP {_fmt_price(w['take_profit'])}")
        st.markdown(plate("Past Picks", lcd(rows)), unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════
#  QUICK CHECK MODULE — deep scan with CURRENT scanner logic
# ════════════════════════════════════════════════════════════════════════

def render_quick_check(cfg: TradingConfig, kraken):
    sb = _bridge()
    block, day, thr, _, no_buy = current_threshold()

    # ⚡ FAST $20 leaderboard — RETIRED with the scalp game (user rule,
    # Sep 1: "scalping is over with, count the 20's is gone"). The board
    # and its code are kept (don't delete anything) — flip the flag to
    # bring it back.
    FAST_BOARD_ENABLED = False
    if FAST_BOARD_ENABLED:
        try:
            from background_scanner import rotation_key_for_hour, _schedule_for_dow
            _n = _now_mst()
            fast = _fast_twenty(rotation_key_for_hour(_n.hour), _schedule_for_dow(_n.weekday()))
        except Exception:
            fast = []
    else:
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
                    st.session_state["qc_auto_scan"] = True   # scan fires on arrival
                    st.rerun()

    # flow hint: arrived from the tournament with a champion preloaded
    qc_hint = st.session_state.pop("qc_hint", None)
    if qc_hint:
        st.markdown(plate("", lcd([f"<span style='color:#4be38a;font-size:16px;'>{qc_hint}</span>"])),
                    unsafe_allow_html=True)

    # a FAST $20 click lands here (BEFORE the selectbox renders)
    if st.session_state.get("qc_pick_next"):
        st.session_state["qc_asset"] = st.session_state.pop("qc_pick_next")
    # arriving from the card/champion: run the scan AUTOMATICALLY — no
    # second click. The loop is: see the pick → one click → the verdict.
    _auto_scan = st.session_state.pop("qc_auto_scan", False)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        asset = st.selectbox("Pick a coin", ASSETS, index=ASSETS.index("BTC"), key="qc_asset")
        # EVENT-DRIVEN INVALIDATION (the adaptive spec): switching pairs
        # means the old pair's candles + book are the wrong data — bust
        # them so the first read of the new pair is fresh
        if st.session_state.get("_qc_last_asset") not in (None, asset):
            ac_invalidate("fetch_df", "qc_liquidity")
        st.session_state["_qc_last_asset"] = asset
    with c2:
        clicked = st.button("🔍 DEEP SCAN", type="primary", width="stretch", key="qc_btn")
    with c3:
        st.markdown(f"<div style='height:26px'></div>"
                    f"<span class='led a'><i></i>{day} {block} · brain sizes, never blocks</span>",
                    unsafe_allow_html=True)

    # ── run the scan on click; PERSIST the result in session state so it
    # survives the WATCH-button rerun (buttons reset on every rerun)
    if clicked or _auto_scan:
        # EVENT-DRIVEN INVALIDATION (the adaptive spec): running a scan
        # NEEDS fresh candles + a fresh book — the pair switch too
        ac_invalidate("fetch_df", "qc_liquidity")
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
                # THE SCALP GATE (ADVISORY BRAIN, Sep 5): hard gates = the
                # goal gate (the engine's raw move must reach the 1% floor),
                # never during cooldown, and NO DOWNWARD TREND PICKS (user
                # directive, Aug 25: the calibration showed hit-rate parity
                # for falling trends, but the user chooses stuck-case
                # protection over opportunity — their game, their rule).
                # The brain's NUMBER sized the spend above — advisory, it
                # never blocks. Chase/just-paid are amber warnings —
                # displayed, never blocking (no hit-rate separation, and
                # cheaper entries aren't worse entries by the data).
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
                # ── THE GOAL (the rebuild, Sep 1): percentage goals 1-3%
                # set by the market analysis (the engine's expected move
                # for this coin/rotation/day) on a 1-3h window. The $20
                # pace gate retires; the new gate: the coin's RAW expected
                # move must reach the 1% goal floor — a coin the engine
                # says moves 0.3% in this window can't make 1%.
                try:
                    if not hasattr(_bs, "_market_goal_pct"):
                        import importlib
                        _bs = importlib.reload(_bs)
                    # (Sep 4) the goal is the asset's OWN week of daily
                    # candles when the market read is available; the
                    # engine's per-rotation cell is the fallback
                    if hasattr(_bs, "_market_goal_pct"):
                        _goal_pct = _bs._market_goal_pct(kraken, pair)
                    else:
                        _goal_pct = _bs._window_goal_pct(pair)
                    from volatility_engine import get_move_threshold as _gmt
                    from background_scanner import _rotation_window as _rw, _schedule_for_dow as _sd
                    _rwin = _rw(_now_mst())
                    _raw_move = _gmt(pair, _rwin[2] if _rwin else "08-12",
                                     _sd(_now_mst().weekday()), level="standard")
                except Exception:
                    _goal_pct, _raw_move = 1.5, None
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
                # trend: the stuck-case guard — no green lights on confirmed
                # falling knives, no matter how fast the wiggle is
                _trend = _trend_state(df, cfg, float(px))
                # ── BOOK CARRY (user rule, Aug 26: "big money in small markets
                # acts differently — I put too much money in small coins and it
                # fails"). Your $5,000 eating more than 20% of the visible
                # sell-side book means your money IS the market: you push the
                # price up buying in and down selling out — the $20 can't come
                # from a market you're moving. Verdicts: all-in fine / size
                # down to the biggest contest chunk the book carries / too
                # thin, skip. Fail-open when the book can't be read.
                _carry = None
                _spend_usd = float(cfg.challenge_balance)
                try:
                    from execution_tiers import liquidity_snapshot as _ls
                    _snap = _ls(kraken.exchange, pair, _spend_usd)
                    if _snap.get("ok"):
                        _depth = float(_snap["book"]["ask_tob"])
                        if _depth > 0:
                            _carry = _depth * 0.20
                except Exception:
                    _carry = None
                if _carry is not None and _spend_usd > _carry:
                    _fit = [c * float(cfg.challenge_balance) for c in CONTEST_CHUNKS
                            if c * float(cfg.challenge_balance) <= _carry]
                    _spend_usd = max(_fit) if _fit else 0.0   # 0 = too thin
                # ── THE 3% DEATH-LINE GUARD (Kraken Funded rules, encoded
                # Sep 1 — the drift audit found the -3% line was never
                # modeled): size the entry so the coin's WORST NORMAL CASE
                # (its profiled window drawdown from the Volatility Engine)
                # consumes at most ~1/3 of the remaining buffer. Three
                # consecutive worst-cases still leave the account alive.
                # Buffer gone → NO new risk at all (the at-the-line skip).
                _risk_cap = None
                try:
                    import contest_account as _ca
                    _acct = _ca.load_state()
                    _wdd = _ca.window_drawdown_pct(pair)
                    _risk_cap = _ca.max_entry_usd(
                        _acct["current_balance"], _wdd, _acct["start_balance"])
                    # ── THE HAIL MARY EXCEPTION (user rule, Sep 4 — KEPT
                    #    when the lock was removed, Sep 5: the QC AGREES
                    #    with the alert path, never disagree): when the
                    #    account is UNDER break-even (the contest is in
                    #    danger) with platform room and no window this
                    #    rotation, a CHAMPION-grade deep read in a VERIFIED
                    #    uptrend on a book that carries the FULL balance
                    #    opens the one-hour window — the popup is marked,
                    #    the watch clock bounds by the hour. The guard, the
                    #    knife, the exits all stand; the death line never
                    #    yields.
                    _qc_hm = _ca.hail_mary_active(_acct)
                    if not _qc_hm and _risk_cap is not None and _risk_cap > 0:
                        _hm_ok, _hm_why = _ca.hail_mary_available(_acct)
                        if _hm_ok:
                            _hm_big_book = (_carry is not None
                                            and float(_carry) >= float(cfg.challenge_balance))
                            # the champion floor — computed HERE, self-sufficient
                            # (the shared _champ_floor block runs later in this
                            # handler; referencing it would NameError and the
                            # except would silently fail-open the whole guard —
                            # caught in review, Sep 5)
                            try:
                                import background_scanner as _bs_hm
                                if not hasattr(_bs_hm, "popup_floor"):
                                    import importlib
                                    _bs_hm = importlib.reload(_bs_hm)
                                _hm_tpct, _hm_tstate = _bs_hm._fleet_tide()
                                _hm_lw_pts, _hm_lw_note = _bs_hm.lone_wolf_discount(kraken, pair)
                                _hm_floor = float(_bs_hm.popup_floor(_hm_tstate, _hm_lw_pts))
                            except Exception:
                                _hm_floor = 89.0
                            if (sig.action == "buy"
                                    and float(sig.confidence) >= _hm_floor
                                    and _trend == "UP" and _hm_big_book):
                                _acct = _ca.activate_hail_mary(_acct)
                                _qc_hm = True
                            else:
                                _hm_why = ("Hail Mary available but this read doesn't qualify — "
                                           "it needs a champion-grade brain read "
                                           f"(≥{_hm_floor:.0f}%) in a VERIFIED uptrend on a "
                                           "book that carries the full balance")
                                st.session_state["qc_result"]["hail_mary_why"] = _hm_why
                    st.session_state["qc_result"]["hail_mary"] = _qc_hm
                    st.session_state["qc_result"]["risk_cap"] = _risk_cap
                    st.session_state["qc_result"]["window_dd"] = _wdd
                    if _risk_cap <= 0:
                        _spend_usd = 0.0  # at/near the death line — no new risk
                    else:
                        _spend_usd = min(_spend_usd, _risk_cap)
                except Exception as _exc:
                    logger.debug("3%% guard unavailable: %s", _exc)  # fail-open: book sizing stands
                # ── THE ADVISORY BRAIN (user rule, Sep 5 — the QC AGREES
                # with the alert path, never disagrees): the deep read's
                # NUMBER no longer blocks the green light — it SIZES it
                # (the same bucket table every popup uses: >=85 full,
                # 45-54 a fifth, <45 minimum viable). A 45% read on a
                # passing setup is a GREEN at 0.20x size, exactly what the
                # popup would fire. The scan itself (availability +
                # direction) still gates: it ran, or there is no verdict.
                try:
                    from hindsight_scorer import brain_size_mult as _qc_bsm
                    _qc_mult = _qc_bsm(float(sig.confidence))
                except Exception:
                    _qc_mult = 1.0
                st.session_state["qc_result"]["brain_mult"] = _qc_mult
                if _spend_usd > 0:
                    _spend_usd = _spend_usd * _qc_mult
                # ── THE TIDE (user rule, Aug 26): TIDE OUT = no entries
                # anywhere. Correlation means every ship sinks together —
                # one fleet-wide gate instead of per-coin trend checks.
                try:
                    import background_scanner as _bs4
                    if not hasattr(_bs4, "_fleet_tide"):
                        import importlib
                        _bs4 = importlib.reload(_bs4)
                    _tide_pct, _tide_state = _bs4._fleet_tide()
                except Exception:
                    _tide_pct, _tide_state = None, None
                # ── THE FLAT SIZING FACTOR (Sep 7): the QC mirrors the alert
                #    path, never disagrees — FLAT sizes half (the stamped
                #    week: FLAT 22% win vs RISING 57%; the user picked 0.50×).
                if _spend_usd > 0 and (_tide_state or "") == "FLAT":
                    try:
                        import background_scanner as _bs_tsf
                        _spend_usd = _spend_usd * _bs_tsf.TIDE_FLAT_SIZE_FACTOR
                    except Exception:
                        pass
                # re-entry guard: a pair that just paid a big TAKE IT is an
                # exhausted move — re-entering rides the giveback
                _payout = _recent_big_payout(pair)
                # ── ROTATION MEMORY (user rule, Aug 30): one green light per
                # asset per rotation + yesterday's-hour context (blowoff
                # demotion). Same reference frame as the user's instinct:
                # what this coin does AT THIS ROTATION, not just trailing
                # candles.
                try:
                    import background_scanner as _bs5
                    # ROTATION_CONFIRM_WINDOW_MIN is the marker for the
                    # confirmation-window gate (user rule, Aug 31) — reload
                    # if this console process predates it
                    if not hasattr(_bs5, "ROTATION_CONFIRM_WINDOW_MIN"):
                        import importlib
                        _bs5 = importlib.reload(_bs5)
                    _rot_ok, _rot_reason, _rot_reentry, _rot_fine = _bs5.rotation_gate(
                        asset, confirm_window_min=getattr(_bs5, "ROTATION_CONFIRM_WINDOW_MIN", 0.0))
                    _yctx = _bs5.rotation_yesterday_context(pair)
                except Exception:
                    _rot_ok, _rot_reason, _rot_reentry, _rot_fine = True, "", False, 0.0
                    _yctx = None
                # ── LONE WOLF CONTEXT (user rule, Aug 31: "the QC verdict
                # needs to match lone-wolf alerts — information is key,
                # don't ever keep her in the dark"). SAME test as the
                # scanner's alert funnel (identical function, identical
                # constants): a coin moving on her own in a FLAT tide.
                # (Sep 5, ADVISORY BRAIN: the conviction floor retired as
                # a blocker — the context STAYS so she's never in the dark
                # about WHY the system likes this shape; the number now
                # sizes the spend, it never reds the verdict.)
                try:
                    import background_scanner as _bs6
                    if not hasattr(_bs6, "lone_wolf_discount"):
                        import importlib
                        _bs6 = importlib.reload(_bs6)
                    _lw_pts, _lw_note = _bs6.lone_wolf_discount(kraken, pair)
                except Exception:
                    _lw_pts, _lw_note = 0, ""
                st.session_state["qc_result"]["lone_wolf_pts"] = _lw_pts
                st.session_state["qc_result"]["lone_wolf_note"] = _lw_note
                st.session_state["qc_result"]["pace_h"] = _s_h
                st.session_state["qc_result"]["goal_pct"] = _goal_pct
                st.session_state["qc_result"]["raw_move"] = _raw_move
                st.session_state["qc_result"]["chase_pct"] = _chase
                st.session_state["qc_result"]["payout_block"] = _payout
                st.session_state["qc_result"]["trend"] = _trend
                st.session_state["qc_result"]["carry_usd"] = _carry
                st.session_state["qc_result"]["spend_usd"] = _spend_usd
                st.session_state["qc_result"]["tide"] = _tide_state
                st.session_state["qc_result"]["rotation_ok"] = _rot_ok
                st.session_state["qc_result"]["rotation_reason"] = _rot_reason
                st.session_state["qc_result"]["reentry"] = _rot_reentry
                st.session_state["qc_result"]["fine"] = _rot_fine
                st.session_state["qc_result"]["yesterday"] = _yctx
                # ── THE THIN-BOOK BRAIN RULE (user rule, Aug 31: "thin book
                # buys in the contest are noisy... less entries but more
                # money, bigger margins from big coins... it still needs the
                # brain"): a book that can't carry the FULL position only
                # gets a green light when the BRAIN calls it exceptional —
                # the champion floor, same as the alert path (the rotation
                # rail, tide-relaxed / lone-wolf discounted — ONE helper,
                # popup_floor, so the alert and the QC never disagree).
                # Below that: SKIP — thin book, brain not exceptional.
                # POPCAT (74% on a thin book) would have been skipped; ARB
                # passes on the lone-wolf floor.
                # ── HEAD-TO-HEAD CHALLENGE (user rule, Sep 4): the same
                # 5-point challenger window the alert path grants — a thin-
                # book read within POPUP_NEAR_MISS_PTS below the floor is a
                # CHALLENGER green, not a skip (the PUMP lesson: the QC and
                # the alert must never disagree).
                try:
                    if not hasattr(_bs5, "popup_floor"):
                        import importlib
                        _bs5 = importlib.reload(_bs5)
                    _champ_floor = float(_bs5.popup_floor(_tide_state, _lw_pts))
                except Exception:
                    _trdf = float(getattr(_bs5, "TIDE_RISING_DESKTOP_FLOOR", 78.0))
                    _lwdf = float(getattr(_bs5, "LONE_WOLF_DESKTOP_FLOOR", 55.0))
                    _champ_floor = (_trdf if _tide_state == "RISING" else 89.0)
                    if _lw_pts:
                        _champ_floor = max(_lwdf, _champ_floor - _lw_pts)
                # THE THIN-BOOK BRAIN RULE RETIRED (user rule, Sep 5:
                # "remember I said yesterday, 'Big book only'? (It stops me
                # from deep scanning Thin book entries.) I don't like it"):
                # a thin book no longer needs a champion-grade brain read —
                # the standard gates apply (the 60 floor, trend, tide,
                # rotation, goal, guard). What STAYS: the carry sizing
                # (the spend sized to the biggest chunk the book carries)
                # and the TOO THIN block (a book that can't fit even 25%
                # can't be traded — the contest app only takes those
                # chunks). The champ_floor is still computed — it's the
                # ALERT's floor, shown for context (alerts need more than
                # the QC; the QC is your manual check).
                _champ_floor = _champ_floor  # computed above (kept for context)
                _thin_book = (0 < _spend_usd < float(cfg.challenge_balance))
                # THE HAIL MARY WINDOW bounds the watch's clock (one hour,
                # not the rotation) — the QC agrees with the alert path
                _champ_hm_win = bool(st.session_state.get("qc_result", {}).get("hail_mary"))
                st.session_state["qc_result"]["thin_book"] = _thin_book
                st.session_state["qc_result"]["champ_floor"] = _champ_floor
                # ── THE CLIMB-OUT MIRROR (user rule, Sep 6): the QC
                #    AGREES with the alert path, never disagrees — the
                #    same gates (clearance / ER / floor / the bid door),
                #    the same live rules + dial. A coin the alert buried
                #    for no escape route can't green here.
                _climb_ok, _climb_res = True, None
                try:
                    import background_scanner as _bs_cl
                    if not hasattr(_bs_cl, "_climb_out_check"):
                        import importlib
                        _bs_cl = importlib.reload(_bs_cl)
                    _climb_res = _bs_cl._climb_out_check(
                        kraken, pair, float(px),
                        float(_goal_pct or 1.5), float(_spend_usd or 0.0))
                    _climb_ok = bool(_climb_res.get("ok"))
                except Exception:
                    _climb_ok, _climb_res = True, None
                st.session_state["qc_result"]["climb"] = _climb_res
                # HARD GATES: the goal gate + cooldown + no downward trend +
                # the book carries your money + the tide isn't OUT + rotation
                # isn't locked + this hour didn't blow off yesterday. (The
                # brain's NUMBER is advisory since Sep 5 — it sized the
                # spend above; it no longer blocks the green.)
                if (sig.action == "buy" and not no_buy
                        and _trend != "FALLING"
                        and _tide_state != "OUT"
                        and _spend_usd > 0
                        and _rot_ok
                        and _climb_ok
                        and not (_yctx and _yctx.get("shape") == "blowoff")
                        and not (_raw_move is not None and float(_raw_move) < 1.0)):
                    # AUTO-WATCH on green light: the deep scan IS the entry.
                    # The user's system: green → all in, scalp to +$20, out,
                    # re-enter small, green again → buy more. The BOOK CARRY
                    # rule may size the spend down — the watch records the
                    # RECOMMENDED size so the $-counting matches what they
                    # actually hold.
                    _chunk = (round(_spend_usd / max(cfg.challenge_balance, 1), 2), _spend_usd)
                    _brain_size_txt = (f"the brain sizes it {_qc_mult:.2f}x. "
                                       if _qc_mult < 1.0 else "")
                    if _spend_usd >= float(cfg.challenge_balance):
                        _chunk = (1.00, float(cfg.challenge_balance))
                        _chunk_txt = f"buy 100% (${_spend_usd:,.0f}). {_brain_size_txt}"
                    else:
                        _chunk_txt = (f"buy {_chunk[0]:.0%} (${_spend_usd:,.0f}) — "
                                      f"this book carries ${_spend_usd:,.0f}, not all "
                                      f"${cfg.challenge_balance:,.0f}. {_brain_size_txt}")
                    try:
                        w = _load_watch()
                        if pair not in w:
                            w[pair] = {"entry": float(px), "high": float(px),
                                       "size": float(_spend_usd),
                                       "added": _now_mst().isoformat(),
                                       # THE GOAL (Sep 1): the entry carries
                                       # its % goal + window — the light's
                                       # clock and goal-call run on these.
                                       "goal_pct": float(_goal_pct or 1.5),
                                       # (Sep 4) the window is bounded by the
                                       # next rotation boundary — the goal should
                                       # reach before the rotation turns; a HAIL
                                       # MARY entry bounds by the one-hour window
                                       "window_min": (min(float(_bs._rotation_window_min()
                                                           if hasattr(_bs, "_rotation_window_min")
                                                           else max(60.0, min(180.0, (_goal_pct or 1.5) * 60.0))),
                                                          60.0)
                                                       if _champ_hm_win else
                                                       float(_bs._rotation_window_min()
                                                             if hasattr(_bs, "_rotation_window_min")
                                                             else max(60.0, min(180.0, (_goal_pct or 1.5) * 60.0)))),
                                       # THE BRAIN (Sep 3): why we entered —
                                       # the exit check reads this back before
                                       # recommending anything (thesis check).
                                       # A challenger entry names itself: the
                                       # exit check treats a near-miss thesis
                                       # with a tighter hand than a champion.
                                       "why": [f"{sig.action.upper()} {sig.confidence:.0f}% "
                                               f"(green light)",
                                               *sig.reasons[:4]],
                                       "source": "green_light"}
                            _save_watch(w)
                            # EVENT-DRIVEN INVALIDATION (the adaptive spec):
                            # a trade just entered — the book + bot reads
                            # refresh on the next read
                            ac_invalidate("qc_liquidity", "bot_positions")
                            logger.info("AUTO-WATCH: %s added on green light (entry %s, goal %.1f%%)",
                                        pair, px, float(_goal_pct or 1.5))
                            # ROTATION LOCKOUT: green light fired — the asset
                            # is cold until the next rotation boundary
                            try:
                                _bs5.rotation_mark_fired(asset)
                            except Exception:
                                pass
                            # amber warnings ride WITH the green (data shows
                            # they don't change hit rate — but you should know)
                            _warns = []
                            if _chase > QC_CHASE_PCT:
                                _warns.append(f"already ran +{_chase:.1f}%")
                            if _trend == "FALLING":
                                _warns.append("trend falling — don't sit in it")
                            if _payout is not None:
                                _warns.append(f"just paid ${_payout}")
                            _warn_txt = (f"  ⚠ {' · '.join(_warns)}." if _warns else "")
                            _reentry_txt = ""
                            if _rot_reentry:
                                _reentry_txt = (f"RE-ENTRY — fired earlier today"
                                                + (f", fine risk ${_rot_fine:,.0f}" if _rot_fine > 0 else "")
                                                + ". ")
                            # CONFIRMATION WINDOW (Aug 31): say WHY the lockout
                            # didn't bounce this — it's the alert's own green
                            # light being confirmed, not a fresh fire
                            _confirm_txt = ""
                            if str(_rot_reason or "").startswith("confirmation window"):
                                _confirm_txt = f"🔁 {_rot_reason}. "
                            # CHALLENGER (Sep 4; retired with the thin-book
                            # rule, Sep 5): the mark made sense when thin
                            # books needed the champion floor — with the
                            # standard floor a green is a green. The toast
                            # and the voice stay clean.
                            _challenge_txt = ""
                            _challenge_say = ""
                            try:
                                st.toast(f"✅ GO — {asset}: {_confirm_txt}{_reentry_txt}{_chunk_txt}I count the $20s.{_warn_txt}",
                                         icon="✅", duration=20)
                                _play_sound("ding")
                                _sized_down = _spend_usd < float(cfg.challenge_balance)
                                _pct_txt = f"{_chunk[0]:.0%}"
                                _fine_say = (f"Re-entry — fine risk {_rot_fine:,.0f} dollars! "
                                             if _rot_reentry and _rot_fine > 0 else "")
                                if _warns:
                                    _speak(f"Go! {asset}! Buy {_pct_txt}! {_fine_say}But heads up: "
                                           + " and ".join(_warns) + "!", rate=1)
                                elif _sized_down:
                                    # the book sized the spend down — say the
                                    # sizing so the entry is informed
                                    _speak(f"Go! {asset}! Buy {_pct_txt} — the book is thin! "
                                           f"{_fine_say}Aim for plus {_goal_pct:.0f} percent!", rate=1)
                                elif _qc_mult < 1.0:
                                    # ADVISORY BRAIN (Sep 5): the number
                                    # sized this green — say it plainly
                                    _speak(f"Go! {asset}! The brain says {sig.confidence:.0f} "
                                           f"percent — sizing at {_qc_mult:.2f}! "
                                           f"Aim for plus {_goal_pct:.0f} percent!", rate=1)
                                else:
                                    _speak(f"Go! {asset}! Buy 100 percent! "
                                           f"{_fine_say}Aim for plus {_goal_pct:.0f} percent!")
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
                                _speak(f"{asset} is already being watched!")
                                _play_sound("ding")
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.debug("auto-watch add failed: %s", exc)
                else:
                    if (sig.action == "buy" and not no_buy
                            and not _rot_ok):
                        try:
                            st.toast(f"⛔ ALREADY RAN THIS ROTATION — {asset}: {_rot_reason}. "
                                     f"The whipsaw re-entry is where double-dip fines eat the "
                                     f"$20s. Next rotation re-arms it.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"Already ran! {asset} is cold until the next rotation!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and not _climb_ok and _climb_res):
                        try:
                            st.toast(f"🧗 NO ESCAPE ROUTE — {asset}: "
                                     f"{_climb_res.get('fail_reason')}. "
                                     f"{_climb_res.get('escape_line', '')}",
                                     icon="🧗", duration=20)
                            _play_sound("alarm")
                            _speak(f"No escape route! {asset} — "
                                   f"{_climb_res.get('fail_gate', 'the climb-out')} says skip it!",
                                   rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _yctx and _yctx.get("shape") == "blowoff"):
                        try:
                            st.toast(f"⛔ THIS HOUR DUMPED YESTERDAY — {asset}: "
                                     f"{_yctx.get('detail', '')}. Today's signal at this hour is a "
                                     f"re-run of a shape that already paid. Wait for a fresh base.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"No! {asset} dumped at this hour yesterday! Wait for a base!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _tide_state == "OUT" and not _lw_pts):
                        try:
                            st.toast(f"🌊 TIDE'S OUT — only {_tide_pct:.0f}% of the market is rising. "
                                     f"All ships sink together — don't enter anything right now. "
                                     f"Wait for the tide to turn. "
                                     f"(A coin moving +5% or more on her own still gets the "
                                     f"lone-wolf exception — this one isn't.)",
                                     icon="🌊", duration=20)
                            _play_sound("alarm")
                            _speak(f"The tide is out! All ships sink together! Wait!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _trend == "FALLING"):
                        try:
                            st.toast(f"⛔ DOWNWARD TREND — {asset}: the 1h trend is falling and still "
                                     f"making lows. No downward trend picks — wait for it to base. "
                                     f"Skip it.",
                                     icon="⛔", duration=18)
                            _play_sound("alarm")
                            _speak(f"No! {asset} is falling! Wait for it to base!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _risk_cap is not None and _risk_cap <= 0):
                        # THE DEATH LINE (Kraken Funded: -3% from start, never
                        # moves) — no new risk. (Sep 5: the daily lock removed
                        # — the platform line is the only floor left.)
                        try:
                            st.toast(f"☠ AT THE DEATH LINE — {asset}: the account is at "
                                     f"the -3% line. ANY new trade can close the contest. "
                                     f"No entries until the balance recovers above the line.",
                                     icon="☠", duration=25)
                            _play_sound("alarm")
                            _speak(f"No! At the death line! No new trades!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _spend_usd <= 0 and _carry is not None):
                        _pct_book = 100.0 * float(cfg.challenge_balance) / (_carry / 0.20)
                        try:
                            st.toast(f"⛔ TOO THIN — {asset}: your ${cfg.challenge_balance:,.0f} would be "
                                     f"{_pct_book:.0f}% of this coin's order book. Your money IS the "
                                     f"market here — the $20 can't come from a market you're moving. "
                                     f"Skip it.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"Too thin! {asset}! Your money is the market! Skip it!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and _raw_move is not None and float(_raw_move) < 1.0):
                        # THE GOAL GATE (Sep 1): the engine's raw expected
                        # move can't reach the 1% goal floor — this coin
                        # doesn't move enough in this window to make the
                        # goal. The $20-pace "too slow" retired.
                        try:
                            st.toast(f"⛔ TOO SLOW — {asset}: the engine expects only "
                                     f"{float(_raw_move):.1f}% in this window; the goal "
                                     f"needs 1%+. This coin can't get there today. Skip it.",
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
                        # ADVISORY BRAIN (Sep 5): a BUY can no longer land
                        # here — every hard gate has its own branch above,
                        # and the brain's number no longer blocks (it
                        # already sized the spend). This is the sell/hold
                        # verdict; the lone-wolf near-miss voice retired
                        # with the floor.
                        act_txt = {"buy": f"{sig.confidence:.0f}% — sized {_qc_mult:.2f}x (advisory)",
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
    # scalp-gate verdict (Aug 24 redesign; ADVISORY BRAIN, Sep 5): the
    # goal gate decides, cooldown always blocks, chase guard rejects
    # stale moves, and the brain's NUMBER sizes the spend (it no longer
    # floors anything). The old median-bar LED is gone — it rejected 79%
    # PUMP (paid $20 x3) and passed 100% BTC (negative all day). Replay:
    # the bar predicts nothing for scalps.
    pace_h = result.get("pace_h")
    chase_pct = result.get("chase_pct", 0.0)
    payout_block = result.get("payout_block")
    trend = result.get("trend", "UP")
    carry_usd = result.get("carry_usd")
    spend_usd = result.get("spend_usd") or cfg.challenge_balance
    tide_state = result.get("tide")
    # THE GOAL (Sep 1): the coin's raw expected move must reach the 1%
    # goal floor. The $20-pace check retired with the scalp game.
    goal_pct = float(result.get("goal_pct", 1.5) or 1.5)
    raw_move = result.get("raw_move")
    goal_ok = raw_move is None or float(raw_move) >= 1.0
    # HARD gates (ADVISORY BRAIN, Sep 5): the goal gate + no downward trend
    # + book carries your size + tide isn't OUT + rotation not locked + this
    # hour didn't blow off yesterday (+ cooldown in the handler). The
    # brain's NUMBER sized the spend — it no longer greens or reds.
    # Chase/payout warn.
    rot_ok = result.get("rotation_ok", True)
    rot_reason = result.get("rotation_reason", "")
    is_reentry = result.get("reentry", False)
    fine_usd = result.get("fine", 0.0)
    yesterday = result.get("yesterday") or {}
    # LONE WOLF (user rule, Aug 31): the same context the alert funnel uses —
    # a coin moving on her own in a FLAT tide. The number no longer gates
    # (Sep 5); the LED still shows the shape so she's never in the dark
    # about WHY the system likes this coin.
    lw_pts = result.get("lone_wolf_pts", 0) or 0
    lw_note = result.get("lone_wolf_note", "") or ""
    yshape = yesterday.get("shape")
    thin_book = bool(result.get("thin_book"))
    # the ALERT's former floor — context only (Sep 5: the popup floor
    # RETIRED — the brain sizes, it never blocks; this is what the rail
    # WOULD have been, kept for the Hail Mary's champion bar)
    champ_floor = float(result.get("champ_floor", 89.0) or 89.0)
    passes = (sig.action == "buy" and not no_buy
              and goal_ok
              and trend != "FALLING"
              # THE OUT-TIDE LONE-WOLF CARVE-OUT (user rule, Sep 4 — the
              # POPCAT lesson): a TRUE solo runner (own 24h move ≥ the
              # lone-wolf tier — lone_wolf_discount now covers OUT tides)
              # passes the tide gate, same as the alert path (never
              # disagree). Everyone else: OUT stays a hard reject.
              and (tide_state != "OUT" or lw_pts > 0)
              and (result.get("spend_usd", 1) or 0) > 0
              and rot_ok
              and yshape != "blowoff")
              # (Sep 5: the thin-book brain gate RETIRED — the user's call;
              # thin books green at the standard floor, the carry sizing
              # and the TOO THIN block stand)
    momentum_capped = any("momentum ceiling" in r.lower() for r in sig.reasons)

    k = (
        knob(sig.confidence, 0, 100, "Confidence", lambda v: f"{v:.0f}%") +
        knob(sig.volatility_rank or 0, 0, 100, "Volatility", lambda v: f"{v:.0f}") +
        knob(qv / 1e6, 0, 500, "24h Volume", lambda v: f"${v:,.0f}M") +
        knob(sig.risk_reward or 0, 0, 5, "R:R", lambda v: f"1:{v:.1f}")
    )
    st.markdown(plate(f"Quick Check — {asset}", f'<div class="knob-row">{k}</div>'), unsafe_allow_html=True)

    _pace_txt = (f"GOAL +{goal_pct:.1f}% · ENGINE EXPECTS "
                 f"{float(raw_move):.1f}%" if raw_move is not None
                 else f"GOAL +{goal_pct:.1f}% · WINDOW ~{goal_pct:.0f}H")
    def _qc_green_text() -> str:
        """The QC's verdict line — flat, in the rejection order (ALREADY
        RAN → DUMPED YESTERDAY → TIDE OUT → TOO THIN → AT THE LINE →
        DOWNWARD TREND → TOO SLOW → COOLDOWN → the scan's direction).
        (Sep 5: the brain's number is ADVISORY — it sized the spend, it
        no longer greens or reds; a buy that passes every hard gate IS a
        green; the daily lock removed — the platform line is the only
        floor; the Hail Mary KEPT — the account-in-danger exception.)"""
        if passes and result.get("hail_mary"):
            return ("SCALP GREEN LIGHT — 💪 HAIL MARY (the emergency hour is open; "
                    "the death line stands; full allocation = the guard's cap)")
        if passes:
            return "SCALP GREEN LIGHT"
        if no_buy:
            return "COOLDOWN — NO BUYS"
        if not rot_ok:
            return "NO GREEN — ALREADY RAN THIS ROTATION"
        if yshape == "blowoff":
            return "NO GREEN — THIS HOUR DUMPED YESTERDAY"
        if tide_state == "OUT" and not lw_pts:
            return "NO GREEN — TIDE'S OUT"
        if (result.get("spend_usd", 1) or 0) <= 0:
            return "NO GREEN — TOO THIN"
        if (result.get("risk_cap", 1) or 0) <= 0:
            return "NO GREEN — AT THE DEATH LINE (the platform -3% floor is gone — no new risk)"
        if trend == "FALLING":
            return "NO GREEN — DOWNWARD TREND"
        if not goal_ok:
            return (f"NO GREEN — TOO SLOW (engine expects only "
                    f"{float(raw_move):.1f}% in this window; the goal needs 1%+)")
        if sig.action != "buy":
            return (f"NO GREEN — THE SCAN READS {sig.action.upper()} "
                    f"(the direction vetoes; the number never does)")
        # a buy with every hard gate passed IS a green (the brain's number
        # sized it — advisory, Sep 5); this line is the safety net
        return "SCALP GREEN LIGHT"
    _green_txt = _qc_green_text()
    if carry_usd is not None:
        if spend_usd >= float(cfg.challenge_balance):
            _carry_txt, _carry_col = "BOOK CARRIES ALL-IN", "g"
        elif spend_usd > 0:
            # (Sep 5: the brain-floor wording retired with the rule — the
            # LED now just states the sizing fact)
            _carry_txt, _carry_col = (f"⚠ THIN BOOK — SPEND ${spend_usd:,.0f} MAX "
                                      f"(the book carries this, not the full "
                                      f"${cfg.challenge_balance:,.0f})", "a")
        else:
            _carry_txt, _carry_col = "TOO THIN — YOUR MONEY IS THE MARKET", "r"
    else:
        _carry_txt, _carry_col = "BOOK UNREADABLE — SPEND YOUR CALL", "a"
    # OUT is red — EXCEPT the lone-wolf carve-out (amber: the solo runner
    # passes; the 🐺 LED explains why)
    _tcol2 = ("g" if tide_state == "RISING"
              else "a" if tide_state == "FLAT"
              else "a" if (tide_state == "OUT" and lw_pts)
              else "r" if tide_state == "OUT"
              else "a")
    if yshape == "blowoff":
        _yled, _ytxt = "r", f"⚠ YESTERDAY THIS HOUR: BLEW OFF — {yesterday.get('detail', '')}"
    elif yshape == "trend":
        _yled, _ytxt = "g", f"YESTERDAY THIS HOUR: {yesterday.get('detail', '')}"
    elif yshape:
        _yled, _ytxt = "a", f"YESTERDAY THIS HOUR: {yesterday.get('detail', '')}"
    else:
        _yled, _ytxt = "a", "YESTERDAY THIS HOUR: NO DATA"
    # CONFIRMATION WINDOW (user rule, Aug 31): the lockout was set by the
    # alert the user is holding — this QC run is CONFIRMING that green
    # light, not asking for a new one. Shows instead of the cold rejection.
    _confirming = str(rot_reason or "").startswith("confirmation window")
    if _confirming:
        _fine_led, _fine_txt = "a", ("🔁 CONFIRMING THE ALERT — "
                                     + str(rot_reason).replace("confirmation window — ", ""))
    elif fine_usd > 0 and is_reentry:
        _fine_led, _fine_txt = "a", f"⚠ RE-ENTRY — FINE RISK ${fine_usd:,.0f}"
    elif is_reentry:
        _fine_led, _fine_txt = "a", "RE-ENTRY (fired earlier today)"
    else:
        _fine_led, _fine_txt = "g", "FRESH — FIRST FIRE TODAY"
    status_leds = (
        led("LIQUID" if liquid else f"ILLIQUID < ${cfg.min_volume_usd_24h/1e6:.0f}M", "g" if liquid else "r") +
        led(_pace_txt, "g" if goal_ok else "r") +
        led(f"TIDE {tide_state}" if tide_state else "TIDE: NO DATA", _tcol2) +
        (led(f"🐺 LONE WOLF — {lw_note}", "a") if lw_pts else "") +
        led(_carry_txt, _carry_col) +
        led(("ROTATION LOCK — " + (rot_reason or "cold until the boundary")) if not rot_ok
            else "ROTATION: FRESH — GO ALLOWED", "r" if not rot_ok else "g") +
        led(_ytxt, _yled) +
        led(_fine_txt, _fine_led) +
        led(f"{chase_pct:+.1f}% OFF BASE" if chase_pct <= QC_CHASE_PCT
            else f"⚠ CHASED +{chase_pct:.1f}% — pricier entry",
            "g" if chase_pct <= QC_CHASE_PCT else "a") +
        led(f"TREND {trend}" + (" — NO PICKS IN DOWNTRENDS" if trend == "FALLING" else ""),
            "g" if trend == "UP" else ("r" if trend == "FALLING" else "a")) +
        led(f"⚠ JUST PAID ${payout_block} — may be tired" if payout_block is not None else "NO PAYOUT BLOCK",
            "a" if payout_block is not None else "g") +
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
        _notional = sizing.position_size_usd if (sig.entry_price and sizing) else 400.0
        snap = _qc_liquidity(pair, _notional)
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
                       f"will tell you when to sell. Fix your numbers below if the "
                       f"fill differs.")
        else:
            _wa_size = st.number_input("size $ you're putting in", min_value=0.0, value=None,
                                       step=100.0, key="qc_watch_size",
                                       help="what you actually bought — keeps the $-counting real")
            if st.button(f"👁 WATCH {asset} anyway", key="qc_watch", width="stretch",
                         help="Green light adds coins automatically — this is for "
                              "entering on your own conviction (thin books included)"):
                w = _load_watch()
                entry_used = sig.entry_price or px
                g, win = _derive_goal(pair, kraken)
                rec = w.get(pair) or {}
                rec["entry"] = float(entry_used)
                rec["high"] = float(px)
                rec["added"] = _now_mst().isoformat()
                if _wa_size:
                    rec["size"] = float(_wa_size)
                rec.setdefault("goal_pct", g)
                rec.setdefault("window_min", win)
                # THE BRAIN (Sep 3): the scan's own reasons ride along
                rec.setdefault("why", [f"{sig.action.upper()} {sig.confidence:.0f}% (watched anyway)",
                                       *sig.reasons[:4]])
                rec.setdefault("source", "watch_anyway")
                w[pair] = rec
                _save_watch(w)
                st.success(f"◆ {asset} added to the exit auto-watch (entry {_fmt_price(entry_used)}"
                           + (f", ${float(_wa_size):,.0f}" if _wa_size else "")
                           + f", goal {g:.1f}%). Flip on AUTO-WATCH in EXIT CHECK for 1-min exit alerts.")
    with w2:
        # THE ONE ENTRY CONTROL (Sep 3, Option B): same-trade corrections and
        # fresh registrations share one form — contest-app language, keyed
        # updates in same-trade mode so the clock/goal/peak survive.
        _render_entry_control(prefix="qc", default_pair=pair, kraken=kraken)

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

    # ── REAL-TIME PRICE (user rule, Sep 3: "the Exit bar needs real time
    # data check not just what the console says"). The ticker is the truth;
    # the candle close lags. Trail hit, goal call, PnL and the tracked peak
    # all read the ticker. Candles stay for the strategy read (RSI/MACD/BB
    # genuinely need them) — but the in-progress candle is seeded with the
    # live price so the sell bar reacts to the move happening NOW, not the
    # move that closed the last candle.
    live = float(df["close"].iloc[-1])
    try:
        _t = _ticker_px(pair)
        if _t and _t > 0:
            live = float(_t)
            # seed the in-progress candle so indicators see the live move
            _idx = df.index[-1]
            df.loc[_idx, "close"] = live
            df.loc[_idx, "high"] = max(float(df.loc[_idx, "high"]), live)
            df.loc[_idx, "low"] = min(float(df.loc[_idx, "low"]), live)
    except Exception:
        pass  # ticker failed — candle close is the fallback

    sig = _eval(df, cfg)

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
        # the ratchet (user rule, Aug 26): trail tightens on big runs
        try:
            from background_scanner import _ratchet_trail_pct as _rtp
        except ImportError:
            _rtp = None
        if _rtp and ref and contest:
            trail_pct = _rtp(float(ref), high, float(watch_entry.get("size") or 2500.0))
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

    # ── THE GOAL CALL (the rebuild, Sep 1: "scalping is over, count the
    # 20's is gone") ──────────────────────────────────────────────────
    # Every watch entry carries its goal (the engine's expected move at
    # entry, 1-3%). When price reaches it, the console pops the GOAL
    # toast (voice comes from the scanner — no double-talking). The $20
    # level counting is retired with the scalp game.
    try:
        if _load_console_state().get("goal_on",
                                     _load_console_state().get("scalp_on", True)):
            take_level = st.session_state.setdefault("take_level", {})
            for v in watch_status:
                if v["kind"] != "contest":
                    continue
                pair = v["pair"]
                live = v.get("live")
                entry_px = v.get("entry")
                if not live or not entry_px:
                    continue
                w = contest_watch.get(pair, {}) or {}
                goal = float(w.get("goal_pct") or 0) or 1.5
                pct = (live / entry_px - 1) * 100
                glevel = int(pct / goal) if pct > 0 else 0
                if pct < goal * 0.25:
                    take_level.pop(pair, None)
                elif glevel > take_level.get(pair, 0):
                    take_level[pair] = glevel
                    asset = _asset(pair)
                    try:
                        if glevel == 1:
                            st.toast(f"🎯 GOAL — {asset} up {pct:.1f}% (goal {goal:.1f}%) "
                                     f"— take the profit", icon="🎯", duration=15)
                        else:
                            st.toast(f"🎯 {glevel}× GOAL — {asset} up {pct:.1f}%",
                                     icon="🎯", duration=15)
                        _play_sound("ding")
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

    # Aug 30 fix: GET OUT positions are NEVER auto-dropped. One toast + one
    # voice that deletes the tracking was losing exits (user: "if I didn't
    # see or hear the get out and it clears it, I miss my exit"). The red
    # alert repeats every light cycle until the user taps SOLD.
    # (The SOLD button in render_auto_watch is the ONLY thing that clears
    # a contest position — and it advances the flow.)

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


def _render_her_words():
    """💬 HER WORDS — every line Zira said, on screen (user rule, Aug 30:
    "put her notifications on the console so I can see them if my volume
    is off"). Reads voice_log.jsonl written by the scanner's single mouth.
    The LATEST line shows in the expander title — visible at a glance —
    with the full recent history inside."""
    try:
        import json as _json
        import html as _html
        from pathlib import Path as _P
        f = _P(__file__).parent / "voice_log.jsonl"
        if not f.exists():
            return
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return
        rows = []
        latest_txt = ""
        for l in reversed(lines[-12:]):  # newest first
            try:
                d = _json.loads(l)
                ts = str(d.get("ts", ""))
                hhmm = ts[11:16] if len(ts) >= 16 else ""
                txt = str(d.get("text", ""))
                if not latest_txt:
                    latest_txt = txt
                rows.append(
                    f"<div style='padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.08);'>"
                    f"<span class='dim' style='font-family:monospace;'>{hhmm}</span>"
                    f"&nbsp;&nbsp;{_html.escape(txt)}</div>"
                )
            except Exception:
                continue
        if not rows:
            return
        _latest = _html.escape(latest_txt[:70]) + ("…" if len(latest_txt) > 70 else "")
        with st.expander(f"💬 HER WORDS — \"{_latest}\"  (tap to read all)", expanded=False):
            st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception:
        pass


def _render_bots_book(kraken, cfg):
    """🤖 BOT'S BOOK — the bot's real-money trades, tracked on the console
    (user rule, Aug 31: "can the console track it and send the signal
    silently to the bridge side for the bot's trades?"). Bridge filled
    records = the bot's holdings (the two repos' contract); live PnL;
    per-pair 1h trend state — the same knife that sends the silent
    GET OUT NOW (restored Sep 4). 🔴 REVERSED — GET OUT SENT shows while
    a scout_trend_exit sell pends. Console-side: browser F5 loads
    changes, no restart."""
    try:
        import signal_bridge as _sb7
        if not hasattr(_sb7, "get_open_positions"):
            import importlib
            _sb7 = importlib.reload(_sb7)
        _pos = _sb7.get_open_positions() or []
        _pend = set()
        try:
            for _s in (_sb7.get_pending_signals() or []):
                if (_s or {}).get("source") == "scout_trend_exit" and (_s or {}).get("action") == "sell":
                    _pend.add((_s or {}).get("pair"))
        except Exception:
            _pend = set()

        if not _pos:
            with st.expander("🤖 BOT'S BOOK — flat (no open bot trades)", expanded=False):
                st.markdown("<div class='dim'>The bot holds nothing — every trade is closed.</div>",
                            unsafe_allow_html=True)
            return

        # the knife, dup-safe reload (older console processes predate it)
        try:
            import background_scanner as _bs8
            if not hasattr(_bs8, "knife_reversed_1h"):
                import importlib
                _bs8 = importlib.reload(_bs8)
        except Exception:
            _bs8 = None

        rows = []
        title_bits = []
        for _p in _pos:
            _pair = (_p or {}).get("pair") or "?"
            _asset = _pair.split("/")[0]
            _entry = float(_p.get("execution_price") or _p.get("entry_price") or 0.0)
            _px = _ticker_px(_pair)
            _pnl = ((_px - _entry) / _entry * 100) if (_px > 0 and _entry > 0) else None
            _rev = False
            if _bs8 is not None:
                try:
                    _rev, _kn = _bs8.knife_reversed_1h(kraken, _pair, cfg)
                except Exception:
                    _rev = False
            _src = str((_p or {}).get("source") or "")
            _conf = (_p or {}).get("confidence")
            _tstate = str((_p or {}).get("tide_state") or "")
            _pnl_txt = (f"{_pnl:+.1f}%" if _pnl is not None else "—")
            _pnl_col = ("#3fb950" if (_pnl or 0) > 0
                        else ("#f85149" if (_pnl or 0) < 0 else "#9a9a9a"))
            if _pair in _pend:
                _trend_html = "<span style='color:#f85149;font-weight:700;'>🔴 REVERSED — GET OUT SENT</span>"
            elif _rev:
                _trend_html = "<span style='color:#f85149;'>🔴 reversed</span>"
            else:
                _trend_html = "<span style='color:#3fb950;'>🟢 riding</span>"
            _meta = f" · entry {_entry:.4f}"
            if _conf:
                _meta += f" · {float(_conf):.0f}%"
            if _tstate:
                _meta += f" · {_tstate}"
            if _src:
                _meta += f" · {_src}"
            rows.append(
                f"<div style='padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.08);'>"
                f"<b>{_asset}</b> &nbsp;{_trend_html}&nbsp; "
                f"<span style='color:{_pnl_col};font-weight:600;'>{_pnl_txt}</span>"
                f"&nbsp;<span class='dim'>{_meta}</span></div>"
            )
            title_bits.append(f"{_asset} {_pnl_txt}")
        with st.expander(f"🤖 BOT'S BOOK — {'  ·  '.join(title_bits)}", expanded=False):
            st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception:
        pass


def _toast_new_her_words():
    """POP UP her IMPORTANT new lines as console toasts — the volume-off
    popup the user asked for (Aug 30: "so not as a pop up?"). Scanner-side
    lines (GET OUT, TAKE IT, warnings, picks) currently only speak + push
    to the phone; this makes them visible on screen too.
    Urgent lines get the alarm; picks get a ding. Routine position
    updates ("plus 19 dollars, 40 minutes left") stay panel-only — they'd
    spam the popups every minute. First run just sets the marker (no
    replaying history)."""
    try:
        import json as _json
        from pathlib import Path as _P
        f = _P(__file__).parent / "voice_log.jsonl"
        if not f.exists():
            return
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return
        first_run = "her_words_last_ts" not in st.session_state
        last_seen = st.session_state.get("her_words_last_ts", "")
        newest_ts = last_seen
        for l in lines[-20:]:
            try:
                d = _json.loads(l)
                ts = str(d.get("ts", ""))
                text = str(d.get("text", "")).strip()
            except Exception:
                continue
            if not text:
                continue
            if ts > newest_ts:
                newest_ts = ts
            if first_run or ts <= last_seen:
                continue  # history on first run, or already seen
            t = text.lower()
            urgent = any(k in t for k in ("get out", "warning",
                                          "stopped out", "tide", "trade failed"))
            heads_up = any(k in t for k in ("new pick", "buy ", "go!",
                                            "trade done", "sold",
                                            "goal", "take it"))
            try:
                if urgent:
                    st.toast(f"🚨 {text[:120]}", icon="🚨", duration=20)
                    _play_sound("alarm")
                elif heads_up:
                    st.toast(f"💬 {text[:120]}", icon="✅", duration=12)
                    _play_sound("ding")
            except Exception:
                pass
        st.session_state["her_words_last_ts"] = newest_ts
    except Exception:
        pass


def _render_light_panel(interval: str):
    """The always-visible light panel — rendered from the last check."""
    watch_status = st.session_state.get("watch_status") or []
    any_alert = any(v["kind"] == "contest" and v["exit"] for v in watch_status)
    n_wallet = sum(1 for v in watch_status if v["kind"] == "wallet")
    _w = _load_watch()
    # THE HAIL MARY clock rides the panel header (user rule, Sep 4 — kept Sep 5)
    _hm_note = ""
    try:
        import contest_account as _ca_lp
        _hm_left = _ca_lp.hail_mary_minutes_left()
        if _hm_left > 0:
            _hm_note = f" · 💪 HAIL MARY {max(_hm_left, 0):.0f}m (the emergency hour)"
    except Exception:
        pass
    lines = [f"<span class='dim'>{_now_mst().strftime('%I:%M %p')} · every {interval} · "
             f"green = hold, red = sell{_hm_note}</span>"]
    for v in watch_status:
        if v["kind"] != "contest":
            continue
        asset = _asset(v["pair"])
        entry = v.get("entry")
        live = v.get("live")
        rec = _w.get(v["pair"], {})
        size = float(rec.get("size") or 2500.0) if entry and live else 0.0
        pnl_html = ""
        clock_html = ""
        if entry and live:
            pnl = (live / entry - 1) * 100
            usd = pnl / 100.0 * size
            pnl_html = (f' · <span style="color:{"#4be38a" if pnl >= 0 else "#ff6b5e"};">'
                        f'${usd:+,.0f}</span>')
            # THE CLOCK (user directive: green must say how long to hold).
            # (Sep 4) the entry's OWN window is the clock now — set at
            # entry, bounded by the rotation boundary. The old 2x-$20-pace
            # math retired with the scalp game; it stays as the fallback
            # for legacy entries with no window. STAY CLOSE when the goal
            # is nearly paid or the clock is short.
            try:
                _added = datetime.fromisoformat(rec["added"]) if rec.get("added") else _now_mst()
                _win_min = float(rec.get("window_min") or 0.0)
                _elapsed = (_now_mst() - _added).total_seconds() / 60.0
                if _win_min > 0:
                    _rem = _win_min - _elapsed
                else:
                    from background_scanner import _hours_to_scalp
                    _eta = _hours_to_scalp(v["pair"], entry) or 0.75
                    _rem = max(2.0 * _eta * 60.0, 45.0) - _elapsed
                _goal = float(rec.get("goal_pct") or 0.0)
                _near = (_rem <= 15.0) or (_goal > 0 and pnl >= _goal * 0.75)
                clock_html = (f' · <span style="color:{"#ffc36b" if _near else "#8d8271"};">'
                              f'~{max(_rem, 0):.0f}m left'
                              + (" · STAY CLOSE" if _near else " · walk around")
                              + '</span>')
            except Exception:
                pass
        if v["exit"]:
            lines.append(f"<span style='color:#ff6b5e;font-size:16px;'>◆ {asset} — SELL NOW — {v['reasons'][0]}</span>"
                         f"<span class='dim'> · give it about an hour to base — she'll call the next one</span>")
        else:
            bail = v.get("high", 0) * (1 - CONTEST_TRAIL_PCT / 100) if v.get("high") else None
            bail_txt = f" · out at {_fmt_price(bail)}" if bail else ""
            lines.append(f"<span style='color:#4be38a;font-size:16px;'>◆ {asset}</span> {pnl_html}"
                         f"{clock_html}{bail_txt}")
    if n_wallet:
        lines.append(f"<span class='dim'>bot trades: {n_wallet} (watched separately)</span>")
    st.markdown(plate("MY TRADES", lcd(lines, tone="red" if any_alert else "green")),
                unsafe_allow_html=True)
    if any_alert:
        st.error("🚨 SELL NOW — on the contest app.")


def _render_entry_control(prefix: str = "ec", default_pair: str | None = None,
                          kraken=None):
    """THE ONE ENTRY CONTROL (user decision, Sep 3 — Option B): merges the old
    'I RE-ENTERED' form and the Quick Check +/- inputs into ONE form with a
    mode toggle. One language (the contest app's numbers), no ambiguity about
    which tool to reach for.

    Modes (user rule, Sep 3: "when I enter from the deep scan I need to adjust
    to reality this would not need a new time stamp. they are not the same"):
      • SAME TRADE — fix my numbers: keyed updates only. The clock (`added`),
        the goal (`goal_pct`/`window_min`) and the tracked peak all SURVIVE;
        only entry + size change. The peak is floored at the corrected entry.
        This is the deep-scan adjust-to-reality case.
      • NEW TRADE — fresh clock: new `added`, peak resets to the entry, and
        the goal is derived from the engine (preserved if it already exists —
        the old form's full-dict replacement silently wiped it).

    Inputs speak contest-app language: $ you spent + coins you own (primary —
    paid = spent ÷ owned), or entry $ + size $ for those who have the numbers
    directly. Pair is a dropdown of watched coins, or free text to register a
    manual entry the scan never watched (thin-book trades, Sep 3).
    Wrapped in st.form — the light's 60s reruns must not interrupt typing."""
    cw = _load_watch()
    with st.expander("✎ MY ENTRY — fix the numbers or start a fresh clock"):
        with st.form(f"{prefix}_form", clear_on_submit=False):
            mode = st.radio("this is…", ["◉ SAME TRADE — fix my numbers",
                                         "◯ NEW TRADE — fresh clock"],
                            index=0, key=f"{prefix}_mode",
                            help="SAME TRADE keeps the clock, the goal and the peak "
                                 "running — only the numbers change. NEW TRADE restarts "
                                 "the clock: use it after you sold and re-entered, or to "
                                 "register a manual entry fresh.")
            same_trade = mode.startswith("◉")

            # pair: dropdown when watched coins exist, free text otherwise
            # (default_pair pre-selects the scanned coin when present)
            if cw:
                _pairs = list(cw.keys())
                _dflt = default_pair if default_pair in cw else None
                if len(_pairs) > 1:
                    _idx = _pairs.index(_dflt) if _dflt else 0
                    pair = st.selectbox("which coin", _pairs, index=_idx,
                                        format_func=_asset, key=f"{prefix}_pair")
                else:
                    pair = _pairs[0]
            else:
                pair = None
            manual_pair = st.text_input("coin (type it if it's not on the watch yet)",
                                        value=(default_pair or "") if (pair is None or default_pair not in cw) else "",
                                        key=f"{prefix}_manual_pair",
                                        placeholder="e.g. PENGU/USD",
                                        help="for manual entries the scan never watched — "
                                             "thin books the auto-system won't touch")
            if manual_pair.strip():
                _mp = manual_pair.strip().upper()
                pair = _mp if "/" in _mp else f"{_mp}/USD"

            how = st.radio("I'll tell you…", ["◉ $ you spent + coins you own",
                                              "◯ entry $ + size $"],
                           index=0, key=f"{prefix}_how",
                           help="the contest app shows the $ of the trade and how much "
                                "you own — never the price you paid")
            c1, c2 = st.columns(2)
            if how.startswith("◉"):
                with c1:
                    spent = st.number_input("$ you spent", min_value=0.0, value=None,
                                            step=100.0, key=f"{prefix}_spent",
                                            help="the contest app shows this: the amount of your trade")
                with c2:
                    owned = st.number_input("coins you own", min_value=0.0, value=None,
                                            step=0.0001, key=f"{prefix}_owned",
                                            help="the contest app shows this: how much you own")
                entry = size = None
                if spent and owned and float(owned) > 0:
                    entry, size = float(spent) / float(owned), float(spent)
            else:
                with c1:
                    entry = st.number_input("entry $", min_value=0.0, value=None,
                                            step=0.0001, key=f"{prefix}_entry",
                                            help="what you actually paid per coin")
                with c2:
                    size = st.number_input("size $", min_value=0.0, value=None,
                                       step=100.0, key=f"{prefix}_size",
                                       help="what you actually bought — the $ of the trade")
                entry = float(entry) if entry else None
                size = float(size) if size else None

            why_txt = st.text_input("why did you enter? (optional — she'll check "
                                    "the thesis before recommending anything)",
                                    value="", key=f"{prefix}_why",
                                    placeholder="e.g. deep scan green, momentum rider, my own read")

            if st.form_submit_button("✎ SET MY ENTRY", type="primary"):
                # EVENT-DRIVEN INVALIDATION (the adaptive spec): an entry
                # changed — the book + bot reads refresh on the next read
                ac_invalidate("qc_liquidity", "bot_positions")
                if not pair:
                    st.error("Which coin? Type it in the coin field — ANY coin, it doesn't "
                             "have to be on any list (e.g. ZEC, or TAO/USD).")
                elif not entry or entry <= 0:
                    st.error(f"Need the numbers for {_asset(pair)}: either '$ you spent + "
                             f"coins you own' (the two numbers the contest app shows) or "
                             f"'entry $ + size $' — fill both fields of whichever pair you're "
                             f"using.")
                else:
                    w = _load_watch()
                    fresh = pair not in w
                    if fresh or not same_trade:
                        # NEW TRADE (or first registration): fresh clock, peak
                        # resets to the entry, goal preserved or derived fresh
                        # (the old form's dict replacement silently wiped it)
                        rec = w.get(pair) or {}
                        rec["entry"] = float(entry)
                        rec["high"] = float(entry)
                        if size is not None:
                            rec["size"] = float(size)
                        rec["added"] = _now_mst().isoformat()
                        if not rec.get("goal_pct") or not rec.get("window_min"):
                            g, win = _derive_goal(pair, kraken)
                            rec.setdefault("goal_pct", g)
                            rec.setdefault("window_min", win)
                        # THE BRAIN (Sep 3): why you entered — free text, read
                        # back by the exit check's thesis review
                        _why_txt = st.session_state.get(f"{prefix}_why", "").strip()
                        if _why_txt:
                            rec["why"] = [_why_txt]
                        rec.setdefault("source", "manual")
                        w[pair] = rec
                        _save_watch(w)
                        _what = "registered fresh" if fresh else "fresh clock started"
                        st.toast(f"✅ {_asset(pair)}: entry ${entry:.4f}"
                                 + (f" (${size:,.0f})" if size else "")
                                 + f" — {_what}. She counts from now.",
                                 icon="✅", duration=15)
                        _speak(f"{_asset(pair)} entry set! Fresh clock!")
                    else:
                        # SAME TRADE: keyed updates ONLY — the clock, the goal
                        # and the peak keep running. Peak is floored at the
                        # corrected entry (it IS the reference if above).
                        rec = w[pair]
                        rec["entry"] = float(entry)
                        if size is not None:
                            rec["size"] = float(size)
                        rec["high"] = max(float(rec.get("high") or 0.0), float(entry))
                        # added / goal_pct / window_min untouched by design
                        _save_watch(w)
                        st.toast(f"✅ {_asset(pair)}: entry ${entry:.4f}"
                                 + (f" (${size:,.0f})" if size else "")
                                 + " — numbers corrected, the clock keeps running.",
                                 icon="✅", duration=15)
                        _speak(f"{_asset(pair)} numbers corrected!")
                    try:
                        st.rerun(scope="app")
                    except TypeError:
                        st.rerun()


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
        # one compact settings row (Sep 4 smoothness pass): the interval +
        # goal + voice toggles used to stack three full-width rows between
        # the AUTO-WATCH switch and your trades on EVERY page
        _s1, _s2, _s3 = st.columns([1, 1, 1])
        with _s1:
            interval = st.selectbox("Check every", ["1 min", "2 min", "5 min"], index=0, key="watch_interval")
        with _s2:
            goal_on = st.toggle("🎯 GOAL CALL",
                                value=st.session_state.get("goal_on",
                                                           _cs.get("goal_on",
                                                                   _cs.get("scalp_on", True))),
                                key="goal_toggle",
                                help="THE RULES OF THE GAME: every entry carries its own "
                                     "PERCENTAGE goal — set from what THIS coin actually "
                                     "moves at this hour of the week (its own daily ranges, "
                                     "capped by the engine, scaled by the tide), clamped "
                                     "1-3%. The clock runs to the next rotation boundary. "
                                     "She calls 'Goal reached — take the profit!' when it "
                                     "pays, and 'N times the goal!' at each doubling. Not "
                                     "paid by the window's end — get out. Entry size is "
                                     "capped by the 3% death-line guard (the contest "
                                     "survives three worst-cases), so no single trade ever "
                                     "risks the account.")
        if goal_on != _cs.get("goal_on", _cs.get("scalp_on", True)):
            _cs["goal_on"] = goal_on
            _save_console_state(_cs)
        with _s3:
            voice_on = st.toggle("🔊 HER VOICE",
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
        _render_her_words()
        _render_bots_book(kraken, cfg)
        _toast_new_her_words()

        # her-words popup ticker: fast lane (15s) — GET OUT / TAKE IT
        # popups shouldn't wait for the 60s light cycle (user rule, Aug 30:
        # "so not as a pop up?")
        @st.fragment(run_every=15_000)
        def _her_words_tick():
            _toast_new_her_words()

        _her_words_tick()

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
                        # EVENT-DRIVEN INVALIDATION (the adaptive spec): a
                        # trade just executed — the bot book + the QC's
                        # book read refresh on the next read
                        ac_invalidate("bot_positions", "qc_liquidity")
                        _sw = _cw_sold.pop(_sp) or {}
                        _save_watch(_cw_sold)
                        # the re-entered expander follows right below
                        st.session_state.get("take_level", {}).pop(_sp, None)
                        # flow advance moves here (Aug 30): SOLD is now the
                        # only exit — the board clears when YOU clear it
                        st.session_state["flow_exit_at"] = time.time()
                        # ── AUTO PnL + BALANCE (user rule, Sep 2: "stamp the
                        #    real sold time and auto calculate the PnL...
                        #    auto fill when the button is pushed — I can
                        #    manually self adjust later"): the entry's size +
                        #    entry price + the live price at tap time give the
                        #    round trip; the 0.08% RT cost counts (platform
                        #    rule — it hits the target AND the line); the
                        #    contest balance adjusts itself and the trade is
                        #    logged with its sold-time stamp. No size or no
                        #    live price → the manual reminder stands.
                        _say_sold = (f"{_asset(_sp)} sold! Cleared. Ready for the next one! "
                                     f"Update the contest balance in the sidebar — my numbers follow it!")
                        _toast_sold = f"✔ {_asset(_sp)} cleared — next green light re-adds it"
                        _sold_at = _now_mst()
                        try:
                            _entry = float(_sw.get("entry") or 0.0)
                            _size = float(_sw.get("size") or 0.0)
                            _live = None
                            if _entry > 0:
                                try:
                                    _live = float(kraken.exchange.fetch_ticker(_sp)["last"])
                                except Exception:
                                    _live = None
                            if _entry > 0 and _size > 0 and _live:
                                import contest_account as _ca_s
                                _gross, _cost, _net = _ca_s.pnl_net_usd(_entry, _live, _size)
                                _acct_s = _ca_s.load_state()
                                _new_bal = float(_acct_s["current_balance"]) + _net
                                _ca_s.save_state(int(_acct_s["tier"]), _new_bal)
                                _held_min = ""
                                try:
                                    from datetime import datetime as _dts
                                    _added = _sw.get("added")
                                    if _added:
                                        _held_min = f"{(_sold_at - _dts.fromisoformat(_added)).total_seconds() / 60.0:.0f}"
                                except Exception:
                                    pass
                                try:
                                    import json as _jsons
                                    with open("contest_trades.jsonl", "a", encoding="utf-8") as _fs:
                                        _fs.write(_jsons.dumps({
                                            "sold_at": _sold_at.isoformat(),
                                            "pair": _sp, "entry": _entry, "exit": _live,
                                            "size": _size,
                                            "pnl_gross": round(_gross, 2),
                                            "cost_rt": round(_cost, 2),
                                            "pnl_net": round(_net, 2),
                                            "balance_after": round(_new_bal, 2),
                                            "goal_pct": _sw.get("goal_pct"),
                                            "held_min": _held_min,
                                        }) + "\n")
                                except Exception:
                                    pass
                                _sign = "+" if _net >= 0 else "-"
                                _toast_sold = (f"✔ {_asset(_sp)} sold — {_sign}${abs(_net):,.2f} net "
                                               f"(0.08% RT cost incl.) · balance now ${_new_bal:,.0f}")
                                _say_sold = (f"{_asset(_sp)} sold! "
                                             f"{'Plus' if _net >= 0 else 'Minus'} {abs(_net):,.0f} dollars net! "
                                             f"The balance is now {_new_bal:,.0f} dollars! "
                                             f"Adjust me in the sidebar if that's off!")
                                # ── THE PROFIT RE-ARM (user rule, Sep 6: "can
                                #    we carve out the winner exception if I
                                #    collected money (profit)? I want to jump
                                #    back in as soon when it sets up again"):
                                #    a SOLD that books NET PROFIT disarms the
                                #    rotation lock for this asset — the next
                                #    setup can call it again (every gate still
                                #    applies; the fire-time scan re-runs, the
                                #    stale guard and the knife still judge the
                                #    re-entry). A loss or a scratch keeps the
                                #    lock armed — no re-buying what just beat
                                #    you. The state file is shared with the
                                #    running scanner — effective immediately.
                                if _net > 0:
                                    try:
                                        import background_scanner as _bsr
                                        if not hasattr(_bsr, "rotation_rearm_on_profit"):
                                            import importlib
                                            _bsr = importlib.reload(_bsr)
                                        if _bsr.rotation_rearm_on_profit(_asset(_sp)):
                                            _toast_sold += " · 🔁 re-armed"
                                            _say_sold += (f" Profit collected — "
                                                          f"{_asset(_sp)} is re-armed! "
                                                          f"The next setup can call it again!")
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        try:
                            st.toast(_toast_sold, icon="✔", duration=10)
                            _speak(_say_sold)
                        except Exception:
                            pass
                        st.rerun()

        # ── contest watch list: live positions only, no history ────────
        # the re-entered control: right after the SOLD buttons — the loop
        # is sold → re-entered → sold, and it lives on every page
        _render_entry_control(prefix="ec")

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
            # Aug 30 fix: the add controls are a FORM — no reruns/dimming
            # while picking size + asset; everything submits at once on ADD.
            # Entry field removed (user: useless — I RE-ENTERED sets the
            # real entry right after, and it starts at the live price).
            # (Sep 5, user rule: "the manual input +/- positions, it was
            # never designed to pick and choose") — the ADD accepts ANY
            # coin (a free-text field overrides the dropdown: movers and
            # off-list coins too), and a failed price read is a VISIBLE
            # error, never a silent rejection.
            _chunk_opts = {"25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.00}
            with st.form("watch_add_form", clear_on_submit=False):
                f0, f1, f2 = st.columns([1, 1.4, 1.4])
                new_chunk = f0.selectbox("Size", list(_chunk_opts), index=1, key="watch_add_size",
                                         help="How much you bought in the contest app — sets the $ size "
                                              "for P&L and scalp alerts")
                new_asset = f1.selectbox("Asset", ASSETS, key="watch_add_asset",
                                         help="She tracks it from the LIVE price — if you paid "
                                              "something else, tell her in MY ENTRY right after")
                manual_add_coin = f2.text_input(
                    "…or type ANY coin", value="", key="watch_add_manual",
                    placeholder="e.g. ZEC, or TAO/USD — anything",
                    help="Not on the list? Type it here — this overrides the dropdown. "
                         "Movers, off-list coins, anything with a Kraken market. "
                         "The add NEVER picks and chooses.")
                if st.form_submit_button("◆ ADD", type="primary"):
                    npair = f"{new_asset}/USD"
                    _manual_txt = (manual_add_coin or "").strip().upper()
                    if _manual_txt:
                        npair = _manual_txt if "/" in _manual_txt else f"{_manual_txt}/USD"
                    live = live_price(npair)
                    if live:
                        contest_watch[npair] = {
                            "entry": live,
                            "high": live,
                            "size": _chunk_opts[new_chunk] * cfg.challenge_balance,
                            "added": _now_mst().isoformat(),
                        }
                        _save_watch(contest_watch)
                        try:
                            st.toast(f"◆ {_asset(npair)} added — entry {_fmt_price(live)} "
                                     f"(live price). Paid something else? MY ENTRY, right below.",
                                     icon="✅", duration=12)
                            _speak(f"{_asset(npair)} added to the watch!")
                        except Exception:
                            pass
                        st.rerun()
                    else:
                        # (Sep 5) THE VISIBLE REJECTION: a failed price read
                        # used to reject SILENTLY (the button did nothing) —
                        # and the 60s cache made it stick. Now it says so, and
                        # the retry reads fresh (failures are never cached).
                        st.error(f"Couldn't read the live price for {npair} — nothing was "
                                 f"added, and it's NOT a rejection of the coin. Try again "
                                 f"(the retry reads fresh); if it keeps failing, add it in "
                                 f"MY ENTRY below with your real entry price.")

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


@st.cache_data(ttl=300)
def _history_read(_ex, pair: str, dip_pct: float) -> dict | None:
    """REAL HISTORY, no guessing (user directive): count this coin's actual
    episodes over the last 5 days — times it fell at least dip_pct from a
    local peak — and whether/how fast it recovered to that peak. 15-minute
    candles straight from Kraken. Returns {n, recovered, median_min}."""
    try:
        oh = _ex.fetch_ohlcv(pair, "15m", limit=480)
        if not oh or len(oh) < 120:
            return None
        ts = [c[0] for c in oh]
        highs = [c[2] for c in oh]
        lows = [c[3] for c in oh]
        episodes = []          # (recovered, minutes)
        peak = highs[0]
        ep = None              # (start_i, peak)
        for i in range(1, len(oh)):
            if ep is None:
                if highs[i] > peak:
                    peak = highs[i]
                elif peak > 0 and (peak - lows[i]) / peak * 100 >= dip_pct:
                    ep = (i, peak)          # the dip broke the threshold
            else:
                if highs[i] >= ep[1]:       # back to the old peak = recovered
                    episodes.append((True, (ts[i] - ts[ep[0]]) / 60000.0))
                    peak = highs[i]
                    ep = None
        if ep is not None:
            episodes.append((False, None))  # still underwater at data's end
        if len(episodes) < 3:
            return None
        rec = [m for ok, m in episodes if ok]
        return {"n": len(episodes), "recovered": len(rec),
                "median_min": sorted(rec)[len(rec) // 2] if rec else None}
    except Exception:
        return None


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
            # REAL-TIME (Sep 3): the ticker is the price truth — candles stay
            # for the strategy read, seeded with the live price so the exit
            # bar reacts to the move happening now.
            live = float(df["close"].iloc[-1])
            try:
                _t = _ticker_px(pair)
                if _t and _t > 0:
                    live = float(_t)
                    _idx = df.index[-1]
                    df.loc[_idx, "close"] = live
                    df.loc[_idx, "high"] = max(float(df.loc[_idx, "high"]), live)
                    df.loc[_idx, "low"] = min(float(df.loc[_idx, "low"]), live)
            except Exception:
                pass
            sig = _eval(df, cfg)
        except Exception as exc:
            st.error(f"Check failed for {asset}: {exc}")
            return

    # ── entry + position truth (Sep 3: "the manual exit check button needs
    #    to use the real time data + the current position size/data + Goal
    #    knowledge + the brain (why we entered and when). Before it
    #    recommends anything.")
    # Priority: the contest watch record (her ledger: entry, size, peak,
    # clock, goal, thesis) → the bridge (real spot positions) → nothing.
    # The bridge entry alone was blind to contest positions: no size, no
    # goal, no clock, no why — half the recommendation was missing.
    _wr = (_load_watch().get(pair) or {})
    entry = None
    try:
        for p in sb.get_open_positions():
            if p["pair"] == pair:
                entry = p.get("execution_price") or p.get("entry_price")
                break
    except Exception:
        pass
    if _wr.get("entry"):
        entry = float(_wr["entry"])          # the watch is the contest truth
    _w_size = float(_wr.get("size") or 0.0)  # $ of the trade (0 = unknown)
    _w_added = _wr.get("added")
    _w_goal = float(_wr.get("goal_pct") or 0.0)
    _w_window = float(_wr.get("window_min") or 0.0)
    _w_why = _wr.get("why") or []
    _w_high = float(_wr.get("high") or 0.0)  # persistent tracked peak

    # 1. trailing stop — CLONED tool: session-local tracking, read-only bridge
    #    peek. Manual checks never write to the bridge. CONTEST profile:
    #    quick 4% guard (the bot's patient 8% is for spot, not the contest).
    # the ratchet for the exit check too (user rule, Aug 26)
    try:
        from background_scanner import _ratchet_trail_pct as _rtp2
        # the tracked high BEFORE the call (the same read-only sources
        # _cloned_trailing reads): bridge tracking + the session max.
        # (BUGFIX Sep 2: this referenced `high` before it existed — the
        # NameError was swallowed by this except, so the ratchet NEVER
        # fired on this page; every check used the flat 4% default.)
        _ec_high = None
        try:
            _ec_high = sb.get_trailing_high(pair)
        except Exception:
            pass
        _ec_sess = float(st.session_state.get("_manual_highs", {}).get(pair) or 0.0)
        _ec_high = max(float(_ec_high or 0.0), _ec_sess) or None
        _ec_trail = _rtp2(float(entry or live), _ec_high, float(_load_watch().get(pair, {}).get("size") or 2500.0)) \
            if (entry and _ec_high) else CONTEST_TRAIL_PCT
    except Exception:
        _ec_trail = CONTEST_TRAIL_PCT
    high, trail_hit = _cloned_trailing(pair, live, entry, sb, trail_pct=_ec_trail)
    # the watch's persistent peak survives restarts — merge it in so the
    # trail counts from the REAL top, not just this browser session's
    if _w_high:
        high = max(high, _w_high)
        trail_hit = bool(entry and high > entry and live <= high * (1 - _ec_trail / 100))

    # ── THE SYNTHESIS: goal, clock, dollars, thesis — computed BEFORE any
    #    recommendation is rendered ──────────────────────────────────────
    _pct = ((live / entry - 1.0) * 100.0) if (entry and live) else 0.0
    _pnl_usd = (_pct / 100.0 * _w_size) if _w_size else None
    _goal_mult = int(_pct / _w_goal) if (_w_goal and _pct > 0) else 0
    _goal_left = (_w_goal - _pct) if _w_goal else None
    _elapsed_min = _left_min = None
    if _w_added:
        try:
            _ad = datetime.fromisoformat(_w_added)
            if _ad.tzinfo is None:
                _ad = _ad.replace(tzinfo=MST)
            _elapsed_min = (datetime.now(MST) - _ad).total_seconds() / 60.0
            if _w_window:
                _left_min = _w_window - _elapsed_min
        except Exception:
            pass
    _window_expired = bool(_w_window and _left_min is not None and _left_min <= 0)
    # tier-aware hard stop: half the remaining death-line buffer
    # (Sep 5: the daily lock removed — contest_account is the one source
    # of truth and its only floor is the platform line)
    _stop_usd = None
    if _w_size:
        try:
            import contest_account as _ca_ec
            _acct_ec = _ca_ec.load_state()
            _stop_usd = max(_ca_ec.buffer(_acct_ec) * 0.5, 5.0)
        except Exception:
            _stop_usd = None
    _hard_stop_hit = bool(_pnl_usd is not None and _stop_usd and _pnl_usd <= -_stop_usd)
    # thesis check: the brain's entry read vs the current signal
    _thesis_was_buy = any(("BUY" in str(_r).upper()) or ("GREEN" in str(_r).upper())
                          for _r in _w_why)
    _thesis_now = f"{sig.action} {sig.confidence:.0f}%"
    _thesis_ok = (not _thesis_was_buy) or (sig.action == "buy") or \
                 (sig.action == "hold" and sig.confidence < 60)

    # 2. contest sell bar — lower than the bot's (75/60): get out fast
    is_momentum = any("MOMENTUM RIDER" in r for r in sig.reasons)
    sell_thr = CONTEST_SELL_BAR
    strategy_sell = sig.action == "sell" and sig.confidence >= sell_thr
    exhaustion = any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy"
    rider_exit = any("exit signal" in r.lower() for r in sig.reasons)

    should_exit = trail_hit or strategy_sell or exhaustion or rider_exit \
        or _window_expired or _hard_stop_hit
    reasons = []
    if trail_hit and high:
        reasons.append(f"down {(high-live)/high*100:.1f}% from the top")
    if strategy_sell:
        reasons.append(f"trend broke ({sig.confidence:.0f}% red)")
    if exhaustion:
        reasons.append("move's done")
    if rider_exit:
        reasons.append("out of steam")
    if _window_expired:
        reasons.append(f"window expired ({_elapsed_min:.0f} min in, goal not reached)")
    if _hard_stop_hit:
        reasons.append(f"stopped out — down ${abs(_pnl_usd):,.0f} (stop is -${_stop_usd:,.0f})")

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
    ]

    # ── THE BRAIN: why we entered, when, and is the thesis still alive ──
    if _w_why or _elapsed_min is not None:
        _when_txt = (f"{_elapsed_min:.0f} min ago" if _elapsed_min is not None else "—")
        verdict_lines.append(
            f"ENTERED <span class='dim'>...........</span> {_when_txt}"
            + (f" · {' · '.join(str(_r) for _r in _w_why[:2])}" if _w_why else ""))
        _th_col = "#4be38a" if _thesis_ok else "#ff6b5e"
        verdict_lines.append(
            f"THESIS <span class='dim'>............</span> "
            f"<span style='color:{_th_col};'>{_thesis_now} — "
            f"{'still valid' if _thesis_ok else 'FLIPPED against the entry'}</span>")

    # ── THE GOAL: reached? how many times? window state? ───────────────
    if _w_goal:
        if _goal_mult >= 1:
            verdict_lines.append(
                f"GOAL <span class='dim'>...............</span> "
                f"<span style='color:#4be38a;'>{_pct:+.1f}% = {_goal_mult}× the {_w_goal:.1f}% goal — TAKE THE PROFIT</span>")
        else:
            verdict_lines.append(
                f"GOAL <span class='dim'>...............</span> "
                f"{_pct:+.1f}% of the {_w_goal:.1f}% goal — {_goal_left:.1f}% to go")
        if _w_window and _elapsed_min is not None:
            _win_col = "#ff6b5e" if _window_expired else ("#ffc36b" if _left_min < 15 else "#4be38a")
            verdict_lines.append(
                f"WINDOW <span class='dim'>.............</span> "
                f"<span style='color:{_win_col};'>{_elapsed_min:.0f} of {_w_window:.0f} min — "
                + (f"EXPIRED" if _window_expired else f"{_left_min:.0f} min left") + "</span>")

    # ── THE POSITION: real dollars, real size, real peak ───────────────
    if _w_size:
        verdict_lines.append(
            f"POSITION <span class='dim'>..........</span> ${_w_size:,.0f} · "
            + (f"P&L <span style='color:{'#4be38a' if _pnl_usd >= 0 else '#ff6b5e'};'>"
               f"${_pnl_usd:+,.0f}</span>" if _pnl_usd is not None else "P&L —")
            + (f" · peak {_fmt_price(high)} (gave back {(high - live) / high * 100:.1f}%)"
               if high > live else ""))

    verdict_lines.append(
        f"GET OUT AT <span class='dim'>..........</span> "
        + (f"🔴 NOW — down {CONTEST_TRAIL_PCT:.0f}% from the top" if trail_hit
           else f"🟢 {_fmt_price(high * (1 - CONTEST_TRAIL_PCT / 100))}"
           f" <span class='dim'>(top was {_fmt_price(high)})</span>"),
    )
    if sig.action == "sell" and sig.confidence < sell_thr:
        gap = sell_thr - sell_thr + (sell_thr - sig.confidence)
        verdict_lines.append(
            f"HOW SURE <span class='dim'>............</span> {sig.confidence:.0f}% red · "
            f"sell at {sell_thr}% · <span style='color:#4be38a;'>{gap:.0f} points to go</span>")
    else:
        verdict_lines.append(
            f"HOW SURE <span class='dim'>............</span> {sig.confidence:.0f}% · "
            f"sells at {sell_thr}% red")
    # ── ETA to the goal: same Volatility Engine the bot uses — but the
    # watch's own goal (entry × goal%) is the truth when we have one ──
    _goal_px = (entry * (1 + _w_goal / 100.0)) if (entry and _w_goal) else sig.take_profit
    goal_eta = _time_to_goal(pair, live, _goal_px)
    if goal_eta and not should_exit:
        pct_left, hours = goal_eta
        verdict_lines.append(
            f"HITS GOAL IN <span class='dim'>........</span> "
            f"<span style='color:#4be38a;'>{_fmt_hours(hours)}</span> <span class='dim'>(goal {_fmt_price(_goal_px)}, {pct_left:.1f}% away)</span>")
    elif not should_exit and not _goal_px:
        verdict_lines.append("HITS GOAL IN <span class='dim'>........</span> <span class='dim'>no goal on the chart</span>")

    if reasons:
        verdict_lines.append('<span style="color:#ff6b5e;">SELL NOW</span>')
        for r in reasons:
            verdict_lines.append(f"  ✕ {r}")
    elif _goal_mult >= 1:
        verdict_lines.append(
            f'<span style="color:#ffb347;">TAKE THE PROFIT — {_goal_mult}× the goal pays '
            f'{_fmt_price(_goal_px)}' + (f" (${_pnl_usd:+,.0f} on ${_w_size:,.0f})" if _pnl_usd is not None else "")
            + "</span>")
    else:
        verdict_lines.append('<span style="color:#4be38a;">HOLD</span>')

    # ── HISTORY: real episodes, no guessing (user directive, Aug 25) ──
    # For an underwater coin: how often did dips THIS size fill, and how
    # fast? Counted from 5 days of actual 15-minute candles.
    try:
        _peak_ref = max(float(entry or 0), float(high or 0))
        if _peak_ref > live > 0:
            _dip = (_peak_ref - live) / _peak_ref * 100
            if _dip >= 1.0:
                hr = _history_read(kraken.exchange, pair, _dip)
                if hr:
                    rate = hr["recovered"] / hr["n"]
                    med = hr.get("median_min")
                    med_txt = f", median {med:.0f} min" if med else ""
                    col = "#4be38a" if rate >= 0.6 else ("#ffc36b" if rate >= 0.4 else "#ff6b5e")
                    verdict_lines.append(
                        f"HISTORY (5 days) <span class='dim'>....</span> fell {-_dip:.1f}% like this "
                        f"{hr['n']}x — <span style='color:{col};'>{hr['recovered']} came back{med_txt}</span>")
                    if rate >= 0.6:
                        verdict_lines.append("<span class='dim'>history says: HOLD — dips this size usually fill</span>")
                    elif rate < 0.4:
                        verdict_lines.append('<span style="color:#ff6b5e;">history says: GET OUT — dips this size usually don\'t fill</span>')
                    else:
                        verdict_lines.append("<span class='dim'>history says: coin-flip — your call</span>")
    except Exception:
        pass

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
        # (and when the goal is REACHED, the answer is TAKE IT)
        try:
            if _goal_mult >= 1:
                _speak(f"Goal reached! {asset} is up {_pct:.1f} percent — "
                       f"{_goal_mult} times the goal! Take the profit!")
            else:
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
    import contest_account as _ca
    _acct = _ca.load_state()
    _tier_default = _cs.get("tier", _ca.TIERS[_acct["tier"]]["label"])
    tier = st.sidebar.selectbox(
        "Contest account balance",
        _tiers,
        index=_tiers.index(_tier_default) if _tier_default in _tiers else 2,
        help="The strategy sizes positions as % of the account — this must match "
             "your real contest balance or the position sizes will be wrong.",
        key="challenge_tier")
    if tier != _cs.get("tier"):
        _cs["tier"] = tier
        _save_console_state(_cs)
        # a new contest at this tier: the account starts fresh at the line's zero
        _t = int(tier.replace("$", "").replace(",", ""))
        _acct = _ca.save_state(_t, _ca.TIERS[_t]["start"])
    # ── THE CONTEST ACCOUNT (Kraken Funded rules, encoded Sep 1): the
    # current balance is the sizing base; the -3% DEATH LINE never moves.
    _cur = st.sidebar.number_input(
        "Current contest balance ($)", min_value=0.0,
        value=float(_acct["current_balance"]), step=50.0,
        help="Update as the contest account moves. The -3% death line and every "
             "position size are computed from this.")
    if abs(_cur - float(_acct["current_balance"])) > 0.01:
        _acct = _ca.save_state(int(_acct["tier"]), _cur)
    cfg.challenge_balance = float(_acct["current_balance"])
    _line = _ca.death_line(_acct)
    _buf = _ca.buffer(_acct)
    _pace = _ca.daily_pace_usd(_acct)
    _line_col = "r" if _buf <= 0 else ("a" if _buf < _ca.TIERS[_acct["tier"]]["start"] * 0.01 else "g")
    st.sidebar.markdown(plate("☠ CONTEST ACCOUNT", (
        led(f"DEATH LINE ${_line:,.0f}", _line_col) + "<br>" +
        led(f"BUFFER ${_buf:,.0f}", _line_col) + "<br>" +
        led(f"PACE ${_pace:,.0f}/DAY (12%/WK)", "a") + "<br>" +
        led(f"TARGET +12% = ${float(_acct['start_balance']) * 1.12:,.0f}", "g")
    )), unsafe_allow_html=True)
    # ── THE HAIL MARY WINDOW (user rule, Sep 4 — kept Sep 5): when
    #    active, the plate shows the countdown — the emergency hour runs,
    #    the death line stands
    try:
        _hm_left = _ca.hail_mary_minutes_left(_acct)
        if _hm_left > 0:
            st.sidebar.markdown(plate("💪 HAIL MARY", (
                led(f"WINDOW {max(_hm_left, 0):.0f} MIN LEFT", "a") + "<br>" +
                led("THE EMERGENCY HOUR — ACCOUNT IN DANGER, CHAMPION SHOT", "a") + "<br>" +
                led("THE DEATH LINE STANDS — NEVER YIELDS", "r") + "<br>" +
                led(f"WINDOWS USED: {int(_acct.get('hail_mary_count') or 0)} · "
                    f"ONE PER ROTATION", "off")
            )), unsafe_allow_html=True)
    except Exception:
        pass
    # ── THE WORTH FLOOR DIAL (user rule, Sep 2: "should be a dial on the
    #    console — adjustable without Workshop"): the popup gates (champion
    #    + mover) read it LIVE from console_state.json — turning the dial
    #    takes effect on the next alert, no scanner restart. 0% = OFF.
    #    (Sep 4) THE AUTO-DIAL: rotation-aware — the slider becomes the
    #    BASE anchor; the effective dial scales with the hour's real room
    #    (the engine's pace factor), the tide, and the day's catch-up
    #    state. Toggle defaults ON (user, Sep 4).
    _wf_raw = _cs.get("worth_floor_frac")
    _wf = float(_wf_raw) if _wf_raw is not None else 0.25
    _wf_auto_raw = _cs.get("worth_floor_auto")
    _wf_auto = bool(_wf_auto_raw) if _wf_auto_raw is not None else True
    _wf_auto_new = st.sidebar.toggle(
        "🤖 AUTO-DIAL the worth floor (rotation-aware)",
        value=_wf_auto,
        help="ON: the dial below becomes the BASE — the effective floor auto-adjusts "
             "every rotation: x the hour's expected move (the engine's pace factor), "
             "x the tide (RISING relaxes 15%). The scanner and this display read the "
             "SAME math live. OFF: the static dial, as before.",
        key="worth_floor_auto_toggle")
    if _wf_auto_new != _wf_auto:
        _cs["worth_floor_auto"] = _wf_auto_new
        _save_console_state(_cs)
        _wf_auto = _wf_auto_new
    _wf_new = st.sidebar.slider(
        "💰 Worth floor (% of daily pace) — the BASE when auto is on",
        0.0, 1.0, _wf, 0.05, format="%.0f%%",
        help="A popup must be worth it: the sized entry at its goal has to pay at least "
             "this share of the daily pace, or it's bumped. 50% on the 1k tier = a $12 "
             "minimum payout; on 10k = $120. 0% turns the floor off (auto respects OFF).",
        key="worth_floor_dial")
    if abs(_wf_new - _wf) > 1e-9:
        _cs["worth_floor_frac"] = _wf_new
        _save_console_state(_cs)
        _wf = _wf_new
    # the LIVE math — the same factors the scanner gates on (ONE source
    # of truth; dup-safe reload for consoles older than the helper)
    try:
        import background_scanner as _bsw
        if not hasattr(_bsw, "worth_floor_auto_factors"):
            import importlib
            _bsw = importlib.reload(_bsw)
        _wf_f = _bsw.worth_floor_auto_factors()
    except Exception:
        _wf_f = {"auto": _wf_auto, "base": _wf, "pace": 1.0, "tide": 1.0,
                 "effective": _wf}
    _wf_eff = float(_wf_f.get("effective", _wf))
    _wf_usd = _pace * _wf_eff
    if _wf <= 0:
        st.sidebar.caption("Popups must pay ≥ $0 at the goal — floor OFF, every "
                           "gated popup fires. The scanner reads it live — no restart.")
    elif _wf_f.get("auto"):
        st.sidebar.caption(
            f"THIS ROTATION: {_wf:.0%} base × {_wf_f['pace']:.2f} pace × "
            f"{_wf_f['tide']:.2f} tide × {_wf_f.get('actual', 1.0):.2f} actual "
            f"= **{_wf_eff:.0%}** → popups must pay ≥ ${_wf_usd:,.0f} "
            f"(auto — recalculated every rotation, reads the day's ACTUAL "
            f"banked P&L too; live, no restart).")
    else:
        st.sidebar.caption(
            f"Popups must pay ≥ ${_wf_usd:,.0f} at the goal "
            f"({_wf:.0%} of ${_pace:,.0f}/day, static). The scanner reads it live — "
            f"no restart.")
    # ── THE ADVISORY BRAIN + POPUP CAP (user rules, Sep 5 — the entry-
    #    path rebuild): the brain's number SIZES every entry (the bucket
    #    table — ONE source: hindsight_scorer.brain_size_mult, imported by
    #    the scanner AND this console), it never blocks. The scan itself
    #    must run at fire-time (fail-closed — the JUP guarantee, kept).
    #    The popup cap bounds how many BUY popups may fire inside 90 min
    #    so the watch never floods. Both dials read LIVE — no restart.
    _pc_raw = _cs.get("popup_cap")
    _pc = int(_pc_raw) if _pc_raw else 3
    _pc_new = st.sidebar.number_input(
        "🧠 Popup cap (BUY popups per 90 min)", 1, 10, _pc,
        help="The advisory brain fires far more often than the old floor did — no "
             "more than this many BUY popups inside 90 minutes. Bridge writes are "
             "unaffected (the bot's own gates govern real money); this caps the "
             "watch and the voice.",
        key="popup_cap_dial")
    if int(_pc_new) != _pc:
        _cs["popup_cap"] = int(_pc_new)
        _save_console_state(_cs)
        _pc = int(_pc_new)
    # ── THE CLIMB-OUT DIAL (user rule, Sep 6): the strictness
    #    multiplier scales every climb-out clearance requirement.
    #    The scanner reads it LIVE — no restart.
    _cs_str = _cs.get("climb_strictness")
    _cstr = float(_cs_str) if _cs_str is not None else 1.0
    _cstr_new = st.sidebar.slider(
        "🧗 Climb-out strictness (escape-route gates)", 0.5, 2.0, _cstr, 0.1,
        format="%.1fx",
        help="Scales the clearance requirement on every climb-out gate: room to "
             "the nearest major ceiling, the chop bar, the floor cushion. 1.0x = "
             "the calibrated rules; 0.5x = loosen (more setups through); 2.0x = "
             "strict (only pristine escape routes). The weekly gate report tells "
             "you which way the data says to move it.",
        key="climb_strictness_dial")
    if abs(float(_cstr_new) - _cstr) > 1e-9:
        _cs["climb_strictness"] = float(_cstr_new)
        _save_console_state(_cs)
        _cstr = float(_cstr_new)
    try:
        from hindsight_scorer import BRAIN_SIZE_TABLE
        _tbl = " · ".join(f"{int(f)}+→{m:.2f}x" for f, m in BRAIN_SIZE_TABLE)
    except Exception:
        _tbl = "85+→1.00x · 75+→0.75x · 65+→0.50x · 55+→0.33x · 45+→0.20x · <45→0.10x"
    _sn_n = 0
    try:
        _snap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "signal_snapshots.jsonl")
        with open(_snap_path, "r", encoding="utf-8") as _f:
            _sn_n = sum(1 for _ln in _f if _ln.strip())
    except Exception:
        pass
    st.sidebar.caption(
        f"THE BRAIN SIZES, IT NEVER BLOCKS — the scan must run (fail-closed): "
        f"{_tbl}. Every popup and every burial lands in the snapshot dataset "
        f"({_sn_n} logged) — the hindsight scorer learns from both.")
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

    # ── STALENESS-AWARE UI (the adaptive spec's polish layer): one row —
    #    the regime + per-fetcher age, colored (green fresh <10s / yellow
    #    10-30s / orange past ttl / red forced-refresh pending), each with
    #    a hover tooltip ('Last updated 17s ago — next refresh in 13s')
    try:
        st.sidebar.markdown(
            plate("Data Freshness", f"<div style='font-family:Share Tech Mono,"
                                    f"monospace;font-size:11px;line-height:1.7;'>"
                                    f"{freshness_html()}</div>"),
            unsafe_allow_html=True)
    except Exception:
        pass

    st.sidebar.caption("Launch everything from the desktop shortcuts.\nConsole = Scout + Trader in one board.")

    # ── header nameplate ─────────────────────────────────────────────
    n = _now_mst()
    block, day, thr, interval, no_buy = current_threshold()
    vol = volatility_snapshot()
    led_row = (led(f"{day} {block}", 'a') + led(f"BAR {thr}%", 'a') + led(f'SCAN/{interval}m', 'off')
               + led(f"☠ LINE ${_line:,.0f} · ${_buf:,.0f} LEFT", _line_col))
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
            "**One board, two jobs.** It advises YOUR trades on the Kraken Funded "
            "contest. And it runs the bot that trades your real spot account — "
            "that's **BOT'S BOOK**, separate money on its own rules.\n\n"
            "**The contest loop — follow the card:**\n"
            "1. **She hunts around the clock.** Time runs in 4-hour rotations. "
            "Every coin has a history at every hour — she knows what each one "
            "does at THIS hour.\n"
            "2. **A real move clears the bar → POPUP.** The deep scan runs at "
            "fire-time — no scan, no popup, ever. The brain's NUMBER sizes the "
            "entry (a 100% read gets full size, a weak read a small slice — it "
            "never blocks). The popup names the coin, the size, and the out-by "
            "time — and she says it out loud. Popups are for the contest only.\n"
            "3. **THE CARD (MASTER)** carries the pick: WHY the brain likes it, "
            "WHEN it fired, HOW LONG to hold. The goal % is what this coin "
            "really moves this week, and the clock ends at the rotation "
            "boundary.\n"
            "4. **One click = the deep scan.** Every guard gets its say: the -3% "
            "death line, the tide, the trend, the order book, rotation memory. "
            "Green light → enter on the contest app at the size it names. "
            "Anything else → skip; she says why.\n"
            "5. **In the trade, THE LIGHT watches every minute** — green = all "
            "good, red = GET OUT NOW. She talks even with the browser closed. "
            "When the goal pays, she calls it.\n"
            "6. **Sold?** Tap SOLD — she books the P&L and moves the contest "
            "balance herself. Paid a different price, or re-entered? **MY "
            "ENTRY** — one form: fix the numbers, or start a fresh clock.\n"
            "7. **Every rotation close she grades the day** — the DISCIPLINE "
            "SCORE: did you leave money on the table? Right next to TODAY'S "
            "FACTS on MASTER.\n\n"
            "**The other buttons:**\n"
            "- **TOURNAMENT** — run the same hunt yourself, any time. Winners "
            "battle head to head.\n"
            "- **QUICK CHECK** — the deep scan on any coin you pick.\n"
            "- **EXIT CHECK** — 'should I sell?' on any coin, with that coin's "
            "real history.\n\n"
            "**House rules:** only long, never short. Red never means short — it "
            "means *sell it if you've got it, stay out if you don't.* Anything "
            "not green counts as red."
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
