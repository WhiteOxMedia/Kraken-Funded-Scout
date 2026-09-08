@echo off
REM ============================================================
REM  Install Scout-to-Bot Signal Bridge
REM  Copies signal_bridge.py and scout_signal_reader.py to the
REM  kraken_xrp_trading_bot project and patches bot_engine.py
REM ============================================================

cd /d "%~dp0"

echo Installing signal bridge to kraken_xrp_trading_bot...

REM Copy the bridge files.
REM DIRECTIONS MATTER (the Sep 8 twin audit): signal_bridge flows
REM scout -> bot (the bot's reader resolves THIS project's bridge via
REM SCOUT_PATH, so this copy is the live one). The reader twin is the
REM REVERSE: the bot's scout_signal_reader.py is the live one (it carries
REM the day-start balance breaker, the DST-aware MST, the dynamic caps) —
REM a blind copy here would regress real-money protections.
copy "signal_bridge.py" "C:\Users\birds\Workspace\kraken_xrp_trading_bot\signal_bridge.py"

fc /b "scout_signal_reader.py" "C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   *** STOPPED: scout_signal_reader.py has DIVERGED from the bot's copy. ***
    echo   The BOT's reader is the live one ^(day-start breaker, dynamic caps,
    echo   DST MST^). Port anything new INTO the bot copy first, or diff:
    echo     fc "scout_signal_reader.py" "C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"
    echo   Nothing was copied over it.
    exit /b 1
)
copy "scout_signal_reader.py" "C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"

echo.
echo Files copied. Now you need to patch bot_engine.py manually:
echo.
echo 1. Open C:\Users\birds\Workspace\kraken_xrp_trading_bot\bot_engine.py
echo 2. Find the line:  def run_tick(self)
echo 3. Add these lines right after it (before the try:):
echo.
echo     from scout_signal_reader import check_scout_signals
echo     scout_result = check_scout_signals(self.kraken, self.config)
echo     if scout_result:
echo         return scout_result
echo.
echo 4. Save the file
echo 5. Restart the XRP bot
echo.
echo The scout will now write buy/sell signals to trade_signals.json
echo The XRP bot will read and execute them on your real Kraken account.
echo.
pause
