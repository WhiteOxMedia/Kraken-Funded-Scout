"""Current bot state: positions, bridge, buying power — why only 1 asset?"""
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BOT))

lines = [f"now: {datetime.now().strftime('%a %I:%M %p')}"]

# bot positions
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, pair, entry_price, entry_volume, entry_timestamp, is_open "
            "FROM positions WHERE is_open = true")).fetchall()
        lines.append(f"\nbot open positions: {len(rows)}")
        for r in rows:
            lines.append(f"  #{r[0]} {r[1]} {r[2]} vol={r[3]} opened {str(r[4])[:16]}")
        rows = conn.execute(text(
            "SELECT side, pair, price, timestamp FROM trades "
            "WHERE timestamp >= '2026-08-24 14:00' ORDER BY timestamp DESC LIMIT 10")).fetchall()
        lines.append(f"recent trades since 8 AM MST: {len(rows)}")
        for r in rows:
            lines.append(f"  {str(r[3])[:16]} {r[0]:<4} {r[1]} @ {r[2]}")
except Exception as exc:
    lines.append(f"bot DB: {exc}")

# bridge
try:
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence FROM signals "
        "WHERE timestamp >= '2026-08-24T08:45' ORDER BY timestamp DESC LIMIT 15").fetchall()
    lines.append(f"\nbridge since 8:45 AM ({len(rows)}):")
    for r in rows:
        lines.append(f"  {r['timestamp'][:19]} {r['pair']:<12} {r['action']:<4} {r['status']:<9} "
                     f"{r['source']:<12} {r['confidence']}%")
    conn.close()
except Exception as exc:
    lines.append(f"bridge: {exc}")

# wallet / buying power
try:
    import keyring
    from kraken_api import KrakenAPI
    kr = KrakenAPI(keyring.get_password("workshop", "KRAKEN_API_KEY") or "",
                   keyring.get_password("workshop", "KRAKEN_API_SECRET") or "")
    usd = kr.get_available_quote("USD")
    total = 0.0
    for k, v in kr.fetch_balance().items():
        try:
            amt = float(v.get("total") or 0) if isinstance(v, dict) else float(v or 0)
            if amt > 0 and k != "USD":
                total += amt  # rough
        except Exception:
            pass
    lines.append(f"\nwallet: ${usd:.2f} USD free")
except Exception as exc:
    lines.append(f"wallet: {exc}")

out = BASE / "one_asset.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
