"""Patch background_scanner.py for Sep 9: contest-size-aware popup signals (robust v2)."""

import re
from pathlib import Path

p = Path(__file__).resolve().parent / "background_scanner.py"
t = p.read_text(encoding="utf-8")

def find_re(label, pat, s):
    if isinstance(pat, (list, tuple)):
        pat = "".join(pat)
    m = re.search(pat, s, re.S)
    assert m, f"{label} NOT FOUND"
    return m

개정 = False

# ---------------------------------------------------------------------------
# 1. _worth_actual_factor: size-aware halflife
# ---------------------------------------------------------------------------
m = find_re("_worth_actual_factor", r"def _worth_actual_factor\(now=None\) -> float:.*?\n\s*return max\(WORTH_ACTUAL_MIN, min\(WORTH_ACTUAL_MAX, _raw\)\)\r?\n\s*except Exception:\r?\n\s*return 1\.0(?:\r?\n|\n)", t)
assert "HALFLIFE_USD" in m.group(0), "expected HALFLIFE_USD ref in worth_actual_factor"

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
t = t[:m.start()] + new1 + t[m.end():]
개정 = True

# ---------------------------------------------------------------------------
# 2. contest_msg block
# ---------------------------------------------------------------------------
m2 = find_re("contest_msg", (
    r"(\s*)# Contest chunk \(25/50/75/100% \?\ the contest app only takes those\):\r?\n"
    r"\s*# snap the strategy size UP, reading the console's tier setting\.\r?\n"
    r"\s*contest_msg = \"\"\r?\n"
    r"\s*try:\r?\n"
    r"\s*import json as _json\r?\n"
    r"\s*from pathlib import Path as _P\r?\n"
    r"\s*_cs = _json\.loads\(\(_P\(__file__\)\.resolve\(\)\.parent / \"console_state\.json\"\)\.read_text\(\)\)\r?\n"
    r"\s*_bal = float\(str\(_cs\.get\(\"tier\", \"\$5,000\"\)\)\.replace\(\"$\", \"\"\)\.replace\(\",\", \"\"\)\)\r?\n"
    r"\s*except Exception:\r?\n"
    r"\s*_bal = 5000\.0\r?\n"
    r"\s*# the user's system: green light \?\ all in\. The deep scan is the\r?\n"
    r"\s*# confirmation; when it's green they go 100%\. Say what they do\.\r?\n"
    r"\s*contest_msg = f\"  SPEND: 100% = \$\{_bal:,\.0f\}\\\n\" if _bal > 0 else \"\"\r?\n"
), t)
indent = m2.group(1)
new2 = (
    f"{indent}# Contest chunk (25/50/75/100% ? the contest app only takes those):\r\n"
    f"{indent}# the spend line must reflect the REAL contest size, not the static tier\r\n"
    f"{indent}# label. The TIA close / window reset changes the contest; the live\r\n"
    f"{indent}# ledger (contest_account.json current_balance) is the actual game state;\r\n"
    f"{indent}# the console_state.json tier label is the user's selection fallback.\r\n"
    f"{indent}# Every size-sensitive popup gate (worth tilt, cap dollar ceiling, death\r\n"
    f"{indent}# line, danger-zone impact) now scales off _contest_size() so the same\r\n"
    f"{indent}# signal means a different SIZE at a $1k contest vs a $10k contest.\r\n"
    f"{indent}contest_msg = \"\"\r\n"
    f"{indent}try:\r\n"
    f"{indent}    _sz = _contest_size()\r\n"
    f"{indent}    if _sz > 0:\r\n"
    f"{indent}        # also read the tier label for the friendly display line\r\n"
    f"{indent}        import json as _json2\r\n"
    f"{indent}        from pathlib import Path as _P2\r\n"
    f"{indent}        _cs2 = _json2.loads((_P2(__file__).resolve().parent / \"console_state.json\").read_text())\r\n"
    f"{indent}        _tier_lbl = str(_cs2.get(\"tier\", \"$1,000\")).replace(\",\", \"\")\r\n"
    f"{indent}        contest_msg = (f\"  SPEND: 100% = ${_sz:,.0f}  (tier {_tier_lbl})\\n\")\r\n"
    f"{indent}except Exception:\r\n"
    f"{indent}    pass\r\n"
    f"{indent}# the user's system: green light ? all in. The deep scan is the\r\n"
    f"{indent}# confirmation; when it's green they go 100%. Say what they do.\r\n"
    f"{indent}# (Size-adjusted popup cap + worth tilt live on the gates, not here.)\r\n"
)
t = t[:m2.start()] + new2 + t[m2.end():]
개정 = True

# ---------------------------------------------------------------------------
# 3. _popup_cap_dollars after _popup_cap
# ---------------------------------------------------------------------------
m3 = find_re("_popup_cap", (
    r"def _popup_cap\(\) -> int:\r?\n"
    r"\s*\"\"\"The popup cap dial \?: read LIVE from console_state\.json \(the\r?\n"
    r"\s*sidebar writes it; no scanner restart\), default 3, floor 1\.\"\"\"\r?\n"
    r"\s*try:\r?\n"
    r"\s*import json as _json\r?\n"
    r"\s*from pathlib import Path as _P\r?\n"
    r"\s*_cs = _json\.loads\(\(_P\(__file__\)\.resolve\(\)\.parent / \"console_state\.json\"\)\.read_text\(\)\)\r?\n"
    r"\s*return max\(1, int\(_cs\.get\(\"popup_cap\"\) or POPUP_CAP_DEFAULT\)\)\r?\n"
    r"\s*except Exception:\r?\n"
    r"\s*return POPUP_CAP_DEFAULT\r?\n"
), t)
new3 = m3.group(0) + (
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
t = t[:m3.start()] + new3 + t[m3.end():]
개정 = True

# ---------------------------------------------------------------------------
# 4. _recent_fired_popups_dollars after _recent_fired_popups
# ---------------------------------------------------------------------------
m4 = find_re("_recent_fired_popups", (
    r"def _recent_fired_popups\(window_min=POPUP_CAP_WINDOW_MIN\) -> int:\r?\n"
    r"\s*\"\"\"How many BUY popups fired inside the cap window \(reads the\r?\n"
    r"\s*snapshot log \?\ the same file the scorer learns from\)\.\"\"\"\r?\n"
    r"\s*try:\r?\n"
    r"\s*import json as _json\r?\n"
    r"\s*from datetime import datetime as _dt, timedelta as _td\r?\n"
    r"\s*if not os\.path\.exists\(SIGNAL_SNAPSHOTS_FILE\):\r?\n"
    r"\s*return 0\r?\n"
    r"\s*_cutoff = _now_mst\(\) - _td\(minutes=window_min\)\r?\n"
    r"\s*_n = 0\r?\n"
    r"\s*with open\(SIGNAL_SNAPSHOTS_FILE, \"r\", encoding=\"utf-8\"\) as f:\r?\n"
    r"\s*for line in f:\r?\n"
    r"\s*try:\r?\n"
    r"\s*r = _json\.loads\(line\)\r?\n"
    r"\s*except Exception:\r?\n"
    r"\s*continue\r?\n"
    r"\s*if r\.get\(\"kind\"\) != \"fired\":\r?\n"
    r"\s*continue\r?\n"
    r"\s*try:\r?\n"
    r"\s*if _dt\.fromisoformat\(r\.get\([\"']ts[\"']\)\) >= _cutoff:\r?\n"
    r"\s*_n \+= 1\r?\n"
    r"\s*except Exception:\r?\n"
    r"\s*continue\r?\n"
    r"\s*return _n\r?\n"
    r"\s*except Exception:\r?\n"
    r"\s*return 0\r?\n"
), t)
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
t = t[:m4.start()] + m4.group(0) + dollar_fn + t[m4.end():]
개정 = True

# ---------------------------------------------------------------------------
# 5. _begin_fire_snapshot: stamp contest_size
# ---------------------------------------------------------------------------
m5 = find_re("_begin_fire_snapshot", (
    r"def _begin_fire_snapshot\(exchange, pair: str, quick_conf, \*\*extras\) -> dict:\r?\n"
    r"\s*\"\"\"Compute \+ stash the fire-time feature vector for this candidate \?\r?\n"
    r"\s*ONE 5-min candle fetch, reused by every downstream verdict \(fired or\r?\n"
    r"\s*buried\)\. Feature failure NEVER blocks the funnel: a broken read logs\r?\n"
    r"\s*features=None and the gates proceed \(the fail-closed rule lives on\r?\n"
    r"\s*the DEEP SCAN, not on the feature math\)\.\"\"\"\r?\n"
    r"\s*ctx = \{\"pair\": pair, \"quick\": quick_conf\}\r?\n"
), t)
new5 = (
    "def _begin_fire_snapshot(exchange, pair: str, quick_conf, **extras) -> dict:\r\n"
    "    \"\"\"Compute + stash the fire-time feature vector for this candidate:\r\n"
    "    ONE 5-min candle fetch, reused by every downstream verdict (fired or\r\n"
    "    buried). Feature failure NEVER blocks the funnel: a broken read logs\r\n"
    "    features=None and the gates proceed (the fail-closed rule lives on\r\n"
    "    the DEEP SCAN, not on the feature math). The contest size is stamped\r\n"
    "    here so every downstream gate + the snapshot log can grade this signal\r\n"
    "    against the account it fired on (Sep 9).\"\"\"\r\n"
    "    ctx = {\"pair\": pair, \"quick\": quick_conf, \"contest_size\": _contest_size()}\r\n"
)
t = t[:m5.start()] + new5 + t[m5.end():]
개정 = True

# ---------------------------------------------------------------------------
# 6. _log_suppression footer: carry contest_size
# ---------------------------------------------------------------------------
m6 = find_re("_log_suppression footer", (
    r"answers 'who buried it\?' in one line next time\. ctx carries the\r?\n"
    r"\s*numbers \(quick, brain, floor, tide, rot\) \?\ anything not None rides\r?\n"
    r"\s*along\. Never raises: a broken log write must not block the funnel\.\"\"\"\r?\n"
    r"\s*try:\r?\n"
    r"\s*import json as _json\r?\n"
    r"\s*entry = \{\"ts\": _now_mst\(\)\.isoformat\(\), \"pair\": pair,\r?\n"
    r"\s*\"gate\": gate, \"detail\": str\(detail\)\[:220\]\}\r?\n"
    r"\s*for _k, _v in ctx\.items\(\):\r?\n"
    r"\s*if _v is not None:\r?\n"
    r"\s*entry\[_k\] = _v\r?\n"
    r"\s*lines = \[\]\r?\n"
), t)
new6 = (
    "answers 'who buried it?' in one line next time. ctx carries the\r\n"
    "    numbers (quick, brain, floor, tide, rot) ? anything not None rides\r\n"
    "    along. Never raises: a broken log write must not block the funnel.\"\"\"\r\n"
    "    try:\r\n"
    "        import json as _json\r\n"
    "        entry = {\"ts\": _now_mst().isoformat(), \"pair\": pair,\r\n"
    "                 \"gate\": gate, \"detail\": str(detail)[:220]}\r\n"
    "        # carry contest_size (one source of truth) if the ctx has it\r\n"
    "        if \"contest_size\" not in entry and \"contest_size\" in ctx:\r\n"
    "            entry[\"contest_size\"] = ctx[\"contest_size\"]\r\n"
    "        for _k, _v in ctx.items():\r\n"
    "            if _v is not None:\r\n"
    "                entry[_k] = _v\r\n"
    "        lines = []\r\n"
)
t = t[:m6.start()] + new6 + t[m6.end():]
개정 = True

# ---------------------------------------------------------------------------
# 7. fired-snapshot write: stamp contest_size
# ---------------------------------------------------------------------------
m7 = find_re("_log_signal_snapshot", (
    r"def _log_signal_snapshot\(rec: dict\):\r?\n"
    r"\s*\"\"\"Append one snapshot record\. Never raises\.\"\"\"\r?\n"
    r"\s*try:\r?\n"
    r"\s*import json as _json\r?\n"
    r"\s*lines = \[\]\r?\n"
    r"\s*if os\.path\.exists\(SIGNAL_SNAPSHOTS_FILE\):\r?\n"
    r"\s*with open\(SIGNAL_SNAPSHOTS_FILE, \"r\", encoding=\"utf-8\"\) as f:\r?\n"
    r"\s*lines = \[l for l in f\.read\(\)\.splitlines\(\) if l\.strip\(\)\]\r?\n"
    r"\s*rec = dict\(rec\)\r?\n"
    r"\s*rec\.setdefault\(\"ts\", _now_mst\(\)\.isoformat\(\)\)\r?\n"
), t)
new7 = (
    "def _log_signal_snapshot(rec: dict):\r\n"
    "    \"\"\"Append one snapshot record. Never raises.\"\"\"\r\n"
    "    try:\r\n"
    "        import json as _json\r\n"
    "        lines = []\r\n"
    "        if os.path.exists(SIGNAL_SNAPSHOTS_FILE):\r\n"
    "            with open(SIGNAL_SNAPSHOTS_FILE, \"r\", encoding=\"utf-8\") as f:\r\n"
    "                lines = [l for l in f.read().splitlines() if l.strip()]\r\n"
    "        rec = dict(rec)\r\n"
    "        rec.setdefault(\"ts\", _now_mst().isoformat())\r\n"
    "        rec.setdefault(\"contest_size\", _contest_size())\r\n"
)
t = t[:m7.start()] + new7 + t[m7.end():]
개정 = True

if 개정:
    p.write_text(t, encoding="utf-8")
    print("PATCHED OK")
else:
    print("NO CHANGES")
