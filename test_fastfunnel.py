"""Test THE FAST FUNNEL + THE SELF-STRATEGY RETIREMENT (user rule,
Sep 6: "We have enough data to act now. Get it done. This won't survive
a week like this.").

THE DUAL FUNNELS (the hindsight scorer's task 4, live): FAST = immediate
entry, minimal gates (the worth floor WAIVES; the 3% guard, the popup
cap, the death line, too-thin, tide-OUT all STAND; the rotation lock and
the per-pair anti-spam are RETIRED (Sep 10));
SLOW = the full stack unchanged. The router reads hindsight_rules.json's
fast_route keys LIVE (weekly re-extraction, no restart) with the
PROVISIONAL data-backed rule until a tercile clears the bar:
momentum_1h >= +0.4% (73% winners, zero duds in the first 109 events).

THE SELF-STRATEGY RETIREMENT (the $176 drain): the bot's own RSI/BB
entry scan is OFF — the real account trades ONLY scout signals; exits,
the ladder, head-to-head and the exit stack all stand.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import hindsight_scorer as hs

# ── 1. THE ROUTER: the provisional rule ───────────────────────────────
check("the provisional rule is the documented tercile edge",
      hs.FAST_ROUTE_PROVISIONAL == {"momentum_1h": 0.4}, hs.FAST_ROUTE_PROVISIONAL)
ok, why = hs.fast_route({"momentum_1h": 0.99, "vol_burst": 4.1})
check("momentum_1h >= 0.4 routes FAST (the S-class 02:38 read)",
      ok and "momentum_1h" in why, why)
ok, why = hs.fast_route({"momentum_1h": 0.39})
check("momentum_1h 0.39 stays SLOW (just under the edge)",
      not ok and "0.39" in why, why)
ok, why = hs.fast_route({"momentum_1h": None})
check("a missing value stays SLOW", not ok, why)
ok, why = hs.fast_route(None)
check("no features at all stays SLOW (fail-closed to the full stack)",
      not ok, why)
ok, why = hs.fast_route("garbage")
check("a non-dict features payload stays SLOW, never raises",
      not ok, why)

# ── 2. THE ROUTER: the rules file overrides, LIVE ─────────────────────
tmp = Path(tempfile.mkdtemp())
rules_path = tmp / "rules.json"
old_rules = hs.RULES_FILE
try:
    hs.RULES_FILE = str(rules_path)
    rules_path.write_text(json.dumps({"fast_route": {
        "vol_burst": {"min": 2.0, "support": 20, "winner_rate": 0.8}}}))
    ok, why = hs.fast_route({"momentum_1h": 5.0, "vol_burst": 1.0})
    check("a landed rule REPLACES the provisional (momentum alone no "
          "longer routes)", not ok, why)
    ok, why = hs.fast_route({"momentum_1h": -1.0, "vol_burst": 2.5})
    check("the landed rule routes on ITS feature", ok and "vol_burst" in why, why)
    check("the why names the source", "rules.json" in why, why)
    # AND-semantics with two landed rules
    rules_path.write_text(json.dumps({"fast_route": {
        "vol_burst": {"min": 2.0}, "momentum_1h": {"min": 0.4}}}))
    ok1, _ = hs.fast_route({"vol_burst": 2.5, "momentum_1h": 0.9})
    ok2, _ = hs.fast_route({"vol_burst": 2.5, "momentum_1h": 0.1})
    check("multiple landed rules AND together (both must clear)",
          ok1 and not ok2, (ok1, ok2))
    # a broken/unreadable rules file falls back to the provisional
    rules_path.write_text("{not json")
    ok, why = hs.fast_route({"momentum_1h": 0.9})
    check("a corrupt rules file falls back to the provisional rule",
          ok and "provisional" in why, why)
finally:
    hs.RULES_FILE = old_rules

# ── 3. the scanner wiring (champion path) ────────────────────────────
bs = open("background_scanner.py", encoding="utf-8").read()
champ = bs[bs.find("def send_alert"):bs.find("def log_winner")]
check("the champion path routes via hindsight_scorer.fast_route",
      "from hindsight_scorer import fast_route" in champ
      and "_fast_route, _fast_why = fast_route(" in champ)
check("the route reads the FIRE-TIME features (the snapshot ctx)",
      '(_SNAPSHOT_CTX.get("ctx") or {}).get("features")' in champ)
check("the router fails CLOSED (a broken read = the slow funnel)",
      "_fast_route, _fast_why = False, \"\"" in champ)
check("THE WORTH FLOOR WAIVES on the fast route (and only there)",
      "if is_buy and not _fast_route:" in champ
      and "THE FAST FUNNEL WAIVES IT" in champ)
check("the popup title carries the FAST ROUTE mark",
      '" — FAST ROUTE" if _fast_route' in champ)
check("the voice says Fast ROUTE", '"Fast route! " if _fast_route' in champ)
check("the message spells the route + the risk story",
      "FAST ROUTE: {_fast_why}" in champ
      and "the 3% guard + the" in champ)
check("the fired snapshot tags the route (champion-fast)",
      '"champion-fast" if _fast_route' in champ)
check("the ONLY gate the fast route waives is the worth floor "
      "(death line, caps, tide, thin all stand)",
      champ.count("not _fast_route") == 1
      and "if is_buy and not _fast_route:" in champ
      and "DEATH LINE" in champ)
check("the popup cap is retired (Sep 9: functions kept, never called) — the fast route's cap waiver is moot",
      "_recent_fired_popups()" not in champ and "THE FAST FUNNEL" in champ)

# ── 4. the scanner wiring (mover path) ───────────────────────────────
check("the mover path routes too (momentum runners are the fast feed)",
      "_m_fast, _m_fast_why = fast_route(" in bs)
check("the mover's worth floor waives on the fast route",
      "if _m_worth < _m_pace * _m_wf and not _m_fast:" in bs)
check("the mover popup carries the mark + the snapshot tags the route",
      '" — FAST ROUTE" if _m_fast' in bs and '"mover-fast" if _m_fast' in bs)

# ── 5. THE SELF-STRATEGY RETIREMENT (the bot side) ───────────────────
bot = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
cfg_src = (bot / "config.py").read_text(encoding="utf-8")
eng_src = (bot / "bot_engine.py").read_text(encoding="utf-8")
run_src = (bot / "bot_runner.py").read_text(encoding="utf-8")
check("config: self_strategy_enabled defaults OFF",
      "self_strategy_enabled: bool = False" in cfg_src)
check("engine: the entry scan is gated on the flag",
      "if _self_strategy:" in eng_src
      and "_self_strategy = bool(getattr(self.config, \"self_strategy_enabled\", False))" in eng_src)
check("engine: the retirement logs once, says what still runs",
      "SELF-STRATEGY ENTRY SCAN OFF" in eng_src
      and "scout signals only; exits + reader stand" in eng_src)
check("engine: the DCA continuation is gated too (a retired scan must "
      "not keep adding tranches)",
      'and getattr(self.config, "self_strategy_enabled", False)' in eng_src)
check("engine: the exit checks are NOT gated (the exit stack stands)",
      eng_src.find("Check exits for all open positions")
      < eng_src.find("SELF-STRATEGY RETIREMENT"))
check("runner: the config_json override can flip it back without a code edit",
      '"self_strategy_enabled" in overrides' in run_src)
check("the scout reader's own buys are untouched (the bridge path)",
      "engine._execute_buy" in (bot / "scout_signal_reader.py").read_text(encoding="utf-8"))

# ── 6. the seam: the scanner + the scorer share the ONE router ──────
import background_scanner as _bs_probe  # noqa: F401  (imports clean)
check("the router lives in the scorer (ONE source, like brain_size_mult)",
      hasattr(hs, "fast_route") and callable(hs.fast_route))

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_fastfunnel_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
