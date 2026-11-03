"""THE 0.8.1 FIXTURE DRIVE (task 9A): the captured-shape stream at
tests/fixtures/coderabbit_0_8_1.json is frozen and fed through the REAL
hook parser exactly as a commit runs it — the three stream shapes the
0.8.1 CLI sends (errorType, skeletal, codegenInstructions) proven from
an immutable artifact instead of inline literals. If a future CLI
drifts one shape, this names it — and the fixture doubles as the
replay evidence for a bad live commit's stderr."""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

src = (BASE / "hooks" / "pre-commit").read_text(encoding="utf-8")
_a = src.find('PYCODE="$(cat <<\'CRPYEOF\'')
_b = src.find("\nCRPYEOF", _a)
PYCODE = src[_a:_b].split("<<'CRPYEOF'", 1)[1].lstrip("\n")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


fixture_path = BASE / "tests" / "fixtures" / "coderabbit_0_8_1.json"
check("the fixture exists and every line is valid JSON (a captured "
      "stream, not a summary)",
      fixture_path.exists()
      and all(json.loads(l) for l in
              fixture_path.read_text(encoding="utf-8").splitlines()
              if l.strip()),
      fixture_path)

stream = fixture_path.read_text(encoding="utf-8")
r = subprocess.run([sys.executable, "-c", PYCODE], input=stream,
                   capture_output=True, text=True, timeout=30)
out = r.stdout

check("the mixed errorType + findings stream rides ERRORTYPE next to "
      "its COUNT 4 (the error never silences the findings)",
      "COUNT 4" in out and "ERRORTYPE rate_limit" in out,
      out.strip().splitlines()[:2])
check("the full record renders file:line [sev] title",
      "- per_gate_export.py:165 [major] Fix the default rotation id"
      in out, out.strip().splitlines()[:3])
check("the skeletal record renders the honest store message, never a "
      "fabricated title",
      "- s.py:? [minor] (stream gave no line/text - full record in the "
      "local store)" in out, out.strip().splitlines())
check("codegenInstructions line+text extracted (line 33)",
      "- g.py:33 [major] The goal-price calc repeats the raw entry "
      "read. Wrap it." in out, out.strip().splitlines())
check("the hybrid record: codegen beats the skeletal message (scoped "
      "to the h.py line — the stream's real skeletal record keeps its "
      "own honest message)",
      "- h.py:8 [minor] Guard the JSON parse." in out
      and "- h.py:8 [minor] (stream gave no line/text" not in out,
      out.strip().splitlines())

# the pure-error half of the fixture, alone: never CLEANJSON
pure = "\n".join(l for l in stream.splitlines()
                 if "errorType" in l) + "\n"
out2 = subprocess.run([sys.executable, "-c", PYCODE], input=pure,
                      capture_output=True, text=True, timeout=30).stdout
check("the pure-error half is COUNT 0 + ERRORTYPE, never CLEANJSON "
      "(the masquerade class, pinned from the fixture)",
      "COUNT 0" in out2 and "ERRORTYPE rate_limit" in out2
      and "CLEANJSON" not in out2, out2.strip()[:80])

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "parser_fixtures" / "_hookparser_fixture_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
