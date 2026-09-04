"""Compare the scout's reader copy with the bot's live copy (which is the
source of truth for edits)."""
from pathlib import Path
import difflib

a = Path(r"C:\Users\birds\Workspace\kraken_scout\scout_signal_reader.py").read_text(encoding="utf-8").splitlines()
b = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py").read_text(encoding="utf-8").splitlines()

diff = list(difflib.unified_diff(a, b, fromfile="scout_copy", tofile="bot_live", lineterm="", n=1))
out = [f"scout copy: {len(a)} lines; bot live: {len(b)} lines; diff hunks: {sum(1 for l in diff if l.startswith('@@'))}"]
# print the diff hunks (capped)
for l in diff[:220]:
    out.append(l)
Path(r"C:\Users\birds\Workspace\kraken_scout\reader_copy_diff.txt").write_text("\n".join(out), encoding="utf-8")
print("DONE")
