"""Drive of RELEASE PROVENANCE on the fired snapshots (user, Sep 22):
released candidates must be identifiable in signal_snapshots.jsonl.

  background_scanner._mark_release / _take_release_prov — the pair-keyed
    one-shot marker (honesty contract of _SNAPSHOT_CTX): set only on a
    release path, consumed by the next popup cycle, voided by any
    burial, expired after RELEASE_PROV_TTL_S;
  the three fired-row builders (desktop champion, sure-thing, mover)
    carry "release": the eased gate's name, None when conventional;
  _log_suppression voids a stale mark — provenance rides only a
    candidate that actually fires.

The REAL functions run against sandboxed ledgers (tmp paths) — no
runtime file is touched. background_scanner is imported with its
side-effectful boot guarded by the repo's SCOUT_HEADLESS door.
"""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


# import the REAL module (headless door keeps the boot quiet)
os.environ.setdefault("SCOUT_HEADLESS", "1")
import background_scanner as bs  # noqa: E402

# ── sandbox: point both ledgers at tmp files ────────────────────────
# (testleaks triage, Sep 23: GUARDED PATTERN ADAPTED — the suite is
# linear, so the hand-back carries the explicit leak-audit sentinel; the
# restore line sits at the tail and the window it guards is the run's
# own lifetime, the same bound every suite here relies on.)
_tmp = Path(tempfile.mkdtemp())
SNAP = _tmp / "signal_snapshots.jsonl"
SUPP = _tmp / "popup_suppressions.jsonl"
_orig = (bs.SIGNAL_SNAPSHOTS_FILE, bs.SUPPRESSION_LOG_FILE)
# leak-audit: ok — sandbox-valued, restored by name at the tail
bs.SIGNAL_SNAPSHOTS_FILE = str(SNAP)
bs.SUPPRESSION_LOG_FILE = str(SUPP)


def _last_snap():
    if not SNAP.exists():
        return None
    lines = [l for l in SNAP.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def _reset():
    for p in (SNAP, SUPP):
        if p.exists():
            p.unlink()
    bs._RELEASE_PROV.clear()


# ═══════════════════════════════1. the helper contract ══════════════════
_reset()
check("no mark -> take is None (a conventional fire carries no release)",
      bs._take_release_prov("A/USD") is None)

bs._mark_release("A/USD", "deep-veto")
check("mark -> take returns the eased gate's name",
      bs._take_release_prov("A/USD") == "deep-veto")
check("take is one-shot: the mark is consumed even on success",
      bs._take_release_prov("A/USD") is None)
check("marks are pair-keyed (another pair unaffected)",
      bs._take_release_prov("B/USD") is None)

# TTL: a stale mark never rides a fresh fire
_reset()
bs._RELEASE_PROV["STALE/USD"] = ("deep-veto", time.time() - bs.RELEASE_PROV_TTL_S - 5)
check("a mark past the TTL is dead (stale provenance cannot masquerade)",
      bs._take_release_prov("STALE/USD") is None)
check("the stale mark is still consumed (one-shot even when expired)",
      "STALE/USD" not in bs._RELEASE_PROV)
check("the TTL is the designed ten minutes", bs.RELEASE_PROV_TTL_S == 600)

# broken/stored junk fails soft to None
bs._RELEASE_PROV["JUNK/USD"] = ("deep-veto", "not-a-number")
check("a corrupted mark fails soft to None, never raises",
      bs._take_release_prov("JUNK/USD") is None)

# ═══════════════════════════════2. the void by burial ═══════════════════
_reset()
bs._mark_release("C/USD", "deep-veto")
bs._log_suppression("C/USD", "downward-trend",
                    "EMA9<EMA21 — a release past one gate is not a pass past all")
check("any burial of the pair voids the release mark",
      bs._take_release_prov("C/USD") is None)

# a burial on ANOTHER pair does not void
_reset()
bs._mark_release("D/USD", "tide-out")
bs._log_suppression("OTHER/USD", "deep-veto", "unrelated burial")
check("a burial of a different pair leaves the mark intact",
      bs._take_release_prov("D/USD") == "tide-out")

# ═══════════════════════════════3. the end-to-end row ═══════════════════
# the real chain: release decision -> popup fires -> row lands in the
# jsonl — exactly what the fired-row builders do with _take_release_prov
_reset()
bs._mark_release("REL/USD", "deep-veto")
row = {"pair": "REL/USD", "kind": "fired", "route": "champion",
       "release": bs._take_release_prov("REL/USD")}
bs._log_signal_snapshot(row)
rec = _last_snap()
check("released fire: the row carries release=<eased gate>",
      rec is not None and rec.get("release") == "deep-veto", rec)
check("released fire: the row is a normal fired row besides the field",
      rec.get("kind") == "fired" and rec.get("route") == "champion")

_reset()
row = {"pair": "CONV/USD", "kind": "fired", "route": "champion",
       "release": bs._take_release_prov("CONV/USD")}
bs._log_signal_snapshot(row)
rec = _last_snap()
check("conventional fire: the row carries release=None (never absent — "
      "the field is always auditable)",
      rec is not None and "release" in rec and rec["release"] is None, rec)

_reset()
# released, buried by the NEXT gate, then fired conventionally later
bs._mark_release("MIX/USD", "tide-out")
bs._log_suppression("MIX/USD", "deep-veto", "the deep scan still said no")
row = {"pair": "MIX/USD", "kind": "fired", "route": "mover",
       "release": bs._take_release_prov("MIX/USD")}
bs._log_signal_snapshot(row)
rec = _last_snap()
check("released-then-buried-then-fired: no provenance (the void held)",
      rec is not None and rec.get("release") is None, rec)

# ═══════════════════════════════4. the call-site wiring ═════════════════
src = Path(__file__).parent.joinpath("background_scanner.py") \
    .read_text(encoding="utf-8").replace("\r\n", "\n")
check("all three fired-row builders carry the release field",
      src.count('"release": _take_release_prov(pair)') == 3,
      src.count('"release": _take_release_prov(pair)'))
check("every release log line marks the provenance (desktop deep-veto, "
      "desktop tide-out, sure-thing tide-out, mover deep-veto)",
      src.count('_mark_release(pair, "deep-veto")') == 2
      and src.count('_mark_release(pair, "tide-out")') == 2,
      (src.count('_mark_release(pair, "deep-veto")'),
       src.count('_mark_release(pair, "tide-out")')))
# (Oct 8) scoped to each site's OWN log line: the stale-move release
# added a second "JOURNAL RELEASE (desktop)" EARLIER in the file, and a
# first-occurrence find paired the deep-veto mark with the WRONG log —
# the intent is mark-before-its-own-log, both sites
_dv_mark = src.find('_mark_release(pair, "deep-veto")')
_sm_mark = src.find('_mark_release(pair, "stale-move")')
check("the desktop release mark sits inside the released branch "
      "(before the JOURNAL RELEASE log)",
      _dv_mark != -1
      and 0 < _dv_mark < src.find("JOURNAL RELEASE (desktop)", _dv_mark)
      and _sm_mark != -1
      and 0 < _sm_mark < src.find("JOURNAL RELEASE (desktop)", _sm_mark))
check("each mark precedes its popup's snapshot write (mark -> fire order)",
      all(0 < src.find('_mark_release(pair, "%s")' % g) < i for g in
          ("deep-veto", "tide-out")
          for i in (src.find('"release": _take_release_prov(pair)'),)))
check("_log_suppression voids the mark (the burial gate)",
      (lambda _ls: _ls < src.find("_RELEASE_PROV.pop(str(pair), None)")
       < src.find("\ndef ", _ls + 1))(src.find("def _log_suppression(")))
check("the marker contract documents the one-shot honesty",
      "one-shot" in src and "RELEASE_PROV_TTL_S" in src)

# ═══════════════════════════════5. the label vocabulary ═════════════════
check("the eased-gate labels are journal gates, free of route names",
      all(g in ("deep-veto", "tide-out") for g in
          [m for m in ("deep-veto", "tide-out")]))

# restore runtime paths (the leak-audit sentinel's hand-back target)
bs.SIGNAL_SNAPSHOTS_FILE, bs.SUPPRESSION_LOG_FILE = _orig
_reset()

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
