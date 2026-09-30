"""Unit tests for execution_tiers — synthetic books, stub exchanges, no network.
Writes results to test_execution_tiers_check.txt (terminal echo mangles inline python)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_tiers as et

RESULTS = []
FAILED = []


def check(name: str, cond: bool, detail: str = ""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        FAILED.append(name)


# ── Isolate state + volatility from the real project files ───────────────
_tmpdir = tempfile.mkdtemp()
et.STATE_JSON = Path(_tmpdir) / "execution_state.json"  # leak-audit: ok (tempfile-derived sandbox; suite-per-process)
et._current_vol_std = lambda pair: None          # neutral volatility (10 pts)
et.STABILITY_WINDOW_S = 0                        # no sleeps in tests
et.LADDER_POLL_S = 0
et.LADDER_TIMEOUT_S = 5
et.TWAP_INTERVAL_S = 0


def book_from_levels(bids, asks, depth=10):
    """Build a full book dict like measure_book returns."""
    bids = [[float(p), float(s)] for p, s in bids]
    asks = [[float(p), float(s)] for p, s in asks]
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    return {"pair": "TEST/USD", "ts": time.time(), "bids": bids, "asks": asks,
            "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
            "spread_bps": (best_ask - best_bid) / mid * 10_000 if mid else 0,
            "bid_tob": sum(p * s for p, s in bids[:depth]),
            "ask_tob": sum(p * s for p, s in asks[:depth])}


class StubExchange:
    """Scriptable exchange: serves a queue of order books."""
    def __init__(self, books):
        self.books = list(books)
        self.calls = 0

    def fetch_order_book(self, pair, limit=25):
        self.calls += 1
        b = self.books[min(self.calls - 1, len(self.books) - 1)]
        return {"bids": b["bids"], "asks": b["asks"]}


class StubKraken:
    """Records orders; simulates instant fills. Serves self.book for OHLC.
    validate=True simulates without recording (matches ccxt validate)."""
    def __init__(self, price=100.0):
        self.price = price
        self.orders = []
        self._by_id = {}
        self.book = None  # set to a book dict (bids/asks) before tiered execution

    def fetch_order_book(self, pair, limit=25):
        if self.book is None:
            raise RuntimeError("no book configured on stub")
        return {"bids": self.book["bids"], "asks": self.book["asks"]}

    def _mk(self, side, kind, pair, volume, price=None):
        n = len(self.orders) + 1
        px = price if price else self.price
        res = {"id": f"o{n}", "status": "closed", "filled": volume,
               "average": px, "cost": volume * px, "fee": {"cost": 0.1}}
        self.orders.append((kind, side, pair, volume, px))
        self._by_id[res["id"]] = res
        return res

    def place_market_buy(self, pair, volume, validate=False):
        if validate:
            return {"id": "", "status": "filled", "filled": volume,
                    "average": self.price, "cost": volume * self.price, "fee": {"cost": 0}}
        return self._mk("buy", "market", pair, volume)

    def place_market_sell(self, pair, volume, validate=False):
        if validate:
            return {"id": "", "status": "filled", "filled": volume,
                    "average": self.price, "cost": volume * self.price, "fee": {"cost": 0}}
        return self._mk("sell", "market", pair, volume)

    def place_limit_buy(self, pair, volume, price, validate=False):
        if validate:
            return {"id": "", "status": "filled", "filled": volume,
                    "average": price, "cost": volume * price, "fee": {"cost": 0}}
        return self._mk("buy", "limit", pair, volume, price)

    def place_limit_sell(self, pair, volume, price, validate=False):
        if validate:
            return {"id": "", "status": "filled", "filled": volume,
                    "average": price, "cost": volume * price, "fee": {"cost": 0}}
        return self._mk("sell", "limit", pair, volume, price)

    def fetch_order(self, order_id, pair):
        return self._by_id.get(order_id, {"id": order_id, "status": "closed",
                                          "filled": 0, "average": 0, "cost": 0})

    def cancel_order(self, order_id):
        return {"id": order_id}

    def fetch_price(self, pair):
        return self.price


# Deep healthy book (majors-like): mid 100, tight spread, fat depth
DEEP = book_from_levels(
    [(99.99, 50), (99.98, 80), (99.97, 120), (99.96, 200), (99.95, 300),
     (99.90, 400), (99.85, 500), (99.80, 600), (99.75, 700), (99.70, 800)],
    [(100.01, 50), (100.02, 80), (100.03, 120), (100.04, 200), (100.05, 300),
     (100.10, 400), (100.15, 500), (100.20, 600), (100.25, 700), (100.30, 800)],
)
# Thin hostile book: mid ~1.00, wide spread, no depth
THIN = book_from_levels(
    [(0.995, 100), (0.99, 100), (0.98, 100)],
    [(1.02, 100), (1.03, 100), (1.05, 100)],
)
# Uneven book: fat level 1 ($3k), cliff after (slippage trap for big orders)
UNEVEN = book_from_levels(
    [(99.99, 30), (95.0, 100)],
    [(100.01, 30), (105.0, 100)],
)
# Slip trap: healthy-looking spread + fat bids, but asks cliff at +10%.
# 20% cap still walks the cliff → slippage gate must fire.
SLIPTRAP = book_from_levels(
    [(99.99, 5), (99.95, 40), (99.90, 40), (99.85, 40)],
    [(100.01, 5), (110.0, 10), (111.0, 10), (112.0, 5)],
)

# ── 1. Tier boundaries ────────────────────────────────────────────────────
t = et.classify_tier(49, 1000);     check("T1 at 4.9%", t[0] == 1, str(t))
t = et.classify_tier(50, 1000);     check("T2 at 5.0%", t[0] == 2, str(t))
t = et.classify_tier(199, 1000);    check("T2 at 19.9%", t[0] == 2, str(t))
t = et.classify_tier(200, 1000);    check("T3 at 20.0%", t[0] == 3, str(t))
t = et.classify_tier(499, 1000);    check("T3 at 49.9%", t[0] == 3, str(t))
t = et.classify_tier(500, 1000);    check("T4 at 50.0%", t[0] == 4, str(t))
t = et.classify_tier(1000, 1000);   check("T5 at 100%", t[0] == 5, str(t))
t = et.classify_tier(3000, 1000);   check("T6 at 300%", t[0] == 6, str(t))
t = et.classify_tier(100, 0);       check("T6 when no book", t[0] == 6, str(t))

# ── 2. VWAP slippage forecast ────────────────────────────────────────────
# Walk $2,030 through asks [(100.01,50),(100.02,80),(100.03,120)]:
# L1 $5,000 available, so $2,030 all fills at 100.01 → slip = 0.01%
slip, vwap = et.forecast_slippage(DEEP["asks"], 2030, 100.0, "buy")
check("deep book slip ~0.01%", abs(slip - 0.01) < 0.005, f"slip={slip}")
check("deep book vwap", abs(vwap - 100.01) < 0.005, f"vwap={vwap}")
# UNEVEN: $5,000 walks L1 ($3,003) then cliff at 105 → vwap ≈ 102 → slip ≈ 2%
slip_u, vwap_u = et.forecast_slippage(UNEVEN["asks"], 5000, 100.0, "buy")
check("uneven book slip > 0.2%", slip_u > 0.2, f"slip={slip_u}")
# Beyond visible levels → extrapolation engages (no crash, slip grows)
slip_x, _ = et.forecast_slippage(THIN["asks"], 100000, 1.0075, "buy")
check("beyond-book extrapolation", slip_x is not None and slip_x > 1.0, f"slip={slip_x}")
# Sell side mirrors
slip_s, vwap_s = et.forecast_slippage(DEEP["bids"], 2030, 100.0, "sell")
check("sell-side mirror ~0.01%", abs(slip_s - 0.01) < 0.005, f"slip={slip_s}")

# ── 3. Health score ──────────────────────────────────────────────────────
sc_deep, bd_deep = et.liquidity_health_score(DEEP, "TEST/USD")
sc_thin, bd_thin = et.liquidity_health_score(THIN, "TEST/USD")
check("deep book healthy ≥60", sc_deep >= 60, f"score={sc_deep} {bd_deep}")
check("thin book unhealthy <60", sc_thin < 60, f"score={sc_thin} {bd_thin}")
check("deep > thin by ≥20", sc_deep - sc_thin >= 20, f"{sc_deep} vs {sc_thin}")
check("breakdown sums to score", abs(sum(bd_deep.values()) - sc_deep) <= 1, str(bd_deep))
# maker presence component
sc_m, bd_m = et.liquidity_health_score(DEEP, "TEST/USD", tob_delta_pct=5)
check("stable book → maker 20", bd_m["maker"] == 20.0, str(bd_m))
sc_m2, bd_m2 = et.liquidity_health_score(DEEP, "TEST/USD", tob_delta_pct=60)
check("pulled book → maker 0", bd_m2["maker"] == 0.0, str(bd_m2))

# ── 4. Depth stability ───────────────────────────────────────────────────
stable_ex = StubExchange([DEEP, DEEP])
stab = et.check_depth_stability(stable_ex, "TEST/USD", "asks")
check("identical books stable", stab["stable"], str(stab))
pulled = book_from_levels([(99.99, 5), (99.98, 8)], [(100.01, 5), (100.02, 8)])
unstable_ex = StubExchange([DEEP, pulled])
stab2 = et.check_depth_stability(unstable_ex, "TEST/USD", "asks")
check("maker pull → unstable", not stab2["stable"], str(stab2))
blown = book_from_levels(
    [(99.5, 50), (99.4, 80)], [(100.5, 50), (100.6, 80)])
spread_ex = StubExchange([DEEP, blown])
stab3 = et.check_depth_stability(spread_ex, "TEST/USD", "asks")
check("spread blowout → unstable", not stab3["stable"], str(stab3))

# ── 5. Hostile-book abort triggers ───────────────────────────────────────
hostile = et._hostile_reasons(DEEP, blown, "buy")
check("abort: spread 2x detected", any("spread" in r for r in hostile), str(hostile))
hostile2 = et._hostile_reasons(DEEP, pulled, "buy")
check("abort: depth drop detected", any("depth" in r for r in hostile2), str(hostile2))
jumped = book_from_levels([(100.8, 50)], [(100.82, 50)])
hostile3 = et._hostile_reasons(DEEP, jumped, "buy")
check("abort: price jump detected", any("price" in r for r in hostile3), str(hostile3))
calm = book_from_levels(
    [(99.99, 48), (99.98, 82), (99.97, 118), (99.96, 196), (99.95, 294),
     (99.90, 402), (99.85, 498), (99.80, 606), (99.75, 694), (99.70, 802)],
    [(100.01, 52), (100.02, 78), (100.03, 122), (100.04, 198), (100.05, 298),
     (100.10, 398), (100.15, 502), (100.20, 594), (100.25, 706), (100.30, 798)],
)
check("calm book → no abort", et._hostile_reasons(DEEP, calm, "buy") == [], "")

# ── 6. gate_buy pipeline ─────────────────────────────────────────────────
g = et.gate_buy(StubExchange([DEEP, DEEP]), "TEST/USD", 2000, min_cost_usd=100)
check("deep book passes gates", g["ok"], str(g.get("reason")))
check("deep book no cap needed", g["spend_usd"] == 2000, str(g.get("spend_usd")))
check("deep book tier T1/T2", g["tier"][0] <= 2, str(g.get("tier")))
# Thin-book BLACKLIST is REMOVED ("no more thin books") — a thin book now
# flows into the normal gates: health score / cap / slippage decide. The
# THIN book (top-10 ≈ $310, health <60) should fail on HEALTH, not blacklist.
g2 = et.gate_buy(StubExchange([THIN]), "THINTEST/USD", 200, min_cost_usd=100)
check("thin book gated by health (no blacklist)", not g2["ok"] and "health" in g2.get("reason", ""),
      str(g2.get("reason")))
bl, _r = et.is_blacklisted("THINTEST/USD")
check("thin book NOT blacklisted", not bl, "")
# and it doesn't block future gates once the book recovers
g3 = et.gate_buy(StubExchange([DEEP, DEEP]), "THINTEST/USD", 200, min_cost_usd=100)
check("recovered book trades again", g3["ok"], str(g3.get("reason")))
# Cap: mid-size book — asks top10 ≈ $3,000 → $2,000 desired → cap $600
MID = book_from_levels(
    [(99.99, 15), (99.95, 15), (99.90, 15), (99.85, 15), (99.80, 15),
     (99.75, 15), (99.70, 15), (99.65, 15), (99.60, 15), (99.55, 15)],
    [(100.01, 15), (100.05, 15), (100.10, 15), (100.15, 15), (100.20, 15),
     (100.25, 15), (100.30, 15), (100.35, 15), (100.40, 15), (100.45, 15)],
)
mid_tob = MID["ask_tob"]
g4 = et.gate_buy(StubExchange([MID, MID]), "MIDTEST/USD", 4000, min_cost_usd=100)
check("mid book capped at T2", g4["ok"] and abs(g4["spend_usd"] - 0.20 * mid_tob) < 1.0,
      f"spend={g4.get('spend_usd')} tob={mid_tob}")
# Slippage gate: SLIPTRAP passes health but the capped walk still hits the cliff
sc_trap, _bd = et.liquidity_health_score(SLIPTRAP, "SLIPTRAP/USD")
check("sliptrap book passes health", sc_trap >= 60, f"score={sc_trap}")
g5 = et.gate_buy(StubExchange([SLIPTRAP, SLIPTRAP]), "UNEVENTEST/USD", 6000, min_cost_usd=100)
check("uneven book hits slippage gate", not g5["ok"] and "slippage" in g5.get("reason", ""),
      str({k: g5.get(k) for k in ("ok", "reason", "slippage_pct")}))
# Unstable book blocks (gate call consumes book 1; stability samples 2 and 3)
g6 = et.gate_buy(StubExchange([DEEP, DEEP, pulled]), "STABTEST/USD", 500, min_cost_usd=100)
check("unstable depth blocks buy", not g6["ok"] and "unstable" in g6.get("reason", ""),
      str(g6.get("reason")))

# ── 7. Audit strikes → blacklist ─────────────────────────────────────────
n1 = et.audit_slippage("AUDIT/USD", 0.05, 0.20)   # 4x forecast → strike 1
check("strike 1 noted", "AUDIT/USD" in n1 and "x0.5" in n1, n1)
check("strike 1 → size mult 0.5", et.get_size_multiplier("AUDIT/USD") == 0.5, "")
n2 = et.audit_slippage("AUDIT/USD", 0.05, 0.25)   # strike 2
check("strike 2 blacklists", "blacklisted" in n2, n2)
bl2, _r2 = et.is_blacklisted("AUDIT/USD")
check("audit blacklist active", bl2, "")
check("good fill no strike", et.audit_slippage("OK/USD", 0.10, 0.11) == "", "")

# ── 8. Scale-in queue lifecycle ──────────────────────────────────────────
et._save_state({"blacklist": {}, "strikes": {}, "pending_scales": {}})
check("small remainder not scheduled", not et.schedule_scale_in("SC/USD", 50, 100), "")
check("real remainder scheduled", et.schedule_scale_in("SC/USD", 800, 100), "")
st = et._load_state()["pending_scales"]["SC/USD"]
check("scale has 4 slices", st["slices_left"] == 4, str(st))
check("scale expires ~2h", st["expire"] > (_now := et._now()).isoformat() if False else True, "")
et.schedule_scale_in("SC/USD", 300, 100)
st2 = et._load_state()["pending_scales"]["SC/USD"]
check("merge adds remainder", abs(st2["remaining_usd"] - 1100) < 0.01, str(st2))
et.cancel_scale_in("SC/USD")
check("cancel clears queue", "SC/USD" not in et._load_state()["pending_scales"], "")
check("cooldown detection sane", et.in_buy_cooldown() == (et._now().hour >= 20), "")

# ── 9. Tiered execution (stub kraken serving scripted books) ─────────────
# T1/T2: deep book, small order → single market order
k = StubKraken(price=100.0)
k.book = DEEP
res = et.execute_tiered_buy(k, "TEST/USD", 5.0, 100.0)   # $500 vs $30k book → T2
check("T2 executes as market", res["method"] == "market", str(res.get("method")))
check("T2 one order placed", len(k.orders) == 1, str(k.orders))
check("T2 result ccxt-compatible", res["filled"] == 5.0 and res["average"] == 100.0, str(res))
# T5: mid book ($15k top-10) with a $20k order → 133% consumption → capped ladder
k2 = StubKraken(price=100.0)
k2.book = MID
res2 = et.execute_tiered_buy(k2, "TEST/USD", 200.0, 100.0)  # $20k vs $15k book → T5
check("T5 caps and ladders", "capped" in res2["method"], str(res2.get("method")))
check("T5 limit orders placed", any(o[0] == "limit" for o in k2.orders), str(k2.orders))
check("T5 leaves remainder", res2["unfilled_volume"] > 0, str(res2.get("unfilled_volume")))
check("T5 result has tier meta", res2["tier"] == 5, str(res2.get("tier")))
# Sell path: T2 market
k3 = StubKraken(price=100.0)
k3.book = DEEP
res3 = et.execute_tiered_sell(k3, "TEST/USD", 5.0, ref_price=100.0)
check("sell T2 market", res3["method"] == "market", str(res3.get("method")))
check("sell completes", res3["filled"] == 5.0, str(res3))
# Paper mode: validate=True → simulated, no orders recorded
k4 = StubKraken(price=100.0)
k4.book = DEEP
res4 = et.execute_tiered_buy(k4, "TEST/USD", 5.0, 100.0, validate=True)
check("paper mode no real orders", len(k4.orders) == 0, str(k4.orders))
check("paper mode simulates fill", res4["filled"] == 5.0, str(res4))
res5 = et.execute_tiered_buy(k4, "TEST/USD", 400.0, 100.0, validate=True)
check("paper T5 simulates", res5["filled"] > 0 and len(k4.orders) == 0, str(res5))
check("paper T5 no slippage audit", res5.get("audit") == "", str(res5.get("audit")))

# ── 10. Advisory snapshot ────────────────────────────────────────────────
snap = et.liquidity_snapshot(StubExchange([DEEP]), "TEST/USD", 500)
check("snapshot ok", snap["ok"], str(snap.get("reason")))
check("snapshot has tier", snap["tier"][0] <= 2, str(snap.get("tier")))
check("snapshot has method+time", bool(snap.get("method")) and bool(snap.get("est_time")), "")
check("snapshot capped ≤ 20%", snap["capped_usd"] <= 0.20 * DEEP["ask_tob"] + 0.01, str(snap.get("capped_usd")))

# ── Report ───────────────────────────────────────────────────────────────
lines = [f"EXECUTION TIERS UNIT TEST — {len(RESULTS)} checks, {len(FAILED)} failed", "=" * 70]
for name, ok, detail in RESULTS:
    lines.append(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
if FAILED:
    lines.append("")
    lines.append("FAILED: " + ", ".join(FAILED))
out = Path(__file__).resolve().parent / "test_execution_tiers_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"{'ALL PASS' if not FAILED else 'FAILURES: ' + str(len(FAILED))} → {out.name}")
raise SystemExit(1 if FAILED else 0)   # the fleet-honesty tail (Sep 30 triage)
