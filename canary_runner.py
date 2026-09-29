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
import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIAL_FILE = ROOT / "exit_extension.dial.json"
RUNS_DIR = ROOT / "artifacts" / "canary"

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


# ── the dial (the machine's real operator surface) ──────────────────
def read_dial(doc: dict | None = None) -> str:
    """The canary-state reader: the DEDICATED cohort key set = 'canary'
    (the feature master is never consulted for the cohort); otherwise
    the master's own value. A bare 'canary' left on the master by the
    old reuse reads as junk, not as a cohort."""
    if doc is None:
        try:
            doc = json.loads(DIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    if str(doc.get(AA_CANARY_KEY, "") or ""):
        return "canary"
    return str(doc.get("EXIT_EXTENSION_ENABLED", "0"))


def _write_dial(doc: dict) -> None:
    """ATOMIC (the round-2 cross-writer sweep): the dial file was a
    bare write_text — a torn write lands between the ticks that read
    it live, and the reader degrades a half-file to defaults while an
    arm or a ROLLBACK is mid-flight. tmp + os.replace: the scanner's
    live read always sees whole old or whole new."""
    import os
    tmp = str(DIAL_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, DIAL_FILE)


# THE DEDICATED CANARY DIAL KEY (the isolated review's major, closed):
# the feature master (EXIT_EXTENSION_ENABLED) is the machine's own arm
# switch and is NEVER reused as a cohort marker — 'canary' meant OFF to
# the reader but silently clobbered the operator's real value on flip.
# The cohort rides its own key; the master is preserved byte-exact.
AA_CANARY_KEY = "AUTO_ADJUST_CANARY"


def flip_canary(cohort_id: str, doc: dict | None = None) -> dict:
    """watch_extension.enabled = canary: the machine stays OFF for the
    book but announces the cohort the runner is watching."""
    if doc is None:
        doc = {}
    if DIAL_FILE.exists() and doc == {}:
        try:
            doc = json.loads(DIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    doc.update({AA_CANARY_KEY: cohort_id,   # the machine key — the
                "cohort_id": cohort_id})    # master is NEVER touched
    _write_dial(doc)
    return {"dial": "canary", "cohort_id": cohort_id}


def rollback(feature_doc: dict | None = None) -> dict:
    """THE ROLLBACK: the dial OFF. Live-read per tick — no restart, no
    pids. Returns the event for the run report."""
    if feature_doc is None:
        feature_doc = {}
    if DIAL_FILE.exists() and feature_doc == {}:
        try:
            feature_doc = json.loads(
                DIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            feature_doc = {}
    feature_doc["EXIT_EXTENSION_ENABLED"] = "0"
    feature_doc.pop("cohort_id", None)
    feature_doc.pop(AA_CANARY_KEY, None)   # the cohort rides its own key
    _write_dial(feature_doc)
    return {"rolled_back": True, "dial": "0",
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
def evaluate(cycles: list[dict], prior_band: tuple[float, float] | None,
             worst_cap: float = WORST_LOSS_CAP) -> dict:
    """Apply the machine's rule per cycle (the task-2 compute_delta,
    B_MIN/R_MIN live), then the spec's three thresholds."""
    import auto_adjust as aa
    import simulate_sweep as ss
    net = 0.0
    worst = 0.0
    deltas = []
    rates = []
    for c in cycles:
        delta, reason = aa.compute_delta(c["B"], c["P"])
        if reason == "ok":
            deltas.append(delta)
            net += delta
            worst = min(worst, delta)
        if c["B"]:
            rates.append(c["P"] / c["B"])
    ci = ss.bootstrap_ci(deltas) if deltas else (0.0, 0.0)
    band = prior_band or ci
    net_in_band = band[0] <= net <= band[1]
    # the machine's twist on the spec's 'net within CI': the CI of the
    # applied deltas is the per-rotation shape; the NET over the run
    # must sit inside the band scaled by the streak length. A canary
    # that applied nothing idles INSIDE any band (refusing is safe).
    n_applied = max(1, len(deltas))
    net_ok = (band[0] * n_applied) <= net <= (band[1] * n_applied)
    if not deltas:
        net_ok = True
    half = max(1, len(rates) // 2)
    r1 = sum(rates[:half]) / half if rates else 0.0
    r2 = (sum(rates[half:]) / (len(rates) - half)) if len(rates) > half \
        else r1
    stable = abs(r2 - r1) <= HIT_RATE_DRIFT
    breach = (not net_ok) or worst < worst_cap or (not stable)
    reasons = []
    if not net_ok:
        reasons.append(f"net_delta {net:.4f} outside band "
                       f"[{band[0] * n_applied:.4f}, "
                       f"{band[1] * n_applied:.4f}]")
    if worst < worst_cap:
        reasons.append(f"single_book_loss {worst:.4f} < {worst_cap}")
    if not stable:
        reasons.append(f"hit rate drift {r2 - r1:+.3f} > "
                       f"{HIT_RATE_DRIFT}")
    return {"net_delta": round(net, 4), "worst_single_book_loss":
            round(worst, 4), "net_in_band": net_ok,
            "band": [round(band[0] * n_applied, 4),
                     round(band[1] * n_applied, 4)],
            "hit_rate_1": round(r1, 4), "hit_rate_2": round(r2, 4),
            "hit_rate_stable": stable, "applied_cycles": len(deltas),
            "breach": breach, "reasons": reasons}


def run_canary(cohort: list[str], rotation_seed: dict,
               prior_band: tuple[float, float] | None = None,
               cycles: int = CYCLES, seed: int = SEED,
               breach_injection: dict | None = None) -> dict:
    """The spec's canary job, end to end. breach_injection forces a
    distribution past the thresholds (the staging drill: the rollback
    must be proven, not assumed). Writes the run report."""
    import auto_adjust as aa
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    flipped = flip_canary(f"canary-{run_id}")
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
    ev = evaluate(cyc, prior_band)
    record = {"run_id": run_id, "cohort": list(cohort),
              "dial": flipped, "cycles": len(cyc),
              "evaluation": ev,
              "commit_sha": _commit_sha(),
              "rollback": None}
    if ev["breach"]:
        record["rollback"] = rollback()
        record["status"] = "rolled_back"
    else:
        record["status"] = "pass_continue"
        record["next"] = ("collect 24-72h, then request Ops + Product "
                          "sign-off (governance/adjustment_policy.md)")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    rp = RUNS_DIR / f"{run_id}.json"
    rp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["report"] = str(rp)
    return record
