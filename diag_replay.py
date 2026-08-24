"""Replay test: would sub-bar signals have paid the $20 scalp?
Cohorts: sub-bar vs passed-bar. Price from the engine's 1h candle cache."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
week_ago = now - timedelta(days=7)
lines = [f"replay run: {now.strftime('%a %I:%M %p')} Denver", ""]

# ── 1. signal pools available ──────────────────────────────────────
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, entry_price "
    "FROM signals WHERE action='buy' AND entry_price IS NOT NULL "
    "ORDER BY timestamp").fetchall()
conn.close()
lines.append(f"bridge signals total (any date): {len(rows)}")
by_day = {}
for r in rows:
    d = r["timestamp"][:10]
    by_day[d] = by_day.get(d, 0) + 1
for d in sorted(by_day):
    lines.append(f"  {d}: {by_day[d]}")

# ── 2. scout DB tournament winners (the deeper archive) ────────────
winners = []
try:
    import db as scout_db
    with scout_db.get_session() as s:
        ws = scout_db.get_tournament_winners(limit=400)
        for w in ws:
            t = w.timestamp
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC"))
            t = t.astimezone(MST)
            if t >= week_ago and w.entry_price:
                winners.append({
                    "ts": t, "pair": w.pair, "conf": float(w.confidence or 0),
                    "entry": float(w.entry_price), "action": w.action,
                })
    lines.append(f"")
    lines.append(f"tournament winners (past 7 days, with entry): {len(winners)}")
    wdays = {}
    for w in winners:
        d = w["ts"].strftime("%a %m/%d")
        wdays[d] = wdays.get(d, 0) + 1
    for d in sorted(wdays):
        lines.append(f"  {d}: {wdays[d]}")
except Exception as e:
    lines.append(f"winners query failed: {e}")

# ── 3. bars per rotation (current engine data) ─────────────────────
from background_scanner import _schedule_for_dow, ROTATIONS, rotation_key_for_hour
from volatility_engine import get_confidence_bar, load_rotation_thresholds
ROT_KEYS = [r[2] for r in ROTATIONS]
bars = {}
for sched in ("weekday", "weekend"):
    for rk in ROT_KEYS:
        try:
            bars[(rk, sched)] = int(get_confidence_bar(None, rk, sched) or 65)
        except Exception:
            bars[(rk, sched)] = 65
lines.append("")
lines.append(f"current rotation median bars: " + ", ".join(
    f"{rk}{'wd' if s_=='weekday' else 'we'}={bars[(rk,s_)]}" for rk, s_ in bars if s_ == "weekday"))

# ── 4. candle cache ────────────────────────────────────────────────
cc = sqlite3.connect(BASE / "ohlcv_cache.db")
cc.row_factory = sqlite3.Row
candles = {}
for r in cc.execute("SELECT pair, ts, high FROM ohlcv"):
    candles.setdefault(r["pair"], []).append((int(r["ts"]), float(r["high"])))
for p in candles:
    candles[p].sort()
cc.close()
lines.append(f"")
lines.append(f"candle cache pairs: {len(candles)}")

# ── 5. replay ──────────────────────────────────────────────────────
def replay(sig):
    """$20 hit? (entry +0.4% within min(pace cap, to midnight))."""
    t, pair, entry = sig["ts"], sig["pair"], sig["entry"]
    if entry <= 0 or pair not in candles:
        return None
    target = entry * 1.004
    t_ms = int(t.timestamp() * 1000)
    end_mid = t.replace(hour=0, minute=0, second=0, microsecond=0)
    if end_mid <= t:
        end_mid += timedelta(days=1)
    hrs_to_mid = (end_mid - t).total_seconds() / 3600
    rk = rotation_key_for_hour(t.hour)
    sched = _schedule_for_dow(t.weekday())
    # pace cap from engine median avg_move of that rotation
    cap = 4.0
    try:
        doc = load_rotation_thresholds(max_age_s=3600) or {}
        cell = doc.get("median", {}).get(sched, {}).get(rk) or {}
        avg = float(cell.get("avg_move") or 0)
        cap = 6.0 if avg >= 1.0 else (5.0 if avg >= 0.8 else 4.0)
    except Exception:
        pass
    window_ms = int(min(cap, hrs_to_mid) * 3600 * 1000)
    hit24 = None
    hit = None
    for ts, high in candles[pair]:
        if ts < t_ms:
            continue
        if hit is None and ts <= t_ms + window_ms and high >= target:
            hit = (ts - t_ms) / 3600000
        if hit24 is None and ts <= t_ms + 24 * 3600 * 1000 and high >= target:
            hit24 = (ts - t_ms) / 3600000
        if hit is not None and hit24 is not None:
            break
    return {"bar": bars.get((rk, sched), 65), "hit": hit, "hit24": hit24}

def cohort_report(name, sigs):
    got = [(s, replay(s)) for s in sigs]
    got = [(s, r) for s, r in got if r]
    if not got:
        lines.append(f"  {name}: no replayable signals")
        return
    n = len(got)
    hits = sum(1 for _, r in got if r["hit"] is not None)
    hits24 = sum(1 for _, r in got if r["hit24"] is not None)
    lines.append(f"  {name}: n={n}  $20-within-window: {hits} ({hits/n:.0%})  "
                 f"within-24h: {hits24} ({hits24/n:.0%})")

# combine pools (bridge buys + winners), dedupe pair+hour
pool = []
for r in rows:
    try:
        t = datetime.fromisoformat(r["timestamp"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=MST)
        t = t.astimezone(MST)
    except Exception:
        continue
    if t < week_ago or t.hour >= 20:
        continue
    pool.append({"ts": t, "pair": r["pair"], "conf": float(r["confidence"] or 0),
                 "entry": float(r["entry_price"])})
pool.extend(winners)
seen = set()
deduped = []
for s in sorted(pool, key=lambda x: x["ts"]):
    k = (s["pair"], s["ts"].strftime("%m%d%H"))
    if k in seen:
        continue
    seen.add(k)
    deduped.append(s)
lines.append("")
lines.append(f"replayable pool (7d, buys, pre-cooldown, deduped): {len(deduped)}")

sub = [s for s in deduped if replay(s) and s["conf"] < replay(s)["bar"]]
passed = [s for s in deduped if replay(s) and s["conf"] >= replay(s)["bar"]]
lines.append(f"sub-bar cohort: {len(sub)}   passed-bar cohort: {len(passed)}")
lines.append("")
cohort_report("PASSED the bar (old gate said GO)", passed)
cohort_report("SUB-BAR (old gate said NO)", sub)

# by confidence band
lines.append("")
lines.append("sub-bar hit rate by confidence band:")
for lo, hi in [(50, 60), (60, 70), (70, 80), (80, 90)]:
    band = [s for s in sub if lo <= s["conf"] < hi]
    if band:
        cohort_report(f"  {lo}-{hi}%", band)

# the would-have-paid list (sub-bar winners)
lines.append("")
lines.append("sub-bar signals that WOULD have paid $20 (top 15 by speed):")
paid = [(s, replay(s)) for s in sub]
paid = [(s, r) for s, r in paid if r and r["hit"] is not None]
paid.sort(key=lambda x: x[1]["hit"])
for s, r in paid[:15]:
    lines.append(f"  {s['ts'].strftime('%a %H:%M')} {s['pair']:<12} {s['conf']:.0f}% (bar {r['bar']}) "
                 f"$20 in {r['hit']:.1f}h")

out = BASE / "replay_report.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
