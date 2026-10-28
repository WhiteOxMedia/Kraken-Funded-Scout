"""test_xmrj_rpc_endpoint.py — THE ENDPOINT CONTRACT (Oct 27).

WHY THIS EXISTS. Both XMRJ clients talk to a cluster, and until Oct 27 each one
chose that cluster its own way: the harness read `XMRJ_RPC_URL` and refused to
run without it, while the suite created its provider at load time and inherited
whatever the process environment happened to hold. Three devnet passes (the
execution report's §11.9) died on a `429` from the free public endpoint, and the
fix (§11.11) only holds if ONE layer chooses the endpoint and both clients go
through it: the moment a client can build a connection of its own and default to
a cluster, a run stops being reproducible from the environment — and a CI job
can go green against an endpoint nobody intended.

WHAT IT CHECKS, in the source and in the wiring, with no chain, no network and
no toolchain. It reads files; the layer's BEHAVIOUR is
tests/xmrj_rpc_resilience.ts's job, and section 6 pins that the gate runs it):

  1. the layer exists and exports the surface the clients use, and its retry set
     is the transport failures only — 429 and 5xx, never another 4xx
  2. the endpoint comes from `XMRJ_RPC_URL` then `ANCHOR_PROVIDER_URL` (in that
     order), and the layer returns nothing when both are unset instead of
     guessing a cluster; the confirmation window comes from
     `XMRJ_CONFIRM_TIMEOUT_MS`
  3. both clients import that layer, build their provider/connection from it,
     and pass no cluster hostname to a constructor — a deliberate dead endpoint
     (loopback) is allowed, because tools/xmrj_loadtest.ts keeps one to prove a
     dead RPC aborts rather than retries for ever
  4. the runbook executor resolves one endpoint and hands it to every child, and
     the graded driver refuses the live half without one
  5. the retry loop stays BOUNDED — an attempt count and a wall-clock budget,
     both consulted on every path out of the loop
  6. the gate exists: `package.json` has a script for the layer's tests, the CI
     workflow runs both that script and this file, and the pipeline gate waits
     on it — a contract nothing runs is documentation
  7. RED-FIRST: every check above is shown to be able to FAIL, by breaking the
     property in a copy of the tree and re-running this file against the copy

READ-ONLY apart from a temporary directory: it builds nothing, deploys nothing,
signs nothing, and mutates only files it has copied.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Section 7 re-runs this file against a mutated COPY of the tree by pointing the
# root elsewhere; every path below is relative to it.
ROOT = Path(os.environ.get("XMRJ_CONTRACT_ROOT") or HERE)
_SELF = Path(__file__).resolve()

LAYER = "tools/rpc_resilience.ts"
SUITE = "tests/xmrj_devnet_e2e.ts"
HARNESS = "tools/xmrj_loadtest.ts"
LAYER_TESTS = "tests/xmrj_rpc_resilience.ts"
RUNNER = "tools/xmrj_devnet_run.sh"
DRIVER = "test_xmrj_devnet_e2e.py"
WORKFLOW = ".github/workflows/ci.yml"
PACKAGE = "package.json"

results: list[tuple[bool, str, object]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def code_lines(rel: str) -> list[str]:
    """The lines of a TS file that are CODE, with block and full-line comments
    dropped. Inline trailing comments are left alone: a `//` inside a string
    (every URL in these files) is not a comment, and treating it as one would
    hide exactly the literals this drive is looking for."""
    text = re.sub(r"/\*.*?\*/", "", read(rel), flags=re.S)
    return [
        line for line in text.splitlines()
        if line.strip() and not re.match(r"\s*(//|\*)", line)
    ]


def body(rel: str, signature: str) -> str:
    """One exported function's source text, signature to its closing brace."""
    found = re.search(rf"export function {signature}\(.*?\n\}}", read(rel), flags=re.S)
    return found.group(0) if found else ""


# ── 1. the layer, and what it is allowed to retry ────────────────────────
layer = read(LAYER)
check(
    "1.1 tools/rpc_resilience.ts exists — the one place both clients build a "
    "provider from (report §11.11)",
    (ROOT / LAYER).is_file(),
    "missing",
)

_expected_exports = [
    "providerUrl", "confirmTimeoutMs", "resilientFetch",
    "makeConnection", "makeProvider", "rpcStats", "resetRpcStats",
]
_missing_exports = [
    name for name in _expected_exports
    if not re.search(rf"export (function|const) {name}\b", layer)
]
check(
    "1.2 the layer exports the surface both clients use — the endpoint reader, the "
    "retry wrapper, the connection/provider builders, and the counters",
    not _missing_exports,
    _missing_exports,
)

_retry_set = re.search(r"export const RETRYABLE_STATUS\s*=\s*\[([^\]]*)\]", layer)
_statuses = [int(n) for n in re.findall(r"\d+", _retry_set.group(1))] if _retry_set else []
_not_transport = [s for s in _statuses if s != 429 and not 500 <= s <= 599]
check(
    "1.3 the retry set is 429 and 5xx ONLY — a 400/403/404 is not a busy endpoint, "
    "and a program refusal arrives as a JSON-RPC error inside an HTTP 200 that this "
    "set must never touch",
    bool(_statuses) and not _not_transport,
    f"RETRYABLE_STATUS = {_statuses}",
)

# ── 2. the endpoint comes from the environment ──────────────────────────
_provider_body = body(LAYER, "providerUrl")
# NB: whole names, not substrings — `XMRJ_RPC_URL_FALLBACK` would otherwise read
# as the very variable this check is about (and 2.3's mutation proved it).
def _at(text: str, name: str) -> int:
    found = re.search(rf"\b{name}\b", text)
    return found.start() if found else -1


_names = [_at(_provider_body, "XMRJ_RPC_URL"), _at(_provider_body, "ANCHOR_PROVIDER_URL")]
check(
    "2.1 the endpoint is `XMRJ_RPC_URL`, then `ANCHOR_PROVIDER_URL` — the names the "
    "harness, the runbook executor and the Anchor CLI already share, read in that "
    "order",
    all(i >= 0 for i in _names) and _names[0] < _names[1],
    _names,
)
check(
    "2.2 neither set means NO endpoint (null), never a default cluster — a run that "
    "inherits a stray cluster is the failure this rule exists to prevent",
    ": null" in _provider_body and "https://" not in _provider_body,
    "providerUrl falls back to something other than null",
)

_confirm = body(LAYER, "confirmTimeoutMs")
check(
    "2.3 the confirmation window is read from XMRJ_CONFIRM_TIMEOUT_MS, with the "
    "documented 180 s default — the 30 s legacy path is what threw "
    "TransactionExpiredTimeoutError while the endpoint was throttling",
    _at(_confirm, "XMRJ_CONFIRM_TIMEOUT_MS") >= 0 and "180_000" in _confirm,
    _confirm[:120],
)

_connection_body = body(LAYER, "makeConnection")
check(
    "2.4 the builder installs that window and the resilient fetch on the Connection "
    "itself (so web3's own five-attempt loop never sees a 429 it would give up on)",
    "confirmTransactionInitialTimeout" in _connection_body
    and "resilientFetch(" in _connection_body,
    "the Connection is built without the window or without the wrapper",
)

_provider_fn = body(LAYER, "makeProvider")
check(
    "2.5 makeProvider refuses to run without an endpoint instead of guessing one",
    "if (!url)" in _provider_fn and "throw new Error" in _provider_fn,
    "makeProvider would build a provider with no endpoint",
)

# ── 3. both clients go through the layer ────────────────────────────────
for _n, (_label, _rel, _marker) in enumerate(
    (("the devnet suite", SUITE, "makeProvider("), ("the stress harness", HARNESS, "makeConnection(")),
    start=1,
):
    _text = read(_rel)
    check(
        f"3.{_n} {_label} is built from the layer — it imports it and calls {_marker.rstrip('(')}",
        "rpc_resilience" in _text and _marker in _text,
        f"{_rel}: no {_marker.rstrip('(')} call, or no import",
    )

# A constructor may take an EXPRESSION (the endpoint the layer resolved). It may
# not take a quoted cluster: that is a client choosing a cluster of its own. A
# loopback literal is allowed on purpose — the harness keeps one to prove a DEAD
# endpoint aborts rather than retries for ever.
_CONSTRUCTORS = re.compile(
    r"new\s+(?:anchor\.web3\.|anchor\.)?(?:Connection|AnchorProvider)\s*\(\s*([^,)\n]+)"
)
_cluster_literals: dict[str, list[str]] = {}
for _rel in (SUITE, HARNESS):
    for _line in code_lines(_rel):
        for _arg in _CONSTRUCTORS.findall(_line):
            _arg = _arg.strip()
            _loopback = re.match(r"[\"'`]https?://(127\.0\.0\.1|localhost)(:\d+)?", _arg)
            if _arg[:1] in "\"'`" and not _loopback:
                _cluster_literals.setdefault(_rel, []).append(_arg)
check(
    "3.3 no client passes a cluster hostname to a Connection or a provider "
    "constructor — the endpoint is always the expression the layer resolved (a "
    "loopback literal stays legal: the harness's dead-endpoint scenario needs one)",
    not _cluster_literals,
    _cluster_literals,
)

# ── 4. one endpoint, all the way down ───────────────────────────────────
runner = read(RUNNER)
check(
    "4.1 the runbook executor resolves ONE endpoint (XMRJ_RPC_URL, else "
    "ANCHOR_PROVIDER_URL, else the named cluster) and exports it, so every step "
    "runs against the cluster the operator named",
    "XMRJ_RPC_URL:-${ANCHOR_PROVIDER_URL:-" in runner and "export XMRJ_RPC_URL=" in runner,
    "the executor does not resolve/export a single endpoint",
)
_children = [
    line for line in runner.splitlines()
    if "test_xmrj_devnet_e2e.py" in line or "xmrj_loadtest.ts" in line
]
_unwired = [line.strip() for line in _children if "XMRJ_RPC_URL='$RPC_URL'" not in line]
check(
    "4.2 both client steps pass that resolved URL through — neither is left to read "
    "whatever the shell happens to hold",
    bool(_children) and not _unwired,
    _unwired or "no client step found",
)

driver = read(DRIVER)
check(
    "4.3 the graded driver refuses the live half when no endpoint is set, and names "
    "the variables it accepts",
    "XMRJ_RPC_URL" in driver and "ANCHOR_PROVIDER_URL" in driver,
    "the driver would run the live half with no endpoint named",
)

# ── 5. the loop stays bounded ───────────────────────────────────────────
_attempt_bound = len(re.findall(r"attempt >= maxAttempts", layer))
_budget_bound = len(re.findall(r"now\(\) - started >= budgetMs", layer))
check(
    "5.1 the layer is bounded twice over — an attempt count AND a wall-clock budget, "
    "and the loop consults both before every retry",
    _attempt_bound >= 2 and _budget_bound >= 2,
    f"attempt bound seen {_attempt_bound}x, budget bound seen {_budget_bound}x",
)

# ── 6. a contract nothing runs is documentation ─────────────────────────
try:
    package = json.loads(read(PACKAGE))
except json.JSONDecodeError:
    package = {}
_script = (package.get("scripts") or {}).get("test:rpc", "")
check(
    "6.1 package.json has a `test:rpc` script that runs the layer's own tests, so "
    "the behaviour and this contract are one command to run",
    "tests/xmrj_rpc_resilience.ts" in _script,
    _script or "no test:rpc script",
)

workflow = read(WORKFLOW)
check(
    "6.2 the CI workflow runs `npm run test:rpc` and this file — otherwise a push can "
    "regress the layer and nobody finds out until a live devnet run does",
    bool(re.search(r"\n  xmrj-rpc-contract:\n", workflow))
    and "npm run test:rpc" in workflow
    and "python test_xmrj_rpc_endpoint.py" in workflow,
    "the workflow does not run both, or has no xmrj-rpc-contract job",
)
check(
    "6.3 that job is in the pipeline gate's `needs`, so it cannot be skipped while "
    "the pipeline still reports success",
    bool(re.search(r"needs:[\s\S]*?- xmrj-rpc-contract", workflow)),
    "xmrj-rpc-contract is not in the ci job's needs",
)

# ── 7. RED-FIRST: every check above must be able to fail ────────────────
# Each mutation breaks ONE property in a copy of the tree and re-runs this file
# against that copy. A check that cannot go red is a check nobody should trust,
# and this section is what keeps a future edit from quietly emptying one.
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("1.2", LAYER, "export function resetRpcStats", "function resetRpcStats"),
    ("1.3", LAYER, "export const RETRYABLE_STATUS = [429, 500, 502, 503, 504];",
     "export const RETRYABLE_STATUS = [400, 429, 500, 502, 503, 504];"),
    ("2.1", LAYER, "env.XMRJ_RPC_URL ?? env.ANCHOR_PROVIDER_URL",
     "env.ANCHOR_PROVIDER_URL ?? env.XMRJ_RPC_URL"),
    ("2.2", LAYER, 'return url && url.trim() !== "" ? url.trim() : null;',
     'return url && url.trim() !== "" ? url.trim() : "https://api.devnet.solana.com";'),
    ("2.3", LAYER, "Number(env.XMRJ_CONFIRM_TIMEOUT_MS)", "Number(env.XMRJ_CONFIRM_TIMEOUT_MS_UNUSED)"),
    ("2.4", LAYER, "confirmTransactionInitialTimeout: confirmTimeoutMs(),", ""),
    ("2.5", LAYER, "if (!url) {", "if (false) {"),
    ("3.1", SUITE, "makeProvider(", "legacyInlineProvider("),
    ("3.3", SUITE, "const OWNER_COUNT = 3;",
     'const OWNER_COUNT = 3;\nconst stray = new anchor.web3.Connection("https://api.mainnet-beta.solana.com", "confirmed");'),
    ("4.2", RUNNER, "XMRJ_RPC_URL='$RPC_URL' ANCHOR_WALLET='$WALLET_NATIVE' npx ts-node tools/xmrj_loadtest.ts",
     "ANCHOR_WALLET='$WALLET_NATIVE' npx ts-node tools/xmrj_loadtest.ts"),
    ("4.3", DRIVER, "ANCHOR_PROVIDER_URL", "SOME_OTHER_URL"),
    ("5.1", LAYER, "attempt >= maxAttempts", "attempt >= 99"),
    ("6.1", PACKAGE, '    "test:rpc": "ts-mocha -p ./tsconfig.json -t 120000 \\"tests/xmrj_rpc_resilience.ts\\"",\n', ""),
    # NB: the copies are read in text mode, so Python has already turned CRLF
    # into LF here — the workflow's own line endings are pinned by CI, not by us.
    ("6.2", WORKFLOW, "        run: python test_xmrj_rpc_endpoint.py\n", ""),
    ("6.3", WORKFLOW, "      - xmrj-rpc-contract\n", ""),
]

FILES = (LAYER, SUITE, HARNESS, LAYER_TESTS, RUNNER, DRIVER, WORKFLOW, PACKAGE)


def run_against(root: Path) -> tuple[int, list[str], str]:
    """This file, against another root: (rc, failed-check ids, raw output).

    The id is the first token of a FAIL line (`FAIL  2.2 neither set means …`),
    read positionally so a non-UTF-8 console cannot turn a red check into a
    silently unmatched one."""
    env = dict(os.environ, XMRJ_CONTRACT_ROOT=str(root))
    proc = subprocess.run(
        [sys.executable, str(_SELF)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(root),
    )
    failed = [
        line.split()[1]
        for line in proc.stdout.splitlines()
        if line.startswith("FAIL") and len(line.split()) > 1
    ]
    return proc.returncode, failed, proc.stdout


if not os.environ.get("XMRJ_CONTRACT_ROOT"):
    _control_root = Path(tempfile.mkdtemp(prefix="xmrj-contract-"))
    for _rel in FILES:
        _dst = _control_root / _rel
        _dst.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / _rel).is_file():
            shutil.copy2(ROOT / _rel, _dst)
    _rc, _failed, _out = run_against(_control_root)
    check(
        "7.1 control: the UNMUTATED copy of the tree passes every check above — "
        "without this, a mutation that goes red proves nothing about the check",
        _rc == 0 and not _failed,
        _failed or f"rc {_rc}: {_out.strip().splitlines()[-1] if _out.strip() else 'no output'}",
    )
    shutil.rmtree(_control_root, ignore_errors=True)

    _stale = []
    _blind = []
    for _id, _rel, _old, _new in MUTATIONS:
        _tmp = Path(tempfile.mkdtemp(prefix="xmrj-contract-"))
        try:
            for _f in FILES:
                _dst = _tmp / _f
                _dst.parent.mkdir(parents=True, exist_ok=True)
                if (ROOT / _f).is_file():
                    shutil.copy2(ROOT / _f, _dst)
        except OSError as err:  # a copied tree the mutation cannot reach is a broken drive
            _stale.append(f"{_id}: {err}")
            shutil.rmtree(_tmp, ignore_errors=True)
            continue
        _target = _tmp / _rel
        _text = _target.read_text(encoding="utf-8")
        if _old not in _text:
            _stale.append(f"{_id}: mutation no longer applies to {_rel}")
            shutil.rmtree(_tmp, ignore_errors=True)
            continue
        _target.write_text(_text.replace(_old, _new), encoding="utf-8")
        _rc, _failed, _ = run_against(_tmp)
        shutil.rmtree(_tmp, ignore_errors=True)
        if _id not in _failed:
            _blind.append(f"{_id} kept passing once {_rel} was mutated")
    check(
        "7.2 every mutation in this section still applies to the file it names — a "
        "mutation that no longer matches has stopped testing anything",
        not _stale,
        _stale,
    )
    check(
        f"7.3 RED-FIRST: each of the {len(MUTATIONS)} mutations breaks its property in "
        f"a copy of the tree and trips the check that owns it, while the other checks "
        f"stay green (rc != 0 and the expected FAIL line, per mutation)",
        not _blind and not _stale,
        _blind,
    )

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
