"""Kraken API wrapper using ccxt.

Provides a clean interface for:
  - Market data (OHLC candles, ticker)
  - Account balance
  - Order placement (market & limit)
  - Cold-wallet withdrawals (using a pre-registered address key)

Authentication uses API keys from st.secrets.
"""
from __future__ import annotations

import logging
from typing import Any

import ccxt

logger = logging.getLogger(__name__)


class KrakenAPI:
    """Thin wrapper around ccxt.kraken with error handling and logging."""

    def __init__(self, api_key: str, api_secret: str, sandbox: bool = False):
        """Initialise the ccxt Kraken client.

        Args:
            api_key: Kraken API public key.
            api_secret: Kraken API private/secret key.
            sandbox: If True, set sandbox mode (Kraken doesn't have a true
                     sandbox, but ccxt's 'test' mode can be enabled).
        """
        self.exchange = ccxt.kraken({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    # ── Market data ──────────────────────────────────────────────────────

    def fetch_ohlcv(
        self, pair: str, interval_minutes: int = 60, limit: int = 200
    ) -> list[list[float]]:
        """Fetch OHLCV candlestick data.

        Args:
            pair: Trading pair, e.g. "XMR/USD".
            interval_minutes: Candle interval (1, 5, 15, 30, 60, 240, 1440, 10080).
            limit: Number of candles.

        Returns:
            List of [timestamp_ms, open, high, low, close, volume].
        """
        timeframe = self._minutes_to_timeframe(interval_minutes)
        ohlcv = self.exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        return ohlcv

    def fetch_ticker(self, pair: str) -> dict[str, Any]:
        """Fetch current ticker (bid, ask, last price, volume)."""
        return self.exchange.fetch_ticker(pair)

    def fetch_price(self, pair: str) -> float:
        """Return the current last price for a pair."""
        ticker = self.fetch_ticker(pair)
        return float(ticker["last"])

    # ── Account ──────────────────────────────────────────────────────────

    def fetch_balance(self) -> dict[str, dict[str, float]]:
        """Fetch account balances.

        Returns ccxt balance dict, e.g.:
            {"USD": {"free": 1000.0, "used": 0.0, "total": 1000.0}, ...}
        """
        balance = self.exchange.fetch_balance()
        # Filter out empty currencies for cleanliness
        return {
            cur: info
            for cur, info in balance.get("total", {}).items()
            if info and info > 0
        }

    def get_available_quote(self, quote_currency: str) -> float:
        """Return available balance of the quote currency (e.g. USD)."""
        balance = self.exchange.fetch_balance()
        free = balance.get(quote_currency, {}).get("free", 0.0)
        return float(free)

    def get_available_base(self, base_currency: str) -> float:
        """Return available balance of the base currency (e.g. XMR)."""
        balance = self.exchange.fetch_balance()
        free = balance.get(base_currency, {}).get("free", 0.0)
        return float(free)

    # ── Trading ──────────────────────────────────────────────────────────

    def place_market_buy(
        self, pair: str, volume: float, validate: bool = False
    ) -> dict[str, Any]:
        """Place a market buy order.

        Args:
            pair: Trading pair, e.g. "XMR/USD".
            volume: Amount of base currency to buy.
            validate: If True, simulate the order without executing.

        Returns:
            ccxt order dict with id, status, etc.
        """
        params = {}
        if validate:
            params["validate"] = True
        logger.info("Market BUY %s volume=%s validate=%s", pair, volume, validate)
        order = self.exchange.create_order(
            symbol=pair,
            type="market",
            side="buy",
            amount=volume,
            params=params,
        )
        return order

    def place_market_sell(
        self, pair: str, volume: float, validate: bool = False
    ) -> dict[str, Any]:
        """Place a market sell order.

        Args:
            pair: Trading pair, e.g. "XMR/USD".
            volume: Amount of base currency to sell.
            validate: If True, simulate without executing.

        Returns:
            ccxt order dict.
        """
        params = {}
        if validate:
            params["validate"] = True
        logger.info("Market SELL %s volume=%s validate=%s", pair, volume, validate)
        order = self.exchange.create_order(
            symbol=pair,
            type="market",
            side="sell",
            amount=volume,
            params=params,
        )
        return order

    def place_limit_buy(
        self, pair: str, volume: float, price: float, validate: bool = False
    ) -> dict[str, Any]:
        """Place a limit buy order."""
        params = {}
        if validate:
            params["validate"] = True
        logger.info("Limit BUY %s volume=%s price=%s validate=%s", pair, volume, price, validate)
        order = self.exchange.create_order(
            symbol=pair,
            type="limit",
            side="buy",
            amount=volume,
            price=price,
            params=params,
        )
        return order

    def place_limit_sell(
        self, pair: str, volume: float, price: float, validate: bool = False
    ) -> dict[str, Any]:
        """Place a limit sell order."""
        params = {}
        if validate:
            params["validate"] = True
        logger.info("Limit SELL %s volume=%s price=%s validate=%s", pair, volume, price, validate)
        order = self.exchange.create_order(
            symbol=pair,
            type="limit",
            side="sell",
            amount=volume,
            price=price,
            params=params,
        )
        return order

    def fetch_order(self, order_id: str, pair: str) -> dict[str, Any]:
        """Fetch order status by id."""
        return self.exchange.fetch_order(order_id, pair)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order."""
        return self.exchange.cancel_order(order_id)

    # ── Withdrawals (cold wallet sweep) ──────────────────────────────────

    def get_withdrawal_fee(self, asset: str, address_key: str, amount: float) -> dict[str, Any]:
        """Query withdrawal info (fee, estimated arrival) before withdrawing.

        Uses Kraken's private/WithdrawInfo endpoint via ccxt's implicit method.

        Args:
            asset: Ticker symbol, e.g. "XMR".
            address_key: Kraken withdrawal address key (pre-registered label).
            amount: Amount to withdraw.

        Returns:
            Dict with withdrawal info.
        """
        # ccxt doesn't have a unified withdrawInfo, so we call the Kraken-specific method
        try:
            result = self.exchange.private_post_withdrawinfo({
                "asset": asset,
                "key": address_key,
                "amount": str(amount),
            })
            return result
        except Exception as exc:
            logger.warning("WithdrawInfo failed for %s %s: %s", asset, address_key, exc)
            return {}

    def withdraw(self, asset: str, amount: float, address_key: str) -> dict[str, Any]:
        """Withdraw funds to a pre-registered cold wallet address.

        CRITICAL: This is irreversible. The address_key must be a withdrawal
        address previously whitelisted in the Kraken account settings.

        Args:
            asset: Ticker symbol, e.g. "XMR".
            amount: Amount to withdraw.
            address_key: Kraken withdrawal address key (label).

        Returns:
            Kraken response dict with 'refid' on success.
        """
        logger.warning("WITHDRAW %s amount=%s to key=%s", asset, amount, address_key)
        # Use ccxt's withdraw method which maps to Kraken's private/Withdraw endpoint
        result = self.exchange.withdraw(
            code=asset,
            amount=amount,
            address=address_key,  # ccxt passes this as 'key' for Kraken
            tag=None,
            params={"key": address_key},  # Explicit key for Kraken
        )
        return result

    def fetch_withdrawal_methods(self, asset: str) -> list[dict[str, Any]]:
        """Fetch available withdrawal methods for an asset."""
        try:
            result = self.exchange.private_post_withdrawmethods({
                "asset": asset,
            })
            return result.get("result", []) if isinstance(result, dict) else []
        except Exception as exc:
            logger.warning("WithdrawMethods failed: %s", exc)
            return []

    def fetch_withdrawal_addresses(self, asset: str | None = None) -> list[dict[str, Any]]:
        """Fetch pre-registered withdrawal addresses.

        Useful for the UI to let the user pick a cold-wallet key.
        """
        try:
            params = {}
            if asset:
                params["asset"] = asset
            result = self.exchange.private_post_withdrawaddresses(params)
            return result.get("result", []) if isinstance(result, dict) else []
        except Exception as exc:
            logger.warning("WithdrawAddresses failed: %s", exc)
            return []

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _minutes_to_timeframe(minutes: int) -> str:
        """Convert minutes to a ccxt timeframe string."""
        mapping = {
            1: "1m",
            5: "5m",
            15: "15m",
            30: "30m",
            60: "1h",
            240: "4h",
            1440: "1d",
            10080: "1w",
        }
        return mapping.get(minutes, "1h")

    def is_pair_available(self, pair: str) -> bool:
        """Check if a trading pair is available on Kraken."""
        try:
            markets = self.exchange.load_markets()
            return pair in markets
        except Exception as exc:
            logger.error("Failed to load markets: %s", exc)
            return False
