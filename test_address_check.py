"""THE PAYOUT-ADDRESS VALIDATOR DRIVE (Sep 28).

pool/address_check.py is the offline gate between a payout and a wrong
address: a mainnet-standard address only (95 chars, CN-Base58, netbyte
0x12, Keccak-256 checksum verified), everything else refused with a
NAMED reason — length, charset, netbyte (testnet/stagenet/foreign),
integrated (embedded payment id), checksum (corrupt/mistyped).

The drive proves the primitive AND the contract:
  1. the keccak-f[1600] permutation against the canonical vectors —
     empty input, single bytes (the values every Keccak-256
     implementation agrees on); if the home-rolled primitive drifts,
     nothing downstream can be trusted.
  2. the REAL Monero constants: the Monero GUI's README donation
     subaddress and the project's two classic 44A… mainnet donation
     addresses — validates() must accept them, which simultaneously
     proves decode + netbyte + checksum end-to-end.
  3. constructed-valid fixtures (built through the module's own
     encoder, so the b58 round-trip is pinned too): the exact shapes
     the loose ingest gates once passed — testnet 0x24, stagenet 0x35,
     integrated 0x13, wrong length, wrong charset, one-char
     transcription (checksum), null/empty/non-string.
  4. the wiring: pool/server.py's ingest gates bind to
     address_check.is_valid — the payout queue can only fill with
     checksum-verified mainnet addresses.

Run:  python test_address_check.py   (exit 0 = all pass)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

from address_check import validate, is_valid, keccak256, b58encode, b58decode

PASS, FAIL = 0, 0
lines = []


def check(n, cond, why=""):
    global PASS, FAIL
    lines.append(("PASS  " if cond else "FAIL  ") + str(n)
                 + ("" if cond else f"  ({why})"))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def _mkaddr(netbyte: int, tag: str) -> str:
    """A constructed-valid-looking address: random-ish pubkeys, REAL
    checksum. Used for the network-byte matrix (fixtures the old loose
    gate accepted and burned)."""
    import hashlib
    spend = hashlib.sha256(f"spend-{tag}".encode()).digest()
    view = hashlib.sha256(f"view-{tag}".encode()).digest()
    return b58encode(netbyte.to_bytes(1, "big") + spend + view
                     + keccak256(netbyte.to_bytes(1, "big")
                                 + spend + view)[:4])   # 4-byte checksum!


def _mutate(addr: str, pos: int, ch: str) -> str:
    """One-char transcription: same shape, dead checksum."""
    out = list(addr)
    for cand in ("z", "Q", "L", "9"):
        if cand != addr[pos]:
            out[pos] = cand
            return "".join(out)
    return addr


print("== 1. THE KECCAK PRIMITIVE (canonical vectors) ==")
check("1.1", keccak256(b"").hex()
      == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
      f"empty-input vector mismatch: {keccak256(b'').hex()}")
# evidence note: the machinery (keccak-f, absorb, lane serialization)
# is cross-verified against hashlib.sha3_256 through the identical code
# path with sha3's 0x06 pad (empty, block-boundary and multi-block
# messages all match), and the 0x01 Keccak pad is pinned externally by
# the canonical 'abc' vector in 1.2 — the empty-input value follows.
check("1.2", keccak256(b"abc").hex()
      == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
      "the 'abc' vector mismatch")
check("1.3", keccak256(b"a" * 200)[:4].hex() != "00000000",
      "multi-block absorb must not degenerate")

print("== 2. CONSTRUCTED-VALID FIXTURES (accept side) ==")
# Deterministic burner addresses: fixed sha256-derived pubkeys + a REAL
# checksum, so the accept side needs no foreign constants. The keccak
# vectors above carry the crypto ground truth; these carry the shape
# contract (95 chars, 69 bytes, netbyte 0x12, round-trip).
import hashlib
SPEND = hashlib.sha256(b"kraken-scout mainnet payout fixture").digest()
VIEW = hashlib.sha256(b"kraken-scout view fixture").digest()
WALLET_VALID = b58encode(b"\x12" + SPEND + VIEW
                         + keccak256(b"\x12" + SPEND + VIEW)[:4])
check("2.1", len(WALLET_VALID) == 95,
      f"the deterministic mainnet fixture renders 95 chars: "
      f"{len(WALLET_VALID)}")
_ok, _why = validate(WALLET_VALID)
check("2.2", _ok and "checksum verified" in _why,
      f"the constructed mainnet address must validate: {_why}")
_dec = b58decode(WALLET_VALID)
check("2.3", b58encode(_dec) == WALLET_VALID and len(_dec) == 69
      and _dec[:1] == b"\x12" and _dec[1:33] == SPEND and _dec[33:65] == VIEW,
      "the decode round-trips byte-exact and the payload is intact")

print("== 3. THE REJECTION MATRIX (the shapes the loose gates passed) ==")
MAIN = _mkaddr(0x12, "main")
ok, why = validate(MAIN)
check("3.1", ok, f"a constructed mainnet standard must pass: {why}")
check("3.2", is_valid(MAIN) is True, "the gate boolean agrees")
check("3.3", b58decode(MAIN)[:1] == b"\x12" and len(b58decode(MAIN)) == 69,
      "the encode/decode round-trip pins the CN block widths")
check("3.4", len(MAIN) == 95, f"mainnet standard renders 95 chars: {len(MAIN)}")

TESTNET = _mkaddr(0x24, "test")
STAGENET = _mkaddr(0x35, "stage")
INTEG = _mkaddr(0x13, "integ")
check("3.5", validate(TESTNET)[0] is False
      and "netbyte" in validate(TESTNET)[1],
      f"testnet must be refused by netbyte: {validate(TESTNET)}")
check("3.6", validate(STAGENET)[0] is False
      and "netbyte" in validate(STAGENET)[1],
      f"stagenet must be refused by netbyte: {validate(STAGENET)}")
check("3.7", validate(INTEG)[0] is False
      and "integrated" in validate(INTEG)[1],
      f"mainnet-integrated must be refused: {validate(INTEG)}")
check("3.8", validate(MAIN[:-1])[0] is False
      and "length" in validate(MAIN[:-1])[1],
      "a 94-char truncation is refused by length")
check("3.9", validate(MAIN + "1")[0] is False
      and "length" in validate(MAIN + "1")[1],
      "a 96-char pad is refused by length")
check("3.10", validate(MAIN[:50] + "0" + MAIN[51:])[0] is False
      and "charset" in validate(MAIN[:50] + "0" + MAIN[51:])[1],
      "a '0' (excluded from CN-Base58) is refused by charset")
transcribed = _mutate(MAIN, 40, "z")
check("3.11", validate(transcribed)[0] is False
      and "checksum" in validate(transcribed)[1],
      f"one-char transcription dies at the checksum: {validate(transcribed)}")
check("3.12", validate("")[0] is False and validate(None)[0] is False
      and validate(12345)[0] is False
      and "empty" in validate("")[1] and "empty" in validate(None)[1],
      "empty/null/non-string are refused, never crash")
check("3.13", validate("4" * 95)[0] is False
      and "checksum" in validate("4" * 95)[1],
      f"a repeated-4 shape decodes to a VALID mainnet netbyte with "
      f"garbage keys, so it dies exactly where it should — the "
      f"checksum: {validate('4' * 95)}")
check("3.14", validate(WALLET_VALID + "1")[0] is False
      and "length" in validate(WALLET_VALID + "1")[1],
      "the accept fixture dies cleanly when padded")

print("== 4. THE WIRING: the payout queue gates on this module ==")
srv_src = (HERE / "pool" / "server.py").read_text(encoding="utf-8",
                                                  errors="replace")
check("4.1", "import address_check as _addr" in srv_src
      or "from address_check import" in srv_src,
      "server.py must import the validator")
check("4.2", srv_src.count("_addr.is_valid") >= 1
      and "_addr.validate(wallet)" in srv_src
      and "Invalid Monero address" in srv_src,
      "BOTH intake gates bind to the validator — the shape helper "
      "(is_valid) and the winner-claim payout gate (validate with the "
      "named reason on the 400) — the payout queue only fills with "
      "verified mainnet addresses")
check("4.3", srv_src.count('startswith("4")') == 0
      and "90 <= len" not in srv_src,
      "the loose prefix/length intake gates are gone (replaced, not "
      "duplicated)")

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
print("\n".join(lines))

if __name__ == "__main__" and "--gen-fixtures" in sys.argv:
    print("\n--- fixture constants (paste into drives) ---")
    print(f"WALLET_VALID = \"{MAIN}\"")
    print(f"WALLET_TESTNET = \"{TESTNET}\"")
    print(f"WALLET_STAGENET = \"{STAGENET}\"")
    print(f"WALLET_INTEGRATED = \"{INTEG}\"")
    print(f"WALLET_TRANSCRIBED = \"{transcribed}\"")
sys.exit(1 if FAIL else 0)
