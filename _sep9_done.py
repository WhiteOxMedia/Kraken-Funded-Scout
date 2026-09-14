"""Final Sep 9 patch: all edits with exact-match strings.

The file uses LF (\n) line endings (read_text gives LF; write_text stores
CRLF on Windows). Match strings use \n. Blank lines between statements
are preserved exactly as they appear in the file.
"""

from pathlib import Path

p = Path(__file__).resolve().parent / "background_scanner.py"
t = p.read_text(encoding="utf-8")

changed = 0


def replace_block(s, start, end, new_body):
    i = s.find(start)
    assert i != -1, f"start not found: {start[:50]!r}"
    j = s.find(end, i)
    assert j != -1, f"end not found: {end[:50]!r}"
    return s[:i] + new_body + s[j + len(end):]


# ---- EDIT 1: _worth_actual_factor body (size-aware halflife) --------------
if "_size = _contest_size(now)" not in t:
    new1 = (
        "def _worth_actual_factor(now=None) -> float:\n"
        "    \"\"\"The ACTUAL lever (Sep 7 + Sep 9): realized net P&L today vs the day's\n"
        "    pace, as a gentle 10%-swing tilt whose DOLLAR SENSITIVITY self-adjusts to\n"
        "    the contest size. deficit = pace - realized; factor = 1 + SWING x\n"
        "    (deficit / halflife), clamped [0.85, 1.15]. Behind schedule -> >1.0\n"
        "    (worthier popups); ahead -> <1.0 (smaller pays count).\n"
        "    \n"
        "    The halflife is size-aware (Sep 9):\n"
        "      halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, contest_size x 0.12)\n"
        "    so a $1k contest doesn't tilt on a $120 deficit it can't afford, and a\n"
        "    $10k contest doesn't need a $120 deficit to max out. Fail-open to 1.0\n"
        "    (the dial stands on pace x tide alone). Open positions carry no realized\n"
        "    outcome yet: the ledger counts what's banked, which is the point.\"\"\"\n"
        "    try:\n"
        "        import contest_account as _ca_a\n"
        "        _acct = _ca_a.load_state()\n"
        "        _pace = float(_ca_a.daily_pace_usd(_acct))\n"
        "        if _pace <= 0:\n"
        "            return 1.0\n"
        "        _real = float(_ca_a.realized_pnl_today(_acct, now=now))\n"
        "        _deficit = _pace - _real\n"
        "        _size = _contest_size(now)\n"
        "        _halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, _size * WORTH_ACTUAL_HALFLIFE_FRAC)\n"
        "        _raw = 1.0 + WORTH_ACTUAL_SWING * (_deficit / _halflife)\n"
        "        return max(WORTH_ACTUAL_MIN, min(WORTH_ACTUAL_MAX, _raw))\n"
        "    except Exception:\n"
        "        return 1.0\n"
        "\n"
    )
    t = replace_block(t, "def _worth_actual_factor(now=None) -> float:", "def worth_floor_auto_factors(now=None) -> dict:", new1)
    changed += 1
    print("EDIT 1 applied")
else:
    print("EDIT 1 skipped")


# ---- EDIT 2: contest_msg (size-aware spend line) -------------------------
old2_marker = '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""\n'
if old2_marker in t:
    new2 = (
        "# Contest chunk (25/50/75/100% ? the contest app only takes those):\n"
        "        # the spend line must reflect the REAL contest size, not the static tier\n"
        "        # label. The TIA close / window reset changes the contest; the live\n"
        "        # ledger (contest_account.json current_balance) is the actual game state;\n"
        "        # the console_state.json tier label is the user's selection fallback.\n"
        "        # Every size-sensitive popup gate (worth tilt, cap dollar ceiling, death\n"
        "        # line, danger-zone impact) now scales off _contest_size() so the same\n"
        "        # signal means a different SIZE at a $1k contest vs a $10k contest.\n"
        "        contest_msg = \"\"\n"
        "        try:\n"
        "            _sz = _contest_size()\n"
        "            if _sz > 0:\n"
        "                # also read the tier label for the friendly display line\n"
        "                import json as _json2\n"
        "                from pathlib import Path as _P2\n"
        "                _cs2 = _json2.loads((_P2(__file__).resolve().parent / \"console_state.json\").read_text())\n"
        "                _tier_lbl = str(_cs2.get(\"tier\", \"$1,000\")).replace(\",\", \"\")\n"
        "                contest_msg = (f\"  SPEND: 100% = ${_sz:,.0f}  (tier {_tier_lbl})\\n\")\n"
        "        except Exception:\n"
        "            pass\n"
        "        # the user's system: green light ? all in. The deep scan is the\n"
        "        # confirmation; when it's green they go 100%. Say what they do.\n"
        "        # (Size-adjusted popup cap + worth tilt live on the gates, not here.)\n"
    )
    t = replace_block(t, "# Contest chunk", old2_marker, new2)
    changed += 1
    print("EDIT 2 applied")
else:
    print("EDIT 2 skipped")


# ---- EDIT 3: _popup_cap_dollars after _popup_cap -------------------------
if "def _popup_cap_dollars() -> float:" not in t:
    i3 = t.find("def _popup_cap() -> int:")
    j3 = t.find("def _climb_strictness() -> float:", i3)
    assert i3 != -1 and j3 != -1
    new3 = "\n\n" + (
        "def _popup_cap_dollars() -> float:\n"
        "    \"\"\"The popup cap's DOLLAR ceiling: the most fresh-entry money the contest\n"
        "    can have open at once across all current popups. Size-adjusted (Sep 9):\n"
        "      cap = max($20, contest_size x 0.50)\n"
        "    so a $1k contest can't open 3 x $240 at once (the count cap would allow\n"
        "    it, but the dollar cap stops at ~$500 total), while a $10k contest can\n"
        "    hold up to $5,000 in fresh entries. The cap is checked at fire time: a\n"
        "    candidate whose spend would push past the ceiling is suppressed as a\n"
        "    'cap-dollar' gate (distinct from the count cap, which still applies).\n"
        "    Fail-open to $20 on a broken read (one small trade still fine).\"\"\"\n"
        "    try:\n"
        "        _sz = _contest_size()\n"
        "        if _sz <= 0:\n"
        "            return 20.0\n"
        "        return max(20.0, _sz * 0.50)\n"
        "    except Exception:\n"
        "        return 20.0\n"
    )
    t = t[:j3] + new3 + t[j3:]
    changed += 1
    print("EDIT 3 applied")
else:
    print("EDIT 3 skipped")


# ---- EDIT 4: _recent_fired_popups_dollars after _recent_fired_popups -----
if "def _recent_fired_popups_dollars(window_min" not in t:
    i4 = t.find("def _recent_fired_popups(window_min")
    j4 = t.find("def _recent_signal_events", i4)
    assert i4 != -1 and j4 != -1
    new4 = (
        "def _recent_fired_popups_dollars(window_min=POPUP_CAP_WINDOW_MIN) -> float:\n"
        "    \"\"\"Total spend (USD) committed by BUY popups that fired inside the cap\n"
        "    window (reads the snapshot log). Used by the size-adjusted dollar cap: a\n"
        "    new candidate whose spend + already-committed > the dollar ceiling is\n"
        "    suppressed as 'cap-dollar'. Fail-open to 0.0 on any error.\"\"\"\n"
        "    try:\n"
        "        import json as _json\n"
        "        from datetime import datetime as _dt, timedelta as _td\n"
        "        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):\n"
        "            return 0.0\n"
        "        _cutoff = _now_mst() - _td(minutes=window_min)\n"
        "        _tot = 0.0\n"
        "        with open(SIGNAL_SNAPSHOTS_FILE, \"r\", encoding=\"utf-8\") as f:\n"
        "            for line in f:\n"
        "                try:\n"
        "                    r = _json.loads(line)\n"
        "                except Exception:\n"
        "                    continue\n"
        "                if r.get(\"kind\") != \"fired\":\n"
        "                    continue\n"
        "                _spend = float(r.get(\"spend\") or 0.0)\n"
        "                if _spend <= 0:\n"
        "                    continue\n"
        "                try:\n"
        "                    if _dt.fromisoformat(r.get(\"ts\")) >= _cutoff:\n"
        "                        _tot += _spend\n"
        "                except Exception:\n"
        "                    continue\n"
        "        return _tot\n"
        "    except Exception:\n"
        "        return 0.0\n"
    )
    t = t[:j4] + new4 + t[j4:]
    changed += 1
    print("EDIT 4 applied")
else:
    print("EDIT 4 skipped")


# ---- EDIT 5: _begin_fire_snapshot stamps contest_size --------------------
old5 = '    ctx = {"pair": pair, "quick": quick_conf}\n'
new5 = '    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}\n'
if old5 in t and '"contest_size": _contest_size()' not in t:
    t = t.replace(old5, new5, 1)
    changed += 1
    print("EDIT 5 applied")
else:
    print("EDIT 5 skipped")

oldDoc5 = (
    '    """Compute + stash the fire-time feature vector for this candidate ?\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\n'
    '    the DEEP SCAN, not on the feature math)."""'
)
newDoc5 = (
    '    """Compute + stash the fire-time feature vector for this candidate:\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\n'
    '    the DEEP SCAN, not on the feature math). The contest size is stamped\n'
    '    here so every downstream gate + the snapshot log can grade this signal\n'
    '    against the account it fired on (Sep 9)."""'
)
if oldDoc5 in t:
    t = t.replace(oldDoc5, newDoc5, 1)
    changed += 1
    print("EDIT 5b applied")
else:
    print("EDIT 5b skipped")


# ---- EDIT 6: _log_suppression carries contest_size -----------------------
# Exact strings (blank lines between statements as they appear in file).
suppOld = (
    '        for _k, _v in ctx.items():\n\n'
    '            if _v is not None:\n\n'
    '                entry[_k] = _v\n\n'
    '        lines = []'
)
suppNew = (
    '        # carry contest_size (one source of truth) if the ctx has it\n\n'
    '        if "contest_size" not in entry and "contest_size" in ctx:\n\n'
    '            entry["contest_size"] = ctx["contest_size"]\n\n'
    '        for _k, _v in ctx.items():\n\n'
    '            if _v is not None:\n\n'
    '                entry[_k] = _v\n\n'
    '        lines = []'
)
if suppOld in t:
    t = t.replace(suppOld, suppNew, 1)
    changed += 1
    print("EDIT 6 applied")
else:
    print("EDIT 6 skipped (not found)")


# ---- EDIT 7: fired-snapshot stamps contest_size -------------------------
snapOld = (
    '        rec = dict(rec)\n\n'
    '        rec.setdefault("ts", _now_mst().isoformat())'
)
snapNew = (
    '        rec = dict(rec)\n\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\n'
    '        rec.setdefault("contest_size", _contest_size())'
)
if snapOld in t:
    t = t.replace(snapOld, snapNew, 1)
    changed += 1
    print("EDIT 7 applied")
else:
    print("EDIT 7 skipped (not found)")


if changed:
    p.write_text(t, encoding="utf-8")
    print(f"DONE -- {changed} edits written")
else:
    print("DONE -- no edits needed")
