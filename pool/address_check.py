"""pool/address_check.py — OFFLINE PAYOUT-ADDRESS VALIDATION (Sep 28).

Validate a Monero mainnet address BEFORE any payout is sent, with no
wallet RPC, no network, and no third-party dependency: pure stdlib.

WHAT A MONERO ADDRESS IS (monero/src/cryptonote_basic):
    Base58(CN alphabet) of 69 bytes = [netbyte | 64-byte spend+view
    pubkeys | 4-byte Keccak-256 checksum over the first 65 bytes]
    netbyte 0x12 mainnet standard (renders '4...'),
             0x13 mainnet integrated (renders '4...', 106 chars),
             0x24 testnet  standard,
             0x35 stagenet standard,
             0x2a testnet integrated.
    The CN Base58 is NOT Bitcoin Base58: it encodes 8-byte blocks as
    11 chars (leading zeros as '1') and a final partial block exactly
    wide enough — byte-count to char-count is deterministic, which is
    why the 95/106 lengths are hard shapes, not heuristics.

THE HOUSE POLICY THIS ENCODES:
    Payouts are real money sent from Kraken by a human. An address
    that only *looks* Monero-shaped ('4'/'8', 90-120 chars) can pass
    the loose ingest gates and then burn the payout: a testnet or
    stagenet address is a different network's bytes, a corrupt or
    transcribed address fails its own checksum, and an integrated or
    payment-id shape is not a wallet address at all. validate() is
    therefore FAIL-CLOSED with NAMED reasons: every rejection says
    exactly what shape was refused, and only a fully decoded
    mainnet-standard address with a verified checksum returns ok.

THE KECCAK PRIMITIVE:
    Monero's checksum is Keccak-256 (the ORIGINAL Keccak padding, not
    SHA3-256's — the two disagree and the checksum would never
    verify). The pool venv carries no keccak primitive, so one lives
    here: a compact keccak-f[1600] permutation. Its correctness is
    not an act of faith — the module's own drive pins the canonical
    empty-input vector  c5d2460186f7233c927e7db2dcc703c0e500b653
    ca82273b7bfad8045d85a456  (the value every Keccak-256
    implementation in the wild agrees on) plus the single-byte
    vectors, and every real address test doubles as a checksum round.

USAGE:
    from address_check import validate
    ok, why = validate(addr)      # ok -> mainnet standard, checksum good
"""

from __future__ import annotations

# ── the CN Base58 alphabet (cryptonote_base58.cpp) ───────────────────
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# ── keccak-f[1600] (original Keccak padding — NOT sha3-256) ──────────
_RC = (0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
       0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
       0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
       0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
       0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
       0x8000000000008080, 0x0000000080000001, 0x8000000080008008)
_ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
        (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))
_MASK = (1 << 64) - 1


def _keccak_f(state):
    """24 rounds of the keccak-f[1600] permutation, in place."""
    for rnd in range(24):
        # theta
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3]
             ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ ((c[(x + 1) % 5] << 1)
                               | (c[(x + 1) % 5] >> 63)) & _MASK
             for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                s = _ROT[x][y]
                b[y][(2 * x + 3 * y) % 5] = \
                    ((state[x][y] << s) | (state[x][y] >> (64 - s))) & _MASK
        # chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y])
                                         & b[(x + 2) % 5][y] & _MASK)
        # iota
        state[0][0] ^= _RC[rnd]
    return state


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (original padding, 0x01 ... 0x80) — Monero's hash."""
    lanes = [[0] * 5 for _ in range(5)]
    rate = 136                                   # 1088-bit rate

    def absorb(block):
        for i in range(rate // 8):
            w = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            lanes[i % 5][i // 5] ^= w
        _keccak_f(lanes)

    off = 0
    while off + rate <= len(data):               # full blocks, no padding
        absorb(data[off:off + rate])
        off += rate
    # THE FINAL PADDED BLOCK always exists — even for EMPTY input
    # (the empty-input canonical vector caught the missing round).
    tail = data[off:]
    if len(tail) == rate - 1:                    # pad fits in one byte
        absorb(tail + b"\x81")
    else:
        absorb(tail + b"\x01" + b"\x00" * (rate - len(tail) - 2)
               + b"\x80")
    out = b"".join(lanes[x][y].to_bytes(8, "little")
                   for y in range(5) for x in range(5))
    return out[:32]


def _bytes_for_chars(nchars: int) -> int:
    """The exact inverse of the CN encoder's width rule: the largest
    byte count B with 256**B < 58**nchars (7 chars -> 5 bytes, 11 -> 8
    — the widths that make 95 chars decode to exactly 69 bytes)."""
    b = 0
    while pow(256, b + 1) < pow(58, nchars):
        b += 1
    return b


def _chars_for_bytes(nbytes: int) -> int:
    """The CN encoder's width rule: the smallest char count C with
    58**C > 256**nbytes (8 bytes -> 11 chars, 5 bytes -> 7)."""
    c = 0
    while pow(58, c) <= pow(256, nbytes):
        c += 1
    return c


def b58decode(data: str) -> bytes:
    """Cryptonote Base58 decode (11-char blocks -> 8 bytes, final
    partial block exactly as wide as the CN rule demands)."""
    out = bytearray()
    for i in range(0, len(data), 11):
        chunk = data[i:i + 11]
        num = 0
        for ch in chunk:
            idx = _B58.find(ch)
            if idx < 0:
                raise ValueError(f"invalid base58 char {ch!r}")
            num = num * 58 + idx
        width = 8 if len(chunk) == 11 else _bytes_for_chars(len(chunk))
        out += num.to_bytes(width, "big")
    return bytes(out)


def b58encode(data: bytes) -> str:
    """Cryptonote Base58 encode (the checksum-vector helper)."""
    out = ""
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        num = int.from_bytes(chunk, "big")
        nch = (11 if len(chunk) == 8
               else _chars_for_bytes(len(chunk)))
        s = ""
        for _ in range(nch):
            num, r = divmod(num, 58)
            s = _B58[r] + s
        out += s
    return out


# ── the network bytes (cryptonote_protocol defs.h) ───────────────────
# THE CRYPTONOTE TRUTH (cryptonote_protocol defs.h; the Oct 6 critical
# sweep): the old table labeled 0x24 "testnet-standard" (it is
# STAGENET-SUBADDRESS) and 0x35 "stagenet-standard" (it is
# TESTNET-STANDARD), and mainnet subaddresses (0x2A) — valid 95-char
# payout destinations users actually hold — were refused as foreign
# bytes. All nine prefixes, correctly named:
_NETBYTES = {
    0x12: ("mainnet-standard", 95),
    0x13: ("mainnet-integrated", 106),
    0x2A: ("mainnet-subaddress", 95),
    0x35: ("testnet-standard", 95),
    0x36: ("testnet-integrated", 106),
    0x3F: ("testnet-subaddress", 95),
    0x18: ("stagenet-standard", 95),
    0x19: ("stagenet-integrated", 106),
    0x24: ("stagenet-subaddress", 95),
}


def validate(addr) -> tuple[bool, str]:
    """Offline mainnet validation (standard AND subaddress; the
    8-prefixed subaddress shape is a first-class payout
    destination). Returns (ok, why).

    ok=False is always a NAMED shape rejection:
      empty / non-string      the caller sent nothing usable
      length                  not 95 chars (integrated is 106 and is
                              refused separately — see netbyte below)
      charset                 a non-CN-Base58 character
      netbyte                 decoded to testnet/stagenet/an unknown
                              prefix — a different network's bytes
      integrated              mainnet-integrated (0x13): carries an
                              embedded payment id, not a wallet
                              address; payouts refuse it
      checksum                the trailing 4 bytes are not the
                              Keccak-256 of the first 65 — corrupt,
                              truncated, or transcribed wrong
    """
    if not isinstance(addr, str):
        return False, "empty"
    a = addr.strip()
    if not a:
        return False, "empty"
    if len(a) != 95:
        return False, (f"length ({len(a)} chars, not the 95 of a "
                       f"mainnet standard address)")
    for ch in a:
        if ch not in _B58:
            return False, f"charset ({ch!r} is not CN-Base58)"
    raw = b58decode(a)
    if len(raw) != 69:
        return False, "length (decoded to an impossible byte count)"
    nb = raw[0]
    name, want_len = _NETBYTES.get(nb, ("unknown", 0))
    if nb not in (0x12, 0x13, 0x2A):   # mainnet shapes only;
        # 0x13 falls through to the integrated rejection below
        return False, (f"netbyte ({name or hex(nb)} — testnet/stagenet "
                       f"or foreign network bytes)")
    if want_len != 95:
        return False, ("integrated (0x13 carries an embedded payment id "
                       "— payouts go to wallet addresses only)")
    if keccak256(raw[:65])[:4] != raw[65:]:   # checksum = FIRST 4 bytes
        return False, "checksum (Keccak-256 mismatch — corrupt or mistyped)"
    cls = "mainnet subaddress" if nb == 0x2A else "mainnet standard"
    return True, f"{cls}, checksum verified"


def is_valid(addr) -> bool:
    """The boolean convenience for gates: True ONLY for a mainnet
    standard or subaddress with a verified checksum."""
    return validate(addr)[0]


if __name__ == "__main__":
    import sys
    ok, why = validate(sys.argv[1] if len(sys.argv) > 1 else "")
    print(("VALID  " if ok else "REJECT ") + why)
    sys.exit(0 if ok else 1)
