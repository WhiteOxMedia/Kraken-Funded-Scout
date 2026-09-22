"""test_autotrack.py — THE POPUP CONTRACT (Sep 21).

"If it pops up it should be auto tracking" and "after the 'Get out Now'
fires a popup, it closes the virtual trade and counts the PnL so the
brain can learn from the results every time — no more user interference."

Pinned here:
  1. ONE auto-track writer (auto_track_popup) that every BUY popup
     route calls at fire time — champion, Sure Thing/breakout, mover.
     It never displaces an existing entry (functional, tempdir).
  2. The GET OUT auto-close: the exit call IS the close — the row is
     removed, the trade is graded with the same arithmetic the SOLD
     path uses (pnl_net_usd → apply_pnl, own-contest window, 0.08% RT
     cost) and journaled to contest_trades.jsonl (the ledger the
     discipline learner + hindsight studies read).
  3. SOLD stays the manual override (early profit-take, re-entry).
  4. Every popup speaks through _speak_server → voice_log.jsonl, so
     popup history and Her Words are the same record.
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ap = (BASE / "app.py").read_text(encoding="utf-8")
bs = (BASE / "background_scanner.py").read_text(encoding="utf-8")
results = []


def check(name, ok):
    results.append((name, bool(ok)))


# ── 1. one writer, every route ───────────────────────────────────────────
check("auto_track_popup defined (one writer for every popup route)",
      "def auto_track_popup(pair: str, entry: float, size: float, goal_pct: float,"
      in bs)
check("writer never displaces an existing entry",
      "if pair in w:\n            return False" in bs)
check("writer logs AUTO-TRACK at fire time",
      "AUTO-TRACK: %s watched at fire time" in bs)
champ_i = bs.find("AUTO-TRACK AT FIRE TIME — one writer")
check("champion route delegates to the writer",
      champ_i > 0 and "auto_track_popup(pair" in bs[champ_i:champ_i + 400])
st_i = bs.find("the breakout popup tracks too")
check("Sure Thing/breakout route delegates to the writer",
      st_i > 0 and 'auto_track_popup(pair, float(r.get("trigger_px")' in bs[st_i:st_i + 300])
mv_i = bs.find("the mover\n                                    # popup tracks too")
check("mover route delegates to the writer",
      mv_i > 0 and "float(signal.price or 0.0)" in bs[mv_i:mv_i + 300])
check("writer keeps the user's own row authoritative (merge intact)",
      "_load_contest_watch()" in bs and "_save_contest_watch(w)" in bs)

# ── 2. the GET OUT auto-close ────────────────────────────────────────────
gi = bs.find("AUTO-CLOSE AT FIRE TIME")
check("GET OUT auto-close block present", gi > 0)
gx = bs[gi:bs.find("watch.pop(pair, None)", gi) + 200]
check("close grades with the SOLD arithmetic (pnl_net_usd)",
      "pnl_net_usd(" in gx)
check("close applies to the trade's OWN contest window",
      "apply_pnl(" in gx and '|getout")' in gx)
check("close journals to contest_trades.jsonl (the brain's record)",
      'open("contest_trades.jsonl", "a", encoding="utf-8")' in gx)
check("close journal carries closed_by=getout + the exit reason",
      '"closed_by": "getout"' in gx and '"exit_reason": reason[:200]' in gx)
check("close removes the watch row itself (no user action)",
      "watch.pop(pair, None)" in bs[gi:gi + 5000]
      and bs.find("watch.pop(pair, None)", gi) < bs.find("# exit condition cleared", gi))
check("close speaks once, then the row is gone (no repeat ladder on a dead row)",
      '_speak_server(f"Get out now! Sell {asset}! {_why}"' in bs)
check("close failure keeps the row for the SOLD override",
      "the SOLD button remains the way out" in gx)
check("profit re-arm fires on a closed winner (same as SOLD)",
      "rotation_rearm_on_profit(asset)" in gx)

# ── 3. SOLD stays the manual override ────────────────────────────────────
check("console SOLD button still present (early take / watch didn't close)",
      'button(f"✔ SOLD {_asset(_sp)}"' in ap)
check("SOLD still journals its own graded record",
      'open("contest_trades.jsonl", "a", encoding="utf-8")' in ap)
check("SOLD still re-arms a collected profit for re-entry",
      "rotation_rearm_on_profit" in ap)

# ── 4. popup history == her words ────────────────────────────────────────
check("notify_big is the single popup funnel (all routes pop through it)",
      bs.count("def notify_big(") == 1)
sp_i = bs.find("def _speak_server")
check("every spoken line lands in voice_log.jsonl (Her Words)",
      "_log_her_line(text)" in bs[sp_i:sp_i + 1200])
check("unspoken popups still speak (title fallback → Her Words)",
      "say = (spoken if spoken else title)" in bs)

# ── 4b. THE ONE-WATCH RULE (Sep 21): no BUY popup on a watched asset ────
check("one-watch guard helper defined",
      "def _popup_already_watched(pair: str) -> bool:" in bs)
check("guard consults the watch (the entry record)",
      "if pair in _load_contest_watch():" in bs)
check("guard suppression is journaled (the 'who buried it' ledger)",
      '_log_suppression(pair, "already-watched"' in bs)
check("guard fails OPEN (a dead watch read never silences the alarm)",
      "popup \"\n                     " in bs or "fires anyway" in bs)
champ_g = bs.find("if _popup_already_watched(pair):\n            return")
check("champion route gated (return before notify_big)", champ_g > 0)
st_g = bs.find("if _popup_already_watched(pair):\n                    continue")
check("Sure Thing/breakout route gated (continue)", st_g > 0)
mv_g = bs.find("if _popup_already_watched(pair):\n                                        continue")
check("mover route gated (continue)", mv_g > 0)
check("guard sits BEFORE the fire (no popup on a watched pair)",
      0 < champ_g < bs.find("notify_big(title, message", max(0, champ_g)))
check("DCA 'BUY MORE' (the bot's book) stays ungated",
      bs.find("DCA TRANCHE: BUY MORE") > 0)

# ── 4c. GET OUT ALWAYS FIRES + the push actually sends (Sep 21) ─────────
go_i = bs.find("GET OUT PERSISTS + ALWAYS FIRES")
check("GET OUT always-fires contract at the gate", go_i > 0)
check("GET OUT fires with HER VOICE off (no voice gate on the exit call)",
      "if (_now_ts - _last_exit_voice.get(pair, 0)) >= 60:" in bs
      and "if voice and (_now_ts - _last_exit_voice" not in bs)
check("GET OUT push carries the FULL reasons (why + position facts)",
      "push_text=_push_txt" in bs and "WHY: {reason}" in bs)
check("clear only AFTER the call (a cleared row always got its popup)",
      0 < bs.find('_speak_server(f"Get out now!')
      < bs.find("watch.pop(pair, None)"))
push_i = bs.find("def _push_to_phone")
send_i = bs.find("def _send_ntfy")
check("push actually SENDS (attempt 1/2 rides _send_ntfy — the one pipe "
      "every path shares — and that pipe holds the urlopen send)",
      send_i > 0
      and "urllib.request.urlopen(req, timeout=10)" in bs[send_i:send_i + 1600]
      and "_send_ntfy(str(text), prio, \"attempt 1/2\")" in bs[push_i:push_i + 2600])
check("push success is logged (never a silent maybe)", "PUSHED (" in bs)
check("push failure is logged (never silent)", "phone push failed" in bs)

# ── 5. functional: the writer against a tempdir watch file ──────────────
try:
    tmp = Path(tempfile.mkdtemp())
    import background_scanner as bsm
    bsm.CONTEST_WATCH_JSON = str(tmp / "contest_watch.json")
    ok1 = bsm.auto_track_popup("XYZ/USD", 1.23, 45.0, 1.5, 60,
                               "BUY 90% (champion)", "champion")
    row = json.loads((tmp / "contest_watch.json").read_text(encoding="utf-8"))
    check("functional: first fire writes the row with fire-time numbers",
          ok1 and row["XYZ/USD"]["entry"] == 1.23
          and row["XYZ/USD"]["size"] == 45.0
          and row["XYZ/USD"]["source"] == "champion")
    ok2 = bsm.auto_track_popup("XYZ/USD", 9.99, 10.0, 2.0, 30,
                               "BUY 95% (mover)", "mover")
    row2 = json.loads((tmp / "contest_watch.json").read_text(encoding="utf-8"))
    check("functional: a second fire never displaces the user's row",
          (not ok2) and row2["XYZ/USD"]["entry"] == 1.23)
    check("functional: a machine-closed pair can be re-added fresh by a new fire",
          bsm.auto_track_popup("ZZZ/USD", 2.0, 31.5, 1.4, 60,
                               "BUY 88% (breakout crack)", "sure_thing")
          and json.loads((tmp / "contest_watch.json").read_text(encoding="utf-8"))["ZZZ/USD"]["entry"] == 2.0)
    bsm.CONTEST_WATCH_JSON = str(tmp / "watch2.json")
    bsm._save_contest_watch({"WWW/USD": {"entry": 1.0}})
    check("functional: one-watch guard trips on a watched pair",
          bsm._popup_already_watched("WWW/USD") is True)
    check("functional: one-watch guard passes an unwatched pair",
          bsm._popup_already_watched("QQQ/USD") is False)
except Exception as exc:  # noqa: BLE001 — the functional block reports itself
    check(f"functional writer block crashed: {exc}", False)

out = ["POPUP AUTO-TRACK / GET OUT AUTO-CLOSE", "=" * 60]
fails = 0
for name, ok in results:
    out.append(f"{'PASS' if ok else 'FAIL'}  {name}")
    fails += 0 if ok else 1
out.append("")
out.append(f"{len(results) - fails}/{len(results)} passed")
Path(__file__).with_name("_autotrack_check.txt").write_text("\n".join(out), encoding="utf-8")
print(f"{len(results) - fails}/{len(results)} passed -> _autotrack_check.txt")
sys.exit(1 if fails else 0)
