"""diag_extfade.py - THE EXTENSION-FADE HINDSIGHT STUDY (Sep 19).

WHY THIS EXISTS: the proposal on the table is a fade gate - deny or
shrink a fire when the coin is over-extended at fire time (stretched
above its EMA20 / big 24h run-up) because "extended buys fade." Before
wiring any such gate, this study grades the idea's hindsight on the
deep 1h stores: every REAL popup fire (Sep 12 -> today, from the dated
scanner log rotations), its extension features at fire time, and what
the trade actually did in its 4h contest window - per extension band.

If the fade shape pays (extended fires LOSE money), the gate has a
case. If extended fires are the fleet's best trades (momentum), the
gate is a money-burner and stays unwired. Either way the wiring
decision gets data instead of instinct.

Convention (same as the fleet's other hindsight studies): $1,000
position, forward 4h window (the contest rotation), end-to-end close
and MFE >= +3% = "ran". Pure core = grader + band verdicts so the
study is pinnable hermetically and the monthly driver can reuse it.

Run: .venv\\Scripts\\python.exe -X utf8 diag_extfade.py
Writes _extfade.txt. Read-only on ledgers; the fires are parsed from
the dated scanner.log.2026-09-* rotations (no writes to them).
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
OUT = ROOT / "_extfade.txt"
OUT_JSON = ROOT / "_extfade_rows.json"

POS_USD = 1000.0          # the $1k-position standard
H = 4                     # contest rotation window (hours)
RAN_MFE_PCT = 3.0         # MFE >= +3% = the trade "ran"
LOG_GLOB = "scanner.log.2026-09-*"
LOG_TODAY = "scanner.log"

# fire-line formats: the desktop BIG ALERT record, both dash styles
LINE = re.compile(
    r"(\d{1,2}):(\d{2}) (AM|PM)\s+\[\w+\]\s+(?:🚀 )?"
    r"BIG ALERT: BUY (\S+) - (\d+)% - entry \$([0-9.]+)")
LINE2 = re.compile(
    r"(\d{1,2}):(\d{2}) (AM|PM)\s+\[\w+\]\s+🚀 "
    r"BIG ALERT: BUY (\S+) — (\d+)% — entry \$([0-9.]+)")


# ---------------------------------------------------------------- fires
def harvest_fires(log_glob: str = LOG_GLOB,
                  today_log: str | None = LOG_TODAY) -> list[dict]:
    """Parse dated scanner-log rotations into (pair, ts, conf) fires.

    Wall-clock times are -06:00 (the fleet's fixed MST7MDT offset).
    Deduped to (pair, hour) so a re-armed fire inside the same hour
    prices once. Today's fires ride the live scanner.log only if a
    date is recoverable from its name pattern; otherwise the dated
    rotations are the record (the popup ledger holds the newest).
    """
    fires: list[dict] = []
    for f in sorted(glob.glob(log_glob)) + ([today_log] if today_log else []):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})$", f)
        if not m:
            continue
        y, mo, d = (int(g) for g in m.groups())
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            mm = LINE.search(line) or LINE2.search(line)
            if not mm:
                continue
            hh, mi, ap, asset, conf, px = mm.groups()
            h12 = int(hh) % 12 + (12 if ap == "PM" else 0)
            fires.append({
                "pair": asset if "/" in asset else asset + "/USD",
                "conf": int(conf),
                "px": float(px),
                "ts": pd.Timestamp(f"{y:04d}-{mo:02d}-{d:02d} {h12:02d}:{mi}:00"),
            })
    seen: set[tuple] = set()
    ded: list[dict] = []
    for r in fires:
        k = (r["pair"], int(r["ts"].timestamp() // 3600))
        if k in seen:
            continue
        seen.add(k)
        ded.append(r)
    return ded


# ------------------------------------------------------- fire features
def grade_fire(df: pd.DataFrame, ts: pd.Timestamp) -> dict | None:
    """Extension features + forward outcome for one fire hour.

    df: deep 1h store (utc DatetimeIndex, OHLCV), ts: naive fire time
    in fleet-local wall clock. Returns None when ungradeable (short
    history or the forward window still open).
    """
    if df.index.tz is not None:
        idx = df.index.tz_convert("MST7MDT").tz_localize(None)
    else:
        idx = df.index
    pos = int(idx.searchsorted(ts, side="right")) - 1
    if pos < 60 or pos + H >= len(df):
        return None
    fwd = df.iloc[pos + 1:pos + 1 + H]
    if len(fwd) < H:
        return None
    entry = float(df["close"].iloc[pos])
    ema20 = float(df["close"].iloc[:pos + 1].ewm(span=20, adjust=False).mean().iloc[-1])
    win = df["high"].iloc[pos - 48:pos + 1].max() - df["low"].iloc[pos - 48:pos + 1].min()
    return {
        "entry": entry,
        "ext": (entry / ema20 - 1.0) * 100.0,                     # stretch above EMA20, %
        "run24": (entry / float(df["close"].iloc[pos - 24]) - 1.0) * 100.0,
        "rngpos": ((entry - float(df["low"].iloc[pos - 48:pos + 1].min())) / win * 100.0) if win > 0 else 50.0,
        "end": (float(fwd["close"].iloc[-1]) / entry - 1.0) * 100.0,
        "mfe": (float(fwd["high"].max()) / entry - 1.0) * 100.0,
    }


def load_store(path: str | Path) -> pd.DataFrame | None:
    try:
        df = pd.read_feather(path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df.set_index("date").sort_index()
    except Exception:
        return None


# --------------------------------------------------------- the verdict
def band_verdict(rows: list[dict], key: str, bands: list[tuple[str, float, float]]) -> dict:
    """Price fires in each named band of a feature. Pure."""
    out: dict[str, dict] = {}
    for name, lo, hi in bands:
        sub = [r for r in rows if lo <= r[key] < hi]
        if not sub:
            continue
        ends = [POS_USD * r["end"] / 100.0 for r in sub]
        out[name] = {
            "n": len(sub),
            "won": sum(1 for r in sub if r["end"] > 0),
            "ran": sum(1 for r in sub if r["mfe"] >= RAN_MFE_PCT),
            "end_usd": round(sum(ends), 2),
            "avg_mfe": round(sum(r["mfe"] for r in sub) / len(sub), 2),
        }
    return out


def terciles(rows: list[dict], key: str) -> tuple[float, float]:
    v = sorted(r[key] for r in rows)
    t1, t2 = np.percentile(v, 33), np.percentile(v, 66)
    return float(t1), float(t2)


def main() -> None:
    fires = harvest_fires()
    out: list[str] = []
    out.append("EXTENSION-FADE HINDSIGHT (Sep 19) - were extended fires good or bad?")
    out.append(f"convention: ${POS_USD:.0f} position, {H}h window, MFE >= +{RAN_MFE_PCT:.0f}% = ran")
    out.append("")
    out.append(f"fires parsed (dated rotations, deduped to pair-hour): {len(fires)}")

    stores: dict[str, str] = {}
    for p in glob.glob(str(ROOT / "surething_data" / "kraken" / "*_USD-1h.feather")):
        stem = Path(p).stem.replace("-1h", "")
        stores[stem.replace("_", "/")] = p

    rows: list[dict] = []
    for r in fires:
        path = stores.get(r["pair"])
        if path is None:
            continue
        df = load_store(path)
        if df is None:
            continue
        g = grade_fire(df, r["ts"])
        if g is None:
            continue
        g.update(pair=r["pair"], conf=r["conf"], ts=str(r["ts"]))
        rows.append(g)

    out.append(f"fires graded on the deep store (closed 4h windows): {len(rows)}")
    out.append("")

    if rows:
        t1e, t2e = terciles(rows, "ext")
        out.append(f"EXT (stretch above EMA20) terciles: <= {t1e:.1f}% | {t1e:.1f}-{t2e:.1f}% | > {t2e:.1f}%")
        ev = band_verdict(rows, "ext", [
            ("LOW stretch", -1e9, t1e),
            ("MID stretch", t1e, t2e),
            ("HIGH stretch (fade shape)", t2e, 1e9),
        ])
        for name in ("LOW stretch", "MID stretch", "HIGH stretch (fade shape)"):
            b = ev.get(name)
            if not b:
                continue
            out.append(f"  {name}: n={b['n']} won={b['won']} ({b['won'] / b['n'] * 100:.0f}%) "
                       f"ran3%={b['ran']} end$ {b['end_usd']:+,.2f} avg MFE {b['avg_mfe']:+.2f}%")
        out.append("")

        t1r, t2r = terciles(rows, "run24")
        out.append(f"RUN24 (24h run-up) terciles: <= {t1r:.1f}% | {t1r:.1f}-{t2r:.1f}% | > {t2r:.1f}%")
        rv = band_verdict(rows, "run24", [
            ("LOW run", -1e9, t1r),
            ("MID run", t1r, t2r),
            ("HIGH run (fade shape)", t2r, 1e9),
        ])
        for name in ("LOW run", "MID run", "HIGH run (fade shape)"):
            b = rv.get(name)
            if not b:
                continue
            out.append(f"  {name}: n={b['n']} won={b['won']} ({b['won'] / b['n'] * 100:.0f}%) "
                       f"end$ {b['end_usd']:+,.2f} avg MFE {b['avg_mfe']:+.2f}%")
        out.append("")

        strict = [r for r in rows if r["ext"] > t2e and r["run24"] > t2r]
        if strict:
            ends = [POS_USD * r["end"] / 100.0 for r in strict]
            won = sum(1 for r in strict if r["end"] > 0)
            out.append(f"STRICT fade (both terciles): n={len(strict)} won={won} "
                       f"({won / len(strict) * 100:.0f}%) end$ {sum(ends):+,.2f} "
                       f"avg MFE {sum(r['mfe'] for r in strict) / len(strict):+.2f}%")
        else:
            out.append("STRICT fade (both terciles): no fires qualify")
        out.append("")

        worst = sorted(rows, key=lambda r: POS_USD * r["end"] / 100.0)
        out.append("worst 5 fires:")
        for r in worst[:5]:
            out.append(f"  {r['ts'][:16]} {r['pair']:10s} conf {r['conf']:>3d}% "
                       f"ext {r['ext']:+.1f}% run24 {r['run24']:+.1f}% end$ {POS_USD * r['end'] / 100:+,.2f}")
        out.append("")
        out.append("best 5 fires:")
        for r in sorted(rows, key=lambda r: POS_USD * r["end"] / 100.0, reverse=True)[:5]:
            out.append(f"  {r['ts'][:16]} {r['pair']:10s} conf {r['conf']:>3d}% "
                       f"ext {r['ext']:+.1f}% run24 {r['run24']:+.1f}% end$ {POS_USD * r['end'] / 100:+,.2f}")
        out.append("")
        total = sum(POS_USD * r["end"] / 100.0 for r in rows)
        won = sum(1 for r in rows if r["end"] > 0)
        out.append(f"ALL FIRES: n={len(rows)} won={won} ({won / len(rows) * 100:.0f}%) end$ {total:+,.2f}")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(rows), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
