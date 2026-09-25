"""miner_yield.py — REAL MINING YIELD FOR THE FUNDING FORMULA (Sep 25).

THE CHARTER'S MinerYield, made honest:

    MinerYield = rolling 14-day average of XMR mined by active
                 Miner Bot users

That requires KNOWING how much XMR the bots actually mine. This
module is the observer: it sits beside proxy_poller, converts each
worker's reported hashrate into expected XMR with the standard
mining estimate, accumulates per miner across poll cycles, and once
per LOCAL DAY flushes one receipt per miner into jackpot_funding
(category 'mining_yield'). The funding formula then sees REAL
inflows — never invented ones.

THE ESTIMATE (the standard hashrate model, no secrets in it):

    XMR/day = hashrate_Hps * 86400 / network_difficulty * block_reward

- hashrate comes from xmrig-proxy's per-worker summary (live truth).
- network_difficulty and block_reward are Monero mainnet constants
  here; when the operator sets NETWORK_DIFFICULTY / NETWORK_HASHRATE
  in the settings those override the constants (a deployment that
  tracks the real network can stay accurate without code changes).
- If the proxy summary carries no difficulty, NO estimate is made —
  a receipt is never invented from missing data.

NO PHANTOM MONEY, NO DOUBLE-COUNT
---------------------------------
- Accumulators hold EXPECTED XMR per miner for the CURRENT day and
  are cleared exactly once by flush_daily() at the day boundary.
  A flush day that saw no cycles flushes nothing.
- Cold start (fresh state): the first day's partial data is skipped
  (state stamped, accumulators empty) — yesterday's mining was never
  observed, so it is never claimed.
- Crash recovery: accumulators are REBUILT from recent miner rows
  (last_seen within 48h) when the state is missing — at most the
  current day's unflushed earnings are re-credited, never a whole
  extra day.
- flush_daily() is idempotent per day: a second call the same day
  flushes nothing.
- Receipts are USD at the tracked rate (the ledger's unit); the
  daily accrual in jackpot_funding remains the ONLY place funding
  becomes base XMR.

PRIVACY: receipts carry the opaque miner id as sponsor and the
source 'proxy_poller' — never addresses, never keys.
"""
import time

import database as db
import jackpot_funding as funding

# ── Monero mainnet facts (settings may override difficulty) ───────────
BLOCK_REWARD_XMR = 0.6          # post-2022 tail emission, per block
DEFAULT_NETWORK_DIFFICULTY = 313_962_386_248.0   # ~mid-2026 snapshot
# self-consistent with the difficulty above: D = H × 120 s (2-min blocks)
DEFAULT_NETWORK_HASHRATE = DEFAULT_NETWORK_DIFFICULTY / 120.0
STATE_KEY = "miner_yield_state_v1"
FLUSH_INTERVAL_SECONDS = 86400  # one receipt window per LOCAL DAY
COLD_START_GRACE = 15 * 60      # crash recovery: only the narrow gap
                                # since the poller was last alive
MAX_CYCLE_SECONDS = 300.0       # one cycle credits at most this much
                                # wall time (a long outage has no
                                # connected workers to credit anyway)


def _setting_float(key, default):
    try:
        v = float(db.get_setting(key, default))
        return v if v > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def network_difficulty(now_ts=None):
    return _setting_float("NETWORK_DIFFICULTY", DEFAULT_NETWORK_DIFFICULTY)


def network_hashrate(now_ts=None):
    return _setting_float("NETWORK_HASHRATE", DEFAULT_NETWORK_HASHRATE)


def estimate_daily_xmr(hashrate, now_ts=None):
    """Expected XMR per day at this hashrate. Standard mining math."""
    if not hashrate or hashrate <= 0:
        return 0.0
    return (float(hashrate) * 86400.0
            / network_difficulty(now_ts) * BLOCK_REWARD_XMR)


def _load_state():
    import json
    raw = db.get_setting(STATE_KEY, "{}")
    try:
        state = json.loads(raw)
        return state if isinstance(state, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save_state(state):
    import json
    db.set_setting(STATE_KEY, json.dumps(state, separators=(",", ":")))


def _today(now_ts):
    return time.strftime("%Y-%m-%d", time.localtime(now_ts))


def _rebuild_from_miners(now_ts):
    """Crash recovery: rebuild accumulators from miner rows that were
    seen recently (within the grace window). This estimates the
    UNFLUSHED portion only — the state's absence caps it at the
    current day, never a full extra day."""
    miners = {}
    try:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT id, hashrate, last_seen FROM miners "
                "WHERE hashrate > 0 AND last_seen > ?",
                (int(now_ts) - COLD_START_GRACE,),
            ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        row = dict(r)
        hours = max(0.0, (now_ts - float(row["last_seen"])) / 3600.0)
        # CRASH RECOVERY, TIGHTENED (review triage, Sep 25): credit only
        # the narrow window since the poller was last alive — a miner
        # seen 6 hours ago was NOT necessarily mining 6 hours ago (the
        # proxy may have restarted, the rig may have idled); inventing
        # those hours credited yield nobody earned.
        hours = min(hours, COLD_START_GRACE / 3600.0)
        xmr = estimate_daily_xmr(row["hashrate"], now_ts) * hours / 24.0
        if xmr <= 0:
            continue          # seen this instant — nothing unflushed to rebuild
        miners[int(row["id"])] = {"xmr": xmr}
    return miners


def note_workers(workers, now_ts=None):
    """Called by proxy_poller each cycle with the parsed worker list
    ([{miner_id, hashrate, ...}] or wallet/worker_name dicts). Only
    workers with a live hashrate accumulate — a dead rig earns nothing.
    Returns the state (also handy for drives)."""
    now_ts = now_ts if now_ts is not None else time.time()
    state = _load_state()
    miners = state.get("miners", {})

    # Cold start: stamp the state without crediting history, then
    # fall through — THIS call's workers still count (accumulation
    # begins now; only the unobserved past stays unclaimed).
    if not state:
        state = {"day": _today(now_ts),
                 "miners": _rebuild_from_miners(now_ts)}
        miners = state["miners"]      # accumulate INTO the rebuilt set
        _save_state(state)

    # THE WALL-CLOCK FIX (review triage, Sep 25): the first draft
    # credited estimate_daily_xmr/86400 per CYCLE — one SECOND of
    # yield on a 10s poll — about 10x under the truth. Credit the real
    # elapsed wall time between cycles instead (clamped: one call can
    # never credit more than MAX_CYCLE_SECONDS).
    prev_ts = float(state.get("ts") or 0.0)
    delta = min(max(0.0, now_ts - prev_ts), MAX_CYCLE_SECONDS) \
        if prev_ts else 0.0
    for w in workers or []:
        hashrate = float(w.get("hashrate", 0) or 0)
        if hashrate <= 0 or delta <= 0:
            continue
        mid = w.get("miner_id")
        if mid is None:
            m = db.get_or_create_miner(w.get("wallet_address", ""),
                                       w.get("worker_name", ""))
            mid = int(m["id"])
        mid = int(mid)
        per_cycle = estimate_daily_xmr(hashrate, now_ts) \
            * delta / 86400.0
        slot = miners.get(str(mid)) or miners.get(mid) or {"xmr": 0.0}
        # NO rounding here: a CPU rig's per-cycle yield is ~1e-8 XMR —
        # round() at this magnitude would destroy it. Precision is
        # applied only when the receipt is written (USD, 2dp).
        slot["xmr"] = float(slot.get("xmr", 0.0)) + per_cycle
        miners[str(mid)] = slot

    state["miners"] = miners
    state["ts"] = now_ts
    _save_state(state)
    return state


def flush_daily(now_ts=None):
    """Once per LOCAL DAY: clear the accumulators and record one
    mining_yield receipt per miner into jackpot_funding. Idempotent —
    a day already flushed is a no-op. Returns the flush record.

    SUB-CENT RECEIPTS CARRY: the ledger's unit is 2-dp USD, so a
    receipt below half a cent would round to zero and be refused.
    Rather than drop mined value, that miner's accumulator is kept
    and rolls forward until it crosses one cent — nothing mined is
    ever silently lost, and nothing is ever recorded that wasn't
    mined."""
    now_ts = now_ts if now_ts is not None else time.time()
    today = _today(now_ts)
    state = _load_state()

    state_day = state.get("day")
    if state_day == today:
        # Accumulators were already flushed for this day (or the state
        # was stamped by today's cold start — nothing yet to flush).
        if not state.get("miners"):
            return {"flushed": False, "day": today,
                    "reason": "nothing accumulated today"}
        return {"flushed": False, "day": today,
                "reason": "accumulating — the day's receipts flush "
                          "at the day boundary"}

    miners = state.get("miners", {})
    receipts, total_xmr, carried = [], 0.0, 0
    rate = funding.xmr_usd_rate()
    remaining = {}
    for mid_s, slot in miners.items():
        xmr = float(slot.get("xmr", 0.0))
        usd = round(xmr * rate, 2)
        if usd > 0:
            rid = funding.record_funding(
                "mining_yield", usd,
                sponsor_id=int(mid_s), source="proxy_poller")
            if rid is not None:
                receipts.append({"miner_id": int(mid_s), "xmr": xmr,
                                 "amount_usd": usd, "row": rid})
                total_xmr += xmr
                continue              # receipted — accumulator cleared
        # sub-cent (or refused): CARRY the accumulator forward
        carried += 1
        remaining[mid_s] = slot

    _save_state({"day": today, "miners": remaining})
    return {"flushed": True, "day": today, "prior_day": state_day,
            "receipts": receipts, "carried": carried,
            "total_xmr": round(total_xmr, 8),
            "total_usd": round(total_xmr * rate, 2)}
