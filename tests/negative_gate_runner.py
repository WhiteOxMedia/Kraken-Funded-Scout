"""tests/negative_gate_runner.py — THE DEVNET NEGATIVE PROOF's driver.

Builds a THROWAWAY deployment of the REAL gated program and runs the negative
suite against it:

  1. copy xmrj_jackpot into a temp dir (it has one dependency, anchor-lang,
     so the copy is self-contained), re-declare its id to a freshly generated
     keypair, and build the .so with the pinned Solana CLI's cargo build-sbf;
  2. deploy it to devnet — a program nobody has ever initialized, so its
     `jackpot_state` PDA is genuinely fresh;
  3. compute the NotUpgradeAuthority custom code FROM THE RUST ENUM and run
     tests/xmrj_gate_negative.ts with it in the environment, so the suite
     asserts the real number rather than a literal that would go stale;
  4. close the throwaway program to reclaim its rent.

WHY A THROWAWAY DEPLOYMENT OF THE REAL CRATE, not a second gated program:
the gate is the thing under test. A stub would prove the stub works. Copying
the crate means the bytes on devnet are the bytes the workspace ships, and the
only difference is the program address (which is what makes the state fresh).

WHAT IT COSTS: the deploy's rent (returned when the program is closed) and the
fees. The positive control creates the 128-byte state account; there is no
close instruction, so that account's rent (~0.001 SOL) stays locked after the
program is closed. Stated here rather than discovered.

SKIP CONTRACT (the fleet convention): with XMRJ_GATE_NEGATIVE!=1, or without
`solana`/`solana-keygen`/`cargo` on PATH, a wallet, a devnet RPC and ~3 SOL,
this prints `SKIP: <reason>` and exits 0. It never spends SOL by surprise.

Run:
  XMRJ_GATE_NEGATIVE=1 ANCHOR_WALLET=~/.config/solana/id.json \
    python tests/negative_gate_runner.py
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GATED_CRATE = ROOT / "xmrj_jackpot"
SUITE = HERE / "xmrj_gate_negative.ts"
DEFAULT_URL = "https://api.devnet.solana.com"
MIN_LAMPORTS = 3_000_000_000  # rent for the program + fees + the state


def log(msg):
    print(msg, flush=True)


def skip(reason):
    log("SKIP: " + reason)
    return 0


def which(tool):
    return shutil.which(tool)


def wallet_path():
    raw = os.environ.get("ANCHOR_WALLET") or "~/.config/solana/id.json"
    return Path(os.path.expanduser(raw))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def error_code(source_text, variant="NotUpgradeAuthority"):
    """Anchor numbers its errors 6000 + the variant's index. Reading the index
    from the enum is why the suite cannot stale: reorder the enum and the
    runner hands it the new number."""
    body = re.search(r"#\[error_code\]\s*pub enum ErrorCode\s*\{(.*?)\n\}",
                     source_text, re.S)
    if not body:
        raise SystemExit("cannot find #[error_code] enum ErrorCode")
    variants = re.findall(r"^    ([A-Z][A-Za-z0-9_]*)", body.group(1), re.M)
    if variant not in variants:
        raise SystemExit("{} is not an ErrorCode variant".format(variant))
    return 6000 + variants.index(variant)


def copy_and_reid(program_id):
    """A self-contained copy of the crate with declare_id! rewritten."""
    tmp = Path(tempfile.mkdtemp(prefix="xmrj_gate_negative_"))
    crate = tmp / "xmrj_jackpot"
    shutil.copytree(GATED_CRATE, crate,
                    ignore=shutil.ignore_patterns("target", "__pycache__"))
    manifest = crate / "Cargo.toml"
    text = manifest.read_text(encoding="utf-8")
    if "[workspace]" not in text:
        # Detach from any workspace the temp dir might sit inside; the copy
        # must resolve anchor-lang on its own.
        manifest.write_text(text.rstrip() + "\n\n[workspace]\n", encoding="utf-8")
    lib = crate / "src" / "lib.rs"
    source = lib.read_text(encoding="utf-8")
    rewritten, count = re.subn(r'declare_id!\("[^"]+"\)',
                               'declare_id!("{}")'.format(program_id), source)
    if count != 1:
        raise SystemExit("expected exactly one declare_id!, found {}".format(count))
    lib.write_text(rewritten, encoding="utf-8")
    return tmp, crate


def deploy(url, wallet, program_keypair, so_path):
    return run(["solana", "program", "deploy", "--url", url,
                "--keypair", str(wallet),
                "--program-id", str(program_keypair), str(so_path)])


def close_program(url, wallet, program_id):
    """Best-effort rent recovery. A cleanup failure must not turn a proven
    gate into a failed run."""
    return run(["solana", "program", "close", "--url", url,
                "--keypair", str(wallet), "--bypass-warning", program_id])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("SOLANA_URL", DEFAULT_URL))
    ap.add_argument("--keep", action="store_true",
                    help="leave the throwaway program deployed for debugging")
    args = ap.parse_args(argv)

    if os.environ.get("XMRJ_GATE_NEGATIVE") != "1":
        return skip("XMRJ_GATE_NEGATIVE is not 1 — this deploys to devnet and "
                    "spends SOL, so it is opt-in")

    missing = [t for t in ("solana", "solana-keygen", "cargo") if not which(t)]
    if missing:
        return skip("not on PATH: " + ", ".join(missing))
    if not SUITE.exists():
        return skip("missing " + str(SUITE))

    wallet = wallet_path()
    if not wallet.is_file():
        return skip("no wallet at {} (set ANCHOR_WALLET)".format(wallet))

    source = (GATED_CRATE / "src" / "lib.rs").read_text(encoding="utf-8")
    code = error_code(source)

    bal = run(["solana", "balance", "--url", args.url,
               "--keypair", str(wallet), "--lamports"])
    if bal.returncode != 0:
        return skip("cannot read the wallet balance: " + bal.stderr.strip()[:200])
    try:
        lamports = int(bal.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return skip("unreadable balance: " + bal.stdout.strip()[:120])
    if lamports < MIN_LAMPORTS:
        return skip("wallet holds {} lamports, needs about {} for rent + fees "
                    "on devnet (solana airdrop 2)".format(lamports, MIN_LAMPORTS))

    tmp = None
    crate_tmp = None
    program_id = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="xmrj_gate_keys_"))
        keypair = tmp / "program-keypair.json"
        gen = run(["solana-keygen", "new", "--no-bip39-passphrase", "--silent",
                   "--force", "-o", str(keypair)])
        if gen.returncode != 0:
            raise SystemExit("solana-keygen failed: " + gen.stderr.strip()[:400])
        program_id = run(["solana-keygen", "pubkey", str(keypair)]).stdout.strip()
        if len(program_id) < 32:
            raise SystemExit("solana-keygen pubkey returned nothing usable")

        log("throwaway program id: " + program_id)
        crate_tmp, crate = copy_and_reid(program_id)
        log("building (cargo build-sbf) …")
        build = run(["cargo", "build-sbf", "--manifest-path",
                     str(crate / "Cargo.toml")])
        if build.returncode != 0:
            sys.stderr.write((build.stderr or build.stdout)[-4000:])
            raise SystemExit("build failed")

        so = crate / "target" / "deploy" / "xmrj_jackpot.so"
        if not so.is_file():
            raise SystemExit("build produced no .so at " + str(so))

        log("deploying to " + args.url + " …")
        dep = deploy(args.url, wallet, keypair, so)
        if dep.returncode != 0:
            sys.stderr.write((dep.stderr or dep.stdout)[-4000:])
            raise SystemExit("deploy failed")

        env = dict(os.environ)
        env.update({
            "XMRJ_GATE_PROGRAM_ID": program_id,
            "XMRJ_GATE_ERROR": str(code),
            "ANCHOR_PROVIDER_URL": args.url,
            "ANCHOR_WALLET": str(wallet),
        })
        log("running the negative suite (expected custom code {}) …".format(code))
        suite = subprocess.run(
            ["npx", "ts-mocha", "-p", "./tsconfig.json", "-t", "1000000",
             "tests/xmrj_gate_negative.ts"],
            cwd=str(ROOT), env=env, text=True, capture_output=True)
        out = (suite.stdout or "") + (suite.stderr or "")
        log(out)
        # A suite that SKIPPED (no XMRJ_GATE_PROGRAM_ID) would exit 0 with zero
        # tests, so "exit 0" alone is not a pass: the five assertions must be
        # the ones that ran.
        if suite.returncode != 0 or not re.search(r"\b5 passing\b", out):
            raise SystemExit("the negative suite did not run its five checks "
                             "(exit {})".format(suite.returncode))
        log("PASS: a non-authority signer was rejected and the authority "
            "initialized")
        return 0
    finally:
        if program_id and not args.keep:
            log("closing the throwaway program (rent returns to the wallet) …")
            done = close_program(args.url, wallet, program_id)
            if done.returncode != 0:
                log("WARNING: program close failed; close it by hand: "
                    + (done.stderr or done.stdout).strip()[:300])
            else:
                log("note: the 128-byte state account has no close instruction; "
                    "its rent (~0.001 SOL) stays locked")
        for _d in (tmp, crate_tmp):
            if _d:
                shutil.rmtree(_d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
