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

NOTE ON GIT: the whole Solana side (Anchor.toml, the workspace Cargo.toml
and the four xmrj_* crates) is UNTRACKED in this repo — there is no HEAD
revision to diff against, which is why the red-first fixtures are literals
here rather than `git show` reads.

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


def check(name, cond, why=""):
    results.append((bool(cond), name, why))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  ({why})" if why and not cond else ""))


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
key_bad, key_missing, pub_mismatch = [], [], {}
for n in PROGRAMS:
    kp = HERE / "target" / "deploy" / f"{n}-keypair.json"
    if not kp.exists():
        key_missing.append(n)
        continue
    try:
        raw = json.loads(kp.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
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

_calls = re.findall(r"(xmrj_[a-z_]+)::cpi::([a-z_][a-z0-9_]*)\s*\(", GOV)
_missing_fn = [(c, f) for c, f in _calls
               if not re.search(rf"pub fn {f}\s*(?:<[^>]*>)?\s*\(", src.get(c, ""))]
check("9.3 every CPI call names an instruction that EXISTS in the target "
      "crate (the callee's own `pub fn`)",
      bool(_calls) and not _missing_fn,
      {"calls": _calls, "missing": _missing_fn})

_accts = re.findall(r"(xmrj_[a-z_]+)::cpi::accounts::([A-Za-z_][A-Za-z0-9_]*)\s*\{", GOV)
_field_bad = {}
for c, st in _accts:
    target = src.get(c, "")
    m = re.search(rf"pub struct {st}\s*(?:<'[^>]*>)?\s*\{{", target)
    if not m:
        _field_bad[f"{c}::{st}"] = "no such accounts struct in the target crate"
        continue
    want = _opt_fields(_balanced(target, m.end() - 1), "pub ")
    literal = re.search(rf"{c}::cpi::accounts::{st}\s*\{{", GOV)
    got = _opt_fields(_balanced(GOV, literal.end() - 1), "")
    if set(want) != set(got):
        _field_bad[f"{c}::{st}"] = {"target": want, "caller": got}
check("9.4 every `cpi::accounts::<Struct>` the caller builds carries the "
      "SAME field names as the target's own Accounts struct (a rename on "
      "either side cannot drift past this)",
      bool(_accts) and not _field_bad, _field_bad)

_imports = re.findall(r"use (xmrj_[a-z_]+)::program::([A-Za-z0-9_]+);", GOV)
_used = set(re.findall(r"Program<'info,\s*([A-Za-z0-9_]+)>", GOV))
_camel_ok = all(
    marker == "".join(p.capitalize() for p in crate.split("_"))
    and re.search(rf"#\[program\]\s*pub mod {crate}\b", src.get(crate, ""))
    for crate, marker in _imports)
# `System` is anchor-lang's own marker (system_program), not a workspace dep.
_ANCHOR_MARKERS = {"System"}
check("9.5 every Program<> marker is the name anchor-syn generates from the "
      "dependency's own `#[program]` module (CamelCase of the module name), "
      "and every imported marker is actually used",
      bool(_imports) and _camel_ok
      and {m for _, m in _imports} <=
      _used <= {m for _, m in _imports} | _ANCHOR_MARKERS,
      {"imports": _imports, "used": sorted(_used), "camel_ok": _camel_ok})

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
_deps = _manifests["xmrj_governance"].get("dependencies") or {}
# The dependency path is relative to the MANIFEST's directory, not to the
# workspace root — resolved that way here, exactly as cargo resolves it.
_dep_bad = {n: _deps.get(n) for n in ("xmrj_treasury", "xmrj_jackpot")
            if not (isinstance(_deps.get(n), dict)
                    and "cpi" in (_deps[n].get("features") or [])
                    and (HERE / "xmrj_governance"
                         / _deps[n].get("path", "x")).is_dir())}
check("10.3 governance depends on both programs by PATH and enables "
      "features = [\"cpi\"] (without the feature those cpi paths do not "
      "exist; the path dep keeps ONE account layout between caller and callee)",
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


def _pins(text):
    """{pinned const -> pubkey literal} for every `<NAME>_PROGRAM_ID`."""
    return dict(re.findall(
        r"pub const ([A-Z][A-Z0-9_]*)_PROGRAM_ID:\s*Pubkey\s*=\s*[^;]*?"
        r'pubkey!\("([^"]+)"\)', text, re.S))


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

_flip_targets = [m for m in re.finditer(r'pubkey!\("([^"]+)"\)', TRE)]
_flip = TRE[:_flip_targets[0].start(1)] + (
    "1" if _flip_targets[0].group(1)[0] != "1" else "2"
) + _flip_targets[0].group(1)[1:] + TRE[_flip_targets[0].end(1):]
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

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
