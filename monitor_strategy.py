"""monitor_strategy.py — THE PRE-REGISTERED 170-CLOSE MONITOR (STRATEGY_MVP.md §4).

The pre-registration, verbatim from scout/engine/STRATEGY_MVP.md:

  Metric   mean pnl_net per close (fees included), H0: mu <= 0 vs H1: mu > 0
  Sample   N = 170 closes; ONE interim look at n = 85
  Bounds   O'Brien-Fleming, two-sided alpha = 0.05:
           |z| >= 2.96 at the interim, |z| >= 1.98 at the final
  Report   (1) mean + 20,000-resample bootstrap percentile 95% CI
           (2) t against zero
           (3) expectancy excluding the three best closes
           (4) largest pair's share of net
  Decision edge exists      : final lower 95% bound > 0
           worth the budget : point >= +$2.00 AND lower bound > 0
                              AND exp-without-top-3 > 0 AND top-pair share < 40%
           kill              : at any look, cumulative net <= -4% of balance,
                              or (final) z < 1.98

WHY A SEQUENTIAL MONITOR AND NOT A GROUP-BY: reading the report at 30 closes
and again at 90 and again at 140 is how "it looked great" becomes a rule. The
look schedule is fixed here, and this script refuses to render an edge verdict
off-schedule.

TWO COHORTS, never mixed:

  TEST     closes carrying `strategy_v` (the coded strategy's own stamp). This
           is the only cohort the pre-registration judges. It is empty until
           the strategy ships — an empty test is the honest starting state.

  HISTORY  closes that match the strategy's *shape* — full size (not min-size),
           fast route, no re-entry, climb PASS — from --since onward. These are
           the fires that came before the rule was coded; they are useful for a
           sanity read and are explicitly NOT the test. Reported separately and
           labelled, never merged into the z.

Usage:
  python monitor_strategy.py                     # both cohorts, current ledger
  python monitor_strategy.py --trades PATH
  python monitor_strategy.py --json              # machine-readable
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import time
import random
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOOKS_FILE = HERE / "monitor_strategy_looks.json"

Z_INTERIM = 2.96
Z_FINAL = 1.98
N_INTERIM = 85
N_FINAL = 170
BOOT = 20000
POWER_EFFECT = 4.00          # the +$4/trade design point the n=170 buys
WORTH_POINT = 2.00
TOP_PAIR_MAX = 0.40
KILL_FRAC = 0.04


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            rows.append(doc)
    rows.sort(key=lambda r: str(r.get("sold_at") or ""))
    return rows


def shape_ok(r: dict) -> bool:
    """The strategy's shape: the FULL fire only, fast route, no re-entry, climb PASS."""
    s = r.get("setup")
    if not isinstance(s, dict) or not s:
        return False
    if s.get("min_size"):
        return False
    if s.get("reentry") or s.get("hail"):
        return False
    if str(s.get("route") or "") != "champion-fast":
        return False
    if str(s.get("climb") or "") != "PASS":
        return False
    return True


def cohorts(rows: list[dict], since: str, strategy_v: int) -> tuple[list, list]:
    """TEST = generation stamp AND the strategy's cuts.

    `strategy_v` alone does NOT identify this strategy: the close writers stamp
    STRATEGY_VERSION on every fire (family_fields falls back to the constant),
    so it marks which rule generation wrote the row, not which strategy fired.
    Counting it alone would pour every ordinary fire into the judged cohort.
    """
    test = [r for r in rows
            if r.get("strategy_v") is not None
            and int(_f(r.get("strategy_v"), -1)) == strategy_v
            and str(r.get("sold_at") or "")[:10] >= since
            and shape_ok(r)]
    hist = [r for r in rows
            if r not in test
            and str(r.get("sold_at") or "")[:10] >= since
            and shape_ok(r)]
    return test, hist


def bootstrap_ci(vals: list[float], n_boot: int = BOOT, seed: int = 20261106):
    """Percentile 95% CI of the mean. Seeded: the same ledger gives the same CI."""
    n = len(vals)
    if n < 2:
        return (float(vals[0]) if vals else 0.0, float(vals[0]) if vals else 0.0)
    rnd = random.Random(seed)
    means = []
    for _ in range(n_boot):
        means.append(sum(vals[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    return lo, hi


def stats(vals: list[float], rows: list[dict]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    z = mean / se if se else 0.0
    lo, hi = bootstrap_ci(vals)
    srt = sorted(vals, reverse=True)
    ex3 = srt[3:] if n > 3 else []
    per_pair: dict[str, float] = {}
    for r, v in zip(rows, vals):
        per_pair[str(r.get("pair"))] = per_pair.get(str(r.get("pair")), 0.0) + v
    top_pair, top_net = max(per_pair.items(), key=lambda kv: kv[1]) if per_pair else ("-", 0.0)
    total = sum(vals)
    share = (top_net / total) if total > 0 else float("nan")
    return {
        "n": n, "mean": mean, "sd": sd, "se": se, "z": z, "ci": (lo, hi),
        "win": 100.0 * sum(1 for v in vals if v > 0) / n,
        "net": total,
        "ex3_n": len(ex3), "ex3_mean": statistics.mean(ex3) if ex3 else float("nan"),
        "top_pair": top_pair, "top_pair_net": top_net, "top_share": share,
        "pairs": len(per_pair),
    }


def _cohort_key(since: str, strategy_v: int) -> str:
    return f"since={since}|strategy_v={strategy_v}"


def load_looks() -> dict:
    """Missing means no looks yet. Corrupt does NOT mean missing."""
    if not LOOKS_FILE.exists():
        return {}
    try:
        doc = json.loads(LOOKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("look record %s is unreadable (%s) — refusing to take a look; "
                         "repair it, or delete it deliberately and say so"
                         % (LOOKS_FILE.name, exc))
    if not isinstance(doc, dict):
        raise SystemExit("look record %s is not an object — refusing to take a look"
                         % LOOKS_FILE.name)
    return doc


def _take_lock():
    lock = LOOKS_FILE.with_suffix(".lock")
    for _ in range(40):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            time.sleep(0.05)
            continue
        try:
            os.write(fd, ("pid=%d at=%s\n" % (os.getpid(),
                        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))).encode())
        except OSError:
            # A lock we cannot annotate must not outlive this attempt.
            os.close(fd)
            try:
                lock.unlink()
            except OSError:
                pass
            raise
        os.close(fd)
        return lock
    # Never steal the lock: a paused writer is indistinguishable from a dead one,
    # and taking its lock could let two runs record different looks. Say who holds
    # it instead, so a genuinely abandoned lock is one deliberate delete away.
    try:
        holder = lock.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        holder = "(unreadable)"
    raise SystemExit("could not lock %s — held by %s. If that process is gone, "
                     "delete the lock file deliberately." % (lock.name, holder or "(empty)"))


def save_looks(doc: dict) -> None:
    tmp = LOOKS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, allow_nan=False, default=str), encoding="utf-8")
    tmp.replace(LOOKS_FILE)


def _num(v):
    """NaN is not JSON; a missing ratio is null."""
    try:
        return None if v is None or v != v else v
    except TypeError:
        return v


def record_look(key: str, which: str, st: dict, lines: list[str]) -> None:
    lock = _take_lock()
    try:
        _record_look_locked(key, which, st, lines)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _record_look_locked(key: str, which: str, st: dict, lines: list[str]) -> None:
    doc = load_looks()
    if which in doc.get(key, {}):
        return  # someone else recorded it first; the first one stands
    doc.setdefault(key, {})[which] = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n": st.get("n"), "mean": st.get("mean"), "z": st.get("z"),
        "net": st.get("net"), "ci": list(st.get("ci") or ()),
        "ex3_mean": st.get("ex3_mean"), "top_share": _num(st.get("top_share")),
        "verdict": lines,
    }
    save_looks(doc)


def look_of(n: int) -> str:
    if n < N_INTERIM:
        return f"NO LOOK — {n}/{N_INTERIM} to the interim"
    if n < N_FINAL:
        return "INTERIM LOOK (bar |z| >= %.2f)" % Z_INTERIM
    return "FINAL LOOK (bar |z| >= %.2f)" % Z_FINAL


def verdict(st: dict, balance: float, interim: bool = False,
            decide: bool = True) -> list[str]:
    out: list[str] = []
    if not decide:
        return ["no new decision — the final look is on record (replayed above)"]
    n = st["n"]
    if n == 0:
        return ["no closes in this cohort yet — nothing to read"]
    kill_net = -KILL_FRAC * balance
    if st["net"] <= kill_net:
        out.append(f"KILL — cumulative net ${st['net']:.2f} <= -4% of balance (${kill_net:.2f})")
        return out
    if n < N_INTERIM:
        out.append("verdict withheld: the pre-registration allows no look before n = %d" % N_INTERIM)
        return out
    if n < N_FINAL and not interim:
        # THE SCHEDULE IS THE TEST. Reading the numbers at n=120 and again at
        # n=150 and stopping when they look good is the trap this monitor
        # exists to close. One interim look, then the final; nothing between.
        out.append("report only — no decision off-schedule. Pass --interim ONCE, "
                   "at the first read past n=%d, to consume the registered look" % N_INTERIM)
        return out
    bar = Z_INTERIM if n < N_FINAL else Z_FINAL
    if abs(st["z"]) < bar:
        out.append("no decision — |z| %.2f below the %.2f bar at this look" % (st["z"], bar))
    elif st["z"] < 0:
        out.append("look is NEGATIVE beyond the bar (|z| %.2f) — the program is losing, not noise" % st["z"])
    else:
        if n >= N_FINAL:
            out.append("final look is POSITIVE beyond the bar (|z| %.2f)" % st["z"])
        else:
            out.append("interim look is POSITIVE beyond the bar (|z| %.2f) — the test is "
                       "unchanged until the final" % st["z"])
    if n >= N_FINAL and st["z"] < Z_FINAL:
        out.append("KILL — final z %.2f < %.2f: the pre-registered test failed"
                   % (st["z"], Z_FINAL))
        return out
    if n >= N_FINAL:
        checks = {
            "edge exists (lower 95% bound > 0)": st["ci"][0] > 0,
            "point >= +$%.2f" % WORTH_POINT: st["mean"] >= WORTH_POINT,
            "exp without top 3 > 0": (st["ex3_mean"] == st["ex3_mean"] and st["ex3_mean"] > 0),
            "top-pair share < 40%": (st["top_share"] != st["top_share"] or st["top_share"] < TOP_PAIR_MAX),
        }
        out.append("final decision inputs: " + ", ".join(
            f"{k}={'yes' if v else 'no'}" for k, v in checks.items()))
        out.append("WORTH THE RISK BUDGET" if all(checks.values())
                   else "NOT worth the budget — at least one pre-registered condition fails")
    return out


def report(label: str, rows: list[dict], balance: float, interim: bool = False,
          quiet: bool = False, decide: bool = True) -> dict:
    vals = [_f(r.get("pnl_net")) for r in rows]
    st = stats(vals, rows)
    def say(text=""):
        if not quiet:
            print(text)

    say(f"\n=== {label} ===")
    if st["n"] == 0:
        say("  n=0 — no closes in this cohort yet")
        for line in verdict(st, balance, interim, decide):
            say("  " + line)
        return st
    n = st["n"]
    say(f"  {look_of(n)}   n={n}   progress {min(n, N_FINAL)}/{N_FINAL}")
    say(f"  mean            ${st['mean']:+.2f}/trade   95% bootstrap CI "
        f"[${st['ci'][0]:+.2f}, ${st['ci'][1]:+.2f}]")
    say(f"  t (z) vs zero   {st['z']:+.2f}          sd ${st['sd']:.2f}   se ${st['se']:.2f}")
    say(f"  win rate        {st['win']:.0f}%           net ${st['net']:+.2f}")
    if st["ex3_n"]:
        say(f"  exp excl top 3  ${st['ex3_mean']:+.2f}/trade   (n={st['ex3_n']})")
    else:
        say("  exp excl top 3  n/a (fewer than 4 closes)")
    if st["top_share"] == st["top_share"]:
        say(f"  top pair        {st['top_pair']} ${st['top_pair_net']:+.2f} = "
            f"{100*st['top_share']:.0f}% of net")
    else:
        say(f"  top pair        {st['top_pair']} ${st['top_pair_net']:+.2f} = n/a (net <= 0)")
    mde = (1.96 * st["sd"] / math.sqrt(n)) if n > 1 else 0.0
    say(f"  pairs           {st['pairs']}   smallest detectable effect ~${mde:.2f}/trade at this n")
    lines = verdict(st, balance, interim, decide)
    for line in lines:
        say("  " + line)
    st = dict(st)
    st["verdict"] = lines
    return st


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default=str(HERE / "contest_trades.jsonl"))
    ap.add_argument("--since", default="2026-11-06")
    ap.add_argument("--strategy-v", type=int, default=1)
    ap.add_argument("--balance", type=float, default=None,
                    help="balance the -4%% kill is measured against")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--interim", action="store_true",
                    help="consume the ONE registered interim look (first read past n=85)")
    a = ap.parse_args(argv)

    balance = a.balance
    if balance is None:
        try:
            balance = float(json.loads((HERE / "contest_account.json").read_text()).get("start_balance") or 0.0)
        except Exception:
            balance = 0.0
    if not balance:
        balance = 10000.0

    rows = load(Path(a.trades))
    test, hist = cohorts(rows, a.since, a.strategy_v)
    q = a.json
    if not q:
        print("PRE-REGISTERED STRATEGY MONITOR — STRATEGY_MVP.md §4")
        print(f"ledger: {a.trades}   closes: {len(rows)}   since: {a.since}")
        print(f"design: N={N_FINAL}, interim n={N_INTERIM}, alpha=0.05 two-sided, "
              f"|z|>={Z_INTERIM} interim / >={Z_FINAL} final, kill at -{KILL_FRAC:.0%} of ${balance:,.0f}")
        print("cohort: strategy_v stamp AND the strategy's cuts — the stamp alone is a"
              " rule-generation marker, not a cohort selector")
    # THE LOOK RECORD: the schedule is only a schedule if a look cannot be
    # taken twice. The interim is written to disk the first time it is
    # rendered; a second --interim on the same cohort shows the recorded look
    # instead of a fresh one, so the registered alpha stays honest.
    key = _cohort_key(a.since, a.strategy_v)
    looks = load_looks().get(key, {})
    n_test = len(test)
    do_interim = False
    if a.interim and N_INTERIM <= n_test < N_FINAL:
        if "interim" in looks:
            rec = looks["interim"]
            if not q:
                print("\n=== TEST — the registered interim look was already taken ===")
                print(f"  taken {rec.get('at')} at n={rec.get('n')}  mean ${_f(rec.get('mean')):+.2f}  z {_f(rec.get('z')):+.2f}")
                for line in rec.get("verdict") or ():
                    print("  " + line)
                print("  a second interim look is not registered — the next decision is the final at n=%d" % N_FINAL)
        else:
            do_interim = True

    decide_test = True
    if n_test >= N_FINAL and "final" in looks:
        rec = looks["final"]
        decide_test = False
        if not q:
            print("\n=== TEST — the final look was already taken ===")
            print(f"  taken {rec.get('at')} at n={rec.get('n')}  mean ${_f(rec.get('mean')):+.2f}  z {_f(rec.get('z')):+.2f}")
            for line in rec.get("verdict") or ():
                print("  " + line)
            print("  the verdict is fixed; newer closes are descriptive only")

    st_test = report(f"TEST — strategy_v={a.strategy_v} and the strategy's cuts (judged)",
                     test, balance, do_interim, q, decide_test)
    if n_test and not q:
        pass
    if n_test >= N_INTERIM:
        which = "final" if n_test >= N_FINAL else ("interim" if do_interim else None)
        if which and which not in looks:
            record_look(key, which, st_test, st_test.get("verdict") or [])
    st_hist = report("HISTORY — pre-coding closes that match the strategy's shape (NOT the test)",
                     hist, balance, False, q)
    if a.json:
        def clean(st):
            return {k: (_num(v) if isinstance(v, float) else
                        ([_num(x) for x in v] if isinstance(v, tuple) else v))
                    for k, v in st.items()}
        print(json.dumps({"test": clean(st_test), "history": clean(st_hist),
                          "recorded": {k: v for k, v in
                                       load_looks().get(key, {}).items()
                                       if k in ("interim", "final")},
                          "interim_flag": a.interim, "since": a.since,
                          "ledger": a.trades}, indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
