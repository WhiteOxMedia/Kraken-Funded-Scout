"""Test the EXACT user flow: xmrjackpot.com -> Admin tab -> embedded Scout
console iframe (with a real admin session cookie set, phone-sized viewport)."""
import secrets
import sqlite3
import time

from playwright.sync_api import sync_playwright

DB = r"C:\Users\birds\Workspace\monero_project\pool\jackpot.db"
o = []

token = "diag-" + secrets.token_hex(24)
conn = sqlite3.connect(DB)
conn.execute(
    "INSERT INTO sessions (token, user_id, created_at, expires_at, ip_address) "
    "VALUES (?, 1, ?, ?, 'diag')",
    (token, time.time(), time.time() + 3600),
)
conn.commit()
conn.close()

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 420, "height": 900},
            user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
        )
        ctx.add_cookies([{
            "name": "session", "value": token,
            "domain": "xmrjackpot.com", "path": "/",
        }])
        page = ctx.new_page()
        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text[:160]}"))

        page.goto("https://xmrjackpot.com/", timeout=45000, wait_until="domcontentloaded")
        o.append("loaded home page")

        # Visit Dashboard first — that triggers dashCheckAuth() which reveals navAdmin
        try:
            page.wait_for_selector('[data-page="dashboard"]', timeout=15000)
            page.click('[data-page="dashboard"]')
            o.append("clicked Dashboard tab (triggers auth check)")
            page.wait_for_timeout(2500)
        except Exception as e:
            o.append(f"dashboard tab issue: {type(e).__name__}")

        # The admin nav button appears for admins (navAdmin)
        try:
            page.wait_for_selector("#navAdmin", state="visible", timeout=15000)
            page.click("#navAdmin")
            o.append("clicked Admin tab")
        except Exception as e:
            o.append(f"admin tab not found/clickable: {type(e).__name__}")
            o.append("nav html: " + page.inner_text("nav")[:200])

        # Admin panel loads, scout card + iframe appear
        try:
            page.wait_for_selector("#scoutFrame", timeout=20000)
            o.append("scout iframe present in admin panel")
        except Exception:
            o.append("NO scout iframe found in admin panel")

        # Wait for the iframe's Streamlit to render (look inside frame)
        try:
            frame = page.frame_locator("#scoutFrame")
            frame.locator(
                '[data-testid="stMainMenu"], [data-testid="stAppViewContainer"], section.main'
            ).wait_for(timeout=60000)
            o.append("EMBEDDED CONSOLE RENDERED inside the admin panel iframe")
            txt = frame.locator("body").inner_text(timeout=10000)[:400].replace("\n", " | ")
            o.append(f"iframe body: {txt}")
        except Exception as e:
            o.append(f"iframe did NOT render: {type(e).__name__}: {str(e)[:200]}")

        o.append("--- console messages (first 15):")
        o.extend(console_msgs[:15])
        page.screenshot(path="_scout_admin_phone.png")
        browser.close()
finally:
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

open("_adminflow_check.txt", "w", encoding="utf-8").write("\n".join(o))
print("DONE")
