# The 17:48/17:59 Silent GET OUT Incident — Root Cause + Hardening (Oct 9)

## What fired, what reached

By the logs, every firing decision that afternoon was correct:
- 17:40 WIF climax GET OUT — journal row 240, pushes ×2 (+voice wav)
- 17:44 WIF min-size BUY — journal 241
- 17:48 PUMP + MOODENG exit pair — journal 242/243, pushes ×2 each (+voice wav)
- 17:59 VIRTUAL BUY→GET OUT same minute — journal 245/246
- All voice renders (David + Mira) landed — wav mtime advances confirmed; the `+voice`
  tag on the push log lines proves the rendered WAV actually attached to the phone.

Two legs DID run in the code path but produced nothing at your desktop:

1. **Voice playback** — `speak_loud.ps1` renders the WAV, copies it to `OutFile`,
   then plays it with `SoundPlayer.PlaySync()`. The renders succeeded (the file
   went fresh, the push got it), but nothing reached the speakers during that
   window, or it did and died silently.
2. **Desktop modal** — the `WScript.Popup()` call never visibly appeared.

## Root cause: two holes, two fixes

### Hole 1 — the modal had NO topmost hint

`notify_big` fired `WScript.Popup(msg, 180, title, 48)` — 48 = MB_ICONWARNING +
OK/Cancel. With no 4096 (MB_TOPMOST) bit, the modal lands at default Z-order.
The scanner's own tee console (visible, elevated-focus) sat ON TOP, exactly per
`start_detached.ps1`'s tited-tee visible window design, and the modal spawned
BEHIND it — never seen, never clicked.

**Fix:** the popup bit-field became `4096 + 48` (topmost + warning icon). Sites
patched:
- `notify_big`'s `$w.Popup('{_m}', 180, '{_t}', 4096 + 48)` in `background_scanner.py`
- The green-light console modal in `app.py` (same shape, `'..., 4096+48)`)

Tested live in this session with a 10s Popup - it pops over everything, restoring
the "stays until you read it" contract the comment always claimed but the
Z-order never delivered.

### Hole 2 — the speak spawn could die silently, no trace

`_speak_server` launched PowerShell as `Popen(..., creationflags=0x08000000)` and
immediately threw away the process handle. If a spawn died before it could
render a single sample (an antivirus quarantining a mimic-process, a PS session
race, a System.Speech load failure in a hidden session), the old code noticed
only a "missed render" after the 15s poll — with NO distinguishing fact about
WHY. Debugging cost: ~20 minutes of log mining to even suspect it.

**Fix in `_speak_server`:** the spawn handle is kept now. After the render
poll, if `mtime` never went fresh AND the child has already exited before the
first poll tick, we log:
```
🔇 VOICE DIED BEFORE SPEAKING: the speak spawn exited instantly (pid N) — no audio on the PC; the push goes out text-only.
```
The push still goes out (text-only) — the call is never delayed into silence.
The 15s poll and the `wav_path if _render_fresh else None` flow stay exactly as
they were; this is pure observability.

The parallel hardening also cleaned `safe_title`/`safe_msg` (chars that
PowerShell's single-quote parser mishandles — broad nuke of the `'`s and `"`s).

## What the checks say

- `py_compile` on `app.py` and `background_scanner.py`: clean
- test_riskcaplabel 13/0, test_thinbrain 21/0, test_autotrack 44/44,
  test_sink_hygiene 6/0, test_pytestsafe 11/0 — all still green after both edits
- The 4096+48 modal live-fires over everything (validated with 3 test popups)

## Rollout state

1. **Scanner restart REQUIRED** — the running scanner (pid 67280 @ 15:50) holds
   the old `notify_big` and `_speak_server` in memory. The topmost bit, the
   lifeline log, and the earlier "book will carry it" spend-line fixes go live
   ONLY after a restart (the 11:32 "old code still firing vetos" lesson).
2. **Console side: live on next page load** — Streamlit reloads app.py on rerun;
   the QC/green-light 4096+48 modal and the "book will carry it" green-light
   confirm text show without touching any process.
3. **Voice leg**: compared to the earlier testing the two tests I just ran
   (David + plain volume) DID render (the wav mtime jumped) — the web of sys
   audio lives wherever it lives. The lifeline log distinguishes "failed to
   spawn" from "spawned but silent" for the NEXT incident, which is the
   ground truth this incident lacked.

## Open follow-ups

- The "died before speaking" log fires ONLY when the child exits before the
  0.5s first poll. A spawn that lives 1s then dies still reads as merely
  "missed render"; a sharper discriminator would be checking the child's exit
  code once after render-poll completes (needs holding the handle, which we
  now do — natural next step).
- `_voice_volume()` reads `console_state.json.voice_volume` — the key is
  currently ABSENT (the guard defaults to 100). Confirm the console dial
  actually saves on change; if it never did, the 0%-silence Volume-Mixer
  incident from ~12:30 could also have been the dial reading a missing key
  and, in the same missing-key window, falling all the way to a `0` render
  target in speak_loud.ps1's `$volTarget` — worth one console-side look.
