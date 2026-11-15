"""DRY RUN — prove the scanner's own fire path writes the deep fields + shadow record.

No order is placed. Nothing is written to the live watch file. This drives:
  * the live loop's own deep-read/ranking function  (background_scanner.deep_crown_pool)
  * the live loop's own entry writer                (background_scanner.auto_track_popup,
    called exactly as the champion/mover fire sites call it)
against the current live market read, pointing the watch file at scratch/_probe_watch.json.

Run: ./.venv/Scripts/python.exe -X utf8 scratch/fire_probe.py
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import ccxt  # noqa: E402

import background_scanner as bs      # noqa: E402
import buy_low                       # noqa: E402
import config                        # noqa: E402
import strategy                      # noqa: E402

PROBE_WATCH = ROOT / "scratch" / "_probe_watch.json"
# THE LIVE BOOK ITSELF: the same pair list the running loop's full scan walks,
# so the pool and every comparison in the record is made of the coins the live
# bracket actually evaluated. No synthetic candidates, no hand-picked pairs.
PAIRS = None            # filled from config.TradingConfig().trading_pairs


def main() -> int:
    cfg = config.TradingConfig()          # the loop's own defaults
    hour = bs._now_mst().hour
    _block, _interval, min_conf, *_rest = bs.get_time_block(hour, bs._now_mst().weekday())
    print(f"cfg: deep_scan_enabled={cfg.deep_scan_enabled} "
          f"lookback={cfg.deep_lookback_candles} | flags "
          f"timing={cfg.entry_timing_rank} spend={cfg.spend_rank} "
          f"buy_low={cfg.buy_low_rank} | min_conf={min_conf}")

    exchange = ccxt.kraken({"enableRateLimit": True})
    bal_before = None
    try:
        import contest_account as ca
        bal_before = (ca.load_state() or {}).get("current_balance")
    except Exception as exc:
        print("balance read failed:", exc)

    # ── the bracket exactly as the full scan builds it ──────────────────
    PAIRS = list(cfg.trading_pairs)      # the running loop's own full-scan book
    print(f"live book: {len(PAIRS)} pairs")
    all_data = bs.fetch_ohlcv_parallel(exchange, PAIRS, max_workers=10)
    print(f"fetched {len(all_data)}/{len(PAIRS)} pairs")
    strategy.disable_deep_analysis()
    results = {}
    for pair, df in all_data.items():
        try:
            results[pair] = strategy.evaluate_strategy(df, cfg)
        except Exception as exc:
            print(f"  eval failed {pair}: {exc}")
    buys = {p: s for p, s in results.items() if s.action == "buy"}
    print("actions:", {p.split('/')[0]: (s.action, round(s.confidence))
                       for p, s in sorted(results.items(),
                                          key=lambda kv: -kv[1].confidence)})
    if not results:
        print("nothing evaluated — no probe")
        return 1

    # the crown candidate: highest-confidence buy (the live rule), else the
    # best-evaluated coin so the row path is still exercised on a quiet night.
    if buys:
        best_pair = max(buys, key=lambda p: buys[p].confidence)
        why_choice = "highest-confidence BUY (the live crown rule)"
    else:
        best_pair = max(results, key=lambda p: results[p].confidence)
        why_choice = ("no BUY in the bracket — driving the best-evaluated coin "
                      "so the row path is still proven (would NOT fire live)")
    best_signal = results[best_pair]
    print(f"probe crown: {best_pair} {best_signal.action} "
          f"{best_signal.confidence:.0f}% — {why_choice}")

    # ── THE LIVE LOOP'S OWN READ + RANKINGS ─────────────────────────────
    best_pair, best_signal, cands, ranks = bs.deep_crown_pool(
        exchange, results, all_data, cfg, best_pair, best_signal, min_conf)

    print("\n-- every candidate's deep read --")
    bad = []
    for p, r in sorted(cands.items(), key=lambda kv: -kv[1].signal.confidence):
        sp = r.spend or {}
        src = sp.get("src") or []
        print(f"  {p:10s} {r.signal.action:4s} {r.signal.confidence:5.1f}%  "
              f"bars={sp.get('bars')} cap=${sp.get('cap_usd')} "
              f"daypos={sp.get('pos_in_day_pct')} src={','.join(src) or '(none)'}")
        if "deep_history" not in src:
            bad.append((p, "history-less", src))
        if "no_capacity" in src or not sp.get("cap_usd"):
            bad.append((p, "no_capacity", src))
    print("candidates failing the deep read:", bad or "none")

    # ── THE LIVE FIRE WRITER, called as the fire sites call it ──────────
    PROBE_WATCH.write_text("{}", encoding="utf-8")
    old_watch = bs.CONTEST_WATCH_JSON
    bs.CONTEST_WATCH_JSON = str(PROBE_WATCH)
    try:
        ok = bs.auto_track_popup(
            best_pair,
            float(best_signal.price or 0.0),
            float((best_signal.spend_capacity or {}).get("cap_usd") or 0.0),
            3.0, 180.0,
            f"BUY {best_signal.confidence:.0f}% (dry-run probe)",
            "champion",
            entry_quality=getattr(best_signal, "entry_quality", None),
            entry_timing=getattr(best_signal, "entry_timing", None),
            spend_capacity=getattr(best_signal, "spend_capacity", None),
            spend_rank=getattr(best_signal, "spend_rank", None),
            buy_low=getattr(best_signal, "buy_low", None),
            buy_low_rank=getattr(best_signal, "buy_low_rank", None),
            setup=bs._setup_tags(route="champion",
                                 conviction=float(best_signal.confidence)),
            exchange=exchange)
        row = json.loads(PROBE_WATCH.read_text(encoding="utf-8")).get(best_pair)
    finally:
        bs.CONTEST_WATCH_JSON = old_watch

    print(f"\n-- auto_track_popup returned {ok} — the row it wrote --")
    if not row:
        print("NO ROW WRITTEN")
        return 1
    show = {k: row.get(k) for k in
            ("added", "opened_ts", "contest_id", "entry", "size", "source",
             "why", "entry_quality", "spend_capacity", "spend_rank",
             "entry_timing", "buy_low", "buy_low_rank", "buy_low_v", "spend_v")}
    print(json.dumps(show, indent=1, default=str)[:4000])

    # ── the contract checks ─────────────────────────────────────────────
    print("\n-- checks --")
    fails = []

    def chk(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + "  " + str(detail))
        if not cond:
            fails.append(name)

    cap = row.get("spend_capacity") or {}
    chk("row carries spend_capacity with deep_history",
        "deep_history" in (cap.get("src") or []), cap.get("src"))
    chk("row carries the guard's cap and the deep variant beside it",
        bool(cap.get("cap_usd")), (cap.get("cap_usd"), cap.get("cap_deep_usd")))
    chk("row carries no no_capacity", "no_capacity" not in (cap.get("src") or []))
    bl = row.get("buy_low") or {}
    chk("row carries buy_low with an explicit source",
        bl.get("cheap") is not None and bl.get("src"), (bl.get("cheap"), bl.get("src")))
    blr = row.get("buy_low_rank") or {}
    chk("row carries the buy-low shadow record, flag OFF and nothing applied",
        blr.get("on") is False and blr.get("applied") is False,
        (blr.get("on"), blr.get("applied")))
    _cmp = blr.get("compared") or []
    chk("it names TWO OR MORE real candidates it compared",
        isinstance(_cmp, list) and len(_cmp) >= 2,
        [(c.get("pair"), c.get("cheap"), c.get("conf"), c.get("src")) for c in _cmp])
    chk("every compared candidate carries pair/cheap/conf/src",
        all(c.get("pair") and c.get("cheap") is not None
            and c.get("conf") is not None and c.get("src") for c in _cmp),
        _cmp)
    chk("it states its preference, the margin and the threshold",
        (blr.get("cheapest") or {}).get("pair") and blr.get("gain_cheap") is not None
        and blr.get("min_gain") is not None,
        {k: blr.get(k) for k in ("cheapest", "gain_cheap", "min_gain")})
    chk("it states explicitly whether the bracket would change",
        isinstance(blr.get("would_change"), bool)
        and blr.get("would") in ("prefer" if blr["would_change"] else "hold", "hold"),
        {k: blr.get(k) for k in ("would", "would_change", "to", "gain")})
    chk("spend_rank is recorded too, and off",
        (row.get("spend_rank") or {}).get("on") is False,
        (row.get("spend_rank") or {}).get("would"))
    fam = bs._family_fields(row)
    chk("the journal's one writer carries all of it",
        fam.get("buy_low") is not None and fam.get("buy_low_rank") is not None
        and fam.get("spend_capacity") is not None
        and fam.get("buy_low_v") == buy_low.BUY_LOW_STAMP_VERSION,
        (fam.get("buy_low_v"), fam.get("spend_v")))

    bal_after = None
    try:
        import contest_account as ca
        bal_after = (ca.load_state() or {}).get("current_balance")
    except Exception:
        pass
    chk("no real balance moved", bal_before == bal_after, (bal_before, bal_after))
    live = json.loads((ROOT / "contest_watch.json").read_text(encoding="utf-8"))
    chk("the live watch file was not touched by the probe",
        best_pair not in live or live.get(best_pair) is None,
        sorted(k for k, v in live.items() if v)[:4])
    print(f"\nRESULT: {'PASS' if not fails else 'FAIL'} "
          f"({len(fails)} failed)")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
