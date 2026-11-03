"""THE PER-GATE CALIBRATION STUDY (Sep 30 ask): the month's refusals,
joined to the OHLCV cache the burial panel uses, priced as counterfactual
admissions — what would retuning TIDE-OUT and STALE-MOVE have been worth
last month?

REPRODUCIBLE FROM DISK (Oct 2, the re-audit ask): the suppression LEDGER
rotates ~daily, so the live popup_suppressions.jsonl no longer remembers
September — a re-run on Oct 2 priced ZERO refusals and the evidence went
dark (the review's own finding). The Sep-30 session archived the ledger
AT THE COMMITS IT RODE (scratch/_supp_<commit>.jsonl: five from Sep 6,
the Sep-13 month-state, an empty Sep-21 — the commit where runtime state
left version control), heavily overlapping, and COVERAGE IS WHAT THE
COMMITS CAUGHT: the study's own window (the live ledger of Sep 28-30)
rotated away before any snapshot could take it. The study reads THE
UNION: the live ledger + every archive, identical raw lines collapsed,
ts-sorted into the chronology the single ledger used to be, with each
source's sha256 and commit date in the provenance — a swept or edited
archive shows in the study's own output, the evidence-bundle manifest
pattern. Every month figure carries its day coverage — a two-day
survivor is not a month rate.

HONESTY ABOUT DRIFT: the archived Sep-30 numbers (tide-out n=8, 6
payers, med payer +$2.11/$100, med dud −$0.09; stale-move n=17) came
from that day's live ledger and that day's OHLCV cache, and neither is
recoverable — the live file rotated and the cache has since backfilled
candles the Sep-30 join could not find. This re-run therefore does NOT
pretend to byte-match them: it carries the archived verdict as a
PRE-REGISTERED claim, prints the re-derived numbers beside it, and names
the two drift sources. What reproduces is the pipeline (ledger union →
dedupe → OHLCV join → pricing) and every input it needs; the numbers are
what the surviving evidence says.

--audit [YYYY-MM]: THE RE-ARM RE-AUDIT — the valve AS BUILT (dial floor
66, 3/day, the valve's own keys: first refusal per pair per DAY, ts
order, the cap stops the day) simulated against the month's ACTUAL
refusals and priced by the same join. The valve was OFF the whole month,
so both pricings are counterfactual; this is what re-arming would have
admitted and earned.

METHOD (read-only everywhere: sqlite mode=ro, jsonl read_text):
  * candidates = DISTINCT refusals (first per pair per rotation — the
    panel's dedupe; the funnel re-records the same refusal per scan).
    The key is (pair, rotation-band) and it is GATE-BLIND by design —
    a pair-band's chronologically first refusal stands for it, on the
    single ledger the study always assumed.
  * window = the panel's: peak = max(high) within 7h after the refusal,
    entry ≈ the last close before it.
  * PRICED LIKE THE BOT (the fill model the trade would actually ride):
    entry pays half the spread, exit gives back half, the bot's 0.52%
    taker round trip comes off the top.
  * TWO PRICINGS, ON PURPOSE:
      OBSERVED EV — payers sell at their peak fill (fill_run), duds
      exit at the peak-hour close (trail_run, the data's own exit —
      when the trail would have spat the trade out). This is the
      optimistic flavor: it lets one monster payer inflate the gate.
      MEDIAN-PRICED — the panel doctrine: the gate's MEDIAN fill-
      positive payer stands for every payer, the MEDIAN dud for every
      dud. One number about the typical trade, monster-proof.
    The gap between the two IS the monster-inflation risk, printed.
  * the admission rule is mined from the data: per gate, the `quick`
    score threshold with the best observed EV (and admit-all when the
    threshold adds nothing) — a retune is a rule the scanner could
    actually run, not a wish.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone as _tzn
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "ohlcv_cache.db"
SUPP = BASE / "popup_suppressions.jsonl"
ARCH = BASE / "scratch"            # the Sep-30 per-commit ledger snapshots
ARCH_PAT = "_supp_*.jsonl"

SPREAD = 0.001          # the bot's fill model (fills.py)
FEE_RT_PCT = 0.52       # Kraken Pro taker x 2 — what the bot pays
HOURS = 7
TRADE_USD = 100.0       # the bot's minimum trade
TARGET_MONTH = 15.21    # September's realized net (the ledger)
MONTH = "2026-09"
MST = _tzn(timedelta(hours=-6))   # the journal's naive stamps are MST
FOCUS = ["tide-out", "stale-move"]

# THE VALVE AS BUILT (background_scanner.py, Oct 2) — what re-arming means
VALVE_FLOOR = 66.0
VALVE_CAP = 3

# THE ARCHIVED VERDICT (pre-registered Sep 30 — what the study printed
# from that day's live ledger and that day's cache; the re-audit starts
# from this claim and reports its own divergence, never from memory)
ARCHIVED_CLAIM = {
    "tide-out": {"n": 8, "payers": 6, "med_fill": 2.11, "med_dud": -0.09},
    "stale-move": {"n": 17},
}


def load_ledger() -> tuple[list[dict], list[str], int]:
    """Every suppression row the repo still holds: the LIVE ledger plus
    every archived _supp_*.jsonl snapshot. Overlapping snapshots repeat
    rows; identical raw lines collapse. Returns (rows ts-sorted, the
    provenance lines, the archive count)."""
    paths = sorted(ARCH.glob(ARCH_PAT))
    n_arch = len(paths)
    if SUPP.exists():
        paths.append(SUPP)
    seen: set[str] = set()
    rows: list[dict] = []
    srcs: list[str] = []
    for p in paths:
        try:
            raw = p.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            srcs.append(f"{p.name}: UNREADABLE ({e})")
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        kept = 0
        for line in raw:
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            try:
                rows.append(json.loads(line))
                kept += 1
            except ValueError:
                pass
        tag = "live" if p == SUPP else f"archive @ {_commit_date(p.stem)}"
        srcs.append(f"{p.name} [{tag}] sha256:{digest} — {kept} new rows")
    rows.sort(key=lambda r: str(r.get("ts", "")))
    return rows, srcs, n_arch


def _commit_date(stem: str) -> str:
    """The archive's commit date (the filename IS the commit hash) —
    provenance that survives without git prints 'date unknown'."""
    h = stem.replace("_supp_", "", 1)
    try:
        out = subprocess.run(
            ["git", "show", "-s", "--format=%cI", h],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return str(out.stdout.strip())[:10]
    except Exception:
        pass
    return "date unknown"


def month_rows(rows: list[dict], month: str) -> list[dict]:
    return [r for r in rows if r.get("pair")
            and str(r.get("ts", "")).startswith(month)]


def month_candidates(rows: list[dict], month: str) -> list[dict]:
    """The study's dedupe, unchanged: first per (pair, rotation-band)
    in ts order — the funnel re-records the same refusal per scan."""
    first: dict[tuple, dict] = {}
    for r in month_rows(rows, month):
        first.setdefault((str(r["pair"]), str(r.get("rot") or "day")), r)
    return list(first.values())


def price(con, r: dict) -> dict | None:
    try:
        t = datetime.fromisoformat(str(r.get("ts", "")))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=MST)
    t_ms = int(t.timestamp() * 1000)
    end_ms = t_ms + HOURS * 3_600_000
    pre = con.execute(
        "SELECT close FROM ohlcv WHERE pair=? AND ts<? "
        "ORDER BY ts DESC LIMIT 1", (r["pair"], t_ms)).fetchone()
    if not pre or not pre[0]:
        return None
    hirow = con.execute(
        "SELECT ts, MAX(high) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
        (r["pair"], t_ms, end_ms)).fetchone()
    if not hirow or not hirow[1]:
        return None
    end_close = con.execute(
        "SELECT close FROM ohlcv WHERE pair=? AND ts>? AND ts<=? "
        "ORDER BY ts ASC LIMIT 1", (r["pair"], hirow[0], end_ms)).fetchone()
    entry = float(pre[0])
    peak_hi = float(hirow[1])
    exit_px = float(end_close[0]) if end_close and end_close[0] else peak_hi
    peak = (peak_hi / entry - 1.0) * 100.0
    fill_entry = entry * (1.0 + SPREAD * 0.5)
    fill_exit = peak_hi * (1.0 - SPREAD * 0.5)
    fill = (fill_exit / fill_entry - 1.0) * 100.0 - FEE_RT_PCT
    trail_exit = exit_px * (1.0 - SPREAD * 0.5)
    trail = (trail_exit / fill_entry - 1.0) * 100.0 - FEE_RT_PCT
    return {"pair": r["pair"], "gate": str(r.get("gate") or "?"),
            "quick": (float(r["quick"]) if r.get("quick") is not None
                      else None),
            "run": peak, "fill_run": fill, "trail_run": trail,
            "fill_pos": fill > 0.0, "payer15": peak >= 1.5}


def med(vals: list[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2] if s else 0.0


def gate_book(rows: list[dict]) -> dict:
    payers = [s for s in rows if s["fill_pos"]]
    duds = [s for s in rows if not s["fill_pos"]]
    return {"n": len(rows), "payers": payers, "duds": duds,
            "rate": len(payers) / len(rows) if rows else 0.0,
            "med_fill": med([p["fill_run"] for p in payers]),
            "med_dud": med([d["trail_run"] for d in duds])}


def price_admission(book: dict, admitted: list[dict]) -> dict:
    """OBSERVED: payers at their own peak fill, duds at the peak-hour
    close. MEDIAN: the gate's typical payer and typical dud stand for
    all. The gap is the monster-inflation risk."""
    obs = sum(s["fill_run"] if s["fill_pos"] else s["trail_run"]
              for s in admitted) * TRADE_USD / 100.0
    npay = sum(1 for s in admitted if s["fill_pos"])
    ndud = len(admitted) - npay
    medp = (npay * book["med_fill"] + ndud * book["med_dud"]) \
        * TRADE_USD / 100.0
    return {"n": len(admitted), "observed": obs, "median": medp,
            "payers": npay, "duds": ndud}


def best_quick_rule(rows: list[dict]) -> tuple[float | None, list[dict]]:
    """The `quick` floor with the best observed EV; None when the
    threshold buys nothing over admit-all (quick does not separate)."""
    withq = [s for s in rows if s["quick"] is not None]
    if not withq:
        return None, rows
    base = sum(s["fill_run"] if s["fill_pos"] else s["trail_run"]
               for s in withq)
    best_x, best_ev, best_rows = None, base, withq
    for x in sorted(set(s["quick"] for s in withq)):
        adm = [s for s in withq if s["quick"] >= x]
        ev = sum(s["fill_run"] if s["fill_pos"] else s["trail_run"]
                 for s in adm)
        if ev > best_ev + 1e-9:
            best_x, best_ev, best_rows = x, ev, adm
    return best_x, best_rows


def audit_valve(con, ledger: list[dict], month: str) -> None:
    """THE RE-ARM RE-AUDIT: the valve AS BUILT — dial floor 66, 3/day,
    the valve's own keys (first refusal per pair per DAY, ts order, the
    cap stops the day) — simulated against the month's ACTUAL refusals
    and priced by the same join. The valve was OFF the whole month, so
    both pricings are counterfactual."""
    rows = [r for r in month_rows(ledger, month)
            if str(r.get("gate") or "") == "tide-out"]
    by_day: dict[str, dict[str, dict]] = {}
    in_band = 0
    for r in sorted(rows, key=lambda r: str(r.get("ts", ""))):
        q = r.get("quick")
        if q is None or float(q) < VALVE_FLOOR:
            continue
        in_band += 1
        by_day.setdefault(str(r["ts"])[:10], {}).setdefault(
            str(r["pair"]), r)
    cov = sorted({str(r["ts"])[:10] for r in rows})
    admitted: list[dict] = []
    for d in sorted(by_day):
        admitted.extend(list(by_day[d].values())[:VALVE_CAP])
    priced = [p for p in (price(con, r) for r in admitted) if p is not None]
    # the month's own tide-out book: one refusal per pair per DAY (the
    # valve's dedupe view), no floor filter — the median prices come
    # from the same population the cap would draw on
    day_first: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: str(r.get("ts", ""))):
        day_first.setdefault((str(r["pair"]), str(r["ts"])[:10]), r)
    book = gate_book([p for p in (price(con, r)
                                  for r in day_first.values())
                      if p is not None])
    p = price_admission(book, priced)
    print(f"THE RE-ARM RE-AUDIT — the valve AS BUILT "
          f"(quick >= {VALVE_FLOOR:.0f}, {VALVE_CAP}/day) against "
          f"{month}'s actual refusals:")
    print(f"  ledger coverage for {month}: {len(cov)} day(s) "
          f"({', '.join(cov)}) — the /mo figures price what survives, "
          f"NOT a month projection")
    print(f"  tide-out refusals: {len(rows)} raw, {in_band} "
          f"(pair, day) first-refusals in the study's band")
    print(f"  the {VALVE_CAP}/day cap admits {len(admitted)} across "
          f"{len(by_day)} days -> {p['n']} priced "
          f"({p['payers']} payers / {p['duds']} duds)")
    print(f"  observed EV +${p['observed']:.2f}/mo, median-priced "
          f"+${p['median']:.2f}/mo (the month's own book: {book['n']} "
          f"priced, {len(book['payers'])} payers, "
          f"med payer {book['med_fill'] * TRADE_USD / 100:+.2f}/$100, "
          f"med dud {book['med_dud'] * TRADE_USD / 100:+.2f}/$100)")
    print("  the valve was OFF all month — both pricings are "
          "counterfactual; the cap binds hardest on funnel-heavy days")
    print()


def main(argv: list[str]) -> int:
    audit = "--audit" in argv
    args = [a for a in argv if a != "--audit"]
    month = args[1] if len(args) > 1 else MONTH
    if len(month) != 7 or month[4] != "-":
        print(f"usage: pergate_calibration.py [YYYY-MM] [--audit] "
              f"(got {month!r})")
        return 1
    if not DB.exists():
        print("missing ohlcv_cache.db")
        return 1
    ledger, srcs, n_arch = load_ledger()
    if not ledger:
        print("no suppression ledger found — the live ledger and every "
              "scratch/_supp_*.jsonl archive are empty/absent")
        return 1
    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=2.0)

    print(f"ledgers read: live popup_suppressions.jsonl + {n_arch} "
          f"archives ({len(ledger)} distinct rows, identical lines "
          f"collapsed, ts-sorted):")
    for s in srcs:
        print(f"  {s}")
    print()

    cands = month_candidates(ledger, month)
    priced = [p for p in (price(con, r) for r in cands) if p is not None]
    cov = sorted({str(r["ts"])[:10] for r in month_rows(ledger, month)})

    by_gate: dict[str, list[dict]] = defaultdict(list)
    for s in priced:
        by_gate[s["gate"]].append(s)
    books = {g: gate_book(rows) for g, rows in by_gate.items()}

    print(f"{month} refusals: {len(cands)} distinct, {len(priced)} "
          f"priced (joined to OHLCV, {HOURS}h window) — ledger "
          f"coverage: {len(cov)} day(s) "
          f"({', '.join(cov) if cov else 'none'})")
    print(f"pricing: spread {SPREAD:.1%} half/side, RT fee "
          f"{FEE_RT_PCT:.2f}%, $100 trades")
    print()
    hdr = (f"{'gate':<22}{'n':>4}{'pay':>4}{'rate':>7}"
           f"{'medfill$':>10}{'meddud$':>9}{'quickmed':>9}")
    print(hdr)
    print("-" * len(hdr))
    for g, b in sorted(books.items(), key=lambda kv: -kv[1]["n"]):
        qs = sorted(float(s["quick"]) for s in by_gate[g]
                    if s["quick"] is not None)
        qm = f"{qs[len(qs)//2]:.0f}" if qs else "-"
        print(f"{g:<22}{b['n']:>4}{len(b['payers']):>4}{b['rate']:>7.0%}"
              f"{b['med_fill'] * TRADE_USD / 100:>10.2f}"
              f"{b['med_dud'] * TRADE_USD / 100:>9.2f}{qm:>9}")
    print()

    if month == MONTH:
        # ── the reproduction check (pre-registered, never from memory) ──
        a_to = ARCHIVED_CLAIM["tide-out"]
        a_sm = ARCHIVED_CLAIM["stale-move"]
        d_to = books.get("tide-out", {"n": 0, "payers": []})
        d_sm = books.get("stale-move", {"n": 0})
        match = (d_to["n"] == a_to["n"]
                 and len(d_to.get("payers", [])) == a_to["payers"]
                 and d_sm["n"] == a_sm["n"])
        print(f"REPRODUCTION CHECK (the archived Sep-30 verdict vs this "
              f"re-run): {'MATCH' if match else 'DIVERGES'}")
        print(f"  archived claim: tide-out n={a_to['n']} "
              f"({a_to['payers']} payers, med payer "
              f"+${a_to['med_fill']:.2f}/$100, med dud "
              f"{a_to['med_dud']:+.2f}), stale-move n={a_sm['n']}")
        print(f"  this re-run:    tide-out n={d_to['n']} "
              f"({len(d_to.get('payers', []))} payers), "
              f"stale-move n={d_sm['n']}")
        if not match:
            print("  drift sources (all known, none an error):")
            print("   - these archives are COMMIT-era snapshots (five "
                  "from Sep 6, the Sep-13 month-state, an empty Sep-21 "
                  "— runtime state left version control there); the "
                  "study's own window (the live ledger of Sep 28-30) "
                  "rotated away before any snapshot")
            print("   - the OHLCV cache has since backfilled candles "
                  "the Sep-30 join could not find — more candidates "
                  "price today")
            if (d_sm["n"] == a_to["n"]
                    and len(d_sm.get("payers", [])) == a_to["payers"]):
                print("   - the archived tide-out claim (n=8, 6 payers, "
                      "75%) matches THIS re-run's STALE-MOVE book "
                      "exactly, and the two n's swap 17/8 — the "
                      "archived gate labels were likely transposed")
            print("  the archived numbers are unrecoverable history; "
                  "this table is the fullest surviving evidence")
        print()

    # ── scenario A: the ceiling (context, not the answer) ───────
    ceil = sum(max(s["fill_run"], 0.0) for s in priced) \
        * TRADE_USD / 100.0

    # ── the retunes, per gate ────────────────────────────────────
    lines = []
    tot_obs = tot_med = 0.0
    for g in FOCUS:
        b = books.get(g)
        if not b or not b["n"]:
            lines.append(f"  {g}: no {month} refusals")
            continue
        x, adm = best_quick_rule(by_gate[g])
        rule = (f"admit quick>={x:.0f}" if x is not None
                else "admit ALL (quick does not separate)")
        p = price_admission(b, adm)
        tot_obs += p["observed"]
        tot_med += p["median"]
        gap = p["observed"] - p["median"]
        lines.append(
            f"  {g}: {rule} -> {p['n']} admissions "
            f"({p['payers']} payers / {p['duds']} duds)\n"
            f"      observed EV +${p['observed']:.2f}/mo, "
            f"median-priced +${p['median']:.2f}/mo"
            + (f"  (monster gap ${gap:.2f} — the mean rides "
               f"one payer)" if gap > 2.0 else ""))
    print("THE RETUNES (the rule mined from the data, priced two ways):")
    print("\n".join(lines))
    print()
    print("SCENARIOS (per month, $100 trades):")
    print(f"  A. CEILING — every refusal, perfect fills: +${ceil:.2f}"
          f"  (context only: assumes the trail sells the peak)")
    print(f"  B. RETUNE tide-out + stale-move, the mined rules:")
    print(f"       optimistic (observed EV):  +${tot_obs:.2f}/mo")
    print(f"       robust (median-priced):    +${tot_med:.2f}/mo")
    if month == MONTH:
        uplift = tot_med / TARGET_MONTH * 100
        uplift_o = tot_obs / TARGET_MONTH * 100
        print()
        print(f"VERDICT: retuning the two gates was worth ≈ +${tot_med:.2f}"
              f"/month (robust pricing; +${tot_obs:.2f} if September's "
              f"observed mix repeats exactly) against September's "
              f"+${TARGET_MONTH:.2f} realized — ≈ {uplift:.0f}% of the "
              f"month (optimistic bound {uplift_o:.0f}%).")
    else:
        print()
        print(f"VERDICT ({month}): retuning the two gates was worth "
              f"≈ +${tot_med:.2f}/month (robust pricing; "
              f"+${tot_obs:.2f} observed-mix) — no realized-month "
              f"target is pinned for {month}; September's baseline "
              f"stays +${TARGET_MONTH:.2f}.")
    print()

    if audit:
        audit_valve(con, ledger, month)

    nto = books.get("tide-out", {}).get("n", 0)
    nsm = books.get("stale-move", {}).get("n", 0)
    print(f"Caveats: n={nto} tide-out / n={nsm} stale-move — one month, "
          "one tide; the mined quick threshold is IN-SAMPLE (it admits "
          "the month's winners by construction — expect less out of "
          "sample); peaks are chart highs the bot would trail below, "
          "so even the median pricing leans optimistic on payers; the "
          "dud cost uses the peak-hour close, not the worst case; a "
          "retune also admits future refusals the month never saw — "
          "the regime risk no counterfactual prices; the (pair, band) "
          "dedupe is gate-blind by design — a pair-band's FIRST refusal "
          "stands for it, so a gate's book counts only refusals that "
          "were chronologically first for their pair-band.")

    print(f"\nrecord: scratch/pergate_calibration.py — window {HOURS}h, "
          f"month {month}, ledger = live + {n_arch} "
          f"scratch/_supp_*.jsonl archives (sha256 in the provenance), "
          f"read-only")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
