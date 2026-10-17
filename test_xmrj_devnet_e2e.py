"""test_xmrj_devnet_e2e.py — THE DEVNET SUITE'S OWN TRIPWIRE (Oct 16).

WHY THIS EXISTS: tests/xmrj_devnet_e2e.ts is the only artifact in this repo
that claims the WHOLE Solana side works â€” three deploys, two initializers, a
proposal that reaches its threshold, and a cross-program call that changes a
parameter on chain. It cannot run on the box that wrote it (no anchor, no
solana, no funded devnet wallet) and it will not run on most boxes that check
this repo out. An unrunnable file that nothing checks is a liability: it can
drift into a shape that would fail at the one moment it matters â€” the deploy.

So this drive does two things.

  THE STRUCTURAL HALF (always runs, no network, no toolchain): it reads the
  suite and the four Rust sources and pins every name the suite uses against
  the program it calls â€” instruction names (a camelCase call against the
  callee's `pub fn`), the account keys of every `.accounts({...})` block
  against that instruction's own Accounts struct, every PDA seed literal
  against the crates' `*_SEED` constants, the proposed GovernanceAction
  variant and its field against the Rust enum, the suite's `max_payout_bps`
  expectations against what `initialize` actually writes, the negative
  control, the genesis guard, and rerun safety. A rename on either side fails
  here instead of at deploy time. Section 4 feeds mutations back through the
  SAME predicate, so "it passes" and "it discriminates" are one code path.

  THE LIVE HALF (opt-in only): with anchor + solana on PATH, a wallet and a
  provider URL configured, and XMRJ_DEVNET_E2E=1, it runs the suite and grades
  mocha's own output. Otherwise it prints a SKIP line naming the missing
  piece â€” the fleet must never read "no devnet wallet on this box" as a
  regression. A devnet deploy spends SOL and mutates a shared cluster, which
  is exactly why it is not something a test run does by surprise.

SKIP CONTRACT: the structural checks run either way and are reported; the live
half announces itself with a `SKIP: <reason>` line (run_fleet.py's SKIP_RE).

READ-ONLY ON THE WORKSPACE: nothing here writes, builds, deploys or signs.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE / "tests" / "xmrj_devnet_e2e.ts"
PKG = HERE / "package.json"
TSCONFIG = HERE / "tsconfig.json"
MANIFEST = HERE / "Anchor.toml"
PROGRAMS = ("xmrj_treasury", "xmrj_jackpot", "xmrj_governance")
NOT_A_PROGRAM = "xmrj_conversion_service"
CRATES = PROGRAMS + (NOT_A_PROGRAM,)

# Read live from each cluster's own RPC (getGenesisHash), Oct 16 2026:
#   devnet        https://api.devnet.solana.com
#   mainnet-beta  https://api.mainnet-beta.solana.com
#   testnet       https://api.testnet.solana.com
# The suite may guard on the first and must NEVER be the other two; the values
# live here so a copy-paste of the mainnet hash cannot pass unnoticed.
GENESIS = {
    "devnet": "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG",
    "mainnet-beta": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    "testnet": "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY",
}

results = []


def check(name, cond, why=""):
    results.append((bool(cond), name, why))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  ({why})" if why and not cond else ""))


def read(path):
    return Path(path).read_text(encoding="utf-8")


def code(text):
    """Rust source with comments removed â€” the same rule the wiring drive
    uses: a comment mentioning a name must neither satisfy nor break a check.
    Deliberately duplicated rather than imported, because importing
    test_anchor_wiring.py would EXECUTE that drive and print its own RESULT
    line into this one's output."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def camel_to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def upper_camel(name):
    return "".join(part.capitalize() for part in camel_to_snake(name).split("_"))


def match_delim(text, start, opener, closer):
    """Index of the delimiter matching text[start] (which must be `opener`)."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


def object_keys(obj):
    """The keys of an object literal, shorthand or not. Anchor's client idiom
    writes accounts as `{{ treasuryState, adminAuthority: ... }}`, so a
    colon-only parser would silently validate nothing â€” the red-first fixture
    in section 4 is what caught that."""
    body, parts, depth, cur = obj[1:-1], [], 0, ""
    for ch in body:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    keys = set()
    for part in parts:
        m = re.match(r"\s*([A-Za-z_]\w*)", part)
        if m:
            keys.add(m.group(1))
    return keys


def call_args(suite, var, call):
    """The top-level arguments of `<var>.methods.<call>(...)`, or None. The
    suite formats these across lines and nests calls in them, so this walks
    delimiters rather than matching a shape."""
    m = re.search(rf"\b{re.escape(var)}\.methods\s*\.\s*{re.escape(call)}\s*\(",
                  suite)
    if not m:
        return None
    open_paren = suite.index("(", m.end() - 1)
    body = suite[open_paren + 1:match_delim(suite, open_paren, "(", ")")]
    args, depth, cur = [], 0, ""
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return [a for a in args if a]


def rust_param_count(src, fn):
    """How many parameters `pub fn <fn>` takes AFTER its Context — what the
    client must supply. None when the instruction or its parens cannot be
    read."""
    m = re.search(rf"pub fn {re.escape(fn)}\s*(?:<[^>]*>)?\s*\(", src)
    if not m:
        return None
    open_paren = src.index("(", m.end() - 1)
    body = src[open_paren + 1:match_delim(src, open_paren, "(", ")")]
    params, depth, cur = [], 0, ""
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            params.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        params.append(cur.strip())
    params = [p for p in params if p]
    return sum(1 for p in params
               if not re.match(r"\s*ctx\s*:\s*Context", p))


def call_sites(suite):
    """Every `<receiver>.methods.<call>(...)`, with the `.accounts({...})`
    object that follows it (or None). The suite formats these across several
    lines, so this walks delimiters instead of matching a line shape."""
    sites = []
    for m in re.finditer(r"(\w+)\.methods\s*\.\s*(\w+)\s*\(", suite):
        open_paren = suite.index("(", m.end() - 1)
        close_paren = match_delim(suite, open_paren, "(", ")")
        rest = suite[close_paren + 1:]
        acc = re.match(r"\s*\.\s*accounts\s*\(\s*\{", rest)
        obj = None
        if acc:
            brace = close_paren + 1 + acc.end() - 1
            obj = suite[brace:match_delim(suite, brace, "{", "}") + 1]
        sites.append((m.group(1), m.group(2), obj))
    return sites


SOURCES = {n: code(read(HERE / n / "src" / "lib.rs")) for n in CRATES}
SUITE_TEXT = read(SUITE)

# â”€â”€ 1. the harness is pinned, not floating â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_pkg = json.loads(read(PKG))
_tomllib = __import__("tomllib")
_manifest = _tomllib.loads(read(MANIFEST))
_anchor_pin = str((_manifest.get("toolchain") or {}).get("anchor_version", "")).strip()
_client_pin = (_pkg.get("dependencies") or {}).get("@anchor-lang/core")
check("1.1 package.json pins @anchor-lang/core to the CLI's own version, "
      "EXACTLY â€” that package was renamed from @coral-xyz/anchor in Anchor "
      "1.0.0, so this is also the check that the rename landed everywhere a "
      "client is built",
      _client_pin == _anchor_pin and not str(_client_pin).startswith(("^", "~")),
      {"package.json": _client_pin, "Anchor.toml": _anchor_pin})
_loose = {k: v for k, v in {**(_pkg.get("dependencies") or {}),
                            **(_pkg.get("devDependencies") or {})}.items()
          if str(v).startswith(("^", "~")) or v == "*"}
check("1.2 every npm dependency is pinned EXACTLY (no caret, tilde or star) â€” "
      "the rule the crates' `=` pins follow, because a range resolves "
      "differently on the day someone else clones this",
      not _loose, _loose)
_scripts = _pkg.get("scripts") or {}
_template_cmd = 'ts-mocha -p ./tsconfig.json -t 1000000 "tests/**/*.ts"'
check("1.3 the test script is the command Anchor 1.1.2's own mocha template "
      "generates (ts-mocha, the same project tsconfig, timeout and glob), and "
      "a typecheck script exists so the suite can be compiled without a chain",
      _scripts.get("test") == _template_cmd and "typecheck" in _scripts,
      _scripts)
_copt = (json.loads(read(TSCONFIG)).get("compilerOptions")) or {}
_types = _copt.get("types") or []
check("1.4 tsconfig carries the template's types plus node (the suite reads "
      ".so sizes and the ANCHOR_* env vars, which need the node types) and "
      "keeps strict on, so `npm run typecheck` is a real gate rather than a "
      "build step",
      set(_types) == {"mocha", "chai", "node"} and bool(_copt.get("strict")),
      {"types": _types, "strict": _copt.get("strict")})
_manifest_scripts = _manifest.get("scripts") or {}
_e2e_script = str(_manifest_scripts.get("e2e-devnet", ""))
check("1.5 Anchor.toml's e2e-devnet script DEPLOYS with the cluster named on "
      "the command and then runs the suite (the manifest's own rule: nothing "
      "that acts may inherit a stray [provider] cluster), and no bare `test` "
      "script exists for `anchor test` to run against whatever the provider "
      "happens to point at",
      "anchor deploy --provider.cluster devnet" in _e2e_script
      and "ts-mocha" in _e2e_script
      and "test" not in _manifest_scripts,
      _manifest_scripts)


# â”€â”€ 2/3. one predicate for what the suite claims about the programs â”€â”€â”€â”€â”€
def _suite_problems(suite, sources, genesis=None):
    """Returns {problem: detail}. Called with the real suite AND with the
    mutations in section 4, so a passing check and a discriminating check are
    the same code."""
    genesis = genesis or GENESIS
    problems = {}

    # The receiver -> crate map is PARSED out of the suite, not hardcoded:
    #   const treasury = anchor.workspace.XmrjTreasury as unknown as ...
    receiver_crate = {var: camel_to_snake(ws) for var, ws in re.findall(
        r"const\s+(\w+)\s*=\s*anchor\.workspace\.(\w+)\s+as", suite)}
    if set(receiver_crate.values()) != set(PROGRAMS):
        problems["workspace_receivers"] = receiver_crate

    sites = call_sites(suite)

    # 2.1 every call names an instruction that exists as `pub fn <snake>` in
    # the crate the receiver was taken from.
    bad_calls = {}
    for var, call, _ in sites:
        crate = receiver_crate.get(var)
        if crate is None:
            bad_calls[f"{var}.{call}"] = "unknown receiver"
        elif f"pub fn {camel_to_snake(call)}(" not in sources.get(crate, ""):
            bad_calls[f"{var}.{call}"] = f"no `pub fn {camel_to_snake(call)}` in {crate}"
    if not sites:
        problems["method_calls"] = "the suite calls no instruction at all"
    elif bad_calls:
        problems["method_calls"] = bad_calls

    # 2.2 every account key of every `.accounts({...})` is a field of the
    # Accounts struct that instruction is declared with. This is the rename
    # catcher: a field renamed in Rust and not in the suite fails HERE rather
    # than as a mystery at deploy time.
    bad_accounts = {}
    for var, call, obj in sites:
        crate = receiver_crate.get(var)
        if crate is None or obj is None:
            continue
        fn = re.search(r"pub fn " + re.escape(camel_to_snake(call))
                       + r"\(\s*ctx: Context<(\w+)>", sources.get(crate, ""))
        if not fn:
            bad_accounts[f"{crate}:{call}"] = "instruction has no Context<Struct>"
            continue
        struct = re.search(r"pub struct " + re.escape(fn.group(1))
                           + r"<'info>\s*\{", sources[crate])
        if not struct:
            bad_accounts[f"{crate}:{call}"] = f"no Accounts struct {fn.group(1)}"
            continue
        # struct.end() - 1 IS the opening brace of the struct body.
        body = sources[crate][struct.end() - 1:
                              match_delim(sources[crate],
                                          struct.end() - 1, "{", "}") + 1]
        fields = set(re.findall(r"pub\s+(\w+)\s*:", body))
        keys = object_keys(obj)
        unknown = {k for k in keys if camel_to_snake(k) not in fields}
        if unknown:
            bad_accounts[f"{crate}:{call}"] = {"unknown": sorted(unknown),
                                               "fields": sorted(fields)}
    if bad_accounts:
        problems["accounts"] = bad_accounts
    # A site whose object parsed to NO keys would pass the loop above by
    # vacuity â€” the failure mode this section exists to prevent.
    _empty = [f"{v}.{c}" for v, c, obj in sites if obj is not None and not object_keys(obj)]
    if _empty:
        problems["accounts_unparsed"] = _empty

    # 2.3 every PDA seed literal the suite derives with is a seed a crate
    # actually declares.
    suite_seeds = set(re.findall(r'Buffer\.from\("([^"]+)"\)', suite))
    crate_seeds = set(re.findall(r'_SEED:\s*&\s*\[u8\]\s*=\s*b"([^"]+)"',
                                 "\n".join(sources.values())))
    stray = suite_seeds - crate_seeds
    if stray:
        problems["seed_literals"] = {"stray": sorted(stray),
                                     "declared": sorted(crate_seeds)}

    # 2.4 the proposal's action variant and its field exist in the Rust enum.
    prop = re.search(r"proposeAction\(\s*\{\s*(\w+)\s*:\s*\{\s*(\w+)\s*:", suite)
    if not prop:
        problems["proposal_shape"] = "no proposeAction({variant: {field: ...}})"
    else:
        enum_body = re.search(r"pub enum GovernanceAction\s*\{(.*?)\n\}",                              sources["xmrj_governance"],
 re.S)
        enum_text = enum_body.group(1) if enum_body else ""
        variant = upper_camel(prop.group(1))
        if variant not in enum_text:
            problems["proposal_variant"] = {"variant": variant}
        elif camel_to_snake(prop.group(2)) not in enum_text:
            problems["proposal_field"] = {"field": prop.group(2)}

    # 3.1 the values make the assertion NON-VACUOUS: the suite's "before" is
    # what `initialize` writes in Rust, and its proposal moves the cap to a
    # different value.
    # Underscores are part of the literal: `10_000` is ten thousand, and a
    # bare `\d+` would read it as ten.
    init_write = re.search(r"state\.max_payout_bps\s*=\s*(\d[\d_]*)",
                           sources["xmrj_jackpot"])
    initial = re.search(r"INITIAL_MAX_PAYOUT_BPS\s*=\s*([\d_]+)", suite)
    proposed = re.search(r"PROPOSED_MAX_PAYOUT_BPS\s*=\s*([\d_]+)", suite)
    if not (init_write and initial and proposed):
        problems["values"] = "missing one of: initialize write, INITIAL, PROPOSED"
    else:
        i_num = int(initial.group(1).replace("_", ""))
        p_num = int(proposed.group(1).replace("_", ""))
        rust_initial = int(init_write.group(1).replace("_", ""))
        if i_num != rust_initial:
            problems["values_initial"] = {"suite": i_num,
                                          "rust_initialize": rust_initial}
        if p_num == i_num:
            problems["values_vacuous"] = {"proposed": p_num, "initial": i_num}

    # 3.2 the split the suite initializes with must satisfy the treasury's own
    # require (reserve + jackpot == 10 000), or the run fails at
    # initialization for a reason that was checkable offline.
    rr = re.search(r"INIT_RESERVE_BPS\s*=\s*([\d_]+)", suite)
    jj = re.search(r"INIT_JACKPOT_BPS\s*=\s*([\d_]+)", suite)
    if rr and jj and (int(rr.group(1).replace("_", ""))
                      + int(jj.group(1).replace("_", "")) != 10_000):
        problems["split_ratio"] = {rr.group(1): jj.group(1)}

    # 3.3 THE NEGATIVE CONTROL: a direct call signed by the deployer must be
    # attempted AND asserted rejected, and the value re-read afterwards â€” that
    # second read is what proves the rejection changed nothing on chain.
    direct = "adminAuthority: payer.publicKey" in suite
    asserted = bool(re.search(r"expect\(\s*rejected", suite))
    reread = len(re.findall(r"jackpotState\.fetch\(", suite)) >= 2
    if not (direct and asserted and reread):
        problems["negative_control"] = {"direct_call": direct,
                                        "asserted_rejected": asserted,
                                        "reread_after": reread}

    # 3.6 THE FUNDING PATH (the guarantee that replaced the key comparison):
    # the treasury -> jackpot CPI is exercised, BOTH direct fundJackpot calls
    # are attempted and asserted rejected â€” the funder PDA UNSIGNED (which
    # only a signature check rejects) and the treasury program's own address
    # (the recipe the old check accepted) â€” and the amount forwarded never
    # exceeds the convertible share the suite's own recorded inflow produces,
    # or the run would fail on the treasury's own require.
    funding = {}
    if "recordInflowXmrj" not in suite:
        funding["inflow_recorded"] = \
            "no record_inflow_xmrj to bank the convertible share"
    if "fundJackpotWithXmr" not in suite:
        funding["cpi_not_exercised"] = \
            "the treasury -> jackpot funding CPI is never called"
    if "funder: jackpotFunder" not in suite:
        funding["unsigned_pda_control"] = (
            "no direct fundJackpot with the funder PDA UNSIGNED: nothing here "
            "proves a SIGNATURE is what gates funding")
    if "funder: treasury.programId" not in suite:
        funding["old_recipe_control"] = (
            "no direct fundJackpot with the treasury program's own ADDRESS â€” "
            "the call the old key comparison accepted")
    # `refused`, deliberately NOT the `rejected` the setPayoutRules control
    # uses: two assertions sharing one variable name would make the older
    # mutation fixture (which drops that name) stop discriminating.
    if not re.search(r"expect\(\s*refused", suite):
        funding["not_asserted"] = "the direct funding calls are not asserted refused"
    _inflow = re.search(r"FUNDING_INFLOW_XMRJ\s*=\s*([\d_]+)", suite)
    _amount = re.search(r"FUNDING_AMOUNT_XMR\s*=\s*([\d_]+)", suite)
    if not (_inflow and _amount):
        funding["amounts"] = "missing FUNDING_INFLOW_XMRJ / FUNDING_AMOUNT_XMR"
    elif jj:
        _share = (int(_inflow.group(1).replace("_", ""))
                  * int(jj.group(1).replace("_", ""))) // 10_000
        if int(_amount.group(1).replace("_", "")) > _share:
            funding["amount_exceeds_share"] = {
                "forwarded": _amount.group(1), "convertible_share": _share}
    if funding:
        problems["funding_path"] = funding

    # 3.4 THE GENESIS GUARD: the hash the suite guards on is devnet's and is
    # neither of the other two clusters' â€” the suite DEPLOYS, so this is the
    # one value that must never be a copy-paste of the wrong chain.
    guard = re.search(r'DEVNET_GENESIS_HASH\s*=\s*"([^"]+)"', suite)
    if not guard:
        problems["genesis_guard"] = "no DEVNET_GENESIS_HASH constant"
    elif guard.group(1) != genesis["devnet"]:
        problems["genesis_guard"] = {"guarded": guard.group(1),                                     "devnet": genesis["devnet"]}
    if guard and guard.group(1) in (genesis["mainnet-beta"], genesis["testnet"]):





        problems["genesis_is_another_cluster"] = guard.group(1)
    if ("getGenesisHash()" not in suite or "throw new Error" not in suite
            or "!== DEVNET_GENESIS_HASH" not in suite):
        problems["genesis_guard_weak"] = "the hash is read but not enforced"

    # 3.5 RERUN SAFETY: devnet tests get run twice, the state accounts are
    # PDAs, and the program allows exactly one proposal â€” a second run must
    # recognize all of that instead of colliding with it.
    if "fetchNullable(" not in suite:
        problems["rerun_states"] = "no fetchNullable before initialize"
    if "proposalState === null" not in suite:
        problems["rerun_proposal"] = "no guard for the already-created proposal"

    # 3.7 THE INITIALIZE GATE, from the SUITE's side. Every program now gates
    # `initialize` on its own upgrade authority, which means every initializer
    # call must hand the program's ProgramData account to the instruction — an
    # account the client derives as ["programdata", programId] under the
    # upgradeable loader. Omit it and the very first call fails on chain;
    # derive it from the wrong seed or loader and it fails with "account does
    # not exist". Both literals are pinned against the crates here, because
    # the suite and the programs must describe the SAME address.
    gate_calls = [obj for _var, call, obj in sites
                  if call in ("initialize", "initializeGovernance")
                  and obj is not None]
    if not gate_calls:
        problems["init_calls"] = "no initializer call sites parsed"
    else:
        missing_pd = [i for i, obj in enumerate(gate_calls)
                      if "programData" not in object_keys(obj)]
        if missing_pd:
            problems["program_data_argument"] = (
                "initializer call(s) without programData: indexes "
                + repr(missing_pd))

    crate_pd_seed = re.search(r'PROGRAM_DATA_SEED:\s*&\[u8\]\s*=\s*b"([^"]+)"',
                              "\n".join(sources.values()))
    suite_pd_seed = re.search(r'PROGRAM_DATA_SEED\s*=\s*"([^"]+)"', suite)
    if not (crate_pd_seed and suite_pd_seed):
        problems["program_data_seed"] = {"suite": bool(suite_pd_seed),
                                         "crate": bool(crate_pd_seed)}
    elif crate_pd_seed.group(1) != suite_pd_seed.group(1):
        problems["program_data_seed"] = {"suite": suite_pd_seed.group(1),
                                         "crate": crate_pd_seed.group(1)}

    crate_loader = re.search(
        r'UPGRADEABLE_LOADER_ID:\s*Pubkey\s*=\s*[^;]*?"([^"]+)"',
        "\n".join(sources.values()))
    suite_loader = re.search(r'UPGRADEABLE_LOADER_ID\s*=\s*"([^"]+)"', suite)
    if not (crate_loader and suite_loader):
        problems["program_data_loader"] = {"suite": bool(suite_loader),
                                           "crate": bool(crate_loader)}
    elif crate_loader.group(1) != suite_loader.group(1):
        problems["program_data_loader"] = {"suite": suite_loader.group(1),
                                           "crate": crate_loader.group(1)}

    # 2.5 ARITY: every call must pass as many arguments as the Rust `pub fn`
    # takes after its Context. A new instruction PARAMETER (the treasury's
    # operational authority is one) is invisible to every account-name check:
    # the call still compiles here and still names real accounts, so nothing
    # offline would notice until the run failed on chain with "Invalid
    # arguments".
    arity = {}
    for var, call, _obj in sites:
        crate = receiver_crate.get(var)
        if crate is None:
            continue
        fn = camel_to_snake(call)
        args = call_args(suite, var, call)
        want = rust_param_count(sources.get(crate, ""), fn)
        if args is None or want is None:
            continue
        if len(args) != want:
            arity[f"{crate}:{call}"] = {"suite_args": len(args), "rust_params": want}
    if arity:
        problems["init_arity"] = arity

    # 3.8 THE OPERATIONAL GATE, from the suite's side: the two money-moving
    # treasury calls must pass the OPERATIONAL authority as the account AND
    # sign with it (the program checks both), the state assertion must read
    # the field back, and the negative control — a recording signed by a
    # NON-operational wallet, rejected and re-read unchanged — must be there.
    op = {}
    for var, call, obj in sites:
        if call in ("recordInflowXmrj", "fundJackpotWithXmr"):
            if obj is None or "operationalAuthority" not in object_keys(obj):
                op["account:" + call] = (
                    "the call does not pass the treasury's operational authority")
    # Per call, bounded to that call's own statement: a global "the string is
    # somewhere" test would stay green while one of the two calls dropped its
    # signature.
    for call in ("recordInflowXmrj", "fundJackpotWithXmr"):
        seg = re.search(rf"\.{re.escape(call)}\s*\((.*?)\.rpc\(\)", suite, re.S)
        if not seg or "signers([operationalAuthority])" not in seg.group(1):
            op["unsigned:" + call] = (
                "this call does not sign with the operational authority, so "
                "`has_one` alone would let a relayer move the treasury")
    if "operationalAuthority.publicKey" not in suite:
        op["key"] = "the suite never uses the operational key's address"
    if "treasuryBefore.operationalAuthority.toBase58()" not in suite:
        op["state_field"] = (
            "the suite does not assert the state recorded this operational key")
    if not re.search(r"recordInflowXmrj\(new anchor\.BN\(1\)\)", suite):
        op["no_negative_attempt"] = (
            "nothing tries a recording from a NON-operational wallet, so the "
            "gate is never seen rejecting anyone")
    if not re.search(r"expect\(\s*blocked", suite):
        op["negative_not_asserted"] = (
            "the non-operational attempt is not asserted blocked")
    if "operational gate is open" not in suite:
        op["negative_message"] = (
            "the negative control does not say what a success would mean")
    if op:
        problems["operational_gate"] = op

    return problems


_problems = _suite_problems(SUITE_TEXT, SOURCES)
_call_problems = {k: v for k, v in _problems.items()
                  if k in ("workspace_receivers", "method_calls")}
check("2.1 every `<receiver>.methods.<call>()` names an instruction that "
      "exists in that program as `pub fn <snake_case_call>` (the suite drives "
      "the programs through a narrow local interface, so this is the check a "
      "generated IDL type would otherwise provide)",
      not _call_problems, _call_problems)
check("2.2 every account key of every `.accounts({...})` block is a field of "
      "the Accounts struct that instruction is declared with â€” a field "
      "renamed in Rust and not in the suite fails here, not at deploy time "
      "(shorthand keys included, and an object that parses to no keys is a "
      "failure rather than a vacuous pass)",
      "accounts" not in _problems and "accounts_unparsed" not in _problems,
      {"wrong": _problems.get("accounts"),
       "unparsed": _problems.get("accounts_unparsed")})
check("2.3 every PDA seed literal the suite derives with is a seed one of the "
      "crates actually declares (a typo here derives a DIFFERENT address, and "
      "the instruction would simply fail on chain)",
      "seed_literals" not in _problems, _problems.get("seed_literals"))
_enum_problems = {k: v for k, v in _problems.items()
                  if k in ("proposal_shape", "proposal_variant", "proposal_field")}
check("2.4 the proposed GovernanceAction variant and its field exist in the "
      "Rust enum as declared â€” that variant index and those field names ARE "
      "the borsh encoding, so a rename is a different instruction",
      not _enum_problems, _enum_problems)
_value_problems = {k: v for k, v in _problems.items()
                   if k in ("values", "values_initial", "values_vacuous",
                            "split_ratio")}
check("3.1 the suite's before/after values cannot pass vacuously: the cap it "
      "expects BEFORE is the value `initialize` writes in Rust, and the value "
      "it proposes is a different one â€” a CPI that did nothing could not "
      "satisfy that assertion",
      not _value_problems, _value_problems)
check("3.2 the split the suite initializes the treasury with satisfies the "
      "program's own require (reserve_bps + jackpot_bps == 10_000), so the "
      "first run cannot fail at initialization",
      "split_ratio" not in _problems, _problems.get("split_ratio"))
check("3.3 THE NEGATIVE CONTROL is present and complete: a direct "
      "setPayoutRules signed by the deployer is attempted, asserted REJECTED, "
      "and the parameter is re-read afterwards â€” without that, the CPI proof "
      "would be indistinguishable from a writable account",
      "negative_control" not in _problems, _problems.get("negative_control"))
_guard_problems = {k: v for k, v in _problems.items()
                   if k.startswith(("genesis", "genesis_guard"))}
check("3.4 the suite guards on DEVNET's genesis hash — the value the devnet "
      "RPC itself reports — not mainnet-beta's or testnet's, and it reads the "
      "hash live and THROWS on a mismatch (a URL can carry a stale label; a "
      "genesis hash cannot)",
      not _guard_problems, _guard_problems)

_rerun_problems = {k: v for k, v in _problems.items() if k.startswith("rerun")}
check("3.5 rerun safety: the suite checks whether the state accounts and the "
      "(single, ever) proposal already exist before creating them — a devnet "
      "test that fails on its second run teaches nothing",
      not _rerun_problems, _rerun_problems)
check("3.6 THE FUNDING PATH is exercised with its negative controls in "
      "place: an inflow is recorded, the treasury -> jackpot CPI funds the "
      "jackpot, a DIRECT fundJackpot is attempted both with the funder PDA "
      "UNSIGNED and with the treasury program's own address (the recipe the "
      "old key comparison accepted) and both are asserted REFUSED — and the "
      "amount forwarded never exceeds the convertible share the suite's own "
      "recorded inflow produces, which the treasury's require would otherwise "
      "refuse on chain",
      "funding_path" not in _problems, _problems.get("funding_path"))

_ARITY_KEYS = ("init_arity",)
_arity_problems = {k: v for k, v in _problems.items() if k in _ARITY_KEYS}
check("2.5 every program call passes exactly as many arguments as the Rust "
      "`pub fn` takes AFTER its Context — a new instruction parameter (the "
      "treasury's operational authority is one) is invisible to every "
      "account-name check, so nothing else offline would catch a call that "
      "still names real accounts and would fail on chain with 'Invalid "
      "arguments'",
      not _arity_problems, _arity_problems)

_GATE_KEYS = ("init_calls", "program_data_argument", "program_data_seed",
              "program_data_loader")
_op_suite = _problems.get("operational_gate")
check("3.8 THE OPERATIONAL GATE is satisfied from the suite's side: the two "
      "money-moving treasury calls pass the state's operational authority AND "
      "sign with it (the program checks both — an address without a signature "
      "would let a relayer move the treasury), the state's field is read back, "
      "and the NEGATIVE control is present: a recording from a "
      "NON-operational wallet is attempted, asserted blocked, and re-read "
      "unchanged",
      not _op_suite, _op_suite)

_gate_suite = {k: v for k, v in _problems.items() if k in _GATE_KEYS}
check("3.7 THE INITIALIZE GATE is satisfied from the suite's side: every "
      "initializer call passes its program's `programData`, and the seed and "
      "loader literals the suite derives that address with are the SAME "
      "values the crates use — a suite that omitted the account would fail "
      "the first on-chain call, and one that derived it from another loader "
      "would look for an account that does not exist",
      not _gate_suite, _gate_suite)

# ── 4. RED-FIRST: mutations through the SAME predicate ──────────────────
# A predicate that passes is only worth what it rejects. Each mutation below
# is a way this file could rot, and each must be caught by the SAME
# `_suite_problems` the real suite just passed through.
_mutations = [
    (
        "an account key renamed in the suite only",
        SUITE_TEXT.replace(
            "\n          treasuryState,\n          adminAuthority: governanceAuthority,",
            "\n          treasuryStateRenamed,\n          adminAuthority: governanceAuthority,",
            1,
        ),
        "accounts",
    ),
    (
        "the ProgramData account dropped from an initializer call",
        SUITE_TEXT.replace("\n          programData: treasuryProgramData,\n", "", 1),
        "program_data_argument",
    ),
    (
        "the suite deriving ProgramData under a different loader",
        SUITE_TEXT.replace(
            'const UPGRADEABLE_LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111111";',
            'const UPGRADEABLE_LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111112";',
            1,
        ),
        "program_data_loader",
    ),
    (
        "a seed literal typo'd in the suite",
        SUITE_TEXT.replace("Buffer.from(JACKPOT_STATE_SEED)",
                           'Buffer.from("jackpot-state")', 1),
        "seed_literals",
    ),
    (
        "the operational signer dropped from the recording call",
        SUITE_TEXT.replace(
            "\n      .signers([operationalAuthority])\n      .rpc();",
            "\n      .rpc();", 1),
        "operational_gate",
    ),
    (
        "the non-operational recording control removed",
        SUITE_TEXT.replace("recordInflowXmrj(new anchor.BN(1))",
                           "recordInflowXmrj(new anchor.BN(2))", 1),
        "operational_gate",
    ),
    (
        "the non-operational rejection weakened",
        SUITE_TEXT.replace("expect(\n      blocked,", "expect(\n      true,", 1),
        "operational_gate",
    ),
    (
        "the operational authority argument dropped from initialize",
        SUITE_TEXT.replace("\n          operationalAuthority.publicKey,\n        )",
                           "\n        )", 1),
        "init_arity",
    ),
    (
        "the proposal asking for the value already on chain",
        SUITE_TEXT.replace("const PROPOSED_MAX_PAYOUT_BPS = 4_000;",
                           "const PROPOSED_MAX_PAYOUT_BPS = 10_000;", 1),
        "values_vacuous",
    ),
    (
        "the negative control's assertion dropped",
        SUITE_TEXT.replace("\n      rejected,\n", "\n      false,\n", 1),
        "negative_control",
    ),
]
_missed = []
for why, mutant, want in _mutations:
    if mutant == SUITE_TEXT:
        _missed.append((why, "mutation did not apply — fixture is stale"))
        continue
    got = _suite_problems(mutant, SOURCES)
    if want not in got:
        _missed.append((why, {"wanted": want, "got": sorted(got)}))
check("4.1 RED-FIRST: ten mutations — a renamed account key, a dropped "
      "`programData`, a ProgramData address derived under another loader, a "
      "seed typo, a vacuous proposal value, a dropped negative-control "
      "assertion, a dropped operational signature, a removed non-operational "
      "recording control, a weakened non-operational rejection and a missing "
      "initialize argument — each trip the SAME predicate the real suite "
      "passes, so \"it passes\" and \"it discriminates\" are one code path",
      not _missed, _missed)

# ── 5. THE LIVE HALF: opt-in, or an honest SKIP ─────────────────────────
def live_skip_reason(env=None, which=None, root=None):
    """None if the devnet suite can run here, else the reason it cannot.

    Pure on purpose: it reads an env mapping and a `which` function, and
    touches nothing — no network, no SOL, no chain. The live half DEPLOYS
    three programs and initializes shared state, so it must never start
    because a test run happened to be warm."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    root = HERE if root is None else root
    if env.get("XMRJ_DEVNET_E2E") != "1":
        return ("XMRJ_DEVNET_E2E is not 1 — the live half deploys to devnet "
                "and spends SOL, so it is opt-in rather than a surprise")
    missing = [tool for tool in ("anchor", "solana") if which(tool) is None]
    if missing:
        return "not on PATH: " + ", ".join(missing)
    if not (root / "node_modules").is_dir():
        return "node_modules is absent (run `npm ci` first)"
    wallet = env.get("ANCHOR_WALLET")
    if not wallet and not (Path.home() / ".config" / "solana" / "id.json").is_file():
        return "no ANCHOR_WALLET in the environment and no ~/.config/solana/id.json"
    if not env.get("ANCHOR_PROVIDER_URL"):
        return "no ANCHOR_PROVIDER_URL (set it to https://api.devnet.solana.com)"
    return None


check("5.1 the live half's gate names the missing piece instead of running "
      "blind: off unless XMRJ_DEVNET_E2E=1, and each precondition it checks "
      "(toolchain, client deps, wallet, provider) is one the run needs",
      live_skip_reason({}, which=lambda _t: "/usr/bin/" + _t, root=HERE).startswith("XMRJ_DEVNET_E2E")
      and live_skip_reason({"XMRJ_DEVNET_E2E": "1"}, which=lambda _t: None,
                           root=HERE).startswith("not on PATH"),
      {"off_by_default": live_skip_reason({}, which=lambda _t: _t, root=HERE),
       "no_toolchain": live_skip_reason({"XMRJ_DEVNET_E2E": "1"},
                                        which=lambda _t: None, root=HERE)})

_skip = live_skip_reason()
if _skip:
    print("SKIP: " + _skip)
else:
    _cmd = ["npx", "ts-mocha", "-p", "./tsconfig.json", "-t", "1000000",
            "tests/**/*.ts"]
    _run = subprocess.run(_cmd, cwd=str(HERE), capture_output=True, text=True,
                          timeout=3600)
    _out = (_run.stdout or "") + (_run.stderr or "")
    check("5.2 the devnet suite ran to completion and every step passed "
          "(mocha's own report is the grade, not this file's opinion)",
          _run.returncode == 0 and "passing" in _out,
          {"exit": _run.returncode,
           "tail": _out.strip().splitlines()[-25:]})

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
