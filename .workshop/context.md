# Kraken Scout — Project Context

## What it is
A daily trade picker for the Kraken Funded trading challenge. Scans 15 crypto pairs, runs technical analysis, surfaces the best trade opportunities with entry/stop-loss/take-profit levels and position sizing. Advisory only — user executes trades manually in the Kraken app.

## Kraken Funded Challenge Rules
- Grow starting balance by +12% to pass
- Max loss is -5% from starting balance (instant fail)
- No trading fees during challenge
- No leverage, dollar-amount buy/sell only
- Mobile app only
- Keep 80% of profits when funded

## Tech Stack
- Python + Streamlit (dashboard)
- ccxt (Kraken API wrapper)
- pandas/numpy (indicators)
- plotly (charts)
- SQLAlchemy + Neon Postgres (signal/challenge trade logging)
- keyring (secrets)

## Project Structure
- `config.py` — TradingConfig dataclass with scout defaults + Funded challenge params
- `kraken_api.py` — ccxt Kraken wrapper (cloned from kraken_xrp_trading_bot, unchanged)
- `indicators.py` — RSI, MACD, Bollinger, EMA, ATR, Stochastic (cloned, unchanged)
- `patterns.py` — Chart pattern detection (cloned, unchanged)
- `strategy.py` — Signal generation with entry/stop/TP (cloned + adapted)
- `scanner.py` — Multi-pair scanner + position sizing (new)
- `db.py` — Signal logging + challenge state (adapted)
- `app.py` — Streamlit dashboard (new)
- `backtest.py` — Backtesting (cloned, unchanged)
- `secrets_utils.py` — Keyring secret management (cloned, unchanged)

## Key Design Decisions
- Advisory only — no order execution
- Position sizing: risk 1% of starting balance per trade (5 trades before max loss)
- R:R ratio: 2.0 (take-profit = entry + 2× risk)
- Stop-loss: tighter of ATR×1.5 or nearest support level
- Scanner sorts by confidence, returns top 5 picks
- Challenge tracker: user manually logs trades executed in Kraken app

## Related Project
- `C:\Users\birds\Workspace\kraken_xrp_trading_bot` — the source project. Kraken Scout clones files from it but does NOT modify it.