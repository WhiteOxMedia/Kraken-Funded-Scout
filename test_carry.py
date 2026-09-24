"""Test the book-carry math: depth -> 20% carry -> contest-chunk snap-down.
Fail-open and the three verdicts."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from config import TradingConfig
import scratch_space   # scratch policy: nothing volatile lands in the repo root

CONTEST_CHUNKS = (0.25, 0.50, 0.75, 1.00)
BAL = 5000.0

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

def carry_verdict(depth):
    """Replicates the QC's carry math exactly."""
    if depth <= 0:
        return None, BAL  # fail-open
    carry = depth * 0.20
    if BAL <= carry:
        return carry, BAL          # all-in fine
    fit = [c * BAL for c in CONTEST_CHUNKS if c * BAL <= carry]
    return carry, (max(fit) if fit else 0.0)

# big book (BTC-class): $500k deep — all-in fine
c, s = carry_verdict(500_000)
check("BTC-class book ($500k) -> all-in $5,000", s == BAL and c == 100_000, f"carry={c} spend={s}")

# medium: $20k book -> carry $4,000 -> biggest chunk that fits = 75% ($3,750)
c, s = carry_verdict(20_000)
check("$20k book -> spend 75% = $3,750", s == 3750, f"spend={s}")

# smaller: $15k book -> carry $3,000 -> 50% ($2,500)
c, s = carry_verdict(15_000)
check("$15k book -> spend 50% = $2,500", s == 2500, f"spend={s}")

# thin: $8k book -> carry $1,600 -> 25% ($1,250)
c, s = carry_verdict(8_000)
check("$8k book -> spend 25% = $1,250", s == 1250, f"spend={s}")

# too thin: $5k book -> carry $1,000 -> even 25% ($1,250) doesn't fit -> 0
c, s = carry_verdict(5_000)
check("$5k book -> TOO THIN (even 25% exceeds the book) -> no green", s == 0, f"spend={s}")

# razor edge: carry exactly $1,250 -> 25% fits
c, s = carry_verdict(6_250)
check("carry exactly $1,250 -> 25% fits", s == 1250, f"spend={s}")

# unreadable book -> fail-open, full size
c, s = carry_verdict(0)
check("book unreadable -> fail-open full size", s == BAL and c is None)

fails = [r for r in results if not r[1]]
out = BASE / "carry_check.txt"
scratch_space.report_temp('carry_check.txt').write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
