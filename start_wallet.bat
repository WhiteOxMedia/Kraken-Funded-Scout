@echo off
title Monero Wallet RPC — XMR Daily Jackpot
cd /d "%~dp0"

echo ============================================
echo   Monero Wallet RPC Startup
echo   XMR Daily Jackpot Pool
echo ============================================
echo.

REM Path to monero-wallet-rpc.exe
set WALLET_RPC=monero\monero-wallet-rpc.exe

if not exist "%WALLET_RPC%" (
    echo [ERROR] monero-wallet-rpc.exe not found.
    echo.
    echo Download the Monero CLI tools from:
    echo   https://www.getmonero.org/downloads/
    echo.
    echo Extract to: %cd%\monero\
    echo.
    echo Then run this script again.
    pause
    exit /b 1
)

REM Set wallet directory
set WALLET_DIR=monero\wallet
mkdir "%WALLET_DIR%" 2>nul

echo Wallet directory: %cd%\%WALLET_DIR%
echo.

REM Check if wallet exists
if exist "%WALLET_DIR%\jackpot_pool" (
    echo Wallet found. Starting RPC...
    "%WALLET_RPC%" ^
        --wallet-dir="%WALLET_DIR%" ^
        --wallet-file=jackpot_pool ^
        --rpc-bind-port=18082 ^
        --rpc-bind-ip=127.0.0.1 ^
        --daemon-address=node.moneroworld.com:18089 ^
        --confirm-external-bind ^
        --trusted-daemon ^
        --prompt-for-password
) else (
    echo No wallet found. Creating new wallet...
    echo.
    echo Creating wallet: jackpot_pool
    echo.
    "%WALLET_RPC%" ^
        --wallet-dir="%WALLET_DIR%" ^
        --generate-from-json="%WALLET_DIR%\create_wallet.json" ^
        --rpc-bind-port=18082 ^
        --rpc-bind-ip=127.0.0.1 ^
        --daemon-address=node.moneroworld.com:18089 ^
        --confirm-external-bind ^
        --trusted-daemon

    if errorlevel 1 (
        echo.
        echo [NOTE] If wallet creation failed, create it manually with monero-wallet-cli first:
        echo   monero\monero-wallet-cli.exe --generate-new-wallet jackpot_pool --wallet-dir="%WALLET_DIR%" --daemon-address=node.moneroworld.com:18089
        echo.
        echo Then run this script again.
        pause
    )
)

pause
