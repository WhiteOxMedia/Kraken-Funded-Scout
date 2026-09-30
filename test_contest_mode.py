"""Verify contest mode: original rules — strong sells can win brackets."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from scanner import _compare_signals, ScanResult
from strategy import Signal
import scratch_space   # scratch policy: nothing volatile lands in the repo root

lines = []


def res(action, conf, rr=2.0, pair="AAA/USD"):
    return ScanResult(pair=pair, signal=Signal(action=action, confidence=conf, price=100,
                                               risk_reward=rr, reasons=["t"]))


# CONTEST mode (long_only=False): sell 95% beats buy 55% — honest bracket
w, l, reason = _compare_signals(res("buy", 55), res("sell", 95, pair="BBB/USD"), long_only=False)
ok1 = w.pair == "BBB/USD"
lines.append(f"contest buy55 vs sell95 → {w.pair} ({'PASS' if ok1 else 'FAIL'}): {reason}")

# BOT mode (long_only=True default): buy still beats sell
w, l, reason = _compare_signals(res("buy", 55), res("sell", 95, pair="BBB/USD"))
ok2 = w.pair == "AAA/USD"
lines.append(f"bot buy55 vs sell95 → {w.pair} ({'PASS' if ok2 else 'FAIL'}): {reason}")

oks = [ok1, ok2]
fails = oks.count(False)
lines.append("")
lines.append(f"RESULT: {len(oks) - fails} passed, {fails} failed")

out = BASE / "contest_mode_check.txt"
scratch_space.report_temp('contest_mode_check.txt').write_text("\n".join(lines), encoding="utf-8")
print(f"RESULT: {len(oks) - fails} passed, {fails} failed")
raise SystemExit(1 if fails else 0)
