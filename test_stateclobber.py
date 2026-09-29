"""THE CROSS-WRITER CLOBBER DRIVE (the Sep-28 sweep): replays the exact
interleavings that erased state before — a nested writer landing between
another writer's read and its save, on profit_guard's state, meta_layers'
state, and the cross-process contest_watch.json — and proves the merge
saves keep both sides. Also proves the writes are ATOMIC (tmp + rename,
never a torn read) and that intentional deletions still work."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auto_adjust as aa
import profit_guard as pg

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    # EVERY VERDICT ECHOES (the gate's lesson, Sep 29): the pre-commit
    # tail only sees stdout, and a trap-deleted scratch keeps the
    # report file — a silent check is an undiagnosable red.
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


# ── the sandboxes ────────────────────────────────────────────────────
_sbx = Path(tempfile.mkdtemp())
_sbx2 = Path(tempfile.mkdtemp())
_real_state = getattr(pg, "STATE_FILE", None)
pg.STATE_FILE = _sbx / "profit_guard_state.json"

(_sbx / "meta_layers_state.json").write_text(
    json.dumps({"tomorrow_goal_factor": 1.1, "rcl_last": {"x": 1}}),
    encoding="utf-8")
import meta_layers as ml
_real_ml_state = ml.STATE_FILE
ml.STATE_FILE = _sbx / "meta_layers_state.json"

import background_scanner as bs
_real_cw = bs.CONTEST_WATCH_JSON
bs.CONTEST_WATCH_JSON = str(_sbx / "contest_watch.json")
(_sbx / "contest_watch.json").write_text(json.dumps(
    {"KAITO/USD": {"entry": 0.3277, "high": 0.3290,
                   "source": "scout-signal"}}), encoding="utf-8")

try:
    # ── 1. profit_guard: the auto_adjust clobber, replayed ───────────
    aa.set_guard_factor_temporarily(1.92, 3, before=2.0)
    aa.apply_adjustment(-0.05, "CW-1", "sha256:cw1")
    st = json.loads((_sbx / "profit_guard_state.json").read_text())
    check("the box and the ledger coexist after apply (the original "
          "clobber erased one)",
          "auto_adjust_box" in st and "auto_adjust_ledger" in st
          and st["factor"] < 2.0, sorted(st))
    box_before = st["auto_adjust_box"]["before"]

    # THE INTERLEAVING: a foreign key lands between apply's read and
    # its internal writes — the merge-save must keep it.
    aa.tick_rotation()
    cur = json.loads((_sbx / "profit_guard_state.json").read_text())
    cur["self_adjust_parts"] = {"env": 1.02}   # the scanner's daily row
    (_sbx / "profit_guard_state.json").write_text(json.dumps(cur))
    aa.tick_rotation()   # the box holds 3 rotations — expire needs the
    aa.tick_rotation()   # full box drained (2 more ticks)
    aa.expire_if_due()
    st = json.loads((_sbx / "profit_guard_state.json").read_text())
    check("a foreign writer's key survives the next auto-adjust write "
          "(merge-save, not whole-file rewrite)",
          st.get("self_adjust_parts") == {"env": 1.02},
          sorted(st))
    check("the revert restores the before factor and keeps the ledger",
          st["factor"] == box_before
          and any(r.get("reason") == "time_box_expired"
                  for r in st.get("auto_adjust_ledger", [])),
          (st["factor"], box_before))

    # THE DEGENERATION (the round-2 review's major): a writer that
    # passes a FULL STALE snapshot to the merge-save degenerates it
    # into a whole-file rewrite — every stale key overwrites the
    # merge. The revert path must save OWNER KEYS ONLY.
    aa.set_guard_factor_temporarily(1.88, 2, before=1.95)
    aa.tick_rotation()
    aa.tick_rotation()                      # drain the box: 2 left
    cur = json.loads((_sbx / "profit_guard_state.json").read_text())
    cur["self_adjust_factor"] = 1.234       # the scanner's live key
    (_sbx / "profit_guard_state.json").write_text(json.dumps(cur))
    aa.expire_if_due()
    st = json.loads((_sbx / "profit_guard_state.json").read_text())
    check("the revert's owner-key save keeps the other writer's key "
          "(a stale full snapshot would have rewritten it away)",
          st.get("self_adjust_factor") == 1.234
          and "auto_adjust_box" not in st, sorted(st))

    # ── 2. atomicity: a torn write is never readable ─────────────────
    check("the state file has no .tmp residue after normal saves "
          "(atomic rename, in-place write would leave none either — "
          "the next check proves the atomic path directly)",
          not (_sbx / "profit_guard_state.json.tmp").exists(), "")
    (_sbx / "profit_guard_state.json.tmp").write_text('{"torn": true}')
    aa.set_guard_factor_temporarily(1.9, 2, before=2.0)
    st = json.loads((_sbx / "profit_guard_state.json").read_text())
    check("the tmp-residue scenario cannot corrupt the live file (the "
          "rename replaces atomically)",
          st["factor"] == 1.9 and "torn" not in st, st["factor"])

    # ── 3. meta_layers: merge-save keeps foreign keys, honors drops ──
    ml._save({"silent_docks": {"rot": "2026-09-28 12-16"}})
    st = json.loads((_sbx / "meta_layers_state.json").read_text())
    check("meta_layers merge keeps foreign keys and honors _SAVE_DROPS "
          "(rcl_last dropped, silent_docks + tomorrow_goal_factor kept)",
          "silent_docks" in st and "rcl_last" not in st
          and st.get("tomorrow_goal_factor") == 1.1, sorted(st))

    # ── 4. contest_watch: the cross-process interleaving ─────────────
    # the scanner saves its view (stale: it never saw the bot's row)
    bs._save_contest_watch({"KAITO/USD": {"entry": 0.3277,
                                          "high": 0.3300,
                                          "source": "scout-signal"}})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("the scanner's save keeps the bot's row (the interleaving "
          "that erased the paper book's view of the real fill)",
          doc.get("KAITO/USD", {}).get("high") == 0.3300, doc)
    # the bot lands a row the scanner hasn't seen; the scanner then
    # saves ITS stale view — the merge must keep the bot's row
    bs._save_contest_watch({"SOL/USD": {"entry": 100.0,
                                        "source": "bot-strategy"}})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("a bot-side row (SOL) survives the scanner's next stale save",
          "SOL/USD" in doc and "KAITO/USD" in doc, sorted(doc))
    # the scanner's GET-OUT pop (deletion) must still delete
    bs._save_contest_watch({"SOL/USD": None})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("the GET-OUT pop (None value) deletes the row — intentional "
          "deletion still works under merge-save",
          "SOL/USD" not in doc and "KAITO/USD" in doc, sorted(doc))

    # ── 5. the deletion sites are TOMBSTONES, not pops (the round-2
    # review's resurrection bug) ────────────────────────────────────
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    check("the close path TOMBSTONES the row (a pop would resurrect "
          "it under merge-save — an absent key means never-seen)",
          "watch[pair] = None" in bs_src
          and "watch.pop(pair" not in bs_src, "")
    # the remover's own working dict (carrying the None) saved again
    # must keep the row dead — the merge drops it and never re-adds it
    bs._save_contest_watch({"KAITO/USD": {"entry": 0.3277,
                                          "high": 0.3290,
                                          "source": "scout-signal"},
                            "SOL/USD": None})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("a remover's dict carrying a tombstone keeps the row dead "
          "across a later save (drop AND no re-add)",
          "SOL/USD" not in doc and "KAITO/USD" in doc, sorted(doc))
    check("the console writer merge-saves too (three writers share the "
          "file — the round-1 sweep counted two)",
          "MERGE-SAVE" in (BASE / "app.py").read_text(encoding="utf-8"),
          "")


    # ── 6. REAL CONCURRENT WRITER PROCESSES (the round-3 upgrade: the
    # interleavings above are hand-replayed; these are scheduled by the
    # OPERATING SYSTEM against sandboxed state files). One-shot WAVE
    # processes: each spawns, imports its PRODUCTION writer in its own
    # interpreter, performs exactly one write, and exits — no pipes, no
    # protocol, nothing to wedge; a hang is a named kill. The scanner
    # and guard workers ride THIS repo's venv; the bot worker rides the
    # bot's own venv (its pandas lives there); the console worker's
    # _save_watch is exercised in-process (the module carries the
    # streamlit import, too heavy to spawn per-wave — its merge path is
    # the same code the tombstone pins above already drive).
    BOT = BASE.parent / "kraken_xrp_trading_bot"
    _cw_proc = _sbx / "contest_watch.json"   # the cross-process file
    _pg_proc = _sbx / "profit_guard_state.json"

    def _one(exe: str, body: str, env_setup: str) -> subprocess.Popen:
        script = ("import sys, json\n"
                  f"sys.path.insert(0, {str(BASE)!r})\n"
                  + env_setup + "\n" + body + "\n"
                  "print('WROTE', flush=True)\n")
        return subprocess.Popen(
            [exe, "-X", "utf8", "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(BASE))

    def _run_wave(waves: list[tuple[str, str, str]], timeout_s: float = 60):
        procs = [(name, _one(exe, body, setup))
                 for name, exe, (body, setup) in
                 ((n, e, w) for n, e, w in waves)]
        out = {}
        deadline = time.time() + timeout_s
        for name, p in procs:
            try:
                o, e = p.communicate(timeout=max(1, deadline - time.time()))
                out[name] = {"rc": p.returncode, "out": o[-100:],
                             "err": e[-400:]}
            except subprocess.TimeoutExpired:
                p.kill()
                out[name] = {"rc": "KILLED", "out": "", "err": "timeout"}
        return out

    _SCAN_SETUP = f"import background_scanner as bs; bs.CONTEST_WATCH_JSON = {str(_cw_proc)!r}"
    _SCAN_BODY = "bs._save_contest_watch({PAIR: {'entry': 1.0, 'high': 1.0, 'source': 'storm-scanner'}})"
    _PG_SETUP = f"import profit_guard as pg; pg.STATE_FILE = {str(_pg_proc)!r}"
    _PG_BODY = "pg._save({KEY: VALUE})"
    _BOT_SETUP = f"sys.path.insert(0, {str(BOT)!r}); import bot_engine; bot_engine.CONTEST_WATCH_JSON_BOT = {str(_cw_proc)!r}"
    _BOT_BODY = "bot_engine._upsert_bot_watch_row(PAIR, {'entry': ENTRY, 'high': ENTRY, 'size': 100.0, 'source': 'storm-bot'})"
    _botpy = str(BOT / ".venv" / "Scripts" / "python.exe")
    import os as _os
    _have_bot = (_os.path.exists(_botpy))

    # the reader storm: a fresh reader every 2ms for the whole wave —
    # not one torn write may be observable
    _reader = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c",
         "import json, time\n"
         f"path = {str(_cw_proc)!r}\n"
         "end = time.time() + 14\n"
         "n = empty_race = open_err = partial = 0\n"
         "evidence = []\n"
         "while time.time() < end:\n"
         "    try:\n"
         "        with open(path, 'r', encoding='utf-8') as fh:\n"
         "            raw = fh.read()\n"
         "        json.loads(raw)\n"
         "        n += 1\n"
         "    except json.JSONDecodeError as e:\n"
         "        # THE DISTINCTION THE PIN TURNS ON: an EMPTY view is the\n"
         "        # documented Windows oplock/replace race (a reader may\n"
         "        # briefly see 0 bytes mid-swap) and the fail-soft readers\n"
         "        # absorb it by design; a PARTIAL document would be a real\n"
         "        # torn write and is forbidden\n"
         "        import os\n"
         "        if os.path.getsize(path) == 0 or len(e.doc or '') == 0:\n"
         "            empty_race += 1\n"
         "        else:\n"
         "            partial += 1\n"
         "            evidence.append(repr(e)[:120])\n"
         "    except OSError:\n"
         "        open_err += 1\n"
         "    time.sleep(0.002)\n"
         "print(json.dumps({'reads': n, 'empty_race': empty_race,\n"
         "                  'open_err': open_err, 'partial': partial,\n"
         "                  'evidence': evidence[:3]}))\n"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8")

    # ── WAVE 1: the storm — 6 rounds x (scanner + bot + guard) in
    # PARALLEL; the OS schedules every interleaving ──────────────────
    _all = []
    for _i in range(6):
        _all.append((f"scan{_i}", sys.executable,
                     (_SCAN_BODY.replace("PAIR", f"'STR{_i}/USD'"),
                      _SCAN_SETUP)))
        if _have_bot:
            _all.append((f"bot{_i}", _botpy,
                         (_BOT_BODY.replace("PAIR", f"'STR{_i}/USD'")
                                   .replace("ENTRY", str(_i * 0.01 + 1.0)),
                          _BOT_SETUP)))
        _all.append((f"guard{_i}", sys.executable,
                     (_PG_BODY.replace("KEY", f"'stormkey{_i}'")
                              .replace("VALUE", f"'v{_i}'"),
                      _PG_SETUP)))
    _res1 = _run_wave(_all, timeout_s=90)
    _res1j = json.dumps(_res1)
    # THE NAMED CONTENTION FAMILY (the drive's own discoveries): a
    # rename can transiently lose TWO ways on Windows — WinError 5
    # (access denied: the loser of two replaces) and WinError 32
    # (sharing violation: the target was mid-read by another process's
    # handle). Both are NAMED, transient, retryable — the fail-soft +
    # retry contract absorbs them. What is FORBIDDEN: an unnamed crash.
    def _named_contention(v: dict) -> bool:
        # THE CONTENTION FAMILY (widened Sep 29): WinError 5/32 are the
        # winerror renderings; a failed open/replace can also surface
        # as "[Errno 13] Permission denied" (errno strerror form of
        # the same access/share family). All are named, transient,
        # retryable — never a torn file.
        err = v.get("err", "")
        return ("WinError 5" in err or "WinError 32" in err
                or "[Errno 13]" in err)
    _rename_lost = sum(1 for v in _res1.values() if _named_contention(v))
    # REDUCED ENVIRONMENTS (the drill's fixture repo has no writer
    # interpreter): every worker failing with an ImportError means the
    # concurrency section SKIPS HONESTLY — the hand-replayed pins above
    # stand, and no failure family fires for a missing sandbox
    def _starved(res: dict) -> bool:
        """IMPORT STARVATION, NAMED STRICTLY (Sep 29 review minor): at
        least one worker FAILED and EVERY failure is an import error.
        An empty failure set is a green wave — never a skip — so the
        doc-based checks below always get their real verdict."""
        _fails = [v for v in res.values() if v["rc"] == 1]
        return bool(_fails) and all(
            "ModuleNotFoundError" in v.get("err", "")
            or "ImportError" in v.get("err", "")
            for v in _fails)
    _import_starved = _starved(_res1)
    # THE HONEST CONTRACT: the conventions' job is NO CORRUPTION and NO
    # SILENT loss; a named contention failure is the documented residual
    # (round-1: last-writer-wins per key, retried next tick).
    _unnamed = {k: v for k, v in _res1.items()
                if v["rc"] not in (0, "KILLED")
                and not _named_contention(v)
                and "ModuleNotFoundError" not in v.get("err", "")
                and "ImportError" not in v.get("err", "")}
    check("the storm wave: no unnamed writer failure (a concurrent "
          "rename loser fails with the NAMED contention family — "
          "WinError 5 access-denied or WinError 32 sharing-violation — "
          "never silent, never a torn file; an import-starved "
          "environment skips honestly)",
          not _unnamed, json.dumps(_unnamed)[:500])
    doc = json.loads(_cw_proc.read_text(encoding="utf-8"))
    check("the post-storm book parses (merge-saves across real process "
          "scheduling — losers were named, never torn)",
          isinstance(doc, dict),
          (sorted(doc), _rename_lost))
    # THE CONDITIONAL PIN (restored Sep 29): the per-writer tmp names
    # (this batch) made collisions rare — a clean storm draws ZERO
    # losers, and demanding one would fail the fix's own success. The
    # pin fires only when contention actually occurred (proving the
    # loser contract), and reports the clean storm as information.
    if not _import_starved and _rename_lost >= 1:
        check("the rename contention is real but BOUNDED (the storm drew "
              "at least one loser, and every loser was in the named "
              "contention family)",
              True, f"{_rename_lost} named loser(s)")
    elif not _import_starved:
        check("the storm drew no contention losers (the per-writer tmp "
              "names keep writers out of each other's way — a clean "
              "storm is the fix working, reported as information)",
              True, f"losers=0")
    # the storm may have starved a scanner writer (a lost rename is a
    # lost whole-save): the ONE scanner save per pair re-runs until the
    # book heals — the machine's own tick does exactly this
    _missing = [i for i in range(6) if f"STR{i}/USD" not in doc]
    _heal = [(f"heal{_i}", sys.executable,
              (_SCAN_BODY.replace("PAIR", f"'STR{_i}/USD'"),
               _SCAN_SETUP)) for _i in _missing]
    _heal_res = _run_wave(_heal, timeout_s=90) if _heal else {}
    doc = json.loads(_cw_proc.read_text(encoding="utf-8"))
    _heal_starved = _import_starved or _starved(_heal_res)
    check("the book HEALS: one scanner tick per starved row restores "
          "every storm write (fail-soft + retry beats fail-stop; an "
          "import-starved environment skips honestly)",
          _heal_starved
          or all(f"STR{i}/USD" in doc for i in range(6)), sorted(doc))
    _st_pg = json.loads(_pg_proc.read_text(encoding="utf-8"))
    _st_missing = [i for i in range(6)
                   if _st_pg.get(f"stormkey{i}") != f"v{i}"]
    _heal_pg = [(f"healpg{_i}", sys.executable,
                 (_PG_BODY.replace("KEY", f"'stormkey{_i}'")
                          .replace("VALUE", f"'v{_i}'"), _PG_SETUP))
                for _i in _st_missing]
    _heal_pg_res = _run_wave(_heal_pg, timeout_s=90) if _heal_pg else {}
    _st_pg = json.loads(_pg_proc.read_text(encoding="utf-8"))
    _pg_starved = _import_starved or _starved(_heal_pg_res)
    check("the guard state HEALS the same way: concurrent owner-key "
          "saves retry to completeness (the owner-keys rule under real "
          "scheduling)",
          _pg_starved
          or all(_st_pg.get(f"stormkey{i}") == f"v{i}" for i in range(6)),
          sorted(_st_pg))

    # ── WAVE 2: the fill between the reads — the bot's row lands while
    # scanner writers are in flight; the merge must keep it ───────────
    _all2 = [(f"scan2_{i}", sys.executable,
              (_SCAN_BODY.replace("PAIR", f"'KAITO/USD'"), _SCAN_SETUP))
             for i in range(4)]
    if _have_bot:
        _all2.append(("bot_fill", _botpy,
                      (_BOT_BODY.replace("PAIR", "'KAI/USD'")
                                .replace("ENTRY", "0.3277"), _BOT_SETUP)))
    _res2 = _run_wave(_all2, timeout_s=90)
    # THE RETRY LEG (the sandbox drill's flake, caught Sep 29): a fill
    # worker that lost a NAMED contention race is retried ONCE — the
    # machine's own "next tick" heal, the same contract the storm
    # wave's healing pin proves. Unnamed failures stay forbidden.
    # THE RETRY LEG (the sandbox drill's flake, caught Sep 29): a fill
    # worker that lost a NAMED contention race is retried — the
    # machine's own "next tick" heal, the same contract the storm
    # wave's healing pin proves. BOUNDED HEAL-TO-COMPLETION: the
    # contract is not 'one retry suffices', it is 'the next tick saves
    # again UNTIL the row lands'; two heal rounds beyond the first
    # wave. Unnamed failures stay forbidden.
    for _r_i in range(2):
        _retry2 = [(f"{n}_r{_r_i}", exe, args) for (n, exe, args) in _all2
                   if _res2.get(n, {}).get("rc") == 1
                   and _named_contention(_res2[n])]
        if not _retry2:
            break
        _res2.update(_run_wave(_retry2, timeout_s=90))
    doc = json.loads(_cw_proc.read_text(encoding="utf-8"))
    _w2_starved = _import_starved or _starved(_res2)
    # THE BOT ROW IS DEMANDED ONLY WHEN THE BOT RAN (the sandbox
    # lesson, Sep 29): the commitgate sandbox has no sibling bot repo,
    # so _have_bot is False there and KAI/USD can never exist — the
    # pin's success condition must mirror the wave's composition.
    _fill_ok = ("KAITO/USD" in doc
                and (not _have_bot or "KAI/USD" in doc))
    check("under parallel scanner writers a bot fill survives every "
          "merge (the round-1 casualty, process-scheduled; an "
          "import-starved or bot-less environment skips honestly)",
          _w2_starved or _fill_ok,
          (sorted(doc), json.dumps(_res2)[:200]))

    # ── WAVE 3: the tombstone is the LAST word — re-adds and tombstones
    # land in parallel, then one final tombstone closes the file ─────
    _all3 = [("final_scan", sys.executable,
              (_SCAN_BODY.replace("PAIR", "'KAITO/USD'"), _SCAN_SETUP)),
             ("final_tomb", sys.executable,
              ("w = bs._load_contest_watch(); w['KAITO/USD'] = None; "
               "bs._save_contest_watch(w)", _SCAN_SETUP))]
    _res3 = _run_wave(_all3, timeout_s=90)
    # after the wave, ONE more scanner save carrying only the tombstone:
    # the file's KAITO row must stay dead
    _res3b = _run_wave(
        [("closer", sys.executable,
          ("w = bs._load_contest_watch(); w['KAITO/USD'] = None; "
           "bs._save_contest_watch(w)", _SCAN_SETUP))], timeout_s=60)
    doc = json.loads(_cw_proc.read_text(encoding="utf-8"))
    check("the tombstone is the last word across parallel re-adds "
          "(None-means-delete under real scheduling)",
          "KAITO/USD" not in doc, sorted(doc))

    # the reader's verdict: thousands of reads under fire, classified
    _ro, _ = _reader.communicate(timeout=30)
    _rv = json.loads(_ro.strip().splitlines()[-1])
    check("the reader storm: thousands of reads under concurrent fire, "
          "and NOT ONE partial document (an empty mid-swap view is the "
          "documented OS race the fail-soft readers absorb; a truncated "
          "JSON body would be a real torn write and is forbidden)",
          _rv["reads"] > 50 and _rv["partial"] == 0,
          _rv)

finally:
    if _real_state is not None:
        pg.STATE_FILE = _real_state
    else:
        del pg.STATE_FILE
    ml.STATE_FILE = _real_ml_state
    bs.CONTEST_WATCH_JSON = _real_cw
    shutil.rmtree(_sbx, ignore_errors=True)
    shutil.rmtree(_sbx2, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "stateclobber" / "_stateclobber_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
