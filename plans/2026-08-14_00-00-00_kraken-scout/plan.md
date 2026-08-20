# Kraken Scout — Daily Trade Picker for Kraken Funded Challenge

## Context

The user has entered a **Kraken Funded** challenge — a program where you pay a challenge fee ($20–$90) to trade Kraken's capital. If you grow the starting balance by **+12%** without ever dropping more than **-5%** from starting balance, you pass and get to keep trading funded capital (keeping 80% of profits). There are **no trading fees** during the challenge.

Kraken Funded constraints:
- **Profit target**: +12% (e.g., $1,000 → $1,120)
- **Max loss**: -5% from starting balance (e.g., $1,000 → $950 = fail)
- **No leverage, no advanced order types** — buy/sell in dollar amounts
- **Mobile app only** — trades executed manually in the Kraken app
- **No trading commissions** — zero-fee environment

This means the scout is **advisory only** — it finds the best picks, computes entry/stop/target levels, and the user manually executes in the Kraken app. The tight 5% max loss means risk management on every pick is critical.

A full trading bot already exists at `C:\Users\birds\Workspace\kraken_xrp_trading_bot` with reusable components. **We will clone files from it — not modify it.**

### What we clone from the existing bot (unchanged)

| File | What it does |
|---|---|
| `kraken_api.py` | ccxt Kraken wrapper — OHLCV, ticker, price fetching |
| `indicators.py` | RSI, MACD, EMA, SMA, Bollinger Bands, Stochastic, ATR — pure pandas/numpy |
| `patterns.py` | Double bottom/top, head & shoulders, S/R levels, candlestick patterns, trend detection |
| `backtest.py` | Backtesting framework — win rate, PnL, drawdown |
| `secrets_utils.py` | Keyring-based secret management |

### What we adapt (copy + modify)

| File | Changes |
|---|---|
| `strategy.py` | Extend `Signal` with `entry_price`, `stop_loss`, `take_profit`, `risk_reward` — computed from ATR + S/R levels |
| `config.py` | Scout-specific defaults — broader pair list, Funded challenge params (5% max loss, 12% target), R:R ratio, no execution params |

### What's new

| File | Purpose |
|---|---|
| `scanner.py` | Multi-pair scanner — fetches OHLCV for all pairs, runs strategy, ranks by confidence, returns top picks |
| `app.py` | Streamlit dashboard — ranked picks table, detail charts, challenge progress tracker, backtest tab |

## Scope & Non-Goals

### In Scope (v1)
- Scan a broad list of Kraken trading pairs (BTC, ETH, SOL, XRP, XMR, ADA, DOT, LINK, AVAX, MATIC, etc.)
- For each pair: fetch OHLCV → run indicators + patterns → generate signal with confidence
- Rank all pairs by confidence, surface the top opportunities
- For each pick, display:
  - **Entry zone** (current price or suggested limit)
  - **Stop-loss** (ATR-based: `entry - (ATR × 1.5)`, or nearest support)
  - **Take-profit** (R:R based: `entry + (risk × 2.0)`)
  - **Confidence score** (0–100, weighted indicator consensus)
  - **Reasons** (which indicators/patterns triggered)
  - **Position sizing** — how much of the challenge balance to risk per trade to stay within the 5% max loss
- Streamlit dashboard with:
  - **Challenge tracker** — starting balance, current balance, progress toward +12% / -5%
  - **Top picks table** — ranked by confidence with entry/stop/TP
  - **Detail view** — candlestick chart with Bollinger Bands, EMA lines, RSI/MACD subcharts
  - **Config sidebar** — edit pairs, candle interval, indicator settings, R:R ratio
  - **Backtest tab** — validate any pick against historical data
- Signal history logging to DB

### Non-Goals (deferred)
- Automated order execution — user trades manually in the Kraken app
- Real-time WebSocket streaming — v1 uses on-demand REST fetching
- Push notifications (future: email/Discord/Telegram)
- Portfolio tracking / live P&L sync (user enters trades manually for challenge tracking)

## Implementation Plan

### Architecture
```
kraken_scout/
├── pyproject.toml
├── start.sh
├── kraken_api.py        # ← CLONED from existing bot (unchanged)
├── indicators.py         # ← CLONED from existing bot (unchanged)
├── patterns.py           # ← CLONED from existing bot (unchanged)
├── backtest.py           # ← CLONED from existing bot (unchanged)
├── secrets_utils.py      # ← CLONED from existing bot (unchanged)
├── strategy.py           # ← CLONED + ADAPTED: add entry/stop/TP to Signal
├── config.py             # ← CLONED + ADAPTED: scout defaults + Funded params
├── db.py                 # ← CLONED + ADAPTED: signal logging + challenge state
├── scanner.py            # NEW: multi-pair scanner + ranker
├── app.py                # NEW: Streamlit scout dashboard
└── tests/
    └── test_scanner.py
```

### Steps

**1. Project setup**
- `uv init` in `kraken_scout/`
- Install deps: `ccxt`, `pandas`, `numpy`, `plotly`, `streamlit`, `sqlalchemy`, `psycopg2-binary`, `keyring`
- Clone reusable files from `kraken_xrp_trading_bot/` (copy, don't link)

**2. Adapt `config.py` — scout + Funded challenge defaults**
- Broader default pair list: BTC/USD, ETH/USD, SOL/USD, XRP/USD, XMR/USD, ADA/USD, DOT/USD, LINK/USD, AVAX/USD, MATIC/USD
- Funded challenge params: `challenge_balance` (e.g., 1000), `max_loss_pct` (5.0), `profit_target_pct` (12.0)
- Scout params: `risk_reward_ratio` (2.0), `atr_multiplier` (1.5), `top_n` (5), `risk_per_trade_pct` (1.0 — risk 1% of balance per trade, so ~5 trades before max loss)
- Remove execution params (DCA, cold wallet, paper mode, trailing stop — not needed)

**3. Adapt `strategy.py` — add entry/exit levels to Signal**
- Extend `Signal` dataclass with `entry_price`, `stop_loss`, `take_profit`, `risk_reward`
- After computing indicators in `evaluate_strategy()`:
  - `entry_price` = current close
  - `stop_loss` = `entry - (ATR × atr_multiplier)` for buys (or nearest support from patterns)
  - `take_profit` = `entry + (entry - stop_loss) × risk_reward_ratio`
  - `risk_reward` = `(take_profit - entry) / (entry - stop_loss)`
- For sell signals, mirror the logic (stop above, TP below)

**4. Build `scanner.py` — the core new component**
- `scan_pairs(pairs: list[str], config) -> list[ScanResult]`
- For each pair: fetch OHLCV → `evaluate_strategy()` → package as `ScanResult(pair, signal, ohlcv_df)`
- Filter: only return actionable signals (buy or sell with confidence ≥ threshold)
- Sort by confidence descending
- Return top N
- `compute_position_size(balance, entry, stop_loss, max_loss_pct, risk_per_trade_pct) -> float` — calculates how many dollars to put in a trade so that hitting the stop-loss doesn't exceed the per-trade risk budget

**5. Adapt `db.py` — add challenge state tracking**
- Reuse existing `signals` table for logging scan results
- Add `challenge_state` table: starting_balance, current_balance, trades_log (manual entry of executed trades)
- Add `log_scan()` and `get_challenge_state()` functions

**6. Build `app.py` — scout dashboard**
- **Challenge tracker panel** (top): starting balance, current balance, progress bar toward +12%, warning bar toward -5%, list of manually logged trades
- **Top picks table**: pair, action, confidence, entry, stop-loss, take-profit, R:R, suggested position size — sorted by confidence, color-coded
- **Detail panel**: select a pick → candlestick chart with Bollinger Bands + EMA overlays, RSI/MACD subcharts, pattern annotations, entry/stop/TP horizontal lines
- **Config sidebar**: edit pairs, candle interval, lookback, indicator periods, R:R ratio, ATR multiplier, challenge balance
- **Manual trade log**: form to log trades executed in the Kraken app (pair, side, amount, price) → updates challenge tracker
- **Backtest tab**: run backtest on any scanned pair
- **Signal history**: past scans logged to DB

**7. Tests**
- Unit test: `evaluate_strategy()` returns valid entry/stop/TP for sample OHLCV data
- Unit test: `scan_pairs()` ranks correctly, handles API errors gracefully
- Unit test: `compute_position_size()` respects max loss constraint
- Integration test: live scan against Kraken public API returns results for major pairs

## Verification
- Run the Streamlit dashboard, verify it loads and shows ranked picks for major pairs
- Verify each pick has sensible entry/stop/TP (stop below entry for buys, TP above, R:R ≥ 2.0)
- Verify position sizing keeps risk within the 5% max loss budget
- Manually log a test trade, verify challenge tracker updates correctly
- Run backtest on a picked pair, verify it produces results
- Check signal history is logged to DB
