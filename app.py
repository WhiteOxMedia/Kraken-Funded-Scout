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
        # NO DOWNWARD TREND PICKS (user directive, Aug 25) — same EMA knife
        # test as the QC, so the card never crowns what the QC would block.
        # Fail-open on data errors (the QC still gates).
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
        try:
            live = float(_ex.fetch_ticker(pair)["last"])
        except Exception:
            continue
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
# single engine refresh). The QC is a SCALP tool: the $20-pace gate
# (resolve rule) is the judge; conviction only needs a real setup (60%+).
# Desktop alerts keep their own 89% floor; the bot bridge keeps its
# per-asset bars — different games, different ledgers.
QC_SCALP_FLOOR = 60
# ONE BUY VOICE (user rule, Aug 31, sanctioned: "I only want brain buy
# signals for the voice" + "less entries but more money"): the card SHOWS
# the pick (the ⚡ plate + CHECK button) but does not SPEAK it. The
# champion alert — which fires only when the deep read clears 60 and the
# high bar (89 flat / 78 rising / 55 lone wolf) — is the only unsolicited
# buy voice. Flip to True to restore the card's voice.
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
            if not hasattr(_bsg, "_window_goal_pct"):
                import importlib
                _bsg = importlib.reload(_bsg)
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

    # TODAY'S FACTS — the system grades itself from real records (user
    # directive: MASTER is for self-correction on facts and figures —
    # signals fired, twenties banked, what the bot rejected — no guessing)
    st.markdown(plate("📐 TODAY'S FACTS — the system grades itself",
                      lcd(_today_facts())), unsafe_allow_html=True)

    # the bones — every tool and number still there, one click down
    with st.expander("📐 THE BONES — money score, breaker, integrity"):
        render_master_bones(cfg, kraken)


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
    with c2:
        clicked = st.button("🔍 DEEP SCAN", type="primary", width="stretch", key="qc_btn")
    with c3:
        st.markdown(f"<div style='height:26px'></div>"
                    f"<span class='led a'><i></i>{day} {block} · floor {QC_SCALP_FLOOR}%</span>",
                    unsafe_allow_html=True)

    # ── run the scan on click; PERSIST the result in session state so it
    # survives the WATCH-button rerun (buttons reset on every rerun)
    if clicked or _auto_scan:
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
                # THE SCALP GATE: hard gates = setup conviction >= 60 (the
                # 283-signal replay), $20 pace within the rotation's
                # allowance, never during cooldown, and NO DOWNWARD TREND
                # PICKS (user directive, Aug 25: the calibration showed hit-
                # rate parity for falling trends, but the user chooses stuck-
                # case protection over opportunity — their game, their rule).
                # Chase/just-paid are amber warnings — displayed, never
                # blocking (no hit-rate separation, and cheaper entries
                # aren't worse entries by the data).
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
                    if not hasattr(_bs, "_window_goal_pct"):
                        import importlib
                        _bs = importlib.reload(_bs)
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
                    st.session_state["qc_result"]["risk_cap"] = _risk_cap
                    st.session_state["qc_result"]["window_dd"] = _wdd
                    if _risk_cap <= 0:
                        _spend_usd = 0.0  # at/near the line — no new risk
                    else:
                        _spend_usd = min(_spend_usd, _risk_cap)
                except Exception as _exc:
                    logger.debug("3%% guard unavailable: %s", _exc)  # fail-open: book sizing stands
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
                # The QC's conviction floor itself stays 60 — that IS the
                # match: every lone-wolf alert only fires when the DEEP
                # read clears 60 and it SPEAKS the deep number, so the QC's
                # deep floor is the same line the alert already passed.
                # What was missing is the CONTEXT — this makes the QC say
                # what the alert would say, and name a near-miss instead of
                # a bare "weak setup".
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
                # the champion floor, same as the alert path (89 flat tide /
                # 78 rising / 55 lone wolf). Below that: SKIP — thin book,
                # brain not exceptional. POPCAT (74% on a thin book) would
                # have been skipped; ARB passes on the lone-wolf floor.
                try:
                    _trdf = float(getattr(_bs5, "TIDE_RISING_DESKTOP_FLOOR", 78.0))
                    _lwdf = float(getattr(_bs5, "LONE_WOLF_DESKTOP_FLOOR", 55.0))
                except Exception:
                    _trdf, _lwdf = 78.0, 55.0
                _champ_floor = (_trdf if _tide_state == "RISING" else 89.0)
                if _lw_pts:
                    _champ_floor = max(_lwdf, _champ_floor - _lw_pts)
                _thin_book = (0 < _spend_usd < float(cfg.challenge_balance))
                st.session_state["qc_result"]["thin_book"] = _thin_book
                st.session_state["qc_result"]["champ_floor"] = _champ_floor
                # HARD GATES: setup + the goal gate + cooldown + no downward
                # trend + the book carries your money + the tide isn't OUT +
                # rotation isn't locked + this hour didn't blow off yesterday
                # (+ the thin book needs an exceptional brain read).
                if (sig.action == "buy" and not no_buy
                        and sig.confidence >= QC_SCALP_FLOOR
                        and _trend != "FALLING"
                        and _tide_state != "OUT"
                        and _spend_usd > 0
                        and _rot_ok
                        and not (_yctx and _yctx.get("shape") == "blowoff")
                        and not (_raw_move is not None and float(_raw_move) < 1.0)
                        and (not _thin_book or sig.confidence >= _champ_floor)):
                    # AUTO-WATCH on green light: the deep scan IS the entry.
                    # The user's system: green → all in, scalp to +$20, out,
                    # re-enter small, green again → buy more. The BOOK CARRY
                    # rule may size the spend down — the watch records the
                    # RECOMMENDED size so the $-counting matches what they
                    # actually hold.
                    _chunk = (round(_spend_usd / max(cfg.challenge_balance, 1), 2), _spend_usd)
                    if _spend_usd >= float(cfg.challenge_balance):
                        _chunk = (1.00, float(cfg.challenge_balance))
                        _chunk_txt = f"buy 100% (${_spend_usd:,.0f}). "
                    else:
                        _chunk_txt = (f"buy {_chunk[0]:.0%} (${_spend_usd:,.0f}) — "
                                      f"this book carries ${_spend_usd:,.0f}, not all "
                                      f"${cfg.challenge_balance:,.0f}. ")
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
                                       "window_min": float(max(60.0, min(180.0, (_goal_pct or 1.5) * 60.0)))}
                            _save_watch(w)
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
                                    # THIN-BOOK BRAIN RULE: this GO only fired
                                    # because the brain cleared the champion
                                    # floor — say the number so the sizing
                                    # decision is informed
                                    _speak(f"Go! {asset}! Buy {_pct_txt} — the book is thin! "
                                           f"The brain says {sig.confidence:.0f} percent! "
                                           f"{_fine_say}Aim for plus {_goal_pct:.0f} percent!", rate=1)
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
                            and sig.confidence >= QC_SCALP_FLOOR
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
                            and sig.confidence >= QC_SCALP_FLOOR
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
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _tide_state == "OUT"):
                        try:
                            st.toast(f"🌊 TIDE'S OUT — only {_tide_pct:.0f}% of the market is rising. "
                                     f"All ships sink together — don't enter anything right now. "
                                     f"Wait for the tide to turn.",
                                     icon="🌊", duration=20)
                            _play_sound("alarm")
                            _speak(f"The tide is out! All ships sink together! Wait!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and sig.confidence >= QC_SCALP_FLOOR
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
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _risk_cap is not None and _risk_cap <= 0):
                        # THE DEATH LINE (Kraken Funded: -3% from start, never
                        # moves): the buffer is gone — any new trade can close
                        # the account. No new risk, full stop.
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
                            and sig.confidence >= QC_SCALP_FLOOR
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
                            and sig.confidence >= QC_SCALP_FLOOR
                            and _thin_book
                            and sig.confidence < _champ_floor):
                        # THIN-BOOK BRAIN RULE (user rule, Aug 31): the book
                        # can't carry full size AND the brain doesn't call it
                        # exceptional — skip. Less entries, bigger margins.
                        try:
                            st.toast(f"⛔ THIN BOOK, BRAIN NOT EXCEPTIONAL — {asset}: the book "
                                     f"carries ${_spend_usd:,.0f}, not your full "
                                     f"${cfg.challenge_balance:,.0f}, and the brain reads "
                                     f"{sig.confidence:.0f}% (needs {_champ_floor:.0f}% for a "
                                     f"thin book). Skip it — wait for the big-coin calls.",
                                     icon="⛔", duration=20)
                            _play_sound("alarm")
                            _speak(f"Skip {asset}! Thin book — the brain says "
                                   f"{sig.confidence:.0f} percent, not exceptional!", rate=1)
                        except Exception:
                            pass
                    elif (sig.action == "buy" and not no_buy
                            and sig.confidence >= QC_SCALP_FLOOR
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
                        if sig.action == "buy" and _lw_pts:
                            # lone-wolf near miss (user rule, Aug 31: don't
                            # keep her in the dark) — name the shape, not
                            # just the number
                            act_txt = (f"{sig.confidence:.0f}% — needs {QC_SCALP_FLOOR}% — "
                                       f"LONE WOLF NEAR MISS ({_lw_note})")
                            _speak(f"{asset}! Lone wolf near miss! {_lw_note}! "
                                   f"The brain says {sig.confidence:.0f} percent — it needs {QC_SCALP_FLOOR}!", rate=1)
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
    trend = result.get("trend", "UP")
    carry_usd = result.get("carry_usd")
    spend_usd = result.get("spend_usd") or cfg.challenge_balance
    tide_state = result.get("tide")
    # THE GOAL (Sep 1): the coin's raw expected move must reach the 1%
    # goal floor. The $20-pace check retired with the scalp game.
    goal_pct = float(result.get("goal_pct", 1.5) or 1.5)
    raw_move = result.get("raw_move")
    goal_ok = raw_move is None or float(raw_move) >= 1.0
    # HARD gates: setup floor + pace + no downward trend + book carries your
    # size + tide isn't OUT + rotation not locked + this hour didn't blow off
    # yesterday (+ cooldown in the handler). Chase/payout warn.
    rot_ok = result.get("rotation_ok", True)
    rot_reason = result.get("rotation_reason", "")
    is_reentry = result.get("reentry", False)
    fine_usd = result.get("fine", 0.0)
    yesterday = result.get("yesterday") or {}
    # LONE WOLF (user rule, Aug 31): the same context the alert funnel uses —
    # a coin moving on her own in a FLAT tide. The QC floor stays 60 (that's
    # the line every alert already passed on the deep read); the LED and the
    # near-miss verdict make the verdict MATCH instead of leaving her in the
    # dark about WHY the system likes this shape.
    lw_pts = result.get("lone_wolf_pts", 0) or 0
    lw_note = result.get("lone_wolf_note", "") or ""
    yshape = yesterday.get("shape")
    thin_book = bool(result.get("thin_book"))
    champ_floor = float(result.get("champ_floor", 89.0) or 89.0)
    passes = (sig.action == "buy" and not no_buy
              and sig.confidence >= QC_SCALP_FLOOR and goal_ok
              and trend != "FALLING"
              and tide_state != "OUT"
              and (result.get("spend_usd", 1) or 0) > 0
              and rot_ok
              and yshape != "blowoff"
              and (not thin_book or sig.confidence >= champ_floor))
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
    _green_txt = ("SCALP GREEN LIGHT" if passes else
                  ("COOLDOWN — NO BUYS" if no_buy else
                   ("NO GREEN — ALREADY RAN THIS ROTATION" if not rot_ok else
                    ("NO GREEN — THIS HOUR DUMPED YESTERDAY" if yshape == "blowoff" else
                     ("NO GREEN — TIDE'S OUT" if tide_state == "OUT" else
                      ("NO GREEN — TOO THIN" if (result.get("spend_usd", 1) or 0) <= 0 else
                       (f"NO GREEN — AT THE DEATH LINE (buffer gone — any trade can close the contest)"
                        if (result.get("risk_cap", 1) or 0) <= 0 else
                       (f"NO GREEN — THIN BOOK, BRAIN NOT EXCEPTIONAL ({sig.confidence:.0f}% vs the "
                        f"{champ_floor:.0f}% thin-book floor) — wait for the big-coin calls"
                        if thin_book and sig.confidence < champ_floor else
                       ("NO GREEN — DOWNWARD TREND" if trend == "FALLING" else
                        (f"NO GREEN — TOO SLOW (engine expects only {float(raw_move):.1f}% in this window; the goal needs 1%+)"
                         if not goal_ok else
                         (f"NO GREEN — LONE WOLF NEAR MISS {sig.confidence:.0f}% VS {QC_SCALP_FLOOR}% — the alert floor is the same 60 on the deep read"
                          if lw_pts else f"NO GREEN — NEEDS {QC_SCALP_FLOOR}%+")))))))))))
    if carry_usd is not None:
        if spend_usd >= float(cfg.challenge_balance):
            _carry_txt, _carry_col = "BOOK CARRIES ALL-IN", "g"
        elif spend_usd > 0:
            _carry_txt, _carry_col = (f"⚠ THIN BOOK — SPEND ${spend_usd:,.0f} MAX "
                                      f"(the brain needs ≥{champ_floor:.0f}% here)", "a")
        else:
            _carry_txt, _carry_col = "TOO THIN — YOUR MONEY IS THE MARKET", "r"
    else:
        _carry_txt, _carry_col = "BOOK UNREADABLE — SPEND YOUR CALL", "a"
    _tcol2 = {"RISING": "g", "FLAT": "a", "OUT": "r"}.get(tide_state, "a")
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
    GET OUT NOW. 🔴 REVERSED — GET OUT SENT shows while a scout_trend_exit
    sell pends. Console-side: browser F5 loads changes, no restart."""
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
            _px = 0.0
            try:
                _px = float((kraken.fetch_ticker(_pair) or {}).get("last") or 0.0)
            except Exception:
                _px = 0.0
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
    lines = [f"<span class='dim'>{_now_mst().strftime('%I:%M %p')} · every {interval} · green = hold, red = sell</span>"]
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
            # THE CLOCK (user directive: green must say how long to hold):
            # out-by from the entry + 2x this coin's usual $20 time; stay
            # close when a take is near or the clock is short.
            try:
                from background_scanner import _hours_to_scalp
                _eta = _hours_to_scalp(v["pair"], entry) or 0.75
                _added = datetime.fromisoformat(rec["added"]) if rec.get("added") else _now_mst()
                _out_by = _added + timedelta(minutes=max(2.0 * _eta * 60.0, 45.0))
                _rem = (_out_by - _now_mst()).total_seconds() / 60.0
                _next20 = ((int(usd // 20) + 1) * 20 - usd) if usd > 0 else (20 - usd)
                _near = (_next20 <= 10) or (_rem <= 15.0)
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


def _render_reenter_control():
    """'I RE-ENTERED' — the contest app shows what you SPENT and how much
    you OWN, never the price you paid (user reality-check, Aug 26: "I know
    how much I own, the amount of my trade, and the current PnL — you have
    to use that"). Take the two numbers they DO have and do the math:
    paid = spent ÷ owned. Resets the watch entry, size, peak and clock to
    the REAL position so her stop, trail and twenties count from truth.
    Aug 30: wrapped in st.form — the light's 60s reruns were dimming the
    page and interrupting mid-typing; a form batches everything and
    submits once, when you're done."""
    cw = _load_watch()
    if not cw:
        return
    with st.expander("🔁 I RE-ENTERED — tell her what you actually paid"):
        with st.form("reenter_form", clear_on_submit=False):
            pair = (st.selectbox("which coin", list(cw.keys()), format_func=_asset,
                                 key="re_pair") if len(cw) > 1 else list(cw.keys())[0])
            c1, c2 = st.columns(2)
            with c1:
                spent = st.number_input("$ you spent", min_value=0.0, value=None,
                                        step=100.0, key="re_spent",
                                        help="the contest app shows this: the amount of your trade")
            with c2:
                owned = st.number_input("coins you own", min_value=0.0, value=None,
                                        step=0.0001, key="re_owned",
                                        help="the contest app shows this: how much you own")
            if st.form_submit_button("✎ SET MY ENTRY — she counts from reality now",
                                     type="primary"):
                if spent and owned and float(owned) > 0:
                    entry = float(spent) / float(owned)
                    w = _load_watch()
                    w[pair] = {"entry": entry, "high": entry,
                               "size": float(spent),
                               "added": _now_mst().isoformat()}
                    _save_watch(w)
                    st.toast(f"✅ {_asset(pair)}: you paid ${entry:.4f} per coin "
                             f"(${float(spent):,.0f} ÷ {float(owned):,.4f}) — "
                             f"her stop, trail and twenties count from reality now",
                             icon="✅", duration=15)
                    _speak(f"{_asset(pair)} entry set! She counts from reality now!")
                    try:
                        st.rerun(scope="app")
                    except TypeError:
                        st.rerun()
                else:
                    st.error("Need both numbers — they're both on your position screen "
                             "in the contest app: the $ of the trade and the coins you own.")


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
        goal_on = st.toggle("🎯 GOAL CALL — the % goal game",
                            value=st.session_state.get("goal_on",
                                                       _cs.get("goal_on",
                                                               _cs.get("scalp_on", True))),
                            key="goal_toggle",
                            help="THE RULES OF THE GAME (Sep 1 rebuild — the $20 counting is "
                                 "gone): every entry carries its own PERCENTAGE goal, set by "
                                 "the market analysis — the Volatility Engine's expected move "
                                 "for THIS coin, in THIS rotation, on THIS day of the week — "
                                 "always clamped between 1% and 3%. The trade runs on a 1-3 "
                                 "hour window scaled to the goal (1% → ~1h, 3% → ~3h). She "
                                 "calls 'Goal reached — take the profit!' when it pays, and "
                                 "'N times the goal!' at each doubling. If it hasn't paid by "
                                 "the out-by time — get out. Entry size is capped by the 3% "
                                 "death-line guard (the contest survives three worst-cases), "
                                 "so no single trade ever risks the account.")
        if goal_on != _cs.get("goal_on", _cs.get("scalp_on", True)):
            _cs["goal_on"] = goal_on
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
                        _cw_sold.pop(_sp)
                        _save_watch(_cw_sold)
                        # the re-entered expander follows right below
                        st.session_state.get("take_level", {}).pop(_sp, None)
                        # flow advance moves here (Aug 30): SOLD is now the
                        # only exit — the board clears when YOU clear it
                        st.session_state["flow_exit_at"] = time.time()
                        try:
                            st.toast(f"✔ {_asset(_sp)} cleared — next green light re-adds it",
                                     icon="✔", duration=8)
                            _speak(f"{_asset(_sp)} sold! Cleared. Ready for the next one!")
                        except Exception:
                            pass
                        st.rerun()

        # ── contest watch list: live positions only, no history ────────
        # the re-entered control: right after the SOLD buttons — the loop
        # is sold → re-entered → sold, and it lives on every page
        _render_reenter_control()

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
            _chunk_opts = {"25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.00}
            with st.form("watch_add_form", clear_on_submit=False):
                f0, f1 = st.columns([1, 2])
                new_chunk = f0.selectbox("Size", list(_chunk_opts), index=1, key="watch_add_size",
                                         help="How much you bought in the contest app — sets the $ size "
                                              "for P&L and scalp alerts")
                new_asset = f1.selectbox("Asset", ASSETS, key="watch_add_asset",
                                         help="She tracks it from the LIVE price — if you paid "
                                              "something else, tell her in I RE-ENTERED right after")
                if st.form_submit_button("◆ ADD", type="primary"):
                    npair = f"{new_asset}/USD"
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
                            st.toast(f"◆ {new_asset} added — entry {_fmt_price(live)} "
                                     f"(live price). Paid something else? I RE-ENTERED, right below.",
                                     icon="✅", duration=12)
                            _speak(f"{new_asset} added to the watch!")
                        except Exception:
                            pass
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
            "**One card runs the whole thing — follow it.**\n\n"
            "**The loop:**\n"
            "1. **THE CARD (MASTER tab)** — shows the best fresh pick: WHY it's a pick, WHEN it "
            "was spotted, and HOW LONG to hold. One click checks it.\n"
            "2. **The check runs itself** — the deep scan fires the moment you arrive. Green "
            "light = enter the trade on the contest app (it tells you exactly how much).\n"
            "3. **The light** — she checks your trade every minute. Counts your $20s out loud, "
            "tells you to stay close or walk around, and yells GET OUT NOW when it's time.\n"
            "4. **Sold?** Tap the SOLD button. Re-entered on the app? Tell her what you paid "
            "with the I RE-ENTERED panel — she needs the two numbers the contest shows you.\n\n"
            "- **TOURNAMENT** — your backup picker: run the same hunt yourself, any time.\n"
            "- **EXIT CHECK** — 'should I sell?' on any coin, with that coin's real history.\n\n"
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
