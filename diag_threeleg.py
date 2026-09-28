"""Full three-leg probe with the real first message (BackMsg rerun_script = 5a00).
Leg 1: direct to console :8501. Leg 2: through local proxy :3094 (admin cookie).
Leg 3: through public Cloudflare tunnel (admin cookie)."""
import asyncio
import secrets
import sqlite3
import time

import websockets

DB = r"C:\Users\birds\Workspace\monero_project\pool\jackpot.db"
FIRST_MSG = bytes.fromhex("5a00")  # BackMsg{rerun_script: RerunScript{}}
o = []


def make_admin_token():
    token = "diag-" + secrets.token_hex(24)
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at, ip_address) "
        "VALUES (?, 1, ?, ?, 'diag')",
        (token, time.time(), time.time() + 3600),
    )
    conn.commit()
    conn.close()
    return token


def drop_token(token):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


async def probe(label, url, cookie=None, timeout=60):
    hdrs = {"Cookie": f"session={cookie}"} if cookie else None
    try:
        async with websockets.connect(url, additional_headers=hdrs, open_timeout=15) as ws:
            await ws.send(FIRST_MSG)
            o.append(f"{label}: connected, sent rerun_script")
            t0 = time.time()
            total = 0
            frames = 0
            try:
                while frames < 5:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    frames += 1
                    total += len(msg)
                    if frames == 1:
                        o.append(f"{label}: FIRST FRAME after {time.time()-t0:.1f}s, {len(msg)} bytes")
            except asyncio.TimeoutError:
                o.append(f"{label}: frames={frames} total={total} bytes, then {timeout}s silence")
            except websockets.exceptions.ConnectionClosed as e:
                o.append(f"{label}: closed after {frames} frames / {total} bytes ({e.rcvd})")
            if frames == 0:
                o.append(f"{label}: NO FRAME AT ALL  <-- BROKEN")
            else:
                o.append(f"{label}: OK ({frames} frames, {total} bytes)")
    except Exception as e:
        o.append(f"{label}: connect failed: {type(e).__name__}: {e}")


async def main():
    await probe("LEG1 direct :8501", "ws://127.0.0.1:8501/_stcore/stream", timeout=45)
    token = make_admin_token()
    try:
        await probe("LEG2 proxy :3094", "ws://127.0.0.1:3094/scout/_stcore/stream", cookie=token, timeout=45)
        await probe("LEG3 public  CF ", "wss://xmrjackpot.com/scout/_stcore/stream", cookie=token, timeout=60)
    finally:
        drop_token(token)


asyncio.run(main())
open("_threeleg_check.txt", "w", encoding="utf-8").write("\n".join(o))
print("DONE")
