"""test_hookparser.py — the pre-commit finding parser's contract, pinned.

GUARD (Sep 23): the hook's CodeRabbit parser once looked for the OLD key
names ("file"/"line") in a sed one-liner, matched nothing against the
real CLI stream (fileName/startLine/severity/title/comment), and printed
?:? placeholders for every finding. The fix made the NEW generation the
PRIMARY keys with the old names kept only as fallbacks. This drive fails
if the parser ever drops back: precedence is proven BEHAVIORALLY — a
record carrying BOTH generations must resolve through the NEW keys —
plus ordering censuses, the never-silent raw-fallback, and the wiring.
Sep 23 (second guard): the null-field fallbacks are pinned the same way
— title→comment→type→commentCategory→indicatorTypes and
startLine→line→lineRange.start→endLine, each proven alive through the
real parser, because the live stream leaves those fields null and a
dropped fallback renders real findings as bare ?.
Sep 28 (third guard): the 0.8.1 stream shapes are pinned — the errorType
record (mixed rides ERRORTYPE; PURE is COUNT 0 + ERRORTYPE, never the
CLEANJSON masquerade, which this guard caught while pinning), the
skeletal record (honest store message, never fabricated text), and the
codegenInstructions extraction (line + comment text from the
"Review comment at @file at line N:" string, beating the skeletal
message on hybrid records). The CR_SNAPSHOT store-join identity is
pinned in source.

Run:  python test_hookparser.py   (exit 0 = all pass)
Reads hooks/pre-commit RELATIVE to the cwd: in the repo that is the
worktree copy; under the commit gate it is the STAGED copy exported to
the scratch dir.
"""
import sys
import json
import subprocess
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


src = Path("hooks/pre-commit").read_text(encoding="utf-8")

# ── 1. the parser exists as the python heredoc and is what runs ──────
_a = src.find("PYCODE=\"$(cat <<'CRPYEOF'")
_b = src.find("\nCRPYEOF", _a)
check("the parser heredoc exists", 0 < _a < _b, (_a, _b))
PYCODE = src[_a:_b].split("<<'CRPYEOF'", 1)[1].lstrip("\n")
check("the hook runs the heredoc through python -c",
      '"$PY" -c "$PYCODE"' in src)

# ── 2. ordering censuses: NEW keys lead, OLD names are fallbacks ─────
_i_fn = PYCODE.find('o.get("fileName")')
_i_f = PYCODE.find('o.get("file")')
_i_sl = PYCODE.find('o.get("startLine")')
_i_l = PYCODE.find('o.get("line")')
check("fileName is the primary file key", 0 < _i_fn < _i_f, (_i_fn, _i_f))
check("startLine is the primary line key", 0 < _i_sl < _i_l, (_i_sl, _i_l))
check("the old sed parser is gone (no file/line sed extraction)",
      'sed -n \'s/.*"file' not in src and 'sed -n "s/.*\\"file' not in src)

# ── 3. behavioral: the REAL parser, run exactly as the hook runs it ──


def run_parser(stream: str) -> str:
    r = subprocess.run([sys.executable, "-c", PYCODE], input=stream,
                       capture_output=True, text=True, timeout=30)
    return r.stdout


# real-shape record (the verified CLI stream shape) renders fully
out = run_parser(json.dumps({"fileName": "test_file.py", "startLine": 42,
                             "severity": "major", "title": "The title",
                             "comment": "body"}) + "\n")
check("a real-shape record renders file:line [sev] title — no ?:?",
      "- test_file.py:42 [major] The title" in out and "COUNT 1" in out
      and "SEV major" in out, out.strip().splitlines()[:2])

# THE REGRESSION PIN: both generations present → the NEW keys must win.
# If anyone reverts the precedence, this names it.
out = run_parser(json.dumps({"fileName": "NEW.py", "file": "OLD.py",
                             "startLine": 7, "line": 99,
                             "severity": "major", "title": "T"}) + "\n")
check("BOTH key generations present → the NEW keys resolve (no drop-back)",
      "- NEW.py:7 [major] T" in out, out.strip().splitlines()[:1])

# tolerance: an old-generation-only record still renders (fallbacks live)
out = run_parser(json.dumps({"file": "legacy.py", "line": 5,
                             "severity": "minor", "title": "L"}) + "\n")
check("an old-generation-only record still renders (fallbacks kept)",
      "- legacy.py:5 [minor] L" in out, out.strip().splitlines()[:1])

# a null startLine is surfaced honestly, never fabricated
out = run_parser(json.dumps({"fileName": "x.py", "startLine": None,
                             "severity": "minor", "title": "N"}) + "\n")
check("a null line renders ? honestly (never a fabricated number)",
      "- x.py:? [minor] N" in out, out.strip().splitlines()[:1])

# ── 3b. THE NULL-FIELD FALLBACKS (guard, Sep 23): the live stream fills
# title/line fields late — the parser must milk every descriptive field
# before printing ?, or a real finding renders as noise. Each fallback is
# proven BEHAVIORALLY through the real parser; lose one and its check
# names it.
out = run_parser(json.dumps({"fileName": "a.py", "startLine": 1,
                             "severity": "major",
                             "comment": "The comment body"}) + "\n")
check("null title falls back to the comment text",
      "- a.py:1 [major] The comment body" in out,
      out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "t.py", "startLine": 2,
                             "severity": "minor", "type": "actionable"}) + "\n")
check("null title+comment falls back to the type",
      "- t.py:2 [minor] actionable" in out, out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "cc.py", "startLine": 3,
                             "severity": "minor",
                             "commentCategory": "FUNCTIONAL_CORRECTNESS"}) + "\n")
check("null title/comment/type falls back to commentCategory",
      "- cc.py:3 [minor] FUNCTIONAL_CORRECTNESS" in out,
      out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "it.py", "startLine": 4,
                             "severity": "minor",
                             "indicatorTypes": ["potential_issue"]}) + "\n")
check("null title/comment/type/category falls back to indicatorTypes",
      "- it.py:4 [minor] potential_issue" in out,
      out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "e.py", "startLine": 5,
                             "severity": "minor"}) + "\n")
check("a fully-naked record still names itself (never a bare ?)",
      "- e.py:5 [minor] (no title text in stream)" in out,
      out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "lr.py",
                             "severity": "minor", "title": "R",
                             "lineRange": {"start": 12, "end": 15}}) + "\n")
check("a null startLine falls back to lineRange.start",
      "- lr.py:12 [minor] R" in out, out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "el.py",
                             "severity": "minor", "title": "E",
                             "endLine": 9}) + "\n")
check("a null startLine+lineRange falls back to endLine",
      "- el.py:9 [minor] E" in out, out.strip().splitlines()[:1])
# precedence within each chain: the first live field wins
out = run_parser(json.dumps({"fileName": "p.py", "startLine": 3,
                             "lineRange": {"start": 8}, "endLine": 20,
                             "severity": "major", "title": "T",
                             "comment": "C"}) + "\n")
check("chain precedence: title beats comment, startLine beats lineRange/endLine",
      "- p.py:3 [major] T" in out, out.strip().splitlines()[:1])

# the fallback chains survive in source, in order (belt to the suspenders)
_chain = [PYCODE.find(f'o.get("{k}")')
          for k in ("title", "comment", "type", "commentCategory")]
check("the title fallback chain is present and ordered",
      all(0 < v for v in _chain) and _chain == sorted(_chain), _chain)
check("the lineRange fallback survives in source",
      'o.get("lineRange")' in PYCODE and 'lr.get("start")' in PYCODE
      and 'o.get("endLine")' in PYCODE)

# the never-silent guarantee: unparsable text reaches eyes, not /dev/null
out = run_parser("this is not json at all\n")
check("an unparsable stream prints RAWFALLBACK for eyes-on",
      "RAWFALLBACK" in out, out.strip()[:40])

# ── 3c. THE EXPLICIT-EMPTY CONTRACT (guard, Sep 23): valid JSON carrying
# zero records is a CLEAN review, not a parse failure — the eyes-on raw
# dump is noise on every quiet commit. The two states must stay disjoint.
out = run_parser("[]\n")
check("an explicitly empty JSON array prints CLEANJSON (not the raw-dump failure)",
      "CLEANJSON" in out and "RAWFALLBACK" not in out, out.strip()[:40])
out = run_parser(json.dumps({"findings": []}) + "\n")
check("an empty findings envelope prints CLEANJSON (not the raw-dump failure)",
      "CLEANJSON" in out and "RAWFALLBACK" not in out, out.strip()[:40])
out = run_parser(json.dumps({"fileName": "d.py", "startLine": 9,
                             "severity": "major", "title": "D"}) + "\n")
check("CLEANJSON never rides a stream that carried records",
      "COUNT 1" in out and "CLEANJSON" not in out, out.strip()[-20:])

# JSONL and wrapped-envelope shapes both parse (the stream is not one shape)
_lines = [json.dumps({"fileName": "a.py", "startLine": 1,
                      "severity": "major", "title": "A"}),
          json.dumps({"fileName": "b.py", "startLine": 2,
                      "severity": "minor", "title": "B"})]
out = run_parser("\n".join(_lines) + "\n")
check("JSONL parses record-by-record", "COUNT 2" in out, out.strip()[-20:])
out = run_parser(json.dumps({"findings": [
    {"fileName": "c.py", "startLine": 3, "severity": "major", "title": "C"}]}) + "\n")
check("a wrapped envelope parses (nested records found)",
      "- c.py:3 [major] C" in out, out.strip().splitlines()[:1])

# ── 3d. THE 0.8.1 STREAM SHAPES (guard, Sep 28): the live CLI stream
# carries three shapes the older generations never sent. Each is pinned
# BEHAVIORALLY through the real parser so a future CLI update that
# breaks one fails here by name, not in a live commit's stderr.
#
# (1) the ERROR RECORD: the CLI streams {"errorType": ...} for a
# bounced review (rate_limit was hit live Sep 28, twice). MIXED with
# findings it must ride the ERRORTYPE line; PURE it must NOT print
# CLEANJSON — a pure-error stream used to masquerade as a clean review
# (found by this very pinning turn, fixed in the hook same day).
out = run_parser(json.dumps({"errorType": "rate_limit"}) + "\n"
                + json.dumps({"fileName": "m.py", "startLine": 6,
                              "severity": "major", "title": "M"}) + "\n")
check("0.8.1: an errorType record rides ERRORTYPE next to its findings",
      "COUNT 1" in out and "ERRORTYPE rate_limit" in out
      and "- m.py:6 [major] M" in out, out.strip().splitlines()[-3:])
out = run_parser(json.dumps({"errorType": "rate_limit"}) + "\n")
check("0.8.1: a PURE error stream is COUNT 0 + ERRORTYPE, never CLEANJSON",
      "COUNT 0" in out and "ERRORTYPE rate_limit" in out
      and "CLEANJSON" not in out, out.strip()[:60])
check("the hook fires the review-errors banner (never silence) on ERRORTYPE",
      "review errors:" in src and "advisories may be incomplete" in src)
#
# (2) the SKELETAL RECORD: fileName + severity and NOTHING else (the
# live stream fills title/lines late, from the local review store). The
# parser must name the store honestly, never fabricate a ?-title.
out = run_parser(json.dumps({"fileName": "s.py", "severity": "minor",
                             "type": "finding"}) + "\n")
check("0.8.1: a skeletal record renders the honest store message",
      "- s.py:? [minor] (stream gave no line/text - full record in the "
      "local store)" in out, out.strip().splitlines()[:1])
#
# (3) codegenInstructions: the 0.8.1 stream sometimes carries BOTH the
# line number and the comment text ONLY inside that string —
# "Review comment at @file at line N: <text>". Both extractions are
# pinned, and the hybrid (skeletal + codegen) proves codegen wins.
out = run_parser(json.dumps({"fileName": "g.py", "severity": "major",
                             "codegenInstructions":
                             "Review comment at @g.py at line 33: The "
                             "goal-price calc repeats the raw entry "
                             "read. Wrap it."}) + "\n")
check("0.8.1: the line rides in codegenInstructions and is extracted",
      "- g.py:33 [major] The goal-price calc repeats the raw entry "
      "read. Wrap it." in out, out.strip().splitlines()[:1])
out = run_parser(json.dumps({"fileName": "h.py", "severity": "minor",
                             "codegenInstructions":
                             "Review comment at @h.py at line 8: Guard "
                             "the JSON parse."}) + "\n")
check("0.8.1: codegen text beats the skeletal (stream gave no line/text) "
      "message on a hybrid record",
      "- h.py:8 [minor] Guard the JSON parse." in out
      and "stream gave no line/text" not in out,
      out.strip().splitlines()[:1])
# the extraction idiom survives in source (belt to the suspenders)
check("0.8.1: the codegen extraction is the contracted regex shapes",
      're.search(r"at line (\\d+)"' in PYCODE
      and "Review comment at @" in PYCODE)
# the walk collects errorType records and the envelope stays understood
check("0.8.1: the walker collects errorType and walks wrapped envelopes",
      '"errorType" in o' in PYCODE and "has_file" in PYCODE
      and "has_body" in PYCODE)
# the store-join identity: the hook passes CR_SNAPSHOT so the skeletal
# join reads ONLY the review dir that matches THIS snapshot's head
check("0.8.1: the store join is snapshot-locked (CR_SNAPSHOT in, "
      "git.json head compared)",
      "CR_SNAPSHOT=\"${SNAPSHOT:-}\"" in src
      and 'os.environ.get("CR_SNAPSHOT")' in PYCODE
      and 'gitmeta.get("head")' in PYCODE)

# ── 4. the wiring: parser output feeds the findings renderer ─────────
check("the renderer consumes COUNT/SEV and prints the finding list",
      "s/^COUNT //p" in src and "s/^SEV //p" in src
      and "CodeRabbit findings:" in src)
check("an unparsed stream falls back to the raw head (eyes-on, warn-only)",
      "unparsed stream" in src and "head -c 600" in src)
# the explicit-empty answer: CLEANJSON is answered with one quiet line
check("the hook answers CLEANJSON with the quiet zero-findings line",
      "grep -q " in src and "'^CLEANJSON$'" in src
      and "zero findings" in src)
# regional: the CLEANJSON answer sits inside the empty-parse gate, ahead
# of the raw dump, and exits quietly — a clean review never reaches eyes-on
_tail = src[src.rfind('if [ -z "$n" ]; then'):]
_cj = _tail.find("CLEANJSON")
_hd = _tail.find("head -c 600")
check("the clean-JSON answer precedes the raw dump and exits quietly",
      0 <= _cj < _hd and "exit 0" in _tail[_cj:_hd],
      (_cj, _hd))
check("the review stays advisory (findings never block the commit)",
      "Advisory only — commit proceeds" in src)

# ── 5. THE COVERAGE RECEIPT (Oct 3): clean reviews must leave a record ──
# The CLI records a batch only when findings exist; the watchdog reads
# the hook's own receipts to tell a clean review from a review that
# never ran. The writer exists, every review exit records its outcome,
# and the receipt is append-only best-effort (never blocks the commit).
check("the receipt writer exists (repo scratch JSONL, best-effort)",
      "scratch/reviews_receipts.jsonl" in src and "cr_receipt()" in src
      and "|| true" in src)
check("every review exit records its outcome "
      "(clean / unparsed / errors / timeout)",
      all(f"cr_receipt {o}" in src for o in
          ("clean", "unparsed", "errors", "timeout"))
      and src.count("cr_receipt clean") >= 2,
      [o for o in ("clean", "unparsed", "errors", "timeout")
       if f"cr_receipt {o}" not in src])
check("the receipt is repo-rooted and epoch-stamped",
      "git rev-parse --show-toplevel" in src.split("RECEIPTS=", 1)[1][:200]
      and "date +%s" in src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_hookparser_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))   # the verdict rides STDOUT — the gate's result
                           # mode reads it there (DONE-only fails closed)
sys.exit(1 if FAIL else 0)
