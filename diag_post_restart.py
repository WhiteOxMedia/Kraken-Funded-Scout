"""Post-restart safety check (Sep 4 ~17:01): an XRP scout_trend_exit row
appeared at 17:01:09 with no position behind it. Verify no trade executed
after #490 (the 16:27 XRP sell) and the wallet is still flat."""
import os
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
out = []

url = (os.environ.get("DB195374BC_DATABASE_URL")
       or os.environ.get("DB1005AA9E_DATABASE_URL"))
if url:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, timestamp, pair, side, volume, price, reason "
                "FROM trades WHERE id > 490 ORDER BY id")).mappings().all()
            out.append(f"trades after #490: {len(rows)}")
            for r in rows:
                out.append(str(dict(r)))
    except Exception as exc:
        out.append(f"trades query error: {exc}")

try:
    from secrets_utils import setup_secrets
    try:
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
        out.append("wallet: " + ", ".join(
            f"{cur}={float(v if not isinstance(v, dict) else v.get('total', 0)):.6g}"
            for cur, v in sorted(bal.get("total", {}).items())
            if float(v if not isinstance(v, dict) else v.get("total", 0)) > 0))
except Exception as exc:
    out.append(f"wallet read error: {exc}")

txt = "\n".join(out)
BASE.joinpath("post_restart_check.txt").write_text(txt, encoding="utf-8")
print("DONE")
