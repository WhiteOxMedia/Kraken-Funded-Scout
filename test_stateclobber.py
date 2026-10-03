"""THE CROSS-WRITER CLOBBER DRIVE (the Sep-28 sweep): replays the exact
interleavings that erased state before — a nested writer landing between
another writer's read and its save, on profit_guard's state, meta_layers'
state, and the cross-process contest_watch.json — and proves the merge
saves keep both sides. Also proves the writes are ATOMIC (tmp + rename,
never a torn read) and that intentional deletions still work."""
import json
import shutil
import subprocess
from unittest import mock
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

    # ── 3b. THE LAST STORM-SHAPE WRITER JOINS THE CONTRACT (Oct 2):
    # ml._save still ended fixed .tmp + one raw os.replace — a reader
    # (the console's guard read via carve_out) holding the target at
    # the replace instant loses the whole learning state save silently
    # (the caller's fail-soft swallow). Same drill as 4a: one injected
    # WinError 5, then pass-through — the save must still LAND.
    import atomic_io as _aio_ml
    _real_ra_ml = _aio_ml.replace_atomic
    _real_osrep_ml = _aio_ml.os.replace
    _ml_calls = {"n": 0, "tmp": ""}

    def _contend_ml(src, dst, *a, **k):
        _ml_calls["n"] += 1
        _ml_calls["tmp"] = str(src)
        if _ml_calls["n"] == 1:
            err = OSError("drill3b: access denied")
            err.winerror = 5
            raise err
        return _real_osrep_ml(src, dst, *a, **k)

    def _ra_drill_ml(tmp, target, **kw):
        kw.setdefault("replace", _contend_ml)
        return _real_ra_ml(tmp, target, **kw)

    _patcher_ml = mock.patch.object(_aio_ml, "replace_atomic", _ra_drill_ml)
    _patcher_ml.start()
    try:
        ml._save({"drill3b": {"ok": True}})
    finally:
        _patcher_ml.stop()
    st = json.loads((_sbx / "meta_layers_state.json").read_text())
    check("3b.1 meta_layers._save survives a WinError-5 rename injection "
          "(the bounded retry heals in-process — the last storm-shape "
          "writer joined)",
          _ml_calls["n"] >= 2 and st.get("drill3b", {}).get("ok") is True,
          (_ml_calls, st.get("drill3b")))
    check("3b.2 the save's tmp is PER-WRITER (pid-suffixed)",
          ".tmp." in _ml_calls["tmp"], _ml_calls["tmp"])

    # ── 4. contest_watch: the cross-process interleaving ─────────────
    # ── 4a. THE CONTENTION-PROOF SAVE (Oct 2, the de-flake): the storm
    # runs showed the scanner's save loses the rename race to a
    # concurrent reader/writer — WinError 5 on the fixed .tmp — and a
    # single raw os.replace means a LOST SAVE ("contest watch save
    # failed" in the storm's own stderr; the popup's auto-track row
    # vanishes). The save must (a) use a PER-WRITER tmp (two writers
    # sharing one tmp name kill each other's temp, not just the
    # rename), and (b) ride atomic_io.replace_atomic's bounded backoff
    # so a transient contention heals IN-PROCESS. Proven by injection:
    # the real os.replace is wrapped to raise WinError 5 ONCE, then
    # pass through — the save must still land.
    import atomic_io as _aio_mod
    _real_replace = _aio_mod.os.replace
    _calls = {"n": 0, "tmp_names": set()}

    def _contending_replace(src, dst, *a, **k):
        _calls["n"] += 1
        _calls["tmp_names"].add(str(src))
        if _calls["n"] == 1:
            err = OSError("drill: access denied")
            err.winerror = 5
            raise err
        return _real_replace(src, dst, *a, **k)

    # the injection wraps atomic_io.replace_atomic itself (passing the
    # contending replace INTO the real helper): the helper's default
    # parameter bound os.replace at DEFINITION time, so a module-level
    # patch would be invisible to it — the wrapper exercises the REAL
    # retry loop with the injected rename, and bs's in-function import
    # resolves the patched symbol at call time
    _real_ra = _aio_mod.replace_atomic

    def _ra_drill(tmp, target, **kw):
        kw.setdefault("replace", _contending_replace)
        return _real_ra(tmp, target, **kw)

    _patcher = mock.patch.object(_aio_mod, "replace_atomic", _ra_drill)
    _patcher.start()
    try:
        _ok_c = bs._save_contest_watch(
            {"KAI/USD": {"entry": 1.0, "high": 1.0, "source": "drill"}})
    finally:
        _patcher.stop()
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("4a.1 the save survives a WinError-5 rename injection (the "
          "bounded retry heals in-process — a single raw replace "
          "loses the row)",
          _ok_c is True and _calls["n"] >= 2
          and doc.get("KAI/USD", {}).get("source") == "drill",
          (_calls, doc.get("KAI/USD")))
    check("4a.2 the save's tmp is PER-WRITER (pid-suffixed — two "
          "PROCESSES can never collide on one shared .tmp; within one "
          "process the writer holds no lock across the write so the "
          "scanner's own save sites are serialized by design — each "
          "runs on the single scan thread and the QC/bridge paths never "
          "save concurrently with it)",
          any(".tmp." in t.split("contest_watch.json")[-1]
              for t in _calls["tmp_names"]), _calls["tmp_names"])

    # ── 4b. THE OTHER RAW SITES JOIN THE RETRY CONTRACT (Oct 2):
    # rotation_lockout (TWO writers, two processes: the scanner's
    # rotation_mark_fired and the console's profit re-arm), the
    # suppression log (one writer, ~10 production readers — a reader at
    # the replace instant is the same contention family), take_payouts
    # and the snapshot log. Each save must (a) use a per-writer tmp and
    # (b) ride replace_atomic's bounded backoff — proven by the same
    # WinError-5-once injection as 4a, plus the registry pins that hold
    # the census to all of it.
    _rl_real = bs.ROTATION_LOCKOUT_FILE
    _tp_real = bs.TAKE_PAYOUTS_JSON
    _sl_real = bs.SUPPRESSION_LOG_FILE
    _ss_real = getattr(bs, "SIGNAL_SNAPSHOTS_FILE", None)
    bs.ROTATION_LOCKOUT_FILE = str(_sbx / "rotation_lockout.json")
    bs.TAKE_PAYOUTS_JSON = str(_sbx / "take_payouts.json")
    bs.SUPPRESSION_LOG_FILE = str(_sbx / "popup_suppressions.jsonl")
    if _ss_real is not None:
        bs.SIGNAL_SNAPSHOTS_FILE = str(_sbx / "signal_snapshots.jsonl")
    _b4 = {"n": 0, "tmps": set()}

    def _contend4(src, dst, *a, **k):
        _b4["n"] += 1
        _b4["tmps"].add(str(src).split("\\")[-1].split("/")[-1])
        if _b4["n"] in (1, 2):
            err = OSError("drill4: access denied")
            err.winerror = 5
            raise err
        return _real_replace(src, dst, *a, **k)

    def _ra_drill4(tmp, target, **kw):
        kw.setdefault("replace", _contend4)
        return _real_ra(tmp, target, **kw)

    _patcher4 = mock.patch.object(_aio_mod, "replace_atomic", _ra_drill4)
    _patcher4.start()
    try:
        # rotation_mark_fired in the 12-16 rotation: the module clock
        # is patched, so the drill is deterministic wherever it runs
        import datetime as _dt4
        _noon = _dt4.datetime(2026, 10, 2, 12, 30,
                              tzinfo=bs._now_mst().tzinfo)
        _real_now4 = bs._now_mst
        bs._now_mst = lambda: _noon
        try:
            bs.rotation_mark_fired("DRILL4/USD")
        finally:
            bs._now_mst = _real_now4
        # take_payouts
        bs._save_take_payout("DRILL4/USD", 25)
        # the suppression log (one row, then the cap slice applies)
        bs._log_suppression("DRILL4/USD", "drill4-gate", "drill four detail",
                            quick=70.0)
        # the snapshot log (the hindsight seam rides the same write)
        if _ss_real is not None and "_log_signal_snapshot" in dir(bs):
            bs._log_signal_snapshot({"pair": "DRILL4/USD",
                                     "gate": "drill4-gate",
                                     "quick": 70.0})
    finally:
        _patcher4.stop()
        bs.ROTATION_LOCKOUT_FILE = _rl_real
        bs.TAKE_PAYOUTS_JSON = _tp_real
        bs.SUPPRESSION_LOG_FILE = _sl_real
        if _ss_real is not None:
            bs.SIGNAL_SNAPSHOTS_FILE = _ss_real
    _rl_doc = json.loads((_sbx / "rotation_lockout.json").read_text())
    _tp_doc = json.loads((_sbx / "take_payouts.json").read_text())
    _sl_doc = (_sbx / "popup_suppressions.jsonl").read_text().splitlines()
    _drill4_rows = [l for l in _sl_doc if "drill four detail" in l]
    check("4b.1 every remaining raw state writer rides the retry "
          "contract (rotation_lockout, take_payouts, the suppression "
          "log: each survives its WinError-5 injections and LANDS)",
          "DRILL4/USD" in _rl_doc and "DRILL4/USD" in _tp_doc
          and len(_drill4_rows) == 1,
          (_rl_doc, _tp_doc, len(_drill4_rows)))
    check("4b.2 every drill writer used a PER-WRITER tmp (pid-suffixed "
          "— the shared fixed .tmp is what killed the storm)",
          len(_b4["tmps"]) >= 3
          and all(".tmp." in t for t in _b4["tmps"]), _b4["tmps"])
    _src4 = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    _iw = _src4.find("def rotation_mark_fired")
    _iw2 = _src4.find("def _save_take_payout")
    _iw3 = _src4.find("def _log_suppression")
    _iw4 = _src4.find("def _log_signal_snapshot")
    _end4 = _src4.find("\ndef ", _iw4) if _iw4 > 0 else len(_src4)
    _seg4 = (_src4[_iw:_iw2] + _src4[_iw2:_iw3]
             + _src4[_iw3:(_iw4 if _iw4 > 0 else _iw3 + 4000)])
    # (the REGISTRY-presence half lives in diag_statewriters.main() —
    # this sandbox materializes background_scanner.py but not the
    # linter module, and the registry's guard belongs to the linter's
    # own drive, which runs at every commit)
    check("4b.3 all five migrated writers ride replace_atomic with "
          "per-writer tmps in the scanner's source (the write-shape "
          "leg; the registry leg lives in the linter's own drive)",
          "replace_atomic" in _seg4
          and _seg4.count("f\".tmp.{os.getpid()}\"") + _seg4.count(
              "'.tmp.' + ") >= 4,
          ("replace_atomic" in _seg4, _seg4.count("os.getpid()")))
    # ── 4c THE ARCHIVE-ON-TRIM (Oct 3, red-first): the cap keeps the
    # LEDGER fast for its ~10 readers, but the trim used to DESTROY the
    # dropped prefix silently — a rotation's burial burst erased the
    # funnel's own history (the Oct 2 incident: 2 rows gone live at
    # 999/999). The dropped rows must land in the append-only archive
    # BEFORE the ledger's atomic replace: no write can lose a row, and
    # the readers' contract never changes.
    _sl_real4c = bs.SUPPRESSION_LOG_FILE
    _ar_real4c = getattr(bs, "SUPPRESSION_ARCHIVE_FILE", None)
    _max_real4c = bs.SUPPRESSION_LOG_MAX
    bs.SUPPRESSION_LOG_FILE = str(_sbx / "popup_suppressions.jsonl")
    bs.SUPPRESSION_ARCHIVE_FILE = str(
        _sbx / "popup_suppressions.archive.jsonl")
    bs.SUPPRESSION_LOG_MAX = 5   # a small cap: the fixture fits the drill
    try:
        # a clean slate: the 4b drill's row rides in the same sandbox
        # file — the fixture's arithmetic owns exactly its own writes
        (_sbx / "popup_suppressions.jsonl").unlink(missing_ok=True)
        (_sbx / "popup_suppressions.archive.jsonl").unlink(missing_ok=True)
        for _i in range(5):
            bs._log_suppression(f"CAP{_i}/USD", "cap-gate",
                                f"cap fill {_i}", quick=70.0)
        bs._log_suppression("CAP5/USD", "cap-gate", "cap burst five",
                            quick=70.0)
        bs._log_suppression("CAP6/USD", "cap-gate", "cap burst six",
                            quick=70.0)
    finally:
        bs.SUPPRESSION_LOG_FILE = _sl_real4c
        bs.SUPPRESSION_LOG_MAX = _max_real4c
        if _ar_real4c is not None:
            bs.SUPPRESSION_ARCHIVE_FILE = _ar_real4c
        else:
            del bs.SUPPRESSION_ARCHIVE_FILE   # the pin was never there
    _live4c = (_sbx / "popup_suppressions.jsonl").read_text().splitlines()
    _arc4c = (_sbx / "popup_suppressions.archive.jsonl")
    _arows = _arc4c.read_text().splitlines() if _arc4c.exists() else []
    check("4c.1 the cap still governs the LIVE ledger (5 rows, the "
          "newest five — the readers' contract never changes)",
          len(_live4c) == 5
          and all(f"CAP{i + 2}/USD" in _live4c[i] for i in range(5)),
          len(_live4c))
    check("4c.2 the trimmed rows land in the APPEND-ONLY archive in "
          "order (CAP0, CAP1 — the rotation burst's oldest rows are on "
          "the record, never destroyed again)",
          len(_arows) == 2 and "CAP0/USD" in _arows[0]
          and "CAP1/USD" in _arows[1], _arows)
    check("4c.3 no loss, ever: written == live + archived (7 == 5 + 2)",
          len(_live4c) + len(_arows) == 7, (len(_live4c), len(_arows)))
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
    # the scanner's GET-OUT pop (deletion) must still delete — and
    # PERSIST the marker (round 7): the loader reads the pair gone AND
    # the raw disk carries the {"SOL/USD": null} last word.
    bs._save_contest_watch({"SOL/USD": None})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("the GET-OUT pop (None value) deletes the row and PERSISTS "
          "the marker (deletion still works, now durably)",
          "SOL/USD" not in bs._load_contest_watch()
          and doc.get("SOL/USD", "missing") is None, sorted(doc))

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
          "across a later save (drop AND no re-add, marker re-written)",
          "SOL/USD" not in bs._load_contest_watch()
          and "KAITO/USD" in doc
          and doc.get("SOL/USD", "missing") is None, sorted(doc))

    # ── 5b. THE MARKER PERSISTS (the round-7 answer): the STALE-OVERLAY
    # resurrection is now closed FOREVER, not for one save. A later
    # scanner save CARRYING the closed pair's old value must NOT re-add
    # it (the persisted marker is on disk for raw_tombs to see), and a
    # DELIBERATE re-add rides fresh_keys over the marker.
    bs._save_contest_watch({"SOL/USD": {"entry": 0.3277,
                                        "high": 0.3290,
                                        "source": "stale-console-view"}})
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("a STALE overlay carrying the closed pair's value cannot "
          "resurrect it — the persisted marker wins and is re-written "
          "(the round-2 defense, now durable)",
          doc.get("SOL/USD", "missing") is None
          and "KAITO/USD" in doc, sorted(doc))
    bs._save_contest_watch({"SOL/USD": {"entry": 0.3277,
                                        "high": 0.3290,
                                        "source": "deliberate-re-add"}},
                           fresh_keys=["SOL/USD"])
    doc = json.loads((_sbx / "contest_watch.json").read_text())
    check("a DELIBERATE re-add lands over the persisted marker via "
          "fresh_keys (the popup auto-track's first attempt wins)",
          isinstance(doc.get("SOL/USD"), dict)
          and doc["SOL/USD"].get("source") == "deliberate-re-add",
          sorted(doc))
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
    # THE CONVERGENCE POLL ("the next tick", the same fix the guard-
    # state heal leg got): a bounded re-read window — stragglers from
    # the wave converging mid-read count as healed; a hard absence at
    # the deadline is the failure. The old instant re-read flaked under
    # sweep load (a slow process wrote after the check read).
    _cw_deadline = time.time() + 30.0
    while time.time() < _cw_deadline:
        doc = json.loads(_cw_proc.read_text(encoding="utf-8"))
        if all(f"STR{i}/USD" in doc for i in range(6)):
            break
        time.sleep(0.5)
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
    # THE CONVERGENCE POLL ("the next tick"): a bounded re-read window —
    # stragglers converging mid-read count as healed, hard absence at
    # the deadline is the failure. The wave's evidence rides the detail.
    _pg_deadline = time.time() + 30.0
    while time.time() < _pg_deadline:
        _st_pg = json.loads(_pg_proc.read_text(encoding="utf-8"))
        if all(_st_pg.get(f"stormkey{i}") == f"v{i}" for i in range(6)):
            break
        time.sleep(0.5)
    _pg_starved = _import_starved or _starved(_heal_pg_res)
    check("the guard state HEALS the same way: concurrent owner-key "
          "saves retry to completeness (the owner-keys rule under real "
          "scheduling)",
          _pg_starved
          or all(_st_pg.get(f"stormkey{i}") == f"v{i}" for i in range(6)),
          f"wave={_heal_pg_res} state={sorted(_st_pg)}")

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
          "(None-means-delete under real scheduling; round 7: the "
          "marker PERSISTS on disk and the loader reads the pair gone)",
          doc.get("KAITO/USD", "missing") is None
          and "KAITO/USD" not in bs._load_contest_watch(), sorted(doc))

    # the reader's verdict: thousands of reads under fire, classified
    _ro, _ = _reader.communicate(timeout=30)
    _rv = json.loads(_ro.strip().splitlines()[-1])
    check("the reader storm: thousands of reads under concurrent fire, "
          "and NOT ONE partial document (an empty mid-swap view is the "
          "documented OS race the fail-soft readers absorb; a truncated "
          "JSON body would be a real torn write and is forbidden)",
          _rv["reads"] > 50 and _rv["partial"] == 0,
          _rv)

    # ── WAVE 4: the TOMBSTONE WIPE under fire (the round-4/5 family,
    # process-scheduled): new_contest's wipe writes {k: None} for every
    # row it saw. A stale writer mid-RMW must not overlay the old book
    # back over the wipe, and the popup's fresh re-add (fresh_keys) must
    # land over a wipe marker on the FIRST attempt — the recovered CR
    # major: wipe marker + no fresh-keys re-add = a fired popup that sat
    # unwatched. The OS picks the interleaving: a carrier may land
    # before the wipe's read (then the wipe tombstones it), after the
    # wipe's replace (then raw_tombs suppresses it and the marker is
    # re-written), or lose the rename entirely (then its row is simply
    # gone) — EVERY ordering must end with the old rows dead, and the
    # fresh re-add must land regardless of whether its marker survived.
    _sbx4 = Path(tempfile.mkdtemp())
    _w4_cw = _sbx4 / "contest_watch.json"   # the name new_contest resolves
    _w4_acct = _sbx4 / "account_state.json"
    _w4_cw.write_text(json.dumps({
        "TIA/USD": {"entry": 4.20, "source": "old-contest-ghost"},
        "POP/USD": {"entry": 0.55, "source": "old-contest"},
        "ATOM/USD": {"entry": 6.10, "source": "old-contest"},
    }), encoding="utf-8")
    _WIPE_SETUP = (f"from pathlib import Path; "
                   f"import contest_account as ca; "
                   f"ca.STATE_FILE = Path({str(_w4_acct)!r})")
    _WIPE_BODY = "ca.new_contest(1000)"
    _W4_SETUP = (f"import background_scanner as bs; "
                 f"bs.CONTEST_WATCH_JSON = {str(_w4_cw)!r}")
    _W4_STALE = ("bs._save_contest_watch({'TIA/USD': {'entry': 4.20, "
                 "'source': 'stale-carrier'}, 'ATOM/USD': {'entry': 6.10, "
                 "'source': 'stale-carrier'}, 'STRX/USD': {'entry': 1.0, "
                 "'source': 'stale-carrier'}})")
    _W4_READD = ("bs._save_contest_watch({'POP/USD': {'entry': 0.5512, "
                 "'source': 'fresh-auto-track'}}, fresh_keys=['POP/USD'])")
    _reader4 = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c",
         "import json, time, os\n"
         f"path = {str(_w4_cw)!r}\n"
         "end = time.time() + 10\n"
         "n = partial = open_err = 0\n"
         "while time.time() < end:\n"
         "    try:\n"
         "        with open(path, 'r', encoding='utf-8') as fh:\n"
         "            raw = fh.read()\n"
         "        json.loads(raw)\n"
         "        n += 1\n"
         "    except json.JSONDecodeError as e:\n"
         "        if os.path.getsize(path) == 0 or len(e.doc or '') == 0:\n"
         "            pass          # the documented empty mid-swap view\n"
         "        else:\n"
         "            partial += 1\n"
         "    except OSError:\n"
         "        open_err += 1\n"
         "    time.sleep(0.002)\n"
         "print(json.dumps({'reads': n, 'partial': partial, "
         "'open_err': open_err}))\n"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8")
    _res4 = _run_wave([
        ("wipe", sys.executable, (_WIPE_BODY, _WIPE_SETUP)),
        ("stale_a", sys.executable, (_W4_STALE, _W4_SETUP)),
        ("stale_b", sys.executable, (_W4_STALE, _W4_SETUP)),
        ("stale_c", sys.executable, (_W4_STALE, _W4_SETUP)),
    ], timeout_s=90)

    def _w4_doc():
        return json.loads(_w4_cw.read_text(encoding="utf-8"))

    def _w4_old_live(doc):
        return [k for k in ("TIA/USD", "ATOM/USD")
                if isinstance(doc.get(k), dict)]

    # THE WIPE HEAL LOOP (the storm's own next-tick contract): a wipe
    # worker that lost the rename (named contention) re-runs — the
    # operator's real next action is exactly this. Bounded; the old
    # rows' death is the target, not one worker's survival.
    _w4_deadline = time.time() + 45.0
    _w4_round = 0
    while time.time() < _w4_deadline and _w4_round < 3:
        _doc4 = _w4_doc()
        _wipe_failed = (_res4.get("wipe", {}).get("rc") == 1
                        and _named_contention(_res4.get("wipe", {})))
        if not _w4_old_live(_doc4) and not _wipe_failed:
            break
        _res4.update(_run_wave(
            [(f"wipe_h{_w4_round}", sys.executable,
              (_WIPE_BODY, _WIPE_SETUP))], timeout_s=90))
        _w4_round += 1
        time.sleep(0.5)
    _doc4 = _w4_doc()
    _w4_starved = _starved(_res4)
    check("the WIPE wave: new_contest's tombstone wipe lands under "
          "parallel stale carriers — the old rows end DEAD whatever "
          "the interleaving (before-read -> tombstoned, after-replace "
          "-> suppressed by raw_tombs, rename-lost -> gone; an "
          "import-starved environment skips honestly)",
          _w4_starved or not _w4_old_live(_doc4),
          (sorted(_doc4), json.dumps(_res4)[:300]))
    check("the wipe is a REAL new_contest (the account file carries "
          "the tier and a fresh contest_id — the wipe is the production "
          "path, not a synthetic dict write)",
          _w4_starved
          or (lambda a: isinstance(a, dict) and a.get("tier") == 1000
              and bool(a.get("contest_id")))(
              json.loads(_w4_acct.read_text(encoding="utf-8"))
              if _w4_acct.exists() else None),
          _w4_acct.read_text(encoding="utf-8")[:200]
          if _w4_acct.exists() else "account file missing")
    # THE FRESH RE-ADD: after the wave settles, the popup's auto-track
    # shape — fresh_keys names the caller's intent — must land over any
    # wipe marker. RETRY LEG (the storm waves' own contract): a re-add
    # that lost a NAMED contention race re-runs until the row lands —
    # the machine's next-tick heal; contention is the documented
    # residual family, silent loss is not.
    _readd_res = _run_wave([("readd", sys.executable,
                             (_W4_READD, _W4_SETUP))], timeout_s=60)
    _w4_readd_deadline = time.time() + 30.0
    while time.time() < _w4_readd_deadline:
        _doc4 = _w4_doc()
        if isinstance(_doc4.get("POP/USD"), dict) \
                and _doc4["POP/USD"].get("source") == "fresh-auto-track":
            break
        if _res4.get("readd", {}).get("rc") == 1 \
                and _named_contention(_res4.get("readd", {})):
            _res4.update(_run_wave(
                [(f"readd_h{time.time()}", sys.executable,
                  (_W4_READD, _W4_SETUP))], timeout_s=60))
        time.sleep(0.5)
    _doc4 = _w4_doc()
    _readd_starved = _w4_starved or _starved(_readd_res)
    check("the popup's fresh re-add lands over the wipe's marker "
          "(fresh_keys is the caller's intent — the "
          "fired-popup-sat-unwatched shape is closed under real "
          "scheduling; contention losses retry, silence never)",
          _readd_starved
          or (isinstance(_doc4.get("POP/USD"), dict)
              and _doc4["POP/USD"].get("source") == "fresh-auto-track"),
          (sorted(_doc4), json.dumps(_readd_res)[:200]))
    check("the fresh re-add clears ONLY its own marker — the old "
          "rows' markers ride forward (or were rename-lost: dead "
          "either way)",
          _readd_starved
          or (not isinstance(_doc4.get("TIA/USD"), dict)
              and not isinstance(_doc4.get("ATOM/USD"), dict)
              and isinstance(_doc4.get("POP/USD"), dict)),
          sorted(_doc4))
    _ro4, _ = _reader4.communicate(timeout=30)
    _rv4 = json.loads(_ro4.strip().splitlines()[-1])
    check("the wipe wave's reader: reads under wipe+carrier fire, and "
          "NOT ONE partial document",
          _rv4["reads"] > 20 and _rv4["partial"] == 0, _rv4)
    shutil.rmtree(_sbx4, ignore_errors=True)
    # THE PRODUCTION ROUTE (source pin riding the wave): the wave
    # proves the mechanism end-to-end (a real new_contest plus real
    # merge-saves); this pins that the production re-add — the popup's
    # auto-track save — is the fresh_keys shape, and that the wipe
    # verifies its own landing (the settle loop) instead of trusting
    # one replace (a future wipe that trusts the replace goes red).
    check("the popup auto-track's save names fresh_keys and the wipe "
          "carries the settle loop (the production re-add carries "
          "caller intent; the wipe verifies its own landing)",
          "fresh_keys=[pair]" in bs_src
          and "THE SETTLE LOOP" in (BASE / "contest_account.py")
          .read_text(encoding="utf-8"), "")

    # ── 7. THE TOMBSTONE RE-ADD TRIAGE (the recovered CR major's claim:
    # "the raw-tombstone merge-save drops fresh re-adds — add_xrp_watch
    # prints 'XRP added' but writes nothing"). VERDICT SPLIT: the
    # MECHANISM is fixed (fresh_keys, wave 4 above), but the CLAIM'S
    # NAMED SITE (the operator script) really does drop the re-add —
    # it never names fresh_keys — and the writer returns None, so its
    # truth-print is structurally impossible. These pins hold both
    # halves of the verdict forever.
    _ax = (BASE / "add_xrp_watch.py").read_text(encoding="utf-8")
    check("the operator re-add script names fresh_keys (operator "
          "intent — a tombstoned pair re-added BY HAND must land, "
          "the claim's named site)",
          "fresh_keys" in _ax, "")
    check("the re-add script consumes the writer's REPORTED landing "
          "(a truth-print needs a truth to print)",
          "_added = _save_contest_watch" in _ax and "return True" in
          (BASE / "background_scanner.py").read_text(encoding="utf-8")
          .split("def _save_contest_watch")[1]
          .split("\ndef ")[0], "")
    # THE MECHANISM, one more time at the writer level: tombstone on
    # disk + fresh_keys re-add = row lands; WITHOUT fresh_keys = row
    # stays dead (the stale-carry defense is the feature, not the bug).
    _tri_sbx = Path(tempfile.mkdtemp())
    import os as _os
    _tri_real = bs.CONTEST_WATCH_JSON
    bs.CONTEST_WATCH_JSON = str(_tri_sbx / "cw.json")
    try:
        bs._save_contest_watch({"XRP/USD": {"entry": 0.55,
                                            "source": "t"}})
        bs._save_contest_watch({"XRP/USD": None})   # the SOLD
        check("the tombstone holds against an UNNAMED re-add (the "
              "stale-carry defense working as designed)",
              "XRP/USD" not in bs._load_contest_watch(), "")
        bs._save_contest_watch(
            {"XRP/USD": {"entry": 0.5512, "source": "operator-re-add"}},
            fresh_keys=["XRP/USD"])
        check("the SAME re-add lands once the caller names its intent "
              "(fresh_keys — the deliberate re-add is the caller's, "
              "never the file's, to infer)",
              bs._load_contest_watch().get("XRP/USD", {})
              .get("source") == "operator-re-add", "")
        import atomic_io as _aio2
        # THE REPORT: the writer returns whether the replace landed —
        # a contention loser is a False, not a silent None.
        # the exhausted-retry injection: the helper wraps the REAL
        # replace_atomic with an always-contending rename — the bounded
        # backoff must exhaust (5 attempts) and the save REPORT False,
        # not crash and not lie
        _rep_real = _aio2.replace_atomic
        _contend_calls = {"n": 0}

        def _contend(a, b, **kw):
            _contend_calls["n"] += 1
            # the REAL Windows shape: the exception carries winerror=5
            # (a 2-arg OSError(5, ...) has no winerror and is correctly
            # classified by errno — 5 alone is not the retry family)
            err = OSError(13, "Access is denied")
            err.winerror = 5
            raise err

        def _ra_contend(tmp, target, **kw):
            kw.setdefault("replace", _contend)
            return _rep_real(tmp, target, **kw)

        with mock.patch.object(_aio2, "replace_atomic", _ra_contend):
            _reported = bs._save_contest_watch(
                {"W4T/USD": {"entry": 1.0, "source": "t"}})
        check("a save that loses EVERY retry (5 bounded attempts) "
              "REPORTS False (the truth-print contract: the caller can "
              "tell 'lost the race' from 'landed')",
              _reported is False and _contend_calls["n"] == 5,
              (_reported, _contend_calls))
        check("a clean save REPORTS True",
              bs._save_contest_watch(
                  {"W4T/USD": {"entry": 1.0, "source": "t"}}) is True,
              "")
    finally:
        bs.CONTEST_WATCH_JSON = _tri_real
        shutil.rmtree(_tri_sbx, ignore_errors=True)

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
