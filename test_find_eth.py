"""Search the scanner output files for the recent 99% ETH alert details."""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
lines = []

# search all recent workshop output files for ETH mentions
import os
temp_roots = [Path(r"C:\Users\birds\AppData\Local\Temp")]
for root in temp_roots:
    for d in root.glob("workshop_*"):
        for f in d.rglob("*.txt"):
            try:
                if f.stat().st_size > 5_000_000:
                    continue
                content = f.read_text(errors="ignore")
                # find ETH alert blocks
                for m in re.finditer(r"(BIG ALERT.*ETH.*|ETH.*(?:89|99|9\d)%.*|OVERALL CHAMPION.*ETH.*|MOMENTUM CHAMPION.*ETH.*|ETH.*CHALLENGE.*)", content):
                    snippet = m.group(0)[:200]
                    lines.append(f"{f.name}: {snippet}")
            except Exception:
                continue

lines = lines[-30:] if lines else ["no ETH alert mentions found in captured logs"]
out = BASE / "eth_alert_found.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
