"""End-to-end audit after the 15:11 restart: every component alive and
running the new code, the HYPE position guarded, and the new systems'
first signs (trend-exit watch, near-miss, push filter)."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
print(f"AUDIT TIME: {now.strftime('%H:%M:%S')}  (restart was 15:11)")
print()

# ── 1. scanner alive: her words since the restart ────────────────────
vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
recent = [d for d in vl if str(d.get("ts", "")) >= "2026-08-31T15:11"]
print(f"1. SCANNER (voice_log): {len(recent)} lines since restart")
for d in recent[-6:]:
    print(f"   {str(d.get('ts',''))[11:16]}  {str(d.get('text',''))[:100]}")
print()

# ── 2. bot alive: the log since restart ──────────────────────────────
try:
    lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
                 encoding="utf-8", errors="replace").read().splitlines()
    recent = [l for l in lines if l[:16] >= "2026-08-31 15:11"]
    print(f"2. BOT (bot.log): {len(recent)} lines since restart — last 8:")
    for l in recent[-8:]:
        print(f"   {l[:130]}")
except Exception as e:
    print("2. BOT log read failed:", e)
print()

# ── 3. engine alive: thresholds regenerated ──────────────────────────
try:
    m = os.path.getmtime("rotation_thresholds.json")
    age = (now.timestamp() - m) / 60
    d = json.load(open("rotation_thresholds.json", encoding="utf-8"))
    print(f"3. ENGINE: rotation_thresholds.json regenerated "
          f"{age:.0f} min ago (bars: weekday R3={d['median']['weekday']['08-12']['confidence_bar']})")
except Exception as e:
    print("3. ENGINE check failed:", e)
print()

# ── 4. the bot's book: HYPE ─────────────────────────────────────────
import sqlite3
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pair, entry_price, execution_price, confidence, executed_at, "
    "closed_at, pnl, status FROM signals WHERE status='filled'"
).fetchall()
print(f"4. BOT'S BOOK (bridge filled, open): {len(rows)}")
for r in rows:
    print(f"   {r['pair']}  entry {r['execution_price']} (conf {r['confidence']}) "
          f"executed {str(r['executed_at'])[:16]}  closed: {r['closed_at']}")
conn.close()
print()

# ── 5. HYPE guard: the 1h knife right now ────────────────────────────
import background_scanner as bs
import ccxt


class _Cfg:
    ema_short = 9
    ema_long = 21


ex = ccxt.kraken()
rev, note = bs.knife_reversed_1h(ex, "HYPE/USD", _Cfg())
px = float(ex.fetch_ticker("HYPE/USD").get("last") or 0)
entry = 83.98
print(f"5. HYPE GUARD: last {px:.2f} vs bot entry {entry} "
      f"({(px/entry-1)*100:+.2f}%)  |  1h knife reversed: {rev}")
print(f"   {note or 'trend intact — riding'}")
print()

# ── 6. new-system signs ─────────────────────────────────────────────
try:
    bot_all = "\n".join(lines)
    print("6. NEW-SYSTEM SIGNS (bot log since 15:11):")
    for tag in ("TREND EXIT", "GET OUT NOW", "NEAR MISS", "TIDE STAMP",
                "depth unstable", "LIQUIDITY GATE"):
        n = bot_all.count(tag)
        print(f"   {tag}: {n}")
except Exception as e:
    print("6. sign scan failed:", e)
