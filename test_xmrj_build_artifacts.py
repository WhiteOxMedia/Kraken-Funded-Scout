"""test_xmrj_build_artifacts.py — THE BUILD-ARTIFACT DRIVE (Oct 27).

WHY THIS EXISTS: every other Solana-side check in this repo is about SOURCE
(the wiring drive reads Anchor.toml, the crates and the plan; the e2e driver's
offline half reads the TS suite in reverse). None of them can say whether a
build has ever run, whether the .so a deploy would upload is the one the
sources describe, or whether the wallet in the runbook can afford the rent.
This drive answers those three questions from the ARTIFACTS, and it fails
loudly when they are absent — a green fleet on a checkout that has never been
compiled is exactly the failure mode it exists to prevent.

WHAT IT CHECKS
  1. the three .so files and the three IDL files exist under target/, and each
     .so is a REAL program (sBPF ELF, non-zero entrypoint, non-empty .text) —
     not the entrypoint-less build of the same crate, which `anchor build` can
     leave on the deploy path (check 1.3)
  2. each artifact is NEWER than the newest source file of its crate (a stale
     .so next to edited sources is a deploy of code nobody reviewed)
  3. the three program ids agree across FOUR places: declare_id!, the
     [programs.<cluster>] tables, target/deploy/<name>-keypair.json, and the
     IDL's metadata address
  4. the IDL instruction surface is the one the stress harness and the devnet
     suite drive — instruction names, argument names in ORDER, and account
     names, per program (a rename in Rust that no one propagated is caught
     here instead of at run time on the cluster)
  5. the rent budget for a deploy computed from the REAL .so sizes, against
     the runbook's §2 requirement — the number the wallet must hold

READ-ONLY: it builds nothing, deploys nothing, signs nothing. It needs no
toolchain, only target/ and the sources.

IN CI (Oct 27) it runs against artifacts CI BUILT, with PLACEHOLDER program
keypairs and `anchor build --ignore-keys`: an address is a public key, so a
keypair file Anchor accepts for the ids this repo declares needs that address's
PRIVATE half, which no checkout has. With XMRJ_PLACEHOLDER_KEYS=1 check 3.3
reports SKIP with that reason instead of a pass. Check 3.4 still RUNS there, and
that is measured rather than assumed: Anchor stamps the IDL's address from
`declare_id!`, not from the keypair, so a placeholder build produces
`RESULT: 10 passed, 0 failed, 1 skipped` — one skip, not two. Everything else a
build can prove (1.3's "is this .so a real program", the freshness checks, the
IDL surface the harness drives, the rent) is enforced on every push. The keypair
IDENTITY stays host-side (test_anchor_wiring.py 4.3), where the real keypairs
exist; a keypair that mismatches WITHOUT the flag is still a failure.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "target"
DEPLOY = TARGET / "deploy"
IDL = TARGET / "idl"

PROGRAMS = ("xmrj_treasury", "xmrj_jackpot", "xmrj_governance")

# RENT, measured from the pinned CLI rather than assumed. solana-cli 3.1.10
# (Oct 27) prints, for an account of N bytes:
#     N=1000    -> 0.00573024 SOL      = 5_730_240 lamports
#     N=100000  -> 0.50865024 SOL      = 508_650_240 lamports
#     N=220792  -> 1.1222736  SOL      = 1_122_273_600 lamports
# which fits exactly `(N + 128) * 5080` — the 128-byte account overhead times
# the current rent rate (5080 lamports/byte, i.e. the 2-year deposit at the
# reduced per-byte-year rate; the older 6960/byte figure overestimates by 35%,
# which is the difference between budgeting 4.1 SOL and 3.0 SOL for a deploy).
ACCOUNT_OVERHEAD_BYTES = 128
LAMPORTS_PER_BYTE_RENT_EXEMPT = 5080


def rent_exempt_lamports(size: int) -> int:
    """What `solana rent <size>` prints, without needing the CLI or a cluster."""
    return (size + ACCOUNT_OVERHEAD_BYTES) * LAMPORTS_PER_BYTE_RENT_EXEMPT

# The interface the harness (tools/xmrj_loadtest.ts) and the devnet suite
# drive. Instruction -> (arg names in order, account names in order).
EXPECTED_INTERFACE = {
    "xmrj_treasury": {
        "initialize": (
            ["reserve_bps", "jackpot_bps", "exchange_rate_bps", "operational_authority"],
            ["treasury_state", "admin_authority", "program_data", "payer", "system_program"],
        ),
        "record_inflow_xmrj": (["amount"], ["treasury_state", "operational_authority"]),
        "fund_jackpot_with_xmr": (
            ["amount_xmr"],
            ["treasury_state", "operational_authority", "jackpot_state", "jackpot_funder", "jackpot_program"],
        ),
        "set_split_ratio": (["reserve_bps", "jackpot_bps"], ["treasury_state", "admin_authority"]),
        "set_exchange_rate": (["exchange_rate_bps"], ["treasury_state", "admin_authority"]),
        "set_operational_authority": (["new_authority"], ["treasury_state", "admin_authority"]),
    },
    "xmrj_jackpot": {
        "initialize": ([], ["jackpot_state", "admin_authority", "program_data", "payer", "system_program"]),
        "fund_jackpot": (["amount_xmr"], ["jackpot_state", "funder"]),
        "trigger_payout": (["winner", "amount_xmr"], ["jackpot_state", "admin_authority"]),
        "set_payout_rules": (["max_payout_bps"], ["jackpot_state", "admin_authority"]),
        "get_jackpot_state": ([], ["jackpot_state"]),
    },
    "xmrj_governance": {
        "initialize_governance": (
            ["owners", "threshold"],
            ["governance_state", "governance_authority", "program_data", "payer", "system_program"],
        ),
        # `proposal_id` FIRST, ahead of the action: every proposal carries its
        # own id, which is the third seed of its PDA (finding F5). The order is
        # part of the encoding, so it is pinned rather than described.
        "propose_action": (["proposal_id", "action"], ["governance_state", "proposal", "proposer", "system_program"]),
        "approve_action": (["proposal_id"], ["governance_state", "proposal", "owner"]),
        "execute_action": (
            ["proposal_id"],
            [
                "governance_state",
                "proposal",
                "treasury_state",
                "jackpot_state",
                "treasury_program",
                "jackpot_program",
                "governance_authority",
                "target_program",
            ],
        ),
    },
}

results: list[tuple[bool, str, object]] = []
skipped = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def skip(name: str, why: str = "") -> None:
    """A check that CANNOT RUN HERE, said out loud. Not a pass: it is counted and
    printed with its reason, so a run that skipped its identity checks cannot be
    mistaken for one that verified them."""
    global skipped
    skipped += 1
    print("SKIP  " + name + (f"  ({why})" if why else ""))


# base58 — what a Solana address is. Ten lines, no dependency, and it is what
# makes check 3.3 able to FAIL: it compares the keypair's public half, decoded,
# against the id the crate declares, instead of comparing a hex string to nothing.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, remainder = divmod(number, 58)
        out = _B58[remainder] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. the artifacts exist ───────────────────────────────────────────────
_missing = [p for p in PROGRAMS if not (DEPLOY / f"{p}.so").is_file()]
check(
    "1.1 all three .so artifacts exist (target/deploy/<program>.so) — a deploy "
    "uploads these, and the devnet suite reads their sizes to size the rent",
    not _missing,
    _missing,
)
_missing_idl = [p for p in PROGRAMS if not (IDL / f"{p}.json").is_file()]
check(
    "1.2 all three IDLs exist (target/idl/<program>.json) — the client cannot "
    "call an instruction that has no IDL entry",
    not _missing_idl,
    _missing_idl,
)
if _missing or _missing_idl:
    print("RESULT: cannot continue without artifacts — run `anchor build` first")
    sys.exit(1)

# ── 1.3 every .so is a PROGRAM, not an entrypoint-less shell ─────────────
# WHY THIS IS HERE AND NOT IN A BUILD STEP: `anchor build` compiles each
# program TWICE in this workspace — once as the target, and once as the
# library the sibling that CPIs into it links, where the `cpi` feature implies
# `no-entrypoint`. Both write the SAME
# target/sbpf-solana-solana/release/<program>.so, and that is the path
# `anchor build` copies into target/deploy. When the entrypoint-less build is
# the one that lands there, the deploy artifact is a VALID ELF with e_entry = 0
# and 8 bytes of .text: an EMPTY program. The loader refuses it ("Entrypoint
# out of bounds"), so it can never overwrite a working program — but it costs a
# deploy cycle, and it is invisible to everything else in this repo, including
# this drive before check 1.3 existed. xmrj_treasury hit it on Oct 27 — 896
# bytes on the deploy path, while the other two programs upgraded normally in
# the same step. The compile is a single package built from its own directory,
# where nothing can turn `no-entrypoint` on; `tools/xmrj_devnet_run.sh` §3 does
# exactly that before it deploys, and this check is what proves it worked.
SBF_ELF_MAGIC = b"\x7fELF"
EM_SBPF = 263  # what cargo-build-sbf emits for the sbf-solana-solana target
TEXT_FLOOR_BYTES = 4096  # a real Anchor program's .text is >100 KiB


def elf_facts(path: Path) -> dict:
    """e_machine, e_entry and .text size of an SBF artifact — no toolchain."""
    blob = path.read_bytes()
    if blob[:4] != SBF_ELF_MAGIC:
        return {"bytes": len(blob), "reason": "not an ELF file"}
    e_machine, = struct.unpack_from("<H", blob, 18)
    e_entry, = struct.unpack_from("<Q", blob, 24)
    e_shoff, = struct.unpack_from("<Q", blob, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", blob, 58)
    sections = [
        struct.unpack_from("<IIQQQQ", blob, e_shoff + i * e_shentsize)
        for i in range(e_shnum)
    ]
    _, _, _, _, stroff, strsize = sections[e_shstrndx]
    names = blob[stroff:stroff + strsize]

    def section_name(offset: int) -> str:
        return names[offset:names.index(b"\0", offset)].decode()

    text = next((s[5] for s in sections if section_name(s[0]) == ".text"), 0)
    return {"bytes": len(blob), "e_machine": e_machine, "e_entry": e_entry, "text": text}


_stubs = {}
for program in PROGRAMS:
    facts = elf_facts(DEPLOY / f"{program}.so")
    if "reason" in facts:
        rendered = facts["reason"]
        _stubs[program] = facts
    else:
        rendered = (f"e_machine={facts['e_machine']}, e_entry=0x{facts['e_entry']:x}, "
                    f".text={facts['text']} bytes")
        if (facts["e_machine"] != EM_SBPF or facts["e_entry"] == 0
                or facts["text"] < TEXT_FLOOR_BYTES):
            _stubs[program] = facts
    print(f"       {program}.so: {facts['bytes']} bytes, {rendered}")
check(
    "1.3 every deploy artifact is a REAL program — sBPF ELF, non-zero entrypoint, "
    f".text at or above {TEXT_FLOOR_BYTES} bytes (an entrypoint-less build is an "
    "EMPTY program the loader refuses with 'Entrypoint out of bounds')",
    not _stubs,
    _stubs,
)

# ── 2. the artifacts are FRESHER than the sources ────────────────────────
_stale = []
for program in PROGRAMS:
    newest_src = max(
        [p.stat().st_mtime for p in (HERE / program / "src").rglob("*.rs")]
        + [(HERE / program / "Cargo.toml").stat().st_mtime]
    )
    so_mtime = (DEPLOY / f"{program}.so").stat().st_mtime
    if so_mtime < newest_src:
        _stale.append(program)
check(
    "2.1 every .so is newer than its crate's newest source file — a stale "
    "artifact next to edited sources is a deploy carrying code nobody built",
    not _stale,
    _stale,
)

# ── 3. the program ids agree in four places ──────────────────────────────
_cfg = read(HERE / "Anchor.toml")
declared = {}
for program in PROGRAMS:
    m = re.search(rf'declare_id!\("([1-9A-HJ-NP-Za-km-z]{{32,44}})"\)', read(HERE / program / "src" / "lib.rs"))
    declared[program] = m.group(1) if m else None

for cluster in ("devnet", "localnet"):
    table = re.search(rf"\[programs\.{cluster}\]\n((?:\w+ = \"[^\"]+\"\n?)+)", _cfg)
    entries = dict(re.findall(r'(\w+) = "([^"]+)"', table.group(1))) if table else {}
    mismatched = {p: (declared[p], entries.get(p)) for p in PROGRAMS if entries.get(p) != declared[p]}
    check(
        f"3.{1 if cluster == 'devnet' else 2} [programs.{cluster}] agrees with every crate's "
        f"declare_id! — the CLI resolves deploys from the manifest, the runtime from the id "
        f"compiled into the program",
        not mismatched,
        mismatched,
    )

_keypair_pubkey: dict[str, str | None] = {}
for program in PROGRAMS:
    kp_path = DEPLOY / f"{program}-keypair.json"
    if not kp_path.is_file():
        _keypair_pubkey[program] = None
        continue
    secret = json.loads(read(kp_path))
    # A 64-byte ed25519 keypair: the address is the LAST 32 bytes, base58.
    _keypair_pubkey[program] = (
        b58encode(bytes(secret[32:])) if isinstance(secret, list) and len(secret) == 64
        else "malformed"
    )
_keypair_mismatch = {p: (declared[p], got) for p, got in _keypair_pubkey.items()
                     if got != declared[p]}

# THE ONE CASE WHERE "mismatch" IS NOT A DEFECT: a CI checkout has no keypairs at
# all (target/ is gitignored — they hold the PRIVATE half of each address), so the
# build job generates placeholders and passes `--ignore-keys`. The keypair then
# holds a fresh address, and the only honest report is SKIP — not a
# pass (nothing was checked) and not a fail (the tree is fine). A mismatch WITHOUT
# this flag is exactly the regression 3.3 exists for: a regenerated keypair whose
# declare_id! nobody synced, which would deploy under an address the state PDAs
# are not derived from. The flag is only consulted when the keys do NOT match, so
# a host that has the real keypairs runs the check whatever CI sets.
if _keypair_mismatch and os.environ.get("XMRJ_PLACEHOLDER_KEYS") == "1":
    skip(
        "3.3 every target/deploy/<program>-keypair.json still holds the key its crate "
        "declares (a regenerated keypair with an unsynced declare_id! would deploy to "
        "a different address than the one the state PDAs are derived under)",
        "this build used PLACEHOLDER keypairs (XMRJ_PLACEHOLDER_KEYS=1): the private "
        "halves are not in CI, and an address cannot be synthesized without its "
        "private half — the identity check is host-side (test_anchor_wiring.py 4.3)",
    )
else:
    check(
        "3.3 every target/deploy/<program>-keypair.json still holds the key its crate "
        "declares (a regenerated keypair with an unsynced declare_id! would deploy to "
        "a different address than the one the state PDAs are derived under)",
        not _keypair_mismatch,
        _keypair_mismatch,
    )

# 3.4 is NOT skipped in CI, and that is a measurement, not a hope: a build with
# placeholder keypairs and `--ignore-keys` still stamps the IDL's address from
# `declare_id!`, so the file carries the DECLARED id and this check holds exactly
# as it does on a host with the real keypairs (verified: a placeholder build in a
# copy of the tree reported 10 passed, 0 failed, 1 skipped — 3.3 only). Leaving a
# skip branch here would be a branch that can only ever turn a real mismatch into
# a pass, so there is none.
_idl_mismatch = {}
for program in PROGRAMS:
    idl = json.loads(read(IDL / f"{program}.json"))
    addr = ((idl.get("metadata") or {}).get("address")) or idl.get("address")
    if addr and addr != declared[program]:
        _idl_mismatch[program] = (declared[program], addr)
check(
    "3.4 each IDL's metadata address equals the crate's declare_id! — an IDL built "
    "for a different id sends every client call to an address with no program",
    not _idl_mismatch,
    _idl_mismatch,
)

# ── 4. the IDL surface is the one the harness drives ─────────────────────
_surface = {}
for program in PROGRAMS:
    idl = json.loads(read(IDL / f"{program}.json"))
    actual = {}
    for ix in idl.get("instructions", []):
        actual[ix["name"]] = (
            [a["name"] for a in ix.get("args", [])],
            [a["name"] for a in ix.get("accounts", [])],
        )
    expected = EXPECTED_INTERFACE[program]
    diffs = {
        name: {"expected": expected.get(name), "actual": actual.get(name)}
        for name in set(expected) | set(actual)
        if expected.get(name) != actual.get(name)
    }
    if diffs:
        _surface[program] = diffs
check(
    "4.1 every program's instruction surface matches the one the stress harness and "
    "the devnet suite drive — instruction names, ARGUMENT ORDER and account order "
    "(Borsh encodes positionally: a reordered argument is a different instruction, "
    "not a compile error so loud)",
    not _surface,
    _surface,
)

_harness = HERE / "tools" / "xmrj_loadtest.ts"
_harness_missing = []
if _harness.is_file():
    text = read(_harness)
    for program, ixs in EXPECTED_INTERFACE.items():
        for ix in ixs:
            camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), ix)
            if ix not in text and camel not in text:
                _harness_missing.append(f"{program}:{ix}")
else:
    _harness_missing.append("tools/xmrj_loadtest.ts missing")
check(
    "4.2 the stress harness names every instruction this drive pins — the harness is "
    "checked against the BUILT interface, so a Rust rename cannot leave it silently "
    "driving an instruction that no longer exists",
    not _harness_missing,
    _harness_missing,
)

# ── 5. the rent budget from the real sizes ───────────────────────────────
_lines = []
_total = 0
for program in sorted(PROGRAMS):
    size = (DEPLOY / f"{program}.so").stat().st_size
    rent = rent_exempt_lamports(size)
    _total += rent
    _lines.append(f"{program}: {size} bytes -> {rent / 1e9:.6f} SOL")
mint_rent = rent_exempt_lamports(82)  # the 82-byte SPL mint
_state_rent = rent_exempt_lamports(8 + 8 * 5 + 32 * 3)  # one program's state
check(
    "5.1 the deploy budget is computed from the REAL artifact sizes (not a guess) "
    "and is reported for the runbook's §2 top-up",
    True,
    "\n      ".join(_lines + [f"total programs: {_total / 1e9:.6f} SOL",
                              f"+ mint {mint_rent / 1e9:.6f} SOL",
                              f"+ state accounts ~{_state_rent / 1e9:.6f} SOL"]),
)
print("       " + "\n       ".join(_lines))
print(f"       total: {(_total + mint_rent + _state_rent) / 1e9:.4f} SOL "
      f"(programs {_total / 1e9:.4f} + mint + two state accounts)")

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed"
      + (f", {skipped} skipped" if skipped else ""))
sys.exit(1 if failed else 0)
