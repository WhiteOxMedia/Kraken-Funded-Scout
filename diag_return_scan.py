"""Scan send_alert's pre-desktop returns (the suppression-log coverage
audit): every bare `return` between the def and the desktop stage, with
the nearest logger line, so no suppression can vanish unlogged."""
from pathlib import Path

src = Path("background_scanner.py").read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(src) if "def send_alert" in l)
out = []
for i in range(start, min(start + 420, len(src))):
    s = src[i].strip()
    if s == "return":
        # walk back for context
        ctx = []
        for j in range(i - 1, max(i - 8, start), -1):
            cj = src[j].strip()
            if cj and not cj.startswith("#"):
                ctx.append(cj[:110])
            if len(ctx) >= 2:
                break
        out.append(f"line {i+1}: return   <- " + " | ".join(reversed(ctx)))
print("\n".join(out) if out else "no bare returns in range")
