"""Find non-ASCII bytes/mojibake in all .bat files (emoji that didn't convert)."""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
lines = []
for bat in sorted(BASE.glob("*.bat")):
    raw = bat.read_bytes()
    bad = []
    # decode as cp437 (how cmd.exe renders a plain-ASCII bat) and as utf-8
    for i, b in enumerate(raw):
        if b > 0x7F:
            ctx = raw[max(0, i - 30):i + 30]
            try:
                txt = ctx.decode("utf-8", errors="replace")
            except Exception:
                txt = repr(ctx)
            bad.append((i, b, txt))
    if bad:
        lines.append(f"{bat.name}: {len(bad)} non-ASCII byte(s)")
        for i, b, txt in bad[:10]:
            lines.append(f"  offset {i} byte 0x{b:02X}: ...{txt}...")
    else:
        lines.append(f"{bat.name}: clean")

out = BASE / "bat_scan_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("ok")
