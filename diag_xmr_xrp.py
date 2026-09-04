"""Position mismatch audit (Sep 4): console BOT'S BOOK says XMR, the Kraken
account holds XRP. Dump the bridge records, the real wallet, and the actual
Kraken trades since the APT buy (Sep 2 17:48) so the roll can be traced."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
out = []

# ── 1. the bridge: open records + everything XMR/XRP/APT since Sep 2 ──
import signal_bridge as sb
out.append("=== BRIDGE OPEN POSITIONS ===")
try:
    for p in sb.get_open_positions():
        out.append(json.dumps(p, default=str))
except Exception as exc:
    out.append(f"read error: {exc}")

out.append("")
out.append("=== BRIDGE SIGNALS XMR/XRP/APT since Sep 2 ===")
import sqlite3
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
for r in conn.execute(
    "SELECT pair, action, confidence, source, status, timestamp, executed_at, "
    "execution_price, closed_at, pnl, execution_reason, highest_price "
    "FROM signals WHERE pair IN ('XMR/USD','XRP/USD','APT/USD') "
    "AND timestamp > '2026-09-02 17:00' ORDER BY rowid"
):
    out.append(f"{r['timestamp'][:19]} {r['pair']:<9} {r['action']} conf={r['confidence']} "
               f"src={r['source']} status={r['status']} "
               f"exec={r['executed_at'] or ''} px={r['execution_price'] or ''} "
               f"high={r['highest_price'] or ''} closed={r['closed_at'] or ''} "
               f"pnl={r['pnl']} reason={str(r['execution_reason'] or '')[:60]}")
conn.close()

# ── 2. the scanner's pending signals (unacked sells etc.) ─────────────
out.append("")
out.append("=== BRIDGE PENDING ===")
try:
    for s in sb.get_pending_signals() or []:
        out.append(json.dumps(s, default=str))
except Exception as exc:
    out.append(f"read error: {exc}")

# ── 3. reality: the Kraken wallet ─────────────────────────────────────
out.append("")
out.append("=== KRAKEN WALLET (reality) ===")
kr = None
try:
    try:
        from secrets_utils import setup_secrets
        setup_secrets()
    except Exception:
        pass
    import keyring
    from kraken_api import KrakenAPI
    k = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
    s = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
    if k and s:
        kr = KrakenAPI(k, s)
        bal = kr.exchange.fetch_balance()
        for cur, info in sorted(bal.get("total", {}).items()):
            amt = float(info) if not isinstance(info, dict) else float(info.get("total", 0))
            if amt > 0:
                out.append(f"{cur}: {amt:.8g}")
    else:
        out.append("no keyring creds")
except Exception as exc:
    out.append(f"wallet read error: {exc}")

# ── 4. what actually traded: recent Kraken fills ──────────────────────
out.append("")
out.append("=== KRAKEN TRADES since Sep 2 (XMR/XRP/APT/UNI/ASTER) ===")
if kr is not None:
    for pair in ("XMR/USD", "XRP/USD", "APT/USD", "UNI/USD", "ASTER/USD"):
        try:
            trades = kr.exchange.fetch_my_trades(pair, limit=20) or []
            for t in reversed(trades):  # oldest first
                ts = datetime.fromtimestamp(float(t["timestamp"] or 0) / 1000, MST)
                if ts < datetime(2026, 9, 2, 0, 0, tzinfo=MST):
                    continue
                out.append(f"{ts.strftime('%m-%d %H:%M:%S')} {pair:<9} "
                           f"{'BUY ' if t['side'] == 'buy' else 'SELL'} "
                           f"{float(t['amount'] or 0):.6g} @ {float(t['price'] or 0):.6g} "
                           f"fee={t.get('fee', {}).get('cost') if t.get('fee') else 0}")
        except Exception as exc:
            out.append(f"{pair}: trades read error: {exc}")
        time.sleep(0.3)

txt = "\n".join(out)
BASE.joinpath("xmr_xrp_audit.txt").write_text(txt, encoding="utf-8")
print("DONE -> xmr_xrp_audit.txt")
