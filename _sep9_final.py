"""Sep 9 final patch: insert missing pieces only.

State after earlier edits:
  - _contest_size() helper present (tool str_replace)
  - WORTH_ACTUAL_HALFLIFE_FRAC comment present (tool str_replace)
  - _worth_actual_factor body: MAY be size-aware already OR still old
  - contest_msg: still original (reads _bal from console_state tier)
  - _popup_cap_dollars: missing
  - _recent_fired_popups_dollars: missing
  - _begin_fire_snapshot contest_size stamp: missing
  - _log_suppression contest_size carry: missing
  - fired-snapshot contest_size stamp: missing
"""

from pathlib import Path
import re

p = Path(__file__).resolve().parent / "background_scanner.py"
t = p.read_text(encoding="utf-8")

LE = "\r\n" if "\r\n" in t else "\n"


def rx(s):
    return re.escape(s).replace(re.escape("\r\n"), r"\r?\n").replace(re.escape("\n"), r"\r?\n")


def find_block(start_anchor, end_anchor, s):
    pat = re.compile(rx(start_anchor) + r".*?" + rx(end_anchor), re.S)
    m = pat.search(s)
    assert m, f"block not found: {start_anchor[:50]!r} -> {end_anchor[:50]!r}"
    return m.start(), m.end()


def replace_block(s, start_anchor, end_anchor, new_body):
    i, j = find_block(start_anchor, end_anchor, s)
    return s[:i] + new_body + s[j:]


changed = 0


# ---- EDIT A: _worth_actual_factor body (size-aware halflife) --------------
if "WORTH_ACTUAL_HALFLIFE_FRAC" in t and "_size = _contest_size(now)" not in t:
    newA = (
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
    t = replace_block(t, "def _worth_actual_factor(now=None) -> float:", "def worth_floor_auto_factors(now=None) -> dict:", newA)
    changed += 1
    print("EDIT A applied: _worth_actual_factor size-aware")
else:
    print("EDIT A skipped")


# ---- EDIT B: contest_msg (size-aware spend line) -------------------------
if '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""' in t or \
   "contest_msg = f\"  SPEND: 100% = ${_bal:,.0f}\\n\" if _bal > 0 else \"\"" in t:
    newB = (
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
    t = replace_block(
        t,
        "# Contest chunk",
        '        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""',
        newB,
    )
    changed += 1
    print("EDIT B applied: contest_msg size-aware")
else:
    print("EDIT B skipped (already done?)")


# ---- EDIT C: _popup_cap_dollars after _popup_cap -------------------------
if "def _popup_cap_dollars() -> float:" not in t:
    iC = t.find("def _popup_cap() -> int:")
    jC = t.find("def _climb_strictness() -> float:", iC)
    assert iC != -1, "popup_cap not found"
    assert jC != -1, "climb_strictness not found after popup_cap"
    newC = LE + LE + (
        "def _popup_cap_dollars() -> float:\r\n"
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
    t = t[:jC] + newC + t[jC:]
    changed += 1
    print("EDIT C applied: _popup_cap_dollars")
else:
    print("EDIT C skipped")


# ---- EDIT D: _recent_fired_popups_dollars after _recent_fired_popups -----
if "def _recent_fired_popups_dollars(window_min" not in t:
    iD = t.find("def _recent_fired_popups(window_min")
    jD = t.find("def _recent_signal_events", iD)
    assert iD != -1 and jD != -1, "recent_fired_popups region not found"
    newD = (
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
    t = t[:jD] + newD + t[jD:]
    changed += 1
    print("EDIT D applied: _recent_fired_popups_dollars")
else:
    print("EDIT D skipped")


# ---- EDIT E: _begin_fire_snapshot stamps contest_size --------------------
oldE = '    ctx = {"pair": pair, "quick": quick_conf}\r\n'
newE = '    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}\r\n'
if oldE in t and '"contest_size": _contest_size()' not in t:
    t = t.replace(oldE, newE, 1)
    changed += 1
    print("EDIT E applied: _begin_fire_snapshot stamps contest_size")
else:
    print("EDIT E skipped")

oldDocE = (
    '    """Compute + stash the fire-time feature vector for this candidate ?\r\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
    '    the DEEP SCAN, not on the feature math)."""'
)
newDocE = (
    '    """Compute + stash the fire-time feature vector for this candidate:\r\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
    '    the DEEP SCAN, not on the feature math). The contest size is stamped\r\n'
    '    here so every downstream gate + the snapshot log can grade this signal\r\n'
    '    against the account it fired on (Sep 9)."""'
)
if oldDocE in t:
    t = t.replace(oldDocE, newDocE, 1)
    changed += 1
    print("EDIT E2 applied: _begin_fire_snapshot docstring")
else:
    print("EDIT E2 skipped")


# ---- EDIT F: _log_suppression carries contest_size -----------------------
suppOld = (
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
suppNew = (
    '        # carry contest_size (one source of truth) if the ctx has it\r\n'
    '        if "contest_size" not in entry and "contest_size" in ctx:\r\n'
    '            entry["contest_size"] = ctx["contest_size"]\r\n'
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
if suppOld in t and '"contest_size" not in entry and "contest_size" in ctx' not in t:
    t = t.replace(suppOld, suppNew, 1)
    changed += 1
    print("EDIT F applied: _log_suppression carries contest_size")
else:
    print("EDIT F skipped")


# ---- EDIT G: fired-snapshot stamps contest_size -------------------------
snapOld = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
)
snapNew = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
    '        rec.setdefault("contest_size", _contest_size())\r\n'
)
if snapOld in t and 'rec.setdefault("contest_size"' not in t:
    t = t.replace(snapOld, snapNew, 1)
    changed += 1
    print("EDIT G applied: fired-snapshot stamps contest_size")
else:
    print("EDIT G skipped")


if changed:
    p.write_text(t, encoding="utf-8")
    print(f"DONE — {changed} edits written")
else:
    print("DONE — no edits needed")
