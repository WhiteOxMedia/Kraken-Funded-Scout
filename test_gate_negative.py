"""test_gate_negative.py — THE NEGATIVE PROOF's own tripwire.

WHY THIS EXISTS: tests/xmrj_gate_negative.ts is the only artifact that claims a
NON-AUTHORITY signer cannot initialize a gated program, and that claim is worth
nothing unless the suite really runs its checks on-chain: a skipped suite exits
0, a weakened assertion exits 0, and a harness that forgets to re-id the
throwaway program would fail to deploy and never get that far. This drive reads
the suite, the runner and the gated crate and pins all of it offline — with the
mutations in section 5 fed through the SAME predicate, so "it passes" and "it
discriminates" are one code path.

THE LIVE HALF is opt-in (XMRJ_GATE_NEGATIVE=1) and runs
tests/negative_gate_runner.py, which builds a throwaway deployment of the real
xmrj_jackpot crate and runs the suite against it. On a box without the pinned
toolchain this prints SKIP and names what is missing — a devnet proof that
spends SOL must never start because a test run was warm.

READ-ONLY ON THE WORKSPACE: nothing here writes, builds or signs; the runner
it delegates to is the only thing that spends anything, and only when opted in.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE / "tests" / "xmrj_gate_negative.ts"
RUNNER = HERE / "tests" / "negative_gate_runner.py"
GATED = HERE / "xmrj_jackpot" / "src" / "lib.rs"
MANIFEST = HERE / "Anchor.toml"
LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111111"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"

results = []


def check(name, cond, why=""):
    results.append((bool(cond), name, why))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  ({why})" if why and not cond else ""))


def read(path):
    return Path(path).read_text(encoding="utf-8")


def _sum_terms(expr):
    """'8 + 8 + 32' -> 48, by hand: this drive reads repository text and must
    never eval() it."""
    total = 0
    for term in expr.split("+"):
        total += int(term.strip())
    return total


def _ts_consts(text):
    """The suite's byte-layout constants, with earlier names substituted."""
    vals = {}
    for m in re.finditer(r"const (STATE_LEN|OFF_[A-Z_]+) = ([^;]+);", text):
        expr = m.group(2)
        for name, v in vals.items():
            expr = expr.replace(name, str(v))
        try:
            vals[m.group(1)] = _sum_terms(expr.strip())
        except ValueError:
            return {}
    return vals


_FIELD_SIZES = {"u64": 8, "i64": 8, "u32": 4, "Pubkey": 32}


def _rust_layout(src):
    """(total_len, {field: offset}) from JackpotState, discriminator included."""
    s = re.search(r"#\[account\]\s*pub struct JackpotState\s*\{", src)
    if not s:
        return None, {}
    depth, end = 1, s.end()
    for i in range(s.end(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    fields = re.findall(r"pub\s+(\w+)\s*:\s*([A-Za-z0-9_]+)\s*,",
                        src[s.end():end])
    offsets, cursor = {}, 8
    for name, typ in fields:
        offsets[name] = cursor
        cursor += _FIELD_SIZES.get(typ, 0)
    return cursor, offsets


_UNKNOWN = object()


def _harness_problems(ts, runner, gated_src, manifest_text):
    """ONE predicate for the whole negative proof. Returns {problem: detail}."""
    problems = {}

    # 0. the suite must be SKIPPED unless the runner started it - otherwise
    # `npx ts-mocha "tests/**/*.ts"` would need devnet and a wallet.
    if 'const PROGRAM_ID_STR = process.env.XMRJ_GATE_PROGRAM_ID || "";' not in ts:
        problems["skip_env"] = "the suite does not read XMRJ_GATE_PROGRAM_ID"
    # Registration, not `describe.skip` alone: mocha EXECUTES a skipped
    # suite's body, so a bare skip would still call AnchorProvider.env() while
    # loading the file on a box with no ANCHOR_PROVIDER_URL.
    if ("function negativeSuite" not in ts or "if (PROGRAM_ID_STR)" not in ts
            or "describe.skip(" not in ts):
        problems["skip_guard"] = (
            "the suite is registered unconditionally, so loading it needs a "
            "provider URL and the ordinary tests glob breaks")

    # 1. the cluster guard: this file spends SOL.
    guard = re.search(r'DEVNET_GENESIS_HASH = "([^"]+)"', ts)
    if not guard or guard.group(1) != DEVNET_GENESIS:
        problems["genesis"] = guard.group(1) if guard else "no constant"
    if "getGenesisHash()" not in ts or "REFUSING TO RUN" not in ts:
        problems["genesis_guard"] = "the hash is not read and enforced"

    # 2. the expected error code is COMPUTED by the runner from the Rust enum
    #    and handed to the suite - a literal in the suite would stale silently.
    if "XMRJ_GATE_ERROR" not in ts:
        problems["code_env"] = "the suite does not read the computed code"
    if "XMRJ_GATE_ERROR" not in runner:
        problems["code_env_runner"] = "the runner does not set the code"
    # The runner's own source spells the pattern with escapes, so this looks
    # for the two things that make it real: the enum it parses and the base
    # Anchor numbers its variants from.
    if "pub enum ErrorCode" not in runner or "6000" not in runner:
        problems["code_from_enum"] = (
            "the runner does not read the error index from the enum")
    literal = re.search(r"Custom[^\n]*\b6\d{3}\b", ts) or re.search(r"\b600[0-9]\b", ts)
    if literal:
        problems["code_literal"] = (
            "the suite hardcodes a custom code (" + literal.group(0)
            + "): reordering the Rust enum would leave it green")

    # 3. the rejection is asserted three ways: the exact code, the log naming
    #    the gate, and a real send that must not land.
    if "InstructionError?.[1]?.Custom" not in ts \
            or ".to.equal(EXPECTED_CODE)" not in ts:
        problems["code_assertion"] = "the custom code is not asserted exactly"
    if not re.search(r"/NotUpgradeAuthority\|ProgramIsImmutable\|NotProgramData/",
                     ts):
        problems["log_assertion"] = (
            "the failure is not required to NAME the gate, so a rejection for "
            "another reason would read as a pass")
    if "sendAndConfirmTransaction(connection, tx, [outsider])" not in ts \
            or "to.equal(\n      false,\n    )" not in ts:
        problems["must_not_land"] = (
            "the suite never requires the outsider's transaction to fail to land")

    # 4. the negative must run while the state is still UNCREATED, and must be
    #    re-read afterwards: a rejection that still wrote the state is not a
    #    proof of the gate.
    if not re.search(r"expect\(\s*stateBefore,", ts) \
            or "already exists, so a rejected" not in ts:
        problems["state_precondition"] = (
            "no precondition that the state PDA is empty before the attempt")
    if "getAccountInfo(jackpotState)).to.equal(null)" not in ts:
        problems["state_after"] = (
            "the state is not re-read as uncreated after the rejection")

    # 5. the POSITIVE control: the authority CAN initialize, and the state it
    #    writes has the pinned layout. Without it the suite would also pass if
    #    the gate rejected everyone.
    if "signedTx(authority)" not in ts:
        problems["positive_control"] = (
            "the authority's own initialize is never exercised")
    layout_len, offsets = _rust_layout(gated_src)
    consts = _ts_consts(ts)
    if not consts:
        problems["ts_layout_constants"] = "the suite's layout constants do not parse"
    else:
        space = re.search(r"JACKPOT_STATE_SPACE:\s*usize\s*=\s*([0-9+*() ]+);",
                          gated_src)
        declared = _sum_terms(space.group(1)) if space else None
        if declared != layout_len:
            problems["rust_space"] = {"declared": declared,
                                      "fields_sum_to": layout_len}
        expected = {
            "STATE_LEN": layout_len,
            "OFF_TREASURY": offsets.get("treasury_program", _UNKNOWN),
            "OFF_ADMIN": offsets.get("admin_authority", _UNKNOWN),
            "OFF_MAX_BPS": offsets.get("max_payout_bps", _UNKNOWN),
        }
        diff = {k: {"suite": consts.get(k), "rust": v}
                for k, v in expected.items() if consts.get(k) != v}
        if diff:
            problems["layout_mismatch"] = diff
    if "to.equal(10_000)" not in ts:
        problems["uncapped_assertion"] = "the 10 000 bps cap is not asserted"

    # 6. the accounts the suite sends match the gated struct's own list and
    #    seeds: the negative attempt must be a REAL initialize call, not a
    #    malformed one that fails for an unrelated reason.
    # The ProgramData account is NOT in this list on purpose: the loader
    # derives it as `[<program id>]` alone, so there is no seed literal to
    # share — a `"programdata"` prefix derives an address with no account at
    # it, which is NotProgramData (6004) on chain.
    for seed, where in (("jackpot_state", "JACKPOT_STATE_SEED"),
                        ("governance_authority", "GOVERNANCE_AUTHORITY_SEED")):
        if f'{where} = "{seed}"' not in ts:
            problems["seed:" + seed] = "the suite does not use the crate's literal"
    for lit in (LOADER_ID, "AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe"):
        if lit not in gated_src:
            problems["crate_literal:" + lit[:12]] = "not declared in the crate"
    ids = tomllib.loads(manifest_text).get("programs", {}).get("devnet", {})
    governance_id = ids.get("xmrj_governance")
    if governance_id and governance_id not in ts:
        problems["governance_id"] = (
            "the suite's governance program id is not the manifest's")
    if not re.search(r"keys:\s*\[\s*\{ pubkey: jackpotState", ts):
        problems["account_list"] = "the initialize account list is not explicit"

    # 7. the runner: the REAL crate, re-idd, built, deployed, closed, and it
    #    must refuse to start without the toolchain or the SOL.
    for needle, label in (
            ('GATED_CRATE = ROOT / "xmrj_jackpot"',
             "the runner does not copy the REAL gated crate"),
            (r'declare_id!\("[^"]+"\)',
             "the runner does not rewrite declare_id!"),
            ('"[workspace]"', "the copy is not detached from any workspace"),
            ('"build-sbf"', "the runner does not build with cargo build-sbf"),
            ('"--program-id"', "the deploy is not pinned to the new keypair"),
            ("solana-keygen", "the runner does not generate a throwaway keypair"),
            ('"program", "close"', "the runner does not close the throwaway program"),
            ("WARNING: program close failed",
             "a cleanup failure is not kept non-fatal"),
            ("XMRJ_GATE_NEGATIVE", "no opt-in gate"),
            ("MIN_LAMPORTS", "no funding precondition"),
            ("5 passing", "the runner does not require the suite's checks to run"),
    ):
        if needle not in runner:
            problems[label] = "missing: " + needle

    # 8. the suite must verify the wallet really IS the authority, or the
    #    negative case would be testing some other deployment's gate.
    if "recordedAuthority?.toBase58()" not in ts:
        problems["authority_check"] = (
            "the suite does not assert this wallet is the program's upgrade "
            "authority")
    return problems


try:
    _ts = read(SUITE)
    _runner = read(RUNNER)
    _gated = read(GATED)
    _manifest = read(MANIFEST)
except OSError as exc:                                     # noqa: BLE001
    print("FAIL  the negative proof's artifacts are missing: {}".format(exc))
    print("RESULT: 0 passed, 1 failed")
    sys.exit(1)

_problems = _harness_problems(_ts, _runner, _gated, _manifest)

check("1.1 the suite is SKIPPED unless the runner started it (no devnet, no "
      "wallet and no SOL for the ordinary `npx ts-mocha \"tests/**/*.ts\"` run)",
      "skip_env" not in _problems and "skip_guard" not in _problems,
      {k: v for k, v in _problems.items() if k.startswith("skip")})

check("1.2 it runs against DEVNET and nowhere else, by genesis hash read live "
      "and enforced (this file spends SOL)",
      not any(k.startswith("genesis") for k in _problems),
      {k: v for k, v in _problems.items() if k.startswith("genesis")})

check("2.1 the expected error code is COMPUTED from the Rust enum by the "
      "runner and asserted EXACTLY by the suite — a literal in the suite (or "
      "a code read from nowhere) would go stale the day a variant is added",
      not any(k.startswith("code") for k in _problems),
      {k: v for k, v in _problems.items() if k.startswith("code")})

check("2.2 the rejection is proved three ways: the exact custom code, the "
      "program log NAMING the gate, and a real send that must not land",
      "log_assertion" not in _problems and "must_not_land" not in _problems,
      {k: v for k, v in _problems.items()
       if k in ("log_assertion", "must_not_land")})

check("2.3 the negative case runs while the state is still UNCREATED and is "
      "re-read uncreated afterwards — once the state exists, every initializer "
      "fails on `init` and the rejection would prove nothing",
      "state_precondition" not in _problems and "state_after" not in _problems,
      {k: v for k, v in _problems.items()
       if k in ("state_precondition", "state_after")})

check("3.1 the POSITIVE control is present and its byte layout matches the "
      "crate's OWN struct — computed from JackpotState's fields, not copied "
      "from the suite's constants — so the gate cannot pass by rejecting "
      "everyone or by writing a different shape",
      not any(k in _problems for k in ("positive_control", "layout_mismatch",
                                       "rust_space", "ts_layout_constants",
                                       "uncapped_assertion")),
      {k: v for k, v in _problems.items()
       if k in ("positive_control", "layout_mismatch", "rust_space",
                "ts_layout_constants", "uncapped_assertion")})

check("3.2 the attempt is a REAL initialize call: the crate's own seed "
      "literals, the manifest's governance program id, the loader id and an "
      "explicit account list — a malformed call would fail for a reason that "
      "has nothing to do with the gate",
      not any(k.startswith(("seed:", "crate_literal:", "governance_id"))
              or k == "account_list" for k in _problems),
      {k: v for k, v in _problems.items()
       if k.startswith(("seed:", "crate_literal:")) or k in
       ("governance_id", "account_list")})

check("4.1 the runner builds a THROWAWAY deployment of the REAL crate "
      "(re-idd, detached, built with cargo build-sbf, deployed to a fresh "
      "keypair), closes it afterwards, keeps cleanup non-fatal, refuses to "
      "start without the toolchain or the SOL, and requires the suite's five "
      "checks to have actually run",
      not any(k in _problems for k in
              ("no opt-in gate", "no funding precondition",
               "the runner does not require the suite's checks to run"))
      and not [k for k in _problems if "runner" in k.lower()
               or "workspace" in k or "crate" in k or "keypair" in k
               or "deploy" in k or "cleanup" in k or "close" in k],
      {k: v for k, v in _problems.items()
       if "runner" in k.lower() or "crate" in k or "keypair" in k
       or "deploy" in k or "close" in k or "cleanup" in k
       or k in ("no opt-in gate", "no funding precondition",
                "the runner does not require the suite's checks to run")})

check("4.2 the suite first proves this wallet IS the throwaway program's "
      "recorded upgrade authority — without that, a failure would say nothing "
      "about the gate in the shipped crates",
      "authority_check" not in _problems, _problems.get("authority_check"))

# ── 5. RED-FIRST: mutations through the SAME predicate ──────────────────
_mutations = [
    ("the state-empty precondition dropped",
     _ts.replace("expect(\n      stateBefore,", "expect(\n      true,", 1),
     _runner, "state_precondition"),
    ("the exact-code assertion weakened to 'it failed somehow'",
     _ts.replace(".to.equal(EXPECTED_CODE);", ".to.not.equal(null);", 1),
     _runner, "code_assertion"),
    ("the code hardcoded in the suite instead of read from the environment",
     _ts.replace("const EXPECTED_CODE = Number(process.env.XMRJ_GATE_ERROR || \"0\");",
                 "const EXPECTED_CODE = 6008;", 1),
     _runner, "code_env"),
    ("the requirement that the transaction not land dropped",
     _ts.replace(".to.equal(\n      false,\n    );", ".to.equal(true);", 1),
     _runner, "must_not_land"),
    ("the positive control removed",
     _ts.replace("const tx = await signedTx(authority);",
                 "const tx = await signedTx(outsider);", 1),
     _runner, "positive_control"),
    ("the runner no longer rewriting declare_id!",
     _ts, _runner.replace(r'declare_id!\("[^"]+"\)', "nomatch", 1),
     "the runner does not rewrite declare_id!"),
    ("the runner no longer closing the throwaway program",
     _ts, _runner.replace('"program", "close"', '"program", "show"', 1),
     "the runner does not close the throwaway program"),
    ("the runner accepting a vacuous (skipped) suite run",
     _ts, _runner.replace('re.search(r"\\b5 passing\\b", out)', "True", 1),
     "the runner does not require the suite's checks to run"),
    ("the suite registered unconditionally (the provider read at load time)",
     _ts.replace("if (PROGRAM_ID_STR) {", "if (true) {", 1), _runner,
     "skip_guard"),
]
_missed = []
for why, ts_m, runner_m, want in _mutations:
    if ts_m == _ts and runner_m == _runner:
        _missed.append((why, "mutation did not apply — fixture is stale"))
        continue
    got = _harness_problems(ts_m, runner_m, _gated, _manifest)
    if want not in got:
        _missed.append((why, {"wanted": want, "got": sorted(got)}))
check("5.1 RED-FIRST: nine mutations — a dropped state precondition, a "
      "weakened code assertion, a hardcoded code, a dropped must-not-land, a "
      "removed positive control, no declare_id! rewrite, no cleanup, a "
      "suite registered unconditionally, and a runner that would accept a "
      "skipped suite — each trip the SAME "
      "predicate the real harness passes",
      not _missed, _missed)

# ── 6. THE LIVE HALF: opt-in, or an honest SKIP ─────────────────────────
def live_skip_reason(env=None, which=None, wallet_path=None):
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    if env.get("XMRJ_GATE_NEGATIVE") != "1":
        return ("XMRJ_GATE_NEGATIVE is not 1 — the live half builds and "
                "DEPLOYS a throwaway program to devnet and spends SOL")
    missing = [t for t in ("solana", "solana-keygen", "cargo") if which(t) is None]
    if missing:
        return "not on PATH: " + ", ".join(missing)
    wallet = Path(os.path.expanduser(
        env.get("ANCHOR_WALLET") or "~/.config/solana/id.json"))
    if wallet_path is not None:
        wallet = Path(wallet_path)
    if not wallet.is_file():
        return "no wallet at {}".format(wallet)
    return None


check("6.1 the live half is OFF unless XMRJ_GATE_NEGATIVE=1, and each "
      "precondition it names (toolchain, wallet) is one the run needs",
      live_skip_reason({}, which=lambda _t: "/usr/bin/" + _t).startswith(
          "XMRJ_GATE_NEGATIVE")
      and live_skip_reason({"XMRJ_GATE_NEGATIVE": "1"},
                           which=lambda _t: None).startswith("not on PATH"),
      {"off_by_default": live_skip_reason({}, which=lambda _t: _t),
       "no_toolchain": live_skip_reason({"XMRJ_GATE_NEGATIVE": "1"},
                                        which=lambda _t: None)})

_skip = live_skip_reason()
if _skip:
    print("SKIP: " + _skip)
else:
    proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(HERE),
                          capture_output=True, text=True, timeout=3600)
    out = (proc.stdout or "") + (proc.stderr or "")
    if re.search(r"^\s*SKIP:", out, re.M):
        print(out.strip().splitlines()[0])
    else:
        check("6.2 the devnet negative proof ran and PASSED (a non-authority "
              "signer rejected with the gate's own code, the authority able "
              "to initialize)",
              proc.returncode == 0 and "PASS: a non-authority signer" in out,
              {"exit": proc.returncode,
               "tail": out.strip().splitlines()[-25:]})

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
