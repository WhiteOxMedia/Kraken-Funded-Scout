@echo off
title XMR Daily Jackpot — Mining Proxy
cd /d "%~dp0"

echo ============================================
echo   Mining Proxy — XMR Daily Jackpot Pool
echo ============================================
echo.
echo   Players connect to: YOUR_IP:3333
echo   Wallet: House (Kraken) — mined XMR funds jackpot
echo   API: http://127.0.0.1:9090
echo   Upstream: pool.supportxmr.com:3333
echo.
echo   Players earn free tickets from shares.
echo   Mined XMR goes to jackpot pool.
echo.
echo ============================================
echo.

monero\xmrig-proxy.exe --config=monero\proxy_config.json

pause
