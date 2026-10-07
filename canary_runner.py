"""THE CANARY RUNNER (task 6, Sep 28 spec): the proportional
self-adjust runs in cohorts BEFORE it runs the whole book. The runner
flips the machine's own operator dial to canary mode, collects cycles,
evaluates the spec's thresholds, and rolls back by flipping the dial
OFF (the machine's real rollback — the scanner reads it live per tick,
no restart).

The machine's dials (exit_extension's SCHEDULER pattern):
    env > exit_extension.dial.json > defaults (OFF)
The dial file is the single operator surface this runner touches. The
rollback command in the spec's bash form maps to it exactly:
    opsctl feature set watch_extension.enabled off  ==  {"EXIT_EXTENSION_ENABLED": "0"}
    opsctl service restart scanner                  ==  not needed: the dial is read live

The spec's thresholds, evaluated from the telemetry stream
(auto_adjust cohort rows only):
    net within the bootstrap CI of the sweep's expected band
    single-book worst loss under the cap
    hit rate stable across the first half vs the second half
"""
from __future__ import annotations

import json
import os
import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIAL_FILE = ROOT / "exit_extension.dial.json"
RUNS_DIR = ROOT / "artifacts" / "canary"

# THE REPORT-DIR SEAM (the review's canary minor, landed with the
# simulated-runs major): a what-if that reports into the REPO's real
# artifacts/canary/ leaks synthetic run reports into live-machine
# territory — the signoff tooling and the operator both read that
# directory. The env override resolves LIVE (the STATE_ENV / COHORT_ENV
# pattern), so a sandbox moves the reports with the rest of its state.
RUNS_ENV = "CANARY_RUNS_DIR"


def runs_dir() -> Path:
    """The run-report directory, resolved NOW (never import-frozen)."""
    explicit = os.environ.get(RUNS_ENV)
    return Path(explicit) if explicit else RUNS_DIR

CYCLES = 50
WORST_LOSS_CAP = -0.10       # the loosening cap — a breach is a breach
HIT_RATE_DRIFT = 0.10        # |rate2 - rate1| tolerance
SEED = 7


def _commit_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=str(ROOT))
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ── THE TWO-FILE DESIGN (the structural fix, Sep 28): the canary
# cohort gets its OWN state file — arming or rolling back the cohort
# can NEVER touch the exit-extension feature master, because the two
# controls no longer share a file (or a key, or a write). The dial
# remains the operator's feature surface alone.
COHORT_FILE = ROOT / "auto_adjust_canary.json"

# THE LIVE COHORT, RESOLVED AT IMPORT (the three-review family's last
# door, closed): this is the file the RUNNING MACHINE arms — the path
# every accidental arm must be compared against. cohort_file() may
# point elsewhere (the env seam), but THIS path is the production
# cohort, and arm=True against it is a live re-arm, never a drill.
RUNTIME_COHORT_FILE = ROOT / "auto_adjust_canary.json"


class CanaryArmRefused(RuntimeError):
    """arm=True aimed at the LIVE cohort file without arm_confirm.
    The drill is refused, not degraded: production state is never
    touched by a what-if, and a deliberate live re-arm must say so
    (or move the cohort file with AUTO_ADJUST_CANARY_FILE first)."""

# THE COHORT FILE RESOLVES LIVE (the round-7 major): the env seam FIRST
# — AUTO_ADJUST_CANARY_FILE, the same seam exit_extension._aa_cohort has
# honored since the two-file design — then the module path. An
# import-frozen path made the runner's flip/rollback blind to the seam:
# a sandbox that set the env moved the HOOK's read but not the RUNNER's
# write, so an armed or simulated run in that sandbox still flipped the
# LIVE cohort file (the isolation break), and a rollback could not stop
# a cohort the env pointed at. One env move now sandboxes the whole
# two-module pair.
COHORT_ENV = "AUTO_ADJUST_CANARY_FILE"


def cohort_file() -> Path:
    """Resolved NOW, never import-frozen (the STATE_ENV pattern)."""
    explicit = os.environ.get(COHORT_ENV)
    return Path(explicit) if explicit else COHORT_FILE


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, doc: dict) -> None:
    """ATOMIC (the round-2 cross-writer sweep): tmp + os.replace — a
    torn write is never readable by the live readers. NOTE: the
    state-writer tripwire attributes writes per-unit, so the cohort's
    writers (flip/rollback) inline their IO — a write hidden behind a
    path parameter is a write the census cannot see."""
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


def read_cohort(doc: dict | None = None) -> str:
    """The armed cohort id from the cohort's OWN file; '' = disarmed.
    The file resolves LIVE: the env seam first, then the module path."""
    if doc is None:
        doc = _read_json(cohort_file())
    return str(doc.get("cohort_id", "") or "")


def read_dial(doc: dict | None = None) -> str:
    """Compat reader: 'canary' when the cohort file is armed; otherwise
    the exit-extension master's own value, read (never written) from
    the dial file."""
    if read_cohort():
        return "canary"
    dial = doc if doc is not None else _read_json(DIAL_FILE)
    return str(dial.get("EXIT_EXTENSION_ENABLED", "0"))


def _write_dial(doc: dict) -> None:
    """ATOMIC; LEGACY USE ONLY — the cohort never rides the dial file
    anymore, so the only dial write left is rollback's one-window
    de-contamination of the old parked keys (master preserved)."""
    _write_atomic(DIAL_FILE, doc)


# THE LEGACY KEY NAME (one migration window): the old single-file
# design parked the cohort on the dial file — the de-contamination pass
# in rollback strips these leftovers WITHOUT ever writing the master.
AA_CANARY_KEY = "AUTO_ADJUST_CANARY"


def flip_canary(cohort_id: str, doc: dict | None = None,
                params: dict | None = None) -> dict:
    """Arm the cohort on ITS OWN file. The exit-extension dial file is
    never opened for writing here — a flip cannot clobber the feature
    master because it cannot touch the file at all. The IO is INLINE
    (the state-writer tripwire attributes writes per-unit; a write
    behind a path parameter is invisible to the census).

    `params` (Sep 29, the review's long-standing major) persists the
    CELL THE GATE SELECTED — alpha, delta_max, daily_cap — alongside
    the cohort id, so arming is no longer a print: the boundary hook
    reads these from the cohort file and evaluates the propose path
    with the selected cell instead of the module defaults."""
    # A FRESH COHORT DOC PER ARM (Sep 29 review minor): the previous
    # cohort file's fields (a disarmed tombstone, stale params, an old
    # cohort_id) must never ride into the new arm — arming is a new
    # cohort, not an update of the old one.
    cur = dict(doc) if doc is not None else {}
    cur["cohort_id"] = cohort_id
    cur["armed_at"] = datetime.now().astimezone().isoformat()
    if params:
        cur["params"] = {str(k): float(v) for k, v in params.items()
                         if k in ("alpha", "delta_max", "daily_cap")}
    _cf = cohort_file()
    tmp = str(_cf) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2)
    os.replace(tmp, _cf)
    return {"dial": "canary", "cohort_id": cohort_id,
            **({"params": cur["params"]} if "params" in cur else {})}


def rollback(feature_doc: dict | None = None) -> dict:
    """THE ROLLBACK — the cohort's own stop: the cohort file goes to
    disarmed and NOTHING else is written. The exit-extension feature
    master lives on ITS OWN file and is never touched (a cohort
    rollback can never silently disable the exit extension; stopping
    the extension stays the operator's own dial action). One migration
    window: legacy cohort keys parked on the old dial file are stripped
    WITHOUT writing the master value."""
    cleared = read_cohort(feature_doc) if feature_doc is not None \
        else read_cohort()
    import os
    _cf = cohort_file()
    tmp = str(_cf) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"disarmed": True,
                   "cleared_cohort": cleared,
                   "at": datetime.now().astimezone().isoformat()},
                  fh, indent=2)
    os.replace(tmp, _cf)
    dial = _read_json(DIAL_FILE)
    if AA_CANARY_KEY in dial or "cohort_id" in dial:
        dial.pop(AA_CANARY_KEY, None)
        dial.pop("cohort_id", None)     # the master key is untouched
        _write_dial(dial)
    return {"rolled_back": True, "dial": "0", "cohort": "disarmed",
            "at": datetime.now().astimezone().isoformat()}


# ── the cycle collector ─────────────────────────────────────────────
def collect_cycles(cohort: list[str], cycles: int, rotation_seed: dict,
                   now: datetime | None = None,
                   seed: int = SEED) -> list[dict]:
    """The cohort's simulated cycles: one per rotation, the buried
    candidates drawn from the rotation's seeded distribution. Determin-
    istic (seeded) so the run report reproduces. A cycle = one rotation
    outcome: {B, P, buried pairs, paid pairs}."""
    now = now or datetime.now().astimezone()
    rng = random.Random(seed)
    out = []
    for i in range(cycles):
        rot = f"canary-{i:03d}"
        base = rotation_seed.get("B", 0)
        if base <= 0:
            out.append({"cycle": i, "rot": rot, "B": 0, "P": 0,
                        "buried": [], "paid": [], "ts":
                            (now + timedelta(hours=i)).isoformat()})
            continue
        B = max(0, int(rng.gauss(base, max(1.0, base * 0.3))))
        rate = rotation_seed.get("r", 0.0)
        buried = [f"{cohort[i % len(cohort)]}-{j}"
                  for j in range(B)]
        paid = buried[:int(round(B * rate))]
        out.append({"cycle": i, "rot": rot, "B": B, "P": len(paid),
                    "buried": buried, "paid": paid,
                    "ts": (now + timedelta(hours=i)).isoformat()})
    return out


# ── the evaluation (the spec's thresholds, machine-real) ────────────
def _walk_factor(cycles: list[dict], verdicts: list,
                 daily_cap: float,
                 start_factor: float | None = None) -> dict:
    """THE ONE WALK (Sep 30): the factor per applied cycle under the
    machine's own rails — aa.clamp's guard clamp and the daily cap
    (REFUSED whole, never truncated — apply_adjustment's real
    posture). Shared by evaluate()'s guard-factor verdict and
    gf_walk_projection()'s operator projection, so the projection the
    bot's plate renders IS this evaluation's walk, never a second
    implementation that could drift. Cycles carry hourly ts (the
    collector stamps them); the daily rail groups on the ts day, an
    unstamped cycle rides its own day. start_factor pins the walk's
    start (a drill controls its variables); production walks from the
    LIVE factor (aa.read_guard_factor)."""
    import auto_adjust as aa
    try:
        before = (aa.read_guard_factor() if start_factor is None
                  else float(start_factor))
    except Exception:
        before = aa.read_guard_factor()
    start = before
    gf_min = before
    applied = 0
    cap_refused = 0
    consumed: dict = {}
    for c, (delta, reason) in zip(cycles, verdicts):
        if reason != "ok":
            continue
        ts = c.get("ts")
        day = str(ts)[:10] if ts else f"cycle-{c.get('cycle')}"
        used = consumed.get(day, 0.0)
        if used + abs(delta) > daily_cap:
            cap_refused += 1
            continue          # daily_cap_exceeded — the rotation is
                              # refused whole, never shrunk
        after = aa.clamp(before, delta)
        if after < before:    # the rule only loosens; the clamp floors it
            consumed[day] = used + abs(delta)
            before = after
            applied += 1
            gf_min = min(gf_min, before)
    return {"start": start, "end": before, "min": gf_min,
            "applied": applied, "cap_refused": cap_refused}


def gf_walk_projection(cycles: list[dict], params: dict | None = None,
                       start_factor: float | None = None) -> dict:
    """THE OPERATOR'S PROJECTION (Sep 30): where the machine's
    calibration is HEADING — the same walk evaluate() judges, run over
    the caller's cycles (the boundary hook feeds it the live rotation's
    own B/P under the armed cell) and persisted into
    profit_guard_state.json:gf_projection for the bot's learning plate
    to render beside the GF line. Same rails, same clamp, same daily
    cap — one walk, two consumers, zero drift between the verdict and
    the projection. Never raises: a projection that cannot be computed
    must never break the rotation boundary that computes it."""
    import auto_adjust as aa
    try:
        return _gf_walk_projection(cycles, params, start_factor)
    except Exception:
        return {"start": 0.0, "end": 0.0, "delta": 0.0, "applied": 0,
                "rotations": len(cycles), "m_fail": 0,
                "cap_refused": 0, "at_floor": False, "params": None,
                "source": "live", "computed_at":
                datetime.now().astimezone().isoformat(), "error": True}


def _gf_walk_projection(cycles: list[dict], params: dict | None,
                        start_factor: float | None) -> dict:
    import auto_adjust as aa
    _p = params or {}
    try:
        _dcap = (aa.DAILY_CAP if _p.get("daily_cap") is None
                 else float(_p["daily_cap"]))
    except (TypeError, ValueError):
        _dcap = aa.DAILY_CAP
    verdicts = [aa.compute_delta(c["B"], c["P"]) for c in cycles]
    w = _walk_factor(cycles, verdicts, _dcap, start_factor=start_factor)
    cell = None
    if params is not None:
        try:
            cell = {"alpha": (aa.ALPHA if _p.get("alpha") is None
                              else float(_p["alpha"])),
                    "delta_max": (aa.DELTA_MAX if _p.get("delta_max") is None
                                  else float(_p["delta_max"])),
                    "daily_cap": _dcap}
        except (TypeError, ValueError):
            cell = None
    return {"start": round(w["start"], 4), "end": round(w["end"], 4),
            "delta": round(w["end"] - w["start"], 4),
            "applied": w["applied"], "rotations": len(cycles),
            "m_fail": sum(1 for _, r in verdicts if r != "ok"),
            "cap_refused": w["cap_refused"],
            "at_floor": w["end"] <= aa.MIN_GF + 1e-9,
            "params": cell,
            "source": "pinned" if start_factor is not None else "live",
            "computed_at": datetime.now().astimezone().isoformat()}


def evaluate(cycles: list[dict], prior_band: tuple[float, float] | None,
             worst_cap: float = WORST_LOSS_CAP, params: dict | None = None,
             start_factor: float | None = None) -> dict:
    """Apply the machine's rule per cycle (the task-2 compute_delta,
    B_MIN/R_MIN live), then the spec's thresholds — JUDGED AS ARMED
    (Sep 30, the bot follows the factor now: drills that cannot fail
    validate nothing).

    The band must be CERTIFIED: `prior_band` is the caller's external
    evidence (the sweep's expected band, the incumbent factor's own
    history). A self-derived CI grades the run with the run's own
    distribution — it can never fail, so it certifies nothing (the
    recovered CR major). No band → the net threshold reports
    INCONCLUSIVE (named in reasons), never a quiet pass.

    `params` is the CELL the gate armed (ops/canary_enable persists it
    on the cohort file): alpha/delta_max/daily_cap, each optional (the
    module default stands). The worst cap rides the cell — a cell AT
    its own dmax is the cap echoed back (canary_enable's doctrine), so
    the armed verdict demands strictly inside: 0.8 × dmax. Without
    `params` the legacy `worst_cap` argument stands unchanged.

    THE GUARD-FACTOR WALK: the bot now FOLLOWS the factor these deltas
    drive (fills.trail_geometry), so the run walks the factor per
    applied cycle through the machine's own rails — aa.clamp's guard
    clamp [MIN_GF, MAX_GF] and the daily cap (the machine REFUSES an
    over-cap rotation, it never truncates behind the caller's back).
    It breaches when the cell would drive the bot's calibration to the
    MIN_GF floor. Cycles carry hourly ts (the collector stamps them);
    the daily rail groups on the ts day, an unstamped cycle rides its
    own day. `start_factor` pins the drill's starting calibration (a
    drill controls its variables); production walks from the LIVE
    factor (aa.read_guard_factor).
    """
    import auto_adjust as aa
    import simulate_sweep as ss
    net = 0.0
    worst = 0.0
    rates = []
    verdicts = []          # (delta, reason) per cycle — ONE rule verdict,
                           # reused by the thresholds AND the walk
    for c in cycles:
        delta, reason = aa.compute_delta(c["B"], c["P"])
        verdicts.append((delta, reason))
        if reason == "ok":
            net += delta
            worst = min(worst, delta)
        if c["B"]:
            rates.append(c["P"] / c["B"])
    deltas = [d for d, r in verdicts if r == "ok"]
    ci = ss.bootstrap_ci(deltas) if deltas else (0.0, 0.0)
    # THE CERTIFIED BAND: supplied externally or the verdict is
    # inconclusive on this threshold — the self-derived CI stays in the
    # report (the shape of the applied deltas) but certifies nothing.
    band_certified = prior_band is not None
    band = tuple(prior_band) if band_certified else ci
    n_applied = max(1, len(deltas))
    outside_band = (band_certified
                    and not (band[0] * n_applied <= net <= band[1] * n_applied))
    net_ok = not outside_band
    half = max(1, len(rates) // 2)
    r1 = sum(rates[:half]) / half if rates else 0.0
    r2 = (sum(rates[half:]) / (len(rates) - half)) if len(rates) > half \
        else r1
    stable = abs(r2 - r1) <= HIT_RATE_DRIFT
    # THE ARMED CELL (the canary evaluates the machine AS ARMED, never
    # as defaulted): the gate's selected cell overrides the module
    # defaults key-by-key — the same seam the boundary hook honors.
    _p = params or {}
    try:
        _alpha = aa.ALPHA if _p.get("alpha") is None else float(_p["alpha"])
        _dmax = (aa.DELTA_MAX if _p.get("delta_max") is None
                 else float(_p["delta_max"]))
        _dcap = (aa.DAILY_CAP if _p.get("daily_cap") is None
                 else float(_p["daily_cap"]))
        cell_cap = (0.8 * _dmax
                    if (params is not None and _dmax > 0) else worst_cap)
        params_cell = ({"alpha": _alpha, "delta_max": _dmax,
                        "daily_cap": _dcap}
                       if params is not None else None)
    except (TypeError, ValueError):
        _alpha, _dmax, _dcap = aa.ALPHA, aa.DELTA_MAX, aa.DAILY_CAP
        cell_cap = worst_cap
        params_cell = None
    # THE WALK — the factor the bot follows, under the machine's own
    # rails. THE ONE WALK (Sep 30): the mechanics live in _walk_factor,
    # shared with gf_walk_projection — the projection the bot's plate
    # renders is THIS walk, never a second implementation.
    w = _walk_factor(cycles, verdicts, _dcap, start_factor=start_factor)
    start = w["start"]
    before = w["end"]
    gf_min = w["min"]
    walk_applied = w["applied"]
    gf_breach = before <= aa.MIN_GF + 1e-9
    breach = outside_band or worst < cell_cap or (not stable) or gf_breach
    reasons = []
    if outside_band:
        reasons.append(f"net_delta {net:.4f} outside band "
                       f"[{band[0] * n_applied:.4f}, "
                       f"{band[1] * n_applied:.4f}] (certified)")
    elif not band_certified:
        reasons.append("band not certified by external evidence — "
                       "verdict inconclusive on the net threshold "
                       "(the self-derived CI is not a certification)")
    if worst < cell_cap:
        reasons.append(f"single_book_loss {worst:.4f} < {cell_cap:.4f}"
                       + (" (the armed cell's cap, 0.8x its dmax)"
                          if params_cell is not None else ""))
    if not stable:
        reasons.append(f"hit rate drift {r2 - r1:+.3f} > "
                       f"{HIT_RATE_DRIFT}")
    if gf_breach:
        reasons.append(f"guard factor driven to the floor: end "
                       f"{before:.4f} at MIN_GF after {walk_applied} "
                       f"applied cycles (the bot follows this factor)")
    return {"net_delta": round(net, 4), "worst_single_book_loss":
            round(worst, 4), "net_in_band": net_ok,
            "band": [round(band[0] * n_applied, 4),
                     round(band[1] * n_applied, 4)],
            "outside_band": outside_band,
            "band_certified": band_certified,
            "worst_cap": round(cell_cap, 4),
            "params_cell": params_cell,
            "guard_factor": {"start": round(start, 4),
                             "min": round(gf_min, 4),
                             "end": round(before, 4),
                             "applied": walk_applied,
                             "start_source": ("pinned" if start_factor
                                              is not None else "live")},
            "guard_factor_breach": gf_breach,
            "hit_rate_1": round(r1, 4), "hit_rate_2": round(r2, 4),
            "hit_rate_stable": stable, "applied_cycles": len(deltas),
            "breach": breach, "reasons": reasons}


def run_canary(cohort: list[str], rotation_seed: dict,
               prior_band: tuple[float, float] | None = None,
               cycles: int = CYCLES, seed: int = SEED,
               breach_injection: dict | None = None,
               arm: bool = False,
               arm_confirm: bool = False,
               params: dict | None = None,
               start_factor: float | None = None) -> dict:
    """The spec's canary job. BY DEFAULT A PURE SIMULATION (the four-
    review major): no flip, no rollback — production state is never
    touched, because a what-if that silently re-arms the live cohort
    would also STRIP the live params cell (flip_canary writes a fresh
    doc) and a simulated breach would roll the LIVE cohort off.

    `params` (Sep 30, the guard-factor validation) is the CELL the gate
    armed: the evaluation judges the machine AS ARMED (cell-resolved
    worst cap, cell-recorded verdict, the guard-factor walk the bot's
    trail actually rides), and an ARMED run flips the cohort WITH that
    cell — the staging exercise rehearses the exact parameters it
    graded. `prior_band` supplies the external certification; without
    it the run reports band-inconclusive (see evaluate).

    arm=True is the staging exercise — real flip, and a breach executes
    the real rollback — and it is REFUSED when the cohort file in play
    resolves to the LIVE machine's cohort (RUNTIME_COHORT_FILE) unless
    arm_confirm=True: the three-review family (a drill flipping the
    live cohort, stripping its params cell, or rolling it off) dies
    here at the last door. Staging sandboxes stay one env away
    (AUTO_ADJUST_CANARY_FILE — cohort_file() resolves it live), and a
    confirmed live re-arm is stamped in the run report for the audit
    trail. breach_injection forces a distribution past the thresholds
    (the rollback must be proven, not assumed). Writes the run report
    either way."""
    import auto_adjust as aa
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    flipped = None
    armed_live = False
    if arm:
        if cohort_file().resolve() == RUNTIME_COHORT_FILE.resolve() \
                and not arm_confirm:
            raise CanaryArmRefused(
                "arm=True targets the LIVE cohort file "
                f"({RUNTIME_COHORT_FILE}) — a drill must sandbox it "
                f"first: set {COHORT_ENV} to a temp path, or pass "
                "arm_confirm=True if a live re-arm is actually meant")
        armed_live = cohort_file().resolve() \
            == RUNTIME_COHORT_FILE.resolve()
        flipped = flip_canary(f"canary-{run_id}", params=params)
    cyc = collect_cycles(cohort, cycles, rotation_seed, seed=seed)
    if breach_injection:
        # the staging drill: the cohort's payer-rate collapses in the
        # SECOND half — the regime shift the first half hid. (Forcing
        # duds everywhere cannot breach: the rule refuses to loosen on
        # a low-ratio stream, which is the machine being RIGHT, not a
        # failure. The drift threshold is the canary's teeth.)
        half = len(cyc) // 2
        for c in cyc[half:]:
            if c["B"]:
                c["P"] = breach_injection.get("second_half_P", 0)
    # start_factor pins the drill's variable (Oct 7): the live factor
    # may sit AT MIN_GF (an honest dock) and a healthy walk cannot
    # start from a breach - the drill controls its own calibration.
    ev = evaluate(cyc, prior_band, params=params,
                  start_factor=start_factor)
    record = {"run_id": run_id, "cohort": list(cohort),
              "dial": flipped, "cycles": len(cyc),
              "evaluation": ev,
              "commit_sha": _commit_sha(),
              "rollback": None,
              "armed_live_cohort": armed_live}
    if ev["breach"]:
        if arm:
            record["rollback"] = rollback()
            record["status"] = "rolled_back"
        else:
            # a simulated breach REPORTS the required rollback; it must
            # never execute it against whatever cohort is live
            record["status"] = "breach_simulated"
            record["rollback_required"] = True
    else:
        record["status"] = "pass_continue" if arm else "pass_simulated"
        record["next"] = ("collect 24-72h, then request Ops + Product "
                          "sign-off (governance/adjustment_policy.md)")
    out_dir = runs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    rp = out_dir / f"{run_id}.json"
    rp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["report"] = str(rp)
    return record
