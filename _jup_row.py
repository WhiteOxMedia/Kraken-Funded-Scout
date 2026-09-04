import sqlite3
c = sqlite3.connect("signals.db")
c.row_factory = sqlite3.Row
r = c.execute(
    "SELECT * FROM signals WHERE pair LIKE ? ORDER BY timestamp DESC LIMIT 1",
    ("JUP%",),
).fetchone()
for k in r.keys():
    if r[k] is not None:
        print(f"{k}: {r[k]}")
