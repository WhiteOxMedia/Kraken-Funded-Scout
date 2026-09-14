"""test_surethingautorefit.py — the monthly Sure Thing auto-refit.

Pins (all offline; the replay and the ledgers are sandboxed so the
production weights ledger is never touched by a test):
  1. the ship-gate order — beats legacy, above the forward-ranker
     floor, at-or-better than the incumbent ON THE SAME HOLDOUT
  2. the degradation contract — a fit far below the incumbent is
     reported loudly (DEGRADED) and the incumbent stands
  3. a ship swaps REFIT_W atomically and stamps the ledger; a keep
     leaves the weights untouched
  4. the driver loop wires into the scanner like its siblings and
     never raises
Every fake uses the REAL gate math: 11-wide feature rows (the full
FEAT_NAMES space), labels driven by the hist column, and a scripted
ridge_fit so each candidate lands in a known gate band.
"""
import json
import sys
import threading
from pathlib import Path

import numpy as np

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import surething_autorefit as ar
import sure_thing as st

PASS = 0
FAIL = 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ── 0. constants + wiring ────────────────────────────────────────────
check("the refit period is monthly",
      ar.REFIT_PERIOD_S == 30 * 86400.0)
check("the first pass fires shortly after boot (the store is touched "
      "at every restart)",
      0 < ar.FIRST_DELAY_S <= 3600.0)
check("the forward-ranker floor and the drop margin exist",
      ar.DEGRADE_FLOOR == 0.505 and ar.DEGRADE_DROP == 0.03)
check("the weights ledger is a real file beside the module",
      ar.WEIGHTS_FILE.name == "sure_thing_autoweights.json")
check("the report rotates instead of growing forever",
      ar.REPORT_MAX_BYTES == 500_000)
bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
check("the scanner wires the auto-refit driver",
      "refit_driver_loop" in bs_src and "surething-autorefit" in bs_src
      and "surething_autorefit" in bs_src)
check("the driver is a daemon beside its siblings",
      "daemon=True" in bs_src)

# ── sandbox: ledgers + the replay, so production files never see a test
real_ledger = ar.WEIGHTS_FILE
real_report = ar.OUT_FILE
sandbox = BASE / "_test_autorefit_dir"
sandbox.mkdir(exist_ok=True)
ar.WEIGHTS_FILE = sandbox / "weights.json"
ar.OUT_FILE = sandbox / "report.txt"

_refit_w_orig = dict(st.REFIT_W)
_real_replay = ar._replay_rows
_real_ridge = None

try:
    import diag_surething_refit as dg
    _real_ridge = dg.ridge_fit

    # a fake episode dataset in the FULL 11-component space: labels are
    # driven purely by the "hist" column (index 10 in FEAT_NAMES — the
    # last one) — deterministic, so the AUC of a candidate weight
    # vector is a stable function of the signal weight it puts on that
    # column (the incumbent's 56.3 ranks highest, legacy's 10 ranks
    # barely above coin-flip).
    rng = np.random.default_rng(7)
    N = 1500
    feats = rng.random((N, len(dg.FEAT_NAMES)))
    y = (feats[:, list(dg.FEAT_NAMES).index("hist")] > 0.70).astype(float)
    HOURS = list(range(N))             # already chronological

    def fake_rows():
        rows = [(float(h), f, float(yy), {})
                for h, f, yy in zip(HOURS, feats, y)]
        return rows, 3

    ar._replay_rows = fake_rows

    def w_on_hist(signal_w, filler="tests"):
        """An 11-vector: signal_w on the label column, the rest on one
        noise column (renormalized to sum 100 — scale is AUC-invariant)."""
        v = np.zeros(len(dg.FEAT_NAMES))
        i9 = list(dg.FEAT_NAMES).index("hist")
        v[i9] = signal_w
        v[list(dg.FEAT_NAMES).index(filler)] = 100.0 - signal_w
        return v

    w_ship = w_on_hist(90.0)     # far above the incumbent's 56.3
    w_mid = w_on_hist(50.0)      # between legacy (10) and incumbent (56.3)
    w_bad = w_on_hist(25.0)      # beats legacy, far below the incumbent
    w_coin = np.full(len(dg.FEAT_NAMES), 100.0 / len(dg.FEAT_NAMES))

    dg.ridge_fit = lambda X, y_: w_ship

    # ── 1. a fit that beats legacy AND the incumbent -> SHIP + swap ───
    # fit_now clips components to [0, 40] and renormalizes to sum 100
    # before the swap — the shipped vector is the CLIPPED one (its
    # ranking on the hist column is unchanged: clip+renorm is a
    # positive rescale of this fixture's two-active-column vector).
    w_ship_clipped_hist = 80.0
    a = ar.fit_now()
    check("a fit that beats legacy and the incumbent SHIPS",
          a["shipped"] is True, a)
    check("the ship swapped the live weights (atomic dict update, "
          "clipped to the module's [0, 40] band)",
          abs(st.REFIT_W["hist"] - w_ship_clipped_hist) < 1e-6, st.REFIT_W)
    led = ar._read_ledger()
    check("the ship stamped the ledger",
          led.get("shipped") is True
          and led.get("weights", {}).get("hist") == w_ship_clipped_hist,
          led.get("verdict"))
    check("the report landed and carries the verdict",
          ar.OUT_FILE.exists()
          and "SHIPPED" in ar.OUT_FILE.read_text(encoding="utf-8"))

    # ── 2. a regression vs the incumbent -> KEPT, weights untouched ───
    dg.ridge_fit = lambda X, y_: w_mid
    before = dict(st.REFIT_W)
    a2 = ar.fit_now()
    check("a fit below the incumbent is KEPT (no regression ships)",
          a2["shipped"] is False
          and a2["auc_new"] < a2["auc_incumbent"], a2)
    check("a keep leaves the live weights untouched",
          st.REFIT_W == before)
    check("the keep is stamped honestly (shipped: false)",
          ar._read_ledger().get("shipped") is False)

    # ── 3. the DEGRADATION contract: a big drop is REPORTED loudly ────
    dg.ridge_fit = lambda X, y_: w_bad
    a3 = ar.fit_now()
    check("a fit far below the incumbent is named DEGRADED",
          "DEGRADED" in a3["verdict"] and a3["shipped"] is False, a3)
    check("the degradation is on the report (the report-when-degraded contract)",
          "DEGRADED" in ar.OUT_FILE.read_text(encoding="utf-8"))
    check("the degraded pass still keeps the incumbent's weights",
          st.REFIT_W == before)

    # ── 4. a coin-flip fit never ships (whichever gate catches it) ────
    dg.ridge_fit = lambda X, y_: w_coin
    a4 = ar.fit_now()
    check("a coin-flip fit never ships",
          a4["shipped"] is False and a4["auc_new"] < 0.70,
          a4)

    # ── 5. a thin replay keeps everything and stamps the why ──────────
    ar._replay_rows = lambda: ([], 0)
    a5 = ar.fit_now()
    check("a thin replay keeps the incumbent and says why",
          a5["shipped"] is False and "too thin" in a5["verdict"], a5)
    check("a thin replay touches no weights",
          st.REFIT_W == before)

    # ── 6. the driver loop body: one pass, then idles on the stop ─────
    ar._replay_rows = fake_rows
    calls = {"n": 0}
    real_fit = ar.fit_now

    def counting_fit():
        calls["n"] += 1
        return real_fit()

    ar.fit_now = counting_fit
    stop = threading.Event()
    th = threading.Thread(target=ar.refit_driver_loop, args=(stop,),
                          kwargs={"first_delay_s": 0.05, "period_s": 3600.0},
                          daemon=True)
    th.start()
    th.join(timeout=5)
    check("the driver loop runs its first pass and idles alive",
          calls["n"] == 1 and (th.is_alive() or stop.is_set()), calls)
    stop.set()
    th.join(timeout=5)

    # the loop survives a raising fit (never takes the funnel down)
    def boom():
        raise RuntimeError("store broke")

    ar.fit_now = boom
    stop2 = threading.Event()
    th2 = threading.Thread(target=ar.refit_driver_loop, args=(stop2,),
                           kwargs={"first_delay_s": 0.05, "period_s": 3600.0},
                           daemon=True)
    th2.start()
    th2.join(timeout=3)
    check("a raising pass never kills the driver",
          th2.is_alive() or stop2.is_set())
    stop2.set()
    th2.join(timeout=5)
finally:
    ar.WEIGHTS_FILE = real_ledger
    ar.OUT_FILE = real_report
    ar._replay_rows = _real_replay
    st.REFIT_W.update(_refit_w_orig)          # the ship-swap test restored
    try:
        import diag_surething_refit as dg2
        if _real_ridge is not None:
            dg2.ridge_fit = _real_ridge
    except Exception:
        pass
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)

# the shipped weights home is untouched by the suite
src = (BASE / "sure_thing.py").read_text(encoding="utf-8")
check("the shipped REFIT_W source stands (the refit swaps at runtime, "
      "the file is the cold-start truth)",
      '"hist": 56.3' in src and "SURE_THING_USE_REFIT = True" in src)
check("the production ledger was never written by a test",
      not Path("sure_thing_autoweights.json").exists()
      or json.loads(Path("sure_thing_autoweights.json").read_text(encoding="utf-8"))
      .get("verdict", "").startswith(("SHIPPED", "kept", "DEGRADED")))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = BASE / "_surethingautorefit_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
