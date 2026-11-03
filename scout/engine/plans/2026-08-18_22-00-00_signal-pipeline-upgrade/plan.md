# Kraken Scout + XRP Bot — Signal Pipeline Upgrade (Adjusted)

## Context

The system works — Scout finds trades, XRP bot executes them. But we've hit real problems: WLD was bought 8 times in 2 hours (no duplicate guard, since fixed), LINK was bought and sold in the same candle (no cooldown, since fixed), JSON file I/O is fragile, and there's no safety net when things go wrong. This plan adds the critical safety layers we're missing — nothing more.

## What's Already Built (don't rebuild)

- ✅ Signal bridge with buy/sell signals
- ✅ Duplicate buy prevention (won't write if already holding)
- ✅ 30-min cooldown after buy before exit check
- ✅ Exit check requires ≥60% sell confidence
- ✅ Liquidity filter (spread <1%, volume >$100K)
- ✅ False trend detector (wick <20%, volume spike check)
- ✅ Regime detection (5 regimes, phase model)
- ✅ Tournament scanner (1v1 elimination = voting)
- ✅ Adaptive time-of-day scanning
- ✅ Desktop notifications (non-blocking)
- ✅ MST timezone
- ✅ Rebalance: sell weakest loser to fund strong signal
- ✅ Scanner status indicator in dashboard

## Scope — 4 Focused Upgrades

### 1. Replace JSON with SQLite (fixes fragility)

**Problem:** JSON file has no locking, can partially write, can't query, can corrupt.

**File: `signal_bridge.py`**

Replace `trade_signals.json` with `signals.db` (SQLite). One table, atomic writes, queryable.

Schema:
```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    reason TEXT,
    timestamp TEXT NOT NULL,
    valid_until TEXT,
    cooldown_until TEXT,
    price_at_signal REAL,
    entry_price REAL,
    stop_loss REAL,
    take_profit REAL,
    risk_reward REAL,
    position_size_usd REAL,
    source TEXT DEFAULT 'scout_auto',
    status TEXT DEFAULT 'pending',
    acknowledged_at TEXT,
    executed_at TEXT,
    execution_price REAL,
    order_id TEXT,
    closed_at TEXT,
    pnl REAL
);
```

All existing functions (`write_buy_signal`, `write_exit_signal`, `get_pending_signals`, `mark_executed`, `mark_closed`) rewrite to use SQLite instead of JSON. Same API, different backend.

### 2. Signal Validity Window + Cooldown Manager (fixes stale signals)

**Problem:** Signals stay pending forever. If the bot is busy or funds are locked, a 10-minute-old signal fires when market conditions have changed.

**File: `signal_bridge.py`**

- Buy signals: `valid_until` = timestamp + 5 minutes
- Sell signals: `valid_until` = timestamp + 10 minutes
- `get_pending_signals()` only returns signals where `valid_until > now`
- Add `expire_old_signals()` — marks anything past `valid_until` as "expired"
- Call `expire_old_signals()` before reading pending signals

**File: `background_scanner.py`**

Cooldown manager — before writing a buy signal:
1. Check if pair has `cooldown_until > now` in the DB → skip
2. Check if pair already has an executed buy (still open) → skip
3. Set `cooldown_until` = timestamp + 30 minutes on the new signal
4. Only write if all checks pass

### 3. Kill Switch + Circuit Breaker (fixes catastrophic losses)

**Problem:** No safety net. If the strategy goes haywire or Kraken has issues, the bot keeps trading until your account is drained.

**File: `scout_signal_reader.py`**

```python
def _check_kill_switch(kraken, config) -> str | None:
    """Returns reason if trading should halt, None if all clear."""
    # 1. Consecutive API failures > 3
    # 2. Daily loss > 3% of starting balance (circuit breaker)
    # 3. BTC moved >5% in 1 hour (volatility spike)
    # 4. Spread on target pair > 2%
    # 5. Last 3 fills had >1% slippage each
```

If triggered:
- Stop all new trades
- Write `kill_switch.lock` file
- Log reason + send desktop notification
- Bot continues exit checks on open positions (can still sell)
- Requires manual reset: delete `kill_switch.lock` file

**File: `scout_signal_reader.py`**

Circuit breaker — track daily P&L from closed signals in the DB:
- Sum P&L of all signals closed today
- If daily P&L < -3% of portfolio → halt
- If 5 consecutive losing trades → halt
- Log + notify + require manual reset

### 4. Signal Acknowledgment + Execution Feedback (fixes blind execution)

**Problem:** Scout writes a signal, bot executes it, but Scout has no idea if it actually worked or what price it filled at. Can't improve the strategy without feedback.

**File: `scout_signal_reader.py`**

State flow per signal:
1. `pending` → Scout wrote it
2. `acknowledged` → Bot read it, about to execute (writes `acknowledged_at`)
3. `filled` → Order confirmed (writes `executed_at`, `execution_price`, `order_id`)
4. `rejected` → Pre-check failed (writes `execution_reason` with why)
5. `closed` → Position exited (writes `closed_at`, `pnl`)

Bot calls `acknowledge(signal_id)` before executing, then `mark_executed()` or `mark_rejected()` after.

**File: `background_scanner.py`**

After each tournament, check recent signals:
- If signal was `acknowledged` but not `filled` within 2 min → log warning
- If signal was `rejected` → log reason (insufficient funds, slippage, etc.)
- Track average slippage (price_at_signal vs execution_price) for monitoring

## What's NOT in This Plan (deferred)

- Shadow mode — XRP bot already has `paper_mode`, sufficient for testing
- Slippage-aware order book depth — current spread filter is enough
- Position sizing upgrades — current 1% risk works
- Unified monitoring dashboard — current Streamlit is enough
- Message bus (Redis/Kafka) — SQLite is sufficient for single machine
- Multi-model voting — tournament IS the voting
- Exit strategy variants — current exit check works
- Daily/weekly backups — manual for now
- Signal aging with confidence decay — validity window covers this

## Verification

1. Write a buy signal → verify it appears in SQLite with `valid_until` 5 min ahead
2. Wait 6 minutes → verify `expire_old_signals()` marks it "expired"
3. Write two buy signals for same pair → verify second is blocked by cooldown
4. Simulate 3 API failures → verify kill switch triggers
5. Simulate 3% daily loss → verify circuit breaker triggers
6. Execute a signal → verify `acknowledged_at` then `executed_at` are written
7. Reject a signal (insufficient funds) → verify status is "rejected" with reason
8. Corrupt the SQLite file → verify bot continues managing positions, stops new trades
9. Check execution slippage → verify `price_at_signal` vs `execution_price` is logged
