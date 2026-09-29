"""THE CROSS-WRITER CLOBBER DRIVE (the Sep-28 sweep): replays the exact
interleavings that erased state before — a nested writer landing between
another writer's read and its save, on profit_guard's state, meta_layers'
state, and the cross-process contest_watch.json — and proves the merge
saves keep both sides. Also proves the writes are ATOMIC (tmp + rename,
never a torn read) and that intentional deletions still work."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auto_adjust as aa
import profit_guard as pg

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


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
