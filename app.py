"""Kraken Scout — simplified daily best-pick dashboard.

Scans all 54 Kraken Funded challenge assets, finds the single best
trade opportunity, and shows: what to buy/sell, why, entry/stop/target,
how much to spend, potential profit, and expected time frame.
"""
from __future__ import annotations

import logging

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config import TradingConfig
from indicators import bollinger_bands, ema, rsi
from scanner import (
    ScanResult,
    compute_position_sizing,
    estimate_timeframe,
    scan_pairs,
)

try:
    import db
    from secrets_utils import setup_secrets
    import keyring
    from kraken_api import KrakenAPI
    _HAS_DB = True
except Exception:
    _HAS_DB = False

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Kraken Scout", page_icon="🎯", layout="wide")


# ── Helpers ──────────────────────────────────────────────────────────────

def _fmt_price(p: float | None) -> str:
    if p is None:
        return "—"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.8f}"


def _asset_name(pair: str) -> str:
    return pair.split("/")[0] if "/" in pair else pair


def _get_kraken_client():
    """Create a Kraken API client. Uses keyring if available, else bare ccxt."""
    if _HAS_DB:
        try:
            setup_secrets()
            api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
            api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
            return KrakenAPI(api_key, api_secret)
        except Exception:
            pass
    import ccxt
    class _BareKraken:
        exchange = ccxt.kraken({"enableRateLimit": True})
        def fetch_ohlcv(self, pair, interval_minutes=60, limit=200):
            tf = {1:"1m",5:"5m",15:"15m",30:"30m",60:"1h",240:"4h",1440:"1d"}.get(interval_minutes,"1h")
            return self.exchange.fetch_ohlcv(pair, timeframe=tf, limit=limit)
        def fetch_ticker(self, pair):
            return self.exchange.fetch_ticker(pair)
        def fetch_price(self, pair):
            return float(self.exchange.fetch_ticker(pair)["last"])
    return _BareKraken()


def _render_chart(result: ScanResult, signal, config: TradingConfig) -> go.Figure | None:
    """Build a candlestick chart with entry/stop/TP lines and indicators."""
    df = result.ohlcv_df
    if df is None or df.empty:
        return None

    bb = bollinger_bands(df, config.bollinger_period, config.bollinger_std)
    ema_s = ema(df["close"], config.ema_short)
    ema_l = ema(df["close"], config.ema_long)
    rsi_s = rsi(df, config.rsi_period)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.08, row_heights=[0.75, 0.25],
        subplot_titles=("Price", "RSI"),
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#00d68f", decreasing_line_color="#ff5252",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=bb["upper"], name="BB Upper",
                             line=dict(color="#555", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb["lower"], name="BB Lower",
                             line=dict(color="#555", width=1, dash="dot"),
                             fill="tonexty", fillcolor="rgba(85,85,85,0.08)"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=ema_s, name=f"EMA {config.ema_short}",
                             line=dict(color="#4BA9FE", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema_l, name=f"EMA {config.ema_long}",
                             line=dict(color="#FFB347", width=1.5)), row=1, col=1)

    if signal.entry_price:
        fig.add_hline(y=signal.entry_price, line_dash="solid", line_color="#00d68f",
                      line_width=2, annotation_text=f"Entry {_fmt_price(signal.entry_price)}",
                      row=1, col=1)
    if signal.stop_loss:
        fig.add_hline(y=signal.stop_loss, line_dash="dash", line_color="#ff5252",
                      line_width=2, annotation_text=f"Stop {_fmt_price(signal.stop_loss)}",
                      row=1, col=1)
    if signal.take_profit:
        fig.add_hline(y=signal.take_profit, line_dash="dash", line_color="#4BA9FE",
                      line_width=2, annotation_text=f"TP {_fmt_price(signal.take_profit)}",
                      row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=rsi_s, name="RSI",
                             line=dict(color="#b388ff", width=1.5)), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=480,
        xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


# ── App ──────────────────────────────────────────────────────────────────

def main():
    cfg = TradingConfig()

    # ── Sidebar ───────────────────────────────────────────────────────
    st.sidebar.markdown("### ⚙️ Settings")

    challenge_tier = st.sidebar.selectbox(
        "Challenge tier", ["$1,000", "$5,000", "$10,000"], index=0,
    )
    cfg.challenge_balance = float(challenge_tier.replace("$", "").replace(",", ""))

    candle_options = {"15 min": 15, "1 hour": 60, "4 hours": 240, "1 day": 1440}
    candle_label = st.sidebar.selectbox("Chart timeframe", list(candle_options.keys()), index=1)
    cfg.candle_interval = candle_options[candle_label]

    cfg.risk_per_trade_pct = st.sidebar.slider("Risk per trade (%)", 0.5, 2.0, 1.0, 0.1)
    cfg.risk_reward_ratio = st.sidebar.slider("Risk : Reward ratio", 1.5, 4.0, 2.0, 0.5)
    cfg.atr_multiplier = st.sidebar.slider("ATR multiplier (stop distance)", 1.0, 3.0, 1.5, 0.1)
    cfg.min_confidence = st.sidebar.slider("Min confidence (%)", 20, 80, 40, 5)

    st.sidebar.markdown("---")
    st.sidebar.caption("⚡ **Background scanner** runs separately.\n\nDouble-click **Scout Scanner** on your desktop for auto-tournaments + desktop notifications.")

    # ── Check if background scanner is running ───────────────────────
    try:
        import subprocess
        result = subprocess.run(
            ['powershell', '-Command',
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*background_scanner*' } | Select-Object -First 1 ProcessId"],
            capture_output=True, text=True, timeout=5
        )
        scanner_running = bool(result.stdout.strip())
    except Exception:
        scanner_running = False

    if scanner_running:
        st.sidebar.success("⚡ Scanner: **RUNNING**")
    else:
        st.sidebar.warning("⚡ Scanner: **OFFLINE**")

    # ── Kill switch status + reset ───────────────────────────────────
    try:
        import sys as _sys
        _sys.path.insert(0, r"C:\Users\birds\Workspace\kraken_scout")
        from signal_bridge import is_kill_switch_active, get_kill_switch_reason, reset_kill_switch
        if is_kill_switch_active():
            reason = get_kill_switch_reason() or "Unknown"
            st.sidebar.error(f"🛑 Kill switch: **ACTIVE**\n\n{reason}")
            if st.sidebar.button("🔓 Reset Kill Switch", key="reset_ks"):
                reset_kill_switch()
                st.rerun()
        else:
            st.sidebar.info("🟢 Kill switch: **CLEAR**")
    except Exception:
        st.sidebar.caption("Kill switch: unknown")


    st.divider()

    # ── Quick Check: single asset ─────────────────────────────────────
    st.markdown("### 🔍 Quick Check")
    st.caption("Check live price and signal for any single asset — catches breakouts before they become big moves.")

    _QC_ASSETS = [
        "BTC","ETH","LINK","BCH","DOGE","ARB","AAVE","AVAX","BNB","CRV",
        "DOT","FIL","GRASS","ADA","HBAR","INJ","KAITO","LDO","LTC","MOODENG",
        "NEAR","PNUT","SOL","S","TRUMP","UNI","WLD","XRP","ALGO","APT",
        "ATOM","ETC","JTO","JUP","ONDO","OP","POL","POPCAT","RENDER","STX",
        "SUI","TAO","TIA","TRX","WIF","AIXBT","ASTER","FARTCOIN","HYPE",
        "PENGU","PUMP","VIRTUAL","XPL","ZEC",
    ]

    qc_col1, qc_col2 = st.columns([2, 1])
    with qc_col1:
        qc_asset = st.selectbox("Asset to check", _QC_ASSETS, index=0, key="qc_asset")
    with qc_col2:
        qc_clicked = st.button("Check", type="primary", key="qc_btn")

    if qc_clicked:
        kraken = _get_kraken_client()
        pair = f"{qc_asset}/USD"
        with st.spinner(f"Scanning {qc_asset}…"):
            try:
                # Fetch live price
                live_price = kraken.fetch_price(pair)

                # Fetch OHLCV and run strategy
                import pandas as pd
                from strategy import evaluate_strategy as _eval
                from indicators import bollinger_bands as _bb, ema as _ema, rsi as _rsi

                ohlcv = kraken.fetch_ohlcv(pair, interval_minutes=cfg.candle_interval, limit=cfg.lookback_candles)
                df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)

                sig = _eval(df, cfg)

                # Display
                if sig.action == "buy":
                    st.success(f"# 🟢 BUY {qc_asset} — {sig.confidence:.0f}%")
                elif sig.action == "sell":
                    st.error(f"# 🔴 SELL {qc_asset} — {sig.confidence:.0f}%")
                else:
                    st.info(f"# ⏸️ HOLD {qc_asset} — {sig.confidence:.0f}%")

                st.markdown(f"**Live price:** {_fmt_price(live_price)}  ·  **Regime:** {sig.regime}  ·  **Volatility:** {sig.volatility_rank:.0f}/100")

                # Levels
                if sig.entry_price:
                    qc1, qc2, qc3, qc4 = st.columns(4)
                    qc1.metric("Entry", _fmt_price(sig.entry_price))
                    qc2.metric("Stop Loss", _fmt_price(sig.stop_loss))
                    qc3.metric("Take Profit", _fmt_price(sig.take_profit))
                    qc4.metric("R:R", f"1:{sig.risk_reward or 0:.1f}")

                    sizing = compute_position_sizing(sig, cfg)
                    if sizing:
                        st.markdown(f"**Buy ${sizing.position_size_usd:.0f}** → Risk ${sizing.risk_amount_usd:.0f} | Target profit ${sizing.potential_profit_usd:.0f}")

                # Reasons
                st.markdown("#### Why")
                for r in sig.reasons[:8]:
                    st.markdown(f"- {r}")

                # Chart
                chart = _render_chart(ScanResult(pair=pair, signal=sig, ohlcv_df=df), sig, cfg)
                if chart:
                    st.plotly_chart(chart, width="stretch", key="qc_chart")

            except Exception as exc:
                st.error(f"Could not check {qc_asset}: {exc}")

    st.divider()

    # ── Scan ──────────────────────────────────────────────────────────
    # ── Scan buttons ──────────────────────────────────────────────────
    scan_clicked = st.sidebar.button("🔍 Scan Now", type="primary", width="stretch")
    winners_clicked = st.sidebar.button("🏆 Winners Only", width="stretch")

    if scan_clicked or winners_clicked:
        kraken = _get_kraken_client()
        from strategy import enable_deep_analysis, disable_deep_analysis
        from scanner import tournament_scan, _evaluate_single

        # Fast mode for the tournament — deep analysis only on the champion
        disable_deep_analysis()

        is_winners_mode = winners_clicked

        if is_winners_mode and _HAS_DB:
            try:
                db.init_db()
            except Exception:
                pass
            winner_pairs = db.get_winner_pairs(limit=20)
            if len(winner_pairs) < 2:
                st.warning("Not enough tournament winners yet. Run a full Scan Now first to build the winner pool.")
                st.stop()
            scan_cfg = TradingConfig()
            scan_cfg.trading_pairs = [p if "/" in p else f"{p}/USD" for p in winner_pairs]
            scan_cfg.candle_interval = cfg.candle_interval
            scan_cfg.lookback_candles = cfg.lookback_candles
            scan_cfg.risk_per_trade_pct = cfg.risk_per_trade_pct
            scan_cfg.risk_reward_ratio = cfg.risk_reward_ratio
            scan_cfg.atr_multiplier = cfg.atr_multiplier
            scan_cfg.min_confidence = cfg.min_confidence
            for attr in ["rsi_period","rsi_oversold","rsi_overbought","macd_fast","macd_slow",
                         "macd_signal","bollinger_period","bollinger_std","ema_short","ema_long",
                         "stochastic_period","stochastic_oversold","stochastic_overbought","atr_period"]:
                setattr(scan_cfg, attr, getattr(cfg, attr))
            scan_cfg.challenge_balance = cfg.challenge_balance
            scan_label = f"winners-only ({len(winner_pairs)} champions)"
        else:
            scan_cfg = cfg
            scan_label = f"all {len(cfg.trading_pairs)} assets"

        # ── Live tournament display (HTML for colors) ─────────────────
        st.markdown(f"### 🏆 Tournament — {scan_label}")
        terminal = st.empty()

        def _act_label(act):
            if act == "buy": return "BUY"
            if act == "sell": return "SELL"
            return "HOLD"

        def _color_for_action(act):
            if act == "buy": return "#00d68f"
            if act == "sell": return "#ff5252"
            return "#888"

        def on_progress(m):
            st.session_state.setdefault("_match_log", []).append(m)

            a = m["pair_a"].split("/")[0] if "/" in m["pair_a"] else m["pair_a"]
            b = m["pair_b"].split("/")[0] if "/" in m["pair_b"] else m["pair_b"]
            w = m["winner"].split("/")[0] if "/" in m["winner"] else m["winner"]
            l = m["loser"].split("/")[0] if "/" in m["loser"] else m["loser"]

            a_color = _color_for_action(m["a_action"])
            b_color = _color_for_action(m["b_action"])

            html = f"""
            <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:16px;font-family:'Consolas','Monaco',monospace;font-size:14px;color:#c9d1d9;">
              <div style="color:#58a6ff;margin-bottom:8px;">ROUND {m['round']} | MATCH {m['match']}/{m['total_matches']}</div>
            """

            if m["pair_b"] == "(bye)":
                html += f"""
                <div style="margin:8px 0;">
                  <span style="color:{a_color};font-weight:bold;font-size:18px;">{a}</span>
                  <span style="color:#666;"> — BYE — advances</span>
                </div>
                """
            else:
                html += f"""
                <div style="margin:4px 0;">
                  <span style="color:{a_color};font-weight:bold;font-size:20px;">{a}</span>
                  <span style="color:#666;font-size:14px;"> [{_act_label(m['a_action'])} {m['a_conf']:.0f}%]</span>
                </div>
                <div style="color:#666;text-align:center;font-size:12px;margin:2px 0;">VS</div>
                <div style="margin:4px 0;">
                  <span style="color:{b_color};font-weight:bold;font-size:20px;">{b}</span>
                  <span style="color:#666;font-size:14px;"> [{_act_label(m['b_action'])} {m['b_conf']:.0f}%]</span>
                </div>
                <div style="margin:8px 0;font-size:16px;">
                  <span style="color:#00d68f;font-weight:bold;">✅ {w}</span>
                  <span style="color:#666;margin:0 8px;">|</span>
                  <span style="color:#ff5252;text-decoration:line-through;">❌ {l}</span>
                </div>
                """

            # Advancers
            all_matches = st.session_state["_match_log"]
            current_round_matches = [h for h in all_matches if h["round"] == m["round"]]
            if current_round_matches:
                advancers = [h["winner"].split("/")[0] if "/" in h["winner"] else h["winner"] for h in current_round_matches]
                html += f'<div style="color:#8b949e;font-size:12px;margin-top:6px;">Advancing: {", ".join(advancers)}</div>'

            html += "</div>"
            terminal.markdown(html, unsafe_allow_html=True)

        # Reset match log
        st.session_state["_match_log"] = []

        with st.spinner(f"Running tournament..."):
            champion, history = tournament_scan(kraken, scan_cfg, progress_callback=on_progress)

            # Deep-analyze the champion
            if champion.ohlcv_df is not None:
                terminal.markdown("""
                <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:16px;font-family:'Consolas','Monaco',monospace;color:#c9d1d9;text-align:center;">
                  <div style="color:#8b949e;">Deep analyzing champion...</div>
                </div>
                """, unsafe_allow_html=True)
                enable_deep_analysis()
                from strategy import evaluate_strategy as _eval
                champion.signal = _eval(champion.ohlcv_df, cfg)
                disable_deep_analysis()

            # ── Champion display — big, centered, colored ──────────────
            asset_name = _asset_name(champion.pair)
            sig = champion.signal
            champ_color = _color_for_action(sig.action)

            champ_html = f"""
            <div style="background:linear-gradient(180deg,#0d1117,#161b22);border:2px solid {champ_color};
                        border-radius:12px;padding:24px;text-align:center;margin:8px 0;">
              <div style="font-size:14px;color:#8b949e;letter-spacing:3px;margin-bottom:8px;">
                🏆 TOURNAMENT CHAMPION 🏆
              </div>
              <div style="font-size:48px;font-weight:900;color:{champ_color};line-height:1.1;margin:8px 0;">
                {asset_name}
              </div>
              <div style="font-size:20px;font-weight:bold;color:{champ_color};margin:4px 0;">
                {_act_label(sig.action)} — {sig.confidence:.0f}% confidence
              </div>
            """
            if sig.entry_price:
                champ_html += f"""
                <div style="display:flex;justify-content:center;gap:24px;margin:12px 0;font-family:'Consolas',monospace;font-size:15px;">
                  <div><span style="color:#8b949e;">Entry</span><br><span style="color:#c9d1d9;font-weight:bold;">{_fmt_price(sig.entry_price)}</span></div>
                  <div><span style="color:#ff5252;">Stop</span><br><span style="color:#ff5252;font-weight:bold;">{_fmt_price(sig.stop_loss)}</span></div>
                  <div><span style="color:#00d68f;">Target</span><br><span style="color:#00d68f;font-weight:bold;">{_fmt_price(sig.take_profit)}</span></div>
                  <div><span style="color:#8b949e;">R:R</span><br><span style="color:#c9d1d9;font-weight:bold;">1:{sig.risk_reward or 0:.1f}</span></div>
                </div>
                """
            champ_html += "</div>"

            terminal.markdown(champ_html, unsafe_allow_html=True)

            st.session_state.scan_results = [champion]
            st.session_state.scan_cfg = cfg
            st.session_state.tournament_history = history
            st.session_state.scan_type = "winners" if is_winners_mode else "full"

            # Log the champion
            if _HAS_DB:
                try:
                    phase_str = ""
                    for r in champion.signal.reasons:
                        if "Phase" in r:
                            # Extract just the phase label (e.g., "Phase 2 — Momentum")
                            parts = r.split("—")
                            if len(parts) >= 2:
                                phase_str = f"{parts[0].strip()} — {parts[1].strip()}"[:30]
                            else:
                                phase_str = r[:30]
                            break
                    db.log_tournament_winner(
                        pair=champion.pair, action=champion.signal.action,
                        confidence=champion.signal.confidence, price=champion.signal.price,
                        entry_price=champion.signal.entry_price,
                        stop_loss=champion.signal.stop_loss,
                        take_profit=champion.signal.take_profit,
                        risk_reward=champion.signal.risk_reward,
                        regime=champion.signal.regime,
                        phase=phase_str,
                        reasons=[r[:200] for r in champion.signal.reasons[:5]],
                        scan_type="winners" if is_winners_mode else "full",
                    )
                    db.log_signal(
                        pair=champion.pair, action=champion.signal.action,
                        confidence=champion.signal.confidence, price=champion.signal.price,
                        indicators=champion.signal.indicators, patterns=champion.signal.patterns,
                        reasons=champion.signal.reasons,
                        entry_price=champion.signal.entry_price,
                        stop_loss=champion.signal.stop_loss,
                        take_profit=champion.signal.take_profit,
                        risk_reward=champion.signal.risk_reward,
                    )
                except Exception:
                    pass

        disable_deep_analysis()
        st.divider()

    results: list[ScanResult] = st.session_state.get("scan_results", [])
    scan_cfg: TradingConfig = st.session_state.get("scan_cfg", cfg)

    # ── Exit Check (always available) ─────────────────────────────────
    st.markdown("### 🚪 Exit Check")
    st.caption("Before selling, check if the trend is still intact.")

    _EC_ASSETS = [
        "BTC","ETH","LINK","BCH","DOGE","ARB","AAVE","AVAX","BNB","CRV",
        "DOT","FIL","GRASS","ADA","HBAR","INJ","KAITO","LDO","LTC","MOODENG",
        "NEAR","PNUT","SOL","S","TRUMP","UNI","WLD","XRP","ALGO","APT",
        "ATOM","ETC","JTO","JUP","ONDO","OP","POL","POPCAT","RENDER","STX",
        "SUI","TAO","TIA","TRX","WIF","AIXBT","ASTER","FARTCOIN","HYPE",
        "PENGU","PUMP","VIRTUAL","XPL","ZEC",
    ]

    ec_col1, ec_col2 = st.columns([3, 1])
    with ec_col1:
        ec_asset = st.selectbox("Asset you're holding", _EC_ASSETS, index=0, key="ec_asset")
    with ec_col2:
        ec_clicked = st.button("Should I exit?", type="primary", key="ec_btn")

    if ec_clicked:
        kraken = _get_kraken_client()
        pair = f"{ec_asset}/USD"
        with st.spinner(f"Checking {ec_asset}..."):
            try:
                import pandas as pd
                from strategy import evaluate_strategy as _eval

                live_price = kraken.fetch_price(pair)
                ohlcv = kraken.fetch_ohlcv(pair, interval_minutes=cfg.candle_interval, limit=cfg.lookback_candles)
                df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)

                sig = _eval(df, cfg)

                st.markdown(f"**Live price:** {_fmt_price(live_price)}")
                st.markdown(f"**Current signal:** {sig.action} ({sig.confidence:.0f}%)")

                exit_reasons = []
                if sig.action == "sell":
                    exit_reasons.append("Strategy says SELL — trend has reversed")
                if sig.action == "hold" and sig.confidence < 30:
                    exit_reasons.append("Signal weakened to hold with low confidence — momentum fading")
                if any("exhaustion" in r.lower() for r in sig.reasons):
                    exit_reasons.append("Exhaustion signals detected — trend may be dying")
                if any("exit signal" in r.lower() for r in sig.reasons):
                    exit_reasons.append("Exit conditions triggered by momentum rider")

                if exit_reasons:
                    st.error("🚪 **EXIT RECOMMENDED** — reasons:")
                    for r in exit_reasons:
                        st.markdown(f"  ❌ {r}")
                else:
                    st.success("✅ **HOLD** — trend is still intact. No exit signals detected.")

                st.markdown("**Reasons:**")
                for r in sig.reasons[:6]:
                    st.markdown(f"  - {r}")

            except Exception as exc:
                st.error(f"Could not check {ec_asset}: {exc}")

    st.divider()

    if not results:
        st.info("Click **Scan Now** to find today's best trade.")
        return

    best: ScanResult = results[0]
    signal = best.signal
    sizing = compute_position_sizing(signal, scan_cfg)

    # ── Tournament bracket ────────────────────────────────────────────
    history = st.session_state.get("tournament_history", [])
    scan_type = st.session_state.get("scan_type", "full")
    if history:
        type_label = "🏆 Winners Tournament" if scan_type == "winners" else "🔍 Full Tournament"
        with st.expander(f"{type_label} Bracket ({len(history)} matches)", expanded=False):
            current_round = 0
            for m in history:
                if m["round"] != current_round:
                    current_round = m["round"]
                    st.markdown(f"**Round {current_round}**")
                winner_icon = "✅"
                loser_icon = "❌"
                st.markdown(
                    f"{winner_icon} **{_asset_name(m['winner'])}** vs {loser_icon} {_asset_name(m['loser'])} "
                    f"— {m['reason']}"
                )

    # ── Tournament winner history ─────────────────────────────────────
    if _HAS_DB:
        try:
            winners = db.get_tournament_winners(limit=15)
            if winners:
                with st.expander(f"📜 Champion History ({len(winners)} recent)", expanded=False):
                    for w in winners:
                        icon = "🟢" if w.action == "buy" else "🔴" if w.action == "sell" else "⏸️"
                        # Convert UTC to MST for display
                        from datetime import timezone as tz, timedelta
                        if w.timestamp:
                            if w.timestamp.tzinfo is None:
                                ts_mst = w.timestamp.replace(tzinfo=tz.utc).astimezone(tz(timedelta(hours=-7)))
                            else:
                                ts_mst = w.timestamp.astimezone(tz(timedelta(hours=-7)))
                            time_str = ts_mst.strftime("%b %d, %I:%M %p")
                        else:
                            time_str = "—"
                        type_tag = {"winners": "🏆", "auto": "⚡", "full": "🔍"}.get(w.scan_type, "🔍")
                        st.markdown(
                            f"{icon} {type_tag} **{w.action.upper()} {_asset_name(w.pair)}** "
                            f"({w.confidence:.0f}%) — {_fmt_price(w.entry_price) if w.entry_price else '—'} "
                            f"| TP: {_fmt_price(w.take_profit) if w.take_profit else '—'} "
                            f"| {time_str}"
                        )
        except Exception:
            pass
    timeframe = estimate_timeframe(signal, scan_cfg)
    asset = _asset_name(best.pair)

    # ── The Pick ──────────────────────────────────────────────────────
    if signal.action == "buy":
        st.success(f"# 🟢 BUY {asset}")
    else:
        st.error(f"# 🔴 SELL {asset}")
    st.markdown(f"**Confidence: {signal.confidence:.0f}%**  ·  **Regime: {signal.regime}**  ·  **Volatility: {signal.volatility_rank:.0f}/100**")

    # ── Confluence + stability + liquidity ────────────────────────────
    q1, q2, q3, q4 = st.columns(4)
    with q1:
        mtf_label = "✅" if best.mtf_confluent else "❌"
        st.metric("MTF Confluence", f"{best.mtf_agreement:.0f}/3 {mtf_label}",
                  " · ".join(f"{k}:{v}" for k, v in best.mtf_signals.items()))
    with q2:
        stab_label = "✅" if best.stable else "❌"
        st.metric("Signal Stability", f"{best.stability_score:.0f}% {stab_label}")
    with q3:
        spread_str = f"{best.spread_pct:.3f}%" if best.spread_pct is not None else "—"
        st.metric("Spread", spread_str)
    with q4:
        vol_str = f"${best.volume_24h_usd/1e6:.1f}M" if best.volume_24h_usd else "—"
        st.metric("24h Volume", vol_str)

    # ── Key levels ────────────────────────────────────────────────────
    st.markdown("#### Trade Levels")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entry", _fmt_price(signal.entry_price))
    c2.metric("Stop Loss", _fmt_price(signal.stop_loss))
    c3.metric("Take Profit", _fmt_price(signal.take_profit))
    c4.metric("Risk : Reward", f"1 : {signal.risk_reward or 0:.1f}")

    # ── Position sizing + target ──────────────────────────────────────
    if sizing:
        st.markdown("#### Position Sizing")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Buy Amount", _fmt_price(sizing.position_size_usd))
        s2.metric("Risk (if stop)", _fmt_price(sizing.risk_amount_usd))
        s3.metric("Target Profit", _fmt_price(sizing.potential_profit_usd))
        s4.metric("Est. Time Frame", timeframe)

    st.markdown("")

    # ── Why this pick ─────────────────────────────────────────────────
    st.markdown("#### Why this pick")
    for reason in signal.reasons:
        st.markdown(f"- {reason}")

    # ── Pattern summary ───────────────────────────────────────────────
    pats = signal.patterns
    if pats:
        st.markdown("#### Pattern Analysis")
        pcol1, pcol2, pcol3 = st.columns(3)
        with pcol1:
            trend = pats.get("trend", {})
            st.metric("Trend", trend.get("trend", "—").title(),
                      f"strength {trend.get('strength', 0):.0f}")
        with pcol2:
            bias = pats.get("overall_bias", "—")
            st.metric("Bias", bias.title(),
                      f"bull {pats.get('bullish_count', 0)} / bear {pats.get('bearish_count', 0)}")
        with pcol3:
            sr = pats.get("support_resistance", {}) or {}
            st.metric("Support", _fmt_price(sr.get("nearest_support")),
                      f"Resistance: {_fmt_price(sr.get('nearest_resistance'))}")

    # ── Chart ─────────────────────────────────────────────────────────
    st.markdown("#### Chart")
    chart = _render_chart(best, signal, scan_cfg)
    if chart:
        st.plotly_chart(chart, width="stretch", key="scan_chart")

    # ── Pre-entry checklist ───────────────────────────────────────────
    st.divider()
    st.markdown("### ✅ Pre-Entry Checklist")
    st.caption("Run through this before executing the trade in the Kraken app.")

    checks = []

    # Confidence check
    conf_ok = signal.confidence >= 60
    checks.append((conf_ok, f"Signal confidence ≥ 60% (currently {signal.confidence:.0f}%)"))

    # Phase stable check (from reasons)
    phase_stable = "stable" in " ".join(signal.reasons).lower()
    checks.append((phase_stable, "Phase is stable (not transitional)"))

    # No exhaustion
    no_exhaustion = not any("exhaustion" in r.lower() for r in signal.reasons)
    checks.append((no_exhaustion, "No exhaustion signals detected"))

    # No breakout failure
    no_bf = not any("breakout failure" in r.lower() for r in signal.reasons)
    checks.append((no_bf, "No breakout failure detected"))

    # Entry/stop/TP present
    has_levels = signal.entry_price and signal.stop_loss and signal.take_profit
    checks.append((has_levels, "Entry, stop-loss, and take-profit levels computed"))

    # Position sizing
    has_sizing = sizing is not None
    checks.append((has_sizing, f"Position size calculated ({_fmt_price(sizing.position_size_usd) if sizing else 'N/A'})"))

    # R:R check
    rr_ok = signal.risk_reward and signal.risk_reward >= 2.0
    checks.append((rr_ok, f"Risk:Reward ≥ 1:2 (currently 1:{signal.risk_reward or 0:.1f})"))

    all_pass = all(c[0] for c in checks)
    for passed, label in checks:
        icon = "✅" if passed else "❌"
        st.markdown(f"{icon} {label}")

    if all_pass:
        st.success("✅ ALL CHECKS PASSED — safe to enter this trade.")
    else:
        st.warning("⚠️ Some checks failed — consider waiting for a better setup.")

    # ── Check-back timer ──────────────────────────────────────────────
    if sizing and signal.entry_price:
        tf = estimate_timeframe(signal, scan_cfg)
        st.markdown(f"### ⏰ Check Back In: {tf}")

        # Calculate actual time
        from datetime import datetime, timezone, timedelta
        atr_val = signal.indicators.get("atr")
        if atr_val and signal.take_profit:
            tp_dist = abs(signal.take_profit - signal.entry_price)
            candles = tp_dist / atr_val if atr_val > 0 else 0
            minutes = candles * scan_cfg.candle_interval
            check_at = datetime.now(timezone.utc) + timedelta(minutes=minutes // 2)
            check_at_mst = check_at.astimezone(timezone(timedelta(hours=-7)))
            st.info(f"Check back around **{check_at_mst.strftime('%I:%M %p MST')}** (halfway to TP). "
                    f"Full TP estimate: {tf}. "
                    f"Scan the single asset before exiting to confirm the trend is still intact.")

    # ── Scan schedule ─────────────────────────────────────────────────
    st.markdown("### 📅 Scan Schedule")
    interval = scan_cfg.candle_interval
    if interval <= 15:
        schedule = "Every 30 minutes — 15m candles move fast"
        freq = 30
    elif interval <= 60:
        schedule = "Every 2 hours — 1h candles need time to develop"
        freq = 120
    elif interval <= 240:
        schedule = "Every 4-6 hours — 4h candles are slower"
        freq = 300
    else:
        schedule = "Every 8-12 hours — daily candles"
        freq = 600
    st.info(f"**{schedule}.** Run the single-asset Quick Check before entering. "
            f"Run the full Scan Now before deciding your next trade. "
            f"At minimum, check back every {freq} minutes.")


if __name__ == "__main__":
    main()
