"""test_anchor_wiring.py — THE HARNESS DRIVE (Oct 16).

WHY THIS EXISTS: the Solana side of the workspace was structurally present
and functionally disconnected. Anchor.toml held a single empty stub
(`[programs.devnet]` with no provider, no IDs, no scripts), none of the
three program crates declared a `declare_id!`, and none imported the Anchor
prelude — so the harness named nothing, the crates could not compile, and
there was no single place that could tell you the two disagreed. This drive
is the tripwire for that whole class of rot: it reads the four artifacts
that MUST agree (Anchor.toml, each crate's declare_id! + prelude, the
target/deploy program keypairs, the workspace's #[program] crates) and
fails the moment any one of them drifts.

WHAT IT PROVES, section by section:
  1. THE BASE58 PRIMITIVE the IDs are read through — pinned against two
     independent authorities (the all-zero System Program address, and the
     canonical Bitcoin base58check vector) plus a 200-case round trip, so a
     corrupt encoder can never silently bless a wrong program address.
  2. THE MANIFEST: it parses as TOML; [provider] names a real cluster and a
     keypair file; [programs.devnet] and [programs.localnet] list exactly the
     workspace's program crates, with identical addresses on both clusters.
  3. THE CRATES: every #[program] crate declares an ID (the missing piece)
     and imports the prelude at crate root (without it the file cannot
     build); no non-program member (xmrj_conversion_service — the off-chain
     service) is listed as a program.
  4. THE KEYPAIRS: target/deploy/<program>-keypair.json exists, is the
     64-byte Solana shape, and its public half base58-encodes to EXACTLY the
     address the manifest and the crate declare. The ID is therefore a real
     deployable identity, not a typed-in string.
  5. HYGIENE: target/ is gitignored and no keypair file is tracked by git
     (a committed program keypair is a committed upgrade authority).
  6. THE GOVERNANCE -> PROGRAM WIRING (blocker #2's regression guard): the
     phantom cross-crate types are gone; every GovernanceAction variant has a
     match arm; every CPI call names an instruction that exists in the target
     crate; every `cpi::accounts::<Struct>` the caller builds has the SAME
     field names as the target's own Accounts struct; the Program<> markers
     are the names anchor-syn generates; and the PDA seed literal agrees with
     the seed the CPI signs with.
  7. THE CARGO WIRING that makes all of the above compile at all: crate-type
     cdylib+lib per program, the cpi/no-entrypoint feature block (the cpi
     module is FEATURE-GATED), governance's path deps enabling `features =
     ["cpi"]`, the dep paths resolving, and the workspace's resolver + the
     overflow-checks release profile.
  9. THE INITIALIZERS (the gap behind the gap): both programs expose
     `initialize`, the state PDA is pinned in every Accounts struct that
     touches it, the cross-program IDs are PINNED constants that match the
     manifest and the other crates' declare_id!, the authority seed literal
     is one value across all three crates, and the initializer hands the
     authority to that PDA and takes nothing a caller could get wrong.
  10. DELIMITER BALANCE across every program source — explicitly a HEURISTIC
     stand-in for a parse, because this environment has no Rust toolchain
     (cargo/rustc/anchor/solana are all absent, as
     XMRJ_SOLANA_DEPLOYMENT_PLAN.md records). It cannot prove a program
     builds and does not claim to; it only refuses to let gross editing
     damage pass silently on a box with no compiler.
  8. RED-FIRST: the pre-fix shapes are fed back through this drive's own
     predicates — a crate source with the declare_id!/prelude lines stripped
     and the literal stub manifest the file held before (`[programs.devnet]`
     and nothing else). Each must go RED, so the checks above are known to
     discriminate rather than merely to pass.
  13. THE TOOLCHAIN PINS: the five values a fresh clone has to agree on — the
     anchor-cli version, the Solana CLI version (an exact three-part release
     with NO comparator: anchor-cli 1.1.2 parses that key as a plain version
     and refuses to build at all when it carries a `=`), the
     host Rust channel, every crate's anchor-lang pin, and the deployment
     plan's own numbers — judged by ONE predicate that the red-first
     mutations run through too.

NOTE ON GIT: the Solana side is TRACKED as of commit b95362c (Oct 16). This
drive predates that and still takes its red-first fixtures from literals in
this file rather than from `git show`, so it needs no git and no HEAD.

READ-ONLY: nothing here builds, deploys, signs or writes. It needs no Rust
toolchain (absent in this environment), because every claim it makes is a
fact about files that agree with each other.

Run:  python test_anchor_wiring.py        (exit 0 = all pass)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "Anchor.toml"
PROGRAMS = ("xmrj_treasury", "xmrj_jackpot", "xmrj_governance")
NOT_A_PROGRAM = "xmrj_conversion_service"

results = []


skipped = 0


def check(name, cond, why=""):
    results.append((bool(cond), name, why))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  ({why})" if why and not cond else ""))


def skip(name, why=""):
    """A check that CANNOT RUN HERE, said out loud. Not a pass: it stays in the
    transcript and the count, so a run that skipped half its checks cannot be
    mistaken for a run that verified them (the reason is always printed)."""
    global skipped
    skipped += 1
    print("SKIP  " + name + (f"  ({why})" if why else ""))


def read(path):
    return Path(path).read_text(encoding="utf-8")


def code(text):
    """Rust source with its comments removed. The structural checks judge
    CODE: a comment that mentions a type, or quotes an old shape, can never
    break a build, so it must neither satisfy nor fail a wiring check."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


# ── 1. the base58 primitive the addresses are read through ───────────────
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def base58check(payload: bytes) -> str:
    import hashlib
    return b58encode(payload + hashlib.sha256(
        hashlib.sha256(payload).digest()).digest()[:4])


check("1.1 thirty-two zero bytes encode to the System Program address "
      "(the vector every Solana tool prints)",
      b58encode(b"\x00" * 32) == "11111111111111111111111111111111",
      b58encode(b"\x00" * 32))
check("1.2 the canonical base58check vector (Bitcoin wiki) decodes and "
      "re-encodes byte-for-byte — the alphabet and the big-int algorithm "
      "are pinned, not invented here",
      base58check(bytes.fromhex("00010966776006953D5567439E5E39F86A0D273BEE"))
      == "16UwLL9Risc3QfPqBUvKofHmBQ7wMtjvM",
      base58check(bytes.fromhex("00010966776006953D5567439E5E39F86A0D273BEE")))
_rt_bad = [i for i in range(200)
           if (lambda b: b58decode(b58encode(b)) != b)(
               __import__("hashlib").sha256(str(i).encode()).digest()
               + __import__("hashlib").sha256(b"pad" + str(i).encode())
               .digest())]
check("1.3 200 pseudo-random 32-byte keys round-trip encode->decode intact",
      not _rt_bad, _rt_bad[:5])

# ── 2. the manifest itself ───────────────────────────────────────────────
tomllib = __import__("tomllib")
try:
    cfg = tomllib.loads(read(MANIFEST))
    parsed = True
except Exception as exc:                                    # noqa: BLE001
    cfg, parsed = {}, False
    check("2.0 Anchor.toml parses as TOML", False, exc)
if parsed:
    check("2.0 Anchor.toml parses as TOML (it held a single bare stub "
          "section before)", True)
    prov = cfg.get("provider") or {}
    check("2.1 [provider] is present with a cluster and a keypair-file "
          "wallet (Anchor requires both)",
          str(prov.get("cluster", "")).strip() != ""
          and str(prov.get("wallet", "")).strip().endswith(".json"),
          prov)
    check("2.2 the provider targets a real cluster name",
          str(prov.get("cluster", "")) in ("devnet", "localnet", "testnet",
                                          "mainnet-beta", "mainnet"),
          prov.get("cluster"))
    tables = {c: (cfg.get("programs") or {}).get(c) or {}
              for c in ("devnet", "localnet")}
    check("2.3 both devnet and localnet tables exist and list exactly the "
          "three program crates",
          all(set(t) == set(PROGRAMS) for t in tables.values()),
          {k: sorted(v) for k, v in tables.items()})
    bad_len = {c: [n for n, a in t.items()
                   if len(b58decode(a)) != 32] for c, t in tables.items()}
    check("2.4 every listed address is a 32-byte base58 public key",
          not any(bad_len.values()), bad_len)
    check("2.5 devnet and localnet agree on every address (a drift would "
          "deploy two different programs per name)",
          all(tables["devnet"].get(n) == tables["localnet"].get(n)
              for n in PROGRAMS),
          {n: (tables["devnet"].get(n), tables["localnet"].get(n))
           for n in PROGRAMS})

# ── 3. the crates: #[program] membership, declare_id!, the prelude ───────
DECL_RE = re.compile(r'declare_id!\(\s*"([^"]+)"\s*\)')
PRELUDE_RE = re.compile(r"^use anchor_lang::prelude::\*;", re.M)


def declared_id(text):
    """The address a crate declares, or None when it declares none — the
    exact predicate checks 3.1/4.1 ride (and 7 feeds broken sources to)."""
    m = DECL_RE.search(text)
    return m.group(1) if m else None


def has_prelude(text):
    return PRELUDE_RE.search(text) is not None


src = {n: code(read(HERE / n / "src" / "lib.rs"))
       for n in PROGRAMS + (NOT_A_PROGRAM,)}
declared = {n: declared_id(src[n]) for n in PROGRAMS}
check("3.1 every program crate declares an ID (declare_id!) — the piece "
      "that was missing, so the harness had nothing to point at",
      all(declared[n] for n in PROGRAMS), declared)
check("3.2 every program crate imports the Anchor prelude at crate root "
      "(without it the file cannot compile: Context/Pubkey/Result/require! "
      "are all unresolved)",
      all(has_prelude(src[n]) for n in PROGRAMS),
      [n for n in PROGRAMS if not has_prelude(src[n])])
check("3.3 the crates that carry #[program] are exactly the three the "
      "manifest wires",
      all("#[program]" in src[n] for n in PROGRAMS),
      [n for n in PROGRAMS if "#[program]" not in src[n]])
check("3.4 the OFF-CHAIN conversion service is not wired as a program "
      "(no #[program], no declare_id!) and is absent from every programs "
      "table",
      "#[program]" not in src[NOT_A_PROGRAM]
      and not re.search(r"declare_id!", src[NOT_A_PROGRAM])
      and all(NOT_A_PROGRAM not in (t or {})
              for t in [(cfg.get("programs") or {}).get("devnet"),
                        (cfg.get("programs") or {}).get("localnet")]),
      "a non-program in [programs.*] makes anchor build hunt a .so that "
      "will never exist")

# ── 4. crate <-> manifest <-> keypair agreement ──────────────────────────
mismatch = {n: (declared[n], tables["devnet"].get(n)) for n in PROGRAMS
            if declared[n] != tables["devnet"].get(n)}
check("4.1 each crate's declare_id! is the SAME string the manifest lists "
      "for devnet (Anchor fails the build/deploy on a mismatch)",
      not mismatch, mismatch)
# THE ARTIFACT-SIDE HALF OF THIS DRIVE, and where it can run. The keypairs live
# in target/deploy/, which is gitignored by design (6.1) and exists only where a
# build has run: they hold the PRIVATE halves of the program addresses, so no
# clone can contain them and no CI job can regenerate the ids these crates
# declare. On a host that has built (the runbook's §3) both checks run as before —
# including the failure that matters, a keypair deleted next to the sources. On a
# checkout that has never been built they are reported SKIPPED with that reason,
# not PASSED: "nobody built here" is not a wiring defect, but it is also not
# evidence, and a skip that printed nothing would let this drive look green on a
# tree with no keypairs at all.
if not (HERE / "target" / "deploy").is_dir():
    skip("4.2 every program has target/deploy/<program>-keypair.json in the 64-byte "
         "Solana shape",
         "this checkout has no target/deploy — nothing has been built here")
    skip("4.3 each keypair's PUBLIC HALF base58-encodes to exactly the address the "
         "manifest and crate declare (the ID is a real identity, not a typed-in string)",
         "same: the keypairs are gitignored halves of the program addresses; the "
         "runbook's §3 runs this drive where a build exists")
else:
    key_bad, key_missing, pub_mismatch = [], [], {}
    for n in PROGRAMS:
        kp = HERE / "target" / "deploy" / f"{n}-keypair.json"
        if not kp.exists():
            key_missing.append(n)
            continue
        try:
            raw = json.loads(kp.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            key_bad.append(n)
            continue
        if (not isinstance(raw, list) or len(raw) != 64
                or not all(isinstance(b, int) and 0 <= b <= 255 for b in raw)):
            key_bad.append(n)
            continue
        pub = b58encode(bytes(raw[32:]))
        if pub != tables["devnet"].get(n):
            pub_mismatch[n] = pub
    check("4.2 every program has target/deploy/<program>-keypair.json in the "
          "64-byte Solana shape",
          not key_missing and not key_bad,
          {"missing": key_missing, "malformed": key_bad})
    check("4.3 each keypair's PUBLIC HALF base58-encodes to exactly the "
          "address the manifest and crate declare — the ID is a real identity, "
          "not a typed-in string",
          not pub_mismatch and not key_missing, pub_mismatch)

# ── 5. the deploy surface the harness owes the operator ──────────────────
scripts = cfg.get("scripts") or {}
if parsed:
    check("5.1 [scripts] carries a deploy that names its cluster explicitly "
          "(a stray provider cluster can never silently redirect it)",
          any("anchor deploy" in str(v) and "devnet" in str(v)
              for v in scripts.values()),
          scripts)
    check("5.2 [scripts] also names the build and the key listing, so the "
          "operator path is one command each",
          any(str(v).strip() == "anchor build" for v in scripts.values())
          and any("anchor keys list" in str(v) for v in scripts.values()),
          scripts)

# ── 6. hygiene: the keypairs cannot be committed ─────────────────────────
gi = read(HERE / ".gitignore")
check("6.1 .gitignore covers target/ (program keypairs live there: the "
      "private half of every program address)",
      re.search(r"^target/\s*$", gi, re.M) is not None,
      [ln for ln in gi.splitlines() if "target" in ln])
# A git failure is NOT softened into a pass: the check simply asserts that
# git ran and reported no keypair (the crate sources are untracked here, but
# git itself is present — a broken git must never look like a clean sweep).
try:
    tracked = subprocess.run(["git", "ls-files"], cwd=str(HERE),
                             capture_output=True, text=True, timeout=30)
    tracked_files = tracked.stdout.splitlines() if tracked.returncode == 0 else None
except Exception as _exc:                                   # noqa: BLE001
    tracked_files = None
    tracked_why = repr(_exc)
leaked = ([p for p in tracked_files if p.endswith("-keypair.json")
           or p.endswith("id.json")] if tracked_files is not None else None)
check("6.2 no program keypair (or wallet) file is tracked by git — a "
      "committed keypair is a committed upgrade authority",
      tracked_files is not None and leaked == [],
      leaked if leaked is not None else f"git ls-files unavailable: "
      + tracked_why)

# ── 7. red-first: the PRE-FIX SHAPE must fail this drive's own checks ────
# The broken sources are reconstructed here from the pre-fix bytes: every
# crate opened with `#[program]` on line 1 (no prelude, no declare_id!) and
# the manifest held the single line `[programs.devnet]`. If either fixture
# ever PASSES the predicates, the checks stopped discriminating.
_stripped = {n: "\n".join(ln for ln in src[n].splitlines()
                          if "declare_id!" not in ln
                          and "anchor_lang::prelude" not in ln)
             for n in PROGRAMS}
check("8.1 RED-FIRST on the crates: with the declare_id! and prelude lines "
      "removed the real sources fail 3.1 AND 3.2 — the two checks cannot be "
      "passed by a crate that has neither",
      all(declared_id(_stripped[n]) is None
          and not has_prelude(_stripped[n]) for n in PROGRAMS)
      and all(declared_id(src[n]) is not None
              and has_prelude(src[n]) for n in PROGRAMS),
      "a stripped source still satisfied the checks")
_stub_programs = tomllib.loads(
    "[programs.devnet]").get("programs") or {}
check("8.2 RED-FIRST on the manifest: the literal pre-fix content "
      "(`[programs.devnet]` alone) fails the provider and the program-table "
      "checks, while the real manifest passes them",
      "provider" not in tomllib.loads("[programs.devnet]")
      and set(_stub_programs.get("devnet") or {}) != set(PROGRAMS)
      and str((cfg.get("provider") or {}).get("cluster", "")) != ""
      and set(tables["devnet"]) == set(PROGRAMS),
      f"stub programs={_stub_programs}")

# ── 9. the governance -> program CPI wiring (blocker #2's guard) ────────
GOV = src["xmrj_governance"]
PHANTOM = ("crate::TreasuryState", "crate::JackpotState",
           "crate::ConversionServiceState")
check("9.1 the phantom cross-crate types are gone — governance no longer "
      "names account types it does not own (the shapes that could not "
      "compile), and the action that had no on-chain state behind it is "
      "removed from the enum",
      not any(p in GOV for p in PHANTOM)
      and "SetConversionServiceParams" not in GOV,
      [p for p in PHANTOM if p in GOV])


def _balanced(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return ""


def _block_after(text, marker):
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("{", i)
    return _balanced(text, j) if j >= 0 else None


def _opt_fields(body, prefix):
    """Top-level `pub name: ...` (prefix='pub ') or `name: ...` keys of a
    Rust struct body — the account names both sides must agree on."""
    out, depth = [], 0
    for line in body.splitlines():
        s = line.strip()
        d0, depth = depth, depth + s.count("{") - s.count("}")
        if d0:
            continue
        m = re.match(rf"{prefix}([A-Za-z_][A-Za-z0-9_]*)\s*:", s)
        if m:
            out.append(m.group(1))
    return out


_enum_body = _block_after(GOV, "pub enum GovernanceAction") or ""
_variants = re.findall(r"^\s{4}([A-Z][A-Za-z0-9_]*)\s*(?:\{|,)", _enum_body,
                       re.M)
_arms = re.findall(r"GovernanceAction::([A-Z][A-Za-z0-9_]*)", GOV)
check("9.2 every GovernanceAction variant has a handler arm in "
      "execute_action (a variant with no arm is a decision that silently "
      "does nothing)",
      bool(_variants) and set(_variants) <= set(_arms),
      f"variants={_variants} arms={sorted(set(_arms))}")

# 9.3-9.5 scan EVERY program crate, not just governance. The treasury calls
# the jackpot now (fund_jackpot_with_xmr), so a rule that watched one caller
# would let the other caller's wiring rot unwatched. Each call is resolved
# against the callee's own source, whichever crate makes it.
_calls = {n: re.findall(r"(xmrj_[a-z_]+)::cpi::([a-z_][a-z0-9_]*)\s*\(", src[n])
          for n in PROGRAMS}
_missing_fn = {f"{n} -> {c}::{f}": "no such `pub fn` in the target crate"
               for n, calls in _calls.items() for c, f in calls
               if not re.search(rf"pub fn {f}\s*(?:<[^>]*>)?\s*\(", src.get(c, ""))}
_calls_flat = [f"{n} -> {c}::{f}" for n, calls in _calls.items() for c, f in calls]
check("9.3 every CPI call in ANY program crate — not only in governance — "
      "names an instruction that EXISTS in the target crate (the callee's "
      "own `pub fn`)",
      bool(_calls_flat) and not _missing_fn,
      {"calls": _calls_flat, "missing": _missing_fn})

_accts = {n: re.findall(
    r"(xmrj_[a-z_]+)::cpi::accounts::([A-Za-z_][A-Za-z0-9_]*)\s*\{", src[n])
    for n in PROGRAMS}
_field_bad = {}
for _caller, _pairs in _accts.items():
    for c, st in _pairs:
        target = src.get(c, "")
        m = re.search(rf"pub struct {st}\s*(?:<'[^>]*>)?\s*\{{", target)
        if not m:
            _field_bad[f"{_caller} -> {c}::{st}"] = \
                "no such accounts struct in the target crate"
            continue
        want = _opt_fields(_balanced(target, m.end() - 1), "pub ")
        # EVERY literal in the calling crate, not just the first: a second
        # call site that dropped a field is exactly the drift this catches.
        for lit in re.finditer(rf"{c}::cpi::accounts::{st}\s*\{{", src[_caller]):
            got = _opt_fields(_balanced(src[_caller], lit.end() - 1), "")
            if set(want) != set(got):
                _field_bad[f"{_caller} -> {c}::{st}"] = {"target": want,
                                                          "caller": got}
check("9.4 every `cpi::accounts::<Struct>` ANY caller builds carries the "
      "SAME field names as the target's own Accounts struct (a rename on "
      "either side cannot drift past this)",
      any(_accts.values()) and not _field_bad, _field_bad)

_imports = {n: re.findall(r"use (xmrj_[a-z_]+)::program::([A-Za-z0-9_]+);", src[n])
            for n in PROGRAMS}
_used = {n: set(re.findall(r"Program<'info,\s*([A-Za-z0-9_]+)>", src[n]))
         for n in PROGRAMS}
# `System` is anchor-lang's own marker (system_program), not a workspace dep.
_ANCHOR_MARKERS = {"System"}
_marker_bad = {}
for _n in PROGRAMS:
    for _crate, _marker in _imports[_n]:
        if (_marker != "".join(p.capitalize() for p in _crate.split("_"))
                or not re.search(rf"#\[program\]\s*pub mod {_crate}\b",
                                 src.get(_crate, ""))):
            _marker_bad[f"{_n}->{_crate}"] = _marker
    _imported = {m for _, m in _imports[_n]}
    if not _imported <= _used[_n] <= _imported | _ANCHOR_MARKERS:
        _marker_bad[f"{_n}::usage"] = {"imports": sorted(_imported),
                                        "used": sorted(_used[_n])}
check("9.5 every Program<> marker, in every crate, is the name anchor-syn "
      "generates from the dependency's own `#[program]` module (CamelCase of "
      "the module name), and every imported marker is actually used",
      any(_imports.values()) and not _marker_bad, _marker_bad)


def _cpi_program_args(text):
    """The FIRST argument of every `CpiContext::new(...)` /
    `new_with_signer(...)` call — walked by delimiter, because the arguments
    span lines and contain nested calls."""
    args = []
    for m in re.finditer(r"CpiContext::new(?:_with_signer)?\(", text):
        i, depth, start = m.end(), 1, m.end()
        while i < len(text) and depth:
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 1:
                break
            i += 1
        args.append(text[start:i].strip())
    return args


# 9.7: Anchor 1.0 REMOVED the program AccountInfo from CpiContext (it was a
# redundant copy of an account already in the Accounts struct) and the
# generated CPI reads `ctx.program_id`. An AccountInfo in that position does
# not compile — and it is the shape the pre-migration code had, so it is
# exactly the half-applied edit to watch for.
_cpi_prog_bad = {n: args for n in PROGRAMS
                 if (args := [a for a in _cpi_program_args(src[n])
                              if "to_account_info()" in a
                              or not (a.endswith("::id()") or a.endswith("::ID"))])}
_cpi_args_all = [a for n in PROGRAMS for a in _cpi_program_args(src[n])]
check("9.7 every CPI context's program argument is the callee's Pubkey "
      "(`<Program>::id()`), not an AccountInfo: Anchor 1.0 removed the program "
      "AccountInfo from CpiContext, so `to_account_info()` there is a compile "
      "error — and the account itself is still required in the caller's "
      "struct, where Program<> validates it",
      bool(_cpi_args_all) and not _cpi_prog_bad, _cpi_prog_bad)

# RED-FIRST: the regression this check exists for, applied to the REAL call.
_cpi_mutations = [
    ("the treasury's CPI given the jackpot's AccountInfo (the pre-1.0 shape)",
     "xmrj_treasury",
     src["xmrj_treasury"].replace(
         "XmrjJackpot::id(),",
         "ctx.accounts.jackpot_program.to_account_info(),", 1)),
    ("governance's treasury CPI given an AccountInfo",
     "xmrj_governance",
     GOV.replace("XmrjTreasury::id(),",
                 "ctx.accounts.treasury_program.to_account_info(),", 1)),
]
_missed_cpi = []
for why, crate, mutant in _cpi_mutations:
    if mutant == src[crate]:
        _missed_cpi.append((why, "mutation did not apply — fixture is stale"))
    elif not [a for a in _cpi_program_args(mutant)
              if "to_account_info()" in a]:
        _missed_cpi.append((why, "the predicate accepted an AccountInfo"))
check("9.8 RED-FIRST on the CPI argument: handing CpiContext an AccountInfo "
      "instead of the program's Pubkey — in this crate's own new call and in "
      "governance's — trips the SAME predicate the real sources pass",
      not _missed_cpi, _missed_cpi)

_seed_const = re.search(r'GOVERNANCE_AUTHORITY_SEED:\s*&\[u8\]\s*=\s*b"([^"]+)"',
                        GOV)
# Each `seeds = [b"..."]` belongs to the field declared just after it.
_seed_pairs = []
for _m in re.finditer(r'seeds\s*=\s*\[b"([^"]+)"\]', GOV):
    _nxt = re.search(r"pub\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", GOV[_m.end():])
    if _nxt:
        _seed_pairs.append((_m.group(1), _nxt.group(1)))
_auth_seeds = {lit for lit, field in _seed_pairs
               if field == "governance_authority"}
check("9.6 the authority PDA's seed literal, in the account constraint that "
      "DERIVES the address, equals the const the CPI SIGNS with — the signer "
      "seeds and the constraint can never describe two different PDAs",
      _seed_const is not None and _auth_seeds
      and _auth_seeds == {_seed_const.group(1)}
      and "new_with_signer" in GOV,
      {"const": _seed_const.group(1) if _seed_const else None,
       "authority_attr_seeds": sorted(_auth_seeds),
       "all_pairs": _seed_pairs})

# ── 10. the cargo wiring that makes the CPI compile at all ───────────────
_manifests = {n: tomllib.loads(read(HERE / n / "Cargo.toml"))
              for n in PROGRAMS + (NOT_A_PROGRAM,)}
_libtype_bad = [n for n in PROGRAMS
                if (_manifests[n].get("lib") or {}).get("crate-type")
                != ["cdylib", "lib"]]
check("10.1 each program crate declares crate-type [\"cdylib\", \"lib\"] — "
      "the cdylib is the deployable .so, the rlib is what a calling program "
      "links against; without the cdylib there is no artifact to deploy",
      not _libtype_bad, _libtype_bad)
_feat_bad = [n for n in PROGRAMS
             if set((_manifests[n].get("features") or {}).keys())
             < {"cpi", "no-entrypoint", "no-idl", "no-log-ix-name", "idl-build"}
             or (_manifests[n]["features"] or {}).get("cpi") != ["no-entrypoint"]]
check("10.2 each program crate carries the feature block the CPI surface "
      "rides (the generated `cpi` module is feature-gated; `cpi` implies "
      "no-entrypoint so a caller links no second entrypoint)",
      not _feat_bad, _feat_bad)
def _cpi_dep_problems(sources, manifests):
    """{caller -> target: detail} for every crate that makes a `xmrj_*::cpi::`
    call without a matching PATH dependency carrying features = ["cpi"].
    Called with the real sources/manifests AND with the mutations in 10.5, so
    the check that passes and the check that discriminates are one code path.
    The path is resolved relative to the MANIFEST's directory, which is how
    cargo resolves it — not relative to the workspace root."""
    problems = {}
    for caller in PROGRAMS:
        targets = set(re.findall(r"(xmrj_[a-z_]+)::cpi::",
                                 sources.get(caller, "")))
        deps = manifests.get(caller, {}).get("dependencies") or {}
        for target in sorted(targets):
            dep = deps.get(target)
            if not isinstance(dep, dict):
                problems[f"{caller} -> {target}"] = "no dependency declared"
            elif "cpi" not in (dep.get("features") or []):
                problems[f"{caller} -> {target}"] = {
                    "features": dep.get("features")}
            elif not (HERE / caller / str(dep.get("path", "x"))).is_dir():
                problems[f"{caller} -> {target}"] = {
                    "path_does_not_resolve": dep.get("path")}
    return problems


def _manifest_mutation(crate, dep, value):
    """The manifests as they would parse if that crate's Cargo.toml had been
    half-edited: `value=None` removes the dependency, a dict without
    `features` leaves the callee's cpi surface ungenerated."""
    deps = dict(_manifests[crate].get("dependencies") or {})
    if value is None:
        deps.pop(dep, None)
    else:
        deps[dep] = value
    return {**_manifests, crate: {**_manifests[crate], "dependencies": deps}}


_dep_bad = _cpi_dep_problems(src, _manifests)
check("10.3 every crate that makes a `xmrj_*::cpi::` call depends on that "
      "program by PATH and enables features = [\"cpi\"] (without the feature "
      "those cpi paths do not exist; the path dep keeps ONE account layout "
      "between caller and callee) — checked for EVERY calling crate, because "
      "the treasury funds the jackpot as well as governance driving both",
      not _dep_bad, _dep_bad)
_root = tomllib.loads(read(HERE / "Cargo.toml"))
check("10.4 the workspace lists the three program crates as members, pins "
      "resolver 2, and keeps overflow-checks ON in release (checked "
      "arithmetic is a security property of the deployed program)",
      set(PROGRAMS) <= set((_root.get("workspace") or {}).get("members") or [])
      and (_root.get("workspace") or {}).get("resolver") == "2"
      and ((_root.get("profile") or {}).get("release") or {})
      .get("overflow-checks") is True,
      {"members": (_root.get("workspace") or {}).get("members"),
       "resolver": (_root.get("workspace") or {}).get("resolver")})

# RED-FIRST: the three ways this wiring actually rots — the dependency added
# without the feature (the cpi paths then do not exist), the call kept while
# the dependency is deleted, and a path typo. Each mutation goes through the
# predicate the real manifests just passed.
_dep_mutations = [
    ("the treasury's jackpot dependency without features = [\"cpi\"]",
     _manifest_mutation("xmrj_treasury", "xmrj_jackpot",
                        {"path": "../xmrj_jackpot"}),
     "xmrj_treasury -> xmrj_jackpot"),
    ("governance's jackpot dependency removed outright",
     _manifest_mutation("xmrj_governance", "xmrj_jackpot", None),
     "xmrj_governance -> xmrj_jackpot"),
    ("the treasury's dependency path pointing at nothing",
     _manifest_mutation("xmrj_treasury", "xmrj_jackpot",
                        {"path": "../xmrj_jackpot_typo",
                         "features": ["cpi"]}),
     "xmrj_treasury -> xmrj_jackpot"),
]
_missed_dep = []
for why, mutated, want in _dep_mutations:
    probs = _cpi_dep_problems(src, mutated)
    if want not in probs:
        _missed_dep.append((why, "predicate said: " + repr(sorted(probs))))
check("10.5 RED-FIRST on the cpi wiring: a dependency without the cpi "
      "feature, a dependency deleted while the call remains, and a path that "
      "does not resolve each trip the SAME predicate the real manifests pass "
      "through",
      not _missed_dep, _missed_dep)

# ── 11. delimiter balance: the only syntax-level guard available here ────
# NO Rust toolchain exists in this environment (cargo / rustc / anchor /
# solana are all absent — the deployment plan records the same gap), so
# nothing in this file can PARSE the programs. This is the honest stand-in:
# with strings and comments removed, every delimiter must close exactly
# once. It is a heuristic, NOT a compiler — it cannot prove a program
# builds, and it is labelled that way so nobody reads it as one.
def _delimiters(text):
    out = []
    for line in text.splitlines():
        in_str, cut, i = False, len(line), 0
        while i < len(line) - 1:
            ch = line[i]
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_str = not in_str
            if not in_str and line[i:i + 2] == "//":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    stripped = re.sub(r"/\*.*?\*/", "", "\n".join(out), flags=re.S)
    return {c: stripped.count(c) for c in "{}()[]"}


_unbalanced = {}
for _n in PROGRAMS + (NOT_A_PROGRAM,):
    _c = _delimiters(read(HERE / _n / "src" / "lib.rs"))
    if (_c["{"] != _c["}"] or _c["("] != _c[")"]
            or _c["["] != _c["]"]):
        _unbalanced[_n] = _c
check("11.1 every program source's delimiters close, with strings and "
      "comments excluded (a HEURISTIC stand-in for a parse this environment's "
      "absent Rust toolchain makes impossible — not a build)",
      not _unbalanced, _unbalanced)

# ── 12. the initializers, and the IDs they PIN ──────────────────────────
# Each program names the other's program id as a constant, because a PDA is
# only meaningful under the program that derives it: supplying the authority
# program at init would let a caller hand the authority to themselves. The
# duplication that pinning implies is exactly what this section watches.
TRE = src["xmrj_treasury"]
JAC = src["xmrj_jackpot"]
STATE_SEED = {"xmrj_treasury": "treasury_state",
              "xmrj_jackpot": "jackpot_state"}
STATE_TYPE = {"xmrj_treasury": "TreasuryState",
              "xmrj_jackpot": "JackpotState"}


_ACCOUNTS_BLOCKS = re.compile(
    r"#\[derive\(Accounts\)\]\s*pub struct ([A-Za-z0-9_]+)", re.S)


def _accounts_bodies(text):
    marks = list(_ACCOUNTS_BLOCKS.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        brace = text.find("{", m.end())
        out.append((m.group(1), _balanced(text, brace)))
    return out


# A pinned id is a const Pubkey built from a base58 LITERAL. Either spelling
# is accepted — Anchor 1.x dropped the `solana_program::pubkey!` path in favour
# of solana-pubkey's `from_str_const` — because this drive judges the VALUE,
# not the spelling. What it will not accept is a const whose literal it cannot
# find, because then nothing here pins the address at all.
_PIN_RE = re.compile(
    r"pub const ([A-Z][A-Z0-9_]*)_PROGRAM_ID:\s*Pubkey\s*=\s*[^;]*?"
    r'(?:pubkey!|from_str_const)\("([^"]+)"\)', re.S)


def _pins(text):
    """{pinned const -> pubkey literal} for every `<NAME>_PROGRAM_ID`."""
    return dict(_PIN_RE.findall(text))


_pin_bad = {}
for n in ("xmrj_treasury", "xmrj_jackpot"):
    for base, pid in _pins(src[n]).items():
        # `GOVERNANCE_PROGRAM_ID` pins the `xmrj_governance` crate.
        target = "xmrj_" + base.lower()
        if (target not in PROGRAMS or pid != tables["devnet"].get(target)
                or pid != declared.get(target)):
            _pin_bad[f"{n}:{base}"] = {"pinned": pid, "expected": target,
                                       "manifest": tables["devnet"].get(target),
                                       "declared": declared.get(target)}
check("12.1 every PINNED cross-program id equals the manifest address AND the "
      "other crate's declare_id! (the pin is what makes a caller-supplied "
      "authority impossible, so it must never drift)",
      not _pin_bad and bool(_pins(TRE)) and bool(_pins(JAC)), _pin_bad)

_flip_targets = [_PIN_RE.search(TRE)]
_flip = TRE[:_flip_targets[0].start(2)] + (
    "1" if _flip_targets[0].group(2)[0] != "1" else "2"
) + _flip_targets[0].group(2)[1:] + TRE[_flip_targets[0].end(2):]
_flipped = _pins(_flip)
check("12.2 RED-FIRST on the pins: a single flipped character in a pinned id "
      "is caught by 12.1's predicate (so the check discriminates, rather "
      "than passing on any string)",
      bool(_flipped)
      and list(_flipped.values())[0] != list(_pins(TRE).values())[0],
      _flipped)

_seed_consts = {n: (re.search(r'GOVERNANCE_AUTHORITY_SEED:\s*&\[u8\]\s*=\s*b"([^"]+)"',
                              src[n]) or [None, None])[1]
                for n in PROGRAMS}
_auth_constraints = []
for n in ("xmrj_treasury", "xmrj_jackpot"):
    for lit, prog in re.findall(
            r'seeds\s*=\s*\[b"([^"]+)"\]\s*,\s*seeds::program\s*=\s*([A-Z_]+)',
            src[n]):
        _auth_constraints.append((n, lit, prog))
check("12.3 the authority PDA each program hands its admin powers to is "
      "derived from the PINNED governance program under the SAME seed literal "
      "all three crates use (three crates, one value)",
      len(_auth_constraints) == 2
      and all(prog == "GOVERNANCE_PROGRAM_ID" and lit == _seed_consts["xmrj_governance"]
              and lit == _seed_consts[n] for n, lit, prog in _auth_constraints),
      {"seed_consts": _seed_consts, "constraints": _auth_constraints})

_jac_init = _block_after(JAC, "pub fn initialize")
_jac_init_fields = dict(_accounts_bodies(JAC))["Initialize"]
check("12.4 both initializers exist and hand the authority to the PDA; the "
      "jackpot takes NOTHING a caller could choose (its funder is the pinned "
      "treasury id and its cap starts uncapped)",
      "pub fn initialize(" in TRE and "pub fn initialize(" in JAC
      and "state.admin_authority = ctx.accounts.admin_authority.key()" in TRE
      and "state.admin_authority = ctx.accounts.admin_authority.key()" in JAC
      and "state.treasury_program = TREASURY_PROGRAM_ID" in JAC
      and "state.max_payout_bps = 10_000" in JAC
      and "treasury_program" not in _opt_fields(_jac_init_fields, "pub ")
      and "admin_authority" in _opt_fields(_jac_init_fields, "pub "),
      {"jackpot_init_fields": _opt_fields(_jac_init_fields, "pub "),
       "jackpot_init_body": (_jac_init or "")[:120]})

_unpinned = {}
for n in ("xmrj_treasury", "xmrj_jackpot"):
    want = STATE_SEED[n]
    for name, body in _accounts_bodies(src[n]):
        if f"Account<'info, {STATE_TYPE[n]}>" in body \
                and f'seeds = [b"{want}"]' not in body:
            _unpinned[f"{n}::{name}"] = "state account without its PDA seed"
check("12.5 every Accounts struct that touches a state account pins it to "
      "its PDA (the state is a PDA now — an unpinned reference would accept a "
      "lookalike account of the same type)",
      not _unpinned, _unpinned)

# ── 12.6/12.7 the FUNDING authorization ─────────────────────────────────
# fund_jackpot used to compare the caller account's KEY against the jackpot's
# recorded treasury id — a check any wallet satisfied by passing the treasury
# program's ADDRESS, with the treasury program nowhere in the call. What
# replaces it is a signature: a PDA only the treasury program can sign for.
# That property spans TWO crates agreeing on one seed literal, so it is ONE
# predicate here, fed the real sources and the mutations in 12.7.
FUNDER_SEED_CONST = "JACKPOT_FUNDER_SEED"


def _funder_problems(jackpot_src, treasury_src):
    """{problem: detail} unless funding is provably treasury-only: one seed
    literal in both crates, the jackpot demanding a SIGNER for the PDA derived
    under the PINNED treasury id, the treasury deriving that same address
    under its own id and signing for it in the CPI — and the amount bounded by
    what the treasury has RECORDED as convertible (so the call is a bridge,
    not a faucet)."""
    problems = {}
    seed_re = rf'{FUNDER_SEED_CONST}:\s*&\[u8\]\s*=\s*b"([^"]+)"'
    jac_seed = re.search(seed_re, jackpot_src)
    tre_seed = re.search(seed_re, treasury_src)
    if not (jac_seed and tre_seed):
        problems["seed_const"] = {"jackpot": bool(jac_seed),
                                  "treasury": bool(tre_seed)}
    elif jac_seed.group(1) != tre_seed.group(1):
        problems["seed_literals"] = {"jackpot": jac_seed.group(1),
                                     "treasury": tre_seed.group(1)}

    # The jackpot's half: the accounts struct that fund_jackpot actually takes
    # must derive the funder PDA under the pinned treasury id AND demand a
    # signature for it. Without `signer`, passing the PDA's address would be
    # enough — which is precisely the hole this replaced.
    fn = re.search(r"pub fn fund_jackpot\(\s*ctx:\s*Context<([A-Za-z0-9_]+)>",
                   jackpot_src)
    if not fn:
        problems["fund_jackpot"] = "no `pub fn fund_jackpot(ctx: Context<...>)`"
    else:
        body = dict(_accounts_bodies(jackpot_src)).get(fn.group(1))
        if body is None:
            problems["jackpot_accounts_struct"] = fn.group(1)
        elif FUNDER_SEED_CONST not in body:
            problems["jackpot_funder_account"] = (
                fn.group(1) + " does not mention " + FUNDER_SEED_CONST
                + ": funding is not tied to the treasury's PDA")
        else:
            if not re.search(r"seeds\s*=\s*\[\s*" + FUNDER_SEED_CONST, body):
                problems["jackpot_seed_constraint"] = body[:200]
            if "seeds::program = TREASURY_PROGRAM_ID" not in body:
                problems["jackpot_not_pinned"] = (
                    "the funder PDA is not derived under the PINNED treasury "
                    "id, so a lookalike program's PDA would satisfy it")
            if not re.search(r"\bsigner\b", body):
                problems["jackpot_no_signer"] = (
                    "the funder account is not demanded as a SIGNER: passing "
                    "the PDA's address would be enough")
            if not re.search(r"\bbump\b", body):
                problems["jackpot_no_bump"] = body[:200]

    # The treasury's half: the SAME address, derived under its OWN id (the
    # only id it can ever sign for), put behind the CPI.
    tre_structs = [(name, body) for name, body in _accounts_bodies(treasury_src)
                   if FUNDER_SEED_CONST in body]
    if len(tre_structs) != 1:
        problems["treasury_funder_structs"] = [n for n, _ in tre_structs]
    else:
        tbody = tre_structs[0][1]
        if not re.search(r"seeds\s*=\s*\[\s*" + FUNDER_SEED_CONST, tbody):
            problems["treasury_seed_constraint"] = tbody[:200]
        if "seeds::program" in tbody:
            problems["treasury_foreign_program"] = (
                "the treasury derives the funder under another program's id, "
                "which it cannot sign for")
    if "new_with_signer" not in treasury_src:
        problems["treasury_no_signer"] = (
            "the CPI is not signed (no new_with_signer): the PDA is not a "
            "signer at the callee, so the call fails, or worse, no longer "
            "needs one")
    if "ctx.bumps.jackpot_funder" not in treasury_src:
        problems["treasury_bump"] = (
            "the signed bump is not the one the constraint derived "
            "(ctx.bumps), so the signature can cover a different address")
    if "xmrj_jackpot::cpi::fund_jackpot(" not in treasury_src:
        problems["no_cpi_call"] = "the treasury does not call the jackpot at all"

    # The bound: only what was RECORDED as convertible may be forwarded, once —
    # and since finding F2 was fixed it is VALUED AT THE RATE the state holds,
    # which is what makes `exchange_rate_bps` a control instead of a field two
    # instructions wrote and none read. Four facts in one place: the share the
    # treasury recorded, the rate it is converted with, what has already gone
    # out, and the amount bounded by what is left.
    body = _block_after(treasury_src, "pub fn fund_jackpot_with_xmr(") or ""
    missing = []
    if "total_converted_xmrj" not in body:
        missing.append("the recorded convertible share (total_converted_xmrj)")
    if "exchange_rate_bps" not in body:
        missing.append("the rate (exchange_rate_bps) — the bound would be "
                       "blind to it, which is exactly finding F2")
    if "total_jackpot_funded_xmr" not in body:
        missing.append("what has already been forwarded")
    if not re.search(r"checked_mul\(\s*rate\s*\)", body):
        missing.append("the rate MULTIPLIED into the share (checked_mul(rate))")
    if not re.search(r"amount_xmr\s*(?:as u128)?\s*\)?\s*<=\s*forwardable", body):
        missing.append("the amount required to be within what is left "
                       "(<= forwardable)")
    if missing:
        problems["no_bound"] = missing
    # A zero rate would value every recorded share at zero and brick funding,
    # so BOTH places that can set it refuse it.
    if treasury_src.count("exchange_rate_bps > 0") < 2:
        problems["rate_guard"] = (
            "the zero-rate refusal is missing from initialize and/or "
            "set_exchange_rate (found " + str(treasury_src.count("exchange_rate_bps > 0"))
            + " of 2)")
    return problems


_funder_bad = _funder_problems(JAC, TRE)
check("12.6 FUNDING IS TREASURY-ONLY BY SIGNATURE, not by a key comparison: "
      "the funder PDA's seed literal is one value in both crates, the jackpot "
      "demands that PDA as a SIGNER derived under the PINNED treasury id, the "
      "treasury derives the same address under its own id and signs for it in "
      "the CPI, and the amount is bounded by what the treasury recorded as "
      "convertible",
      not _funder_bad, _funder_bad)

_funder_mutations = [
    ("the signer constraint dropped from the jackpot's funder account",
     JAC.replace("        bump,\n        signer\n    )]", "        bump\n    )]", 1), TRE),
    ("the funder PDA no longer pinned under the treasury program",
     JAC.replace("        seeds::program = TREASURY_PROGRAM_ID,\n", "", 1), TRE),
    ("the seed literal changed in the jackpot only",
     JAC.replace(f'{FUNDER_SEED_CONST}: &[u8] = b"jackpot_funder"',
                 f'{FUNDER_SEED_CONST}: &[u8] = b"jackpot_funder_x"', 1), TRE),
    ("the treasury signing nothing (an unsigned CPI)",
     JAC, TRE.replace("new_with_signer", "new", 1)),
    ("the recorded-convertible bound removed from the treasury",
     JAC, TRE.replace("(amount_xmr as u128) <= forwardable",
                      "(amount_xmr as u128) <= u128::MAX", 1)),
    ("the RATE taken back out of the bound (the 1:1 comparison F2 replaced)",
     JAC, TRE.replace("checked_mul(rate)", "checked_mul(10_000)", 1)),
    ("the zero-rate refusal removed from an initializer/setter",
     JAC, TRE.replace("require!(exchange_rate_bps > 0, ErrorCode::InvalidExchangeRate);",
                      "", 1)),
    ("the funder seed constraint dropped from the treasury's account",
     JAC, TRE.replace("#[account(seeds = [JACKPOT_FUNDER_SEED], bump)]",
                      "#[account(mut)]", 1)),
]
_missed_funder = []
for why, jac_text, tre_text in _funder_mutations:
    if (jac_text, tre_text) == (JAC, TRE):
        _missed_funder.append((why, "mutation did not apply — fixture is stale"))
    elif not _funder_problems(jac_text, tre_text):
        _missed_funder.append((why, "the predicate found nothing wrong"))
check("12.7 RED-FIRST on the funding authorization: eight mutations — the "
      "signer constraint dropped, the PDA un-pinned from the treasury id, a "
      "seed literal changed on one side only, an unsigned CPI, the "
      "convertible bound removed, the RATE taken back out of that bound, the "
      "zero-rate refusal removed, and the treasury's seed constraint dropped "
      "— each trip the SAME predicate the real sources just passed through",
      not _missed_funder, _missed_funder)

# ── 12.8/12.9 the declared account SPACE, recomputed from the fields ────
# A state account whose `space =` is one field short fails at initialization
# on chain; one that is too large is rent nobody needs. Both constants were
# written by hand from the structs, and the treasury just gained a field — so
# the arithmetic is recomputed from the fields here.
_FIELD_SIZES = {"u64": 8, "i64": 8, "u32": 4, "i32": 4, "u16": 2, "u8": 1,
                "bool": 1, "Pubkey": 32}
SPACE = {"xmrj_treasury": ("TREASURY_STATE_SPACE", "TreasuryState"),
         "xmrj_jackpot": ("JACKPOT_STATE_SPACE", "JackpotState")}


def _sum_terms(expr):
    """'8 + 8 * 7 + 32' -> 96, by hand rather than eval(): the constants are
    sums of products of integers, and a drive that runs eval() on repository
    text is a drive that can be made to run anything."""
    total = 0
    for term in expr.split("+"):
        product = 1
        for factor in term.split("*"):
            product *= int(factor.strip())
        total += product
    return total


def _space_problems(sources):
    """{crate: detail} unless <NAME>_STATE_SPACE equals the 8-byte
    discriminator plus the serialized size of the #[account] struct's fields."""
    problems = {}
    for crate, (const, struct) in SPACE.items():
        text = sources.get(crate, "")
        m = re.search(rf"pub const {const}:\s*usize\s*=\s*([0-9+*() ]+);", text)
        if not m:
            problems[f"{crate}:{const}"] = "constant not found"
            continue
        try:
            declared = _sum_terms(m.group(1))
        except ValueError:
            problems[f"{crate}:{const}"] = "unreadable expression: " + m.group(1)
            continue
        s = re.search(rf"#\[account\]\s*pub struct {struct}\s*\{{", text)
        if not s:
            problems[f"{crate}:{struct}"] = "#[account] struct not found"
            continue
        fields = re.findall(r"pub\s+\w+\s*:\s*([A-Za-z0-9_]+)",
                            _balanced(text, s.end() - 1))
        unknown = [f for f in fields if f not in _FIELD_SIZES]
        if unknown:
            problems[f"{crate}:{struct}"] = {"unsized field type(s)": unknown}
            continue
        # 8 = the account discriminator Anchor writes in front of the fields.
        expected = 8 + sum(_FIELD_SIZES[f] for f in fields)
        if expected != declared:
            problems[f"{crate}:{const}"] = {"declared": declared,
                                            "fields_sum_to": expected,
                                            "layout": fields}
    return problems


_space_bad = _space_problems(src)
check("12.8 every state account's SPACE constant equals its own field layout "
      "(8-byte discriminator + the fields), recomputed from the struct — a "
      "constant one field short is a runtime failure at initialize, and the "
      "treasury gained a field in this change",
      not _space_bad, _space_bad)

_space_mutations = [
    ("the treasury's space constant still sized for six u64 fields",
     "xmrj_treasury",
     TRE.replace("8 + 8 * 7 + 32", "8 + 8 * 6 + 32", 1)),
    ("a state field widened to u32 with the constant left alone",
     "xmrj_treasury",
     TRE.replace("pub total_jackpot_funded_xmr: u64,",
                 "pub total_jackpot_funded_xmr: u32,", 1)),
    ("a term dropped from the jackpot's space constant",
     "xmrj_jackpot",
     JAC.replace("8 + 8 + 8 + 32 + 32 + 32 + 8", "8 + 8 + 8 + 32 + 32 + 8", 1)),
]
_missed_space = []
for why, crate, mutant in _space_mutations:
    if mutant == src[crate]:
        _missed_space.append((why, "mutation did not apply — fixture is stale"))
    elif not _space_problems({**src, crate: mutant}):
        _missed_space.append((why, "the predicate found no problem"))
check("12.9 RED-FIRST on the account layouts: the PRE-CHANGE space constant "
      "(sized for six u64 fields), a field widened without the constant "
      "following, and a dropped term each fail 12.8's predicate, while the "
      "real sources pass it",
      not _missed_space, _missed_space)

# ── 12.10/12.11 THE INITIALIZE GATE ─────────────────────────────────────
# Every state PDA is a FIXED SEED, so the first caller to initialize it wins
# the race — and the state is create-once. The programs therefore gate their
# initializers on their own UPGRADE AUTHORITY: each `Initialize` struct takes
# the program's ProgramData account (derived from the program's id under the
# loader, so it cannot be substituted), and each initializer requires the
# recorded authority to be the signer. This section exists because that
# guarantee lives in three separate crates and one program-data BYTE LAYOUT:
# a dropped account, a dropped `seeds::program`, a changed loader literal or a
# comparison against anything but the signer silently reopens the hole.
LOADER_ID_LITERAL = "BPFLoaderUpgradeab1e11111111111111111111111"
PROGRAM_DATA_LAYOUT = {
    "variant": "PROGRAM_DATA_VARIANT: u32 = 3",
    "option_offset": "PROGRAM_DATA_AUTHORITY_OPTION_OFFSET: usize = 4 + 8",
    "authority_offset": "PROGRAM_DATA_AUTHORITY_OFFSET: usize = "
                        "PROGRAM_DATA_AUTHORITY_OPTION_OFFSET + 1",
    "min_len": "PROGRAM_DATA_MIN_LEN: usize = PROGRAM_DATA_AUTHORITY_OFFSET + 32",
}


def _upgrade_gate_problems(sources):
    """{problem: detail} unless EVERY program's initializer is gated on its
    own upgrade authority: the loader id pinned by literal, the ProgramData
    address derived from the program's OWN id under that loader, the
    instruction actually calling the gate with `program_data` and the payer,
    and the gate comparing the bytes the loader wrote against the signer."""
    problems = {}
    for name in PROGRAMS:
        text = sources.get(name, "")

        pin = re.search(
            r'UPGRADEABLE_LOADER_ID:\s*Pubkey\s*=\s*[^;]*?"([^"]+)"', text)
        if not pin:
            problems[f"{name}:loader_id"] = "no pinned UPGRADEABLE_LOADER_ID"
        elif pin.group(1) != LOADER_ID_LITERAL:
            problems[f"{name}:loader_id"] = pin.group(1)

        # THE LOADER'S RULE: a program's ProgramData account is
        # `[<program id>]` under UPGRADEABLE_LOADER_ID — one seed, and NO
        # string prefix. A `PROGRAM_DATA_SEED` prefix is the defect this check
        # exists to keep out: it derives a DIFFERENT address, and `initialize`
        # is refused there with `NotProgramData` (6004).
        if "PROGRAM_DATA_SEED" in text:
            problems[f"{name}:program_data_seed"] = (
                "a PROGRAM_DATA_SEED prefix is back; the loader derives the "
                "address from the program id alone")

        for label, needle in PROGRAM_DATA_LAYOUT.items():
            if needle not in text:
                problems[f"{name}:layout_{label}"] = needle

        fn = re.search(
            r"pub fn (initialize|initialize_governance)\(\s*ctx:\s*Context<(\w+)>",
            text)
        if not fn:
            problems[f"{name}:initializer"] = "no initializer found"
            continue
        struct = dict(_accounts_bodies(text)).get(fn.group(2))
        if struct is None:
            problems[f"{name}:accounts_struct"] = fn.group(2)
        else:
            if not re.search(r"pub\s+program_data\s*:", struct):
                problems[f"{name}:program_data_account"] = (
                    fn.group(2) + " does not take the program's ProgramData")
            elif not re.search(r"seeds\s*=\s*\[\s*[^\]]*\]", struct):
                problems[f"{name}:program_data_seed_constraint"] = struct[:200]
            elif "crate::ID" not in struct:
                problems[f"{name}:program_data_not_own_id"] = (
                    "the ProgramData address is not this program's")
            elif not re.search(r"seeds\s*=\s*\[\s*crate::ID\.as_ref\(\)\s*\]",
                               struct):
                problems[f"{name}:program_data_seed_prefix"] = (
                    "the ProgramData seeds carry a prefix; the loader derives "
                    "the address from the program id alone")
            elif "seeds::program = UPGRADEABLE_LOADER_ID" not in struct:
                problems[f"{name}:program_data_not_loader"] = (
                    "the ProgramData address is not derived under the "
                    "upgradeable loader")

        body = _block_after(text, "pub fn " + fn.group(1)) or ""
        if "require_upgrade_authority(" not in body:
            problems[f"{name}:no_gate_call"] = (
                "initialize never calls require_upgrade_authority")
        elif "&ctx.accounts.program_data" not in body:
            problems[f"{name}:gate_account"] = (
                "the gate is called on something other than program_data")
        elif "ctx.accounts.payer.key()" not in body:
            problems[f"{name}:gate_signer"] = (
                "the gate is not called on the signing payer")

        helper = _block_after(text, "fn require_upgrade_authority(") or ""
        for label, missing in (
            ("owner", "*info.owner"),
            ("loader", "UPGRADEABLE_LOADER_ID"),
            ("variant", "PROGRAM_DATA_VARIANT"),
            ("immutable", "ProgramIsImmutable"),
            ("authority", "PROGRAM_DATA_AUTHORITY_OFFSET"),
        ):
            if missing not in helper:
                problems[f"{name}:gate_{label}"] = "missing " + missing
        if not re.search(r"require_keys_eq!\(\s*Pubkey::new_from_array\(authority\)"
                         r"\s*,\s*\*signer\s*,", helper):
            problems[f"{name}:gate_compares_signer"] = (
                "the authority bytes are not compared against the signer")
    return problems


_gate_bad = _upgrade_gate_problems(src)
check("12.10 EVERY initializer is gated on its own upgrade authority: the "
      "loader id is pinned by literal, the ProgramData address is derived "
      "from the program's own id under that loader (so a lookalike cannot be "
      "substituted), the instruction calls the gate on `program_data` and the "
      "payer, and the gate compares the bytes the loader wrote against that "
      "signer — the fix for the fixed-seed init race the plan recorded as an "
      "open item",
      not _gate_bad, _gate_bad)

_gate_mutations = [
    ("the ProgramData account dropped from the jackpot's initializer",
     "xmrj_jackpot",
     JAC.replace("    pub program_data: UncheckedAccount<'info>,\n", "", 1)),
    ("the ProgramData address no longer derived under the loader",
     "xmrj_treasury",
     TRE.replace("        seeds::program = UPGRADEABLE_LOADER_ID,\n", "", 1)),
    ("a character flipped in one crate's loader literal",
     "xmrj_governance",
     GOV.replace(LOADER_ID_LITERAL, LOADER_ID_LITERAL[:-1] + "2", 1)),
    ("the gate call removed from the jackpot's initializer body",
     "xmrj_jackpot",
     JAC.replace("        require_upgrade_authority(\n"
                 "            &ctx.accounts.program_data,\n"
                 "            &ctx.accounts.payer.key(),\n"
                 "        )?;\n", "", 1)),
    ("the gate comparing the authority against a default key, not the signer",
     "xmrj_treasury",
     TRE.replace("        Pubkey::new_from_array(authority),\n        *signer,\n",
                 "        Pubkey::new_from_array(authority),\n"
                 "        Pubkey::default(),\n", 1)),
    ("the immutable-program branch removed from the jackpot's gate",
     "xmrj_jackpot",
     JAC.replace("ErrorCode::ProgramIsImmutable", "ErrorCode::NotProgramData", 1)),
]
_missed_gate = []
for why, crate, mutant in _gate_mutations:
    if mutant == src[crate]:
        _missed_gate.append((why, "mutation did not apply — fixture is stale"))
    elif not _upgrade_gate_problems({**src, crate: mutant}):
        _missed_gate.append((why, "the predicate found nothing wrong"))
check("12.11 RED-FIRST on the initialize gate: six mutations — the "
      "ProgramData account dropped, its derivation un-pinned from the "
      "loader, a flipped loader literal, the gate call deleted, the "
      "comparison pointed at a default key instead of the signer, and the "
      "immutable-program refusal removed — each trip 12.10's predicate, "
      "while the real sources pass it",
      not _missed_gate, _missed_gate)

# ── 12.12/12.13 THE OPERATIONAL GATE ───────────────────────────────────
# record_inflow_xmrj and fund_jackpot_with_xmr used to be open calls: anyone
# could record an inflow (choosing the reserve/conversion split of real money)
# and anyone could trigger the funding transfer. The fix is the small key both
# of them — and only them — must sign with: an OPERATIONAL AUTHORITY recorded
# in the treasury state, chosen at initialize and rotatable ONLY by an approved
# governance proposal. It is deliberately NOT `admin_authority`: that is the
# governance authority PDA, and the proposal PDA is a singleton (9.3), so every
# operational call routed through it would be a once-per-cluster act.
#
# The guarantee spans two crates and three instructions, so it is one predicate:
# the state field, the initialize parameter that seeds it, the `has_one` +
# `signer` pair on BOTH operational calls, the admin-gated rotator, and the
# governance variant + arm that reaches the rotator by CPI (signed with the
# authority PDA's own seeds). A dropped `signer`, a `has_one` pointed at the
# wrong field, a rotator that no longer writes, or an arm that does not CPI all
# reopen the hole while leaving the field in place.
OP_FIELD = "operational_authority"
OP_SETTER = "set_operational_authority"


def _arm_block(src, variant):
    """The `{ ... }` of `GovernanceAction::<variant> ... => { ... }` — the
    handler arm's BODY, not the enum's field list (the first brace after the
    marker belongs to the pattern, so we start at the `=>`)."""
    i = src.find("GovernanceAction::" + variant)
    if i < 0:
        return None
    arrow = src.find("=>", i)
    brace = src.find("{", arrow) if arrow >= 0 else -1
    return _balanced(src, brace) if brace >= 0 else None


def _drop_arm(src, variant):
    """`src` with one handler arm removed, for the red-first fixture below."""
    i = src.find("GovernanceAction::" + variant)
    arm = _arm_block(src, variant)
    if i < 0 or arm is None:
        return src
    line_start = src.rfind("\n", 0, i) + 1
    end = src.find(arm, i) + len(arm)
    return src[:line_start] + src[end:]


def _operational_gate_problems(treasury_src, governance_src):
    """{problem: detail} unless the two money-moving treasury calls are gated
    on an operational authority the state RECORDS, seeded at initialize and
    rotatable only from an approved governance proposal."""
    problems = {}

    state = _block_after(treasury_src, "#[account]\npub struct TreasuryState")
    if state is None:
        problems["state"] = "no TreasuryState struct"
    elif not re.search(r"pub\s+" + OP_FIELD + r"\s*:\s*Pubkey", state):
        problems["field"] = ("TreasuryState does not record " + OP_FIELD
                             + ": the calls have nothing to check")

    sig = re.search(r"pub fn initialize\((.*?)\)\s*->\s*Result<\(\)>",
                    treasury_src, re.S)
    if not sig:
        problems["initialize"] = "no `pub fn initialize(...)`"
    elif not re.search(OP_FIELD + r"\s*:\s*Pubkey", sig.group(1)):
        problems["initialize_param"] = (
            "initialize does not TAKE the operational authority, so the "
            "deployer cannot choose it and the state would start with a "
            "default key nobody controls")
    else:
        body = _block_after(treasury_src, "pub fn initialize(") or ""
        if f"state.{OP_FIELD} = {OP_FIELD};" not in body:
            problems["initialize_store"] = (
                "initialize never writes the parameter into the state")

    structs = dict(_accounts_bodies(treasury_src))
    for ix in ("record_inflow_xmrj", "fund_jackpot_with_xmr"):
        fn = re.search(r"pub fn " + ix + r"\(\s*ctx:\s*Context<(\w+)>",
                       treasury_src)
        if not fn:
            problems[ix] = "no `pub fn " + ix + "`"
            continue
        body = structs.get(fn.group(1))
        if body is None:
            problems[ix + ":accounts_struct"] = fn.group(1)
            continue
        if not re.search(r"has_one\s*=\s*" + OP_FIELD, body):
            problems[ix + ":has_one"] = (
                fn.group(1) + " does not pair the state with " + OP_FIELD
                + ": the address checked would be a caller-supplied input")
        if not re.search(r"#\[account\([^)]*\bsigner\b[^)]*\)\]\s*pub\s+"
                         + OP_FIELD + r"\s*:", body):
            problems[ix + ":signer"] = (
                fn.group(1) + " does not demand the operational authority as "
                "a SIGNER: a relayer could move the treasury")

    setter = re.search(r"pub fn " + OP_SETTER + r"\(\s*ctx:\s*Context<(\w+)>,\s*"
                       r"new_authority:\s*Pubkey", treasury_src)
    if not setter:
        problems["setter"] = ("no " + OP_SETTER
                              + "(ctx, new_authority: Pubkey): a lost or "
                              "leaked operational key would be permanent")
    else:
        body = structs.get(setter.group(1))
        if body is None:
            problems["setter:accounts_struct"] = setter.group(1)
        else:
            if not re.search(r"has_one\s*=\s*admin_authority", body):
                problems["setter:has_one"] = (
                    "the rotator is not admin-gated: the operational key "
                    "could replace itself")
            if not re.search(r"#\[account\([^)]*\bsigner\b[^)]*\)\]\s*pub\s+"
                             r"admin_authority\s*:", body):
                problems["setter:admin_signer"] = (
                    "the admin authority is not demanded as a signer")
        if f"state.{OP_FIELD} = new_authority;" not in (
                _block_after(treasury_src, "pub fn " + OP_SETTER) or ""):
            problems["setter:write"] = "the rotator does not write the state"

    if not re.search(r"SetOperationalAuthority\s*\{\s*new_authority\s*:\s*"
                     r"Pubkey\s*\}", governance_src):
        problems["gov:variant"] = (
            "governance has no SetOperationalAuthority { new_authority } "
            "variant, so the key can never be rotated")
    arm = _arm_block(governance_src, "SetOperationalAuthority")
    if arm is None:
        problems["gov:arm"] = "no handler arm for SetOperationalAuthority"
    else:
        if "xmrj_treasury::cpi::" + OP_SETTER + "(" not in arm:
            problems["gov:cpi"] = "the arm does not CPI into " + OP_SETTER
        if "xmrj_treasury::cpi::accounts::SetOperationalAuthority {" not in arm:
            problems["gov:cpi_accounts"] = (
                "the CPI does not build the callee's accounts struct")
        if ("admin_authority: ctx.accounts.governance_authority"
                ".to_account_info()") not in arm:
            problems["gov:cpi_admin"] = (
                "the CPI does not pass the governance authority, so the "
                "treasury's has_one cannot be satisfied by a proposal")
        if "authority_seeds" not in arm:
            problems["gov:no_signer"] = (
                "the CPI is not signed with the authority PDA's seeds")
        if "new_authority" not in arm:
            problems["gov:arg"] = "the arm does not forward the new key"
    return problems


_op_bad = _operational_gate_problems(TRE, GOV)
check("12.12 EVERY money-moving treasury call is gated on an OPERATIONAL "
      "AUTHORITY the state records: initialize takes and stores it, "
      "record_inflow_xmrj and fund_jackpot_with_xmr each pair `has_one = "
      "operational_authority` with a SIGNER, and governance alone can rotate "
      "it — `set_operational_authority` reached by CPI from an approved "
      "proposal, not from a wallet — closing the open item the funding bound "
      "was standing in for",
      not _op_bad, _op_bad)

def _drop_nth(text, needle, n):
    """`text` with the n-th (1-based) occurrence of `needle` removed. The two
    operational calls share the same account block, so only position tells
    them apart once the source's comments are stripped (as every fixture
    here is: `code()` the predicate's inputs)."""
    idx = -1
    for _ in range(n):
        idx = text.find(needle, idx + 1)
        if idx < 0:
            return text
    return text[:idx] + text[idx + len(needle):]


_op_mutations = [
    ("the state field dropped while both callers still name it",
     TRE.replace("    pub operational_authority: Pubkey,\n", "", 1), GOV),
    ("the initialize parameter dropped (the state would start with no key)",
     TRE.replace("        operational_authority: Pubkey,\n", "", 1), GOV),
    ("the initialize assignment removed",
     TRE.replace("state.operational_authority = operational_authority;",
                 "", 1), GOV),
    ("the signer dropped from the recording call's operational account",
     TRE.replace("#[account(signer)]\n    pub operational_authority: "
                 "AccountInfo<'info>,",
                 "pub operational_authority: AccountInfo<'info>,", 1), GOV),
    ("the `has_one` dropped from the funding call's state account",
     _drop_nth(TRE, "        has_one = operational_authority\n", 2), GOV),
    ("the rotator no longer writing the state",
     TRE.replace("state.operational_authority = new_authority;",
                 "state.operational_authority = state.operational_authority;",
                 1), GOV),
    ("the governance handler arm removed",
     TRE, _drop_arm(GOV, "SetOperationalAuthority")),
    ("the governance CPI no longer signed with the authority seeds",
     TRE, GOV.replace(_arm_block(GOV, "SetOperationalAuthority") or "\0",
                      (_arm_block(GOV, "SetOperationalAuthority") or
                       "\0").replace("authority_seeds,", "", 1), 1)),
]
_missed_op = []
for why, tre_text, gov_text in _op_mutations:
    if (tre_text, gov_text) == (TRE, GOV):
        _missed_op.append((why, "mutation did not apply — fixture is stale"))
    elif not _operational_gate_problems(tre_text, gov_text):
        _missed_op.append((why, "the predicate found nothing wrong"))
check("12.13 RED-FIRST on the operational gate: eight mutations — the state "
      "field dropped, the initialize parameter dropped, the initialize "
      "assignment removed, the recording call's signature dropped, the "
      "funding call's `has_one` dropped, the rotator made a no-op, the "
      "governance arm removed, and the CPI left unsigned — each trip the SAME "
      "predicate the real sources just passed through",
      not _missed_op, _missed_op)

# ── 12.14/12.15 THE PAYOUT PATH — the other half of the authority chain ─
# 12.6 closed the AUTHORIZATION for FUNDING. Nothing closed the mirror-image
# question for PAYING OUT, and the answer was that there was no path at all:
# `trigger_payout` demanded the governance authority PDA as a signer, a PDA can
# only sign inside a CPI from the program that derives it, and no
# `GovernanceAction` variant called it — so a funded jackpot could be topped up
# forever and never paid (finding F1 of XMRJ_DEVNET_EXECUTION_REPORT.md).
#
# The guarantee spans two crates, so it is ONE predicate: the callee's two
# checks, the derivation that makes the stored authority a PDA, the caller's
# variant and arm and the seeds the arm signs with — plus the fact that makes it
# a single path rather than the first of several, that EXACTLY ONE call site in
# the workspace reaches the payout instruction.
PAYOUT_IX = "trigger_payout"
PAYOUT_VARIANT = "TriggerPayout"


def _payout_path_problems(sources):
    """{problem: detail} unless a funded jackpot can be PAID out, through an
    approved governance proposal and through nothing else."""
    problems = {}
    jackpot_src = sources["xmrj_jackpot"]
    governance_src = sources["xmrj_governance"]

    # (1) THE CALLEE'S GATE: the authority the STATE records, demanded as a
    # SIGNER, on an account the instruction can write.
    fn = re.search(r"pub fn " + PAYOUT_IX + r"\(\s*ctx:\s*Context<(\w+)>",
                   jackpot_src)
    if not fn:
        problems["callee_fn"] = "no `pub fn " + PAYOUT_IX + "` in the jackpot"
    else:
        body = dict(_accounts_bodies(jackpot_src)).get(fn.group(1))
        if body is None:
            problems["callee_struct"] = fn.group(1)
        else:
            if not re.search(r"#\[account\([^)]*\bsigner\b[^)]*\)\]\s*pub\s+"
                             r"admin_authority\s*:", body):
                problems["callee_no_signer"] = (
                    PAYOUT_IX + " does not demand the admin authority as a "
                    "SIGNER: any wallet could pay the jackpot out")
            if not re.search(r"#\[account\([^)]*\bmut\b[^)]*\)\]\s*pub\s+"
                             r"jackpot_state\s*:", body):
                problems["callee_state_not_mut"] = (
                    "the payout cannot write the jackpot's own state, so nothing "
                    "would move")
    if not re.search(r"ctx\.accounts\.admin_authority\.key\(\)\s*==\s*"
                     r"state\.admin_authority", jackpot_src):
        problems["callee_key_check"] = (
            PAYOUT_IX + " does not compare the caller's key with the authority "
            "the state records, so the signature would be for the wrong account")

    # (2) THAT AUTHORITY IS THE GOVERNANCE AUTHORITY PDA — the one address
    # xmrj_governance can sign for and no keypair holds.
    init_body = _block_after(jackpot_src, "pub struct Initialize<'info>") or ""
    if not re.search(r"seeds\s*=\s*\[b\"governance_authority\"\]", init_body):
        problems["callee_authority_seed"] = (
            "the jackpot does not derive its admin authority from the governance "
            "authority seed, so the signer the payout needs is not the address "
            "governance can sign for")
    elif "seeds::program = GOVERNANCE_PROGRAM_ID" not in init_body:
        problems["callee_authority_program"] = (
            "the authority is not derived under the PINNED governance program "
            "id, so it is some other program's PDA")

    # (3) THE CALLER: a variant that carries the payout, an arm for it, and a
    # CPI signed with the authority PDA's own seeds.
    if not re.search(PAYOUT_VARIANT + r"\s*\{\s*winner\s*:\s*Pubkey\s*,\s*"
                     r"amount_xmr\s*:\s*u64\s*\}", governance_src):
        problems["gov_variant"] = (
            "GovernanceAction has no " + PAYOUT_VARIANT + " { winner, "
            "amount_xmr } variant, so no proposal can authorize a payout")
    arm = _arm_block(governance_src, PAYOUT_VARIANT)
    if arm is None:
        problems["gov_arm"] = "no handler arm for " + PAYOUT_VARIANT
    else:
        if "xmrj_jackpot::cpi::" + PAYOUT_IX + "(" not in arm:
            problems["gov_cpi"] = "the arm does not CPI into " + PAYOUT_IX
        if "xmrj_jackpot::cpi::accounts::" + PAYOUT_VARIANT + " {" not in arm:
            problems["gov_cpi_accounts"] = (
                "the CPI does not build the callee's accounts struct")
        if ("admin_authority: ctx.accounts.governance_authority"
                ".to_account_info()") not in arm:
            problems["gov_cpi_admin"] = (
                "the CPI does not pass the governance authority as the admin "
                "authority, so the callee's key check cannot be satisfied")
        if "authority_seeds" not in arm:
            problems["gov_no_signer"] = (
                "the CPI is not signed with the authority PDA's seeds, so the "
                "callee's signer requirement is unmet and the payout fails")
        for needle in ("winner", "amount_xmr"):
            if needle not in arm:
                problems["gov_arg:" + needle] = (
                    "the arm does not forward " + needle)

    # (4) ONE PATH, NOT SEVERAL: exactly one call site in the whole workspace
    # reaches the payout instruction. A second one — a helper, a re-export, an
    # "admin" shortcut — would be another way to move the jackpot's money, and
    # it would not necessarily be signed by the authority PDA.
    call_sites = [n + "::" + c + "::" + f
                  for n, text in sources.items()
                  for c, f in re.findall(
                      r"(xmrj_[a-z_]+)::cpi::([a-z_][a-z0-9_]*)\s*\(", text)
                  if f == PAYOUT_IX]
    if call_sites != ["xmrj_governance::xmrj_jackpot::" + PAYOUT_IX]:
        problems["one_path"] = {"call_sites": call_sites}
    return problems


_payout_bad = _payout_path_problems(src)
check("12.14 A FUNDED JACKPOT CAN BE PAID — through an approved proposal and "
      "through nothing else (finding F1): trigger_payout demands the state's "
      "admin authority as a SIGNER and compares it with what the state "
      "records; the jackpot DERIVES that authority as the governance authority "
      "PDA under the pinned governance id; governance carries a TriggerPayout "
      "variant whose arm CPIs into trigger_payout, builds the callee's "
      "accounts, passes that PDA as the admin authority and signs with its "
      "seeds — and EXACTLY ONE call site in the workspace reaches the "
      "instruction, so there is no second, off-path way to pay the jackpot",
      not _payout_bad, _payout_bad)

_payout_mutations = [
    ("the payout variant removed from the enum",
     {"xmrj_governance": GOV.replace(
         "    TriggerPayout { winner: Pubkey, amount_xmr: u64 },",
         "    SetSplitRatio { reserve_bps: u64, jackpot_bps: u64 },", 1)}),
    ("the handler arm removed",
     {"xmrj_governance": _drop_arm(GOV, PAYOUT_VARIANT)}),
    ("the arm's CPI left unsigned",
     {"xmrj_governance": GOV.replace(
         _arm_block(GOV, PAYOUT_VARIANT) or "\0",
         (_arm_block(GOV, PAYOUT_VARIANT) or "\0").replace(
             "authority_seeds,", "", 1), 1)}),
    # Scoped to the payout ARM, not to the file: `admin_authority:
    # ctx.accounts.governance_authority.to_account_info()` is the same line in
    # every parameter arm, so an unscoped replacement would corrupt an earlier
    # arm and leave the payout one correct — a fixture that proves nothing.
    ("the arm pointing the authority at the jackpot's own state",
     {"xmrj_governance": GOV.replace(
         _arm_block(GOV, PAYOUT_VARIANT) or "\0",
         (_arm_block(GOV, PAYOUT_VARIANT) or "\0").replace(
             "admin_authority: ctx.accounts.governance_authority"
             ".to_account_info()",
             "admin_authority: ctx.accounts.jackpot_state.to_account_info()",
             1), 1)}),
    ("a SECOND call site added (an off-path payout)",
     {"xmrj_treasury": TRE + "\nfn off_path(a: u64) { let _ = "
      "xmrj_jackpot::cpi::" + PAYOUT_IX + "(a); }"}),
    ("the callee's signer demand dropped",
     {"xmrj_jackpot": JAC.replace(
         "#[account(signer)]\n    pub admin_authority: AccountInfo<'info>,",
         "pub admin_authority: AccountInfo<'info>,", 1),}),
    ("the callee's authority no longer derived under the governance id",
     {"xmrj_jackpot": JAC.replace("seeds::program = GOVERNANCE_PROGRAM_ID,",
                                  "", 1)}),
    ("the callee's key comparison removed",
     {"xmrj_jackpot": JAC.replace(
         "ctx.accounts.admin_authority.key() == state.admin_authority",
         "ctx.accounts.admin_authority.key() != Pubkey::default()", 1)}),
]
_missed_payout = []
for why, override in _payout_mutations:
    mutated = dict(src)
    mutated.update(override)
    if all(mutated[k] == src[k] for k in override):
        _missed_payout.append((why, "mutation did not apply — fixture is stale"))
    elif not _payout_path_problems(mutated):
        _missed_payout.append((why, "the predicate found nothing wrong"))
check("12.15 RED-FIRST on the payout path: eight mutations — the variant "
      "removed, the handler arm removed, the CPI left unsigned, the authority "
      "pointed at the wrong account, a SECOND call site added, the callee's "
      "signer demand dropped, its authority derivation un-pinned from the "
      "governance id, and its key comparison removed — each trip the SAME "
      "predicate the real sources just passed through",
      not _missed_payout, _missed_payout)

# ── 12.16/12.17 PROPOSALS BY ID — a governance state that can act twice ──
# The proposal PDA used to be ["proposal", governance_state] with `init`, so a
# deployed governance state could execute EXACTLY ONE action and then never
# propose again: a parameter that can be set once and never corrected (finding
# F5). The fix is an id: ["proposal", governance_state, proposal_id], with the
# id required to be the NEXT one in the state's own count, so ids cannot be
# reused or skipped and the order proposals were made in is recorded rather
# than implied.
def _proposal_sequence_problems(governance_src):
    """{problem: detail} unless every proposal has an id of its own."""
    problems = {}
    if not re.search(r"#\[account\]\s*pub struct GovernanceState\s*\{[^}]*"
                     r"pub\s+proposal_count\s*:\s*u64", governance_src, re.S):
        problems["no_counter"] = (
            "GovernanceState does not record proposal_count, so nothing knows "
            "which id is next")
    if not re.search(r"pub fn propose_action\(\s*ctx:\s*Context<ProposeAction>"
                     r"\s*,\s*proposal_id:\s*u64\s*,", governance_src):
        problems["no_param"] = (
            "propose_action does not take a proposal_id, so a second proposal "
            "would compete for the first one's address")
    propose_struct = _block_after(governance_src,
                                  "pub struct ProposeAction<'info>") or ""
    # The attribute sits ABOVE the struct, so it is matched on the source with
    # the declaration it belongs to rather than inside the struct's braces.
    if not re.search(r"#\[instruction\(proposal_id: u64\)\]\s*"
                     r"pub struct ProposeAction", governance_src):
        problems["no_instruction_attr"] = (
            "ProposeAction carries no `#[instruction(proposal_id: u64)]`, so the "
            "account derive cannot build seeds from the id")
    for struct_name in ("ProposeAction", "ApproveAction", "ExecuteAction"):
        body = _block_after(governance_src,
                            "pub struct " + struct_name + "<'info>") or ""
        if not re.search(r"seeds\s*=\s*\[[^\]]*"
                         r"governance_state\.key\(\)\.as_ref\(\)[^\]]*"
                         r"proposal_id\.to_le_bytes\(\)\.as_ref\(\)",
                         body, re.S):
            problems["seeds:" + struct_name] = (
                struct_name + " does not derive the proposal from [literal, "
                "governance_state, proposal_id], so a lookup by id would find "
                "the wrong account or none")
    if not re.search(r"proposal_id\s*==\s*state\.proposal_count",
                     governance_src):
        problems["no_sequence"] = (
            "propose_action does not require the id to BE the next one in the "
            "sequence, so ids could be reused or skipped")
    if not re.search(r"state\.proposal_count\s*=\s*state\s*\.\s*proposal_count"
                     r"\s*\.checked_add\(1\)", governance_src):
        problems["no_increment"] = (
            "proposal_count is never incremented, so the state could never "
            "accept a second proposal")
    if not re.search(r"#\[account\]\s*pub struct Proposal\s*\{[^}]*"
                     r"pub\s+proposal_id\s*:\s*u64", governance_src, re.S):
        problems["not_stored"] = (
            "the Proposal account does not record its own id, so it cannot say "
            "which id it is")
    checked = len(re.findall(r"proposal\.proposal_id\s*==\s*proposal_id",
                             governance_src))
    if checked < 2:
        problems["id_checked"] = (
            "approve_action and execute_action do not BOTH re-check the id "
            "against the account they were handed (found " + str(checked) + ")")
    space = re.search(r"pub const PROPOSAL_SPACE:\s*usize\s*=\s*([0-9+*() A-Z_]+);",
                      governance_src)
    action_len = re.search(r"pub const MAX_ACTION_LEN:\s*usize\s*=\s*([0-9+*() ]+);",
                           governance_src)
    if "space = PROPOSAL_SPACE" not in propose_struct:
        problems["space"] = (
            "the proposal account is not sized from PROPOSAL_SPACE: a round "
            "number would be too small the day a variant grows")
    elif not (space and action_len):
        problems["space_const"] = "PROPOSAL_SPACE / MAX_ACTION_LEN not found"
    else:
        try:
            largest = _sum_terms(action_len.group(1))
            total = _sum_terms(space.group(1).replace("MAX_ACTION_LEN",
                                                      str(largest)))
        except ValueError:
            problems["space_const"] = space.group(1)
        else:
            # 8 discriminator + 8 id + the largest variant + approvals +
            # executed. The largest variant today is TriggerPayout: a 1-byte
            # discriminant, a 32-byte key and a u64.
            if largest < 1 + 32 + 8 or total < 8 + 8 + largest + 1 + 1:
                problems["space_too_small"] = {"MAX_ACTION_LEN": largest,
                                               "PROPOSAL_SPACE": total}
    return problems


_proposal_bad = _proposal_sequence_problems(GOV)
check("12.16 EVERY PROPOSAL HAS AN ID OF ITS OWN (finding F5): GovernanceState "
      "counts proposals, propose_action takes the id and requires it to BE the "
      "next one in that count, the count is incremented so the state can accept "
      "another, all three instructions derive the same three-seed PDA "
      "[literal, governance_state, proposal_id], the id is re-checked against "
      "the account in BOTH approve and execute, and the account is sized from "
      "the largest action variant rather than a round number",
      not _proposal_bad, _proposal_bad)

_proposal_mutations = [
    ("the id dropped from the proposal PDA's seeds",
     GOV.replace("proposal_id.to_le_bytes().as_ref()", "", 1)),
    ("the sequence check loosened to accept any id",
     GOV.replace("proposal_id == state.proposal_count", "proposal_id >= 0", 1)),
    ("the counter never incremented",
     GOV.replace(".checked_add(1)", ".checked_add(0)", 1)),
    ("the stored id no longer checked in approve/execute",
     GOV.replace("proposal.proposal_id == proposal_id",
                 "proposal.proposal_id >= 0")),
    ("the proposal account sized from a round number again",
     GOV.replace("space = PROPOSAL_SPACE", "space = 8 + 64", 1)),
    ("the id argument removed from propose_action",
     GOV.replace("proposal_id: u64,\n        action: GovernanceAction,",
                 "action: GovernanceAction,", 1)),
]
_missed_proposal = []
for why, mutant in _proposal_mutations:
    if mutant == GOV:
        _missed_proposal.append((why, "mutation did not apply — fixture is stale"))
    elif not _proposal_sequence_problems(mutant):
        _missed_proposal.append((why, "the predicate found nothing wrong"))
check("12.17 RED-FIRST on the proposal id sequence: six mutations — the id "
      "dropped from the seeds, the sequence check loosened, the counter never "
      "incremented, the stored id no longer re-checked, the account sized from "
      "a round number, and the id argument removed — each trip the SAME "
      "predicate the real source just passed through",
      not _missed_proposal, _missed_proposal)

# ── 13. the toolchain pins: five values, one fact ────────────────────────
# Moving off Anchor 0.30 is the kind of change that gets HALF applied — a crate
# on a new anchor-lang while the CLI stays old, a rust-toolchain.toml that says
# "stable", a deployment plan still telling a fresh clone to install the moving
# `stable` channel. Each of those means the clone builds something other than
# what the manifest describes. So they are one predicate, and the mutations in
# 13.5 go through the same function the real artifacts just went through —
# which is what makes that red-first check a real one.
RUST_TOOLCHAIN = HERE / "rust-toolchain.toml"
PLAN = HERE / "XMRJ_SOLANA_DEPLOYMENT_PLAN.md"
FLOATING_RUST = ("stable", "beta", "nightly", "dev")
_FLOATING_DOC = re.compile(
    r"release\.anza\.xyz/(?:stable|beta|edge)/"
    r"|\bavm (?:install|use) latest\b"
    r"|\bagave-install (?:init|update) (?:stable|beta|edge)\b")


def _toolchain_pin_problems(manifest, crates, rusttc, plan_text):
    """ONE predicate for the five pins. Returns {problem: detail}."""
    problems = {}
    tc = manifest.get("toolchain") or {}
    anchor_pin = str(tc.get("anchor_version", "")).strip()
    solana_pin = str(tc.get("solana_version", "")).strip()
    # anchor_version: an exact three-part version, no range operator.
    if not re.match(r"^\d+\.\d+\.\d+$", anchor_pin):
        problems["anchor_version"] = anchor_pin
    # solana_version: an exact three-part version too — and NOT a comparator.
    # AVM would accept a `=` requirement on this key, but the anchor CLI is the
    # reader that runs the build, and it parses the value as a plain version:
    # `=3.1.10` stops the build outright ("unexpected character '=' while
    # parsing major version number", anchor-cli 1.1.2). A floor written as
    # "3.1" or "^3.1.10" drifts and is caught here as well.
    if not re.match(r"^\d+\.\d+\.\d+$", solana_pin):
        problems["solana_version"] = solana_pin
    channel = str(((rusttc.get("toolchain") or {}).get("channel")) or "").strip()
    if not re.match(r"^\d+\.\d+\.\d+$", channel) or channel in FLOATING_RUST:
        problems["rust_channel"] = channel
    # Every crate — the three programs AND the off-chain conversion service —
    # declares the same anchor-lang version as the CLI in [toolchain].
    for name in list(crates):
        dep = (crates[name].get("dependencies") or {}).get("anchor-lang")
        dep = dep if isinstance(dep, str) else (dep or {}).get("version")
        if anchor_pin and dep != "=" + anchor_pin:
            problems["anchor-lang:" + name] = {"pinned": dep,
                                               "expected": "=" + anchor_pin}
    # The plan a human follows states the same three versions...
    for label, needle in (("anchor", anchor_pin),
                          ("solana", solana_pin.lstrip("=")),
                          ("rust", channel)):
        if needle and needle not in plan_text:
            problems["doc:" + label] = needle
    # ...and does not point at a channel that moves.
    floating = _FLOATING_DOC.findall(plan_text)
    if floating:
        problems["floating_channel_in_doc"] = floating
    return problems


try:
    _rusttc = tomllib.loads(read(RUST_TOOLCHAIN))
except Exception as exc:                                    # noqa: BLE001
    _rusttc = {}
    check("13.0 rust-toolchain.toml exists and parses as TOML", False, exc)
_pin_problems = _toolchain_pin_problems(cfg, _manifests, _rusttc, read(PLAN))
_pin_ver = {k: v for k, v in _pin_problems.items()
            if k in ("anchor_version", "solana_version")}
check("13.1 Anchor.toml [toolchain] pins BOTH anchor_version and solana_version "
      "as exact three-part versions, with no comparator and no range (a `=` on "
      "solana_version is what AVM would accept but anchor-cli 1.1.2 refuses — "
      "its build parses that key as a plain version and dies before compiling; "
      "a floor like 3.1 or ^3.1.10 drifts, and is caught here too)",
      not _pin_ver, _pin_ver)
check("13.2 rust-toolchain.toml pins an exact Rust channel instead of a floating "
      "one (rustup applies it to every cargo/rustc run beneath this directory, "
      "so \"stable\" means two clones, two compilers)",
      "rust_channel" not in _pin_problems,
      {k: v for k, v in _pin_problems.items() if k == "rust_channel"})
_crate_pins = {k: v for k, v in _pin_problems.items()
               if k.startswith("anchor-lang:")}
check("13.3 every crate — the three programs AND the off-chain conversion "
      "service — pins anchor-lang to the SAME exact version as the CLI in "
      "[toolchain] (a CLI/crate mismatch is what Anchor itself warns about; "
      "this is the half-applied upgrade)",
      not _crate_pins, _crate_pins)
_doc_pins = {k: v for k, v in _pin_problems.items()
             if k.startswith("doc:") or k == "floating_channel_in_doc"}
check("13.4 the deployment plan states all three pinned versions and recommends "
      "no floating channel (a plan still installing `release.anza.xyz/stable` "
      "hands a fresh clone a different toolchain than the manifest pins — and "
      "the plan is where the install path lives)",
      not _doc_pins, _doc_pins)

# RED-FIRST: four mutations, each one the way this pin actually rots.
_mutations = [
    ("anchor_version bumped in the manifest only",
     {"toolchain": dict(cfg.get("toolchain") or {}, anchor_version="1.2.1")},
     _rusttc, read(PLAN), "anchor-lang:"),
    ("solana_version written as a floating major.minor floor",
     {"toolchain": dict(cfg.get("toolchain") or {}, solana_version="3.1")},
     _rusttc, read(PLAN), "solana_version"),
    ("rust-toolchain.toml on the floating channel",
     cfg, {"toolchain": {"channel": "stable", "profile": "minimal"}},
     read(PLAN), "rust_channel"),
    ("the plan still pointing at the moving stable channel",
     cfg, _rusttc,
     read(PLAN) + "\nsh -c \"$(curl -sSfL https://release.anza.xyz/"
     "stable/install)\"\n", "floating_channel_in_doc"),
]
def _pin_problem_named(problems, want):
    """Membership for the fixture below: 'anchor-lang:' must match the
    per-crate keys, so this is a prefix test, not a dict-key lookup."""
    return any(k == want or k.startswith(want) for k in problems)


_missed = [(why, want) for why, manifest, rusttc, plan, want in _mutations
           if not _pin_problem_named(
               _toolchain_pin_problems(manifest, _manifests, rusttc, plan),
               want)]
check("13.5 RED-FIRST on the pins: each of the four mutations this section exists "
      "to catch — a manifest-only anchor bump, a Solana version written as a "
      "floating floor, a floating Rust channel, a plan that still installs "
      "the `stable` channel — trips the SAME predicate the real artifacts just "
      "passed through",
      not _missed, _missed)

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed"
      + (f", {skipped} skipped" if skipped else ""))
sys.exit(1 if failed else 0)
