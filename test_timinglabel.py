"""THE TIMING LABEL SURVIVES ITS CONSUMERS (Nov 15).

The champion's timing label is appended to signal.reasons — a list five readers
walk, three of which truncate from the head:

    signal_bridge    "; ".join(reasons[:3])[:200]      the bot's record
    log_winner       reasons[:5]                       scout_tournament_winners
    console/UI       reasons[:9] / [:10]               what the operator sees

A champion already carries five reasons, so a label APPENDED as the sixth was
dropped by the bridge and by the DB while the console still showed it: the
warning existed only on screen. It is now inserted at index 0.

These checks hold the contract, not the wording: the label leads, survives every
truncation, and — because no reader parses these strings, they only substring-
test them — it can never trip a gate or move a size.

Prints "RESULT: N passed, M failed".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import scanner as sc

PASS, FAIL = 0, 0
lines = []

BASE = ["Regime: high_vol", "RSI oversold (23.8)", "MACD bullish (hist=0.0000)",
        "EMA bearish (short < long)", "  Signal confidence: 60/100 (moderate)"]


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def champion_with(eq):
    """Run the real tournament over two stubs, return the champion result."""
    def mk(pair, quality, conf=60.0):
        sig = sc.Signal(action="buy", confidence=conf, price=1.0,
                        reasons=list(BASE))
        return sc.ScanResult(pair=pair, signal=sig, entry_quality=quality)

    # a 15-point confidence gap keeps the timing TIEBREAK out of it (it only
    # fires within 10 points), so the champion is always AAA at exactly `eq`
    a = mk("AAA/USD", eq, conf=60.0)
    b = mk("BBB/USD", 1.0 - eq, conf=45.0)
    sc._evaluate_single = lambda kraken, pair, cfg: (a if pair == "AAA/USD" else b)
    cfg = config.TradingConfig()
    cfg.mtf_enabled = False
    cfg.trading_pairs = ["AAA/USD", "BBB/USD"]

    class K:
        pass

    champ, _hist = sc.tournament_scan(K(), cfg, progress_callback=lambda m: None)
    return champ


print("== 1. the label leads the champion's reasons ==")
stretched = champion_with(0.9)
check("1.1 a stretched champion is labelled", len(stretched.signal.reasons) == 6
      and stretched.signal.reasons[0].startswith("Timing:"),
      stretched.signal.reasons[:1])
check("1.2 the stretched label says so, with the number (90%)",
      "stretched, not a low" in stretched.signal.reasons[0]
      and "90%" in stretched.signal.reasons[0], stretched.signal.reasons[0])

near = champion_with(0.1)
check("1.3 a near-low champion is labelled at the head too",
      near.signal.reasons[0].startswith("Timing:")
      and "near the low" in near.signal.reasons[0], near.signal.reasons[:1])

print("== 2. it survives every truncation that used to drop it ==")
for champ, tag in ((stretched, "stretched"), (near, "near-low")):
    R = champ.signal.reasons
    check("2.1 %s: the bridge's [0:3] summary keeps it" % tag,
          any("Timing:" in r for r in R[:3]),
          "; ".join(R[:3]))
    summary = "; ".join(R[:3])[:200]
    check("2.2 %s: the 200-char summary is not cut mid-label" % tag,
          "Timing:" in summary and summary.count("not a low") <= 1,
          summary[:90])
    check("2.3 %s: the winner row's [0:5] keeps it" % tag,
          any("Timing:" in r for r in R[:5]), len(R[:5]))
    check("2.4 %s: the console's [0:9] keeps it" % tag,
          any("Timing:" in r for r in R[:9]))

print("== 3. the label cannot trip a gate or move a size ==")
KEYWORDS = ("exhaustion", "breakout failure", "exit signal", "momentum rider",
            "momentum ceiling", "illiquid", "vetoed", "stop")
label_texts = [stretched.signal.reasons[0], near.signal.reasons[0]]
check("3.1 the label carries no reader keyword (uppercase-insensitive)",
      not any(k in t.lower() for t in label_texts for k in KEYWORDS),
      [t for t in label_texts for k in KEYWORDS if k in t.lower()])
check("3.2 the bridge's two hard refusals never see it",
      not any("exhaustion" in t.lower() for t in label_texts)
      and not any("breakout failure" in t.lower() for t in label_texts))
check("3.3 confidence is untouched by the label",
      stretched.signal.confidence == 60.0 and near.signal.confidence == 60.0,
      (stretched.signal.confidence, near.signal.confidence))
check("3.4 GO BIG prepends its own line; the label still lands inside [0:3]",
      any("Timing:" in r for r in ([  "GO BIG: 100% notional, momentum exit, $30 net stop",
                                      *stretched.signal.reasons][:3])))

print("== 4. wiring ==")
src = open("scanner.py", encoding="utf-8").read()
check("4.1 the label is inserted at the head, never appended",
      "reasons.insert(" in src and "Timing: bought at" in src)
check("4.2 no leftover append of the timing label",
      'reasons.append(' not in src.split("Timing: bought at")[0].rsplit("\n", 8)[0])

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
