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


def _scanned_assets() -> set | None:
    """The scanner's LIVE scan set, from its own log. The funnel is
    movers-only (the wiggle-trap kill, Sep 21) — the scan's eligible set
    is exactly the pairs on the newest "Movers (>2%)" line, and the line's
    30s cadence makes staleness measurable. Returns None when no movers
    line exists at all (fresh log, restart truncation, or the file moved)
    so the callers can say "unknown" instead of inventing a set. Fail-soft:
    any read problem is None, never an exception out."""
    try:
        import re as _re
        log = Path(__file__).resolve().parent / "logs" / "scanner_console.log"
        with open(log, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        _stamp_re = _re.compile(r"^(\d{1,2}):(\d{2}) (AM|PM) \[")
        _cont_re = _re.compile(r"^\d{1,2}:\d{2} (AM|PM) \[")
        for idx in range(len(lines) - 1, -1, -1):
            ln = lines[idx]
            if "Movers (>2%)" not in ln:
                continue
            m = _stamp_re.match(ln)
            if not m:
                continue
            _now = datetime.now(MST)
            _hh = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
            _t = _now.replace(hour=_hh, minute=int(m.group(2)),
                              second=0, microsecond=0)
            if _t > _now:  # stamp from yesterday (near midnight)
                _t -= timedelta(days=1)
            if (_now - _t).total_seconds() / 60.0 > 30:
                return None          # the funnel's own view is stale
            # the list wraps across continuation lines (the tee wraps at
            # console width): join until the next stamped log line
            text = ln
            k = idx + 1
            while k < len(lines) and not _cont_re.match(lines[k]):
                text += " " + lines[k].strip()
                k += 1
            _, _, rest = text.partition("Movers (>2%)")
            rest = rest.strip().lstrip(":").strip()
            names = [a.strip() for a in rest.split(",") if a.strip()]
            return {n.split()[0].upper() for n in names
                    if n.split() and n.split()[0].isalpha()}
        return None
    except Exception:
        return None

def _now_mst() -> datetime:
    return datetime.now(MST)


# ══ THE PLATE CACHE STRIP (Sep 13) ══════════════════════════════════
# Cache staleness has been audited offline (diag_cachesweep) — this
# makes it OBSERVABLE LIVE: each MASTER plate records a memory-only
# touch when its body runs, and the MASTER diagnostics expander shows
# per-plate HIT (served from cache) vs COMPUTE (body re-executed = the
# ttl expired or the key changed) with the last render age. A stamp
# frozen for longer than its ttl is now visible on the page, not just
# in a txt file. Memory-only (a dict): no files, no locks, nothing to
# persist; per-process, reset by restart — exactly the observability
# horizon cache staleness lives on.
_PLATE_CACHE_LOG: dict[str, dict] = {}
_PLATE_TTLS: dict[str, int] = {}   # name -> ttl seconds ('' = no cache)


def _plate_touch(name: str, cached: bool = True, ttl: int | None = None):
    """Record that a plate's body EXECUTED (a recompute — the cache
    missed or this plate has no cache). Called inside cached bodies so
    it only fires when streamlit actually runs the function."""
    rec = _PLATE_CACHE_LOG.setdefault(
        name, {"computes": 0, "requests": 0, "last_kind": ""})
    rec["computes"] += 1
    rec["last_kind"] = "compute"
    rec["_dirty"] = True   # a compute landed in the current call→render window
    # deliberately NO clock read here: the touch rides CACHED bodies,
    # and a wall-clock read (even metadata-only) would re-open the
    # cachesweep's tier-2 class. The strip's clock lives in
    # _plate_render, on uncached scope.
    if ttl is not None:
        _PLATE_TTLS[name] = ttl
    elif cached and name not in _PLATE_TTLS:
        _PLATE_TTLS[name] = 0    # cached but ttl unknown


def _plate_call(name: str, cached: bool = True, ttl: int | None = None):
    """Record that a plate was REQUESTED this run. If no recompute was
    logged since the request started, the call was served from CACHE
    (kind HIT); _plate_render then stamps the wall-clock render time.
    The _pending flag survives one scope — set at the call site,
    consumed by the renderer at the end of the body."""
    if ttl is not None:
        _PLATE_TTLS[name] = ttl
    rec = _PLATE_CACHE_LOG.setdefault(
        name, {"computes": 0, "requests": 0, "last_kind": ""})
    # the request counter rides the CALL (uncached scope, every rerun)
    # — hit rate = (requests − computes) / requests, the strip's health
    # metric: a cached plate recomputing more often than its ttl
    # implies is keying on something volatile
    rec["requests"] = rec.get("requests", 0) + 1
    rec.setdefault("_pending", set()).add(id(rec))


def _plate_render(name: str, cached: bool = True, ttl: int | None = None):
    """Resolve the pending call: HIT unless a compute landed between
    the call and here (the body re-executed). Stamps the wall-clock
    render time. _plate_call/_plate_render ride UNCACHED scope, so they
    run every rerun; _plate_touch rides the cached body."""
    if ttl is not None:
        _PLATE_TTLS[name] = ttl
    rec = _PLATE_CACHE_LOG.setdefault(
        name, {"computes": 0, "requests": 0, "last_kind": ""})
    # resolve the kind BEFORE clearing anything: pending and its stored
    # set are the same object (clear first = always empty, the probe
    # caught it), and a compute between call and render must win over
    # the HIT the pending call would otherwise claim
    had_pending = bool(rec.get("_pending"))
    had_dirty = bool(rec.get("_dirty"))
    if "_pending" in rec:
        rec["_pending"].clear()
    rec["_dirty"] = False
    if had_dirty:
        rec["last_kind"] = "compute"
    elif had_pending:
        rec["last_kind"] = "HIT"
    rec["last_render"] = time.time()


def _plate_strip_lines() -> list[str]:
    """The per-plate observability rows, MASTER order."""
    order = [("scalp-headline", "⚡ THE PICK data"),
             ("todays-facts", "📐 TODAY'S FACTS"),
             ("past-champions", "🏆 PAST CHAMPIONS data"),
             ("tidebar-verdict", "🌊 TIDE BAR VERDICT"),
             ("flip-grade", "⚖️ FLIP GRADE + trail")]
    now = time.time()
    rows = []
    for name, label in order:
        rec = _PLATE_CACHE_LOG.get(name) or {}
        ttl = _PLATE_TTLS.get(name)
        computes = rec.get("computes", 0)
        requests = rec.get("requests", 0)
        kind = rec.get("last_kind", "") or "—"
        age = rec.get("last_render")
        age_txt = (f"{now - age:,.0f}s ago" if age else "never")
        ttl_txt = f"ttl {ttl}s" if ttl else "no cache (live)"
        # THE HIT-RATE HEALTH METRIC (Sep 13): requests counted at the
        # uncached call site, computes inside the cached body — a cached
        # plate whose body re-runs on most requests is keying on
        # something its ttl doesn't govern. Sample floor 5: a rate off
        # one or two requests is noise, not a verdict. HOT tag: rate
        # under 50% on a cached plate — recomputing more often than
        # the ttl implies (a healthy plate at 30s ttl and ~30s rerun
        # spacing sits far above it). Uncached plates never claim one:
        # they compute every request by design, 0% would read as sick
        # when it's the contract.
        rate_txt, hot_txt = "", ""
        if ttl and requests >= 5:
            rate = (requests - computes) * 100.0 / requests
            rate_txt = f" · {rate:.0f}% hits"
            if rate < 50.0:
                hot_txt = (" · 🔥 HOT — recomputing more often than "
                           "its ttl implies")
        rows.append(f"{label} <span class='dim'>......</span> "
                    f"{ttl_txt} · last render {age_txt} · "
                    f"{computes} compute(s) · last was {kind or '—'}"
                    f"{rate_txt}{hot_txt}")
    return rows


def _render_master_diagnostics():
    """The cache-strip expander for MASTER: per-plate HIT vs COMPUTE,
    last render age, compute counts — staleness made visible live —
    plus a force-recompute button that resets the process cache and
    asks the page to rerun (the next pass recomputes every plate)."""
    with st.expander("🧪 CACHE STRIP — per-plate hit/recompute (live "
                     "staleness)", expanded=False):
        st.markdown(plate(
            "🧪 PLATE CACHE — live",
            lcd(_plate_strip_lines())), unsafe_allow_html=True)
        st.caption("HIT = served from cache (data as old as the ttl "
                   "allows). COMPUTE = the body re-ran (ttl expired or "
                   "the cache key changed). The 'as of' stamps render "
                   "outside the caches — the stamps are always fresh; "
                   "this strip shows how old the DATA behind them is. "
                   "Hit rate = served-from-cache share of requests over "
                   "the process's life; 🔥 HOT marks a cached plate "
                   "recomputing on most requests — a ttl that isn't "
                   "holding its promise.")
        if st.button("↻ FORCE RECOMPUTE (reset the process cache)",
                     key="plate_cache_reset"):
            st.cache_data.clear()
            st.rerun()


def get_kraken_client():
    if _HAS_DB:
        try:
            try:
                setup_secrets()  # optional — secrets.toml may not exist
            except (FileNotFoundError, OSError, ValueError):
                pass  # absent/unreadable secrets.toml is normal — keyring
                      # is the real credential source; weirder errors fall
                      # to the outer handler and get logged
            api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
            api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
            if api_key and api_secret:
                return KrakenAPI(api_key, api_secret)
        except Exception as exc:
            # THE RISKY SWALLOW (Sep 13): a silently dead keyring client
            # traded on the unauthenticated fallback with no signal at
            # all. The fallback itself is by design; the silence was not.
            logger.warning("keyring Kraken client unavailable — "
                           "using unauthenticated ccxt fallback: %s", exc)
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
    except (OSError, ValueError) as exc:
        logger.warning("contest watch file unreadable — "
                       "scoreboard restored empty: %s", exc)
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
    except (AttributeError, ImportError, KeyError, TypeError, ValueError) as exc:
        logger.debug("goal derivation fell back to 1.5%% for %s: %s", pair, exc)
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


# ── THE PREFS MIRROR (Sep 12): her console choices — the audit trail's
# show-sims toggle and the verdict filter — survive closing the browser
# tab, not just Streamlit reruns: each on_change callback mirrors the
# semantic pick into a tiny JSON file, restored at both init sites on
# the next session's boot. Fail-open everywhere (a broken mirror never
# blocks a rerun); SCOUT_PREFS_FILE overrides the path (tests).
def _prefs_path() -> Path:
    import os
    env = os.environ.get("SCOUT_PREFS_FILE")
    return (Path(env) if env
            else Path(__file__).parent / "console_prefs.json")


def _prefs_load() -> dict:
    try:
        import json
        return (json.loads(_prefs_path().read_text(encoding="utf-8"))
                or {})
    except Exception:
        return {}   # fail-open: absent or corrupt prefs read as defaults


def _prefs_save(prefs: dict) -> None:
    try:
        import json, os
        p = _prefs_path()
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(prefs), encoding="utf-8")
        os.replace(tmp, p)   # atomic: no torn file mid-rerun
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("prefs save failed: %s", exc)  # the mirror never
        # blocks the widget's own behavior — a broken save only means
        # her last choice didn't survive the tab close


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
    except (OSError, ValueError):
        pass  # alerts are best-effort — a broken sound never blocks the alert


VOICE_PREFIX = "Mic check one, two. Testing, testing. "

# ONE VOICE ENGINE (user rule, Aug 27: "I never asked for them to be
# separate"): the console does NOT speak directly — a browser page is dead
# when the tab is closed, so a console-spoken message vanishes the moment
# you walk away. Instead the console DROPS every utterance into a speech
# queue that the SCANNER (always on, browser or not) speaks out loud.
# One voice, one vocabulary, one engine.
SPEECH_QUEUE_DIR = Path(__file__).parent / "speech_queue"


def _her_volume() -> int:
    """Her loudness, 0-100 — the console's VOLUME dial (console_state.json
    voice_volume, default 100). The voice itself is always on; this is
    loudness only. Never raises; an unreadable dial is full volume."""
    try:
        import json as _j
        return max(0, min(100, int(json.loads(STATE_FILE.read_text())
                                  .get("voice_volume", 100))))
    except Exception:
        return 100



def _speak(text: str, rate: int = 2):
    """Queue Zira's line for the SCANNER to speak — the one engine that
    works with the browser closed. Fire-and-forget, never blocks, never
    depends on this tab being alive. Her VOICE is always on (the switch
    that could silence it is retired, Sep 21); the console's VOLUME
    dial (0-100) rides along as her loudness in the WAV the scanner
    renders."""
    try:
        SPEECH_QUEUE_DIR.mkdir(exist_ok=True)
        import json as _json, uuid as _uuid
        name = f"{time.time():.6f}_{_uuid.uuid4().hex[:6]}.json"
        tmp = SPEECH_QUEUE_DIR / (name + ".tmp")
        tmp.write_text(_json.dumps({"text": text, "rate": int(rate),
                                    "volume": _her_volume(),
                                    "ts": time.time()}), encoding="utf-8")
        os.replace(tmp, SPEECH_QUEUE_DIR / name)
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("speech queue write failed — voice line dropped: %s", exc)


def _load_console_state() -> dict:
    import json
    try:
        doc = json.loads(STATE_FILE.read_text())
        if isinstance(doc, dict):
            return doc
    except (OSError, ValueError) as exc:
        logger.warning("console state unreadable — tier defaults applied: %s", exc)
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
    except (OSError, ValueError) as exc:
        logger.warning("payout store unreadable — restored empty: %s", exc)
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


# THE FRESH-STAMP DIALS (Sep 13): each cached plate's ttl, shared with
# its call site so the bucketed now_ts and the visible 'as of' stamp
# roll on the SAME clock — one number to change if a ttl ever moves.
HEADLINE_STALE_TTL_S = 90    # _scalp_headline's cache ttl
FACTS_STALE_TTL_S = 120      # _today_facts' cache ttl


@st.cache_data(ttl=HEADLINE_STALE_TTL_S)
def _scalp_headline(_ex, rotation_key: str, sched: str,
                    now_ts: int) -> tuple[list[dict], int]:
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
    Returns (ranked candidates with age_min, total signals considered).
    (Sep 13) NOW RIDES THE CACHE KEY: now_ts is the call site's BUCKETED
    epoch second — every clock read below derives from it, so ages, the
    freshness cutoff, and the big-payout recency check all roll with the
    bucket instead of freezing at the first render (the cachesweep's
    tier-2 class). The visible 'as of' stamp renders OUTSIDE this body
    at the pick card, fresh every fragment tick."""
    _plate_touch("scalp-headline", ttl=HEADLINE_STALE_TTL_S)
    import sqlite3
    from collections import Counter
    from background_scanner import _hours_to_scalp, _max_resolve_hours
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.db"))
        conn.row_factory = sqlite3.Row
        _nw = datetime.fromtimestamp(now_ts, MST)
        since = (_nw - timedelta(minutes=HEADLINE_MAX_AGE_MIN)).isoformat()
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

    # (Sep 13) the clock flows DOWN from the cache key: the helpers'
    # rotation/weekday picks must ride the plate's bucketed time, not
    # a live read frozen for the ttl (the fleet sweep's first catch).
    cap = _max_resolve_hours(now=datetime.fromtimestamp(now_ts, tz=MST))
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
        # (Sep 21: the just-paid skip is retired — a payout record never
        # hides a candidate; the real-data gates do the judging)
        entry = float(r["entry_price"] or 0)
        if entry <= 0:
            continue
        pace = _hours_to_scalp(pair, entry,
                               now=datetime.fromtimestamp(now_ts, tz=MST))
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
        except (AttributeError, ImportError, IndexError, KeyError,
                TypeError, ValueError) as exc:
            logger.debug("falling-knife probe skipped: %s", exc)
        ran_pct = (live - entry) / entry * 100  # displayed on the card —
        # calibration showed chases hit at the same rate (76-80%), so it
        # warns, it doesn't exclude
        vol = float((doc.get("universe", {}).get(pair, {})
                     .get(sched, {}).get(rotation_key) or {}).get("median_volume_usd") or 0)
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            bucket = int(ts.timestamp() // 600)
            age_min = (_nw - ts).total_seconds() / 60
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


def _recent_big_payout(pair: str, *, now_ts: float) -> int | None:
    """RETIRED (user rule, Sep 21: "I don't want a 'just paid guard' — it
    was replaced with real data already"): kept only so stale callers
    can't crash; always returns None. No payout record ever blocks or
    hides a candidate — the real-data gates (stale-move trap, floor,
    tide, chase) judge every entry. take_payouts.json remains a
    display/history record only."""
    return None  # the guard is retired — nothing reads payout recency anymore


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
    _plate_touch("past-champions", ttl=60)
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
    except Exception as exc:
        logger.debug("tide strip unavailable: %s", exc)
    return ""


def _boot_facts_voice(block, no_buy, vol) -> str:
    """THE FACTUAL BOOT GREETING (user rule, Sep 21: 'should be based on
    facts'). Replaces the old 'ready to make you a ton of money' fantasy
    with what is actually true at boot, in her speaking order:
      1. Systems Check   — scanner heartbeat + bot heartbeat
      2. Rotation        — the current market block (or cooldown)
      3. Fleet knowledge — weeks of data behind the dials + noise floor
      4. Tide awareness  — breadth state + percent of the fleet up
      5. Liquidity size  — balance and the buffer above the death line
      6. Realistic goal  — the tier's real daily pace + what's booked today
    Every clause comes from live data; any broken read drops its clause
    (never stalls boot, never invents a number)."""
    rot = " ".join(ch for ch in str(block or "").split() if ch.isascii()) \
        or "unknown"
    scanner_ok = True
    try:
        scanner_ok = bool(scanner_status())
    except Exception as exc:
        logger.debug("boot facts: scanner probe failed: %s", exc)
    bot_note = ""
    try:
        _bs_stat, _bs_note = bot_status()
        _bs_stat = str(_bs_stat).upper()
        # the heartbeat's own status word is health (TRADING/IDLE/…) —
        # only these two verdicts mean the bot is actually down
        if _bs_stat in ("NOT RUNNING", "UNKNOWN"):
            bot_note = (f"the bot is {_bs_stat.lower()}"
                        + (f" ({_bs_note})" if _bs_note else ""))
    except Exception as exc:
        logger.debug("boot facts: bot status probe failed: %s", exc)
    parts: list[str] = []
    # 2. the rotation's market announcement
    parts.append("cooldown rotation, no new buys" if no_buy
                 else f"{rot} rotation")
    # 4. tide awareness
    try:
        import background_scanner as _bsf
        if not hasattr(_bsf, "_fleet_tide"):
            import importlib
            _bsf = importlib.reload(_bsf)
        _pct, _tstate = _bsf._fleet_tide()
        if _tstate:
            parts.append(f"tide {_tstate.lower()}, {_pct:.0f} percent of the fleet up")
    except Exception as exc:
        logger.debug("boot facts: tide unavailable: %s", exc)
    # 3. fleet knowledge: data weeks + noise floor
    try:
        if vol:
            parts.append(f"{float(vol['data_weeks']):.0f} weeks of data behind the dials, "
                         f"noise floor {float(vol['thresholds']['standard']):.1f} percent")
    except Exception as exc:
        logger.debug("boot facts: vol snapshot unavailable: %s", exc)
    # 5. liquidity size vs the death line
    try:
        import contest_account as _ca_b
        _acct = _ca_b.load_state()
        _bal = float(_acct.get("current_balance") or 0.0)
        _start = float(_acct.get("start_balance") or 0.0)
        if _bal > 0:
            parts.append(f"{_bal:,.0f} dollars on the book")
            _buf = _bal - _start * (1 - _ca_b.MAX_LOSS_PCT / 100.0)
            parts.append(f"{_buf:,.0f} above the death line" if _buf > 0
                         else "no buffer above the death line")
    except Exception as exc:
        logger.debug("boot facts: account unavailable: %s", exc)
    # 6. the realistic goal: the tier's daily pace + today's booked PnL
    try:
        import contest_account as _ca_b
        _acct = _ca_b.load_state()
        _pace = (float(_acct.get("start_balance") or 0.0)
                 * (_ca_b.WEEKLY_GOAL_PCT / 100.0) / _ca_b.TRADING_DAYS_PER_WEEK)
        if _pace > 0:
            parts.append(f"pace {_pace:,.0f} a day")
    except Exception as exc:
        logger.debug("boot facts: pace unavailable: %s", exc)
    try:
        import json as _jsonb
        from pathlib import Path as _Pb
        _today = _now_mst().strftime("%Y-%m-%d")
        _real = 0.0
        _any = False
        for _l in (_Pb(__file__).parent / "contest_trades.jsonl") \
                .read_text(encoding="utf-8").splitlines():
            try:
                _r = _jsonb.loads(_l)
                if _r.get("applied") and str(_r.get("sold_at", "")).startswith(_today):
                    _real += float(_r.get("pnl_net") or 0.0)
                    _any = True
            except Exception:
                continue
        # a sub-$1 day says nothing (the all-good lead's rule: never
        # chatter "$0 today" — 19 cents rounds to a lie of precision)
        if _any and round(abs(_real)) >= 1:
            parts.append(f"{abs(_real):,.0f} dollars {'booked' if _real >= 0 else 'down'} today")
    except Exception as exc:
        logger.debug("boot facts: ledger unavailable: %s", exc)
    # 1. the systems-check verdict closes the line
    tail = "systems green, hunting" if (scanner_ok and not bot_note) \
        else "fix the systems before the hunt"
    if bot_note:
        parts.append(bot_note)
    if not parts:
        return "Systems online. Ready to hunt."
    return ". ".join([*parts, tail]) + "."


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
            # the position size fallback is the LIVE contest balance, never
            # a fictional $5k — at a $1k tier the old fallback showed 5x P&L
            size = float(w.get("size") or 0.0)
            if size <= 0:
                try:
                    import contest_account as _ca_w
                    size = float(_ca_w.load_state().get("current_balance") or 0.0)
                except Exception:
                    size = 0.0
            px = live_price(pair)
            if not entry or not px or size <= 0:
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
            # ── LIVENESS (Sep 22, user: "a silent watcher is noticeable
            # instead of invisible"): the scanner stamps each watch row
            # every time the exit watch actually checks it. Fresh shows
            # green, a stale or missing stamp says so — silence is never
            # dressed up as health.
            _age = None
            try:
                _age = time.time() - float(w.get("checked_ts") or 0)
            except Exception:
                _age = None
            if _age is None or _age <= 0 or _age > 86400:
                rows.append("<span style='color:#ffc36b;'>LIVE <span class='dim'>.............</span> "
                            "watcher has not checked yet — give it a minute</span>")
            else:
                _la = _age if _age < 60 else (_age / 60)
                _lv_col = "#4be38a" if _age <= 180 else "#ffc36b"
                rows.append(f"LIVE <span class='dim'>.............</span> "
                            f"<span style='color:{_lv_col};'>checked {_la:.0f} "
                            f"{'s' if _age < 60 else 'm'} ago</span> "
                            + ("" if _age <= 180 else
                               "<span style='color:#ff6b5e;'>— SILENT, the scanner may be down</span>"))
            if overdue:
                rows.append(f"<span style='color:#ff6b5e;'>HOW LONG <span class='dim'>.........</span> "
                            f"PAST THE WINDOW — it hasn't paid its {goal:.1f}% yet. "
                            f"STAY CLOSE — the exit is hers (trend break), never the clock.</span>")
            else:
                rows.append(f"HOW LONG <span class='dim'>.........</span> goal +{goal:.1f}% — "
                            f"now {pct:+.1f}%. After the window, STAY CLOSE — the exit is hers, "
                            f"never the clock.")
            # THE ENFORCED STOP (Sep 21, on the page): the exact trail
            # width the exit watch applies this second — the ratchet's
            # live band (3% base, 2% at a +1% peak, 1.5% at +2%) and,
            # once the goal trail is armed (latched in the watch row),
            # the half-goal cap. Read from the scanner's own math, not a
            # copy — the page can never disagree with the exit.
            _high = max(float(w.get("high") or 0), px)
            _trail_w, _stop_px, _armed = None, None, False
            try:
                import background_scanner as _bs_tr
                if hasattr(_bs_tr, "_ratchet_trail_pct"):
                    _armed = bool(w.get("trail_armed"))
                    _trail_w = float(_bs_tr._ratchet_trail_pct(entry, _high, size))
                    if _armed and goal > 0:
                        _trail_w = min(_trail_w, max(goal * 0.5, 1.0))
                    _stop_px = _high * (1 - _trail_w / 100.0)
            except Exception:
                _trail_w = None
            if _trail_w is not None and _high > entry:
                _tr_col = "#4be38a" if _armed else "#ffc36b"
                _tr_extra = " · goal trail armed — the net is set" if _armed else ""
                if _high > px:
                    _tr_extra += f" · peak {_fmt_price(_high)}"
                rows.append(f"TRAIL <span class='dim'>.........</span> "
                            f"<span style='color:{_tr_col};'>stop {_trail_w:.1f}% "
                            f"under the peak — fires at {_fmt_price(_stop_px)}</span>"
                            f"{_tr_extra}")
            else:
                rows.append("TRAIL <span class='dim'>.........</span> "
                            "<span class='dim'>none yet — the profit guard arms once the run clears entry</span>")
            rows.extend(_guard_breakdown_rows(w, pct))
            rows.append("<span class='dim'>walk around the house — she'll call you</span>")
            rows.append(_tide_line())
            st.markdown(plate("👁 WATCHING", lcd(rows, tone="green" if usd >= 0 else "red")),
                        unsafe_allow_html=True)
        return

    # ── PICK: something fresh to look at ────────────────────────────
    # (Sep 13) call→render bracketing for the cache strip — resolves
    # HIT vs COMPUTE between here and the strip renderer at body's end
    _plate_call("scalp-headline", ttl=HEADLINE_STALE_TTL_S)
    try:
        from background_scanner import rotation_key_for_hour, _schedule_for_dow
        _n = _now_mst()
        # THE TIME AS DATA (Sep 13): the bucketed epoch rides the cache
        # key — ages, the freshness cutoff and the payout recency check
        # roll with the bucket instead of freezing at the first render
        ranked, total = _scalp_headline(
            kraken.exchange,
            rotation_key_for_hour(_n.hour),
            _schedule_for_dow(_n.weekday()),
            now_ts=int(_n.timestamp() // HEADLINE_STALE_TTL_S)
            * HEADLINE_STALE_TTL_S)
    except Exception:
        ranked, total = [], 0
    _plate_render("scalp-headline")
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
        except (AttributeError, ImportError, TypeError) as exc:
            # fail-open: cards render unfiltered rather than not at all —
            # but the skip is no longer silent
            logger.debug("rotation gate filter skipped: %s", exc)
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
                           f"Stay close after {_out_say} — I'll call the exit!")
                except Exception as exc:
                    logger.debug("pick announcement failed: %s", exc)
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
            f"After the window, STAY CLOSE — the exit is hers, never the clock.",
            f"<span class='dim'>{total} signals · {len(ranked)} real moves · this is the best one — "
            f"the popup is watching it, nothing to tap</span>",
            # THE FRESH STAMP (Sep 13): rendered OUTSIDE the cached body,
            # at fragment-tick time — the card's clock can never freeze
            f"<span class='dim'>as of {_n.strftime('%H:%M')} MST — "
            f"this card refreshes itself every 30 seconds</span>",
            _tide_line(),
        ]
        st.markdown(plate("⚡ THE PICK", lcd(rows, tone="green")), unsafe_allow_html=True)
        # NO CONFIRM BUTTON (user rule, Sep 21: phone-only — "I cannot always
        # click on the console but I go in when it fires the popup"): the
        # popup ROUTE auto-tracks the pick into the watch at fire time
        # (background_scanner.auto_track_popup → contest_watch.json), so the
        # card is a look, not a gate. A confirm button here pretended the
        # entry waited on a tap it never needed.
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
    # run_every is SECONDS in Streamlit (ints pass through raw —
    # time_util.time_to_seconds only converts timedeltas/strings).
    # These were written as milliseconds (30_000 = 8.3 HOURS), which
    # is why every auto-refresh in the app only ever moved on clicks.
    @st.fragment(run_every=30)
    def _card_tick():
        _pick_card(cfg, kraken)
    _card_tick()

    # THE DISCIPLINE SCORE (user spec, Sep 4: "Did I leave money on the
    # table?") — the closing rotation's grade, side-by-side with the
    # contest results, with the lesson learned and the one insight
    st.markdown(_discipline_plate(), unsafe_allow_html=True)
    # THE FRESH STAMP (Sep 13): every MASTER plate carries a visible
    # clock — the discipline plate re-reads its state each rerun, so
    # the stamp is naturally fresh (consistency, not cache)
    st.caption(f"as of {_now_mst().strftime('%H:%M')} MST — the discipline "
               f"grade re-reads its rotation close each rerun")

    # 📞 TONIGHT'S REVIEW (Sep 21): the cooldown check's per-asset
    # verdicts on the page — read the review, don't wait for her to
    # speak it. Sits beside the score it grades.
    _render_cooldown_review()

    # 🕳️ THE FUNNEL, LIVE (Sep 23): every candidate THIS rotation — its
    # quick/brain read, the bar it faced, which gate buried it (or
    # FIRED). The rotation-scoped view; the burial strip below keeps
    # the all-time record.
    _render_funnel_plate()

    # 🚫 WHAT THE FUNNEL BURIED (user rule, Sep 4, the POPCAT lesson: the
    # scanner's suppression verdicts used to print to the console window
    # only — a buried payer took three forensics scripts to name its
    # gate). The suppression log is now persistent and shown here: every
    # gate, its numbers, newest first.
    _render_suppression_strip()

    # TODAY'S FACTS — the system grades itself from real records (user
    # directive: MASTER is for self-correction on facts and figures —
    # signals fired, twenties banked, what the bot rejected — no guessing)
    # THE FRESH STAMP (Sep 13): the graded day rides the bucketed cache
    # key; the visible 'as of' renders OUTSIDE the cached body — fresh
    # every rerun, never a frozen timestamp
    _fn = _now_mst()
    _plate_call("todays-facts", ttl=FACTS_STALE_TTL_S)
    st.markdown(plate("📐 TODAY'S FACTS — the system grades itself",
                      lcd(_today_facts(
                          now_ts=int(_fn.timestamp() // FACTS_STALE_TTL_S)
                          * FACTS_STALE_TTL_S))), unsafe_allow_html=True)
    _plate_render("todays-facts")
    st.caption(f"as of {_fn.strftime('%H:%M')} MST — the graded day rolls "
               f"with the numbers, never frozen")

    # THE TIDE BAR VERDICT (Sep 11): the weekly outcome-vs-quantile
    # hindsight on MASTER — the per-state bars, the $/fire deltas, and
    # the week's realized fills, without opening the report file. The
    # expander is the click-through: the full report history, newest
    # first, capped so a year of sections cannot stall the page.
    # (Sep 13) call→render bracketing for the cache strip: the plate
    # body is cached, this scope runs every rerun — a HIT vs COMPUTE
    # resolves between here and the strip renderer at the body's end.
    _plate_call("tidebar-verdict", ttl=300)
    # THE FRESH STAMP (Sep 13): captured BEFORE the cached call, rendered
    # AFTER it — outside the cached body, so a 5-minute ttl can never
    # freeze the plate's clock (the frozen-stamp class stays closed)
    _tv_n = _now_mst()
    st.markdown(_tidebar_verdict(
        sims_hidden=_sims_hidden_mirror(),
        verdict=_verdict_filter_mirror()),
        unsafe_allow_html=True)
    _plate_render("tidebar-verdict")
    st.caption(f"as of {_tv_n.strftime('%H:%M')} MST — the verdict plate "
               f"recomputes on its data (5-min cache); this clock never "
               f"freezes")
    # THE MASTER-LEVEL VERDICT FILTER (Sep 13): the control lives HERE,
    # beside the chart it drives — one pick and the whole page syncs
    # (chart stamps, benched spans, trail rows); the trail resolves
    # the same pick without its own widget.
    _render_verdict_control()
    # THE FLIP GRADE plate (Sep 11): the bench machinery's latest
    # per-state verdicts — pooled dollars, proximity, aftermath — one
    # glance, no file to open (refreshed after each weekly pass).
    _plate_call("flip-grade")
    st.markdown(_flipgrade_verdict(), unsafe_allow_html=True)
    _plate_render("flip-grade")
    # THE CONFIRM-TO-APPLY (Sep 13): the arc-3 sweep STAGES its
    # evidence-backed POOL_MIN proposal (stability-gated on priced
    # re-arms, net, and the rolling windows); the machinery never moves
    # the gate on its own — THIS is her hand. Rendered outside the plate
    # so the verdict stays pure; the state reader is widget-free, the
    # apply rides the module's guarded apply_staged_pool_min().
    import tidebar_weekly as _tbw
    _pg_apply = _poolgate_apply_state()
    if _pg_apply.get("stale"):
        st.caption(
            f"POOL_MIN proposal staged at {_pg_apply['pool_min']:+.2f} "
            f"is stale — {_pg_apply['stale_reason']}; the next grade "
            f"re-stages before anything can apply.")
    elif _pg_apply:
        st.warning(
            f"**POOL_MIN proposal staged:** default → "
            f"{_pg_apply['pool_min']:+.2f} · net "
            f"{_pg_apply['net']:+,.2f} across "
            f"{_pg_apply['n_priced']} priced real re-arms, positive "
            f"across the 4-week and 8-week rolling windows. Confirming "
            f"writes tide_bar_poolmin_default.json — the flip-back gate "
            f"reads it on its next decision — and WARNS in the scanner "
            f"log.")
        if st.button("Apply POOL_MIN default", key="poolgate_apply_btn",
                     type="primary"):
            _ares = _tbw.apply_staged_pool_min()
            if _ares.get("applied"):
                st.success(
                    f"POOL_MIN default applied: "
                    f"{_ares['pool_min']:+.2f} — the flip-back gate "
                    f"demands a recovery worth that much before "
                    f"re-arming.")
            else:
                st.error(f"Apply refused: {_ares.get('reason')}")
    _render_tidebar_history()
    # THE FLIP AUDIT TRAIL (Sep 11): every auto-flip / flip-back with
    # its evidence weeks, one click down — the regime story in full.
    with st.expander("⚖️ FLIP AUDIT TRAIL — every bench decision and the "
                     "evidence that made it"):
        st.markdown(_render_flip_audit(), unsafe_allow_html=True)

    # the bones — every tool and number still there, one click down
    with st.expander("📐 THE BONES — money score, breaker, integrity"):
        render_master_bones(cfg, kraken)

    # THE CACHE STRIP (Sep 13): per-plate HIT vs COMPUTE with the last
    # render age — staleness observable LIVE on the page, not only in
    # diag_cachesweep's offline audit.
    _render_master_diagnostics()


def _funnel_plate_rows(rot_key: str, sup_rows: list, fire_rows: list):
    """THE FUNNEL PLATE's row builder — pure, so the drive can grade it
    without streamlit or the ledgers. (total, [html]): every candidate
    read this rotation, newest first, each line its quick read, the bar
    it faced, and the gate that buried it (or FIRED). A sub-45 quick is
    dimmed — the funnel's noise floor, not a near-miss."""
    merged = [("sup", d) for d in sup_rows] + [("fired", d) for d in fire_rows]
    merged.sort(key=lambda kd: str(kd[1].get("ts", "")), reverse=True)
    total = len(merged)
    out = []
    for kind, d in merged[:14]:
        ts = str(d.get("ts", ""))[11:16]
        gate = "FIRED" if kind == "fired" else str(d.get("gate", "?"))
        gcol = ("#4be38a" if kind == "fired"
                else "#ff6b5e" if gate in ("tide-out", "death-line")
                else "#ffc36b")
        quick = d.get("quick")
        try:
            qv = float(quick)
            qtxt = (f"<span class='dim'>quick {qv:.0f}</span>" if qv < 45
                    else f"quick {qv:.0f}")
        except Exception:
            qtxt = "quick ?"
        bar = d.get("bar", d.get("floor"))
        try:
            btxt = f"vs bar {float(bar):.0f}"
        except Exception:
            btxt = "vs bar —"
        brain = d.get("brain")
        try:
            brain_txt = f" · brain {float(brain):.0f}"
        except Exception:
            brain_txt = ""
        detail = str(d.get("detail", ""))[:110]
        out.append(
            f"<div style='padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.08);"
            f"font-family:Share Tech Mono,monospace;font-size:12.5px;color:#d8ccb4;'>"
            f"<span class='dim'>{ts}</span>&nbsp;&nbsp;<b>{_asset(str(d.get('pair', '?')))}</b>"
            f"&nbsp;&nbsp;<span style='color:{gcol};'>{gate}</span>"
            f"&nbsp;&nbsp;{qtxt} <span class='dim'>{btxt}{brain_txt}</span><br>"
            f"<span class='dim'>{detail}</span></div>")
    return total, out


def _render_funnel_plate():
    """🕳️ THE FUNNEL, LIVE (Sep 23): every candidate the scanner has read
    THIS rotation — its quick (and brain, when consulted) read, the bar
    it faced, and which gate buried it (or FIRED, from the snapshot
    ledger's kind='fired' rows). The rotation-scoped sibling of the
    burial strip below: the burial log keeps the all-time record, this
    plate shows the rotation the scanner is working right now, scoped
    by the same rot key the scanner stamps on every candidate. Read-
    only — the scanner computes, this plate renders (one-brain)."""
    try:
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _P
        import background_scanner as _bs1
        now = _now_mst()
        rot = _bs1.rotation_key_for_hour(now.hour)
        today = now.date()

        def _rot_rows(fname, kinds):
            f = _P(__file__).parent / fname
            if not f.exists():
                return []
            out = []
            for l in reversed(f.read_text(encoding="utf-8").splitlines()[-400:]):
                try:
                    d = _json.loads(l)
                except Exception:
                    continue
                if str(d.get("rot", "")) != rot:
                    continue
                try:
                    if _dt.fromisoformat(str(d.get("ts", ""))).date() != today:
                        continue
                except Exception:
                    continue
                if kinds is not None and d.get("kind") not in kinds:
                    continue
                out.append(d)
            return out

        sup = _rot_rows("popup_suppressions.jsonl", None)
        fired = _rot_rows("signal_snapshots.jsonl", {"fired"})
        total, rows = _funnel_plate_rows(rot, sup, fired)
        if total == 0:
            st.markdown(
                f"<div class='dim' style='font-family:Share Tech Mono,monospace;"
                f"font-size:12px;padding:2px 2px 6px;'>🕳️ THE FUNNEL, LIVE — "
                f"nothing buried and nothing fired yet in {rot} MST. The plate "
                f"fills as the scanner cycles (a light pass every ~45s, a full "
                f"scan up to 10 min).</div>", unsafe_allow_html=True)
            return
        with st.expander(
                f"🕳️ THE FUNNEL, LIVE — every candidate read this rotation "
                f"({rot} MST): score vs the bar it faced, and the gate that "
                f"buried it ({total} candidate read{'s' if total != 1 else ''})"):
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.caption(
                f"as of {now.strftime('%H:%M')} MST — rotation {rot}; re-reads "
                f"the scanner's own ledgers live each rerun. Showing newest "
                f"{len(rows)} of {total}. Bars move with the tide: tide-out "
                f"prices each rotation's payer rate.")
    except Exception as exc:
        logger.debug("funnel plate failed: %s", exc)


def _render_suppression_strip():
    """🚫 MONEY LEFT ON THE TABLE — the popups the funnel buried, newest
    first (popup_suppressions.jsonl, written by the scanner at every
    suppression: brain-floor, tide-out, worth-floor, stale-move, deep-veto,
    downward-trend, too-thin, death-line, fail-closed). The raw
    record — the discipline plate's 'left by the gates' number is the paid
    subset of exactly this."""
    # ── THE EASE LINE (Sep 21, page level): when the journal's relief
    # points are live, the funnel is being ASKED to ease — that signal
    # sits on the page, no tap required. Gates ride the burial ledger
    # (always present once the journal has graded any rotation); the
    # full per-gate table stays in the expander below. Fail-soft.
    try:
        import discipline as _dc_ez
        _ez_gates = []
        try:
            _st_ez = _dc_ez.load_state()
            for _h in (_st_ez.get("burial_ledger") or []):
                for _g in (_h.get("gates") or {}):
                    if _g and _g not in _ez_gates:
                        _ez_gates.append(_g)
        except Exception as exc:
            logger.debug("funnel-relief strip: discipline state unread: %s", exc)
        if _ez_gates:
            _ez = {g: float(_dc_ez.journal_release_pts(g)) for g in _ez_gates}
            _live = [(g, p) for g, p in
                     sorted(_ez.items(), key=lambda kv: -kv[1]) if p > 0]
            if _live:
                _txt = " · ".join(f"{g} {p:.2f} pts" for g, p in _live)
                st.markdown(
                    f"<div style='font-family:Share Tech Mono,monospace;"
                    f"font-size:12.5px;padding:2px 2px 6px;color:#ffc36b;'>"
                    f"⚑ the funnel is being asked to ease: {_txt}"
                    f"<span class='dim'> — above 0, the gate can release a "
                    f"near-bar champion</span></div>", unsafe_allow_html=True)
            else:
                st.markdown(
                    "<div class='dim' style='font-family:Share Tech Mono,"
                    "monospace;font-size:12px;padding:2px 2px 6px;'>⚑ the "
                    "funnel is strict — no gate carries release fuel right "
                    "now</div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("release fuel strip skipped: %s", exc)
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
            gcol = ("#ff6b5e" if gate in ("tide-out", "death-line")
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
                # THE JOURNAL'S RELEASE FUEL (Sep 21, on the page): each
                # gate's LIVE relief points — the same number the journal
                # release consumes at the deep-veto and tide-out gates.
                # Above 0, the gate can release a near-bar champion; at
                # 0.00 it is closed. Read-only, fail-soft.
                try:
                    import discipline as _dc_rel
                    _gates = []
                    for _rl in reversed(lines[-200:]):
                        try:
                            _g = str(_json.loads(_rl).get("gate") or "")
                            if _g and _g not in _gates:
                                _gates.append(_g)
                        except Exception:
                            continue
                    try:
                        _st_d = _dc_rel.load_state()
                        for _h in (_st_d.get("burial_ledger") or []):
                            for _g in (_h.get("gates") or {}):
                                if _g and _g not in _gates:
                                    _gates.append(_g)
                    except Exception as exc:
                        logger.debug("release fuel: discipline state unread: %s", exc)
                    if _gates:
                        _pts = {g: _dc_rel.journal_release_pts(g) for g in _gates}
                        _rel_rows = []
                        for _g in sorted(_gates, key=lambda g: -_pts[g]):
                            _c = "#4be38a" if _pts[_g] > 0 else "#8a8171"
                            _rel_rows.append(
                                f"<div style='font-family:Share Tech Mono,monospace;"
                                f"font-size:12.5px;color:#d8ccb4;padding:2px 0;'>"
                                f"<span style='color:{_c};'>{_g}</span>"
                                f"<span class='dim'> ......... </span>{_pts[_g]:.2f} pts</div>")
                        st.markdown(
                            "<div style='padding:8px 2px 0;font-family:Share Tech Mono,"
                            "monospace;font-size:12px;color:#8a8171;'>THE JOURNAL'S "
                            "RELEASE FUEL — live relief per gate (buried payers − duds, "
                            "capped 4, decaying over 8h). Above 0, the gate can release "
                            "a near-bar champion; at 0.00 it is closed:</div>"
                            + "".join(_rel_rows), unsafe_allow_html=True)
                except Exception as exc:
                    logger.debug("release fuel points skipped: %s", exc)
                # THE FRESH STAMP (Sep 13): every MASTER plate carries a
                # visible clock — this strip re-reads its file each rerun,
                # so the stamp is naturally fresh (consistency, not cache)
                st.caption(f"as of {_now_mst().strftime('%H:%M')} MST — "
                           f"the funnel's burial log, read live each rerun")
    except Exception as exc:
        logger.debug("suppression strip failed: %s", exc)


def _no_pick_advice() -> str:
    """The bracket's 'no good pick' earns a diagnostic, not just words
    (Sep 10): the funnel's RECENT REJECTS (popup_suppressions.jsonl,
    last 90 min — the scanner's every burial, so the answer covers
    champions, movers, breakout coils and the near-misses alike) are
    graded here: the closest calls, what buried them, the tide's price
    if the tide is the bouncer. Fail-open: an unreadable log says so."""
    try:
        import json as _json2
        from datetime import datetime as _dt2, timedelta as _td2
        f = Path(__file__).parent / "popup_suppressions.jsonl"
        if not f.exists():
            return "The funnel has logged no rejections yet this session."
        cutoff = (_dt2.now().astimezone() - _td2(minutes=90)).isoformat()
        rows = []
        for l in f.read_text(encoding="utf-8").splitlines()[-300:]:
            try:
                d = _json2.loads(l)
            except Exception:
                continue
            if str(d.get("ts", "")) >= cutoff:
                rows.append(d)
        if not rows:
            return ("No funnel rejections in the last 90 minutes — the "
                    "scanner hasn't buried anything recently.")

        def _bar(d):
            """The gate's bar: the floor field, or the learned bar named
            in the tide-out detail (the breakout coils' bar lives in the
            text). None = no bar on the record."""
            try:
                return float(d["floor"])
            except (KeyError, TypeError, ValueError):
                pass  # no floor field — fall through to the detail text
            try:
                import re as _re2
                m = _re2.search(r"vs learned bar (\d+)",
                                str(d.get("detail", "")))
                if m:
                    return float(m.group(1))
            except (TypeError, ValueError):
                pass  # no learned bar in the detail either — no bar
            return None

        def _gap(d):
            """Points below the gate's bar (99 = no bar on the record)."""
            b, q = _bar(d), d.get("quick")
            if b is None or q is None:
                return 99.0
            return b - float(q)

        bits = []
        close = sorted((d for d in rows if _gap(d) <= 8.0), key=_gap)[:3]
        if close:
            bits.append("closest to firing: " + ", ".join(
                f"{_asset(str(d.get('pair', '?')))} {float(d['quick']):.0f}% "
                f"vs {_bar(d):.0f}%" for d in close))
        counts = {}
        for d in rows:
            g = str(d.get("gate", "?"))
            counts[g] = counts.get(g, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
        bits.append("busiest gates: " + ", ".join(f"{g} ×{n}" for g, n in top))
        return "; ".join(bits)
    except Exception:
        return "the funnel's rejection log is unreadable right now"


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


def _render_cooldown_review():
    """\U0001F4DE TONIGHT'S REVIEW (user, Sep 21: "show the per-asset verdicts and
    money-left-on-the-table breakdown in the console so I can read
    tonight's review without waiting for her to speak"). Reads the DAY
    rows the scanner's 20:00 cooldown review writes into
    discipline_state.json. Two layers, each independent:

      \u2022 THE VERDICT-HISTORY STRIP \u2014 one glyph per DAY row (oldest
        \u2192 newest, 30-day cap), colored by the day's worst leak, so the
        pattern over time reads at a glance even on days whose review
        predates per-asset rows.
      \u2022 THE ASSET VERDICTS \u2014 the latest DAY row's per-asset verdict
        rows plus the money-left breakdown, when that row carries them.

    Fail-soft: a day with neither layer renders nothing (the plate has
    not run); a broken state file renders nothing."""
    try:
        import discipline as _disc
        _st = _disc.load_state()
        _hist = _st.get("history") or []
        _day = next((h for h in reversed(_hist)
                     if h.get("rot") == "DAY"), None)
        assets = (_day or {}).get("assets") or []
        _days = [h for h in _hist if h.get("rot") == "DAY"][-30:]
        if not assets and not _days:
            return
        _VC = (("took the goal", "#4be38a"),
               ("exited too soon", "#ffc36b"),
               ("stayed too long", "#ff6b5e"),
               ("bought the top", "#ff6b5e"),
               ("goal hit and never exited", "#ff6b5e"),
               ("buried by the funnel", "#9a86ff"),
               ("still holding under the goal", "#ffc36b"),
               ("scratch", "#9a9a9a"), ("unreadable", "#9a9a9a"))

        def _vcol(v):
            for k, c in _VC:
                if k in str(v):
                    return c
            return "#d8ccb4"
        def _day_row_html(a):
            kind = str(a.get("kind", "?"))
            v = str(a.get("verdict", "?"))
            left = float(a.get("usd_left") or 0)
            if kind == "buried":
                detail = (f"gate {a.get('gate') or '?'} · "
                          f"${left:,.0f} theoretical")
            else:
                entry = float(a.get("entry") or 0)
                peak = float(a.get("peak") or 0)
                run = (peak / entry - 1.0) * 100.0 if entry and peak else 0.0
                detail = (f"peak +{run:.1f}% vs goal "
                          f"{float(a.get('goal_pct') or 0):.1f}% · "
                          f"realized ${float(a.get('realized') or 0):,.2f}"
                          + (f" · ${left:,.0f} left" if left > 1 else ""))
            return (f"<div style='padding:4px 0;"
                    f"border-bottom:1px solid rgba(255,255,255,0.08);"
                    f"font-family:Share Tech Mono,monospace;font-size:12.5px;"
                    f"color:#d8ccb4;'><b>{_asset(str(a.get('asset', '?')))}</b>"
                    f"&nbsp;<span class='dim'>({kind})</span>&nbsp;&nbsp;"
                    f"<span style='color:{_vcol(v)};'>{v}</span><br>"
                    f"<span class='dim'>{detail}</span></div>")

        def _day_bd_md(a_list):
            cls = {}
            for a in a_list:
                k = str(a.get("verdict", "?"))
                cls[k] = cls.get(k, 0.0) + float(a.get("usd_left") or 0.0)
            total = sum(cls.values())
            bd = " · ".join(
                f"<span style='color:{_vcol(k)};'>{k.split(' - ')[0]} "
                f"${v:,.0f}</span>"
                for k, v in sorted(cls.items(), key=lambda kv: -kv[1]) if v > 0)
            if not bd:
                return ""
            return (f"<div style='font-family:Share Tech Mono,monospace;"
                    f"font-size:12.5px;padding:8px 2px 2px;color:#d8ccb4;'>"
                    f"<b>${total:,.0f} left on the table</b> — {bd}</div>")

        rows = [_day_row_html(a) for a in assets]
        bd = _day_bd_md(assets)

        # \u2500\u2500 THE VERDICT-HISTORY STRIP \u2500\u2500 one glyph per DAY row,
        # colored by the day's worst leak. Rows written before
        # top_verdict existed derive their worst leak from the
        # persisted usd_left component map; a day with nothing left on
        # the table is a green glyph.
        _COMP = (("system_burial", "buried by the funnel", "#9a86ff"),
                 ("giveback", "gave back after the goal", "#ffc36b"),
                 ("missed_exit", "goal hit, never exited", "#ff6b5e"),
                 ("early_exit", "exited too soon", "#ffc36b"),
                 ("missed", "missed the popup", "#ffc36b"))
        _glyphs = []
        for _h in _days:
            _d = str(_h.get("date"))[5:]
            _tv = _h.get("top_verdict")
            _total = float(_h.get("total_left") or 0)
            _name, _usd, _col = "clean - nothing leaked", 0.0, "#4be38a"
            if _tv:
                _name = str(_tv[0])
                _usd = float(_tv[1] or 0.0)
                _col = _vcol(_name)
            elif _total > 1:
                _ul = _h.get("usd_left") or {}
                _ck = max(_COMP, key=lambda t: float(_ul.get(t[0]) or 0.0))
                _usd = float(_ul.get(_ck[0]) or 0.0)
                if _usd > 0:
                    _name, _col = _ck[1], _ck[2]
            _glyphs.append(
                f"<span style='color:{_col};font-size:15px;' "
                f"title='{_d}: {_name} - ${_usd:,.0f} left'>\u25a0</span>")
        _strip = ""
        if _glyphs:
            _strip = (
                "<div style='font-family:Share Tech Mono,monospace;"
                "font-size:12px;padding:8px 2px 0;color:#d8ccb4;'>"
                "the book's leaks, day by day (oldest \u2192 newest): "
                + "".join(_glyphs)
                + "<span class='dim' style='font-size:11px;'>"
                "&nbsp; green clean \u00b7 amber exited early \u00b7 red held too "
                "long \u00b7 violet buried by the funnel</span></div>")


        # ── THE WEEK WARNING (user, Sep 21): one verdict class atop the
        # book for a week straight — the alarm the page shows outright.
        try:
            _strk_n, _strk_name, _strk_usd = _disc.top_verdict_streak()
            if _strk_n >= _disc.VERDICT_STREAK_WARN_N:
                st.markdown(
                    "<div style='font-family:Share Tech Mono,monospace;"
                    "font-size:12.5px;padding:6px 2px 0;color:#ff6b5e;'>"
                    f"⚠ {_strk_name} has topped the book {_strk_n} nights "
                    "straight — the dial is waiting</div>",
                    unsafe_allow_html=True)
        except Exception as exc:
            logger.debug("week-strike warning skipped: %s", exc)

        # ── TONIGHT'S PATTERN (user, Sep 22: "add the top-verdict pattern
        # to the plate") — the costliest class on the closed-trade book,
        # cited through discipline.top_verdict so the plate reads the
        # same summary() her 20:00 line speaks. Silence when the book is
        # clean.
        try:
            _tv = _disc.top_verdict()
            if _tv:
                _tv_name = str(_tv.get("verdict") or "")
                _tv_short = _tv_name.split(" - ")[0]
                _tv_reason = (_tv_name.split(" - ", 1)[1]
                              if " - " in _tv_name else "")
                _tv_usd = float(_tv.get("usd_left") or 0.0)
                _tv_n = int(_tv.get("n") or 0)
                st.markdown(
                    "<div style='font-family:Share Tech Mono,monospace;"
                    "font-size:14px;padding:10px 2px 2px;color:#d8ccb4;'>"
                    "<span class='dim' style='font-size:11px;'>tonight's "
                    "pattern — the costliest class on the book:</span><br>"
                    f"<b style='color:{_vcol(_tv_name)};'>{_tv_short}</b>"
                    f"&nbsp;&nbsp;${_tv_usd:,.0f}"
                    "<span class='dim' style='font-size:11px;'> across "
                    f"{_tv_n} closed trades"
                    + (f" · {_tv_reason}</span></div>" if _tv_reason
                       else "</span></div>"),
                    unsafe_allow_html=True)
        except Exception as exc:
            logger.debug("tonight's pattern skipped: %s", exc)

        _label = ((_day or {}).get("date", "") if assets
                  else f"{len(_days)} days on record")
        with st.expander(
                f"\U0001F4DE TONIGHT'S REVIEW \u2014 every asset the day touched, the "
                f"verdicts and the money left ({_label})",
                key="cooldown_review_expander"):
            if rows:
                st.markdown("".join(rows), unsafe_allow_html=True)
            if _strip:
                st.markdown(_strip, unsafe_allow_html=True)
            # ── THE DAY PICK (user, Sep 22: "make the glyphs
            # clickable") ── the strip's days are taps: a picked day
            # reads its own verdict rows through the same helpers; a day
            # without per-asset rows says so and names its worst leak.
            # Guarded so bare probes and stub st's fail soft.
            try:
                _pills_fn = getattr(st, "pills", None)
                if _pills_fn is not None and _days:
                    _d_opts = [str(h.get("date") or "") for h in _days]
                    _d_opts = [d for d in _d_opts if d]
                    _ss = getattr(st, "session_state", None)
                    _pk = "review_day_pick"
                    if _ss is not None and _pk in _ss \
                            and _ss[_pk] not in _d_opts:
                        try:
                            del _ss[_pk]
                        except Exception as exc:
                            logger.debug("day pick: stale selection prune skipped: %s", exc)
                    _picked = _pills_fn(
                        "tap a day to read its verdicts", _d_opts,
                        selection_mode="single", default=_d_opts[-1],
                        format_func=lambda d: str(d)[5:], key=_pk)
                    _cur = str((_day or {}).get("date") or "")
                    if _picked and _picked != _cur:
                        _sel = next((h for h in _days
                                     if str(h.get("date") or "") == _picked),
                                    None)
                        _sel_a = (_sel or {}).get("assets") or []
                        if _sel_a:
                            st.markdown(
                                "".join(_day_row_html(a) for a in _sel_a),
                                unsafe_allow_html=True)
                            _sel_bd = _day_bd_md(_sel_a)
                            if _sel_bd:
                                st.markdown(_sel_bd, unsafe_allow_html=True)
                        else:
                            _sv = (_sel or {}).get("top_verdict")
                            _stl = float((_sel or {}).get("total_left") or 0)
                            _msg = (f"{_picked} predates the per-asset rows "
                                    "— its strip glyph is the record")
                            if _sv:
                                _msg += (f" (worst leak: "
                                         f"{str(_sv[0]).split(' - ')[0]} "
                                         f"${float(_sv[1] or 0):,.0f})")
                            elif _stl > 1:
                                _msg += f" (${_stl:,.0f} left)"
                            st.markdown(
                                "<div class='dim' style='font-family:Share "
                                "Tech Mono,monospace;font-size:12px;"
                                f"padding:4px 2px;'>{_msg}</div>",
                                unsafe_allow_html=True)
            except Exception as exc:
                logger.debug("day pick skipped: %s", exc)

            # ── THE TOP-VERDICT TRAIL (user, Sep 21): the top verdict and its cost, night to night, newest first; green ∆ shrinking, red growing.
            try:
                _trail = _disc.verdict_history()
                _trail_md = ""
                if _trail:
                    _t_rows = []
                    for _i in range(len(_trail) - 1, -1, -1):
                        _d, _nm, _usd = _trail[_i]
                        _prev = (_trail[_i - 1][2]
                                 if _i > 0 and _trail[_i - 1][1].split(" - ")[0]
                                 == _nm.split(" - ")[0] else None)
                        _delta = ""
                        if _prev is not None:
                            _dd = _usd - _prev
                            _dc = "#4be38a" if _dd <= 0 else "#ff6b5e"
                            _delta = (f" <span style='color:{_dc};'>Δ "
                                      f"{'+' if _dd >= 0 else ''}{_dd:,.0f}</span>")
                        _tc = _vcol(_nm)
                        _t_rows.append(
                            f"<div style='font-family:Share Tech Mono,monospace;"
                            f"font-size:12.5px;color:#d8ccb4;padding:2px 0;'>"
                            f"<span class='dim'>{str(_d)[5:]}</span>&nbsp;&nbsp;"
                            f"<span style='color:{_tc};'>{str(_nm).split(' - ')[0]}</span>"
                            f"&nbsp;&nbsp;${_usd:,.0f}{_delta}</div>")
                    _trail_md = "".join(_t_rows)
                if _trail_md:
                    st.markdown(
                        "<div style='font-family:Share Tech Mono,monospace;"
                        "font-size:12px;padding:8px 2px 0;color:#8a8171;'>"
                        "the top verdict, night to night (newest first):</div>"
                        + _trail_md, unsafe_allow_html=True)
            except Exception as exc:
                logger.debug("top-verdict trail skipped: %s", exc)

            if bd:
                st.markdown(bd, unsafe_allow_html=True)
            if not rows and not bd:
                st.markdown(
                    "<div class=\'dim\' style=\'font-family:Share Tech Mono," 
                    "monospace;font-size:12px;padding:4px 2px;\'>"
                    "tonight\'s review predates the per-asset rows \u2014 the "
                    "day-by-day strip above is the record so far.</div>",
                    unsafe_allow_html=True)
            # THE STUDY'S RECORD (Sep 21): the nightly diagnostic's
            # latest section — the same ranking her line cites — read
            # live from logs/verdict_cost_report.md (newest first).
            try:
                import _verdict_study as _vst
                _rep = _vst.REPORT_PATH
                if _rep.exists():
                    _head = _rep.read_text(encoding="utf-8").split("###")
                    if len(_head) > 1:
                        st.markdown(
                            "<div style='font-family:Share Tech Mono," 
                            "monospace;font-size:12px;padding:8px 2px 0;"
                            "color:#d8ccb4;'><b>the nightly diagnostic:</b> "
                            + str(_head[1]).strip().split("\n")[0]
                            + "</div>", unsafe_allow_html=True)
            except Exception as exc:
                logger.debug("nightly diagnostic read skipped: %s", exc)
            st.caption(f"as of {_now_mst().strftime('%H:%M')} MST \u2014 the "
                       f"cooldown review's own verdicts (the same rows her "
                       f"20:00 line speaks), read live each rerun")
    except Exception as exc:
        logger.debug("cooldown review plate failed: %s", exc)


def _spark_html(vals, scale=None, marks=None):
    """A sparkline from a series of fit edges or P&L (None = a
    one-sided week): block chars sized by the value, each char colored
    by its own sign (green paid, red lost, grey flat-zero, dim for
    None), scaled per state by the max |edge| in the series. scale
    lets the caller share one scale across states. marks (aligned to
    vals, 'action@week' elements) stamp regime-change weeks with
    distinct glyphs: amber ▼ auto-flip (the quantile ships), green ▲
    flip-back (the fit returns) — the glyph replaces the bar so the
    regime change reads at a glance. Returns (html, scale-used)."""
    blocks = " ▁▂▃▄▅▆▇█"
    present = [v for v in (vals or []) if v is not None]
    if not present:
        return "", (scale if scale else 1.0)
    m = scale if scale else max(abs(v) for v in present) or 1.0
    chars = []
    for i, v in enumerate(vals or []):
        mk = (marks[i] if marks and i < len(marks) else None)
        if mk:
            act, _, wk = str(mk).partition("@")
            edge = _edge_attr(v)
            if act in ("flip", "flip2"):
                ch, col = ("▼▼", "#ffb347") if act == "flip2" \
                    else ("▼", "#ffb347")
                ttl = (f"{ch} {'re-flip' if act == 'flip2' else 'auto-flip'}"
                       f" {wk} — the quantile ships"
                       f"{' again' if act == 'flip2' else ''}{edge}")
            else:
                ch, col = ("▲▲", "#4be38a") if act == "flip_back2" \
                    else ("▲", "#4be38a")
                ttl = (f"{ch} {'re-arm' if act == 'flip_back2' else 'flip-back'}"
                       f" {wk} — the fit returns"
                       f"{' again' if act == 'flip_back2' else ''}{edge}")
            chars.append(f"<span style='color:{col};' "
                         f"title='{ttl}'>{ch}</span>")
            continue
        if v is None:
            chars.append("<span style='color:#9aa0a6;'>·</span>")
            continue
        k = min(8, max(0, int(round(abs(v) / m * 8))))
        ch = blocks[k] if k else "▁"
        col = ("#4be38a" if v > 0
               else ("#ff6b5e" if v < 0 else "#9aa0a6"))
        chars.append(f"<span style='color:{col};' title='{v:+.2f}'>{ch}</span>")
    return "".join(chars), m


def _edge_attr(v):
    """The ' · edge ±x.xx' suffix for a mark glyph's hover title
    (omitted when the marked week carries no grade)."""
    return f" · edge {v:+.2f}" if v is not None else ""


def _regime_watch(trend, flips):
    """THE REGIME WATCH (Sep 11): the console's render of the shared
    watch math (tidebar_weekly.regime_watch) — which states are
    trending toward a bench or a flip-back, with the WEEKS-TO-GO
    forecast (zero-crossing weeks at the current slope plus the gate's
    remaining fresh weeks, capped at 8+, "gate due now" when nothing
    remains). The scanner runs the same math after each weekly pass
    and WARNS the log the first time a state slides toward a bench —
    one math, both sides. Returns the state phrases (' · '-joined) or
    '' when nothing is under pressure."""
    try:
        import tidebar_weekly as _tbw
        watch = _tbw.regime_watch(trend, flips)
        return " · ".join(f"{nm} {p}" for nm, p in watch.items())
    except Exception:
        return ""


@st.cache_data(ttl=300)
def _tidebar_verdict(jf_path=None, sims_hidden: bool = False,
                     verdict: str = ""):
    """THE TIDE BAR VERDICT plate for MASTER: the tide bar weekly's
    latest outcome-vs-quantile comparison, per tide state, plus the
    week's realized fills — read from the JSON sidecar the weekly
    driver writes (_tidebarweekly.json, atomic-swapped). jf_path lets
    tests inject a sandbox sidecar so the suite never touches the
    production file. Never raises; a missing or stale sidecar renders    a quiet one-liner. sims_hidden (Sep 12) threads the trail
    toggle's choice into the ledger chart — it is part of the CACHE
    KEY, so a toggle flip recomputes the plate instead of serving the
    stale cached HTML (the chart renders inside this plate).
    """
    try:
        import json as _json
        jf = Path(jf_path) if jf_path \
            else Path(__file__).parent / "_tidebarweekly.json"
        data = _json.loads(jf.read_text(encoding="utf-8"))
        states = data.get("states") or {}
        if not states:
            raise ValueError("no states in the sidecar")
        flips = data.get("flips") or {}

        def _fmt_state(name, rec):
            flipped = bool((flips.get(name) or {}).get("flipped"))
            badge = (" <span style='color:#ffb347;'>[BENCHED → the "
                     "quantile ships until it earns a flip-back]</span>"
                     if flipped else "")
            if not rec:
                return (f"{name} <span class='dim'>— no grade this week</span>"
                        f"{badge}")
            inf = rec.get("in_force") or {}
            oth = rec.get("other") or {}
            d = None
            if inf and oth and oth.get("fires"):
                d = inf.get("usd_per_fire", 0.0) - oth.get("usd_per_fire", 0.0)
            src = str(rec.get("other_src") or "quantile")
            dcol = "#4be38a" if (d is not None and d >= 0) else "#ff6b5e"
            line = (f"{name} <span class='dim'>bar</span> "
                    f"{inf.get('bar', 0):.0f} <span class='dim'>→</span> "
                    f"{inf.get('fires', 0)} fires "
                    f"<span class='dim'>{inf.get('rate', 0) * 100:.0f}% pay</span> "
                    f"<span style='color:{dcol};'>"
                    f"${inf.get('usd_per_fire', 0):+.2f}/fire</span>{badge}")
            if oth:
                line += (f" <span class='dim'>vs {src} {oth.get('bar', 0):.0f}: "
                         f"${oth.get('usd_per_fire', 0):+.2f}</span>")
            if d is not None:
                line += (f" <span style='color:{dcol};'>(Δ {d:+.2f})</span>")
            return line

        rows = "<br>".join(_fmt_state(nm, states.get(nm))
                           for nm in ("OUT", "FLAT", "RISING"))
        fills = data.get("fills")
        if isinstance(fills, str):
            rows += (f"<br><span class='dim'>REAL FILLS: not graded "
                     f"({fills})</span>")
        elif isinstance(fills, dict):
            # PER-STATE REAL FILLS (Sep 11): each tide's fires that
            # actually traded, colored by realized P&L — green paid,
            # red lost, dim flat — with the unfilled-but-paper-paid
            # fires (what the size ladder gated) on the same line.
            # Shape: {states: {OUT: {...}}, coverage_from} (the writer's)
            # or a flat {OUT: {...}} legacy shape.
            fstates = fills.get("states")
            if not isinstance(fstates, dict):
                fstates = (fills if any(nm in fills
                                        for nm in ("OUT", "FLAT", "RISING"))
                           else None)
            if fstates:
                frows = []
                tot_f = 0
                tot_p = 0.0
                for nm in ("OUT", "FLAT", "RISING"):
                    v = fstates.get(nm)
                    if not isinstance(v, dict):
                        continue
                    n_f = int(v.get("fills", 0))
                    pnl = float(v.get("pnl", 0.0))
                    tot_f += n_f
                    tot_p += pnl
                    fcol = ("#4be38a" if pnl > 0
                            else ("#ff6b5e" if pnl < 0 else "#9aa0a6"))
                    extra = ""
                    if n_f == 0 and v.get("unfilled"):
                        extra = (f" <span class='dim'>(+{v.get('unfilled')} unfilled, "
                                 f"{v.get('unfilled_payers', 0)} paid on paper — "
                                 f"${v.get('usd_if_paid', 0.0):.2f} the size "
                                 f"ladder gated)</span>")
                    frows.append(f"{nm} <span class='dim'>{n_f} filled</span> "
                                 f"<span style='color:{fcol};'>realized "
                                 f"${pnl:+.2f}</span>{extra}")
                if frows:
                    rows += ("<br><span class='dim'>REAL FILLS — the fires "
                             "that actually traded:</span><br>"
                             + "<br>".join(frows))
                    cov = fills.get("coverage_from")
                    if cov:
                        rows += (f"<br><span class='dim'>fire-event coverage "
                                 f"begins {str(cov)[:16].replace('T', ' ')} UTC "
                                 f"— earlier fills unattributable</span>")
                    tcol = ("#4be38a" if tot_p > 0
                            else ("#ff6b5e" if tot_p < 0 else "#9aa0a6"))
                    rows += (f"<br><span class='dim'>TOTAL:</span> {tot_f} filled, "
                             f"<span style='color:{tcol};'>"
                             f"realized ${tot_p:+.2f}</span> this week")
        # THE TREND (Sep 11): the rolling pooled fit edge after each
        # history week (oldest first), one sparkline per state. The
        # canonical fit-vs-quantile axis: above the zero line the
        # outcome bar pays better, below it the quantile does — so a
        # losing bar's slide toward a bench (and a benched bar's climb
        # toward a flip-back) reads at a glance.
        trend = data.get("trend")
        ftrend = data.get("fills_trend")
        fmarks = data.get("flip_marks")
        if isinstance(trend, dict):
            tseries = {nm: (trend.get(nm) or [])
                       for nm in ("OUT", "FLAT", "RISING")}
            fseries = ({nm: ((ftrend.get(nm) or []) if isinstance(ftrend, dict)
                             else [])
                        for nm in ("OUT", "FLAT", "RISING")}
                       if ftrend else None)
            all_vals = [v for s in tseries.values() for v in (s or [])
                        if v is not None]
            shared = (max(abs(v) for v in all_vals) or 1.0) if all_vals else None
            for nm in ("OUT", "FLAT", "RISING"):
                s = tseries.get(nm) or []
                if not s:
                    continue
                ms = (fmarks.get(nm) or None) \
                    if isinstance(fmarks, dict) else None
                if ms is not None and len(ms) != len(s):
                    ms = None   # a misaligned series degrades to unmarked
                spark, _ = _spark_html(s, scale=shared, marks=ms)
                if spark:
                    last = next(v for v in reversed(s) if v is not None)
                    rows += (f"<br>{nm} <span class='dim'>trend</span> {spark} "
                             f"<span class='dim'>(pooled edge, latest "
                             f"{last:+.2f})</span>")
                # THE FILLS SPARK (Sep 11): the same weeks' REALIZED
                # fill P&L from the history records, under the edge
                # line — the candle counterfactual and the real dollars
                # trend together, one scale across states for honesty.
                if fseries is not None:
                    fs = fseries.get(nm) or []
                    fsp, _ = _spark_html(fs, scale=None) \
                        if any(v is not None for v in fs) else ("", None)
                    if fsp:
                        flast = next(v for v in reversed(fs)
                                     if v is not None)
                        rows += (f"<br>&nbsp;&nbsp;<span class='dim'>fills</span> "
                                 f"{fsp} <span class='dim'>(realized P&L, "
                                 f"latest ${flast:+.2f})</span>")
        # THE REGIME WATCH (Sep 11): one compact line naming the
        # states under bench/flip-back pressure from the edge slope —
        # silent when nothing is under pressure (no fake urgency)
        watch = _regime_watch(trend if isinstance(trend, dict) else None,
                              flips)
        rows += (f"<br><span class='dim'>REGIME WATCH:</span> "
                 f"<span style='color:{'#ffb347' if watch else '#9aa0a6'};'>"
                 f"{watch or 'no state under bench / flip-back pressure'}</span>")
        # THE VERDICT CENSUS CHIP (Sep 12): the machinery's record —
        # how many of its own calls paid, cost, still pend — as a
        # one-line chip beside the regime watch, fed from the flip
        # ledger. ONE FORMAT (the trail's census line): the plate and
        # the trail can never disagree. The empty-trail shape says so
        # honestly instead of a fake 0 · 0 · 0.
        try:
            import tidebar_weekly as _tbw
            # the SAME stream the trail reads, in the SAME newest-first
            # order — the classification's governed windows run on
            # sibling order, so the plate's counts must see exactly
            # what the trail's census sees
            _evs_all = list(reversed(_tbw._load_flip_events()))
            if not _evs_all:
                _counts_chip = None          # the honest empty shape
            else:
                _recs_chip = _tbw._load_history()
                _weeks_chip = [r.get("week") or "?" for r in _recs_chip]
                _, _, _counts_chip = _tbw.classify_decisions(
                    _recs_chip, _weeks_chip, _evs_all, flips)
        except Exception:
            _counts_chip = None
        if _counts_chip:
            _chip_col = ("#ff6b5e" if _counts_chip.get("COSTING")
                         else "#4be38a")   # a COSTING call reads red
            # THE RECKONING TALLY (Sep 12): how the borrow's own calls
            # have graded, from the SAME stream the chip classifies —
            # '' until a real flip-back's aftermath has graded
            _chip_txt = (_tbw.verdict_census(_counts_chip)
                         + _tbw.reckoning_tally(_evs_all))
        else:
            _chip_col = "#9aa0a6"
            _chip_txt = "the flip ledger is empty — no decisions yet"
        rows += (f"<br><span class='dim'>VERDICTS:</span> "
                 f"<span style='color:{_chip_col};'>{_chip_txt}</span>")
        # THE PLATE'S WARN STRIP (Sep 12): the pass's own bench-pressure
        # warning, persisted in the sidecar (bench_warn) — shown while
        # the state stays pressured, gone when the pass stops warning
        # (the episode lifted). Same math, same phrase, both sides.
        bw = data.get("bench_warn")
        if isinstance(bw, dict) and bw.get("phrase"):
            rows += (f"<br><span style='color:#ffb347;font-size:16px;'>"
                     f"⚠️ BENCH PRESSURE: {bw['phrase']} — at 3 losing "
                     f"weeks the bench takes over. Watch the plate.</span>")
        # THE LEDGER CHART (Sep 11): the weekly fit edges over time —
        # the same history the auto-flip reads, drawn. Sits between the
        # sparklines (per-pass recency) and the footer.
        chart = _tidebar_ledger_chart(sims_hidden=sims_hidden,
                                      verdict=verdict)
        if chart:
            rows += chart
        when = str(data.get("generated_ts") or "?")[:16].replace("T", " ")
        rows += (f"<br><span class='dim'>graded {when} UTC · daily pass, "
                 f"7-day grade window · full history below (📜)</span>")
        return plate("🌊 TIDE BAR VERDICT — outcome bar vs the quantile",
                     lcd([rows]))
    except Exception:
        return plate("🌊 TIDE BAR VERDICT — outcome bar vs the quantile",
                     lcd(["the hindsight has not run yet — first pass "
                          "~10 min after boot, daily after (7-day grade "
                          "window)"],
                         tone="amber"))


def _poolgate_apply_state(staged_path=None):
    """THE APPLY STRIP's STATE (Sep 13): whether MASTER shows the
    confirm-to-apply button for the arc-3 sweep's STAGED POOL_MIN
    proposal — PURE over its inputs: reads the stage file and re-prices
    the live sweep, calls no Streamlit widgets (the button block in
    render_master carries those), and never raises. Returns {} when no
    sane stage exists; otherwise {pool_min, net, n_priced, staged_at}
    plus stale=True + stale_reason when the live sweep no longer
    proposes the staged bar (the apply would refuse — shown as a
    caption, not a button)."""
    import json as _json
    try:
        fp = Path(staged_path) if staged_path \
            else Path(__file__).parent / "tide_bar_poolmin_staged.json"
        raw = _json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as _exc:
        return {}   # no stage / unreadable stage -> no strip, why noted
    if not isinstance(raw, dict):
        return {}   # a corrupt stage file is not a proposal
    try:
        pm = float(raw.get("pool_min"))
        net = float(raw.get("net") or 0.0)
        n = int(raw.get("n_priced") or 0)
    except (TypeError, ValueError):
        return {}   # a stage without sane numbers is not a proposal
    if not 0.0 < pm <= 1.0:
        return {}   # out-of-range bars never render as applyable
    out = {"pool_min": pm, "net": net, "n_priced": n,
           "staged_at": raw.get("ts") or ""}
    try:
        import tidebar_weekly as _tbw
        rp = _tbw.recommend_pool_min()
    except Exception as _exc:
        out["stale"] = True
        out["stale_reason"] = "the live sweep could not re-price"
        return out
    if rp.get("recommendation") is None \
            or abs(float(rp["recommendation"]) - pm) > 1e-9:
        out["stale"] = True
        out["stale_reason"] = ("the live sweep no longer proposes "
                               "this bar")
    return out


def _flipgrade_verdict(txt_path=None, json_path=None):
    """THE FLIP GRADE plate for MASTER: the latest per-state verdicts
    from _flipgrade.txt (newest section first) — the pooled fit-vs-
    quantile dollars, the edge streak's bench/flip-back proximity, and
    every real decision's aftermath in dollars — so the monthly grade
    (now refreshed after each weekly pass) is visible without opening
    the file. THE OPEN BENCH (Sep 12): _flipgrade.json (written on
    every grade_ledger path) carries the live verdict for a
    CURRENTLY BENCHED state — PAID/COST in dollars before the
    flip-back — rendered as a strip under the grade rows, so the
    bench's running P&L shows on MASTER without the audit expander.
    THE POOL-GATE DIALS (Sep 13): the sidecar's poolgate blocks render
    as a compact verdict line — the walked family net per graduated
    threshold (green SAVED / red MISSED, dim identical), the
    baseline-hold honesty when the walk can't reproduce the ledger,
    and the arc-3 default's verdict — so the dial's monthly grade
    reads on MASTER without the txt.
    txt_path/json_path let tests inject sandbox files. Never raises;
    a missing/unparseable grade renders a quiet one-liner."""
    try:
        import re
        fg = Path(txt_path) if txt_path \
            else Path(__file__).parent / "_flipgrade.txt"
        txt = fg.read_text(encoding="utf-8")
        parts = txt.split("=== THE FLIP GRADE")
        if len(parts) < 2 or not parts[1].strip():
            raise ValueError("no grade section")
        # the newest section: from after its header line to the next one
        body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
        body = body.split("\n=== THE FLIP GRADE")[0]
        lines = [l for l in body.splitlines() if l.strip()]
        head = re.search(
            r"graded at:\s*(.+?)\s*\((\d+) weeks?: ([^)]+)\)", body)
        if not head:
            raise ValueError("unparseable grade header")
        graded_ts, n_weeks, span = (head.group(1).strip(), head.group(2),
                                    head.group(3).strip())

        def _verdict_col(tail: str) -> str:
            if "PAID" in tail:
                return "#4be38a"
            if "COST" in tail:
                return "#ff6b5e"
            return "#9aa0a6"   # pending — honest dim, no fake verdict

        rows = (f"<span class='dim'>{n_weeks} weeks graded ({span}) · "
                f"graded {graded_ts[:16].replace('T', ' ')} UTC · monthly, "
                f"plus refreshed shortly after each weekly pass</span>")
        cur = None
        for l in lines:
            m = re.match(
                r"^(OUT|FLAT|RISING)\s+\((\d+) two-sided weeks; (.+?)\)$",
                l.strip())
            if m:
                cur = m.group(1)
                rows += (f"<br><b>{cur}</b> <span class='dim'>("
                         f"{m.group(2)} two-sided weeks; {m.group(3)})</span>")
                continue
            if l.startswith("  edges by week:"):
                toks = [t for t in l.split(":", 1)[1].split() if t]
                rows += (f"<br>&nbsp;&nbsp;<span class='dim'>edges by "
                         f"week</span> {' · '.join(toks)}")
                continue
            pm = re.search(
                r"pooled \((\d+)w\): fit (\d+) fires "
                r"\$([+-][\d.]+)/fire vs quantile (\d+) fires "
                r"\$([+-][\d.]+)/fire — fit edge ([+-][\d.]+)", l)
            if pm:
                delta = float(pm.group(6))
                dcol = "#4be38a" if delta >= 0 else "#ff6b5e"
                rows += (f"<br>&nbsp;&nbsp;pooled ({pm.group(1)}w): "
                         f"<span class='dim'>fit</span> {pm.group(2)} fires "
                         f"${float(pm.group(3)):+.2f}/fire <span class='dim'>"
                         f"vs quantile</span> {pm.group(4)} fires "
                         f"${float(pm.group(5)):+.2f}/fire "
                         f"<span style='color:{dcol};'>Δ {delta:+.2f}</span>")
                continue
            xm = re.match(r"^(bench|flip-back) proximity:\s*(.+)$",
                          l.strip())
            if xm:
                pcol = ("#ffb347" if xm.group(1) == "bench"
                        else "#4be38a")
                rows += (f"<br>&nbsp;&nbsp;<span class='dim'>"
                         f"{xm.group(1)} proximity:</span> "
                         f"<span style='color:{pcol};'>{xm.group(2)}</span>")
                continue
            em = re.match(r"^⚡\s*([^:]+):\s*(.+)$", l.strip())
            if em:
                ecol = _verdict_col(em.group(2))
                rows += (f"<br>&nbsp;&nbsp;<span style='color:{ecol};'>"
                         f"⚡ {em.group(1)}: {em.group(2)}</span>")
                continue
            if "not graded" in l:
                rows += (f"<br><span class='dim'>{l.strip()}</span>")
        # THE POOL-GATE DIALS (Sep 13): the sidecar's verdict blocks —
        # the walked family net per graduated threshold (the MAJORITY
        # of the strip) and the arc-3 default's verdict. Absent keys ->
        # no strip (a held/stale grade cleared them with the bench
        # dollars — the plate never shows a stale dial verdict).
        pg_bits = []
        try:
            import json as _json
            fj = Path(json_path) if json_path \
                else Path(__file__).parent / "_flipgrade.json"
            _fgj = _json.loads(fj.read_text(encoding="utf-8"))
            fam = ((_fgj.get("poolgate_family") or {}).get("net")) or {}
            for pm in sorted(fam, key=float):
                try:
                    pmf = float(pm)
                except (TypeError, ValueError):
                    continue
                v = fam[pm]
                if not isinstance(v, (int, float)):
                    continue
                col = ("#4be38a" if v > 0
                       else "#ff6b5e" if v < 0 else "#9aa0a6")
                pg_bits.append(f"<span style='color:{col};'>"
                               f"{pmf:+.2f} ${v:+,.2f}</span>")
            _hold = (_fgj.get("poolgate_family") or {}).get("baseline_ok")
            _arc3 = _fgj.get("poolmin_default") or {}
            if _hold is False:
                pg_bits.append("<span class='dim'>⚠ walk baseline held "
                               "— the dial grade is withheld until "
                               "reconciled</span>")
            if _arc3.get("recommendation") is not None:
                pg_bits.append(f"<span class='dim'>arc-3 proposes a "
                               f"raise to</span> "
                               f"<b>{float(_arc3['recommendation']):+.2f}</b>"
                               f" <span class='dim'>(apply by hand)</span>")
            elif _arc3.get("n_priced") is not None:
                pg_bits.append(f"<span class='dim'>arc-3 holds 0.0 "
                               f"({_arc3.get('n_priced', 0)} priced "
                               "re-arm(s) — a raise needs every rolling "
                               "window positive)</span>")
            # THE LEARNED BARS (Sep 13): the per-state pool bars the
            # learner derives from each state's own pooled-edge
            # distribution — a state in force shows its bar; a thin
            # state stays dim on the global dial. The block travels only
            # on a COMPLETED grade (a hold clears it like the dial
            # verdicts), so its presence — not the loop — gates here.
            # THE FBW DIAL'S WALK (Sep 13): the streak dial's walked
            # family per graduated candidate and its rolling-window-
            # gated recommendation — advice for her hand, never an
            # auto-move. Renders only when the blocks travel (a hold
            # clears them like the rest).
            _ffam = ((_fgj.get("fbwgate_family") or {}).get("net")) or {}
            _fok = (_fgj.get("fbwgate_family") or {}).get("baseline_ok")
            for fw in sorted(_ffam, key=float):
                try:
                    _fv = float(_ffam[fw])
                except (TypeError, ValueError):
                    continue
                _fcol = ("#4be38a" if _fv > 0
                         else "#ff6b5e" if _fv < 0 else "#9aa0a6")
                pg_bits.append(f"<span class='dim'>fbw</span> "
                               f"<b>{float(fw):g}</b> "
                               f"<span style='color:{_fcol};'>"
                               f"${_fv:+,.2f}</span>")
            if _fok is False:
                pg_bits.append("<span class='dim'>⚠ fbw walk baseline "
                               "held — the dial grade is withheld "
                               "until reconciled</span>")
            _fbwd = _fgj.get("fbw_default") or {}
            if _fbwd.get("recommendation") is not None:
                pg_bits.append(
                    f"<span class='dim'>fbw walk proposes raising "
                    f"the streak to</span> "
                    f"<b>{float(_fbwd['recommendation']):g}</b> "
                    f"<span class='dim'>(a code change by her hand — "
                    "never auto-applied)</span>")
            elif _fbwd.get("n_priced") is not None:
                pg_bits.append(
                    f"<span class='dim'>fbw walk holds 2 "
                    f"({_fbwd.get('n_priced', 0)} priced re-arm(s) — a "
                    "raise needs every rolling window positive)</span>")
            # THE STREAK DIAL (Sep 13): the FBW candidates' monthly
            # evidence rides poolgate_streak (same per-threshold
            # denied/delayed/net shape as the pool family) — rendered
            # beside the pool dials so both dials' verdicts read on
            # MASTER. Renders only when the block travels (a hold
            # clears it like the rest); JSON keys arrive as strings.
            _streak = _fgj.get("poolgate_streak") or {}
            for fw in sorted(_streak, key=lambda k: float(k)):
                _fw = _streak.get(fw) or {}
                try:
                    _net = float(_fw.get("net") or 0.0)
                    _den = int(_fw.get("denied") or 0)
                    _del = int(_fw.get("delayed") or 0)
                except (TypeError, ValueError):
                    continue
                _scol = ("#4be38a" if _net > 0
                         else "#ff6b5e" if _net < 0 else "#9aa0a6")
                pg_bits.append(
                    f"<span class='dim'>streak FBW={float(fw):g}</span> "
                    f"<span style='color:{_scol};'>${_net:+,.2f}</span> "
                    f"<span class='dim'>({_den} denied · {_del} "
                    f"delayed)</span>")
            if "poolmin_learned" in _fgj:
                _pl = _fgj.get("poolmin_learned") or {}
                _lmap = _pl.get("map") or {}
                _learn = _pl.get("learner") or {}
                for _ls in ("OUT", "FLAT", "RISING"):
                    if _ls in _lmap:
                        pg_bits.append(
                            f"{_ls} <b style='color:#4be38a;'>"
                            f"{_lmap[_ls]:+.2f}</b>")
                    else:
                        _lv = _learn.get(_ls) or {}
                        _lf = _lv.get("pool_min")
                        _lr = _lv.get("reason") or ""
                        if _lf is not None:
                            pg_bits.append(
                                f"{_ls} <span class='dim'>derives "
                                f"{_lf:+.2f} (ships next pass)</span>")
                        else:
                            _lwhy = _lr or "on the global dial"
                            pg_bits.append(
                                f"{_ls} <span class='dim'>global "
                                f"({_lwhy})</span>")
            if pg_bits:
                rows += ("<br><span style='color:#ffb347;'>POOL-GATE "
                         "DIALS:</span> " + " · ".join(pg_bits))
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            pass   # a malformed sidecar loses its dial strip, never the grade rows
        # THE OPEN BENCH (Sep 12): the live verdict strip — the same
        # entries the audit trail's CURRENTLY BENCHED block carries,
        # traveled here in the sidecar. Absent/blank sidecar -> no
        # strip (no state is benched, or the grade hasn't run).
        strip = ""
        try:
            import json as _json
            fj = Path(json_path) if json_path \
                else Path(__file__).parent / "_flipgrade.json"
            ob = (_json.loads(fj.read_text(encoding="utf-8"))
                  .get("open_bench")) or []
            bits = []
            for e in ob:
                if not isinstance(e, dict) or not e.get("base"):
                    continue
                v = e.get("verdict")
                if v:
                    bits.append(
                        f"{e['base']} — <b style='color:"
                        f"{e.get('verdict_col') or '#9aa0a6'};'>the bench "
                        f"is {v}</b><br><span class='dim'>"
                        f"{e.get('text') or ''}</span>")
                else:
                    bits.append(
                        f"{e['base']} <span class='dim'>· "
                        f"{e.get('text') or 'pending'}</span>")
            if bits:
                strip = ("<br><span style='color:#ffb347;'>CURRENTLY "
                         "BENCHED (live):</span> " + "<br>".join(bits))
        except Exception:
            strip = ""
        return plate("⚖️ FLIP GRADE — the bench machinery on its own "
                     "ledger", lcd([rows + strip]))
    except Exception:
        return plate("⚖️ FLIP GRADE — the bench machinery on its own "
                     "ledger",
                     lcd(["the grade has not run yet — monthly, plus "
                          "refreshed after each weekly pass once 2 weeks "
                          "of history exist (nothing graded so far)"],
                         tone="amber"))


def _tidebar_week_sections(txt: str):
    """Split the report into (label, text) sections keyed by the
    'graded at:' ISO week tag each section carries (e.g. 2026-W37).
    Sections render NEWEST FIRST (the report prepends), so a week with
    several daily passes shows its sections in that order. Untagged or
    unparseable text falls under 'unmarked (early sections)'."""
    import re
    sections = []
    cur_label = None
    cur = []
    lines = txt.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("=== THE TIDE BAR WEEKLY"):
            if cur:
                sections.append((cur_label, "\n".join(cur).strip()))
            cur = [line]
            # the week tag lives on the metadata line(s) right after the
            # header ('graded at:' / the older 'week ending:') — read
            # FORWARD from THIS header, never the whole remaining text
            tag = "unmarked (early sections)"
            for probe in lines[i + 1:i + 4]:
                m = re.search(r"\d{4}-W\d{2}", probe)
                if m:
                    tag = m.group(0)
                    break
            if tag == "unmarked (early sections)":
                # self-heal: sections written before the week tag existed
                # still carry their own timestamp — derive the ISO week
                # from it so the picker can isolate them like tagged
                # weeks. A pre-2000 date is a data echo, not a report:
                # leave it unmarked.
                for probe in lines[i + 1:i + 4]:
                    dm = re.search(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}", probe)
                    if dm and not dm.group(1).startswith(("19", "200")):
                        try:
                            import time as _t
                            tag = _t.strftime(
                                "%G-W%V",
                                _t.strptime(dm.group(1), "%Y-%m-%d"))
                            break
                        except (ImportError, ValueError):
                            pass  # unparseable probe line — try the next
            cur_label = tag
        elif cur:
            cur.append(line)
    if cur:
        sections.append((cur_label, "\n".join(cur).strip()))
    return sections


def _week_pooled_edges(sections):
    """Per-week canonical fit-vs-quantile edges parsed from the report's
    own per-state lines: {week: {state: fit_edge}}. Each section's
    in-force Δ$/fire is normalized to the fit−quantile axis by the
    section's OWN in-force bar source (the state header names it:
    'in force: outcomes bar N' means the fit is live and Δ is already
    the fit edge; 'in force: quantile bar N' means a flipped regime and
    the sign flips), so every week reads on one axis. A week with
    several sections (daily passes) takes the NEWEST section's grade —
    the same policy the sidecar trend uses. Unparseable bodies simply
    contribute nothing."""
    import re as _re
    out = {}
    for lbl, body in sections:
        if not _re.fullmatch(r"\d{4}-W\d{2}", lbl or ""):
            continue
        edges = {}
        cur = None
        fit_live = True
        for line in body.splitlines():
            m = _re.match(r"(OUT|FLAT|RISING)\s+\(\d+ candidates this week; "
                          r"in force: ([a-z ]+) bar \d+\)", line)
            if m:
                cur = m.group(1)
                fit_live = m.group(2).strip().startswith("outcomes")
                continue
            dm = _re.search(r"in-force Δ\$/fire ([+-]?\d+(?:\.\d+)?)", line)
            if cur and dm:
                d = float(dm.group(1))
                edges[cur] = round(d if fit_live else -d, 2)
        out.setdefault(lbl, edges)   # the file is NEWEST-first: the first
                                     # section of a week is its latest grade
    return out


def _week_side_stats(sections):
    """Per-week per-state stats for BOTH bars, parsed from the prose
    report's own per-state lines: {week: {state: {fit: (fires, rate),
    quantile: (fires, rate)}}} — fit and quantile always carry their
    own side's numbers on the canonical axis, so a tooltip reads the
    same two bars whatever regime shipped. Payer rate comes from the
    line's own '(N payers (R%))' — no division of rounded dollar
    figures, and a zero-fire side stays None (an honest gap, not 0%).
    The newest section of a week wins (the file is newest-first, the
    same policy as _week_pooled_edges). Unparseable bodies contribute
    nothing."""
    import re as _re
    out = {}
    for lbl, body in sections:
        if not _re.fullmatch(r"\d{4}-W\d{2}", lbl or ""):
            continue
        week = {}
        cur = None
        fit_live = True
        for line in body.splitlines():
            m = _re.match(r"(OUT|FLAT|RISING)\s+\(\d+ candidates this week; "
                          r"in force: ([a-z ]+) bar \d+\)", line)
            if m:
                cur = m.group(1)
                fit_live = m.group(2).strip().startswith("outcomes")
                continue
            dm = _re.search(r"bar \d+: (\d+) fires, \d+ payers "
                            r"\((\d+(?:\.\d+)?)%\)", line)
            if cur and dm:
                # the IN FORCE line is the live seat, any other graded
                # line is the counterfactual seat — each takes its own
                # canonical label, never both onto one
                inforce_tag = "fit" if fit_live else "quantile"
                tag = (inforce_tag if line.lstrip().startswith("IN FORCE")
                       else ("quantile" if fit_live else "fit"))
                week.setdefault(cur, {})[tag] = (
                    int(dm.group(1)), float(dm.group(2)) / 100.0)
        # the file is NEWEST-first: a week's FIRST section is its
        # latest grade and later sections are discarded wholesale —
        # the same setdefault policy as _week_pooled_edges (a merge
        # would let an older section's states leak into the week)
        week = {st: s for st, s in week.items()
                if s.get("fit") and s.get("quantile")}
        if week:
            out.setdefault(lbl, week)
    return out


def _bench_spans(weeks):
    """THE REGIME SPANS (Sep 11): benched week-index ranges per state,
    from the append-only flip-event ledger — [(i0, i1), ...] where the
    span covers the weeks the QUANTILE shipped (the flip week through
    the week before its flip-back; an open bench runs to the last
    charted week). A flip week older than the charted window clamps to
    the left edge; events outside the window are skipped. The events
    ledger is the single source — the flips file is only current
    state, and history belongs to the trail."""
    try:
        import tidebar_weekly as _tbw
        idx = {w: i for i, w in enumerate(weeks)}
        first, last = 0, len(weeks) - 1
        out = {}
        for ev in _tbw._real_flip_events():   # sims never shade a regime
            st = str(ev.get("state") or "")
            if st not in ("OUT", "FLAT", "RISING"):
                continue
            wk = str(ev.get("week") or "")
            act = str(ev.get("action") or "")
            if act == "flip":
                i0 = idx.get(wk)
                if i0 is None:
                    i0 = first if wk < (weeks[0] or "") else None
                if i0 is None:
                    continue
                out.setdefault(st, []).append([i0, last])   # open span
            elif act == "flip_back" and out.get(st):
                open_spans = [s for s in out[st] if s[1] == last]
                if not open_spans:
                    continue
                i1 = idx.get(wk)
                if i1 is None:
                    i1 = last + 1 if wk > (weeks[-1] or "") else None
                if i1 is None:
                    continue
                open_spans[-1][1] = max(i1 - 1, open_spans[-1][0] - 1) \
                    if i1 - 1 >= open_spans[-1][0] else i1 - 1
                if open_spans[-1][1] < open_spans[-1][0]:
                    out[st].pop()   # the bench predates the window
        return {st: [(a, b) for a, b in spans if a <= b]
                for st, spans in out.items()}
    except Exception:
        return {}


def _flip_markers(weeks):
    """THE FLIP-EVENT MARKERS (Sep 12): the weeks that DECIDED a bench,
    projected from the flip-event ledger — REAL events and, since the
    hollow-glyph dialect (Sep 12), the diag's SIMULATED worked examples
    too, tagged sim: True so the chart can render them HOLLOW: a worked
    example teaches on the picture without ever reading as a lived
    decision (the sparklines and the benched spans stay real-only —
    sims never claim regime history). Real events win a collision: a
    sim sharing (week slot, state, action) with a lived decision is
    skipped, so a worked example never overdraws the real story. Every
    evidence week of an event lands its own marker aligned to that
    week's slot, and every marker carries the HREF of its decision's
    audit-trail row (the (state, action, decision-week[, sim]) anchor
    id — the trail's own dedupe key), so the glyph clicks through to
    the evidence:
      [{'state', 'act', 'week', 'idx', 'roll_delta', 'sim', 'href',
        'reflip'}, ...]
    A RE-FLIP (Sep 12) is a state's SECOND (or later) flip — regime
    churn — counted per state within each stream (real / sim) in
    ledger order, so a sim flip never makes a real flip a re-flip.
    Evidence weeks outside the charted window are skipped — a marker
    must never point at a week the picture doesn't show. Pure lookup;
    never raises."""
    try:
        import tidebar_weekly as _tbw
        idx = {w: i for i, w in enumerate(weeks)}
        out = []
        # real events first, so the seen-set gives them the slot when a
        # sim collides — the lived decision always wins the draw
        evs = sorted(_tbw._load_flip_events(),
                     key=lambda e: bool(e.get("sim")))
        seen = set()
        # THE RE-FLIP ORDINAL (Sep 12): per state, per stream — the
        # ledger is chronological, so the 2nd+ flip of a state is its
        # re-flip and the 2nd+ flip-back its re-arm (regime churn:
        # flip, re-arm, re-flip).
        flip_no = {False: {}, True: {}}   # stream -> state -> count
        back_no = {False: {}, True: {}}
        for ev in evs:
            st = str(ev.get("state") or "")
            act = str(ev.get("action") or "")
            if st not in ("OUT", "FLAT", "RISING") or act not in (
                    "flip", "flip_back"):
                continue
            sim = bool(ev.get("sim"))
            repeat = False
            if act == "flip":
                flip_no[sim][st] = flip_no[sim].get(st, 0) + 1
                repeat = flip_no[sim][st] > 1
            else:
                back_no[sim][st] = back_no[sim].get(st, 0) + 1
                repeat = back_no[sim][st] > 1
            for d in (ev.get("evidence") or []):
                i = idx.get(str(d.get("week") or ""))
                if i is None:
                    continue
                key = (i, st, act)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"state": st, "act": act,
                            "week": str(d.get("week")), "idx": i,
                            "roll_delta": ev.get("roll_delta", 0),
                            "sim": sim, "reflip": repeat,
                            "dwk": str(ev.get("week") or ""),
                            "href": "#flip-{}-{}-{}{}".format(
                                st, act, str(ev.get("week") or ""),
                                "-sim" if sim else "")})
        return out
    except Exception:
        return []


def _tidebar_ledger_chart(txt_path=None, hide_sims=None,
                          sims_hidden: bool = False,
                          verdict: str = "") -> str:
    """SVG chart of the weekly fit-vs-quantile edges over time —
    the JSONL history ledger is authoritative, and the picker's prose
    parser (_week_pooled_edges over _tidebarweekly.txt) backfills the
    weeks BEFORE the ledger's first record, so the picture extends
    back through the prose history. One line per tide state, the
    canonical fit−quantile axis (above zero the outcome bar pays
    better, below it the quantile does), a dashed zero line, and a
    per-point hover title (week, edge, plus each side's fires and
    payer rate where known: ledger weeks read the record's own
    in-force/other grades, prose weeks read _week_side_stats — the
    label maps to the bar, not the seat, so a flipped regime still
    reads 'fit 3 fires · 30% pay'). Points span one width step per
    ISO week, newest at the right; None weeks (one-sided) gap the
    line, and a flip landing on a gap week stamps the zero line. A
    flip week's point wears the sparkline's regime glyph — ▼ amber /
    ▲ green ON the point — so chart and sparklines tell one story.
    THE MIRRORED HIDE (Sep 12): hollow sim glyphs obey the audit
    trail's show/hide toggle — one choice, both surfaces. THE DIM
    SYNC (Sep 12): when the trail's verdict filter is active, the
    regime stamps whose decision doesn't match DIM to 25% opacity
    (SVG opacity, not hidden — the picture keeps its full regime
    history while the eye follows her filter; hrefs and hovers stay
    intact). Explicit hide_sims/verdict overrides (tests, probes);
    None/empty reads the toggle's session_state in a runtime and
    fails open to SHOW outside one, so bare calls render exactly as
    before. A prose-only week's
    hover tags its provenance; the caption notes the backfill. Colors
    match the plate palette. Never raises: an empty or missing ledger
    renders '' and the caller falls back to the sparklines."""
    try:
        import tidebar_weekly as _tbw
        recs = _tbw._load_history()
        if not recs:
            return ""
        weeks = [r.get("week") or "" for r in recs]
        series = {}
        for st in ("OUT", "FLAT", "RISING"):
            series[st] = [_tbw._week_fit_edge(r, st) for r in recs]
        # THE PROSE BACKFILL (Sep 11): weeks the prose report knows but
        # the JSONL ledger predates. The ledger stays authoritative —
        # a week present in both is never touched. Sections are
        # newest-first, so the parsed week order must be reversed to
        # join the oldest-first series. Prose edge dicts carry the
        # states that graded two-sided; the rest stay honest gaps.
        prose_note = False
        n_prose = 0
        prose_sides = {}
        try:
            rf = Path(txt_path) if txt_path \
                else Path(__file__).parent / "_tidebarweekly.txt"
            if rf.exists():
                secs = _tidebar_week_sections(
                    rf.read_text(encoding="utf-8", errors="replace"))
                prose = _week_pooled_edges(secs)
                prose_sides = _week_side_stats(secs)
                prose_weeks = [w for w in reversed(list(prose))
                               if w not in set(weeks)]
                if prose_weeks:
                    prose_note = True
                    n_prose = len(prose_weeks)
                    weeks = prose_weeks + weeks
                    for st in ("OUT", "FLAT", "RISING"):
                        series[st] = ([prose.get(w, {}).get(st)
                                       for w in prose_weeks]
                                      + series[st])
        except Exception:
            prose_sides = {}   # a broken prose file backfills nothing
        vals = [v for s in series.values() for v in s if v is not None]
        if not vals:
            return ""
        # THE MIRRORED HIDE (Sep 12): the trail's toggle owns sims on
        # both surfaces. None -> read her session_state choice (runtime
        # only); an explicit True/False is the deterministic override.
        _mirror_note = ""
        _hide_sim_marks = False
        if hide_sims is None:
            # the cached plate threads her live toggle choice in (it
            # is part of the cache key — see _tidebar_verdict); a
            # bare/probe call defaults to SHOWING the sims
            _hide_sim_marks = bool(sims_hidden)
        else:
            _hide_sim_marks = bool(hide_sims)
        if _hide_sim_marks:
            _mirror_note = (" · <span style='color:#9aa0a6;'>simulated "
                            "glyphs hidden by the trail's toggle</span>")
        # THE DIM SYNC (Sep 12): the trail's active verdict filter
        # dims the stamps it filters OUT — ONE classification (the
        # shared classify_decisions, the trail's own math) keyed by
        # each event's (state, action, decision-week), so chart and
        # trail can never disagree about what matches
        _verdict = (verdict if verdict in ("PAYING", "COSTING", "pending")
                    else "")
        _dim_note = ""
        _dim_v = {}
        if _verdict:
            try:
                _recs_c = _tbw._load_history()
                _weeks_c = [r.get("week") or "" for r in _recs_c]
                _flips_c = _tbw._load_flips() or {}
                _evs_all = _tbw._load_flip_events()
                _, _vof, _ = _tbw.classify_decisions(
                    _recs_c, _weeks_c, _evs_all, _flips_c)
                _cnt = 0
                for _i, _e in enumerate(_evs_all):
                    _vid = (_e.get("state"), _e.get("action"),
                            _e.get("week"))
                    _vv = _vof.get(_i)
                    _dim_v[_vid] = _vv
            except Exception:
                _verdict = ""   # classification failed: dim nothing
        _all_marks = _flip_markers(weeks)
        _marks = ([m for m in _all_marks if not m.get("sim")]
                  if _hide_sim_marks else _all_marks)
        # THE DIM SYNC, SPANS (Sep 13): a state's benched spans dim
        # when EVERY one of its decisions on the picture fails the
        # verdict filter — the whole chart reads through one filter.
        # At least one real stamp exists for any span-carrying state
        # (sims never shade), so this rides the rendered marks.
        _dim_states = set()
        if _verdict and _dim_v:
            _stamp_ids = {(m["state"], m["act"], m.get("dwk"))
                          for m in _marks}
            for _dst in {k[0] for k in _stamp_ids}:
                _vs = [_dim_v.get(k) for k in _stamp_ids
                       if k[0] == _dst]
                if _vs and all(v != _verdict for v in _vs):
                    _dim_states.add(_dst)
        # the note names only decisions that actually RENDER stamps
        # (a decision with no evidence weeks and no graded week has
        # nothing on the picture to dim — the trail filters its row,
        # the chart simply has no glyph for it)
        if _verdict and _dim_v:
            _dim_ids = {(m["state"], m["act"], m.get("dwk"))
                        for m in _marks}
            _cnt = sum(1 for k in _dim_ids
                       if _dim_v.get(k) != _verdict)
            if _cnt:
                _span_note = (f" · {len(_dim_states)} benched span(s) "
                              "dimmed with their state") \
                    if _dim_states else ""
                _dim_note = (" · <span style='color:#9aa0a6;'>"
                             f"{_cnt} regime stamp(s) dimmed — "
                             "they don't match the verdict "
                             f"filter{_span_note}</span>")
        vmax = max(abs(v) for v in vals) or 1.0   # an all-flat ledger has no range — 0/0 once blanked the whole chart
        W, H = 640, 150
        pad_x, pad_y = 34, 16
        n = max(len(weeks), 2)
        iw = (W - 2 * pad_x) / (n - 1)          # one step per ISO week
        ih = (H - 2 * pad_y) / 2                # half-height per unit edge

        def pt(i, v):
            x = pad_x + i * iw
            y = H / 2 - v / vmax * ih
            return x, y

        def path_for(s):
            d, open_ = [], False
            for i, v in enumerate(s):
                if v is None:
                    open_ = False
                    continue
                x, y = pt(i, v)
                d.append(("M" if not open_ else "L") + f"{x:.1f},{y:.1f}")
                open_ = True
            return "".join(d)

        parts = [f"<svg width='{W}' height='{H}' viewBox='0 0 {W} {H}' "
                 f"style='max-width:100%;height:auto;'>"]
        y0 = H / 2
        parts.append(f"<line x1='{pad_x}' y1='{y0}' x2='{W - pad_x}' "
                     f"y2='{y0}' stroke='#555' stroke-dasharray='4 4'/>")
        parts.append(f"<text x='{W - pad_x + 2}' y='{y0 + 4}' fill='#9aa0a6' "
                     f"font-size='10'>0</text>")
        # the ± vmax ruler labels: the chart's full vertical span
        parts.append(f"<text x='{W - pad_x + 2}' y='{pad_y + 8}' "
                     f"fill='#9aa0a6' font-size='10'>{vmax:+.1f}</text>")
        parts.append(f"<text x='{W - pad_x + 2}' y='{H - pad_y + 2}' "
                     f"fill='#9aa0a6' font-size='10'>{-vmax:+.1f}</text>")
        cols = {"OUT": "#4be38a", "FLAT": "#4da3ff", "RISING": "#ffb347"}
        # THE BENCHED SPANS (Sep 11): translucent bands under the
        # lines — the weeks the quantile shipped while the fit sat
        # benched, straight from the flip-event ledger
        spans = _bench_spans(weeks)
        for st, st_spans in spans.items():
            _dimmed = st in _dim_states
            _span_note = (" · dimmed — the state's decisions sit "
                          "outside the verdict filter") if _dimmed else ""
            for i0, i1 in st_spans:
                x0 = pad_x + i0 * iw - iw / 2
                x1 = pad_x + i1 * iw + iw / 2
                x0c, x1c = max(x0, pad_x), min(x1, W - pad_x)
                if x1c <= x0c:
                    continue
                parts.append(
                    f"<rect x='{x0c:.1f}' y='{pad_y}' "
                    f"width='{x1c - x0c:.1f}' height='{H - 2 * pad_y}' "
                    f"fill='{cols[st]}' "
                    f"fill-opacity='{'0.02' if _dimmed else '0.08'}'>"
                    f"<title>{st} benched {weeks[i0]}..{weeks[i1]} "
                    f"— the quantile shipped{_span_note}</title></rect>")
        # THE FLIP-EVENT MARKERS (Sep 12): above the lines — ▼ pins the
        # evidence weeks that DECIDED a flip, ▲ the weeks that earned a
        # flip-back, each in its state's color and aligned to its week.
        # THE HOLLOW DIALECT (Sep 12): the diag's simulated worked
        # examples render as OUTLINES (fill none, 1.5px stroke) with a
        # simulated tag in the hover — they teach on the picture but
        # never read as lived decisions.
        for m in _marks:
            x = pad_x + m["idx"] * iw
            st = m["state"]
            sim = m.get("sim")
            suffix = " · simulated worked example" if sim else ""
            paint = (f"fill='none' stroke='{cols[st]}' stroke-width='1.5'"
                     if sim else f"fill='{cols[st]}'")
            # THE DIM SYNC: a stamp whose decision doesn't match the
            # active verdict filter dims to 25% — still visible, still
            # clickable, out of the eye's way
            if _verdict and _dim_v.get(
                    (m["state"], m["act"], m.get("dwk"))) != _verdict:
                paint += " opacity='0.25'"
            if m["act"] == "flip":
                y, hover = (
                    pad_y + 6,
                    f"{'▼▼ re-flip' if m.get('reflip') else '▼ auto-flip'} "
                    f"{m['week']} — {st} benched "
                    f"{'again ' if m.get('reflip') else ''}on "
                    f"pooled edge {m['roll_delta']:+.2f}{suffix}")
            else:
                y, hover = (
                    H - pad_y + 4,
                    f"{'▲▲ re-arm' if m.get('reflip') else '▲ flip-back'} "
                    f"{m['week']} — the fit returned"
                    f"{' again' if m.get('reflip') else ''}"
                    f" (pooled edge {m['roll_delta']:+.2f}){suffix}")
            gch = ("▼▼" if m["act"] == "flip" and m.get("reflip")
                   else ("▼" if m["act"] == "flip"
                         else ("▲▲" if m.get("reflip") else "▲")))
            parts.append(
                f"<a href='{m['href']}' style='text-decoration:none;'>"
                f"<text x='{x:.1f}' y='{y}' {paint} "
                f"font-size='11' text-anchor='middle'>{gch}"
                f"<title>{hover} — click for the evidence</title></text></a>")
        # THE ONE-STORY RULE (Sep 12): every flip event also pins its
        # week SLOT for the point stamps, taken from the events' own
        # evidence — so a flip landing on a gap week (the decision week
        # never graded) still stamps, on the zero line, in the
        # sparkline's action color. One glyph language everywhere.
        edge_map = {}
        for m in _marks:
            edge_map.setdefault((m["idx"], m["state"]),
                                (m["act"], m["week"], m["roll_delta"],
                                 m.get("sim"), m["href"],
                                 m.get("reflip"), m.get("dwk")))
        # week ticks: first, middle, last (clamped — n is padded to 2
        # for geometry, but weeks may hold fewer records)
        for idx in sorted({min(i, len(weeks) - 1)
                           for i in (0, n // 2, n - 1)}):
            if weeks[idx]:
                _, yv = pt(idx, 0)
                parts.append(f"<text x='{pad_x + idx * iw - 20}' "
                             f"y='{yv + 14}' fill='#9aa0a6' font-size='9'>"
                             f"{weeks[idx][2:]}</text>")
        for st in ("OUT", "FLAT", "RISING"):
            d = path_for(series[st])
            if d:
                parts.append(f"<path d='{d}' fill='none' stroke='{cols[st]}' "
                             f"stroke-width='2'/>" )
            # a state with no graded weeks still reaches the point loop:
            # its gap weeks can carry flip stamps (on the zero line)
            for i, v in enumerate(series[st]):
                mk = edge_map.get((i, st))
                if v is None and mk is None:
                    continue
                x, y = (pt(i, 0.0) if v is None else pt(i, v))
                # THE SIDES LAYER (Sep 11): the tooltip names each bar's
                # own fires and payer rate, mapped by LABEL — fit gets
                # the outcome-fitted bar, quantile the quantile bar —
                # so a flipped-regime week still reads the same two
                # bars in the same order. Ledger weeks read the
                # record's grades; prose weeks read the parser.
                rec_st = (recs[i - n_prose].get("states") or {}).get(st) \
                    if i >= n_prose else None
                sides = {}
                if rec_st:
                    src = (rec_st.get("other_src") or "quantile").lower()
                    sides_map = (("other", "fit"), ("in_force", "quantile")) \
                        if src.startswith("outcome") else \
                        (("in_force", "fit"), ("other", "quantile"))
                    for key, tag in sides_map:
                        g = rec_st.get(key) or {}
                        if g.get("fires"):
                            sides[tag] = (g["fires"], g.get("rate", 0.0))
                else:
                    sides = prose_sides.get(weeks[i], {}).get(st) or {}
                extra = ""
                if sides.get("fit") and sides.get("quantile"):
                    f_f, f_r = sides["fit"]
                    q_f, q_r = sides["quantile"]
                    extra = (f" — fit {f_f} fires · {f_r * 100:.0f}% pay"
                             f" | quantile {q_f} fires · {q_r * 100:.0f}% pay")
                prov = " (from the prose report)" if i < n_prose else ""
                # THE REGIME STAMP (Sep 12): a flip week's point wears
                # the sparkline's own glyph — ▼ amber / ▲ green, ON the
                # line (the zero line when the week never graded) — so
                # chart and sparklines tell one story. The hover keeps
                # the decision story AND the week's own data.
                if mk:
                    act, mk_wk, mk_rd, mk_sim, mk_href, mk_rf, mk_dwk = mk
                    sim = bool(mk_sim)
                    suffix = " · simulated worked example" if sim else ""
                    if act == "flip":
                        gch, gcol = ("▼▼", "#ffb347") if mk_rf \
                            else ("▼", "#ffb347")
                        gttl = (f"{gch} "
                                f"{'re-flip' if mk_rf else 'auto-flip'} "
                                f"{mk_wk} — {st} benched "
                                f"{'again ' if mk_rf else ''}on "
                                f"pooled edge {mk_rd:+.2f}{suffix}")
                    else:
                        gch, gcol = ("▲▲", "#4be38a") if mk_rf \
                            else ("▲", "#4be38a")
                        gttl = (f"{gch} "
                                f"{'re-arm' if mk_rf else 'flip-back'} "
                                f"{mk_wk} — the fit returned"
                                f"{' again' if mk_rf else ''}"
                                f" (pooled edge {mk_rd:+.2f}){suffix}")
                    gttl += _edge_attr(v) + extra + prov
                    # hollow dialect: a sim's stamp is an outline in the
                    # state's color, never a solid action-colored glyph
                    paint = (f"fill='none' stroke='{cols[st]}' "
                             f"stroke-width='1.5'" if sim
                             else f"fill='{gcol}'")
                    # THE DIM SYNC (on-point layer): same rule, same
                    # identity — the decision's own (state, action,
                    # decision-week)
                    if _verdict and _dim_v.get(
                            (st, act, mk_dwk)) != _verdict:
                        paint += " opacity='0.25'"
                    parts.append(
                        f"<a href='{mk_href}' "
                        f"style='text-decoration:none;'>"
                        f"<text x='{x:.1f}' y='{y:.1f}' {paint} "
                        f"font-size='9' font-weight='bold' "
                        f"text-anchor='middle'>{gch}"
                        f"<title>{gttl} — click for the evidence</title>"
                        f"</text></a>")
                    continue
                parts.append(
                    f"<circle cx='{x:.1f}' cy='{y:.1f}' r='2.5' "
                    f"fill='{cols[st]}'><title>{weeks[i]} {st} fit edge "
                    f"{v:+.2f}{extra}{prov}</title></circle>")
        parts.append("</svg>")
        legend = "  ".join(
            f"<span style='color:{cols[st]};'>— {st}</span>"
            for st in ("OUT", "FLAT", "RISING")
            if any(v is not None for v in series[st]))
        shaded = " · <span style='color:#ffb347;'>shaded = benched "\
                 "(the quantile shipped)</span>" if spans else ""
        marked = (" · ▼/▼▼/▲ = the weeks that decided a bench (▼▼ = "
                  "re-flip) · on-line "
                  "▼/▲ mirror the sparkline glyphs · every glyph clicks "
                  "through to its decision's evidence"
                  if _all_marks else "")
        if any(m.get("sim") for m in _marks):
            marked += " · hollow ▼/▲ = simulated worked examples"
        back = (f" · {n_prose} earlier week{'s' if n_prose != 1 else ''} "
                f"backfilled from the prose report" if prose_note else "")
        cap = ("<span class='dim'>the fit−quantile edge by week — above "
               f"zero the outcome bar pays better · {legend}{shaded}"
               f"{marked}{back}{_mirror_note}{_dim_note}</span>")
        return f"<br>{''.join(parts)}<br>{cap}"
    except Exception:
        return ""


def _show_sims_btn(_key: str = "") -> None:
    """THE ONE-CLICK SHOW (Sep 12): the 'show worked examples' button
    that sits beside the trail's hidden-count notes. One click restores
    the sims from wherever the note lives (main trail or the sims-only
    one-liner), no hunt for the checkbox. The session write lives in an
    on_click CALLBACK — callbacks run before the script body, the only
    place a widget-keyed session entry may be written (the body write
    raises after the checkbox has instantiated; the callback predates
    it). Module level so the note builders can call it regardless of
    local `st` shadowing; a bare context (no runtime) renders nothing —
    the kwargs drive the tests."""
    def _show() -> None:
        st.session_state["show_flip_sims_cb"] = True
        # the one-click show mirrors too: a click followed by closing
        # the tab must not lose the show (the widget's own on_change
        # may not fire until a later run)
        _prefs_save({**_prefs_load(), "show_flip_sims": True})
    try:
        if _in_runtime():
            st.button("show worked examples",
                      key="show_flip_sims_btn" + _key,
                      on_click=_show,
                      help="one click brings the simulated "
                           "worked examples back — the same "
                           "as re-checking 'show simulated "
                           "worked examples'")
    except Exception as exc:
        logger.debug("show-sims button failed: %s", exc)


def _verdict_filter_mirror() -> str:
    """The audit trail's active verdict-filter choice, read SAFELY from
    any scope: module level, where `st` is streamlit (the same reason
    _sims_hidden_mirror lives here). Reads the trail's semantic
    session entry when present; a fresh session falls back to the
    prefs mirror (the same file the dropdown restores from), so the
    chart's dimming stays in step with her choice even across a tab
    close. 'All verdicts' (or anything unknown) dims nothing."""
    try:
        if "verdict_filter" in st.session_state:
            v = st.session_state["verdict_filter"]
            return v if v in ("PAYING", "COSTING", "pending") \
                else "All verdicts"
    except Exception as exc:
        logger.debug("verdict mirror session read failed: %s", exc)
    try:
        v = _prefs_load().get("verdict_filter")
        return v if v in ("PAYING", "COSTING", "pending") \
            else "All verdicts"
    except Exception as exc:
        logger.debug("verdict mirror prefs read failed: %s", exc)
        return "All verdicts"


def _sims_hidden_mirror() -> bool:
    """The audit trail's show-sims choice, read SAFELY from any scope:
    this module shadows `st` with state-loop variables in several
    render bodies (the chart among them), so the session_state access
    must live HERE — at module level, where `st` is streamlit. Reads
    the toggle widget's own session entry (framework-committed before
    the script run, so it is current for the whole page — the chart
    renders BEFORE the trail's checkbox on MASTER). True means she hid
    the sims (the chart drops its hollow glyphs)."""
    try:
        # membership + getitem only: the SessionStateProxy's proven
        # surface (no .get — see the toggle's init)
        for k in ("show_flip_sims_cb", "show_flip_sims"):
            if k in st.session_state:
                return st.session_state[k] is False
    except Exception as exc:
        logger.debug("sims mirror read failed: %s", exc)
    # no session entry yet (a fresh session mid-boot): the MIRROR's
    # last saved choice answers for the chart — the same file the
    # toggle's init restores from, so her hide survives the tab
    # closing and the chart stays in step
    return _prefs_load().get("show_flip_sims") is False


def _in_runtime() -> bool:
    """True when a Streamlit runtime exists (the live server or an
    AppTest run) — the ONLY case where st.checkbox/st.selectbox are
    real widgets. Bare/probe calls (tests, shell probes) get default
    return values from the widget fakes, never an exception, so the
    old try/except fail-open was an accident: gating on the runtime
    makes the bare path deterministic and the explicit hide_sims /
    verdict kwargs the honest overrides."""
    try:
        from streamlit.runtime.exists import exists as _srt_exists
        return bool(_srt_exists())
    except Exception:
        try:
            from streamlit import runtime as _rt
            return bool(_rt.exists())
        except Exception:
            return False


def _render_verdict_control(counts: dict | None = None):
    """THE MASTER-LEVEL VERDICT FILTER (Sep 13): the control moved out
    of the trail expander and lives HERE, near the ledger chart, so
    the sync is visible without opening the trail. ONE widget (key
    verdict_filter_cb), ONE seed, ONE save — the trail resolves the
    same session_state pick without rendering a second widget (the
    duplicate-key class the gates forbid). The semantic option is what
    session_state and the prefs file store; the counts render
    format_func-side and never become the value (a counted label
    stored would raise the moment the census shifted). No-op outside a
    Streamlit runtime (bare probes fail open to ALL)."""
    try:
        if not _in_runtime():
            return
        _opts = ["All verdicts", "PAYING", "COSTING", "pending"]
        if "verdict_filter" not in st.session_state:
            restored = _prefs_load().get("verdict_filter")
            st.session_state["verdict_filter"] = (
                restored if restored in _opts else "All verdicts")

        def _vf_save():
            val = (st.session_state.get("verdict_filter_cb")
                   or "All verdicts")
            st.session_state["verdict_filter"] = val
            _prefs_save({**_prefs_load(),
                         "verdict_filter": val})   # TAB-SURVIVES

        counts = counts or {}
        _lbl = {o: (o if o == "All verdicts" or not counts
                    else f"{o} ({counts.get(o, 0)})") for o in _opts}
        _vf_saved = st.session_state["verdict_filter"]
        st.selectbox(
            "verdict", _opts, key="verdict_filter_cb",
            index=(_opts.index(_vf_saved) if _vf_saved in _opts else 0),
            format_func=lambda o: _lbl[o],
            on_change=_vf_save,
            help="the MASTER-level filter: one pick and the whole page "
                 "syncs — chart stamps, benched spans, and the trail's "
                 "rows all read through this verdict")
    except Exception as exc:
        logger.debug("verdict control render failed: %s", exc)


def _render_flip_audit(hide_sims: bool = False, verdict: str = "") -> str:
    """THE FLIP AUDIT TRAIL (Sep 11): every auto-flip and flip-back
    event, newest first, with the evidence weeks that triggered it —
    the same canonical fit edges the bench decision read. The current
    regime per state comes from the flips file, so a state sitting
    benched right now is visible — with a LIVE running aftermath (the
    open bench graded in dollars, shared math) before its flip-back.
    hide_sims=True renders the LIVED history in isolation (the show/hide
    toggle's hide side): sim rows drop out and an honest hidden-count
    note stays so nothing disappears silently. The    toggle itself is SESSION_STATE-BACKED (Sep 12, key 'show_flip_sims'):
    her choice survives Streamlit reruns and widget-id drift instead of
    snapping back to 'show', and is MIRRORED to console_prefs.json
    (Sep 12) so it survives closing the browser tab (the next session's
    boot restores it; SCOUT_PREFS_FILE overrides for tests). verdict
    (Sep 12) browses the trail by decision
    outcome — 'PAYING', 'COSTING', or 'pending' isolates those rows
    (state meta and the benched strip always stay, hidden rows keep
    anchor stubs so chart glyphs still resolve); anything else shows
    everything. The dropdown is SESSION_STATE-BACKED (Sep 12, key
    'verdict_filter', the semantic option — not the counted label —
    so the choice also survives the counts changing under it). The hidden-count notes carry a ONE-CLICK 'show worked
    examples' button (Sep 12) so restoring the sims needs no hunt for
    the checkbox. Never raises; an empty trail renders a quiet
    one-liner.
    """
    try:
        import tidebar_weekly as _tbw
        evs = list(reversed(_tbw._load_flip_events()))   # newest first
        flips = _tbw._load_flips() or {}
        if not evs and not any(
                (flips.get(st) or {}).get("flipped")
                for st in ("OUT", "FLAT", "RISING")):
            return ("<span class='dim'>the flip audit trail is empty — "
                    "no state has ever been benched (it shows here the "
                    "day one is)</span>")
        try:
            recs = _tbw._load_history()
            weeks_list = [r.get("week") or "?" for r in recs]
        except Exception:
            recs, weeks_list = [], []   # the aftermath degrades to pending
        # THE SHOW/HIDE TOGGLE (Sep 12): lived flips readable in
        # isolation. The checkbox appears only when sims exist, inside
        # the trail's expander; a bare/probe context (no Streamlit
        # runtime) fails open to SHOW, and hide_sims forces the hide
        # side so tests pin the isolation without a runtime.
        _n_sims = sum(1 for e in evs if e.get("sim"))
        show_sims = True
        if _n_sims:
            if hide_sims:
                show_sims = False   # the forced hide side (tests, probes)
            elif _in_runtime():
                # the SESSION_STATE-BACKED toggle (a stable key means her
                # show/hide choice survives reruns and widget-id drift
                # from layout changes). The widget's own session entry
                # ('show_flip_sims_cb') is committed by the framework
                # BEFORE the script body runs — so the MASTER chart,
                # which renders EARLIER on the page, reads the CURRENT
                # choice via _sims_hidden_mirror(), never one run stale.
                # (Sep 12: NO value= kwarg — the key is init-on-missing
                # only; after value= + a session write, Streamlit's
                # widget-policies warn "created with a default value but
                # also had its value set via the Session State API".)
                if "show_flip_sims_cb" not in st.session_state:
                    restored = _prefs_load().get("show_flip_sims")
                    _val = (bool(restored)
                            if isinstance(restored, bool) else True)
                    st.session_state["show_flip_sims_cb"] = _val
                    # the semantic entry exists from boot, so the
                    # chart's mirror and the trail read the restored
                    # choice without waiting for an interaction
                    st.session_state["show_flip_sims"] = _val

                def _sims_save():
                    val = bool(st.session_state["show_flip_sims_cb"])
                    st.session_state["show_flip_sims"] = val
                    _prefs_save({**_prefs_load(),
                                 "show_flip_sims": val})   # TAB-SURVIVES

                show_sims = bool(st.checkbox(
                    "show simulated worked examples",
                    key="show_flip_sims_cb",
                    on_change=_sims_save,
                    help="the diag's seeded replays, kept so this view "
                         "carries worked examples before the machinery "
                         "ever benches a state for real — your choice "
                         "is remembered across sessions (it survives "
                         "closing the tab)"))
            # else: bare/probe mode (no Streamlit runtime) — fail open to
            # SHOW; hide_sims/verdict kwargs are the deterministic paths
        if not show_sims:
            evs = [e for e in evs if not e.get("sim")]
            if not evs:
                _btn = _show_sims_btn("_only")   # ONE CLICK: beside the note
                return (f"<span class='dim'>{_n_sims} simulated worked "
                        f"example{'s' if _n_sims != 1 else ''} hidden — no "
                        f"lived bench events yet; show sims to read the "
                        f"worked examples</span>{_btn}")
        rows = []
        # THE RE-FLIP ORDINAL (Sep 12): counted CHRONOLOGICALLY (evs is
        # newest-first here, so the count runs reversed) per state per
        # stream — a state's 2nd+ flip is its re-flip and its 2nd+
        # flip-back its re-arm; regime churn (flip, re-arm, re-flip)
        # reads as one pattern.
        flip_no, reflip_of, back_no, rearm_of = {}, {}, {}, {}
        for e_ch in reversed(evs):
            _act = str(e_ch.get("action") or "")
            if _act not in ("flip", "flip_back"):
                continue
            _st = str(e_ch.get("state") or "?")
            _key = str(e_ch.get("sim") or False)
            if _act == "flip":
                flip_no[(_st, _key)] = flip_no.get((_st, _key), 0) + 1
                reflip_of[(_st, str(e_ch.get("week") or ""), _key)] = \
                    flip_no[(_st, _key)] > 1
            else:
                back_no[(_st, _key)] = back_no.get((_st, _key), 0) + 1
                rearm_of[(_st, str(e_ch.get("week") or ""), _key)] = \
                    back_no[(_st, _key)] > 1
        # by-state sibling indices for the governed-window math; sims
        # (marked sim: true, the diag's worked example) keep their OWN
        # embedded aftermath — their weeks never lived in the ledger
        # THE ONE CLASSIFICATION (Sep 12): every decision graded ONCE
        # by the shared math in tidebar_weekly.classify_decisions — the
        # per-row chip, the verdict filter, and the census chip all read
        # this one pass, so they can never disagree with each other or
        # with the aftermath dollars on the row.
        am_of, verdict_of, _counts = _tbw.classify_decisions(
            recs, weeks_list, evs, flips)
        # THE FBW=3 COUNTERFACTUAL (Sep 12): a real flip-back whose
        # aftermath grades COSTING earns its own question on the row —
        # would ONE more re-arm week have saved it? Computed here, once,
        # from the same graded aftermath the row shows (one math), so
        # the clause can never disagree with the dollars above it. Any
        # surprise fails open to no clause — an annotation must never
        # kill the row.
        fbw_of = {}
        for idx, e in enumerate(evs):
            if (e.get("sim")
                    or str(e.get("action") or "") != "flip_back"
                    or verdict_of.get(idx) != "COSTING"):
                continue
            try:
                fbw_of[idx] = _tbw.fbw3_counterfactual(
                    recs, e, str(e.get("state") or "?"), am_of.get(idx))
            except Exception:
                fbw_of[idx] = None
        # THE POOL-THRESHOLD COUNTERFACTUAL (Sep 13): the same rows, the
        # pool dial's own question — would raising the pool bar
        # (+0.25, the pool-grade's graduated family) have denied a
        # COSTING re-arm whose pool was thin? One math with the monthly
        # grade's counterfactual machinery; any surprise fails open to
        # no clause — an annotation must never kill the row.
        poolcf_of = {}
        for idx, e in enumerate(evs):
            if (e.get("sim")
                    or str(e.get("action") or "") != "flip_back"
                    or verdict_of.get(idx) != "COSTING"):
                continue
            try:
                poolcf_of[idx] = _tbw.poolcf_counterfactual(
                    recs, e, str(e.get("state") or "?"), am_of.get(idx))
            except Exception:
                poolcf_of[idx] = None
        # THE BORROW'S RECKONING (Sep 12): every real flip-back's verdict
        # was BORROWED while pending (the closure pass always is) — the
        # grader compares the borrowed tag with the realized aftermath
        # and stamps disagreements, so the live-borrow rule is audited
        # by the dollars it predicted over. Rendered fresh per pass so
        # the row can never go stale; the pass hook stamps the trail.
        rk_of = {}
        for idx, e in enumerate(evs):
            if e.get("sim") or str(e.get("action") or "") != "flip_back":
                continue
            try:
                rk_of[idx] = _tbw.borrow_reckoning(
                    e, str(e.get("state") or "?"), am_of.get(idx))
            except Exception:
                rk_of[idx] = None
        # THE VERDICT FILTER (Sep 12): the machinery's own calls,
        # browsable by outcome once real flips accumulate. The dropdown
        # appears only when a graded verdict exists (an all-pending
        # trail has nothing to filter); a bare/probe context — every
        # existing pin — fails open to ALL, the same discipline as the
        # sims toggle.
        _pick, _keep = "All verdicts", None
        _filter_note, _stub_anchors = "", ""
        _empty_verdict_note = ""
        if any(v != "pending" for v in verdict_of.values()):
            _opts = ["All verdicts", "PAYING", "COSTING", "pending"]
            if _in_runtime():
                # THE MASTER-LEVEL CONTROL (Sep 13): the widget lives on
                # MASTER beside the chart (render_verdict_control); the
                # trail resolves the SAME session_state pick — one seed,
                # one widget, one save. The seed runs here too (her
                # first stop may be the trail with MASTER above still
                # mounting; the session entry must exist either way).
                if "verdict_filter" not in st.session_state:
                    restored = _prefs_load().get("verdict_filter")
                    st.session_state["verdict_filter"] = (
                        restored if restored in _opts else "All verdicts")
                _pick = st.session_state["verdict_filter"]
            elif verdict in ("PAYING", "COSTING", "pending"):
                _pick = verdict   # the caller's explicit filter (tests)
            if _pick != "All verdicts":
                _keep = {i for i in range(len(evs))
                         if verdict_of.get(i) == _pick}
                _n_hidden = len(evs) - len(_keep)
                _filter_note = (f"<span class='dim'>{_n_hidden} decision"
                                f"{'s' if _n_hidden != 1 else ''} hidden by "
                                "the verdict filter (the MASTER control "
                                "above, beside the chart) — switch back to "
                                "'All verdicts' to read them</span>")
                # the hidden rows' anchor stubs: a chart glyph hrefs its
                # decision's id — under a filter the row may be hidden,
                # so the id must still EXIST or the click dead-ends
                _stub_anchors = "".join(
                    f"<a id='flip-{str(e.get('state') or '?')}-"
                    f"{str(e.get('action') or '?')}-"
                    f"{str(e.get('week') or '?')}"
                    f"{'-sim' if e.get('sim') else ''}'></a>"
                    for i, e in enumerate(evs) if i not in _keep)
                if not _keep:
                    # an empty verdict view says so honestly — the note
                    # rides the normal tail (census and the benched
                    # strip still ship, nothing renders as a dead end)
                    _empty_verdict_note = (f"<span class='dim'>no "
                                           f"decisions classified {_pick} "
                                           f"yet — the machinery has not "
                                           f"made that call</span>")
        for idx, e in enumerate(evs):
            if _keep is not None and idx not in _keep:
                continue
            _st_e = str(e.get("state") or "?")
            act = str(e.get("action") or "?")
            _vc = verdict_of.get(idx, "pending")
            _vchip = (" <span style='color:#4be38a;font-size:12px;'>PAYING</span>"
                      if _vc == "PAYING" else
                      (" <span style='color:#ff6b5e;font-size:12px;'>COSTING</span>"
                       if _vc == "COSTING" else
                       " <span style='color:#9aa0a6;font-size:12px;'>pending</span>"))
            if act == "flip":
                reflip = reflip_of.get(
                    (_st_e, str(e.get("week") or ""),
                     str(e.get("sim") or False)), False)
                col = "#ff6b5e"
                verb = ("RE-FLIPPED to the quantile (regime churn)"
                        if reflip else "AUTO-FLIPPED to the quantile")
                wk_n = e.get("losing_weeks")
            else:
                rearm = rearm_of.get(
                    (_st_e, str(e.get("week") or ""),
                     str(e.get("sim") or False)), False)
                col, verb = ("#4be38a", (
                    "RE-ARMED to live duty (regime churn)" if rearm
                    else "FLIP-BACK to live duty"))
                wk_n = e.get("winning_weeks")
            evid = e.get("evidence") or []
            ev_txt = " ".join(
                f"{d.get('week', '?')[2:]} {d.get('fit_edge', 0):+.2f}"
                for d in evid) or "(no two-sided weeks)"
            # THE ARC NAME (Sep 12): a sim's sim_source ends with the
            # replay's arc in parentheses ("... (the bench PAYS)") —
            # surface it inside the badge so the worked examples scan
            # at a glance. Fail-open: no parenthesized tail -> the bare
            # badge (old or handcrafted sim rows never fabricate an arc).
            sim_arc = ""
            if e.get("sim"):
                _src = str(e.get("sim_source") or "")
                _lp = _src.rfind("(")
                if _lp >= 0 and _src.endswith(")"):
                    _tail = _src[_lp + 1:-1].strip()
                    if _tail and ")" not in _tail and "\n" not in _tail:
                        sim_arc = _tail
            sim_badge = ("" if not e.get("sim") else
                         " <span style='color:#ffb347;'>[SIMULATION"
                         + (f" ({sim_arc})" if sim_arc else "")
                         + " — the diag's worked example, not a lived "
                           "decision]</span>")
            # THE ANCHOR (Sep 12): the chart's regime stamps href here —
            # the (state, action, decision-week[, sim]) id is the trail's
            # own dedupe key, so every glyph lands on its decision
            anchor = ("flip-{}-{}-{}{}".format(
                _st_e, act, str(e.get("week") or "?"),
                "-sim" if e.get("sim") else ""))
            rows.append(
                f"<a id='{anchor}'></a>"
                f"<b style='color:{col};'>{_st_e} {verb}</b>{sim_badge}{_vchip} "
                f"<span class='dim'>({e.get('ts') or e.get('week') or '?'} — "
                f"{wk_n} weeks, pooled edge {e.get('roll_delta', 0):+.2f})</span>"
                f"<br><span class='dim'>evidence: {ev_txt}</span>")
            # THE AFTERMATH (Sep 11): the decision graded in dollars —
            # the realized $/fire of the bar the decision put in charge
            # (after a flip that IS the quantile) pooled over the
            # post-decision weeks, vs the benched side's counterfactual
            # over the same weeks. The shared _event_aftermath math, so
            # the trail and the flip grade's txt read the same dollars.
            # A SIM event renders ONLY its embedded simulated aftermath
            # — its replay weeks never lived in the real ledger, so the
            # real-ledger math must never touch it.
            if e.get("sim"):
                sa = e.get("sim_aftermath")
                if isinstance(sa, dict) and sa.get("status") == "graded":
                    rl, cf = sa["realized"], sa["counterfactual"]
                    vcol = ("#4be38a" if sa["verdict"].startswith(
                                "the decision PAID") else "#ff6b5e")
                    rows.append(
                        f"<span class='dim'>aftermath (simulated): in the "
                        f"{sa['weeks']} weeks after, the {rl['bar']} "
                        f"realized ${rl['usd_per_fire']:+.2f}/fire "
                        f"({rl['fires']} fires) vs the {cf['bar']}'s "
                        f"${cf['usd_per_fire']:+.2f}/fire over the same "
                        f"weeks — </span>"
                        f"<b style='color:{vcol};'>{sa['verdict']}</b>")
                else:
                    rows.append("<span class='dim'>aftermath (simulated): "
                                "pending — the replay's weeks never lived "
                                "in the ledger</span>")
                continue
            am = am_of.get(idx)   # graded once, in the filter pre-pass
            if am["status"] == "pending":
                rows.append(f"<span class='dim'>aftermath: pending — "
                            f"{am['reason']}</span>")
            else:
                rl, cf = am["realized"], am["counterfactual"]
                vcol = ("#4be38a" if am["verdict"].startswith(
                            "the decision PAID") else "#ff6b5e")
                rows.append(
                    f"<span class='dim'>aftermath: in the {am['weeks']} "
                    f"weeks after, the {rl['bar']} realized "
                    f"${rl['usd_per_fire']:+.2f}/fire ({rl['fires']} fires) "
                    f"vs the {cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire "
                    f"over the same weeks — </span>"
                    f"<b style='color:{vcol};'>{am['verdict']}</b>")
            # THE FBW=3 CLAUSE (Sep 12): the week-count dial's own
            # counterfactual, priced in dollars — would ONE more re-arm
            # week (FBW=3, the diag_fbw3 dial) have saved this costing
            # re-arm? Every case states its honest shape, including the
            # ones where the extra week saves nothing.
            fbw = fbw_of.get(idx)
            if isinstance(fbw, dict):
                if fbw["status"] == "never-arms":
                    rows.append(
                        f"<span class='dim'>the extra re-arm week (FBW=3) "
                        f"would have denied this re-arm entirely (no third "
                        f"winning week) — saved "
                        f"${fbw['saved_total']:+,.2f} "
                        f"(${fbw['saved_per_fire']:+.2f}/fire over the "
                        f"{fbw['weeks']} weeks after)</span>")
                elif fbw["status"] == "denied":
                    if fbw["saved_total"] >= 0:
                        rows.append(
                            f"<span class='dim'>the extra re-arm week "
                            f"(FBW=3) would have held the bench one more "
                            f"week, re-arming at {fbw['arm_week']} — would "
                            f"have saved ${fbw['saved_total']:+,.2f} "
                            f"(${fbw['saved_per_fire']:+.2f}/fire over the "
                            f"delayed arm's {fbw['delayed_fires']} "
                            f"fires)</span>")
                    else:
                        rows.append(
                            f"<span class='dim'>the extra re-arm week "
                            f"(FBW=3) would have re-armed later at "
                            f"{fbw['arm_week']} and paid "
                            f"${abs(fbw['saved_total']):,.2f} LESS — the "
                            f"extra week would have cost here</span>")
                elif fbw["status"] == "same-week":
                    rows.append(
                        "<span class='dim'>the extra re-arm week (FBW=3) "
                        "re-arms on the same week — the pool guard, not "
                        "the week count, held this arm; nothing to "
                        "save</span>")
                elif fbw["status"] == "delayed":
                    rows.append(
                        f"<span class='dim'>the extra re-arm week (FBW=3) "
                        f"delays the re-arm to {fbw['arm_week']} — too few "
                        f"weeks have traded since to price it</span>")
            # THE POOL-THRESHOLD CLAUSE (Sep 13): the pool dial's own
            # counterfactual on the same COSTING rows — would raising
            # the pool bar to +0.25 have denied this re-arm? Every case
            # states its honest shape, fat pools included (same-week).
            pcf = poolcf_of.get(idx)
            if isinstance(pcf, dict):
                if pcf["status"] == "never-arms":
                    rows.append(
                        f"<span class='dim'>a +0.25 pool bar would have "
                        f"denied this re-arm entirely (no re-arm with a "
                        f"pool clearing the bar) — saved "
                        f"${pcf['saved_total']:+,.2f} "
                        f"(${pcf['saved_per_fire']:+.2f}/fire over the "
                        f"{pcf['weeks']} weeks after)</span>")
                elif pcf["status"] == "denied":
                    if pcf["saved_total"] >= 0:
                        rows.append(
                            f"<span class='dim'>a +0.25 pool bar would "
                            f"have held the bench, re-arming at "
                            f"{pcf['arm_week']} — would have saved "
                            f"${pcf['saved_total']:+,.2f} "
                            f"(${pcf['saved_per_fire']:+.2f}/fire over "
                            f"the delayed arm's {pcf['delayed_fires']} "
                            f"fires)</span>")
                    else:
                        rows.append(
                            f"<span class='dim'>a +0.25 pool bar would "
                            f"have re-armed later at {pcf['arm_week']} "
                            f"and paid ${abs(pcf['saved_total']):,.2f} "
                            f"LESS — the denial would have cost here"
                            f"</span>")
                elif pcf["status"] == "same-week":
                    rows.append(
                        "<span class='dim'>a +0.25 pool bar re-arms on "
                        "the same week — this pool already cleared the "
                        f"bar ({pcf.get('pool', 0):+.2f}); nothing to "
                        "save</span>")
                elif pcf["status"] == "delayed":
                    rows.append(
                        f"<span class='dim'>a +0.25 pool bar delays the "
                        f"re-arm to {pcf['arm_week']} — too few weeks "
                        f"have traded since to price it</span>")
            # THE BENCH'S FINAL VERDICT (Sep 12): a flip-back carries a
            # bench_final snapshot of the bench's OPEN live verdict at
            # the moment the bench ended (cumulative to-date dollars,
            # shared math) — preserved even though the state now
            # renders un-benched. Older events and honest-pending
            # snapshots simply render nothing here.
            bf = e.get("bench_final")
            if isinstance(bf, dict) and bf.get("verdict"):
                bcol = bf.get("verdict_col") or "#9aa0a6"
                rows.append(
                    f"<span class='dim'>the bench's final live verdict: "
                    f"{bf.get('text') or ''} — </span>"
                    f"<b style='color:{bcol};'>the bench was "
                    f"{bf['verdict']} when it ended</b>")
            # THE BORROW'S RECKONING (Sep 12): did the verdict this row's
            # decision borrowed while pending survive the realized
            # dollars? Agree states the truth held; a DISAGREEMENT reads
            # loud — the borrow rule itself got the dollars backwards.
            # Ungradeable cases (aftermath still pending, or a not-
            # operative borrow) render nothing — never a fake verdict.
            rk = rk_of.get(idx)
            if isinstance(rk, dict) and rk.get("status") == "agree":
                rows.append(
                    "<span class='dim'>the borrow's reckoning: the "
                    f"verdict was borrowed while pending and the "
                    f"realized aftermath AGREES — the borrow told the "
                    f"truth ({rk.get('graded')})</span>")
            elif isinstance(rk, dict) and rk.get("status") == "disagree":
                rows.append(
                    f"<span style='color:#ffb347;'>the borrow's "
                    f"reckoning: DISAGREES — {rk.get('text') or ''}</span>")
        benched = [st for st in ("OUT", "FLAT", "RISING")
                   if (flips.get(st) or {}).get("flipped")]
        if benched:
            # THE LIVE AFTERMATH (Sep 12): a currently-benched state's
            # strip grades the OPEN bench in dollars — the same shared
            # _event_aftermath on the state's newest real flip with an
            # open window (next_ev=None) — so PAID/COST reads BEFORE
            # the flip-back closes the decision. Pending renders its
            # honest reason; a flipped state without a flip event (or
            # an empty ledger) degrades to pending.
            # ONE MATH (Sep 12): the strip renders the SHARED
            # _open_bench_entries entries — the same dollars the
            # sidecar ships to the plate — not its own recount, so the
            # trail, the plate, and _flipgrade.json can never disagree.
            # Each graded entry's text carries the cumulative bench
            # cost-to-date (usd_total realized minus the counterfactual
            # over the same weeks).
            parts_b = []
            for en in _tbw._open_bench_entries(recs, evs)[1]:
                if en.get("verdict"):
                    vc = en.get("verdict_col") or "#9aa0a6"
                    parts_b.append(
                        f"{en['base']}<br><span class='dim'>live "
                        f"aftermath: {en.get('text') or ''} — </span>"
                        f"<b style='color:{vc};'>"
                        f"the bench is {en['verdict']}</b>")
                else:
                    parts_b.append(
                        f"{en['base']}<br><span class='dim'>live "
                        f"aftermath: {en.get('text') or 'pending'}</span>")
            rows.append("<span style='color:#ffb347;'>CURRENTLY BENCHED: "
                        + "<br>".join(parts_b) + "</span>")
        if not show_sims:
            rows.append(
                f"<span class='dim'>{_n_sims} simulated worked "
                f"example{'s' if _n_sims != 1 else ''} hidden — "
                f"'show worked examples' beside this restores them"
                f"</span>")
            _show_sims_btn()   # ONE CLICK (Sep 12): beside the note
        # THE VERDICT CENSUS (Sep 12): the machinery's record at a
        # glance — how many of its own calls paid, cost, still pend.
        # ONE FORMAT (Sep 12): the shared verdict_census so the plate's
        # chip and this line can never disagree. The reckoning tally
        # rides the same line: how those borrowed verdicts graded.
        rows.append("<span class='dim'>verdicts: "
                    + _tbw.verdict_census(_counts)
                    + _tbw.reckoning_tally(evs) + "</span>")
        if _empty_verdict_note:
            rows.append(_empty_verdict_note)
        if _filter_note:
            rows.append(_filter_note)
        if _stub_anchors:
            rows.append(_stub_anchors)
        return "<br>".join(rows)
    except Exception:
        return ""


def _render_tidebar_history(max_bytes: int = 120_000, rf_path=None):
    """The click-through under the TIDE BAR VERDICT plate: the full
    _tidebarweekly.txt history in an expander, plus an ISO-WEEK PICKER —
    'All weeks (newest first)' by default, or any single past week's
    sections in isolation. The report PREPENDS the newest section, so
    All-weeks already reads newest-first. Capped at max_bytes (a year
    of daily sections stays ~5 MB, so the cap is a page-stall guard,
    not a real ceiling). rf_path lets tests inject a sandbox report so
    the suite never overwrites the production file. Never raises."""
    try:
        rf = Path(rf_path) if rf_path \
            else Path(__file__).parent / "_tidebarweekly.txt"
        if not rf.exists():
            return
        txt = rf.read_text(encoding="utf-8", errors="replace")
        if len(txt.encode("utf-8")) > max_bytes:
            txt = txt[:max_bytes] + "\n… (history capped for the page — " \
                  "the full file is on disk)"
        with st.expander("📜 FULL TIDE BAR HISTORY — every graded week, "
                         "newest first"):
            secs = _tidebar_week_sections(txt)
            edges_by_week = _week_pooled_edges(secs)

            order = []
            for lbl, _ in secs:              # newest-first option list
                if lbl not in order:
                    order.append(lbl)
            # THE SNAP-BACK FIX (Sep 12): stable key + SEMANTIC options
            # (the week tags) with the verdict deltas rendered via
            # format_func — the old picker stored COUNTED labels (tag
            # + edges), so her pick snapped to All-weeks on every rerun
            # and again whenever the edges shifted under it. A stored
            # tag survives both: the deltas re-render around it.
            opts = (["All weeks (newest first)"] + order if order
                    else ["All weeks (newest first)"])

            def _fmt(o):
                # the week's verdict beside its label: per-state
                # canonical fit edges, rendered never stored
                if o.startswith("All weeks"):
                    return o
                e = edges_by_week.get(o) or {}
                parts = [f"{nm} {e[nm]:+.2f}"
                         for nm in ("OUT", "FLAT", "RISING") if nm in e]
                return f"{o}  ·  {'  '.join(parts)}" if parts else o

            pick = st.selectbox("ISO week", opts, index=0,
                                key="tb_week_pick", format_func=_fmt)
            if pick.startswith("All weeks"):
                st.code(txt, language=None)
            else:
                wk = pick
                body = "\n\n".join(t for lbl, t in secs if lbl == wk) \
                    if wk else ""
                st.code(body or f"(no section stored for {pick})",
                        language=None)
    except Exception as exc:
        logger.debug("tide-bar history expander failed: %s", exc)


@st.cache_data(ttl=FACTS_STALE_TTL_S)
def _today_facts(now_ts: int) -> list[str]:
    """MASTER's self-correction plate: today's numbers, auto-counted —
    TIER-AWARE (user rule, Sep 1: 'everything should auto adjust when the
    contest is selected'): the death line, buffer, and pace all come from
    the contest account (1k/5k/10k), and the banked number counts GOALS,
    not twenties. (Sep 13) now_ts is the call site's BUCKETED epoch second
    and RIDES THE CACHE KEY — the graded day rolls with the    bucket instead of freezing the first render's midnight boundary; the
    visible 'as of' stamp renders OUTSIDE this body at the plate call."""
    _plate_touch("todays-facts", ttl=FACTS_STALE_TTL_S)
    import sqlite3
    day = datetime.fromtimestamp(now_ts, MST).strftime("%Y-%m-%d")
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
    except Exception as exc:
        # the plate renders zeros rather than lying — but a silently dead
        # signals.db would have shown a 0/0 day forever with no trace
        logger.warning("signals.db query failed — score plate shows zeros: %s", exc)
    goals_n = 0
    banked = 0
    try:
        import json
        tp = json.loads((Path(__file__).parent / "take_payouts.json").read_text())
        todays = [r for r in tp.values() if str(r.get("ts", "")).startswith(day)]
        goals_n = len(todays)
        banked = sum(int(r.get("amount") or 0) for r in todays)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("take_payouts.json unreadable — "
                       "goals/banked show zero: %s", exc)
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
                except Exception as exc:
                    logger.debug("reset toast/voice failed: %s", exc)
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
                except Exception as exc:
                    logger.debug("ghost close failed for %s: %s",
                                 s.get("signal_id", "?"), exc)
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
    except Exception as exc:
        logger.debug("signal bus plate failed: %s", exc)


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
            except Exception as exc:
                logger.debug("winner-pairs db init failed: %s", exc)
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
                        except Exception as exc:
                            # a pair whose indicators blew up is skipped —
                            # the scan must survive, but not silently
                            logger.debug("deep-scan eval failed for %s: %s",
                                         futs[f], exc)
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
                    except Exception as exc:
                        logger.debug("champion db write failed: %s", exc)
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
        f"CONFIDENCE <span class='dim'>....... </span> {sig.confidence:.0f}%"
        f"  <span class='dim'>(auto-trade bar: {thr}% → </span>"
        f"<span style='color:{'#4be38a' if passes else '#ff6b5e'};'>{'PASSES' if passes else 'BELOW BAR'}</span><span class='dim'>)</span>",
    ]
    # ── SCAN-SET HONESTY (user, Sep 22: a champion the funnel can't read
    # must never read as a coming popup): the plate says what the scanner
    # is actually scanning. In the set → say so. Out of it → say so, plainly
    # (TRUMP case: a −0.4% coin was 'today's best pick' while the funnel
    # had nothing to say about it). Unknown → say that too.
    try:
        _scanned = _scanned_assets()
        _champ_a = _asset(champ.pair)
        if _scanned is None:
            lines.append("<span class='dim'>SCAN SET unknown — the funnel's movers "
                         "view isn't readable right now; popup eligibility can't "
                         "be judged from this plate.</span>")
        elif _champ_a in _scanned:
            lines.append(f"<span style='color:#4be38a;'>IN THE FUNNEL'S SCAN SET</span> "
                         f"<span class='dim'>({len(_scanned)} movers scanned) — "
                         "eligible for a popup when it fires.</span>")
        else:
            lines.append(f"<span style='color:#ffc36b;'>NOT IN THE FUNNEL'S SCAN SET</span> "
                         f"<span class='dim'>(the funnel scans only the movers, "
                         f"{len(_scanned)} now) — no popup can fire for this pick "
                         "until it moves into the movers list.</span>")
    except Exception as exc:
        logger.debug("scan-set honesty read skipped: %s", exc)
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
            # NO CONFIRM BUTTON (user rule, Sep 21, phone-only): the popup
            # route auto-tracks the champion into the watch at fire time —
            # this card never gated the entry, and the button only made it
            # look like the entry waited on a tap. VISIT-SAFETY still holds:
            # visiting this page silently switching the QC coin selection
            # was the choppiness fix (Sep 4) and that stays.
            if _fresh:
                try:
                    _sc = _scanned_assets()
                    if _sc is not None and _champ_asset in _sc:
                        st.toast(f"🏆 {_champ_asset} is today's best pick — "
                                 f"the popup is watching it, nothing to confirm",
                                 icon="🏆", duration=12)
                        _speak(f"{_champ_asset} is today's best pick! The popup is watching it!")
                        _play_sound("ding")
                    else:
                        st.toast(f"🏆 {_champ_asset} is today's best pick — but it's "
                                 f"NOT in the funnel's scan set (movers only), so no "
                                 f"popup will fire for it",
                                 icon="🟡", duration=12)
                except Exception as exc:
                    logger.debug("champion toast/voice failed: %s", exc)
    elif _fresh:
        _why = (f"{_asset(champ.pair)} is only {sig.confidence:.0f}% sure — needs the bar"
                if sig.action == "buy" else
                f"best survivor {_asset(champ.pair)} is a sell — nobody's buying right now")
        try:
            _advice = _no_pick_advice()
            st.toast(f"⛔ NO GOOD PICK this time — {_why}. {_advice} "
                     f"Don't enter. Try again later or check a coin yourself in Quick Check.",
                     icon="⛔", duration=15)
            _speak("No good pick this time. Don't enter. Try again later.")
            _play_sound("alarm")
        except Exception as exc:
            logger.debug("no-pick alert failed: %s", exc)

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
                _s_h = _scalp_eta(f"{asset}/USD", float(px),
                                  now=_now_mst())
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
                                _hm_floor = float(_bs_hm.popup_floor(_hm_tstate, _hm_lw_pts, None, pair))
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
                    except (AttributeError, ImportError) as exc:
                        logger.debug("FLAT half-size factor skipped: %s", exc)
                # THE JUST-PAID GUARD — RETIRED (user rule, Sep 21: "I
                # don't want a 'just paid guard' — it was replaced with
                # real data already"): no payout-based read, no refusal.
                # The real-data gates (stale-move trap on the 4h tape,
                # floor, tide, chase) judge the entry.
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
                st.session_state["qc_result"]["trend"] = _trend
                st.session_state["qc_result"]["carry_usd"] = _carry
                st.session_state["qc_result"]["spend_usd"] = _spend_usd
                st.session_state["qc_result"]["tide"] = _tide_state
                # ── THE PERSONAL OUT BAR (Sep 10, the QC mirrors the alert
                #    path — never disagree): when the tide is OUT, a
                #    candidate whose quick score clears the coin's PERSONAL
                #    bar passes the tide gate — the fleet's learned price
                #    (tide_learn.py) personalized by the coin's own tide
                #    memory (tide_personal_bar: ONE tax, the memory rides
                #    the bar; clamped to the learned band [86, 97]). The
                #    lone-wolf carve stays; the JOURNAL RELEASE is
                #    alert-side only (the QC never spends it).
                _tl_pass, _tl_why = False, ""
                if (_tide_state or "") == "OUT" and not _lw_pts:
                    try:
                        _tl_bar, _tl_meta = _bs4._tide_learn_bar()
                        _tl_pbar = _bs4.tide_personal_bar(pair, "OUT", _tl_bar)
                        if _tl_pbar is not None and sig.action == "buy" and not no_buy \
                                and float(sig.confidence) >= _tl_pbar:
                            _tl_pass = True
                            _tl_why = _tl_meta
                        elif _tl_pbar is not None:
                            _tl_why = (f"quick {float(sig.confidence):.1f} vs "
                                       f"personal bar {_tl_pbar:.0f} ({_tl_meta})")
                        else:
                            _tl_why = _tl_meta
                    except Exception as _tle:
                        _tl_why = f"tide learner unavailable ({_tle})"
                st.session_state["qc_result"]["tide_learned_pass"] = _tl_pass
                st.session_state["qc_result"]["tide_learned_why"] = _tl_why
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
                    _champ_floor = float(_bs5.popup_floor(_tide_state, _lw_pts, None, pair))
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
                        and ((_tide_state != "OUT") or _lw_pts or _tl_pass)
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
                                       "contest_id": _ca.current_contest_id(),
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
                            except (AttributeError, ImportError, TypeError) as exc:
                                logger.debug("rotation lockout mark failed: %s", exc)
                            # amber warnings ride WITH the green (data shows
                            # they don't change hit rate — but you should know)
                            _warns = []
                            if _chase > QC_CHASE_PCT:
                                _warns.append(f"already ran +{_chase:.1f}%")
                            if _trend == "FALLING":
                                _warns.append("trend falling — don't sit in it")

                            _warn_txt = (f"  ⚠ {' · '.join(_warns)}." if _warns else "")
                            _reentry_txt = ""
                            if _rot_reentry:
                                # display only — the fine is retired (Sep 21)
                                _reentry_txt = "RE-ENTRY — fired earlier today. "
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
                                _fine_say = ""  # the re-entry fine is retired (Sep 21)
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
                            except Exception as exc:
                                logger.debug("green-light toast/voice failed: %s", exc)
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
                            except Exception as exc:
                                logger.debug("already-watching toast/voice failed: %s", exc)
                    except Exception as exc:
                        logger.debug("auto-watch add failed: %s", exc)
                else:
                    if (sig.action == "buy" and not no_buy
                            and not _rot_ok):
                        # DEAD DISPLAY since the lock's retirement (Sep 10 —
                        # the gate always allows); kept so an old ledger
                        # state can never show a green here by accident.
                        try:
                            st.toast(f"⛔ ALREADY RAN THIS ROTATION — {asset}: {_rot_reason}. "
                                     f"The whipsaw re-entry is where double-dip fines eat the "
                                     f"$20s. Next rotation re-arms it.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"Already ran! {asset} is cold until the next rotation!", rate=1)
                        except Exception as exc:
                            logger.debug("already-ran toast/voice failed: %s", exc)
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
                        except Exception as exc:
                            logger.debug("no-escape toast/voice failed: %s", exc)
                    elif (sig.action == "buy" and not no_buy
                            and _yctx and _yctx.get("shape") == "blowoff"):
                        try:
                            st.toast(f"⛔ THIS HOUR DUMPED YESTERDAY — {asset}: "
                                     f"{_yctx.get('detail', '')}. Today's signal at this hour is a "
                                     f"re-run of a shape that already paid. Wait for a fresh base.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"No! {asset} dumped at this hour yesterday! Wait for a base!", rate=1)
                        except Exception as exc:
                            logger.debug("dump-hour toast/voice failed: %s", exc)
                    elif (sig.action == "buy" and not no_buy
                            and _tide_state == "OUT" and not _lw_pts and not _tl_pass):
                        try:
                            st.toast(f"🌊 TIDE'S OUT — only {_tide_pct:.0f}% of the market is rising. "
                                     f"All ships sink together — don't enter anything right now. "
                                     f"Wait for the tide to turn. "
                                     f"(A coin moving +5% or more on her own still gets the "
                                     f"lone-wolf exception — this one isn't. A score clearing "
                                     f"the tide's learned bar passes — {_tl_why or 'the bar is not priced yet'}.)",
                                     icon="🌊", duration=20)
                            _play_sound("alarm")
                            _speak(f"The tide is out! All ships sink together! Wait!", rate=1)
                        except Exception as exc:
                            logger.debug("tide-out toast/voice failed: %s", exc)
                    elif (sig.action == "buy" and not no_buy
                            and _trend == "FALLING"):
                        try:
                            st.toast(f"⛔ DOWNWARD TREND — {asset}: the 1h trend is falling and still "
                                     f"making lows. No downward trend picks — wait for it to base. "
                                     f"Skip it.",
                                     icon="⛔", duration=18)
                            _play_sound("alarm")
                            _speak(f"No! {asset} is falling! Wait for it to base!", rate=1)
                        except Exception as exc:
                            logger.debug("falling toast/voice failed: %s", exc)
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
                        except Exception as exc:
                            logger.debug("death-line toast/voice failed: %s", exc)
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
                        except Exception as exc:
                            logger.debug("too-thin toast/voice failed: %s", exc)
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
                        except Exception as exc:
                            logger.debug("too-slow toast/voice failed: %s", exc)
                    elif sig.action == "buy" and no_buy:
                        try:
                            st.toast(f"⛔ COOLDOWN (8pm-midnight) — no new buys. {asset} waits.",
                                     icon="⛔", duration=15)
                            _play_sound("alarm")
                            _speak(f"Cooldown! No buys!", rate=1)
                        except Exception as exc:
                            logger.debug("cooldown toast/voice failed: %s", exc)
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
                        except Exception as exc:
                            logger.debug("wrong-action toast/voice failed: %s", exc)
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
    # THE LEARNED OUT BAR (Sep 10): the QC mirrors the alert path — a
    # quick score that clears the tide's learned bar passes OUT, so the
    # verdict text and the LED show the same mercy the scanner shows.
    tide_learned_pass = bool(result.get("tide_learned_pass"))
    tide_learned_why = str(result.get("tide_learned_why", "") or "")
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
        if passes and tide_learned_pass:
            return "SCALP GREEN LIGHT — the OUT tide's learned bar was paid " \
                   f"({tide_learned_why or 'the data priced this tide'})"
        if passes:
            return "SCALP GREEN LIGHT"
        if no_buy:
            return "COOLDOWN — NO BUYS"
        if not rot_ok:  # dead since the lock's retirement (Sep 10)
            return "NO GREEN — ALREADY RAN THIS ROTATION"
        if yshape == "blowoff":
            return "NO GREEN — THIS HOUR DUMPED YESTERDAY"
        if tide_state == "OUT" and not lw_pts and not tide_learned_pass:
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
              else "a" if (tide_state == "OUT" and (lw_pts or tide_learned_pass))
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
    elif is_reentry:
        # display only — the fine is retired (Sep 21): the same real-data
        # gates judged this entry as any other
        _fine_led, _fine_txt = "a", "RE-ENTRY (fired earlier today)"
    else:
        _fine_led, _fine_txt = "g", "FRESH — FIRST FIRE TODAY"
    status_leds = (
        led("LIQUID" if liquid else f"ILLIQUID < ${cfg.min_volume_usd_24h/1e6:.0f}M", "g" if liquid else "r") +
        led(_pace_txt, "g" if goal_ok else "r") +
        led(f"TIDE {tide_state}" if tide_state else "TIDE: NO DATA", _tcol2) +
        (led(f"🐺 LONE WOLF — {lw_note}", "a") if lw_pts else "") +
        (led("🌊 TIDE BAR PAID — " + (tide_learned_why or "the learned OUT bar"), "a")
         if tide_learned_pass else "") +
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
    except Exception as exc:
        logger.debug("liquidity advisory plate failed: %s", exc)

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
        # THE ONE ENTRY CONTROL — RETIRED here too (Sep 21): popups
        # auto-track with the scanner's fire-time numbers; nothing to fix.
        pass

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
    except Exception as exc:
        logger.debug("trailing-high peek failed: %s", exc)
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
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("live-price ticker merge skipped: %s", exc)

    sig = _eval(df, cfg)

    entry = None
    try:
        for p in sb.get_open_positions():  # read-only (real spot positions)
            if p["pair"] == pair:
                entry = p.get("execution_price") or p.get("entry_price")
                break
    except Exception as exc:
        logger.debug("live entry lookup failed: %s", exc)
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
    watch_status = []
    # (Sep 22, one brain) no alerts dict: the console builds no exit
    # verdicts, so there is nothing to relay — the scanner pops the
    # console and pushes the phone itself.
    # ── THE ONE BRAIN (Sep 22): the console no longer runs its own exit
    # evaluator — that second brain was the ghost-SELL class (it popped
    # "trend broke, SELL ADA" the scanner's exit watch never called).
    # The scanner's exit watch — the adaptive profit guard, the trend
    # read, the trail — is the ONLY brain that can call GET OUT, and it
    # pops the console and pushes the phone itself. The light still does
    # the pure-price work below: the GOAL CALL and the HOLD scoreboard.
    # (The manual Exit Check page keeps its full readout — a diagnostic
    # you open, not a light that fires on its own.)
    for a, pair in held.items():
        watch_status.append({"pair": pair, "kind": "wallet",
                             "exit": False, "reasons": []})
    for pair, w in contest_watch.items():
        try:
            _live = None
            try:
                _t = _ticker_px(pair)
                _live = float(_t) if _t and float(_t) > 0 else None
            except Exception:
                _live = None
            watch_status.append({
                "pair": pair, "kind": "contest", "exit": False,
                "reasons": [], "entry": w.get("entry"),
                "high": w.get("high"), "live": _live,
            })
        except Exception:
            continue
    if contest_watch:
        # merge-save (the TIA lesson, Sep 9): a clear from another
        # console (or this one's panel) may land while this cycle ran --
        # re-read the disk truth and save only pairs that still exist
        # there, so a stale cycle can never resurrect a cleared record
        _fresh = _load_watch()
        _merged = dict(_fresh)
        for _k, _v in contest_watch.items():
            if _k in _fresh:
                _mv = dict(_v)
                # the high is a monotonic tracker -- NEVER let this
                # cycle's stale read lower what another console raised
                # while we ran (the trailing exit would loosen)
                _mv["high"] = max(float(_v.get("high") or 0),
                                  float(_fresh[_k].get("high") or 0))
                _merged[_k] = _mv
        if _merged != _fresh:
            _save_watch(_merged)  # persist updated tracked highs

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
                                     f"— STAY CLOSE, the exit is hers", icon="🎯", duration=15)
                        else:
                            st.toast(f"🎯 {glevel}× GOAL — {asset} up {pct:.1f}%",
                                     icon="🎯", duration=15)
                        _play_sound("ding")
                    except Exception as exc:
                        logger.debug("goal toast/voice failed: %s", exc)
    except Exception as exc:
        logger.debug("goal watch sweep failed: %s", exc)

    # ── popups: fire on every page — this is the light ────────────────
    contest_rows = [v for v in watch_status if v["kind"] == "contest"]
    try:
        # (Sep 22, one brain) the red SELL NOW toast retired with the
        # console's own evaluator — GET OUT comes only from the scanner's
        # exit watch (its own popup + push). What remains is the scoreboard.
        if contest_rows:
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
    except Exception as exc:
        logger.debug("scoreboard toast failed: %s", exc)

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
        except Exception as exc:
            logger.debug("trade-done toast/voice failed: %s", exc)
    return True


# ── THE DROPDOWN RACE FIX (Sep 21, user spec: "deterministic, persistent,
# stable across reruns on desktop and mobile") ─────────────────────────
# The race: a keyless st.expander's open/close state lives in the BROWSER
# DOM, tied to the widget instance — and Streamlit hashes that identity
# from the LABEL. The strip's labels carry the newest popup / voice line,
# so every new line rebuilt the expander as a NEW widget and it snapped
# shut mid-read. The fix: the open/closed decision lives in
# session_state under a STABLE key (never in the widget identity), the
# expander renders expanded= from that state, and explicit Open / Close /
# mobile buttons remove the tap race entirely — the expander becomes a
# pure display widget. A context manager keeps call sites 1:1 with the
# old `with st.expander(...)` shape (no body re-indent churn).
from contextlib import contextmanager as _ctxmgr


@_ctxmgr
def stable_expander(key: str, title: str):
    """An expander whose expansion survives any rerun or remount.

    1. session_state init guard: first rerun defaults to closed.
    2. stable key: the state key is the identity — the changing label
       can no longer rebuild the widget's state. The header tap IS the
       control: it writes this state, so it persists like any other.
    3. wrapped in a stable container to damp DOM rebuild flicker.

    (Sep 21) the Open/Close/Tap-to-Expand button trios are retired:
    with the state on a stable key the header tap persists on its own,
    and three unlabeled buttons per section told nobody anything.
    """
    if key not in st.session_state:
        st.session_state[key] = False
    _open = bool(st.session_state[key])
    with st.container():
        with st.expander(title, expanded=_open):
            yield


def _render_her_words():
    """💬 HER WORDS — every line Zira said, on screen (user rule, Aug 30:
    "put her notifications on the console so I can see them if my volume
    is off"). Reads voice_log.jsonl written by the scanner's single mouth.
    The LATEST line shows in the expander title — visible at a glance —
    with the full recent history inside.
    MERGED (Sep 21, user rule: "the contest is on my phone and I need to
    see so I can match the records to the actual contest"): the open
    WATCHING records sit at the top of the same expander — entry, live
    PnL, goal, window clock — so one glance matches her words to the
    contest, then SOLD clears each one."""
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
        # the watched-count rides the title so the phone glance sees it
        # before the expander even opens
        try:
            _n_watch = len(_load_watch() or {})
        except Exception:
            _n_watch = 0
        _watch_tag = f"  · 👁 {_n_watch} watched" if _n_watch else ""
        with stable_expander("her_words",
                             f"💬 HER WORDS — \"{_latest}\"{_watch_tag}  (tap to read all)"):
            # ── WATCHING NOW (the merge, Sep 21): the open records first —
            #    same math as the WATCHING card (entry, live px, goal, the
            #    record's own window clock). A dead price feed dims the row
            #    instead of hiding it: an open trade is never invisible.
            try:
                _cw = _load_watch() or {}
            except Exception:
                _cw = {}
            if _cw:
                _bal = 0.0
                try:
                    import contest_account as _ca_hw
                    _bal = float(_ca_hw.load_state().get("current_balance") or 0.0)
                except Exception:
                    _bal = 0.0
                _wrows = []
                for _wp, _w in list(_cw.items()):
                    try:
                        _entry = float(_w.get("entry") or 0)
                        _size = float(_w.get("size") or 0) or _bal
                        _goal = float(_w.get("goal_pct") or 0) or 1.5
                        try:
                            _added = datetime.fromisoformat(_w["added"])
                        except Exception:
                            _added = _now_mst()
                        try:
                            _win = (float(_w.get("window_min") or 0)
                                    or max(60.0, min(180.0, _goal * 60.0)))
                        except Exception:
                            _win = 90.0
                        _left = int((_added + timedelta(minutes=_win)
                                     - _now_mst()).total_seconds() // 60)
                        _px = _ticker_px(_wp)
                        if _entry > 0 and _px and _size > 0:
                            _usd = (_px / _entry - 1.0) * _size
                            _pct = (_px / _entry - 1.0) * 100
                            _col = "#4be38a" if _usd >= 0 else "#ff6b5e"
                            _flags = "🎯 GOAL HIT" if _pct >= _goal else ""
                            if _left <= 0:
                                _flags += (" · " if _flags else "") \
                                    + "⏰ time's up — staying close (exit waits for the trend)"
                            _wrows.append(
                                f"<div style='padding:6px 0;"
                                f"border-bottom:1px solid rgba(255,255,255,0.08);'>"
                                f"<b>{_asset(_wp)}</b> "
                                f"<span style='color:{_col};font-family:Audiowide;"
                                f"font-size:20px;'>{'+' if _usd >= 0 else '-'}"
                                f"${abs(_usd):,.0f}</span> "
                                f"<span class='dim'>· {_pct:+.1f}% · goal +{_goal:.1f}% · "
                                f"{_flags or f'{_left} min left'}</span></div>")
                        else:
                            _wrows.append(
                                f"<div style='padding:6px 0;"
                                f"border-bottom:1px solid rgba(255,255,255,0.08);'>"
                                f"<b>{_asset(_wp)}</b> <span class='dim'>· entered "
                                f"{_fmt_price(_entry) or '?'} · no live price right "
                                f"now</span></div>")
                    except Exception as exc:
                        logger.debug("her-words watch row failed for %s: %s",
                                     _wp, exc)
                if _wrows:
                    st.markdown(
                        "<span class='dim' style='font-size:12px;'>👁 WATCHING NOW — "
                        "match these to the contest</span>"
                        + "".join(_wrows)
                        + "<div style='padding:4px 0 8px 0;'>"
                          "<span class='dim' style='font-size:12px;'>— everything "
                          "she said —</span></div>",
                        unsafe_allow_html=True)
            st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("her-words expander failed: %s", exc)


def _render_popup_history():
    """📣 POPUP HISTORY — every popup the scanner fired, on screen (Sep 21,
    user rule: "All popups are now visible from the console"). Reads
    popups.jsonl — the scanner's APPEND-ONLY popup ledger (BUY NOW and GET
    OUT NOW, journaled at fire time in background_scanner.popup_journal).
    Built exactly like HER WORDS: the latest popup rides the expander
    title, the recent history sits inside, newest first. Because the
    ledger is append-only, a clobbered watch, a phone-push failure or a
    closed scanner window can never erase what fired — the history IS the
    record the phone contest is matched against."""
    try:
        import json as _json
        import html as _html
        from pathlib import Path as _P
        f = _P(__file__).parent / "popups.jsonl"
        if not f.exists():
            return
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return
        rows = []
        latest_title = ""
        for l in reversed(lines[-12:]):
            try:
                d = _json.loads(l)
                if str(d.get("kind", "")) == "TEST":
                    continue  # journal plumbing probes never display
                ts = str(d.get("ts", ""))
                hhmm = ts[11:16] if len(ts) >= 16 else ""
                kind = str(d.get("kind", "POPUP"))
                title = str(d.get("title", ""))
                body = str(d.get("text", ""))
                if not latest_title:
                    latest_title = title
                _kcol = "#ff6b5e" if "OUT" in kind.upper() or "SELL" in kind.upper() \
                    else "#4be38a"
                _blines = [s.strip() for s in body.split("\n") if s.strip()]
                _shown = _blines[:3]
                _more = f" <span class='dim'>· …{len(_blines) - 3} more</span>" \
                    if len(_blines) > 3 else ""
                _body_html = "<br>".join(
                    f"<span class='dim'>{_html.escape(s)}</span>" for s in _shown)
                rows.append(
                    f"<div style='padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.08);'>"
                    f"<span class='dim' style='font-family:monospace;'>{hhmm}</span>"
                    f"&nbsp;<span style='color:{_kcol};font-family:Audiowide;'>"
                    f"{ _html.escape(kind.upper()) }</span>"
                    f"&nbsp;&nbsp;<b>{_html.escape(title)}</b>"
                    + (f"<br>{_body_html}{_more}" if _body_html else "")
                    + "</div>")
            except Exception:
                continue
        if not rows:
            return
        _latest = _html.escape(latest_title[:70]) + ("…" if len(latest_title) > 70 else "")
        with stable_expander("popup_history",
                             f"📣 POPUP HISTORY — \"{_latest}\"  (tap to read all)"):
            st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("popup-history expander failed: %s", exc)


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
            with stable_expander("bots_book_flat",
                             "🤖 BOT'S BOOK — flat (no open bot trades)"):
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
        with stable_expander("bots_book",
                             f"🤖 BOT'S BOOK — {'  ·  '.join(title_bits)}"):
            st.markdown("".join(rows), unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("bot-book plate failed: %s", exc)


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
            except Exception as exc:
                logger.debug("her-words toast/voice failed: %s", exc)
        st.session_state["her_words_last_ts"] = newest_ts
    except Exception as exc:
        logger.debug("her-words sweep failed: %s", exc)


# ── THE SCANNER PULSE (Sep 22): the light CYCLE's heartbeat, read from
# scanner_pulse.json — one beat per completed contest_light cycle,
# stamped in the scanner's loop (a raise there never beats). This is
# the whole-machine pulse: it beats even with NO pairs watched, so a
# crashed cycle or a dead scanner is visible on the auto-watch plate
# instead of invisible.
PULSE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "scanner_pulse.json")


def _scanner_pulse():
    """(cycles, age_s) from the pulse file — (None, None) when the
    scanner has never beaten (file missing, empty, or absurd — a stale
    file can't masquerade as a live machine)."""
    try:
        import json as _json
        with open(PULSE_JSON, "r", encoding="utf-8") as fh:
            _p = _json.load(fh)
        _cyc = int(_p.get("cycles") or 0)
        _age = time.time() - float(_p.get("last_cycle_ts") or 0)
        if _cyc <= 0 or _age <= 0 or _age > 86400:
            return None, None   # (CodeRabbit rule: never a 29-million-minute silence)
        return _cyc, _age
    except Exception:
        return None, None


def _fmt_pulse_age(age: float) -> str:
    if age < 90:
        return f"{age:.0f}s"
    if age < 3600:
        return f"{age / 60:.0f}m"
    return f"{age / 3600:.1f}h"


def _pulse_html():
    """The auto-watch plate's heartbeat line — alive / slow / frozen.
    SCAN-AWARE (Sep 22, the boot-night incident): the full tournament
    runs inside the same loop pass as the light cycle and held it ~9
    minutes — the pulse froze and this line cried FROZEN for a machine
    that was working. While the pulse says phase=scan, the age reads
    alive-scan up to a 12-minute grace (Sep 23: the observed full scan
    grew past the old 10-minute worst, and the generic slow band was
    pre-empting the scan branch entirely — a 2-4 minute scan lull read
    'slow' for a machine that was mid-bracket). Past the grace: FROZEN
    — a scanner dead mid-scan is still caught, and the per-pair stamps
    (SILENT >180s) never get scan grace."""
    cyc, age = _scanner_pulse()
    if cyc is None:
        return ("<span style='color:#ffc36b;'>scanner: no heartbeat yet — "
                "give it a minute (or start the scanner)</span>")
    _scanning = False   # fail-soft: an unreadable phase is just the old rule
    try:
        import json as _json
        with open(PULSE_JSON, "r", encoding="utf-8") as _fh:
            _scanning = (_json.load(_fh).get("phase") == "scan")
    except Exception as _scan_exc:
        # fail-soft by design: an unreadable pulse file renders as idle
        # (the plate's fail-soft rule) — the trace keeps the reason visible
        logging.getLogger(__name__).debug(
            "pulse phase read failed (%s) — plate renders idle", _scan_exc)
    if _scanning:
        # the phase reads FIRST (Sep 23): a running scan is never 'slow'
        # by the generic band, and the grace is the observed worst scan
        # plus margin — a scanner dead mid-scan still trips it
        if age <= 720:
            col, word = "#4be38a", "alive-scan (tournament running)"
        else:
            col, word = "#ff6b5e", "FROZEN (scan grace expired)"
    elif age <= 90:
        col, word = "#4be38a", "alive"
    elif age <= 300:
        col, word = "#ffc36b", "slow"
    else:
        col, word = "#ff6b5e", "FROZEN"
    return (f"<span style='color:{col};'>scanner: {word} — cycle #{cyc:,} "
            f"finished {_fmt_pulse_age(age)} ago</span>")


# ── THE GUARD BREAKDOWN (Sep 23, user: "show each watched pair's live
# GuardFactor breakdown — env, discipline, MLT, vol, memory — so I can
# see why the net is wide or tight"): the scanner stamps the FINALIZED
# factor and its named parts onto the watch row each check
# (`guard_brk`); this renders the why — it never computes it (the
# one-brain rule: the page reads, the scanner decides).
def _guard_breakdown_rows(w: dict, pct: float) -> list[str]:
    brk = w.get("guard_brk") or {}
    gf = brk.get("gf")
    if not isinstance(gf, (int, float)) or gf <= 0:
        return ["<span class='dim'>GUARD ......... the net is the committed "
                "fixed rules — the guard hasn't read this pair yet</span>"]
    parts = brk.get("parts") or {}
    names = (("env", "env"), ("disc_mod", "disc"), ("mlt_corr", "mlt"),
             ("vol_mod", "vol"), ("asset_mem", "mem"),
             ("funding_mod", "fund"), ("decay_mod", "decay"))

    def _fmt(key, label):
        v = parts.get(key)
        if key == "mlt_corr":
            return f"{label} {v:+.2f}" if isinstance(v, (int, float)) else f"{label} —"
        return f"{label} {v:.2f}" if isinstance(v, (int, float)) else f"{label} —"

    bits = " · ".join(_fmt(k, lbl) for k, lbl in names)
    gcol = "#4be38a" if gf >= 1.2 else "#ff6b5e" if gf < 0.9 else "#ffc36b"
    mood = ("LET IT RUN" if gf >= 1.2 else
            "TIGHTEN" if gf < 0.9 else "normal trailing")
    carried = brk.get("carried_from")
    head = (f"GUARD <span class='dim'>.........</span> "
            f"<span style='color:{gcol};'>net ×{gf:.2f} — {mood}</span>")
    if brk.get("readable") is False and carried:
        head += (f" <span class='dim'>(carried from {carried} — the live "
                 f"loop is unreadable)</span>")
    rows = [head,
            f"<span class='dim' style='font-size:12px;margin-left:14px;'>"
            f"{bits}</span>"]
    extras = []
    rcl = brk.get("rcl")
    if rcl and rcl != "NORMAL":
        rm = brk.get("rcl_mult")
        extras.append(f"RCL {rcl}" + (f" ×{rm:.2f}" if isinstance(rm, (int, float)) and rm != 1.0 else ""))
    sm = brk.get("sal_mult")
    if isinstance(sm, (int, float)) and sm != 1.0:
        extras.append(f"SAL ×{sm:.2f}")
    if brk.get("momentum"):
        extras.append("MOMENTUM +15%")
    if brk.get("endgame"):
        extras.append("END-GAME")
    if extras:
        rows.append("<span class='dim' style='font-size:12px;margin-left:14px;'>"
                    + " · ".join(extras) + "</span>")
    return rows


def _render_light_panel(interval: str):
    """The always-visible light panel — rendered from the last check."""
    watch_status = st.session_state.get("watch_status") or []
    # (Sep 22, one brain) no console-side exit verdict exists anymore —
    # the light is always green unless the scanner's own GET OUT popup
    # says otherwise. n_wallet keeps the bot-trades line alive.
    n_wallet = sum(1 for v in watch_status if v["kind"] == "wallet")
    _w = _load_watch()
    # THE HAIL MARY clock rides the panel header (user rule, Sep 4 — kept Sep 5)
    _hm_note = ""
    try:
        import contest_account as _ca_lp
        _hm_left = _ca_lp.hail_mary_minutes_left()
        if _hm_left > 0:
            _hm_note = f" · 💪 HAIL MARY {max(_hm_left, 0):.0f}m (the emergency hour)"
    except Exception as exc:
        logger.debug("hail-mary window lookup failed: %s", exc)
    lines = [f"<span class='dim'>{_now_mst().strftime('%I:%M %p')} · every {interval} · "
             f"green = hold, red = sell{_hm_note}</span>",
             # (Sep 22) THE SCANNER PULSE — the whole-machine heartbeat on
             # its own line, above every position: a crashed cycle or a
             # dead scanner shows even with NO pairs watched.
             _pulse_html()]
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
                    _eta = (_hours_to_scalp(v["pair"], entry,
                                            now=_now_mst()) or 0.75)
                    _rem = max(2.0 * _eta * 60.0, 45.0) - _elapsed
                _goal = float(rec.get("goal_pct") or 0.0)
                _near = (_rem <= 15.0) or (_goal > 0 and pnl >= _goal * 0.75)
                clock_html = (f' · <span style="color:{"#ffc36b" if _near else "#8d8271"};">'
                              f'~{max(_rem, 0):.0f}m left'
                              + (" · STAY CLOSE" if _near else " · walk around")
                              + '</span>')
            except Exception as exc:
                logger.debug("rotation clock html failed: %s", exc)
            bail = v.get("high", 0) * (1 - CONTEST_TRAIL_PCT / 100) if v.get("high") else None
            bail_txt = f" · out at {_fmt_price(bail)}" if bail else ""
            # (Sep 22) liveness rides the same line — the watcher's check
            # age straight from the watch row, silent ≠ healthy.
            try:
                _age = time.time() - float(rec.get("checked_ts") or 0)
            except Exception:
                _age = None
            if _age is not None and (_age <= 0 or _age > 86400):
                _age = None   # (CodeRabbit) a missing stamp is NEVER a 29-million-minute silence
            # (Sep 23) THE GUARD BADGE — the finalized factor the exit
            # watch enforces, straight from the row's guard_brk stamp.
            _gb_txt = ""
            try:
                _gb = rec.get("guard_brk") or {}
                _gfg = _gb.get("gf")
                if isinstance(_gfg, (int, float)) and _gfg > 0:
                    _gcol = "#4be38a" if _gfg >= 1.2 else "#ff6b5e" if _gfg < 0.9 else "#ffc36b"
                    _gb_txt = (f" · <span style='color:{_gcol};'>net ×{_gfg:.2f}"
                               + (" · EG" if _gb.get("endgame") else "")
                               + (" · MOM" if _gb.get("momentum") else "")
                               + "</span>")
                else:
                    _gb_txt = " · <span style='color:#8d8271;'>net fixed</span>"
            except Exception:
                _gb_txt = ""
            if _age is not None and 0 < _age <= 180:
                live_txt = (f" · <span style='color:#4be38a;'>checked "
                            f"{_age:.0f}s ago</span>")
            elif _age is not None and _age > 180:
                live_txt = (f" · <span style='color:#ff6b5e;'>SILENT "
                            f"{_age / 60:.0f}m</span>")
            else:
                live_txt = (" · <span style='color:#ffc36b;'>not checked "
                            "yet</span>")
            lines.append(f"<span style='color:#4be38a;font-size:16px;'>◆ {asset}</span> {pnl_html}"
                         f"{clock_html}{live_txt}{bail_txt}{_gb_txt}")
    if n_wallet:
        lines.append(f"<span class='dim'>bot trades: {n_wallet} (watched separately)</span>")
    st.markdown(plate("MY TRADES", lcd(lines, tone="green")),
                unsafe_allow_html=True)


# THE SEMANTIC-OPTIONS IDIOM (Sep 13, the verdict-dropdown pattern,
# fleet-wide): a widget stores plain words — never a derived display
# string — and format_func renders the decorated label. The entry
# form's counted/decorated labels were the last stored-display class
# (the radios' glyph prefixes WERE the stored state, read back with
# startswith); the widgetsweep now gates this class.
_EC_SAME, _EC_NEW = "same", "new"
_EC_MODES = [_EC_SAME, _EC_NEW]


def _ec_mode_lbl(m):
    return ("◉ SAME TRADE — fix my numbers" if m == _EC_SAME
            else "◯ NEW TRADE — fresh clock")


_EC_HOW_SPENT, _EC_HOW_DIRECT = "spent+owned", "entry+size"
_EC_HOWS = [_EC_HOW_SPENT, _EC_HOW_DIRECT]


def _ec_how_lbl(h):
    return ("◉ $ you spent + coins you own" if h == _EC_HOW_SPENT
            else "◯ entry $ + size $")


# THE ONE ENTRY CONTROL — RETIRED (user rule, Sep 21): "My entry-fix the
# numbers is no longer of any use" — popups auto-track at fire time with
# the scanner's own numbers (auto_track_popup, one writer per route), the
# GET OUT auto-close stops the watch itself, and SOLD is the only tap
# left (the profit collect that frees the hunt for the next setup).
# Nothing manual to fix, nothing manual to register. The mode constants
# above are kept (don't delete anything).


def render_auto_watch(cfg: TradingConfig, kraken):
    """THE LIGHT — global (mounted on every page). A check fires whenever
    it's due: on any page navigation OR on the interval timer while you sit
    still. Popups + sounds land on every page either way."""
    sb = _bridge()
    wallet = wallet_snapshot(kraken)
    held = {a: f"{a}/USD" for a in sorted(wallet["holdings"].keys())}

    _cs = _load_console_state()
    # SEMANTIC STATE, DERIVED DISPLAY (Sep 13): the toggles and the
    # interval carry plain keys — session_state keeps the semantic
    # entry, display is derived at render — the init-on-missing shape
    # the verdict dropdown shipped (never value= + session writes:
    # Streamlit's widget-policies warn). The interval stores SECONDS
    # (the machine's number); the panel's 'every X min' is derived.
    # FIXED ON, HIDDEN (Sep 21, user rule — critical to the system):
    # AUTO-WATCH, GOAL CALL and HER VOICE were console toggles, but the
    # switches created the one failure mode that matters — snap-to-OFF
    # (the toggle-guard saga: a widget-defaulting bug silently turned
    # the watch off under a live position, twice). The features are the
    # system's spine: they no longer render as switches and can no
    # longer be off. Their persisted keys stay ON in console_state.json
    # (the scanner's _voice_enabled() still reads voice_on; default ON).
    watch_on = bool(_cs.get("watch_on", True)) or True
    if "watch_interval_s" not in st.session_state:
        _ci = _cs.get("watch_interval", 60)
        if isinstance(_ci, str):   # legacy console_state string
            _ci = {"1 min": 60, "2 min": 120,
                   "5 min": 300}.get(_ci, 60)
        st.session_state["watch_interval_s"] = (
            _ci if _ci in (60, 120, 300) else 60)
    # one compact settings row (Sep 4 smoothness pass): the interval +
    # goal + voice toggles used to stack three full-width rows between
    # the AUTO-WATCH switch and your trades on EVERY page
    # two-column settings row (Sep 21 re-fit): the interval + the
    # volume dial share the width; the status line below is full width
    _s1, _s2 = st.columns([1, 1])
    with _s1:
        interval = st.selectbox(
            "Check every", [60, 120, 300], index=0,
            format_func=lambda s: f"{s // 60} min",
            key="watch_interval_s")
    with _s2:
        # THE VOLUME DIAL (restored Sep 21, user rule: "Volume is
        # separated and controlled with the dial 0-100") — the loudness
        # of her voice, not whether she speaks. Her voice persists
        # across store writes: the value= re-seeds from the store if a
        # hand edit or a restore ever blanked the key.
        voice_volume = st.slider("🔊 Her volume", 0, 100, key="voice_volume",
                                 value=int(_cs.get("voice_volume", 100)),
                                 help="Loudness only — her voice itself is always on "
                                      "(the switch that could silence her is retired). "
                                      "Drives the scanner's speaker and the phone-push audio.")
        if voice_volume != _cs.get("voice_volume", 100):
            _cs["voice_volume"] = int(voice_volume)
            _save_console_state(_cs)
    # THE STATUS LINE, FULL WIDTH (Sep 21 re-fit): this caption used to
    # live in the old third settings column — the long line forced the
    # row wide and pushed every section below it sideways on the phone.
    # Full width now: it wraps, and the sections render across the page.
    st.caption("🔔 AUTO-WATCH · 🎯 GOAL CALL · 🔊 HER VOICE — always on "
               "(critical to the system; the switches are retired)")
    interval_s = interval   # the widget stores SECONDS directly
    interval_ms = interval_s * 1000

    # check NOW if due — fires on every page change, popup follows you
    _light_check(kraken, cfg, sb, held, interval_s)
    _render_light_panel(f"{interval // 60} min")
    _render_her_words()
    _render_popup_history()
    _render_bots_book(kraken, cfg)
    _toast_new_her_words()        # her-words popup ticker: fast lane (15s) — GET OUT / TAKE IT
    # popups shouldn't wait for the 60s light cycle (user rule, Aug 30:
    # "so not as a pop up?")
    @st.fragment(run_every=15)
    def _her_words_tick():
        _toast_new_her_words()

    _her_words_tick()

    # VOICE-DRIVEN FRESHNESS (Sep 21): the watching section reads its
    # PnL and clocks only while a rerun runs — clicks or timers — so
    # it sat stale between them. She speaks every minute while trades
    # are tracked; a new voice_log line now triggers one full rerun
    # (the same event that moved the numbers) so the section shows
    # what she just said. The fragment's rerun is swallowed; the main
    # script's rerun re-renders everything.
    @st.fragment(run_every=15)
    def _voice_refresh_tick():
        try:
            import json as _json
            from pathlib import Path as _P2
            _vf = _P2(__file__).parent / "voice_log.jsonl"
            if not _vf.exists():
                return
            _vlines = [l for l in _vf.read_text(encoding="utf-8").splitlines()
                       if l.strip()]
            if not _vlines:
                return
            _vnew = str(_json.loads(_vlines[-1]).get("ts", ""))
            _vseen = st.session_state.get("voice_refresh_last_ts", "")
            if _vseen and _vnew > _vseen:
                st.session_state["voice_refresh_last_ts"] = _vnew
                st.rerun(scope="app")
            elif not _vseen:
                st.session_state["voice_refresh_last_ts"] = _vnew
        except (OSError, ValueError, KeyError) as _vexc:
            logger.debug("voice-refresh tick skipped: %s", _vexc)

    _voice_refresh_tick()


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
                            # (Sep 9) the trade lands on ITS OWN contest
                            # window's balance -- the watch record carries
                            # the contest_id it opened under; a foreign-
                            # window trade is logged but never moves the
                            # current balance (the TIA bug: an old
                            # contest's console sold it and the loss hit
                            # the new contest's account).
                            _applied, _new_bal, _note = _ca_s.apply_pnl(
                                _net, _sw.get("contest_id"),
                                dedupe_key=f"{_sp}|{_sw.get('added')}")
                            _held_min = ""
                            try:
                                from datetime import datetime as _dts
                                _added = _sw.get("added")
                                if _added:
                                    _held_min = f"{(_sold_at - _dts.fromisoformat(_added)).total_seconds() / 60.0:.0f}"
                            except (TypeError, ValueError) as exc:
                                logger.debug("held-minutes compute failed: %s", exc)
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
                                        "applied": bool(_applied),
                                        "note": ("" if _applied else _note),
                                        "goal_pct": _sw.get("goal_pct"),
                                        "held_min": _held_min,
                                    }) + "\n")
                            except Exception as exc:
                                logger.warning("sold record write failed — outcome not journaled: %s", exc)
                            _sign = "+" if _net >= 0 else "-"
                            _toast_sold = (f"✔ {_asset(_sp)} sold — {_sign}${abs(_net):,.2f} net "
                                           f"(0.08% RT cost incl.) · balance now ${_new_bal:,.0f}")
                            _say_sold = (f"{_asset(_sp)} sold! "
                                         f"{'Plus' if _net >= 0 else 'Minus'} {abs(_net):,.0f} dollars net! "
                                         f"The balance is now {_new_bal:,.0f} dollars! "
                                         f"Adjust me in the sidebar if that's off!")
                            if not _applied:
                                _toast_sold = (f"✔ {_asset(_sp)} sold — "
                                               f"{'+' if _net >= 0 else '-'}${abs(_net):,.2f} net · "
                                               f"NOT APPLIED: {_note or 'OLD-CONTEST trade'} "
                                               f"(balance untouched)")
                                _say_sold = (f"{_asset(_sp)} sold! That was an old contest trade -- "
                                             f"logged, but the current balance is untouched!")
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
                                except (AttributeError, ImportError, TypeError) as exc:
                                    logger.debug("re-arm probe failed: %s", exc)
                    except Exception as exc:
                        logger.debug("sold-record enrich skipped: %s", exc)
                    # Toast + voice fire on EVERY confirmed exit, healthy or
                    # enriched-skip alike — the enrichment's failure must
                    # never silence the confirmation of a closed position.
                    try:
                        st.toast(_toast_sold, icon="✔", duration=10)
                        _speak(_say_sold)
                    except Exception as exc:
                        logger.debug("sold toast/voice failed: %s", exc)
                    st.rerun()

    # ── the manual entry section retired here (user rule, Sep 21):
    # popups auto-track when BUY NOW fires (auto_track_popup — one
    # writer per route, and a BUY popup never fires on an asset
    # already watched); the watch auto-stops when GET OUT NOW fires
    # (the scanner's auto-close removes the row itself). SOLD stays:
    # the profit collect that clears the watch so the hunt can find
    # the next setup. The merged WATCHING rows live in HER WORDS.

    st.caption("Leave this tab open — the watch runs in your browser. "
               "It just watches and alerts. It can't touch your money.")

    # timer: keeps the checks coming while you sit still on one page
    @st.fragment(run_every=interval_s)  # seconds — ms was 16+ hours
    def _light_tick():
        if _light_check(kraken, cfg, sb, held, interval_s):
            try:
                st.rerun(scope="app")  # refresh the global panel
            except TypeError:
                st.rerun()

    _light_tick()


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
            except Exception as exc:
                logger.debug("live-price candle merge skipped: %s", exc)
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
    except Exception as exc:
        logger.debug("live entry lookup failed: %s", exc)
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
        except Exception as exc:
            logger.debug("exit-candle trailing high unavailable: %s", exc)
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
        except (TypeError, ValueError) as exc:
            logger.debug("watch-window minutes unknown: %s", exc)
    # STAY CLOSE (Sep 21 audit): window expiry is a stay-close MARKER,
    # never an exit trigger — the exit belongs to the trend/exit gates
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

    # (Sep 21 audit) NO _window_expired term: the clock never exits. The
    # window's end is display-only (STAY CLOSE) — the exit gates below
    # are all real data: trail, trend sell bar, exhaustion, rider, stop.
    should_exit = trail_hit or strategy_sell or exhaustion or rider_exit \
        or _hard_stop_hit
    reasons = []
    if trail_hit and high:
        reasons.append(f"down {(high-live)/high*100:.1f}% from the top")
    if strategy_sell:
        reasons.append(f"trend broke ({sig.confidence:.0f}% red)")
    if exhaustion:
        reasons.append("move's done")
    if rider_exit:
        reasons.append("out of steam")
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
                f"<span style='color:#4be38a;'>{_pct:+.1f}% = {_goal_mult}× the {_w_goal:.1f}% goal — STAY CLOSE, the exit is hers</span>")
        else:
            verdict_lines.append(
                f"GOAL <span class='dim'>...............</span> "
                f"{_pct:+.1f}% of the {_w_goal:.1f}% goal — {_goal_left:.1f}% to go")
        if _w_window and _elapsed_min is not None:
            # expired ⇒ _left_min <= 0 < 15, so one amber ternary covers both
            _win_col = "#ffc36b" if _left_min < 15 else "#4be38a"
            verdict_lines.append(
                f"WINDOW <span class='dim'>.............</span> "
                f"<span style='color:{_win_col};'>{_elapsed_min:.0f} of {_w_window:.0f} min — "
                + (f"PAST — STAY CLOSE (exit is hers)" if _window_expired else f"{_left_min:.0f} min left") + "</span>")

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
            f'<span style="color:#ffb347;">STAY CLOSE — {_goal_mult}× the goal pays '
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
    except Exception as exc:
        logger.debug("dip-history verdict unavailable: %s", exc)

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
        except Exception as exc:
            logger.debug("get-out toast/voice failed: %s", exc)
    else:
        # HOLD — she answers the real question: how long to the goal?
        # (when the goal is REACHED the answer is STAY CLOSE — the exit
        # is hers: the trend break, never the clock)
        try:
            if _goal_mult >= 1:
                _speak(f"Goal reached! {asset} is up {_pct:.1f} percent — "
                       f"{_goal_mult} times the goal! Stay close!")
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
        except Exception as exc:
            logger.debug("still-riding voice failed: %s", exc)


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def _on_tier_change(key: str):
    """(Sep 9) the tier select's on_change: a NEW CONTEST starts -- the
    only path that resets the balance to the tier's starting amount.
    Fires on USER INTERACTION only; a rerun (even from a stale second
    console) can never re-trigger it. The Sep 9 bug this kills: the old
    fixed session key let a forgotten console pin its '$10,000' and
    re-fire the new-contest reset on every rerun -- the 1k account kept
    snapping back to a full 10k (which also re-widened every sizing
    gate; TAO got buried too-thin under the phantom balance)."""
    try:
        _t = int(str(st.session_state.get(key, "")).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return
    if _t not in (1000, 5000, 10000):
        return
    import contest_account as _ca_t
    try:
        _ca_t.new_contest(_t)
    except RuntimeError:
        # the account file is busy (another writer holds it) -- say so
        # instead of swallowing (Sep 9: a silent drop shows a contest
        # the file never recorded; the user picks the tier again)
        try:
            st.toast("⏳ The account file is busy -- pick the tier again in a moment.",
                     icon="⏳", duration=8)
        except Exception as exc:
            logger.debug("tier-busy toast failed: %s", exc)
        return
    _cs_t = _load_console_state()
    _cs_t["tier"] = _ca_t.TIERS[_t]["label"]
    _save_console_state(_cs_t)
    try:
        st.toast(f"🆕 New {_ca_t.TIERS[_t]['label']} contest started -- "
                 f"balance ${_ca_t.TIERS[_t]['start']:,.0f}, fresh death "
                 f"line, fresh Hail Mary windows.", icon="🆕", duration=10)
        _speak(f"New contest started! Balance {_ca_t.TIERS[_t]['start']:,.0f} dollars!")
    except Exception as exc:
        logger.debug("new-contest toast/voice failed: %s", exc)


def _pace_target_clock_leds() -> str:
    """ON PACE / TARGET / CLOCK — three live gauges for the header strip
    (Sep 21, user ask). The SAME live data her 'all good' line reads:
    today's realized PnL vs the tier's daily pace (weekly 12% / 5 days),
    the +12% target progress from the live account balance, and the
    minutes left in the current rotation (the window her stay-close
    clock talks about). Never raises — the header cannot die for a
    gauge; unreadable data renders nothing rather than a lie."""
    out = ""
    try:
        import contest_account as _ca_p
        _acct = _ca_p.load_state()
        _bal = float(_acct.get("current_balance") or 0)
        _start = float(_acct.get("start_balance") or 0)
        _pace_day = _start * 0.12 / 5.0  # the weekly goal / 5 trading days
        _today = 0.0
        try:
            import json as _j2
            from pathlib import Path as _P5
            _day = _now_mst().strftime("%Y-%m-%d")
            for _l in (_P5(__file__).parent / "contest_trades.jsonl") \
                    .read_text(encoding="utf-8").splitlines():
                if not _l.strip():
                    continue
                _r = _j2.loads(_l)
                if str(_r.get("sold_at", "")).startswith(_day) \
                        and _r.get("applied"):
                    _today += float(_r.get("pnl_net") or 0.0)
        except Exception as exc:
            logger.debug("pace gauge just stays off — the header never lies: %s", exc)
        if _start:
            _pace_col = "g" if _today >= _pace_day else \
                ("a" if _today >= 0 else "r")
            out += led(f"ON PACE ${_today:+,.0f}/${_pace_day:,.0f}/day", _pace_col)
            _tpct = (_bal / _start - 1.0) * 100.0
            out += led(f"TARGET {_tpct:+.2f}% / 12%", "g" if _tpct > 0 else "a")
        try:
            import background_scanner as _bs_h
            if not hasattr(_bs_h, "ROTATIONS"):
                import importlib as _il
                _bs_h = _il.reload(_bs_h)
            _now = _now_mst()
            _win = next((r for r in _bs_h.ROTATIONS
                         if r[0] <= _now.hour < r[1]), None)
            if _win:
                from datetime import timedelta as _td
                _end = _now.replace(hour=_win[1] % 24, minute=0,
                                    second=0, microsecond=0)
                if _win[1] == 24:
                    _end += _td(days=1)
                _left = (_end - _now).total_seconds() / 60.0
                _rname = str(_win[3]).split()[0]  # 'R4' from 'R4 🌤 US Afternoon'
                out += led(f"CLOCK {_rname} {_left:.0f}m left",
                           "a" if _left < 15 else "off")
            else:
                out += led("CLOCK — cooldown hours", "off")
        except Exception as exc:
            logger.debug("clock gauge skipped: %s", exc)
    except Exception as exc:
        logger.debug("pace/clock led block skipped: %s", exc)
    return out


# ── THE BALANCE POP (user ask, Sep 22: 'make it a colorful display with
# real dopamine delivery when the sidebar is changed in slow motion so I
# can see it even when the Amount is small') — a −$6.92 ripples on a 10k
# book is invisible at a glance; when the balance moves, the sidebar now
# counts the old number down (red) or up (green) to the new one over
# ~2.8s, captioned with the trade that moved it, then settles into a
# small colored chip that stays until the next change.
_BALANCE_POP_CSS = """<style>
  .bp{border-radius:12px;padding:10px 14px;margin:2px 0 10px 0;
      font-family:'Source Sans Pro',sans-serif;
      animation:bpIn .8s cubic-bezier(.2,1.6,.35,1) both;}
  .bp-win{background:rgba(61,220,132,.10);border:1px solid rgba(61,220,132,.40);
          animation:bpIn .8s cubic-bezier(.2,1.6,.35,1) both,bpGlowW 1.6s ease-in-out .8s infinite alternate;}
  .bp-loss{background:rgba(255,92,92,.10);border:1px solid rgba(255,92,92,.40);
           animation:bpIn .8s cubic-bezier(.2,1.6,.35,1) both,bpGlowL 1.6s ease-in-out .8s infinite alternate;}
  @keyframes bpIn{0%{transform:scale(.82) translateY(-8px);opacity:0}
                  100%{transform:scale(1) translateY(0);opacity:1}}
  @keyframes bpGlowW{from{box-shadow:0 0 8px rgba(61,220,132,.25)}to{box-shadow:0 0 22px rgba(61,220,132,.55)}}
  @keyframes bpGlowL{from{box-shadow:0 0 8px rgba(255,92,92,.25)}to{box-shadow:0 0 22px rgba(255,92,92,.55)}}
  .bp-top{display:flex;align-items:center;gap:6px;font-size:.78rem;letter-spacing:.4px;
          text-transform:uppercase;color:#9aa7b8;margin-bottom:2px;}
  .bp-delta{font-size:1.55rem;font-weight:800;line-height:1.15;font-variant-numeric:tabular-nums;}
  .bp-win .bp-delta{color:#3ddc84}.bp-loss .bp-delta{color:#ff5c5c}
  .bp-cnt{font-size:1.02rem;font-weight:600;color:#e8edf4;font-variant-numeric:tabular-nums;}
  .bp-sub{font-size:.74rem;color:#9aa7b8;}
</style>"""
_BP_JS = """<script>
const _s=__OLD__, _e=__NEW__, _d=2800, _el=document.getElementById('bpn'), _t0=performance.now();
function _ease(t){return 1-Math.pow(1-t,3);}
function _f(n){var t=Math.min((n-_t0)/_d,1);
 _el.textContent=(_s+(_e-_s)*_ease(t)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
 if(t<1)requestAnimationFrame(_f);}
requestAnimationFrame(_f);
</script>"""


def _balance_pop_state(prev: float | None, cur: float,
                       applied: dict | None, now_ts: float) -> dict:
    """Pure state machine behind the sidebar's balance pop. Returns
    {'kind': 'seed'|'none'|'pop', 'old', 'new', 'delta', 'caption'}.
    First render seeds silently (no pop for history before the tab
    opened); a |delta| under half a cent is float noise, not a change;
    the caption names the trade that moved the balance when the newest
    applied trade's amount matches the delta and it is fresh (< 10 min) —
    'PUMP · GET OUT' — otherwise the change was your own edit: 'manual'.
    Fail-soft: any surprise in the applied map degrades to 'manual'."""
    if prev is None:
        return {"kind": "seed", "old": cur, "new": cur, "delta": 0.0, "caption": ""}
    delta = float(cur) - float(prev)
    if abs(delta) < 0.005:
        return {"kind": "none", "old": prev, "new": cur, "delta": 0.0, "caption": ""}
    caption = "manual"
    try:
        best_ts = best_pair = best_tag = None
        best_amt = None
        for key, amt in (applied or {}).items():
            parts = str(key).split("|")
            ts = parts[1] if len(parts) > 1 else ""
            if ts and (best_ts is None or ts > best_ts):
                best_ts = ts
                best_pair = parts[0]
                best_tag = parts[2] if len(parts) > 2 else ""
                best_amt = float(amt or 0)
        if best_ts and best_amt is not None and abs(best_amt - delta) < 0.02:
            import datetime as _dt
            try:
                t = _dt.datetime.fromisoformat(best_ts)
                fresh = abs(now_ts - t.timestamp()) < 600
            except Exception:
                fresh = False
            if fresh:
                _tag = str(best_tag or "SOLD").strip()
                _tag = "GET OUT" if _tag.lower() == "getout" else _tag.upper()
                caption = f"{str(best_pair).replace('/USD', '')} · {_tag}"
    except Exception:
        caption = "manual"
    return {"kind": "pop", "old": float(prev), "new": float(cur),
            "delta": delta, "caption": caption}


def main():
    cfg = TradingConfig()

    # ── sidebar: channel settings ────────────────────────────────────
    st.sidebar.markdown("### 🎛️ Console Settings")
    _cs = _load_console_state()
    _tiers = ["$1,000", "$5,000", "$10,000"]
    import contest_account as _ca
    _acct = _ca.load_state()
    # (Sep 9) the tier widget DISPLAYS the file's contest -- always. The
    # key carries the account's contest_id: a new window remounts the
    # widget fresh, every rerun shows the file, and the reset fires ONLY
    # on user interaction (on_change). (The old fixed key let a stale
    # console pin its "$10,000" and re-fire the new-contest reset every
    # rerun -- the 1k account kept snapping back to a phantom 10k.)
    _tier_key = f"challenge_tier_{_acct.get('contest_id') or 'legacy'}"
    # (Sep 9) the TIER select's key carries the window stamp: a new
    # contest remounts it fresh. The BALANCE input deliberately has NO
    # key -- see the number_input comment below.
    tier = st.sidebar.selectbox(
        "Contest account (the contest you're in)",
        _tiers,
        index=_tiers.index(_ca.TIERS[_acct["tier"]]["label"]),
        on_change=_on_tier_change, args=(_tier_key,),
        key=_tier_key,
        help="Changing this STARTS A NEW CONTEST at that tier (balance resets, "
             "fresh death line). It only fires when YOU change it -- never on a "
             "rerun, never from another open console.")
    # ── THE CONTEST ACCOUNT (Kraken Funded rules, encoded Sep 1): the
    # current balance is the sizing base; the -3% DEATH LINE never moves.
    _cur = st.sidebar.number_input(
        "Current contest balance ($)", min_value=0.0,
        value=float(_acct["current_balance"]), step=50.0,
        # (Sep 9, Copilot [high]) NO explicit key: Streamlit's auto
        # widget id includes the value parameter, so a file-side change
        # (a SOLD auto-applying P&L, or another console) remounts this
        # input showing the file's number -- an explicit key would pin
        # the stale value and the next rerun's compare-and-save would
        # silently UNDO the booked trade.
        help="Update as the contest account moves. The -3% death line and every "
             "position size are computed from this. SOLD trades of THIS contest "
             "adjust it automatically; old-contest trades never touch it.")
    if abs(_cur - float(_acct["current_balance"])) > 0.01:
        try:
            _acct = _ca.save_state(int(_acct["tier"]), _cur)
        except RuntimeError:
            # busy: the edit lands on the next honest rerun -- say so,
            # never crash the rerun, never pretend it saved
            try:
                st.toast("⏳ The account file is busy -- your balance edit will land in a moment.",
                         icon="⏳", duration=8)
            except Exception as exc:
                logger.debug("balance-busy toast failed: %s", exc)
    cfg.challenge_balance = float(_acct["current_balance"])
    # ── THE BALANCE, LIVE (Sep 22, the KAITO lesson): the +$0.77 from a
    # profit-guard close hit the FILE at 13:45 but the sidebar only
    # re-renders on a full rerun — on a phone (tab backgrounded, timers
    # throttled) it sat unseen until the next touch. The pop and the
    # account plate now ride a fragment that re-reads the account file
    # every 15s: the change arrives on its own, no touch required.
    def _balance_tick():
      try:
        import streamlit.components.v1 as _components
        _acct_l = _ca.load_state()
        _bp_cur = float(_acct_l["current_balance"])
        _bp = _balance_pop_state(st.session_state.get("_bal_pop_prev"), _bp_cur,
                                 _acct_l.get("applied_trades") or {}, time.time())
        st.session_state["_bal_pop_prev"] = _bp_cur
        if _bp["kind"] == "pop":
            st.session_state["_bal_pop"] = {**_bp, "born": time.time(), "rendered": False}
        _pop = st.session_state.get("_bal_pop")
        if _pop:
            _pwin = _pop["delta"] > 0
            _pcol = "#3ddc84" if _pwin else "#ff5c5c"
            _psign = "+" if _pwin else "−"
            if not _pop.get("rendered"):
                _pop["rendered"] = True
                _components.html(
                    _BALANCE_POP_CSS
                    + f'<div class="bp {"bp-win" if _pwin else "bp-loss"}">'
                      f'<div class="bp-top"><span>{"🟢" if _pwin else "🔴"}</span>'
                      f'<span class="bp-cap">{_pop["caption"]}</span></div>'
                      f'<div class="bp-delta">{_psign}${abs(_pop["delta"]):,.2f}</div>'
                      f'<div class="bp-cnt">$<span id="bpn">0.00</span> now</div>'
                      f'<div class="bp-sub">was ${_pop["old"]:,.2f}</div></div>'
                    + _BP_JS.replace("__OLD__", repr(_pop["old"]))
                           .replace("__NEW__", repr(_pop["new"])),
                    height=122, scrolling=False)
            else:
                st.sidebar.markdown(
                    f'<div style="border-left:3px solid {_pcol};background:{_pcol}14;'
                    f'border-radius:8px;padding:5px 10px;margin:-4px 0 10px 0;font-size:.86rem;">'
                    f'<b style="color:{_pcol}">{_psign}${abs(_pop["delta"]):,.2f}</b>'
                    f' · {_pop["caption"]} · now <b>${_pop["new"]:,.2f}</b></div>',
                    unsafe_allow_html=True)
      except Exception as exc:
        logger.debug("balance tick skipped: %s", exc)
        return   # (CodeRabbit) no _acct_l binding — never fall through to the plate
      _line = _ca.death_line(_acct_l)
      _buf = _ca.buffer(_acct_l)
      _pace = _ca.daily_pace_usd(_acct_l)
      _line_col = "r" if _buf <= 0 else ("a" if _buf < _ca.TIERS[_acct_l["tier"]]["start"] * 0.01 else "g")
      st.sidebar.markdown(plate("☠ CONTEST ACCOUNT", (
          led(f"BALANCE ${_bp_cur:,.2f}", _line_col) + "<br>" +
          led(f"DEATH LINE ${_line:,.0f}", _line_col) + "<br>" +
          led(f"BUFFER ${_buf:,.0f}", _line_col) + "<br>" +
          led(f"PACE ${_pace:,.0f}/DAY (12%/WK)", "a") + "<br>" +
          led(f"TARGET +12% = ${float(_acct_l['start_balance']) * 1.12:,.0f}", "g")
      )), unsafe_allow_html=True)
      # ── THE HAIL MARY WINDOW (user rule, Sep 4 — kept Sep 5; swept into
      #    the live fragment Sep 22): the emergency clock reads the SAME
      #    fresh account read as the plate — a frozen countdown was the
      #    same lie as a frozen balance.
      try:
          _hm_left = _ca.hail_mary_minutes_left(_acct_l)
          if _hm_left > 0:
              st.sidebar.markdown(plate("💪 HAIL MARY", (
                  led(f"WINDOW {max(_hm_left, 0):.0f} MIN LEFT", "a") + "<br>" +
                  led("THE EMERGENCY HOUR — ACCOUNT IN DANGER, CHAMPION SHOT", "a") + "<br>" +
                  led("THE DEATH LINE STANDS — NEVER YIELDS", "r") + "<br>" +
                  led(f"WINDOWS USED: {int(_acct_l.get('hail_mary_count') or 0)} · "
                      f"ONE PER ROTATION", "off")
              )), unsafe_allow_html=True)
      except Exception as exc:
          logger.debug("hail-mary plate failed: %s", exc)
    _balance_tick = st.fragment(run_every=15)(_balance_tick)
    _balance_tick()
    # (Sep 22 sweep) the HAIL MARY plate moved INSIDE the live fragment —
    # it renders in the same spot (right after the account plate), now on
    # the 15s re-read instead of waiting for a full rerun.
    # ── THE WORTH FLOOR — INTERNAL ONLY (user rule, Sep 10: "the worth
    #    Floor dials are for internal use only; stays hidden from console"):
    #    NO toggle, NO slider, NO live-math caption on the sidebar. The
    #    floor auto-tunes itself every alert — worth_floor_auto_factors
    #    (background_scanner, ONE source of truth) reads the engine's live
    #    rotation thresholds (the current rotation's pace), the fleet
    #    tide, and the account buffer; both popup gates read that same
    #    math LIVE via _worth_floor_frac(). The console_state keys stay
    #    as the internal override; nothing here renders or saves them.
    st.sidebar.caption("Worth floor: auto-tuned to the current market rotation (internal).")
    # ── THE ADVISORY BRAIN (user rules, Sep 5 — the entry-path rebuild):
    #    the brain's number SIZES every entry (the bucket table — ONE
    #    source: hindsight_scorer.brain_size_mult, imported by the scanner
    #    AND this console), it never blocks. The scan itself must run at
    #    fire-time (fail-closed — the JUP guarantee, kept).
    #    (Sep 9) THE POPUP CAP DIAL REMOVED — not part of the design: no
    #    money cap, no count cap. The BUFFER (the realized PnL above the
    #    death line) is the risk factor and it owns the worth floor.
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
    # (Sep 22 sweep) the snapshot count is a FILE READ — it used to freeze
    # between full reruns; it now rides its own 15s fragment at the same
    # spot, so a fresh popup or burial shows up without a touch.
    @st.fragment(run_every=15)
    def _brain_count_tick():
        _sn_n = 0
        try:
            _snap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "signal_snapshots.jsonl")
            with open(_snap_path, "r", encoding="utf-8") as _f:
                _sn_n = sum(1 for _ln in _f if _ln.strip())
        except OSError as exc:
            logger.debug("snapshot count unavailable: %s", exc)
        st.sidebar.caption(
            f"THE BRAIN SIZES, IT NEVER BLOCKS — the scan must run (fail-closed): "
            f"{_tbl}. Every popup and every burial lands in the snapshot dataset "
            f"({_sn_n} logged) — the hindsight scorer learns from both.")
    _brain_count_tick()
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
    except Exception as exc:
        logger.debug("freshness plate failed: %s", exc)

    st.sidebar.caption("Launch everything from the desktop shortcuts.\nConsole = Scout + Trader in one board.")

    # ── header nameplate ─────────────────────────────────────────────
    n = _now_mst()
    block, day, thr, interval, no_buy = current_threshold()
    vol = volatility_snapshot()
    # the header's LINE LED computes its own fresh account read (Sep 22:
    # the live balance fragment now owns _line/_buf/_line_col in its own
    # scope — the sweep stranded the header's references and booted a
    # NameError. A def-local read here; fail-soft to an OFF led if the
    # account file is busy, never a crash.
    try:
        _acct_h = _ca.load_state()
        _line = _ca.death_line(_acct_h)
        _buf = _ca.buffer(_acct_h)
        _line_col = ("r" if _buf <= 0
                     else ("a" if _buf < _ca.TIERS[_acct_h["tier"]]["start"] * 0.01 else "g"))
    except Exception as exc:
        logger.debug("header line read failed: %s", exc)
        _line, _buf, _line_col = 0.0, 0.0, "off"
    led_row = (led(f"{day} {block}", 'a') + led(f"BAR {thr}%", 'a') + led(f'SCAN/{interval}m', 'off')
               + led(f"☠ LINE ${_line:,.0f} · ${_buf:,.0f} LEFT", _line_col))
    if no_buy:
        led_row += led("COOLDOWN — NO BUYS", 'r')
    if vol:
        led_row += led(f"NOISE FLOOR {vol['thresholds']['standard']:.1f}%", 'a')
        led_row += led(f"VOL DATA {vol['data_weeks']:.1f}w", 'off')
    led_row += _pace_target_clock_leds()
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
            except Exception as exc:
                logger.debug("kill-switch toast/voice failed: %s", exc)
        else:
            try:
                _facts = _boot_facts_voice(block, no_buy, vol)
                st.toast("✅ ALL SYSTEMS GO — "
                         + (_facts.split(". ")[0] if _facts else "watcher armed"),
                         icon="✅", duration=15)
                _speak("Systems check. " + _facts)
            except Exception as exc:
                logger.debug("systems-go toast/voice failed: %s", exc)
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

    # (Sep 22, one brain) the global SELL banner is retired with the
    # console's own evaluator — it fired exit verdicts the scanner's exit
    # watch never called (the ghost-SELL class). GET OUT arrives only
    # from the scanner: its own popup, its own push.

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
            except Exception as exc:
                logger.debug("payout toast/voice failed: %s", exc)
            st.markdown(plate("", f'<div class="celebrate">{_p_banner}</div>'),
                        unsafe_allow_html=True)
    except Exception as exc:
        logger.debug("payout celebration failed: %s", exc)

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
