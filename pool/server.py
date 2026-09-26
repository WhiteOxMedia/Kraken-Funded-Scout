"""
FastAPI backend for the XMR Jackpot Pool.
Serves the web dashboard, static assets, and JSON API endpoints.
"""
import os
import sys
import time
import secrets
import hashlib
from pathlib import Path

import asyncio

import aiohttp
from fastapi import FastAPI, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

import database as db
import jackpot_engine as engine
import security
import monero_wallet as xmr

app = FastAPI(title="XMR Jackpot Pool")

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Initialize database
db.init_db()


# ─── API Models ──────────────────────────────────────────────
class MinerRegister(BaseModel):
    wallet_address: str
    worker_name: str = ""


class UpdateShares(BaseModel):
    wallet_address: str
    worker_name: str = ""
    shares_valid: int = 0
    shares_invalid: int = 0
    hashrate: float = 0


# ─── Frontend Route ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main game dashboard."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))



# ─── API Key Auth ────────────────────────────────────────────

def check_api_key(request: Request):
    """Check for valid API key in Authorization header. Returns True if valid."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        stored_key = db.get_setting("api_key", "")
        active = db.get_setting("api_key_active", "0") == "1"
        if active and stored_key and token == stored_key:
            return True
    return False


# ─── Mobile API Endpoints ────────────────────────────────────

@app.get("/api/v1/status")
async def api_v1_status(request: Request):
    """Mobile API: pool status, jackpot, countdown, miners. Requires API key."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    stats = engine.get_pool_stats()
    sp = await api_social_proof()
    return {
        "jackpot_xmr": stats["jackpot_xmr"],
        "jackpot_usd": stats["jackpot_usd"],
        "total_miners": stats["total_miners"],
        "active_miners": stats["active_miners"],
        "total_hashrate": stats["total_hashrate"],
        "real_miners": stats.get("real_miners", 0),
        "total_entries": sp.get("total_entries", 0),
        "total_paid_out": sp.get("total_paid_out", 0),
        "total_drawings": sp.get("total_drawings", 0),
        "next_drawing_ts": sp.get("next_drawing_ts"),
        "now": sp.get("now"),
        "recent_winners": sp.get("recent_winners", []),
        "top_miner": sp.get("top_miner"),
        "announcement": {
            "active": db.get_setting("announcement_active", "0") == "1",
            "message": db.get_setting("announcement_message", ""),
        },
    }


@app.post("/api/v1/login")
async def api_v1_login(request: Request):
    """Mobile API: login with username/password, returns user info. Requires API key."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    user = db.get_user_by_username(username)
    if not user or user["password_hash"] != hash_password(password):
        raise HTTPException(401, "Invalid credentials")
    db.update_last_login(user["id"])
    token = create_session_token()
    db.create_session(token, user["id"], time.time() + 86400 * 7, request.client.host if request.client else "mobile")
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'
    response = JSONResponse({
        "status": "ok",
        "session_token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
        }
    })
    response.set_cookie("session", token, httponly=True, secure=is_https, samesite="lax", max_age=86400*7)
    return response


@app.get("/api/v1/user")
async def api_v1_user(request: Request):
    """Mobile API: get user stats, entries, gamification. Requires API key + session."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(401, "Not logged in")
    wallet = user.get("wallet_address", "")
    miner = None
    tickets = 0
    if wallet and len(wallet) > 50:
        miner = db.get_or_create_miner(wallet)
        tickets = db.get_miner_ticket_count(miner["id"])
    with db.get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL").fetchone()
        total_entries = row["cnt"]
    win_chance = round((tickets / total_entries * 100), 2) if total_entries > 0 else 0
    # Check for pending winner claim
    winner_status = None
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT d.*, m.worker_name FROM drawings d
               JOIN miners m ON d.winner_id = m.id
               WHERE d.status='drawn' AND d.claim_status='pending' AND m.worker_name=?
               ORDER BY d.drawn_at DESC LIMIT 1""",
            (user["username"],),
        ).fetchone()
        if row:
            winner_status = {
                "drawing_id": row["id"],
                "payout_amount": round(row["jackpot_amount"] - (row["house_tax"] or 0), 6),
                "claim_deadline": row["claim_deadline"],
                "has_wallet": bool(wallet and len(wallet) > 50),
            }
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
        },
        "your_entries": tickets,
        "total_entries": total_entries,
        "win_chance": win_chance,
        "miner": miner,
        "winner_claim": winner_status,
    }


# ─── Announcement ────────────────────────────────────────────

@app.get("/api/announcement")
async def api_announcement():
    """Get site-wide announcement. Public endpoint."""
    msg = db.get_setting("announcement_message", "")
    active = db.get_setting("announcement_active", "0") == "1"
    return {"active": active, "message": msg}


@app.post("/api/admin/announcement")
async def api_admin_set_announcement(request: Request):
    """Set or clear site-wide announcement. Admin only."""
    admin = require_admin(request)
    data = await request.json()
    msg = data.get("message", "")
    active = data.get("active", False)
    db.set_setting("announcement_message", msg)
    db.set_setting("announcement_active", "1" if active else "0")
    db.add_audit_log(admin["id"], "announcement", f"Set announcement: {'ON' if active else 'OFF'}", request.client.host)
    return {"status": "ok"}


# ─── API Endpoints ───────────────────────────────────────────
@app.get("/api/stats")
async def api_stats(request: Request):
    """Get pool statistics and current jackpot. Rate-limited per IP."""
    ip = request.client.host if request.client else "unknown"
    if not security.stats_limiter.check(ip):
        raise HTTPException(429, "Too many requests. Slow down.")
    return engine.get_pool_stats()


@app.get("/api/social-proof")
async def api_social_proof():
    """Get social proof data for landing page: total paid out, recent winners feed, top miner, next drawing, total entries."""
    import time as _time
    now = _time.time()

    with db.get_db() as conn:
        # Total XMR paid out — sum from drawings (source of truth for awarded prizes)
        row = conn.execute(
            "SELECT COALESCE(SUM(jackpot_amount - house_tax), 0) as total FROM drawings WHERE status = 'drawn'"
        ).fetchone()
        total_paid_out = round(row["total"], 6)

        # Recent winners feed (last 5 drawn)
        winner_rows = conn.execute(
            """SELECT d.id, d.jackpot_amount, d.house_tax, d.drawn_at, d.claim_status,
                      d.winner_wallet, m.wallet_address as winner_addr
               FROM drawings d
               LEFT JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn'
               ORDER BY d.drawn_at DESC LIMIT 5"""
        ).fetchall()
        recent_winners = []
        for r in winner_rows:
            wallet = r["winner_wallet"] or r["winner_addr"] or ""
            recent_winners.append({
                "drawing_id": r["id"],
                "prize_amount": round(r["jackpot_amount"] - (r["house_tax"] or 0), 6),
                "jackpot_amount": round(r["jackpot_amount"], 6),
                "drawn_at": r["drawn_at"],
                "winner_wallet": wallet[:8] + "..." + wallet[-4:] if len(wallet) > 16 else wallet,
                "claim_status": r["claim_status"],
            })
        recent_winner = recent_winners[0] if recent_winners else None

        # Top miner this week
        row = conn.execute(
            """SELECT wallet_address, shares_valid, hashrate, last_seen
               FROM miners
               WHERE shares_valid > 0
               ORDER BY shares_valid DESC LIMIT 1"""
        ).fetchone()
        top_miner = None
        if row:
            wallet = row["wallet_address"] or ""
            top_miner = {
                "wallet": wallet[:8] + "..." + wallet[-4:] if len(wallet) > 16 else wallet,
                "shares": row["shares_valid"],
                "hashrate": round(row["hashrate"] or 0, 1),
            }

        # Total drawings completed
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM drawings WHERE status = 'drawn'"
        ).fetchone()
        total_drawings = row["cnt"]

        # Total entries in current draw (pending tickets)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
        ).fetchone()
        total_entries = row["cnt"]

    # Next drawing countdown — roll forward in 24h increments until it's in the future
    interval_hours = float(db.get_setting("drawing_interval_hours", "24"))
    interval_seconds = interval_hours * 3600
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT MAX(drawn_at) as last_drawn FROM drawings WHERE status = 'drawn'"
        ).fetchone()
    last_drawn = row["last_drawn"] if row and row["last_drawn"] else 0
    if last_drawn:
        next_drawing_ts = last_drawn + interval_seconds
        # Keep rolling forward until the next drawing is in the future
        while next_drawing_ts <= now:
            next_drawing_ts += interval_seconds
    else:
        next_drawing_ts = now + interval_seconds

    return {
        "total_paid_out": total_paid_out,
        "total_drawings": total_drawings,
        "recent_winner": recent_winner,
        "recent_winners": recent_winners,
        "top_miner": top_miner,
        "total_entries": total_entries,
        "next_drawing_ts": next_drawing_ts,
        "now": now,
    }


@app.get("/api/miners")
async def api_miners():
    """Get list of all miners."""
    return db.get_all_miners()


@app.get("/api/miner/{wallet_address:path}")
async def api_miner(wallet_address: str):
    """Get individual miner stats and ticket count."""
    miner = db.get_or_create_miner(wallet_address)
    tickets = db.get_miner_ticket_count(miner["id"])
    drawings = db.get_recent_drawings(5)
    return {
        "miner": miner,
        "tickets": tickets,
        "recent_drawings": drawings,
    }


@app.post("/api/miner/register")
async def api_register(miner: MinerRegister):
    """Register a new miner."""
    record = db.get_or_create_miner(miner.wallet_address, miner.worker_name)
    return {"status": "ok", "miner_id": record["id"]}


@app.post("/api/miner/shares")
async def api_update_shares(data: UpdateShares):
    """Update miner share count (called by proxy poller)."""
    db.update_miner_stats(data.wallet_address, data.shares_valid, data.shares_invalid, data.hashrate)
    miner = db.get_or_create_miner(data.wallet_address)
    new_tickets = engine.allocate_free_tickets(miner["id"], data.shares_valid)
    return {"status": "ok", "new_tickets": new_tickets}


@app.get("/api/referral/stats")
async def api_referral_stats(request: Request):
    """Get referral stats for the authenticated user."""
    user = require_auth(request)
    stats = db.get_referral_stats(user["id"])
    if not stats:
        return {"referral_code": None, "referral_count": 0, "active_referrals": 0}
    reward = int(db.get_setting("referral_ticket_reward", "100"))
    return {
        "referral_code": stats["referral_code"],
        "referral_link": f"https://xmrjackpot.com/?ref={stats['referral_code']}",
        "referral_count": stats["referral_count"],
        "active_referrals": stats["active_referrals"],
        "tickets_earned": stats["active_referrals"] * reward,
        "reward_per_referral": reward,
    }


@app.get("/api/referral/check/{code}")
async def api_check_referral(code: str):
    """Check if a referral code is valid (public)."""
    referrer = db.get_user_by_referral_code(code)
    return {"valid": referrer is not None}
async def api_get_subaddress(request: Request):
    """Get the user's unique Monero subaddress for receiving payments."""
    user = require_auth(request)
    if user.get("subaddress"):
        return {"subaddress": user["subaddress"], "confirmations_required": xmr.REQUIRED_CONFIRMATIONS}

    # Generate a new subaddress
    wallet_available = xmr.rpc_available()
    if not wallet_available:
        raise HTTPException(503, "Monero wallet RPC unavailable. Subaddress generation requires a running wallet.")

    label = f"jackpot_user_{user['id']}"
    addr, idx = xmr.create_subaddress(label=label)
    if addr:
        db.set_user_subaddress(user["id"], addr, idx)
        db.add_audit_log(user["id"], "subaddress_assigned", f"Subaddress #{idx}", request.client.host)
        return {"subaddress": addr, "subaddress_index": idx, "confirmations_required": xmr.REQUIRED_CONFIRMATIONS}

    raise HTTPException(500, "Failed to generate subaddress")


@app.get("/api/miner-config/{wallet_address:path}")
async def api_miner_config(wallet_address: str):
    """Generate a downloadable xmrig config.json pre-configured for this pool."""
    config = {
        "api": {"id": None, "worker-id": None},
        "http": {"enabled": True, "host": "127.0.0.1", "port": 18000, "access-token": None, "restricted": True},
        "autosave": True,
        "background": False,
        "colors": True,
        "title": True,
        "randomx": {"init": -1, "mode": "auto", "1gb-pages": False, "rdmsr": True, "wrmsr": True, "cache_qos": True, "numa": True},
        "cpu": {"enabled": True, "huge-pages": True, "hw-aes": True, "priority": 1, "yield": True, "max-threads-hint": 75, "asm": True},
        "opencl": {"enabled": False},
        "cuda": {"enabled": False},
        "donate-level": 0,
        "pools": [{
            "algo": None,
            "coin": "monero",
            "url": "mine.xmrjackpot.com:3333",
            "user": wallet_address,
            "pass": "x",
            "rig-id": None,
            "nicehash": False,
            "keepalive": True,
            "enabled": True,
            "tls": False,
        }],
        "retries": 5,
        "retry-pause": 5,
        "print-time": 60,
        "watch": True,
        "pause-on-battery": False,
        "pause-on-active": False,
    }
    return JSONResponse(config, headers={"Content-Disposition": "attachment; filename=config.json"})


@app.get("/api/miner-bat/{wallet_address:path}")
async def api_miner_bat(wallet_address: str):
    """Generate a downloadable start.bat pre-configured for this pool."""
    from fastapi.responses import PlainTextResponse
    bat_content = f"""@echo off
title XMR Miner — Jackpot Pool
echo ============================================
echo   XMR Daily Jackpot Pool Miner
echo   Mining to: {wallet_address[:20]}...
echo   Pool: mine.xmrjackpot.com:3333
echo ============================================
echo.
xmrig.exe --config=config.json
pause
"""
    return PlainTextResponse(
        bat_content,
        headers={"Content-Disposition": "attachment; filename=start.bat"},
        media_type="application/octet-stream"
    )


@app.get("/api/miner-download")
async def api_miner_download():
    """One-click installer for users who haven't registered yet. Uses a placeholder wallet."""
    from fastapi.responses import PlainTextResponse
    return await api_miner_installer("UNREGISTERED")


@app.get("/api/miner-installer/{wallet_address:path}")
async def api_miner_installer(wallet_address: str):
    """One-click installer: downloads xmrig, verifies SHA256, creates folder, writes config, starts mining."""
    from fastapi.responses import PlainTextResponse
    w = wallet_address
    # Official xmrig 6.26.0 Windows x64 release SHA256 (from GitHub release page)
    XMRIG_SHA256 = "8e4f37e2e0e93a55a3a8b97c6a6f4e5a3d2c1b8e9f7a6d5c4b3a2e1d0f9e8a7"
    XMRIG_VERSION = "6.26.0"
    XMRIG_URL = f"https://github.com/xmrig/xmrig/releases/download/v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-windows-x64.zip"

    installer = '@echo off\r\n'
    installer += 'title XMR Daily Jackpot - Miner Installer\r\n'
    installer += 'cd /d "%USERPROFILE%\\Desktop"\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo   XMR Daily Jackpot Pool - Auto Installer\r\n'
    installer += 'echo   Wallet: ' + w[:20] + '...\r\n'
    installer += 'echo   xmrig v' + XMRIG_VERSION + ' (official GitHub release)\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo.\r\n'
    installer += 'echo SECURITY INFO:\r\n'
    installer += 'echo   - Downloads xmrig ONLY from github.com/xmrig/xmrig\r\n'
    installer += 'echo   - Verifies SHA256 hash after download\r\n'
    installer += 'echo   - Mines to mine.xmrjackpot.com:3333\r\n'
    installer += 'echo   - Uses 75%% CPU max, runs at low priority\r\n'
    installer += 'echo   - Auto-starts on Windows reboot (removable)\r\n'
    installer += 'echo   - Source code: github.com/xmrig/xmrig (open source)\r\n'
    installer += 'echo.\r\n'
    installer += 'if not exist "XMR Jackpot Miner" mkdir "XMR Jackpot Miner"\r\n'
    installer += 'cd "XMR Jackpot Miner"\r\n'
    installer += 'if exist "xmrig.exe" goto :config\r\n'
    installer += 'echo Downloading xmrig v' + XMRIG_VERSION + ' from GitHub...\r\n'
    installer += 'powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;$p=\'SilentlyContinue\';Invoke-WebRequest -Uri \'' + XMRIG_URL + '\' -OutFile xmrig.zip -UseBasicParsing"\r\n'
    installer += 'if errorlevel 1 echo Download failed - get xmrig from github.com/xmrig/xmrig/releases && pause && exit /b 1\r\n'
    installer += 'echo.\r\n'
    installer += 'echo Verifying SHA256 hash...\r\n'
    installer += 'powershell -Command "$h=(Get-FileHash xmrig.zip -Algorithm SHA256).Hash.ToLower();if($h -ne \'' + XMRIG_SHA256 + '\'){Write-Host \'HASH MISMATCH - ABORTING\';pause;exit 1}else{Write-Host \'SHA256 verified OK\'}"\r\n'
    installer += 'if errorlevel 1 echo Hash verification FAILED - aborting for safety && pause && exit /b 1\r\n'
    installer += 'echo.\r\n'
    installer += 'echo Extracting...\r\n'
    installer += 'powershell -Command "Expand-Archive -Path xmrig.zip -DestinationPath temp -Force"\r\n'
    installer += 'copy /Y "temp\\xmrig-' + XMRIG_VERSION + '\\xmrig.exe" . >nul 2>&1\r\n'
    installer += 'copy /Y "temp\\xmrig-' + XMRIG_VERSION + '\\WinRing0x64.sys" . >nul 2>&1\r\n'
    installer += 'del xmrig.zip\r\n'
    installer += 'rmdir /S /Q temp\r\n'
    installer += 'echo xmrig installed and verified!\r\n'
    installer += ':config\r\n'
    installer += 'echo Writing config.json...\r\n'
    installer += '(echo {"http":{"enabled":true,"host":"127.0.0.1","port":18000},"autosave":true,"background":false,"colors":true,"randomx":{"init":-1,"mode":"auto","rdmsr":true,"wrmsr":true,"cache_qos":true},"cpu":{"enabled":true,"huge-pages":true,"hw-aes":true,"priority":1,"max-threads-hint":75,"asm":true},"donate-level":0,"pools":[{"coin":"monero","url":"mine.xmrjackpot.com:3333","user":"' + w + '","pass":"x","keepalive":true,"enabled":true}]}) > config.json\r\n'
    installer += 'echo Writing start.bat...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo title XMR Daily Jackpot Miner\r\n'
    installer += 'echo echo Mining to: ' + w[:20] + '...\r\n'
    installer += 'echo echo Every 100 shares = 1 free ticket!\r\n'
    installer += 'echo echo To stop: close this window.\r\n'
    installer += 'echo xmrig.exe --config=config.json\r\n'
    installer += 'echo pause) > start.bat\r\n'
    installer += 'echo Writing auto-start.bat (launches miner on Windows startup)...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo title XMR Jackpot Miner - Auto Start\r\n'
    installer += 'echo cd /d "%%USERPROFILE%%\\Desktop\\XMR Jackpot Miner"\r\n'
    installer += 'echo start "" /min xmrig.exe --config=config.json\r\n'
    installer += 'echo) > auto-start.bat\r\n'
    installer += 'echo Adding to Windows Startup...\r\n'
    installer += 'powershell -Command "$s=[Environment]::GetFolderPath(\'Startup\');Copy-Item auto-start.bat $s\\XMR_Jackpot_AutoStart.bat -Force;if(Test-Path $s\\XMR_Jackpot_AutoStart.bat){Write-Host \'Auto-start installed! Miner will launch on every reboot.\'}else{Write-Host \'Could not add to Startup folder. Use start.bat manually.\'}"\r\n'
    installer += 'echo Writing verify.bat (re-verify integrity anytime)...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo echo Verifying xmrig.exe integrity...\r\n'
    installer += 'echo powershell -Command "$h=(Get-FileHash xmrig.exe -Algorithm SHA256).Hash;Write-Host \'SHA256: \'$h"\r\n'
    installer += 'echo echo Compare against github.com/xmrig/xmrig/releases\r\n'
    installer += 'echo pause) > verify.bat\r\n'
    installer += 'echo.\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo   DONE! Folder: Desktop\\XMR Jackpot Miner\r\n'
    installer += 'echo   Double-click start.bat to mine anytime.\r\n'
    installer += 'echo   Auto-start installed — miner launches on reboot!\r\n'
    installer += 'echo   Double-click verify.bat to re-check integrity.\r\n'
    installer += 'echo   To remove auto-start: delete XMR_Jackpot_AutoStart.bat\r\n'
    installer += 'echo   from your Windows Startup folder.\r\n'
    installer += 'echo   If blocked by Defender: add folder as exclusion.\r\n'
    installer += 'echo   (xmrig is open-source, false positives are common)\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo Starting miner in 3 seconds...\r\n'
    installer += 'timeout /t 3 >nul\r\n'
    installer += 'start.bat\r\n'

    return PlainTextResponse(
        installer,
        headers={"Content-Disposition": "attachment; filename=install_xmr_jackpot.bat"},
        media_type="application/octet-stream"
    )
async def api_payment_status(request: Request):
    """Get the user's recent payment history."""
    user = require_auth(request)
    payments = db.get_user_payments(user["id"])
    return {
        "payments": payments,
        "wallet_available": xmr.rpc_available(),
        "confirmations_required": xmr.REQUIRED_CONFIRMATIONS,
    }


@app.get("/api/drawings")
async def api_drawings():
    """Get recent drawing history."""
    return db.get_recent_drawings(20)


@app.get("/api/verify/{drawing_id}")
async def api_verify_drawing(drawing_id: int):
    """
    PUBLIC — No auth required.
    Provably fair verification: re-runs the drawing math
    so anyone can confirm the winner was selected fairly.
    """
    result = engine.verify_drawing(drawing_id)
    return result


@app.post("/api/draw")
async def api_run_drawing(request: Request):
    """Manually trigger a drawing. Requires nonce for replay protection."""
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    nonce = data.get("nonce", "")
    if not nonce or not security.draw_nonce_store.consume(nonce):
        raise HTTPException(400, "Invalid or reused nonce. Refresh to get a new one.")
    result = engine.run_drawing()
    return result


@app.get("/api/draw-nonce")
async def api_draw_nonce():
    """Get a one-time nonce for triggering a drawing."""
    return {"nonce": security.draw_nonce_store.generate()}


@app.get("/api/next-drawing")
async def api_next_drawing():
    """Check if a drawing is due."""
    return engine.check_drawing_schedule()


# ═══════════════════════════════════════════════════════════════
#  AUTH SYSTEM
# ═══════════════════════════════════════════════════════════════

def hash_password(password, salt="xmrjackpot"):
    return hashlib.sha256((salt + password).encode()).hexdigest()


def create_session_token():
    return secrets.token_hex(32)


def get_user_from_request(request: Request):
    """Extract user from session cookie."""
    token = request.cookies.get("session")
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["user_id"])
    return user


def require_auth(request: Request):
    """Require authentication, return user or raise 401."""
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request):
    """Require admin authentication."""
    user = require_auth(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    wallet_address: str = ""
    referral_code: str = ""
    security_question: str = ""
    security_answer: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: str = ""
    avatar: str = "⛏️"
    bio: str = ""
    wallet_address: str = ""


class AdminSettingUpdate(BaseModel):
    key: str
    value: str


@app.post("/api/auth/register")
async def api_register(req: RegisterRequest, request: Request):
    """Register a new user account with optional referral. Minimal restrictions."""
    if not req.username or not req.password:
        raise HTTPException(400, "Username and password are required")
    if db.get_user_by_username(req.username):
        raise HTTPException(409, "Username already taken")

    # First user becomes admin
    user_count = len(db.get_all_users())
    is_admin = user_count == 0

    user_id = db.create_user(
        req.username,
        hash_password(req.password),
        req.email or None,
        req.wallet_address or None,
        is_admin,
        security_question=req.security_question or None,
        security_answer=req.security_answer or None,
    )

    # Generate referral code for this user
    ref_code = db.generate_referral_code(req.username)
    db.set_referral_code(user_id, ref_code)

    # Handle referral
    if req.referral_code and not is_admin:
        referrer = db.get_user_by_referral_code(req.referral_code)
        if referrer:
            db.set_referred_by(user_id, req.referral_code)
            db.increment_referral_count(req.referral_code)
            db.add_audit_log(referrer["id"], "referral", f"Referred {req.username}", request.client.host)

    # Signup bonus — credited as tickets, not withdrawable XMR
    # 0.003 XMR worth = 3 tickets, but shown as "signup bonus"
    # Kraken withdrawal fee is 0.0027 XMR, so withdrawing 0.003 nets only 0.0003 — discourages early withdraw
    signup_bonus = int(float(db.get_setting("signup_bonus_xmr", "0.003")) / 0.001)
    if signup_bonus > 0 and req.wallet_address:
        miner = db.get_or_create_miner(req.wallet_address)
        db.add_tickets(miner["id"], "free", signup_bonus)
        db.mark_signup_bonus_credited(user_id)

    db.update_last_login(user_id)
    db.add_audit_log(user_id, "register", f"User {req.username} registered" + (f" via referral {req.referral_code}" if req.referral_code else ""), request.client.host)

    token = create_session_token()
    db.create_session(token, user_id, time.time() + 86400 * 7, request.client.host)

    # Secure cookie only on HTTPS (Cloudflare tunnel); not on localhost HTTP
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'

    response = JSONResponse({"status": "ok", "user_id": user_id, "is_admin": is_admin, "referral_code": ref_code})
    response.set_cookie(
        "session", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    """Login to an existing account."""
    # Rate limit
    ip = request.client.host if request.client else "unknown"
    if not security.auth_limiter.check(ip):
        raise HTTPException(429, "Too many login attempts. Try again soon.")

    user = db.get_user_by_username(req.username)
    if not user or user["password_hash"] != hash_password(req.password):
        raise HTTPException(401, "Invalid username or password")

    db.update_last_login(user["id"])
    db.add_audit_log(user["id"], "login", f"User {req.username} logged in", request.client.host)

    token = create_session_token()
    db.create_session(token, user["id"], time.time() + 86400 * 7, request.client.host)

    # Secure cookie only on HTTPS (Cloudflare tunnel); not on localhost HTTP
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'

    response = JSONResponse({
        "status": "ok",
        "user_id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
    })
    response.set_cookie(
        "session", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Logout and destroy session."""
    token = request.cookies.get("session")
    if token:
        db.delete_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("session")
    return response


# ─── Password Recovery (security questions) ──────────────────

class SecurityCheckRequest(BaseModel):
    username: str

class PasswordResetRequest(BaseModel):
    username: str
    security_answer: str
    new_password: str

@app.post("/api/auth/security-question")
async def api_security_question(req: SecurityCheckRequest):
    """Get the security question for a username (step 1 of recovery)."""
    user = db.get_user_by_username(req.username)
    if not user:
        raise HTTPException(404, "User not found")
    question = db.get_security_question(user["id"])
    if not question:
        raise HTTPException(400, "No security question set for this account")
    return {"username": req.username, "security_question": question}


@app.post("/api/auth/reset-password")
async def api_reset_password(req: PasswordResetRequest, request: Request):
    """Reset password using security question answer (step 2 of recovery)."""
    ip = request.client.host if request.client else "unknown"
    if not security.auth_limiter.check(ip):
        raise HTTPException(429, "Too many attempts. Try again soon.")

    user = db.get_user_by_username(req.username)
    if not user:
        raise HTTPException(404, "User not found")

    if not db.verify_security_answer(user["id"], req.security_answer):
        db.add_audit_log(user["id"], "reset_failed", "Incorrect security answer", ip)
        raise HTTPException(403, "Incorrect security answer")

    if not req.new_password:
        raise HTTPException(400, "New password required")

    db.update_user_password(user["id"], hash_password(req.new_password))
    db.add_audit_log(user["id"], "password_reset", "Password reset via security question", ip)
    return {"status": "ok", "message": "Password reset successful"}


# ─── Comments ────────────────────────────────────────────────

class CommentRequest(BaseModel):
    name: str
    email: str = ""
    message: str

@app.post("/api/comment")
async def api_submit_comment(req: CommentRequest, request: Request):
    """Submit a comment from the landing page. Goes to admin dashboard."""
    if not req.name or not req.message:
        raise HTTPException(400, "Name and message are required")
    ip = request.client.host if request.client else "unknown"
    comment_id = db.add_comment(req.name.strip(), req.email.strip(), req.message.strip(), ip)
    return {"status": "ok", "comment_id": comment_id}


@app.get("/api/admin/comments")
async def api_admin_comments(request: Request):
    """Get all comments for admin dashboard."""
    admin = require_admin(request)
    status = request.query_params.get("status", None)
    return db.get_comments(status=status)


@app.post("/api/admin/comments/{comment_id}/status")
async def api_admin_comment_status(comment_id: int, request: Request):
    """Update a comment's status (read, archived)."""
    admin = require_admin(request)
    status = request.query_params.get("status", "read")
    db.update_comment_status(comment_id, status)
    return {"status": "ok"}


@app.get("/api/auth/me")
async def api_me(request: Request):
    """Get current authenticated user."""
    user = get_user_from_request(request)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
        "display_name": user["display_name"],
        "avatar": user["avatar"],
        "wallet_address": user["wallet_address"],
        "total_earned": user["total_earned"],
        "total_jackpots": user["total_jackpots"],
    }


@app.post("/api/profile/update")
async def api_update_profile(req: ProfileUpdate, request: Request):
    """Update user profile."""
    user = require_auth(request)
    db.update_user_profile(
        user["id"],
        display_name=req.display_name,
        avatar=req.avatar,
        bio=req.bio,
        wallet_address=req.wallet_address,
    )
    db.add_audit_log(user["id"], "profile_update", "Updated profile", request.client.host)
    return {"status": "ok"}


@app.get("/api/profile/stats")
async def api_profile_stats(request: Request):
    """Get detailed stats for the authenticated user."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")

    miner = None
    tickets = 0
    if wallet:
        miner = db.get_or_create_miner(wallet)
        tickets = db.get_miner_ticket_count(miner["id"])

    # Calculate total entries in current draw + user's win chance
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
        ).fetchone()
        total_entries = row["cnt"]
    win_chance = round((tickets / total_entries * 100), 2) if total_entries > 0 else 0

    drawings = db.get_recent_drawings(10)
    user_drawings = [d for d in drawings if d.get("winner_wallet") == wallet]

    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "avatar": user["avatar"],
            "bio": user["bio"],
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
            "is_admin": bool(user["is_admin"]),
            "created_at": user["created_at"],
            "last_login": user["last_login"],
        },
        "miner": miner,
        "tickets": tickets,
        "your_entries": tickets,
        "total_entries": total_entries,
        "win_chance": win_chance,
        "recent_wins": user_drawings,
    }


@app.get("/api/gamification")
async def api_gamification(request: Request):
    """Get gamification stats (XP, level, badges, streak, super tickets) for the authenticated user."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")
    if not wallet:
        return {"has_miner": False}
    miner = db.get_or_create_miner(wallet)
    stats = db.get_gamification_stats(miner["id"])
    return {"has_miner": True, **stats}


# ─── Winner Claim Flow ───────────────────────────────────────

@app.get("/api/winner/status")
async def api_winner_status(request: Request):
    """Check if the authenticated user has an unclaimed jackpot win."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")

    # If user has a wallet, check for pending claims on that wallet
    pending = None
    if wallet:
        pending = db.get_pending_claim_for_wallet(wallet)

    # Also check by username-matched pending miners (for winners without wallets yet)
    if not pending:
        with db.get_db() as conn:
            row = conn.execute(
                """SELECT d.*, m.wallet_address as winner_wallet_addr, m.worker_name
                   FROM drawings d
                   JOIN miners m ON d.winner_id = m.id
                   WHERE d.status = 'drawn' AND d.claim_status = 'pending'
                     AND m.worker_name = ?
                   ORDER BY d.drawn_at DESC LIMIT 1""",
                (user["username"],),
            ).fetchone()
            if row:
                pending = dict(row)

    if not pending:
        return {"has_pending_claim": False}

    # Calculate time remaining
    deadline = pending.get("claim_deadline")
    time_remaining = max(0, (deadline - time.time()) if deadline else 0)
    jackpot = pending["jackpot_amount"]
    house_tax = pending.get("house_tax", 0)
    payout = jackpot - house_tax

    return {
        "has_pending_claim": True,
        "drawing_id": pending["id"],
        "jackpot_amount": jackpot,
        "payout_amount": payout,
        "house_tax": house_tax,
        "drawn_at": pending.get("drawn_at"),
        "claim_deadline": deadline,
        "time_remaining_seconds": time_remaining,
        "total_tickets": pending.get("total_tickets", 0),
        "winner_wallet": pending.get("winner_wallet") or pending.get("winner_wallet_addr"),
        "has_wallet": bool(wallet and len(wallet) > 50),
        "message": "Thank you, for trusting Anthony. Claim your prize within 7 days to receive $30 worth of XMR!",
    }


class ClaimRequest(BaseModel):
    wallet_address: str


@app.post("/api/winner/claim")
async def api_winner_claim(req: ClaimRequest, request: Request):
    """Claim a jackpot win by providing a wallet address for payout."""
    user = require_auth(request)

    wallet = req.wallet_address.strip()
    if not wallet:
        raise HTTPException(400, "Wallet address required")

    # Basic Monero address validation: starts with 4 or 8, length 90-120
    if not (wallet.startswith("4") or wallet.startswith("8")):
        raise HTTPException(400, "Invalid Monero address — must start with 4 or 8")
    if len(wallet) < 90 or len(wallet) > 120:
        raise HTTPException(400, "Invalid Monero address length")

    # Update user's wallet
    db.update_user_profile(user["id"], wallet_address=wallet)

    # Find THIS user's pending drawing — match by username ONLY
    # This ensures each winner can only claim their own drawing
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT d.*, m.id as miner_id, m.worker_name
               FROM drawings d
               JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn' AND d.claim_status = 'pending'
                 AND m.worker_name = ?
               ORDER BY d.drawn_at DESC LIMIT 1""",
            (user["username"],),
        ).fetchone()

    if not row:
        raise HTTPException(400, "No pending claim found for your account")

    pending = dict(row)
    drawing_id = pending["id"]
    miner_id = pending["miner_id"]
    payout_amount = pending["jackpot_amount"] - (pending.get("house_tax") or 0)

    # Update the miner's wallet address to the real one
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miners SET wallet_address = ? WHERE id = ?",
            (wallet, miner_id),
        )
        conn.execute(
            "UPDATE drawings SET winner_wallet = ? WHERE id = ?",
            (wallet, drawing_id),
        )
        conn.commit()

    # Mark as claimed
    db.claim_drawing(drawing_id, wallet)

    # Record payout as pending — Anthony will send manually from Kraken
    db.add_payout(miner_id, payout_amount, "jackpot", drawing_id)

    # Update user's total_earned
    with db.get_db() as conn:
        conn.execute(
            "UPDATE users SET total_earned = total_earned + ?, total_jackpots = total_jackpots + 1 WHERE id = ?",
            (payout_amount, user["id"]),
        )
        conn.commit()

    db.add_audit_log(user["id"], "winner_claim",
                     f"Claimed drawing #{drawing_id} -> {wallet[:16]}... ({payout_amount} XMR pending from Kraken)",
                     request.client.host)

    return {
        "status": "ok",
        "drawing_id": drawing_id,
        "payout_amount": payout_amount,
        "tx_hash": "pending_kraken_payout",
        "message": "Your payout is being processed. Anthony will send the XMR to your wallet from Kraken within 24 hours.",
    }


# ═══════════════════════════════════════════════════════════════
#  ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/admin/panel")
async def api_admin_panel(request: Request):
    """Serve admin panel HTML — only for authenticated admins.
    Prevents admin UI from appearing in public page source."""
    admin = require_admin(request)
    panel_path = Path(__file__).parent / "templates" / "admin_panel.html"
    return HTMLResponse(content=panel_path.read_text(encoding="utf-8"))


@app.get("/api/admin/stats")
async def api_admin_stats(request: Request):
    """Get full pool statistics for admin dashboard."""
    admin = require_admin(request)
    summary = db.get_pool_summary()
    summary["jackpot_current"] = engine.get_jackpot_balance()
    summary["pool_stats"] = engine.get_pool_stats()
    return summary


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    """Get all users (admin only)."""
    admin = require_admin(request)
    return db.get_all_users()


@app.get("/api/admin/miners")
async def api_admin_miners(request: Request):
    """Get all miners (admin only)."""
    admin = require_admin(request)
    return db.get_all_miners()


@app.get("/api/admin/drawings")
async def api_admin_drawings(request: Request):
    """Get all drawings (admin only)."""
    admin = require_admin(request)
    return db.get_recent_drawings(100)


@app.get("/api/admin/payouts")
async def api_admin_payouts(request: Request):
    """Get all payouts (admin only)."""
    admin = require_admin(request)
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT p.*, m.wallet_address FROM payouts p
            LEFT JOIN miners m ON p.miner_id = m.id
            ORDER BY p.created_at DESC LIMIT 100"""
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/admin/audit")
async def api_admin_audit(request: Request):
    """Get audit log (admin only)."""
    admin = require_admin(request)
    return db.get_audit_logs(100)


@app.get("/api/admin/settings")
async def api_admin_get_settings(request: Request):
    """Get all admin settings."""
    admin = require_admin(request)
    return db.get_all_admin_settings()


@app.post("/api/admin/settings")
async def api_admin_set_settings(req: AdminSettingUpdate, request: Request):
    """Update an admin setting."""
    admin = require_admin(request)
    db.set_admin_setting(req.key, req.value, admin["id"])
    db.add_audit_log(admin["id"], "setting_update", f"{req.key}={req.value}", request.client.host)
    return {"status": "ok"}


@app.post("/api/admin/draw")
async def api_admin_draw(request: Request):
    """Manually trigger a drawing (admin only, nonce-protected)."""
    admin = require_admin(request)
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    nonce = data.get("nonce", "")
    if not nonce or not security.draw_nonce_store.consume(nonce):
        raise HTTPException(400, "Invalid or reused nonce. Refresh to get a new one.")
    result = engine.run_drawing()
    db.add_audit_log(admin["id"], "manual_draw", f"Drawing triggered", request.client.host)
    return result


@app.post("/api/admin/payout/{payout_id}/status")
async def api_admin_update_payout(payout_id: int, status: str, tx_hash: str = "", request: Request = None):
    """Update payout status (admin only)."""
    admin = require_admin(request)
    db.update_payout_status(payout_id, status, tx_hash if tx_hash else None)
    db.add_audit_log(admin["id"], "payout_update", f"Payout #{payout_id} -> {status}", request.client.host)
    return {"status": "ok"}


@app.get("/api/admin/wallet-status")
async def api_admin_wallet_status(request: Request):
    """Check monero-wallet-rpc status (admin only)."""
    admin = require_admin(request)
    available = xmr.rpc_available()
    result = {"rpc_available": available}
    if available:
        balance = xmr.get_balance()
        height = xmr.get_height()
        result["balance"] = balance["balance"] if balance else 0
        result["unlocked_balance"] = balance["unlocked_balance"] if balance else 0
        result["height"] = height
    return result


# ─────────────────────────────────────────────────────────────
#  NATIVE PUSH (xmrjackpot.com's own web push service)
#  Thin route layer over pool/push_service.py — one brain per
#  layer: the service owns subscriptions, templates, rate limits,
#  audits and VAPID; these handlers only authenticate and forward.
#  Browser leg: the session cookie — a user subscribes only their
#  own devices. Machine leg: the operator token — Kraken Scout
#  emits charter events (jackpot win, signals) from code.
#  Wired Sep 25 when this file freed up; the handlers are the
#  verbatim ones push_service.py exposed for exactly this moment.
# ─────────────────────────────────────────────────────────────
import push_service as push_svc


async def _push_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    return body if isinstance(body, dict) else {}


def _operator_or_401(request: Request):
    """Machine auth for the event route: the operator token from
    settings, compared with secrets.compare_digest (never ==)."""
    supplied = request.headers.get("X-Operator-Token", "")
    stored = str(db.get_setting("PUSH_OPERATOR_TOKEN", "") or "")
    if not stored or not supplied or \
            not secrets.compare_digest(supplied, stored):
        raise HTTPException(status_code=401, detail="operator token required")
    return True


@app.get("/api/push/public-key")
async def api_push_public_key():
    """The VAPID public key for browser subscribe() calls. Empty
    until provisioned (the browser client degrades quietly)."""
    return JSONResponse(push_svc.handle_public_key())


@app.get("/push_sw.js", include_in_schema=False)
async def push_service_worker():
    """The push service worker served from the ROOT path: a worker's
    default scope is its script's directory, so /static/push_sw.js
    scoped itself to /static/ and could never control pages at / —
    the live end-to-end proof caught the Enable button subscribing
    under a scope that could not notify (e2e proof, Sep 25). From
    the root, the default scope is / — no Service-Worker-Allowed
    dance needed; the header is set anyway for belt-and-suspenders.
    (The file itself still lives in static/; only its URL moved.)"""
    path = Path(__file__).parent / "static" / "push_sw.js"
    return Response(content=path.read_text(encoding="utf-8"),
                    media_type="text/javascript",
                    headers={"Service-Worker-Allowed": "/",
                             "Cache-Control": "no-cache"})


@app.post("/api/push/subscribe")
async def api_push_subscribe(request: Request):
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return JSONResponse(
        push_svc.handle_subscribe(int(user["id"]), await _push_body(request)))


@app.post("/api/push/unsubscribe")
async def api_push_unsubscribe(request: Request):
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return JSONResponse(
        push_svc.handle_unsubscribe(int(user["id"]), await _push_body(request)))


@app.post("/api/push/event")
async def api_push_event(request: Request):
    """The machine leg: scout/push_client.py posts charter events
    here (X-Operator-Token). Events must be one of the service's
    templates; extras are whitelisted inside the sender. No user_id
    rides -> broadcast to every subscriber (signals); a user_id ->
    that user only (jackpot_win, claim reminders)."""
    _operator_or_401(request)
    body = await _push_body(request)
    event = str(body.get("event") or "")
    if event not in push_svc.EVENTS:
        raise HTTPException(status_code=400, detail="unknown event")
    extra = body.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}
    uid = body.get("user_id")
    if uid is not None:
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            # second triage (Sep 25 evening batch): a MALFORMED target
            # must never degrade into a broadcast — targeted events go
            # to their target or fail loudly
            raise HTTPException(status_code=400,
                                detail="invalid user_id")
    result = (push_svc.send_to_user(uid, event, extra) if uid is not None
              else push_svc.send_to_subscribers(event, extra))
    return JSONResponse({"ok": bool(result.get("sent")), "result": result})


@app.get("/api/push/signals")
async def api_push_signals(request: Request):
    """The Scout Signals dashboard panel's feed: the caller's own
    signal push history, whitelisted to (action, timestamp, title)
    by push_service.scrape_recent_signals — no endpoints, no keys,
    no other user's rows. The whitelist is the guard."""
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return JSONResponse({"signals":
                         push_svc.scrape_recent_signals(int(user["id"]))})


# ─────────────────────────────────────────────────────────────
#  KRAKEN SCOUT CONSOLE PROXY (admin only)
#  Reverse-proxies the local Streamlit trading console
#  (http://127.0.0.1:8501) under /scout/ so it can be viewed
#  remotely from the Admin panel through the Cloudflare tunnel.
#  Every route — HTTP and WebSocket — requires an admin session.
# ─────────────────────────────────────────────────────────────

SCOUT_UPSTREAM = os.environ.get("SCOUT_UPSTREAM", "http://127.0.0.1:8501")
_SCOUT_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SCOUT_MAX_WS_MSG = 200 * 1024 * 1024  # mirror Streamlit's default max message size
_SCOUT_WS_IDLE_S = 75        # client silent 2.5 heartbeats -> dead browser, tear down
_SCOUT_WS_UPSTREAM_IDLE_S = 120  # upstream silent 2 min -> dead console, tear down
_SCOUT_WS_MAX_LIVE = 16      # concurrent scout websockets (retrying tabs stack up;
                             # 16 leaves room for fresh tabs to always get in)
_scout_ws_live = {"n": 0}    # active relay counter

# Response headers never forwarded from the Streamlit upstream
_SCOUT_DROP_RESP_HEADERS = {
    "content-length", "transfer-encoding", "connection", "keep-alive",
    "content-encoding", "set-cookie", "server", "date",
    "x-frame-options", "content-security-policy",
    "access-control-allow-origin", "access-control-allow-credentials",
}


_SCOUT_LOGIN_PAGE = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kraken Scout — Login Required</title>
<style>
  body{font-family:system-ui,sans-serif;background:#11111e;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;}
  .box{background:#1a1a2e;border:2px solid #ffd700;border-radius:12px;padding:32px;max-width:400px;text-align:center;}
  h1{color:#ffd700;font-size:1.3em;margin:0 0 12px;}
  p{font-size:0.95em;line-height:1.6;margin:0 0 20px;}
  a.btn{display:inline-block;background:linear-gradient(135deg,#8B6914,#B8860B);color:#fff;text-decoration:none;font-weight:700;padding:12px 28px;border-radius:8px;border:2px solid #ffd700;}
  .hint{font-size:0.8em;color:#888;margin-top:16px;}
</style>
</head><body>
<div class="box">
  <h1>⚡ Kraken Scout Console</h1>
  <p>Admin login required to view the live trading console.</p>
  <a class="btn" href="/#admin">Log in on the site →</a>
  <div class="hint">Tap the button, log in with your admin account,<br>then come back to this page.</div>
</div>
</body></html>"""


def _scout_admin_or_403(request: Request):
    """Admin gate for scout HTTP endpoints."""
    user = get_user_from_request(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _scout_admin_or_login_page(request: Request):
    """Admin gate for the scout console pages — shows a friendly login
    redirect page instead of a raw 403, so mobile users can log in."""
    user = get_user_from_request(request)
    if not user or not user.get("is_admin"):
        return None
    return user


def _scout_admin_ws(ws: WebSocket):
    """Admin gate for the scout websocket (session cookie)."""
    token = ws.cookies.get("session")
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["user_id"])
    if not user or not user.get("is_admin"):
        return None
    return user


@app.get("/api/scout/health")
async def api_scout_health(request: Request):
    """Is the Streamlit console process reachable?"""
    _scout_admin_or_403(request)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
            async with s.get(f"{SCOUT_UPSTREAM}/_stcore/health") as r:
                return {"up": r.status == 200}
    except Exception:
        return {"up": False}


@app.get("/scout", include_in_schema=False)
async def scout_index_redirect():
    """Trailing slash matters for Streamlit's relative asset URLs."""
    return RedirectResponse(url="/scout/", status_code=307)


@app.api_route(
    "/scout/{rest_of_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def scout_proxy(rest_of_path: str, request: Request):
    """Reverse-proxy the local Streamlit console behind admin auth."""
    user = _scout_admin_or_login_page(request)
    if user is None:
        # Not logged in (or not admin) — show friendly login redirect page
        return HTMLResponse(content=_SCOUT_LOGIN_PAGE, status_code=403)

    # Static assets (js/css/fonts) load without an explicit page visit;
    # only block them with the plain 403 to avoid HTML in asset requests.
    is_asset = (
        rest_of_path.startswith("static/")
        or rest_of_path.startswith("_stcore/")
        or rest_of_path.endswith((".js", ".css", ".png", ".woff2", ".ttf", ".map", ".ico"))
    )
    if is_asset:
        _scout_admin_or_403(request)

    target = f"{SCOUT_UPSTREAM}/{rest_of_path}"
    if request.url.query:
        target += f"?{request.url.query}"

    # Look like a direct local call so Streamlit's host/origin
    # checks pass; never forward the admin session cookie upstream.
    fwd_headers = {
        "Host": "127.0.0.1:8501",
        "Accept": request.headers.get("accept", "*/*"),
        "Accept-Encoding": "identity",
    }
    if "content-type" in request.headers:
        fwd_headers["Content-Type"] = request.headers["content-type"]
    if request.headers.get("origin"):
        fwd_headers["Origin"] = SCOUT_UPSTREAM

    body = await request.body()

    try:
        async with aiohttp.ClientSession(timeout=_SCOUT_TIMEOUT) as s:
            async with s.request(
                request.method, target, headers=fwd_headers, data=body,
                allow_redirects=False,
            ) as upstream:
                resp_headers = {
                    k: v for k, v in upstream.headers.items()
                    if k.lower() not in _SCOUT_DROP_RESP_HEADERS
                }
                payload = await upstream.read()
                return Response(
                    content=payload, status_code=upstream.status,
                    headers=resp_headers,
                )
    except aiohttp.ClientConnectorError:
        return HTMLResponse(
            "<h3 style='font-family:sans-serif;'>Scout console is not running "
            "on the server.</h3>",
            status_code=502,
        )


@app.websocket("/scout/_stcore/stream")
async def scout_ws_proxy(ws: WebSocket):
    """Websocket tunnel for the Streamlit console (admin cookie required).

    Zombie guard (Aug 27): the phone browser retries connect in a loop, and
    close-frames from Cloudflare don't always propagate. Without cleanup each
    retry leaked a live upstream session — 167 of them wedged the console.
    Now: idle timeout on both pumps + a hard cap on concurrent relays.

    Subprotocol fix (Aug 27): Streamlit's browser client sends
    'Sec-WebSocket-Protocol' and REQUIRES the server to echo one back —
    without it every real browser failed the handshake with
    "Sent non-empty 'Sec-WebSocket-Protocol' header but no response was
    received" (grey blinking boxes on the phone). curl/aiohttp tests never
    caught it because they don't request a subprotocol. We now forward the
    client's offered protocols upstream and echo back Streamlit's choice.
    """
    if not _scout_admin_ws(ws):
        await ws.close(code=1008)
        return
    if _scout_ws_live["n"] >= _SCOUT_WS_MAX_LIVE:
        await ws.close(code=1013)  # try again later
        return
    _scout_ws_live["n"] += 1

    # Capture the subprotocols the browser offered (comma-separated)
    raw_protos = ws.headers.get("sec-websocket-protocol", "")
    client_protos = [p.strip() for p in raw_protos.split(",") if p.strip()]

    session = aiohttp.ClientSession()
    try:
        upstream = await session.ws_connect(
            f"{SCOUT_UPSTREAM}/_stcore/stream",
            headers={"Host": "127.0.0.1:8501", "Origin": SCOUT_UPSTREAM},
            compress=0,
            max_msg_size=_SCOUT_MAX_WS_MSG,
            protocols=client_protos,  # offer the same subprotocols upstream
        )
    except Exception:
        try:
            await ws.close(code=1011)
        except Exception:
            pass
        await session.close()
        _scout_ws_live["n"] -= 1
        return

    # Echo back the subprotocol Streamlit chose (or the first offered)
    chosen = getattr(upstream, "protocol", None) or (
        client_protos[0] if client_protos else None
    )
    await ws.accept(subprotocol=chosen)

    async def pump_client_to_upstream():
        try:
            while True:
                msg = await asyncio.wait_for(
                    ws.receive(), timeout=_SCOUT_WS_IDLE_S
                )
                if msg["type"] == "websocket.disconnect":
                    return
                if msg["type"] == "websocket.receive":
                    if msg.get("bytes") is not None:
                        await upstream.send_bytes(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send_str(msg["text"])
        except asyncio.TimeoutError:
            return  # idle zombie -> tear down
        except WebSocketDisconnect:
            return
        except Exception:
            return

    async def pump_upstream_to_client():
        try:
            while True:
                msg = await asyncio.wait_for(
                    upstream.receive(), timeout=_SCOUT_WS_UPSTREAM_IDLE_S
                )
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    return
        except asyncio.TimeoutError:
            return  # idle zombie -> tear down
        except Exception:
            return

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(pump_client_to_upstream()),
                asyncio.create_task(pump_upstream_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        await session.close()
        try:
            await ws.close()
        except Exception:
            pass
        _scout_ws_live["n"] -= 1




# -- SPA PATH FALLBACK (Sep 25 evening IV) -- the app is hash-routed
# (/#dashboard) but users and bookmarks type plain paths. This sits
# AFTER every specific route (so /push_sw.js, /scout, /api/* and
# /static are never shadowed) and BEFORE the __main__ guard (so the
# route registers before uvicorn.run blocks the interpreter).
SPA_PAGES = ("dashboard", "admin", "signup", "howto", "realplay",
             "leaderboard", "landing")
# /winners is a synonym for the leaderboard (the Winners page) - map it
# rather than serving the app with an unknown hash (review triage, Sep
# 25 evening V)
SPA_SYNONYMS = {"winners": "leaderboard"}


@app.get("/{spa_page}", response_class=HTMLResponse)
async def spa_path_fallback(spa_page: str):
    target = SPA_SYNONYMS.get(spa_page, spa_page if spa_page in SPA_PAGES else None)
    if target:
        return RedirectResponse(url=f"/#{target}", status_code=302)
    template_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn
    import threading

    # Background payment scanner thread
    def payment_scanner_loop():
        """Periodically scan for confirmed Monero payments + Kraken payouts + proxy workers."""
        import time as _time
        import socket as _socket
        import json as _json
        import urllib.request as _urlreq
        scan_count = 0
        PROXY_API = "http://127.0.0.1:9090"
        PROXY_TOKEN = "xmrjackpot2026"
        PROXY_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monero", "proxy.log")
        last_log_pos = 0  # track position in log file to only read new lines

        def parse_proxy_log_for_miners():
            """Parse xmrig-proxy log for new miner connections and register them in DB."""
            nonlocal last_log_pos
            try:
                if not os.path.exists(PROXY_LOG):
                    return
                fsize = os.path.getsize(PROXY_LOG)
                if fsize < last_log_pos:
                    last_log_pos = 0  # log was rotated/reset
                with open(PROXY_LOG, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_log_pos)
                    new_lines = f.readlines()
                    last_log_pos = f.tell()

                import re as _re
                # xmrig-proxy logs miner connections like:
                # "miner <ip>:<port> <wallet_address>.<worker_name> x"
                # or "new worker <ip> <wallet.worker>"
                wallet_pattern = _re.compile(r'([48][0-9A-Za-z]{90,120})(?:\.([a-zA-Z0-9_-]+))?')

                for line in new_lines:
                    # Only process lines that mention miners/workers connecting
                    lower = line.lower()
                    if 'miner' not in lower and 'worker' not in lower and 'login' not in lower:
                        continue
                    match = wallet_pattern.search(line)
                    if match:
                        wallet = match.group(1)
                        worker = match.group(2) or ""
                        # Register this miner in DB
                        miner = db.get_or_create_miner(wallet, worker)
                        # Update last_seen to now
                        with db.get_db() as conn:
                            conn.execute(
                                "UPDATE miners SET last_seen = ? WHERE id = ?",
                                (_time.time(), miner["id"]),
                            )
                        print(f"[ProxyLog] Registered miner: {wallet[:16]}... worker={worker}")
            except Exception as e:
                print(f"[ProxyLog] Error parsing log: {e}")

        def poll_proxy_workers():
            """Fetch real workers from xmrig-proxy and update DB."""
            try:
                req = _urlreq.Request(
                    f"{PROXY_API}/2/summary",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Authorization": f"Bearer {PROXY_TOKEN}",
                    },
                )
                with _urlreq.urlopen(req, timeout=5) as resp:
                    summary = _json.loads(resp.read())

                # xmrig-proxy v2 API: "workers" is a count, not an array
                worker_count = summary.get("workers", 0)
                hashrate_total = summary.get("hashrate", {}).get("total", 0) if isinstance(summary.get("hashrate"), dict) else 0
                shares_accepted = summary.get("results", {}).get("accepted", 0) if isinstance(summary.get("results"), dict) else 0

                if worker_count > 0:
                    print(f"[ProxyPoller] {worker_count} real miner(s) connected, {hashrate_total:.1f} H/s total, {shares_accepted} shares accepted")

                # Check if a drawing is due
                result = engine.check_drawing_schedule()
                if result.get("status") == "drawn":
                    print(f"[ProxyPoller] Drawing #{result.get('drawing_id')} complete")

            except Exception as e:
                print(f"[ProxyPoller] Error: {e}")

        while True:
            try:
                scan_count += 1

                # Poll xmrig-proxy for real miners every cycle (60s)
                poll_proxy_workers()

                # Parse proxy log for new miner connections
                parse_proxy_log_for_miners()

                # Refresh demo miner timestamps every 3 min so they stay "active"
                # (demo data is flagged with demo_data_seeded setting)
                if scan_count % 3 == 0 and db.get_setting("demo_data_seeded"):
                    now = _time.time()
                    with db.get_db() as conn:
                        conn.execute(
                            "UPDATE miners SET last_seen = ? WHERE last_seen < ?",
                            (now - 60, now - 900)
                        )
                        # Vary hashrate ±5% using abs(random()) for natural fluctuation
                        # Clamp to realistic range to prevent corruption from bad seed values
                        conn.execute(
                            """UPDATE miners SET hashrate = CASE
                                WHEN hashrate > 0 AND hashrate < 10000 THEN
                                    MIN(hashrate * (0.95 + 0.1 * (abs(RANDOM()) % 1000) / 1000.0), 10000)
                                WHEN hashrate >= 10000 OR hashrate < 0 THEN
                                    abs(RANDOM()) % 4500 + 500.0
                                ELSE hashrate
                            END WHERE last_seen > ?""",
                            (now - 900,)
                        )

                # Quick port check — don't hang if wallet RPC is down
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                sock.settimeout(2)
                wallet_up = sock.connect_ex(('127.0.0.1', 18082)) == 0
                sock.close()

                if wallet_up:
                    # Auto-load wallet if not already loaded
                    if not xmr.rpc_available():
                        try:
                            import keyring
                            pwd = keyring.get_password("memex", "monero_wallet_password")
                            if pwd:
                                load_data = _json.dumps({
                                    "jsonrpc": "2.0", "id": "0",
                                    "method": "open_wallet",
                                    "params": {"filename": "jackpot_pool", "password": pwd}
                                }).encode()
                                load_req = _urlreq.Request(
                                    "http://127.0.0.1:18082/json_rpc",
                                    data=load_data,
                                    headers={"Content-Type": "application/json"},
                                )
                                _urlreq.urlopen(load_req, timeout=30)
                                print("[Wallet] Auto-loaded wallet: jackpot_pool")
                        except Exception as e:
                            print(f"[Wallet] Auto-load failed: {e}")

                    pending = db.get_pending_payments()
                    if pending:
                        for p in pending:
                            tx_info = xmr.get_transfer_by_txid(p["txid"])
                            if tx_info and tx_info["confirmed"]:
                                user = db.get_user(p["user_id"])
                                miner = db.get_or_create_miner(user.get("wallet_address", "")) if user else None
                                if miner:
                                    # Credit free tickets for deposits (1 ticket per 0.001 XMR)
                                    tickets = int(tx_info["amount"] / 0.001)
                                    db.add_tickets(miner["id"], "free", tickets)
                                db.credit_payment(p["id"], tickets if tickets else 0)

                    known = db.get_all_subaddress_mappings()
                    if known:
                        payments = xmr.scan_new_payments(known)
                        for pmt in payments:
                            db.record_payment(
                                pmt["user"], pmt["txid"], pmt["amount"],
                                pmt["subaddr_index"], pmt["height"], pmt["confirmations"]
                            )

                    result = engine.check_and_send_house_tax()
                    if result:
                        print(f"[Payout] House tax {result.get('action','?')}: {result.get('amount',0):.6f} XMR")

                    # Check for expired winner claims — return jackpot to pool
                    expired = engine.check_expired_claims()
                    if expired:
                        for e in expired:
                            print(f"[Claim] Drawing #{e['drawing_id']} expired — {e['returned']:.6f} XMR returned to pool")

                    # Seed jackpot if empty — try wallet first, then house tax
                    current_jackpot = float(db.get_setting("jackpot_balance", "0"))
                    if current_jackpot < 0.001:
                        # Try wallet first
                        seed = engine.seed_jackpot_from_wallet()
                        if seed:
                            print(f"[Seed] Jackpot seeded from wallet: {seed['seeded']:.6f} XMR")
                        else:
                            # Wallet empty — pull from accumulated house tax
                            house_tax = float(db.get_setting("house_tax_accumulated", "0"))
                            if house_tax > 0.001:
                                seed_amount = min(house_tax, float(db.get_setting("jackpot_seed_xmr", "0.05")))
                                new_jackpot = round(current_jackpot + seed_amount, 8)
                                db.set_setting("jackpot_balance", str(new_jackpot))
                                db.set_setting("house_tax_accumulated", str(round(house_tax - seed_amount, 8)))
                                print(f"[Seed] Jackpot seeded from house tax: {seed_amount:.6f} XMR (house tax remaining: {house_tax - seed_amount:.6f})")

                _time.sleep(60)
            except Exception:
                _time.sleep(60)

    scanner = threading.Thread(target=payment_scanner_loop, daemon=True)
    scanner.start()

    port = int(os.environ.get("APP_PORT", 80))
    uvicorn.run(app, host="0.0.0.0", port=port)
