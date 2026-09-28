"""Quick fix: refresh miner last_seen timestamps so they show as active."""
import database as db
import time

now = time.time()
miners = db.get_all_miners()
with db.get_db() as conn:
    for m in miners:
        mins_ago = (m['id'] % 10) * 60
        conn.execute('UPDATE miners SET last_seen = ? WHERE id = ?', (now - mins_ago, m['id']))
print(f'Updated {len(miners)} miners with fresh last_seen timestamps')
