"""diag_asciiclean.py — THE MOJIBAKE SWEEP (Sep 16).

The scanner/bot/VE console windows render UTF-8 prose (em-dashes,
arrows, emojis) as cp437 garbage: '≡ƒÜÇ', 'ΓÇö', 'ΓëÑ'. The fix that
matches the ask: every window-facing STRING LITERAL becomes pure ASCII
(mapped: — -> -, ≥ -> >=, × -> x; unknown emoji stripped with its
trailing space). Docstrings/comments outside strings are untouched by
the tokenizer; app.py is NOT swept (the browser renders UTF-8 fine;
its emojis are intentional). Tests are swept only by hand after the
battery names broken pins.

Run:  .venv\\Scripts\\python.exe -X utf8 diag_asciiclean.py
Writes originals to %TEMP%/ascii_backup_<stamp>/ before touching files.
"""
import ast
import os
import shutil
import sys
import tempfile
import time
import tokenize
import io
from pathlib import Path

ROOTS = [Path(r"C:\Users\birds\Workspace\kraken_scout"),
         Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")]

CHAR_MAP = {
    "\u2014": "-",   # em dash
    "\u2013": "-",   # en dash
    "\u2192": "->",  # right arrow
    "\u2190": "<-",  # left arrow
    "\u2265": ">=",  # >=
    "\u2264": "<=",  # <=
    "\u00d7": "x",   # multiplication
    "\u00b7": ".",   # middle dot
    "\u2026": "...", # ellipsis
    "\u2019": "'",   # right single quote
    "\u2018": "'",   # left single quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2261": "=",   # identical to (the ≡ of ≡ƒÜÇ)
    "\u00a0": " ",   # nbsp
}

# emojis/symbols with NO faithful ASCII: dropped, plus one following space
DROP_WITH_SPACE = True


def map_text(s: str) -> str:
    out = []
    for ch in s:
        if ch in CHAR_MAP:
            out.append(CHAR_MAP[ch])
        elif ord(ch) < 128:
            out.append(ch)
        else:
            if DROP_WITH_SPACE and out and out[-1] == " ":
                pass          # collapse 'emoji  word' -> ' word'
            else:
                out.append(" ")
    txt = "".join(out)
    while "  " in txt:        # emoji removal leaves doubles at line starts
        txt = txt.replace("  ", " ")
    return txt


def sweep_file(path: Path, backup_dir: Path) -> tuple[int, str | None]:
    src = path.read_text(encoding="utf-8")
    if all(ord(c) < 128 for c in src):
        return 0, None
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    edits = []                # (row, col_start, col_end, new_text)
    n = 0
    for t in toks:
        if t.type != tokenize.STRING:
            continue
        raw = t.string
        if all(ord(c) < 128 for c in raw):
            continue
        new_raw = map_text(raw)
        if new_raw == raw:
            continue
        # sanity: non-f-string literals must still eval to str
        if not raw.lstrip().lower().startswith(("f", "rb", "br")):
            try:
                v = ast.literal_eval(new_raw)
                if not isinstance(v, str):
                    continue
            except Exception:
                continue
        edits.append((t.start[0], t.start[1], t.end[0], t.end[1], new_raw))
        n += 1
    if not edits:
        return 0, None
    # splice by ABSOLUTE offsets: a triple-quoted token spans lines, so
    # its END offset must use its OWN end row — using the start row for
    # both is the tail-duplication bug the first cut had.
    line_offsets = [0]
    for line in src.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    abs_edits = sorted(
        ((line_offsets[r1 - 1] + c1, line_offsets[r2 - 1] + c2, new_raw)
         for r1, c1, r2, c2, new_raw in edits),
        key=lambda e: -e[1])
    new_src = src
    for a1, a2, new_raw in abs_edits:
        new_src = new_src[:a1] + new_raw + new_src[a2:]
    shutil.copy2(path, backup_dir / path.name)
    path.write_text(new_src, encoding="utf-8")
    import py_compile
    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as exc:
        shutil.copy2(backup_dir / path.name, path)   # restore, abort loudly
        return -1, f"COMPILE FAILED, restored: {exc}"
    return n, None


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(tempfile.gettempdir()) / f"ascii_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    bad = []
    for root in ROOTS:
        for f in sorted(root.glob("*.py")):
            if f.name.startswith(("test_", "diag_")):
                continue
            try:
                n, err = sweep_file(f, backup_dir)
            except Exception as exc:
                n, err = -1, f"tokenize failed: {exc}"
            if n == -1:
                bad.append((f, err))
            elif n:
                print(f"{n:4d} strings  {f}")
                total += n
    print(f"\nTOTAL strings mapped: {total}")
    print(f"backups: {backup_dir}")
    if bad:
        for f, err in bad:
            print(f"FAILED: {f}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
