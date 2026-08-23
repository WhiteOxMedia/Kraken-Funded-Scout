"""End-to-end: fetch real payouts + run the tracker (baseline run)."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    import keyring
    from kraken_api import KrakenAPI
    api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
    api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
    kr = KrakenAPI(api_key, api_secret)

    # import the app's helpers WITHOUT running streamlit app code
    import importlib.util
    # fetch_payouts/track_payouts are small — reimplement the exact filter
    # by exec'ing the source section
    src = (BASE / "app.py").read_text(encoding="utf-8")
    start = src.index("PAYOUT_TRACK_FILE = ")
    end = src.index("def _payout_date")
    import types, json
    from pathlib import Path as _P
    ns = {"st": types.SimpleNamespace(cache_data=lambda **k: (lambda f: f)),
          "Path": _P, "json": json, "__file__": str(BASE / "app.py")}
    exec("import logging\nlogger = logging.getLogger('t')\n" + src[start:end], ns)

    payouts = ns["fetch_payouts"](kr)
    lines.append(f"ledger payouts found: {len(payouts)}")
    for p in payouts:
        lines.append(f"  ${p['amount']:,.2f}  ts={p['ts']}  id={p['id']}")
    banner, all_p = ns["track_payouts"](payouts)
    lines.append(f"banner (baseline run → None expected): {banner}")
    lines.append(f"tracked payouts: {len(all_p)} · total ${sum(p['amount'] for p in all_p):,.2f}")
    # second run: no new → no banner
    b2, all2 = ns["track_payouts"](payouts)
    lines.append(f"second run banner (None expected): {b2}")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "payout_e2e_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
