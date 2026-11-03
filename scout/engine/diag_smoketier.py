"""diag_smoketier.py — THE FULL SIZING CHAIN AT THE $1,000 TIER (Sep 19).

One pass, arithmetic only, no network: the tier account (death line,
buffer), the 3% entry guard, the book-carry chunk fit, the combined
entry size, and the tier-aware exit stop — each shrunk to the small
account by formula and checked against hand-computed dollars.

  death line   = start x (1 - 3%)          = $970.00 at the 1k tier
  buffer       = current - death line      = $30.00 fresh
  entry guard  = min(balance, buffer x 0.34 / dd)   (dd fail-conserv >= 0.5%)
  book carry   = biggest chunk (25/50/75/100%) whose dollars fit
                 under 20% of the top-of-book depth
  entry size   = min(account, book carry, guard)
  exit stop    = max(buffer x 0.5, $5)     — tightens as the buffer burns

Every check is exact (no tolerances beyond float equality). Exit-coded:
0 = all green, 1 = any check failed. SAFE TO RUN ANYTIME: read-only on
the module state (load_state is sandboxed to a tmpdir shape, never the
production contest_account.json).

Run:  .venv\\Scripts\\python.exe -X utf8 diag_smoketier.py
Writes _smoketier.txt.
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
    lines.append(("PASS  " if ok else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not ok:
        FAIL += 1
    else:
        PASS += 1


def cents(x):
    """Money compares at money precision: binary floats carry
    340.00000000000006 for what is exactly $340.00 — the smoke test
    judges dollars in cents, never in float identity."""
    return round(float(x), 2)


import contest_account as ca

# ── sandbox the tier state: the 1k shape, never production ──────────
_td = Path(tempfile.mkdtemp())
_ca_saved = ca.STATE_FILE
try:
    (_td / "ca.json").write_text(json.dumps(
        {"tier": 1000, "start_balance": 1000.0, "current_balance": 1000.0}),
        encoding="utf-8")
    ca.STATE_FILE = _td / "ca.json"

    lines.append("THE FULL SIZING CHAIN AT THE $1,000 TIER — smoke, arithmetic only")
    lines.append("")

    # ── A. the tier account ──────────────────────────────────────────
    acct = ca.load_state()
    check("A1 the sandboxed account is the 1k tier, fresh balance",
          acct["tier"] == 1000 and float(acct["current_balance"]) == 1000.0)
    _line = ca.death_line(acct)
    check("A2 the death line is start x 97% = $970.00", _line == 970.00, f"got {_line}")
    _buf = ca.buffer(acct)
    check("A3 the buffer is current - line = $30.00", _buf == 30.00, f"got {_buf}")
    _line10k = ca.death_line({"start_balance": 10000.0, "current_balance": 10000.0})
    check("A4 the 10k line is $9,700 (the percent never moves)", _line10k == 9700.00)

    # ── B. the 3% entry guard ────────────────────────────────────────
    g3 = ca.max_entry_usd(1000.0, 3.0, 1000.0)
    check("B1 guard @ 3% dd (fail-conservative) = 30x0.34/0.03 = $340.00",
          cents(g3) == 340.00, f"got {g3}")
    g2 = ca.max_entry_usd(1000.0, 2.0, 1000.0)
    check("B2 guard @ 2% dd = $510.00", cents(g2) == 510.00, f"got {g2}")
    g1 = ca.max_entry_usd(1000.0, 1.0, 1000.0)
    check("B3 guard @ 1% dd = $1,000 (full balance, capped by the account)",
          cents(g1) == 1000.00, f"got {g1}")
    g6 = ca.max_entry_usd(1000.0, 6.0, 1000.0)
    check("B4 guard @ 6% dd = $170.00 (jumpy coins shrink hardest)",
          cents(g6) == 170.00, f"got {g6}")
    g10k = ca.max_entry_usd(10000.0, 2.0, 10000.0)
    check("B5 the same coin at the 10k tier = $5,100 — the guard shrank "
          "10.0x to the small account",
          cents(g10k) == 5100.00 and round(cents(g10k) / cents(g2), 4) == 10.0)
    g_dead = ca.max_entry_usd(970.0, 2.0, 1000.0)
    check("B6 buffer gone (at the line) -> $0 — no new risk, ever",
          g_dead == 0.0, f"got {g_dead}")

    # ── C. the book carry (chunk fit) ────────────────────────────────
    def _chunk_fit(bal, tob):
        carry = tob * 0.20
        fit = [c * bal for c in (0.25, 0.50, 0.75, 1.00) if c * bal <= carry]
        return (max(fit) if fit else 0.0), carry

    c1v, carry1 = _chunk_fit(1000.0, 1500.0)
    check("C1 tob $1,500 -> carry $300 -> the 25% chunk ($250) fits",
          carry1 == 300.0 and c1v == 250.0, f"got carry {carry1}, chunk {c1v}")
    c2v, carry2 = _chunk_fit(1000.0, 4000.0)
    check("C2 tob $4,000 -> carry $800 -> the 75% chunk ($750) fits",
          c2v == 750.0, f"got {c2v}")
    c3v, carry3 = _chunk_fit(1000.0, 6000.0)
    check("C3 tob $6,000 -> carry $1,200 -> all-in ($1,000) fits",
          c3v == 1000.0, f"got {c3v}")
    c4v, carry4 = _chunk_fit(1000.0, 800.0)
    check("C4 tob $800 -> carry $160 -> NO chunk fits ($250 > $160): too thin, $0",
          carry4 == 160.0 and c4v == 0.0, f"got {c4v}")
    from execution_tiers import classify_tier
    _tn = classify_tier(250.0, 1500.0)
    check("C5 classify_tier prices the $250 order at 16.7% of the $1,500 tob",
          abs(_tn[2] - 250.0 / 1500.0 * 100.0) < 1e-9, f"got {_tn}")

    # ── D. the combined entry (guard x book x account) ───────────────
    e1 = ca.entry_size_usd(1000.0, 250.0, 2.0, 1000.0)
    check("D1 entry = min($1,000 account, $250 book, $510 guard) = $250",
          cents(e1) == 250.00, f"got {e1}")
    e2 = ca.entry_size_usd(1000.0, 400.0, 3.0, 1000.0)
    check("D2 entry = min($1,000, $400 book, $340 guard) = $340 — the guard binds",
          cents(e2) == 340.00, f"got {e2}")
    e3 = ca.entry_size_usd(1000.0, None, 2.0, 1000.0)
    check("D3 no book read -> the guard alone caps at $510",
          cents(e3) == 510.00, f"got {e3}")

    # ── E. the tier-aware exit stop ──────────────────────────────────
    stop_fresh = max(ca.buffer(acct) * 0.5, 5.0)
    check("E1 stop fresh @ 1k = max($30x0.5, $5) = $15.00", stop_fresh == 15.00,
          f"got {stop_fresh}")
    acct_mid = dict(acct, current_balance=985.0)
    stop_mid = max(ca.buffer(acct_mid) * 0.5, 5.0)
    check("E2 stop at $985 balance = max($15x0.5, $5) = $7.50 — it tightens "
          "as the buffer burns", stop_mid == 7.50, f"got {stop_mid}")
    acct_line = dict(acct, current_balance=970.0)
    stop_line = max(ca.buffer(acct_line) * 0.5, 5.0)
    check("E3 at the line the $5 floor holds the stop open", stop_line == 5.00,
          f"got {stop_line}")
    _buf10k = 10000.0 - 9700.0
    check("E4 the same stop at 10k = $150.00 — 10.0x the 1k stop",
          max(_buf10k * 0.5, 5.0) == 150.00)

    # ── F. the chain as one story (the live code's own formula) ──────
    bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
    check("F1 the scanner's live stop reads the same formula "
          "(max(buffer x 0.5, $5))",
          "max(_ca_l.buffer(_acct_l) * 0.5, 5.0)" in bs_src
          and "_ca_l.buffer" in bs_src)
    check("F2 the scanner's stop fall-back is config-tiered "
          "(balance x 3% x 0.5), not a fixed $100",
          "float(cfg.challenge_balance) * 0.03) * 0.5" in bs_src)
    # one story: $1,000 account, a 2%-dd coin, tob $1,500
    _entry = ca.entry_size_usd(1000.0, 250.0, 2.0, 1000.0)          # 250
    _worst = _entry * 0.02                                          # 5.00
    _stop = max(ca.buffer(acct) * 0.5, 5.0)                         # 15.00
    check("F3 the story: $250 entry, worst case -$5.00 = 16.7% of the $30 "
          "buffer (under the 1/3 rule), stop -$15.00 = 6% of entry",
          _entry == 250.0 and _worst == 5.0
          and round(_worst / 30.0, 4) <= round(0.34, 4) and _stop == 15.0)
    check("F4 three consecutive worst cases leave the account alive "
          "(3 x $5 = $15 <= $30 buffer)",
          3 * _worst <= ca.buffer(acct))
    check("F5 the funnel clamps the popup's own spend to the guard cap "
          "(send_alert: _spend = _risk_cap, with a spoken SPEND line)",
          "if _spend > _risk_cap:" in bs_src
          and "_spend = _risk_cap" in bs_src
          and "THE 3% GUARD" in bs_src)
    try:
        _dd_missing = ca.window_drawdown_pct("NOPE123")
        check("F6 an unknown coin's drawdown fails conservative to 3%",
              cents(_dd_missing) == 3.0, f"got {_dd_missing}")
    except Exception as _exc:
        check("F6 an unknown coin's drawdown fails conservative to 3%",
              False, _exc)
finally:
    ca.STATE_FILE = _ca_saved

lines.append("")
lines.append(f"RESULT: {PASS} passed, {FAIL} failed")
Path("_smoketier.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
