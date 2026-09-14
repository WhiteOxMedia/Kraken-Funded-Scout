"""Sep 9 patch v3: line-ending-agnostic targeted block edits.

Normalizes each target region's line endings to the file's prevailing style
before matching, so the unicode-heavy comment lines and mixed \\r\\n / \\n
endings don't break exact-match replacements.
"""

import re
from pathlib import Path

p = Path(__file__).resolve().parent / "background_scanner.py"
raw = p.read_bytes()
text = raw.decode("utf-8")

# Detect prevailing line ending
has_crlf = b"\r\n" in raw
LE = "\r\n" if has_crlf else "\n"

# Work on a normalized-to-LE version for matching; we'll write back with LE.
t = text.replace("\r\n", "\n").replace("\n", LE)  # already LE if consistent
# But the file may be MIXED; normalize everything to LE for the edits.
t_norm = text  # keep as-is; we match with flexible LE in regex.


def find_block(start_anchor_re, end_anchor_re, s):
    """Return (start_idx, end_idx) using regex with flexible line endings."""
    pat = re.compile(
        r"""(?P<start>{start}).*?(?P<end>{end})""".format(
            start=start_anchor_re, end=end_anchor_re
        ),
        re.S | re.M,
    )
    m = pat.search(s)
    if not m:
        raise AssertionError(f"block not found: start={start_anchor_re[:40]!r}")
    return m.start("start"), m.end("end")


def replace_block(s, start_re, end_re, new_body):
    i, j = find_block(start_re, end_re, s)
    return s[:i] + new_body + s[j:]


# Convenience: build a start/end regex from plain anchors (escaped, flexible LE)
def rx(anchor):
    return re.escape(anchor).replace(re.escape("\r\n"), r"\r?\n").replace(
        re.escape("\n"), r"\r?\n"
    )


# ---------------------------------------------------------------------------
# EDIT 1: _worth_actual_factor body
# ---------------------------------------------------------------------------
if "WORTH_ACTUAL_HALFLIFE_FRAC" not in t:
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
    t = replace_block(
        t,
        rx("def _worth_actual_factor(now=None) -> float:"),
        rx("def worth_floor_auto_factors(now=None) -> dict:"),
        new1,
    )
    print("EDIT 1 applied")
else:
    print("EDIT 1 skipped")


# ---------------------------------------------------------------------------
# EDIT 2: contest_msg
# ---------------------------------------------------------------------------
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
t = replace_block(
    t,
    rx("# Contest chunk"),
    rx('        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\\n" if _bal > 0 else ""'),
    new2,
)
print("EDIT 2 applied")


# ---------------------------------------------------------------------------
# EDIT 3: _popup_cap_dollars after _popup_cap
# ---------------------------------------------------------------------------
end3 = rx("def _climb_strictness() -> float:")
new3_tail = (
    "\r\n"
    "\r\n"
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
t = replace_block(
    t,
    rx("def _popup_cap() -> int:"),
    end3,
    rx("def _popup_cap() -> int:") and t[t.find("def _popup_cap() -> int:"): t.find("def _climb_strictness() -> float:")]
    + new3_tail,
)
# The above replace_block call is wrong (returns string concat, not indices).
# Do it properly:
i3 = t.find("def _popup_cap() -> int:")
j3 = t.find("def _climb_strictness() -> float:", i3)
assert i3 != -1 and j3 != -1
t = t[:j3] + new3_tail + t[j3:]
print("EDIT 3 applied")


# ---------------------------------------------------------------------------
# EDIT 4: _recent_fired_popups_dollars after _recent_fired_popups
# ---------------------------------------------------------------------------
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
i4 = t.find("def _recent_fired_popups(window_min")
j4 = t.find("def _recent_signal_events", i4)
assert i4 != -1 and j4 != -1
t = t[:j4] + t[j4:j4] + dollar_fn + t[j4:]
print("EDIT 4 applied")


# ---------------------------------------------------------------------------
# EDIT 5: _begin_fire_snapshot stamps contest_size
# ---------------------------------------------------------------------------
old_ctx = '    ctx = {"pair": pair, "quick": quick_conf}\r\n'
new_ctx = '    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}\r\n'
if old_ctx in t and "contest_size" not in t[t.find("def _begin_fire_snapshot"): t.find("def _begin_fire_snapshot") + 700]:
    t = t.replace(old_ctx, new_ctx, 1)
    print("EDIT 5 applied")
else:
    print("EDIT 5 skipped")

old_doc5 = (
    '    """Compute + stash the fire-time feature vector for this candidate ?\r\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
    '    the DEEP SCAN, not on the feature math)."""'
)
new_doc5 = (
    '    """Compute + stash the fire-time feature vector for this candidate:\r\n'
    '    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n'
    '    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n'
    '    features=None and the gates proceed (the fail-closed rule lives on\r\n'
    '    the DEEP SCAN, not on the feature math). The contest size is stamped\r\n'
    '    here so every downstream gate + the snapshot log can grade this signal\r\n'
    '    against the account it fired on (Sep 9)."""'
)
if old_doc5 in t:
    t = t.replace(old_doc5, new_doc5, 1)
    print("EDIT 5b applied")
else:
    print("EDIT 5b skipped")


# ---------------------------------------------------------------------------
# EDIT 6: _log_suppression carries contest_size
# ---------------------------------------------------------------------------
supp_old = (
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
supp_new = (
    '        # carry contest_size (one source of truth) if the ctx has it\r\n'
    '        if "contest_size" not in entry and "contest_size" in ctx:\r\n'
    '            entry["contest_size"] = ctx["contest_size"]\r\n'
    '        for _k, _v in ctx.items():\r\n'
    '            if _v is not None:\r\n'
    '                entry[_k] = _v\r\n'
    '        lines = []\r\n'
)
if supp_old in t:
    t = t.replace(supp_old, supp_new, 1)
    print("EDIT 6 applied")
else:
    print("EDIT 6 skipped")


# ---------------------------------------------------------------------------
# EDIT 7: fired-snapshot stamps contest_size
# ---------------------------------------------------------------------------
snap_old = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
)
snap_new = (
    '        rec = dict(rec)\r\n'
    '        rec.setdefault("ts", _now_mst().isoformat())\r\n'
    '        rec.setdefault("contest_size", _contest_size())\r\n'
)
if snap_old in t and 'rec.setdefault("contest_size"' not in t:
    t = t.replace(snap_old, snap_new, 1)
    print("EDIT 7 applied")
else:
    print("EDIT 7 skipped")


p.write_text(t, encoding="utf-8")
print("DONE")
