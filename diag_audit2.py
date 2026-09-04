"""Full audit of the REBUILD (post-14:27 restart): every old-popup vector
gated, every new system live, the bot guarded. READ-ONLY."""
import json
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
print(f"AUDIT TIME: {now.strftime('%H:%M:%S')} (restart was 14:27)")
print()

# ── 1. the contest account: the platform rules live ───────────────────
import contest_account as ca
s = ca.load_state()
print(f"1. CONTEST ACCOUNT: tier ${s['tier']:,}  balance ${s['current_balance']:,.2f}")
print(f"   death line ${ca.death_line(s):,.2f}  buffer ${ca.buffer(s):,.2f}  "
      f"pace ${ca.daily_pace_usd(s):,.2f}/day")
cap2 = ca.max_entry_usd(s["current_balance"], 2.0, s["start_balance"])
cap3 = ca.max_entry_usd(s["current_balance"], 3.0, s["start_balance"])
print(f"   guard: max entry ${cap2:,.0f} (2% dd coin) / ${cap3:,.0f} (3% dd coin)")
print(f"   buffer gone -> ${ca.max_entry_usd(9700.0, 2.0, 10000.0):,.0f} (no entries)")
print()

# ── 2. the goal clock + the stale guard (live reads) ─────────────────
import background_scanner as bs
import ccxt
ex = ccxt.kraken()
for p in ("BTC/USD", "HYPE/USD", "UNI/USD"):
    g = bs._window_goal_pct(p)
    dd = ca.window_drawdown_pct(p)
    print(f"2. GOAL: {p:<9} goal {g:.1f}%  window {max(60, min(180, g*60)):.0f}min  "
          f"profiled window-drawdown {dd:.1f}%")
st = bs._stale_move(ex, "APT/USD")
print(f"   stale-move check APT: {st if st else 'fresh (or fail-open)'}")
print()

# ── 3. no old popups since restart: her words ────────────────────────
vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
recent = [d for d in vl if str(d.get("ts", "")) >= "2026-09-01T14:27"]
print(f"3. HER WORDS since restart: {len(recent)}")
for d in recent[-8:]:
    print(f"   {str(d.get('ts',''))[11:16]}  {str(d.get('text',''))[:105]}")
old_vectors = ("Take it!", "twenty dollars", "The twenty comes in", "dollars up!")
bad = [d for d in recent if any(v in str(d.get("text", "")) for v in old_vectors)]
print(f"   OLD-PHRASE popups since restart: {len(bad)} (must be 0)")
print()

# ── 4. bridge + bot since restart ────────────────────────────────────
import sqlite3
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pair, timestamp, action, confidence, status, source FROM signals "
    "WHERE timestamp >= '2026-09-01T14:27' ORDER BY timestamp"
).fetchall()
print(f"4. BRIDGE since restart: {len(rows)} signals")
for r in rows:
    d = dict(r)
    print(f"   {d['timestamp'][:16]}  {d['pair']:<10} {d['action']} conf {d['confidence']} "
          f"src {d['source']} -> {d['status']}")
open_pos = conn.execute(
    "SELECT pair, execution_price, confidence, executed_at FROM signals "
    "WHERE status='filled'").fetchall()
print(f"   BOT'S BOOK (open): {len(open_pos)}")
for r in open_pos:
    print(f"   {dict(r)['pair']} @ {dict(r)['execution_price']} "
          f"(conf {dict(r)['confidence']}, since {str(dict(r)['executed_at'])[:16]})")
conn.close()
print()

# ── 5. the bot log since restart (errors?) ───────────────────────────
lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
             encoding="utf-8", errors="replace").read().splitlines()
post = [l for l in lines if l[:16] >= "2026-09-01 14:27"]
errs = [l for l in post if "ERROR" in l or "WARN" in l.upper()[:40]]
print(f"5. BOT since restart: {len(post)} lines, {len(errs)} errors/warnings")
for l in post[-5:]:
    print(f"   {l[:120]}")
print()

# ── 6. the UNI guard: the 1h knife on the held position ──────────────
class _Cfg:
    ema_short = 9
    ema_long = 21


if open_pos:
    held = dict(open_pos[0])["pair"]
    rev, note = bs.knife_reversed_1h(ex, held, _Cfg)
    px = float(ex.fetch_ticker(held).get("last") or 0)
    ent = float(dict(open_pos[0])["execution_price"])
    print(f"6. THE HELD POSITION GUARD: {held} last {px:.4f} vs entry {ent:.4f} "
          f"({(px/ent-1)*100:+.2f}%)  |  1h knife reversed: {rev}")
    print(f"   {note or 'trend intact — riding'}")
