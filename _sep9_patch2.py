"""Targeted Sep 9 patch: contest-size-aware popup signals.

For each edit: locate the block by anchor substrings present in the file,
verify the exact old substring is there, replace with the new.

This avoids regex entirely, which keeps the unicode-heavy comment lines
stable across the file's mixed line endings.
"""

from pathlib import Path

p = Path(__file__).resolve().parent / "background_scanner.py"
t = p.read_text(encoding="utf-8")


def anchor_slice(s, start_anchor, end_anchor, before_end=0):
    """Return (start_idx, end_idx) spanning [start_anchor...end_anchor+before_end)."""
    i = s.find(start_anchor)
    assert i != -1, f"start anchor not found: {start_anchor[:60]!r}"
    j = s.find(end_anchor, i)
    assert j != -1, f"end anchor not found after start: {end_anchor[:60]!r}"
    return i, j + before_end


# ---------------------------------------------------------------------------
# EDIT 1 (already applied by _sep9_patch.py step 1? check):
#   _worth_actual_factor body -> size-aware halflife
# ---------------------------------------------------------------------------
WA_START = "def _worth_actual_factor(now=None) -> float:"
WA_END = "\n\ndef worth_floor_auto_factors(now=None) -> dict:"

if WA_START in t and "WORTH_ACTUAL_HALFLIFE_FRAC" not in t[t.find(WA_START):t.find(WA_END)]:
    i, j = anchor_slice(t, WA_START, WA_END)
    new1 = (
        "def _worth_actual_factor(now=None) -> float:\r\n"
        "    \"\"\"The ACTUAL lever (Sep 7 + Sep 9): realized net P&L today vs the day's\r\n"
        "    pace, as a gentle 10%-swing tilt whose DOLLAR SENSITIVITY self-adjusts to\r\n"
        "    the contest size. deficit = pace - realized; factor = 1 + SWING x\r\n"
        "    (deficit / halflife), clamped [0.85, 1.15]. Behind schedule -> >1.0\r\n"
        "    (worthier popups); ahead -> <1.0 (smaller pays count).\r\n"
        "    \r\n"
        "    The halflife is size-aware (Sep 9):\r\n"
        "      halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, contest_size x 0.12)\r\n"
        "    so a $1k contest doesn't tilt on a $120 deficit it can't afford, and a\r\n"
        "    $10k contest doesn't need a $120 deficit to max out. Fail-open to 1.0\r\n"
        "    (the dial stands on pace x tide alone). Open positions carry no realized\r\n"
        "    outcome yet: the ledger counts what's banked, which is the point.\"\"\"\r\n"
        "    try:\r\n"
        "        import contest_account as _ca_a\r\n"
        "        _acct = _ca_a.load_state()\r\n"
        "        _pace = float(_ca_a.daily_pace_usd(_acct))\r\n"
        "        if _pace <= 0:\r\n"
        "            return 1.0\r\n"
        "        _real = float(_ca_a.realized_pnl_today(_acct, now=now))\r\n"
        "        _deficit = _pace - _real\r\n"
        "        _size = _contest_size(now)\r\n"
        "        _halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, _size * WORTH_ACTUAL_HALFLIFE_FRAC)\r\n"
        "        _raw = 1.0 + WORTH_ACTUAL_SWING * (_deficit / _halflife)\r\n"
        "        return max(WORTH_ACTUAL_MIN, min(WORTH_ACTUAL_MAX, _raw))\r\n"
        "    except Exception:\r\n"
        "        return 1.0\r\n"
        "\r\n"
    )
    t = t[:i] + new1 + t[j:]
    print("EDIT 1 applied: _worth_actual_factor size-aware")
else:
    print("EDIT 1 skipped (already done or HALFLIFE_FRAC present)")


# ---------------------------------------------------------------------------
# EDIT 2: contest_msg block
# ---------------------------------------------------------------------------
CM_START = "# Contest chunk"
CM_END = '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""\r\n'
CM_END2 = '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""\n'

def find_cm_end(s):
    j = s.find(CM_END)
    if j != -1:
        return j, len(CM_END)
    j = s.find(CM_END2)
    if j != -1:
        return j, len(CM_END2)
    raise AssertionError("contest_msg end line not found")

if CM_END in t:
    i, j = anchor_slice(t, CM_START, CM_END)
    end_len = len(CM_END)
else:
    i, j = anchor_slice(t, CM_START, CM_END2)
    end_len = len(CM_END2)

old_cm = t[i:j]
# sanity: the old block should contain the tier-parsing lines
assert '_cs.get("tier"' in old_cm or "tier" in old_cm, "unexpected contest_msg block content"

new2 = (
    "# Contest chunk (25/50/75/100% ? the contest app only takes those):\r\n"
    "        # the spend line must reflect the REAL contest size, not the static tier\r\n"
    "        # label. The TIA close / window reset changes the contest; the live\r\n"
    "        # ledger (contest_account.json current_balance) is the actual game state;\r\n"
    "        # the console_state.json tier label is the user's selection fallback.\r\n"
    "        # Every size-sensitive popup gate (worth tilt, cap dollar ceiling, death\r\n"
    "        # line, danger-zone impact) now scales off _contest_size() so the same\r\n"
    "        # signal means a different SIZE at a $1k contest vs a $10k contest.\r\n"
    "        contest_msg = \"\"\r\n"
    "        try:\r\n"
    "            _sz = _contest_size()\r\n"
    "            if _sz > 0:\r\n"
    "                # also read the tier label for the friendly display line\r\n"
    "                import json as _json2\r\n"
    "                from pathlib import Path as _P2\r\n"
    "                _cs2 = _json2.loads((_P2(__file__).resolve().parent / \"console_state.json\").read_text())\r\n"
    "                _tier_lbl = str(_cs2.get(\"tier\", \"$1,000\")).replace(\",\", \"\")\r\n"
    "                contest_msg = (f\"  SPEND: 100% = ${_sz:,.0f}  (tier {_tier_lbl})\\n\")\r\n"
    "        except Exception:\r\n"
    "            pass\r\n"
    "        # the user's system: green light ? all in. The deep scan is the\r\n"
    "        # confirmation; when it's green they go 100%. Say what they do.\r\n"
    "        # (Size-adjusted popup cap + worth tilt live on the gates, not here.)\r\n"
)
# Normalize line endings of new2 to match surrounding (restore original newline style)
sep = "\r\n" if "\r\n" in old_cm[:20] else "\n"
new2 = new2.replace("\r\n", sep).replace("\n", sep)

t = t[:i] + new2 + t[j:]
print("EDIT 2 applied: contest_msg size-aware")


# ---------------------------------------------------------------------------
# EDIT 3: _popup_cap_dollars after _popup_cap
# ---------------------------------------------------------------------------
PC_END = "        return POPUP_CAP_DEFAULT\r\n"
i3, j3 = anchor_slice(t, "def _popup_cap() -> int:", PC_END, before_end=len(PC_END))
assert "def _popup_cap_dollars" not in t[j3:j3+60], "popup_cap_dollars already present?"
new3 = (
    t[i3:j3]
    + "\r\n"
    + "\r\n"
    + "def _popup_cap_dollars() -> float:\r\n"
    "    \"\"\"The popup cap's DOLLAR ceiling: the most fresh-entry money the contest\r\n"
    "    can have open at once across all current popups. Size-adjusted (Sep 9):\r\n"
    "      cap = max($20, contest_size x 0.50)\r\n"
    "    so a $1k contest can't open 3 x $240 at once (the count cap would allow\r\n"
    "    it, but the dollar cap stops at ~$500 total), while a $10k contest can\r\n"
    "    hold up to $5,000 in fresh entries. The cap is checked at fire time: a\r\n"
    "    candidate whose spend would push past the ceiling is suppressed as a\r\n"
    "    'cap-dollar' gate (distinct from the count cap, which still applies).\r\n"
    "    Fail-open to $20 on a broken read (one small trade still fine).\"\"\"\r\n"
    "    try:\r\n"
    "        _sz = _contest_size()\r\n"
    "        if _sz <= 0:\r\n"
    "            return 20.0\r\n"
    "        return max(20.0, _sz * 0.50)\r\n"
    "    except Exception:\r\n"
    "        return 20.0\r\n"
)
t = t[:j3] + new3 + t[j3:]
print("EDIT 3 applied: _popup_cap_dollars")


# ---------------------------------------------------------------------------
# EDIT 4: _recent_fired_popups_dollars after _recent_fired_popups
# ---------------------------------------------------------------------------
RFP_END = "        return 0\r\n"
i4, j4 = anchor_slice(t, "def _recent_fired_popups(window_min", RFP_END, before_end=len(RFP_END))
assert "def _recent_fired_popups_dollars" not in t[j4:j4+60], "dollars fn already present?"
dollar_fn = (
    "def _recent_fired_popups_dollars(window_min=POPUP_CAP_WINDOW_MIN) -> float:\r\n"
    "    \"\"\"Total spend (USD) committed by BUY popups that fired inside the cap\r\n"
    "    window (reads the snapshot log). Used by the size-adjusted dollar cap: a\r\n"
    "    new candidate whose spend + already-committed > the dollar ceiling is\r\n"
    "    suppressed as 'cap-dollar'. Fail-open to 0.0 on any error.\"\"\"\r\n"
    "    try:\r\n"
    "        import json as _json\r\n"
    "        from datetime import datetime as _dt, timedelta as _td\r\n"
    "        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):\r\n"
    "            return 0.0\r\n"
    "        _cutoff = _now_mst() - _td(minutes=window_min)\r\n"
    "        _tot = 0.0\r\n"
    "        with open(SIGNAL_SNAPSHOTS_FILE, \"r\", encoding=\"utf-8\") as f:\r\n"
    "            for line in f:\r\n"
    "                try:\r\n"
    "                    r = _json.loads(line)\r\n"
    "                except Exception:\r\n"
    "                    continue\r\n"
    "                if r.get(\"kind\") != \"fired\":\r\n"
    "                    continue\r\n"
    "                _spend = float(r.get(\"spend\") or 0.0)\r\n"
    "                if _spend <= 0:\r\n"
    "                    continue\r\n"
    "                try:\r\n"
    "                    if _dt.fromisoformat(r.get(\"ts\")) >= _cutoff:\r\n"
    "                        _tot += _spend\r\n"
    "                except Exception:\r\n"
    "                    continue\r\n"
    "        return _tot\r\n"
    "    except Exception:\r\n"
    "        return 0.0\r\n"
)
t = t[:j4] + t[j4:j4] + dollar_fn + t[j4:]
print("EDIT 4 applied: _recent_fired_popups_dollars")


# ---------------------------------------------------------------------------
# EDIT 5: _begin_fire_snapshot stamps contest_size
# ---------------------------------------------------------------------------
BFS_START = "def _begin_fire_snapshot(exchange, pair: str, quick_conf, **extras) -> dict:\r\n"
BFS_CTX_OLD = '    ctx = {"pair": pair, "quick": quick_conf}\r\n'
BFS_CTX_NEW = '    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}\r\n'
if BFS_CTX_OLD in t and "contest_size" not in t[t.find(BFS_START):t.find(BFS_START)+600]:
    assert BFS_CTX_OLD in t
    t = t.replace(BFS_CTX_OLD, BFS_CTX_NEW, 1)
    print("EDIT 5 applied: _begin_fire_snapshot stamps contest_size")
else:
    print("EDIT 5 skipped")

# update the docstring too
old_doc = ('    """Compute + stash the fire-time feature vector for this candidate ?\r\n'
           '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
           '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
           '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
           '    the DEEP SCAN, not on the feature math)."""')
new_doc = ('    """Compute + stash the fire-time feature vector for this candidate:\r\n'
           '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
           '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
           '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
           '    the DEEP SCAN, not on the feature math). The contest size is stamped\r\n'
           '    here so every downstream gate + the snapshot log can grade this signal\r\n'
           '    against the account it fired on (Sep 9)."""')
if old_doc in t:
    t = t.replace(old_doc, new_doc, 1)
    print("EDIT 5b applied: _begin_fire_snapshot docstring")
else:
    print("EDIT 5b skipped (docstring already updated or not matched)")


# ---------------------------------------------------------------------------
# EDIT 6: _log_suppression footer carries contest_size
# ---------------------------------------------------------------------------
SUPP_OLD = (
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
SUPP_NEW = (
    '        # carry contest_size (one source of truth) if the ctx has it\r\n'
    '        if "contest_size" not in entry and "contest_size" in ctx:\r\n'
    '            entry["contest_size"] = ctx["contest_size"]\r\n'
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
if SUPP_OLD in t:
    t = t.replace(SUPP_OLD, SUPP_NEW, 1)
    print("EDIT 6 applied: _log_suppression carries contest_size")
else:
    print("EDIT 6 skipped (suppression footer not matched)")


# ---------------------------------------------------------------------------
# EDIT 7: fired-snapshot write stamps contest_size
# ---------------------------------------------------------------------------
SNAP_OLD = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
)
SNAP_NEW = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
    '        rec.setdefault("contest_size", _contest_size())\r\n'
)
if SNAP_OLD in t and 'rec.setdefault("contest_size"' not in t:
    t = t.replace(SNAP_OLD, SNAP_NEW, 1)
    print("EDIT 7 applied: fired-snapshot stamps contest_size")
else:
    print("EDIT 7 skipped (snapshot setdefault already present or not matched)")


p.write_text(t, encoding="utf-8")
print("DONE")
