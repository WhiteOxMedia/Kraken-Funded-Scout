"""Registry -> live entry routing for Kraken Scout.

This is the only bridge between research and live entries.

* No promoted policy -> `active=False`, `allow=True`, and the scanner runs
  byte-for-byte as before.  The file is read, but nothing is resized or gated.
* A promoted policy -> only its entry rule is allowed.  The best ranked policy
  (deterministic: chained final balance, passes, then id) is used for every
  symbol.  The contest is long-only: there are no short policies, and a stale
  short registry row is refused rather than silently turned into a buy.
* The death buffer is checked before the policy can authorize risk.  This is
  an ADDITIONAL refusal only for registry-driven entries; existing scanner
  entries keep the watch-only floor rule.
* Forward evidence is a hard gate (Nov 16): a promoted policy whose paper lane
  is under water, or that has never closed a forward trade, refuses registry
  entries.  A backtest that cleared three chained windows is not evidence that
  the policy survives contact with the market.  Every plan and the status
  plate carry the lane's net and trade count, so the number travels with the
  decision instead of living in a separate file.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pandas as pd

import edge_engine as ee
import edge_policy as ep

REGISTRY = ee.REGISTRY
# The forward paper lane's report: the only source of out-of-sample evidence.
FORWARD_REPORT = Path(__file__).resolve().parent / "edge_forward_report.json"
FORWARD_MIN_TRADES = 1      # "missing": a lane that never closed a trade
FORWARD_MIN_NET = 0.0       # "negative": under water must not route live risk
_FWD_CACHE: tuple[str, int, int, dict] | None = None
_MARKET_CACHE: tuple[float, dict] | None = None
_REG_CACHE: tuple[str, float, dict] | None = None
_RANK_CACHE: tuple[int, dict] | None = None
_ATR_CACHE: tuple[int, dict] | None = None


def _read(path: Path | None = None) -> dict:
    global _REG_CACHE
    try:
        p = path or REGISTRY
        mtime = p.stat().st_mtime_ns
        if (_REG_CACHE and _REG_CACHE[0] == str(p)
                and _REG_CACHE[1] == mtime):
            return _REG_CACHE[2]
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc = doc if isinstance(doc, dict) else {}
        _REG_CACHE = (str(p), mtime, doc)
        return doc
    except (OSError, ValueError, TypeError):
        return {}


def _rank_key(row: dict) -> tuple:
    ch = row.get("chain") or {}
    return (-(ch.get("final") or 0), -(ch.get("passes") or 0),
            str(row.get("id") or ""))


def promoted(doc: dict) -> list[dict]:
    rows = [r for r in (doc.get("candidates") or {}).values()
            if isinstance(r, dict) and r.get("status") == "promoted"]
    rows.sort(key=_rank_key)
    return rows


def select(doc: dict) -> dict | None:
    rows = promoted(doc)
    return rows[0] if rows else None


def forward_evidence(cid: str, path: Path | None = None) -> dict | None:
    """The paper lane's record for one candidate: net, trades, wins.

    Reads `edge_forward_report.json` in either shape (`rows` from the current
    summary, or the older `candidates` map).  A missing file or a candidate
    with no row returns None - no evidence is not the same as good evidence.
    """
    global _FWD_CACHE
    try:
        p = path or FORWARD_REPORT
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        if _FWD_CACHE and _FWD_CACHE[:3] == key:
            rows = _FWD_CACHE[3]
        else:
            doc = json.loads(p.read_text(encoding="utf-8"))
            rows: dict = {}
            if isinstance(doc, dict):
                for r in doc.get("rows") or []:
                    if isinstance(r, dict) and r.get("id"):
                        rows[r["id"]] = r
                for cid_, r in (doc.get("candidates") or {}).items():
                    if cid_ not in rows and isinstance(r, dict):
                        rows[cid_] = r
            _FWD_CACHE = (*key, rows)
    except (OSError, ValueError, TypeError):
        return None
    row = rows.get(cid)
    if not isinstance(row, dict):
        return None
    try:
        trades = int(row.get("trades") or 0)
        net = float(row.get("net") or 0.0)
    except (TypeError, ValueError):
        return None
    equity = row.get("equity")
    base = None
    try:
        if equity is not None:
            base = float(equity) - net
    except (TypeError, ValueError):
        base = None
    return {
        "net": round(net, 2), "trades": trades,
        "wins": int(row.get("wins") or 0),
        "scratch": int(row.get("scratch") or 0),
        "equity": equity,
        "net_pct": round(net / base * 100.0, 2) if base else None,
    }


def forward_verdict(ev: dict | None) -> str | None:
    """None when the paper lane supports routing, else why it does not."""
    if not ev or int(ev.get("trades") or 0) < FORWARD_MIN_TRADES:
        return ("no forward paper evidence for this policy "
                f"(needs >= {FORWARD_MIN_TRADES} closed trade(s)) - registry "
                "entry refused")
    if float(ev.get("net") or 0.0) < FORWARD_MIN_NET:
        pct = ev.get("net_pct")
        tail = f" ({pct:+.2f}% of its start)" if isinstance(pct, (int, float)) else ""
        return (f"forward paper lane is {float(ev['net']):+.2f}{tail} over "
                f"{int(ev.get('trades') or 0)} trades, "
                f"{int(ev.get('wins') or 0)} wins - registry entry refused")
    return None


def admit(spec: dict, f: dict) -> bool:
    """Pure policy rule check used by tests and the live gate."""
    pol = ep.policy_from_spec(spec)
    if pol is None:
        return False
    try:
        return bool(pol.rule(f))
    except Exception:
        return False


def features_from_df(pair: str, df: pd.DataFrame,
                     market: dict | None = None) -> dict | None:
    """Features for the latest CLOSED 1h bar, using market breadth if present."""
    if df is None or len(df) < 210:
        return None
    df = df.iloc[:-1] if len(df) > 2 else df
    try:
        global _RANK_CACHE
        fa = ee.frame_features(df, ee.atr14(df))
        mk = market or {pair: df}
        if _RANK_CACHE is None or _RANK_CACHE[0] != id(mk):
            _RANK_CACHE = (id(mk), ee._panel_ranks(mk))
        ranks = _RANK_CACHE[1]
        i = len(df) - 1
        f = ee.features_at(fa, ranks, df.index[i], i)
        _fill_live_atr_rank(f, df.index[i], mk)
        return f
    except Exception:
        return None


def _fill_live_atr_rank(f: dict, ts, market: dict) -> None:
    global _ATR_CACHE
    if _ATR_CACHE is None or _ATR_CACHE[0] != id(market):
        _ATR_CACHE = (id(market),
                      {p: ee.atr14(d) for p, d in market.items()})
    atrs = _ATR_CACHE[1]
    vals = []
    for p, d in market.items():
        try:
            if ts not in d.index:
                continue
            i = d.index.get_loc(ts)
            if isinstance(i, slice):
                continue
            a = atrs[p].iloc[int(i)]
            c = float(d["close"].iloc[int(i)])
            if c > 0 and math.isfinite(float(a)):
                vals.append(float(a) / c)
        except Exception:
            continue
    if len(vals) < 3:
        return
    v = f.get("atr_pct")
    if not math.isfinite(float(v)):
        return
    lo = sum(1 for x in vals if x < v)
    eq = sum(1 for x in vals if x == v)
    f["atr_rank"] = (lo + 0.5 * eq) / len(vals)


def market_pairs() -> dict:
    global _MARKET_CACHE
    now = time.time()
    if _MARKET_CACHE and now - _MARKET_CACHE[0] < 300:
        return _MARKET_CACHE[1]
    pairs = ee.load_pairs()
    _MARKET_CACHE = (now, pairs)
    return pairs


def _buffer(account: dict | None) -> float | None:
    if not isinstance(account, dict):
        return None
    try:
        start = float(account.get("start_balance") or 0.0)
        bal = float(account.get("current_balance") or 0.0)
        if start <= 0 or bal <= 0:
            return None
        return bal - start * 0.97
    except (TypeError, ValueError):
        return None


def live_plan(pair: str, signal=None, *, exchange=None, cfg=None,
              features: dict | None = None, account: dict | None = None,
              registry_path: Path | None = None,
              df: pd.DataFrame | None = None,
              forward_path: Path | None = None) -> dict:
    """The scanner's one registry decision.

    `features` exists for deterministic tests.  In the scanner it is omitted
    and the latest closed 1h bar is read from the deep-read frame or exchange.
    """
    doc = _read(registry_path)
    row = select(doc)
    if row is None:
        return {"active": False, "allow": True, "size_frac": None,
                "policy": None, "forward": None,
                "reason": "no promoted policy"}
    spec = row.get("spec") or {}
    fwd = forward_evidence(row.get("id"), forward_path)
    pol = ep.policy_from_spec(spec)
    if pol is None:
        if str(spec.get("side") or "") == "short":
            reason = "shorting is disabled on this long-only contest"
        else:
            reason = "promoted row has no executable spec"
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "forward": fwd, "reason": reason}
    if pol.side != "long":
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd,
                "reason": "shorting is disabled on this long-only contest"}
    ch = row.get("chain") or {}
    if int(ch.get("passes") or 0) < 3 or ch.get("died"):
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd,
                "reason": "promoted row lacks three clean chained-window passes"}
    refusal = forward_verdict(fwd)
    if refusal:
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd, "reason": refusal}

    buf = _buffer(account)
    if buf is not None and buf <= 0.0:
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd,
                "reason": "death-line buffer is exhausted — registry entry refused"}

    if features is None:
        try:
            if df is None and exchange is not None:
                ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=260)
                if ohl:
                    df = pd.DataFrame(
                        ohl, columns=["timestamp", "open", "high", "low",
                                      "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
            if df is not None:
                features = features_from_df(pair, df, market_pairs())
        except Exception:
            features = None
    if features is None:
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd,
                "reason": "policy feature read unavailable"}
    if not admit(pol.spec, features):
        return {"active": True, "allow": False, "size_frac": None,
                "policy": row.get("id"), "policy_key": pol.id,
                "forward": fwd,
                "reason": "promoted policy does not match this signal"}
    return {"active": True, "allow": True, "size_frac": pol.size_frac,
            "policy": row.get("id"), "policy_key": pol.id, "side": pol.side,
            "forward": fwd, "reason": "promoted policy matched"}


def status(registry_path: Path | None = None,
           forward_path: Path | None = None) -> dict:
    """Who would route live risk, and what the paper lane says about them."""
    doc = _read(registry_path)
    row = select(doc)
    fwd = forward_evidence(row.get("id"), forward_path) if row else None
    refusal = forward_verdict(fwd) if row else None
    return {"active": row.get("id") if row else None,
            "status": row.get("status") if row else None,
            "label": row.get("label") if row else None,
            "spec": row.get("spec") if row else None,
            "chain": row.get("chain") if row else None,
            "forward": fwd,
            "forward_ok": bool(row) and refusal is None,
            "forward_refusal": refusal,
            "forward_bar": {"min_trades": FORWARD_MIN_TRADES,
                            "min_net": FORWARD_MIN_NET},
            "promoted_rows": [
                {"id": r.get("id"),
                 "chain_final": (r.get("chain") or {}).get("final"),
                 "forward": forward_evidence(r.get("id"), forward_path)}
                for r in promoted(doc)],
            "promoted": len(promoted(doc)),
            "updated_at": doc.get("updated_at")}
