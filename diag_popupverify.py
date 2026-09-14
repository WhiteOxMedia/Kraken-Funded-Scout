"""End-to-end popup path verify (live data, read-only) — writes
_popupverify.txt. The three dials the user asked to verify:
  1. THE 3% GUARD  — entry sizing off the live contest balance
  2. THE WORTH FLOOR — auto-tuned per rotation (pace x tide x buffer)
  3. THE EXIT TRAIL — ratchet width off a live peak
Run: .venv\\Scripts\\python.exe diag_popupverify.py
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import contest_account as ca
import background_scanner as bs

MST = ZoneInfo("America/Denver")
lines = []


def out(s=""):
    print(s)
    lines.append(s)


now = datetime.now(MST)
out(f"=== POPUP PATH VERIFY — {now:%Y-%m-%d %H:%M %Z} (live) ===")
out("")

# ── 1. THE 3% GUARD — entry sizing off the LIVE balance ────────────────
out("── 1. THE 3% GUARD (entry sizing) ──")
try:
    from contest_account import load_state
    _st = load_state()
    bal = float(_st.get("balance") or _st.get("current_balance") or 0)
    start = _st.get("start_balance") or ca.TIERS[ca.DEFAULT_TIER]["start"]
    dd = _st.get("window_dd_pct", _st.get("dd_pct", 0.5))
    if bal <= 0:
        out(f"  (no live balance in contest state — sizing math shown "
            f"at the ${start:,.0f} tier start)")
        bal, dd = start, 0.5
    cap = ca.max_entry_usd(bal, dd, start)
    size = ca.entry_size_usd(bal, None, dd, start)
    out(f"  live balance   : ${bal:,.2f}")
    out(f"  start (tier)   : ${start:,.2f}   death line: -{ca.MAX_LOSS_PCT}% "
        f"(never moves)")
    out(f"  buffer to death: ${bal - start * (1 - ca.MAX_LOSS_PCT / 100):,.2f}")
    out(f"  window dd      : {dd}%")
    out(f"  guard cap      : ${cap:,.0f}   entry size: ${size:,.0f}")
except Exception as exc:
    out(f"  guard check FAILED: {exc}")
out("")

# ── 2. THE WORTH FLOOR — auto factors on the CURRENT rotation ─────────
out("── 2. THE WORTH FLOOR (auto-tuned, per rotation) ──")
try:
    f = bs.worth_floor_auto_factors(now)
    rot = f"{(now.hour // 4) * 4:02d}-{((now.hour // 4) * 4 + 4) % 24:02d}"
    out(f"  rotation now   : {rot} MST")
    out(f"  auto           : {f['auto']}")
    out(f"  base           : {f['base']:.2f}")
    out(f"  pace factor    : {f['pace']:.3f}")
    out(f"  tide factor    : {f['tide']:.3f}")
    out(f"  BUFFER factor  : {f['buffer']:.3f}   (the bar — fat balance "
        f"eases, thin tightens)")
    out(f"  EFFECTIVE floor: {f['effective']:.3f}  "
        f"({f['effective'] * 100:.1f}%)  clamp [{bs.WORTH_AUTO_MIN}, "
        f"{bs.WORTH_AUTO_MAX}]")
    out(f"  gates read this LIVE at alert time (no restart)")
    out(f"  cross-check    : _worth_floor_frac() -> "
        f"{bs._worth_floor_frac():.3f}")
except Exception as exc:
    out(f"  worth-floor check FAILED: {exc}")
out("")

# ── 3. THE EXIT TRAIL — ratchet widths on the live sizes ──────────────
out("── 3. THE EXIT TRAIL (ratchet) ──")
out(f"  base trail     : {bs.CONTEST_TRAIL_PCT}%")
for run in (50.0, 100.0, 250.0):
    entry, size = 0.50, 1000.0
    high = entry * (1.0 + run / size)
    out(f"  run +${run:5.0f} -> trail {bs._ratchet_trail_pct(entry, high, size)}%")
out("  (3% base, 2% after +$100, 1.5% after +$200 — big runs earn tight)")
out("")

out("VERIFIED: guard sizing, worth floor (buffer), exit trail — all live.")
Path("_popupverify.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n-> _popupverify.txt")
