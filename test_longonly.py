"""Verify long-only tournament rules: buys beat sells in every path."""
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from scanner import _compare_signals, ScanResult
    from strategy import Signal

    def res(action, conf, rr=2.0, pair="AAA/USD"):
        return ScanResult(pair=pair, signal=Signal(action=action, confidence=conf, price=100,
                                                   risk_reward=rr, reasons=["test"]))

    # 1. Low-confidence buy beats high-confidence sell (long-only mandate)
    w, l, reason = _compare_signals(res("buy", 55), res("sell", 95, pair="BBB/USD"))
    ok1 = w.pair == "AAA/USD"
    lines.append(f"buy55 vs sell95 → winner={w.pair} ({'PASS' if ok1 else 'FAIL'}): {reason}")

    # 2. Sell vs buy (flipped sides)
    w, l, reason = _compare_signals(res("sell", 95, pair="BBB/USD"), res("buy", 55))
    ok2 = w.pair == "AAA/USD"
    lines.append(f"sell95 vs buy55 → winner={w.pair} ({'PASS' if ok2 else 'FAIL'}): {reason}")

    # 3. Buy beats hold
    w, l, reason = _compare_signals(res("buy", 40), res("hold", 90, pair="BBB/USD"))
    ok3 = w.pair == "AAA/USD"
    lines.append(f"buy40 vs hold90 → winner={w.pair} ({'PASS' if ok3 else 'FAIL'}): {reason}")

    # 4. Higher-confidence buy beats lower buy
    w, l, reason = _compare_signals(res("buy", 70, pair="AAA/USD"), res("buy", 80, pair="BBB/USD"))
    ok4 = w.pair == "BBB/USD"
    lines.append(f"buy70 vs buy80 → winner={w.pair} ({'PASS' if ok4 else 'FAIL'}): {reason}")

    # 5. Sell still beats hold (both non-buy — sell advances only when no buy opposes)
    w, l, reason = _compare_signals(res("sell", 60, pair="AAA/USD"), res("hold", 90, pair="BBB/USD"))
    ok5 = w.pair == "AAA/USD"
    lines.append(f"sell60 vs hold90 → winner={w.pair} ({'PASS' if ok5 else 'FAIL'}): {reason}")

    # 6. Both sells — higher confidence advances (zombie bracket, can't beat any buy)
    w, l, reason = _compare_signals(res("sell", 60, pair="AAA/USD"), res("sell", 70, pair="BBB/USD"))
    ok6 = w.pair == "BBB/USD"
    lines.append(f"sell60 vs sell70 → winner={w.pair} ({'PASS' if ok6 else 'FAIL'}): {reason}")
except Exception as exc:
    ok1 = ok2 = ok3 = ok4 = ok5 = ok6 = False
    lines.append(f"ERROR: {exc}")

oks = [ok1, ok2, ok3, ok4, ok5, ok6]
fails = oks.count(False)
lines.append("")
lines.append(f"RESULT: {len(oks) - fails} passed, {fails} failed")

out = BASE / "longonly_check.txt"
scratch_space.report_temp('longonly_check.txt').write_text("\n".join(lines), encoding="utf-8")
print(f"RESULT: {len(oks) - fails} passed, {fails} failed")


def main():
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
