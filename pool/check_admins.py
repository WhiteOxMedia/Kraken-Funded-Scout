"""Quick check: list all users and their admin status."""
import database as db
users = db.get_all_users()
for u in users:
    print(f"ID:{u['id']} | {u['username']} | is_admin:{u['is_admin']}")
