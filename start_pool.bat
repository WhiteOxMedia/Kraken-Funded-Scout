@echo off
title XMR Daily Jackpot Pool Server
cd /d "%~dp0"

echo ============================================
echo   XMR Daily Jackpot Pool - Starting...
echo ============================================
echo.

echo [1/2] Starting mining proxy (port 3333)...
start "Mining Proxy" cmd /c "monero\xmrig-proxy.exe --config=monero\proxy_config.json"

echo [2/2] Starting web dashboard + API...
start "Pool Dashboard" .venv\Scripts\python.exe pool\server.py

echo.
echo ============================================
echo   Pool is running!
echo.
echo   Mining Proxy:  YOUR_IP:3333
echo   Dashboard:    http://localhost:3094
echo   API:          http://localhost:3094/api/stats
echo   Proxy API:    http://127.0.0.1:9090
echo.
echo   Players connect with:
echo   xmrig -o YOUR_IP:3333 -u WALLET.worker -p x
echo.
echo   Mined XMR funds the jackpot.
echo   Players earn free tickets from shares.
echo ============================================
echo.
echo Close both popup windows to stop.
pause
