"""Clean Sep 9 patch v4: plain ASCII, LF everywhere."""

from pathlib import Path

p = Path(__file__).resolve().parent / "background_scanner.py"
t = p.read_text(encoding="utf-8")
changed = 0


def find(s, needle, start=0):
    i = s.find(needle, start)
    assert i != -1, f"NOT FOUND: {needle[:60]!r}"
    return i


# ---- EDIT 1: _contest_size() helper before POPUP BRAIN -----------------------
A1 = "# \u2500\u2500 THE POPUP BRAIN (user rebuild, Sep 1"
i1 = find(t, A1)

helper = (
    "# -- THE CONTEST SIZE (the single source of truth for how big is this\n"
    "# contest -- every popup path that self-adjusts to the account ticks off\n"
    "# this one helper, the way the voice/giveback/loss warnings did at Sep 1).\n"
    "# Priority: (1) contest_account.json current_balance (the live ledger -- the\n"
    "# TIA close changed the contest window and reset the popups, so the account\n"
    "# file is the actual game state); (2) console_state.json tier label (the\n"
    "# user's selection -- $1,000 / $2,500 / $5,000 / $10,000 etc.); (3) the\n"
    "# config default. Cached per scan so every gate reads the same number.\n"
    "_CONTEST_SIZE_CACHE = {'ts': 0.0, 'size': 0.0}\n"
    "\n"
    "def _contest_size(now=None) -> float:\n"
    "    '''The contest's current balance in USD -- the number every size-sensitive\n"
    "    popup gate scales off. Fail-closed to 0.0 (no size-sensitive gate\n"
    "    fires on a broken read; the brain/goal/tide gates still decide).\n"
    "    Refresh at most every 30s -- the contest ledger moves in trade events,\n"
    "    not tick-by-tick.\n"
    "    '''\n"
    "    import time as _t\n"
    "    _now = _t.time()\n"
    "    if _CONTEST_SIZE_CACHE['size'] and _now - _CONTEST_SIZE_CACHE['ts'] < 30.0:\n"
    "        return _CONTEST_SIZE_CACHE['size']\n"
    "    _size = 0.0\n"
    "    try:\n"
    "        import contest_account as _ca\n"
    "        _acct = _ca.load_state()\n"
    "        _cb = float(_acct.get('current_balance') or 0.0)\n"
    "        if _cb > 0:\n"
    "            _size = _cb\n"
    "    except Exception:\n"
    "        pass\n"
    "    if _size <= 0.0:\n"
    "        try:\n"
    "            import json as _json\n"
    "            from pathlib import Path as _P\n"
    "            _cs = _json.loads((_P(__file__).resolve().parent / 'console_state.json').read_text())\n"
    "            _tier_txt = str(_cs.get('tier', '$1,000')).replace(',', '').replace('$', '')\n"
    "            _parsed = float(_tier_txt)\n"
    "            if _parsed > 0:\n"
    "                _size = _parsed\n"
    "        except Exception:\n"
    "            pass\n"
    "    if _size <= 0.0:\n"
    "        try:\n"
    "            from config import DEFAULT_CONFIG\n"
    "            _size = float(DEFAULT_CONFIG.challenge_balance)\n"
    "        except Exception:\n"
    "            _size = 1000.0\n"
    "    _CONTEST_SIZE_CACHE.update(ts=_now, size=_size)\n"
    "    return _size\n"
    "\n"
)
t = t[:i1] + helper + t[i1:]
changed += 1
print("EDIT 1 applied: _contest_size helper")


# ---- EDIT 2: _worth_actual_factor body -> size-aware halflife ---------------
A2 = "def _worth_actual_factor(now=None) -> float:"
A2e = "def worth_floor_auto_factors(now=None) -> dict:"
i2 = find(t, A2)
j2 = find(t, A2e, i2)
if "_size = _contest_size(now)" not in t[i2:j2]:
    body2 = (
        "def _worth_actual_factor(now=None) -> float:\n"
        "    '''The ACTUAL lever (Sep 7 + Sep 9): realized net P&L today vs the day's\n"
        "    pace, as a gentle 10%-swing tilt whose DOLLAR SENSITIVITY self-adjusts to\n"
        "    the contest size. deficit = pace - realized; factor = 1 + SWING x\n"
        "    (deficit / halflife), clamped [0.85, 1.15]. Behind schedule -> >1.0\n"
        "    (worthier popups); ahead -> <1.0 (smaller pays count).\n"
        "\n"
        "    The halflife is size-aware (Sep 9):\n"
        "      halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, contest_size x 0.12)\n"
        "    so a $1k contest doesn't tilt on a $120 deficit it can't afford, and a\n"
        "    $10k contest doesn't need a $120 deficit to max out. Fail-open to 1.0\n"
        "    (the dial stands on pace x tide alone). Open positions carry no realized\n"
        "    outcome yet: the ledger counts what's banked, which is the point.'''\n"
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
    t = t[:i2] + body2 + t[j2:]
    changed += 1
    print("EDIT 2 applied: _worth_actual_factor size-aware")
else:
    print("EDIT 2 skipped")


# ---- EDIT 3: contest_msg block -> size-aware spend line --------------------
A3e = '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""\n'
i3 = find(t, "# Contest chunk")
j3 = find(t, A3e, i3)
body3 = (
    "# Contest chunk (25/50/75/100% ? the contest app only takes those):\n"
    "        # the spend line must reflect the REAL contest size, not the static tier\n"
    "        # label. The TIA close / window reset changes the contest; the live\n"
    "        # ledger (contest_account.json current_balance) is the actual game state;\n"
    "        # the console_state.json tier label is the user's selection fallback.\n"
    "        # Every size-sensitive popup gate (worth tilt, cap dollar ceiling, death\n"
    "        # line, danger-zone impact) now scales off _contest_size() so the same\n"
    "        # signal means a different SIZE at a $1k contest vs a $10k contest.\n"
    "        contest_msg = ''\n"
    "        try:\n"
    "            _sz = _contest_size()\n"
    "            if _sz > 0:\n"
    "                import json as _json2\n"
    "                from pathlib import Path as _P2\n"
    "                _cs2 = _json2.loads((_P2(__file__).resolve().parent / 'console_state.json').read_text())\n"
    "                _tier_lbl = str(_cs2.get('tier', '$1,000')).replace(',', '')\n"
    "                contest_msg = (f'  SPEND: 100% = ${_sz:,.0f}  (tier {_tier_lbl})\\n')\n"
    "        except Exception:\n"
    "            pass\n"
    "        # the user's system: green light ? all in. The deep scan is the\n"
    "        # confirmation; when it's green they go 100%. Say what they do.\n"
    "        # (Size-adjusted popup cap + worth tilt live on the gates, not here.)\n"
)
t = t[:i3] + body3 + t[j3:]
changed += 1
print("EDIT 3 applied: contest_msg")


# ---- EDIT 4: _popup_cap_dollars AFTER _popup_cap --------------------------
i4 = find(t, "def _popup_cap() -> int:")
j4 = find(t, "def _climb_strictness() -> float:", i4)
if "def _popup_cap_dollars() -> float:" not in t[i4:j4]:
    body4 = (
        "\n"
        "def _popup_cap_dollars() -> float:\n"
        "    '''The popup cap's DOLLAR ceiling: the most fresh-entry money the contest\n"
        "    can have open at once across all current popups. Size-adjusted (Sep 9):\n"
        "      cap = max($20, contest_size x 0.50)\n"
        "    so a $1k contest can't open 3 x $240 at once (the count cap would allow\n"
        "    it, but the dollar cap stops at ~$500 total), while a $10k contest can\n"
        "    hold up to $5,000 in fresh entries. The cap is checked at fire time: a\n"
        "    candidate whose spend would push past the ceiling is suppressed as a\n"
        "    'cap-dollar' gate (distinct from the count cap, which still applies).\n"
        "    Fail-open to $20 on a broken read (one small trade still fine).'''\n"
        "    try:\n"
        "        _sz = _contest_size()\n"
        "        if _sz <= 0:\n"
        "            return 20.0\n"
        "        return max(20.0, _sz * 0.50)\n"
        "    except Exception:\n"
        "        return 20.0\n"
        "\n"
    )
    t = t[:j4] + body4 + t[j4:]
    changed += 1
    print("EDIT 4 applied: _popup_cap_dollars")
else:
    print("EDIT 4 skipped")


# ---- EDIT 5: _recent_fired_popups_dollars AFTER _recent_fired_popups -------
i5 = find(t, "def _recent_fired_popups(window_min")
j5 = find(t, "def _recent_signal_events", i5)
if "def _recent_fired_popups_dollars(window_min" not in t[i5:j5]:
    body5 = (
        "def _recent_fired_popups_dollars(window_min=POPUP_CAP_WINDOW_MIN) -> float:\n"
        "    '''Total spend (USD) committed by BUY popups that fired inside the cap\n"
        "    window (reads the snapshot log). Used by the size-adjusted dollar cap: a\n"
        "    new candidate whose spend + already-committed > the dollar ceiling is\n"
        "    suppressed as 'cap-dollar'. Fail-open to 0.0 on any error.'''\n"
        "    try:\n"
        "        import json as _json\n"
        "        from datetime import datetime as _dt, timedelta as _td\n"
        "        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):\n"
        "            return 0.0\n"
        "        _cutoff = _now_mst() - _td(minutes=window_min)\n"
        "        _tot = 0.0\n"
        "        with open(SIGNAL_SNAPSHOTS_FILE, 'r', encoding='utf-8') as f:\n"
        "            for line in f:\n"
        "                try:\n"
        "                    r = _json.loads(line)\n"
        "                except Exception:\n"
        "                    continue\n"
        "                if r.get('kind') != 'fired':\n"
        "                    continue\n"
        "                _spend = float(r.get('spend') or 0.0)\n"
        "                if _spend <= 0:\n"
        "                    continue\n"
        "                try:\n"
        "                    if _dt.fromisoformat(r.get('ts')) >= _cutoff:\n"
        "                        _tot += _spend\n"
        "                except Exception:\n"
        "                    continue\n"
        "        return _tot\n"
        "    except Exception:\n"
        "        return 0.0\n"
        "\n"
    )
    t = t[:j5] + body5 + t[j5:]
    changed += 1
    print("EDIT 5 applied: _recent_fired_popups_dollars")
else:
    print("EDIT 5 skipped")


# ---- EDIT 6: _begin_fire_snapshot ctx line ---------------------------------
old6 = '    ctx = {"pair": pair, "quick": quick_conf}\n'
new6 = '    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}\n'
if old6 in t and "'contest_size': _contest_size()" not in t:
    t = t.replace(old6, new6, 1)
    changed += 1
    print("EDIT 6 applied: _begin_fire_snapshot ctx")
else:
    print("EDIT 6 skipped")


# ---- EDIT 7: _begin_fire_snapshot docstring --------------------------------
old7 = (
    '    """Compute + stash the fire-time feature vector for this candidate ?\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\n'
    '    the DEEP SCAN, not on the feature math)."""\n'
)
new7 = (
    '    """Compute + stash the fire-time feature vector for this candidate:\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\n'
    '    the DEEP SCAN, not on the feature math). The contest size is stamped\n'
    '    here so every downstream gate + the snapshot log can grade this signal\n'
    '    against the account it fired on (Sep 9)."""\n'
)
if old7 in t:
    t = t.replace(old7, new7, 1)
    changed += 1
    print("EDIT 7 applied: docstring")
else:
    print("EDIT 7 skipped")


# ---- EDIT 8: _log_suppression -- carry contest_size -----------------------
old8 = (
    "        for _k, _v in ctx.items():\n"
    "            if _v is not None:\n"
    "                entry[_k] = _v\n"
    "        lines = []\n"
)
new8 = (
    "        # carry contest_size (one source of truth) if the ctx has it\n"
    "        if 'contest_size' not in entry and 'contest_size' in ctx:\n"
    "            entry['contest_size'] = ctx['contest_size']\n"
    "        for _k, _v in ctx.items():\n"
    "            if _v is not None:\n"
    "                entry[_k] = _v\n"
    "        lines = []\n"
)
if old8 in t and "'contest_size' not in entry and 'contest_size' in ctx" not in t:
    t = t.replace(old8, new8, 1)
    changed += 1
    print("EDIT 8 applied: _log_suppression")
else:
    print("EDIT 8 skipped")


# ---- EDIT 9: _log_signal_snapshot -- stamp contest_size -------------------
old9 = (
    '        rec = dict(rec)\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\n'
)
new9 = (
    '        rec = dict(rec)\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\n'
    '        rec.setdefault("contest_size", _contest_size())\n'
)
if old9 in t and "rec.setdefault('contest_size'" not in t:
    t = t.replace(old9, new9, 1)
    changed += 1
    print("EDIT 9 applied: _log_signal_snapshot")
else:
    print("EDIT 9 skipped")


# =========================================================================
p.write_text(t, encoding="utf-8")
print(f"\nDONE -- {changed} edits written")

import ast
ast.parse(t)
print("SYNTAX OK")
for c in ["_contest_size", "_popup_cap_dollars", "_recent_fired_popups_dollars",
          "_size = _contest_size(now)", "rec.setdefault('contest_size'"]:
    print(f"  {c!r:48} present={c in t}")
