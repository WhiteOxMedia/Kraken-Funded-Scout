"""Test the SILENT TREND-EXIT CHANNEL (user rule, Aug 31: "can the console
track it and send the signal silently to the bridge side for the bot's
trades?"). Three pieces: (1) the scanner watches every pair the bot holds
and writes a SILENT scout_trend_exit SELL when the 1h knife reverses;
(2) the reader's two-ledger guard honors exactly that ONE source;
(3) the console's BOT'S BOOK panel tracks it. The bot's own engine knife
(bot repo, 76a8810) remains the independent floor."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# (the absolute bot-repo sys.path insert is GONE — Sep 30: it shadowed the
# scout's signal_bridge with the bot's copy and let the drive test the
# live tree inside the gate sandbox; the bot-repo files this drive reads
# do so by absolute open() paths, which never needed the insert)

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs
import scratch_space   # scratch policy: nothing volatile lands in the repo root


class _Cfg:
    ema_short = 9
    ema_long = 21


def series(start, end, n=80):
    import time as _t
    step = (end - start) / (n - 1)
    t0 = int(_t.time() * 1000) - n * 3_600_000
    return [[t0 + i * 3_600_000, 1, 1, 1, start + i * step, 100] for i in range(n)]


class FakeExchange:
    def __init__(self, candles):
        self.candles = candles
        self.fetch_calls = 0

    def fetch_ohlcv(self, pair, timeframe="1h", limit=100):
        self.fetch_calls += 1
        return self.candles


def knife(candles):
    bs._trend_exit_cache.pop("TEST/USD", None)
    return bs.knife_reversed_1h(FakeExchange(candles), "TEST/USD", _Cfg())


# ── 1. the scanner's knife ────────────────────────────────────────────
rev, note = knife(series(2.0, 1.0))
check("declining 1h -> reversed", rev is True, note)
check("note names the knife", "EMA9" in note and "EMA21" in note, note)
rev, note = knife(series(1.0, 2.0))
check("rising 1h -> not reversed", rev is False, note)
rev, note = knife([[0, 1, 1, 1, 1.0, 10]] * 10)
check("short data -> fail-closed, not cached", rev is False, note)
fx = FakeExchange(series(2.0, 1.0))
bs._trend_exit_cache.pop("TEST/USD", None)
bs.knife_reversed_1h(fx, "TEST/USD", _Cfg())
bs.knife_reversed_1h(fx, "TEST/USD", _Cfg())
check("knife cached per pair (1 fetch / 5 min)", fx.fetch_calls == 1, fx.fetch_calls)


class RaisingExchange:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("api down")


bs._trend_exit_cache.pop("TEST/USD", None)
rev, note = bs.knife_reversed_1h(RaisingExchange(), "TEST/USD", _Cfg())
check("broken read -> fail-closed (no exit on a broken read)", rev is False and note == "")

# ── 2. the watch block wiring (source checks) ────────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("watch reads the bot's holdings from the bridge",
      "get_open_positions as _bop" in src)
check("watch writes the silent exit signal",
      'write_exit_signal as _wes' in src and 'source="scout_trend_exit"' in src)
check("watch runs ALWAYS — placed before the cooldown flatten",
      0 < src.find("TREND-EXIT WATCH") < src.find("Cooldown flatten"))
_i = src.find("TREND-EXIT WATCH")
_block = src[_i:_i + 2600]
check("the exit write is SILENT — no voice, no popup, no phone push",
      all(s not in _block for s in ("notify_big", "_speak", "_push_to_phone", "speech_queue")))
check("exit reason says GET OUT NOW + the knife",
      "GET OUT NOW — trend reversed" in src)
check("rewrite pacing present (15 min per pair)",
      "_trend_exit_pace" in src and "900.0" in src)
# ── THE EXIT-WATCH SWITCH (Sep 14): the watch holds its fire when her
#    The Sep-14 switch wiring ("_held = (_bop() if _xwe() else [])" via
#    an "exit_watch_enabled as _xwe" import) was REPLACED later: the
#    watch now gates on the module constant SCOUT_TREND_EXIT_ENABLED —
#    an OFF flag leaves _held empty (same no-silent-SELL guarantee,
#    simpler mechanism; the console-toggle wiring was retired).
check("the watch's holdings read rides the enabled flag (an OFF "
      "watch holds its fire — no silent SELL written)",
      "if SCOUT_TREND_EXIT_ENABLED:" in src
      and "_held = _bop()" in src
      and "_held = []" in src)
check("the switch lives in the shared bridge (both processes read the "
      "same toggle)",
      "def exit_watch_enabled" in open("signal_bridge.py", encoding="utf-8").read()
      and 'DB_PATH.parent / "console_state.json"'
      in open("signal_bridge.py", encoding="utf-8").read())

# ── 3. the bridge contract: dup-safe + 10-min validity (already true) ─
_sigt = open("signal_bridge.py", encoding="utf-8").read()
check("write_exit_signal is dup-safe (skips while a sell pends)",
      "Exit signal already pending" in _sigt)
check("write_exit_signal takes a source param",
      'source: str = "scout_exit_check"' in _sigt)

# ── 4. the reader carve-out (THE LIVE copy in the bot repo) ──────────
# (updated Sep 4 restore: BOTH scanner exit sources are admitted on real
# money — scout_exit_check, the 5-min exit check, AND scout_trend_exit,
# the silent knife. Every other sell source never enters the branch.)
rd = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py",
          encoding="utf-8").read()
check("sell branch admits BOTH scanner exit sources",
      'source in ("scout_exit_check", "scout_trend_exit")' in rd)
check("contest exits still ignored (the two-ledger rule stands — no other sell source enters the branch)",
      rd.count('action == "sell" and source in') == 1)
check("trend-exit pass is logged",
      "TREND-EXIT SELL" in rd)
check("the trend-exit log names the knife reversal",
      "the scanner's 1h knife reversed" in rd)
check("trend exits flow before the manual-position sell path",
      0 < rd.find("TREND-EXIT SELL") < rd.find("Direct sell failed"))

# ── 5. the console panel ──────────────────────────────────────────────
ap = open("app.py", encoding="utf-8").read()
check("BOT'S BOOK panel exists", "def _render_bots_book" in ap)
check("panel wired next to her words",
      0 < ap.find("_render_her_words()") < ap.find("_render_bots_book(kraken, cfg)"))
check("panel reads the bridge holdings",
      "get_open_positions" in ap[ap.find("def _render_bots_book"):ap.find("def _toast_new_her_words")])
check("panel shows the GET OUT SENT state",
      "REVERSED — GET OUT SENT" in ap)
check("panel shows riding vs reversed LEDs",
      "🟢 riding" in ap and "🔴 reversed" in ap)
check("panel reuses the scanner's knife (dup-safe reload)",
      "knife_reversed_1h" in ap)

# ── 6. the bot's own engine knife is still there (the floor) ─────────
be = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\bot_engine.py",
          encoding="utf-8").read()
check("engine knife floor still present (independent of the scanner)",
      "_trend_reversed_1h" in be and "GET OUT NOW — trend reversed" in be)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_trendbridge_check.txt'), "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
