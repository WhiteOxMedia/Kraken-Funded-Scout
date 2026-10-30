"""test_operator_config_contract.py — THE ONE READER RULE, ENFORCED (Oct 29).

pool/operator_config.py is the single source of truth for five chains the
operator sits behind:

    vrsc_stratum()              XMRJ_VRSC_STRATUM         (the Verus relay lane)
    vrsc_territories()          db > env > US,CA          (who gets the VRSC branch)
    pro_price()                 scout_pro_price_usd       (the Scout Pro price)
    pro_days()                  scout_pro_days            (the Pro grant length)
    pro_checkout_minutes()      scout_pro_checkout_minutes (the checkout TTL)
    house_wallet()              house_wallet setting      (the pool's XMR wallet)
    kraken_deposit_address()    kraken_deposit_address    (the house-tax target)
    miner_wallet()              XMRJ_MINER_WALLET         (the miner's own address)
    xmrig_pin()                 version/url/sha/layout    (the xmrig release pin)
    proxy_api_base()            XMRJ_PROXY_API (> default) (the proxy API base URL)
    proxy_token()               env > txt > json > db     (the proxy bearer token)

Two halves:

  STATIC — every production file under the repo is scanned; reading any
  of the chains' env keys or settings DIRECTLY (an os.environ touch of
  one of the five env keys, or a db.get_setting on the four setting
  keys), or restating the xmrig pin (SHA or release URL) in Python,
  fails the contract — wherever the read hides. The allowlist is
  explicit and reasoned: pool/operator_config.py is THE reader;
  database.py's two env reads are the Oct 25 bootstrap SEED (a write
  into the defaults, not a resolution); installer/install.ps1 keeps the
  PowerShell chain's literals because it must stay standalone in the
  shipped bundle, and the second half of this file proves it agrees
  with the module to the byte. Verifiers are exempt by shape (test_*
  files, conftest.py, install_smoke's parse of install.ps1).

  BEHAVIOUR — sandbox-DB drives proving each chain still resolves in
  its documented order after the consolidation, that the module is
  importable with nothing but the standard library loaded, and that the
  PowerShell pin and the module pin are the same literals.

House conventions (the conftest harness): check/report, sandbox_db.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

from conftest import (                     # the harness, ONE copy: conftest.py
    check,                                 # one grade: PASS/FAIL + counters
    report,                                # the RESULT line + the exit code
    sandbox_db,                            # a fresh jackpot.db in a fresh tempdir
)

sys.path.insert(0, str(HERE / "pool"))

import operator_config as oc               # noqa: E402  (after the path insert)

os.environ["XMR_PRICE_LIVE"] = "0"         # hermetic: the rate chain stays off

_tmp = sandbox_db("operator_config_")

import database as db                      # noqa: E402
import xmr_price                           # noqa: E402


# ── the chains: tokens, and who may touch them and why ────────────────

BANNED_ENV = ["XMRJ_VRSC_STRATUM", "XMRJ_VRSC_TERRITORIES",
              "XMRJ_HOUSE_WALLET", "XMRJ_KRAKEN_DEPOSIT_ADDRESS",
              "XMRJ_MINER_WALLET", "XMRJ_PROXY_API",
              "XMRJ_PROXY_API_TOKEN"]
BANNED_SETTINGS = {"house_wallet": "setting:house_wallet",
                   "kraken_deposit_address": "setting:kraken_deposit_address",
                   "scout_pro_price_usd": "setting:scout_pro_price_usd",
                   "scout_pro_days": "setting:scout_pro_days",
                   "scout_pro_checkout_minutes": "setting:scout_pro_checkout_minutes",
                   "VRSC_TERRITORIES": "setting:VRSC_TERRITORIES",
                   "PROXY_API_TOKEN": "setting:PROXY_API_TOKEN"}
PIN_SHA = oc.XMRIG_SHA256
PIN_URL_MARK = "xmrig/releases/download/v"

ALLOWLIST = {
    "pool/operator_config.py": "*",        # THE reader — the point of the module
    "pool/database.py": {                  # the Oct 25 bootstrap seed (a WRITE)
        "env:XMRJ_HOUSE_WALLET",
        "env:XMRJ_KRAKEN_DEPOSIT_ADDRESS"},
    "installer/install.ps1": {             # the PS chain, bound in section 4
        "pin:sha", "pin:url"},
}
SKIP_DIRS = {"node_modules", "dist", "dist_site_review", ".git",
             "__pycache__", ".pr-cleanup-worktree", "reports", "artifacts",
             "_probe_state_dir", "auditors", "plans", ".workshop",
             ".freebuff", "xmrjackpot-seo", "release", "installer_payload"}
SCAN_EXTS = {".py", ".ps1"}

# READ-shaped patterns only: an os.environ TOUCH of a banned env key, or
# a get_setting on a banned setting. WRITES (set_setting) are not reads —
# the admin paths may store the operator's values anywhere; only a READ
# outside the resolver can drift. A string that mentions a key in a
# comment or an instruction does not match: these require the actual
# Python read shape.
_ENV_BODY = "|".join(re.escape(k) for k in BANNED_ENV)
_SETTING_BODY = "|".join(re.escape(k) for k in BANNED_SETTINGS)
PATTERNS = {
    **{("env:" + k): re.compile(
        r"os\.environ\.get\s*\(\s*['\"]" + re.escape(k) + r"['\"]"
        + r"|os\.environ\[[^]]*['\"]" + re.escape(k) + r"['\"]")
       for k in BANNED_ENV},
    **{tag: re.compile(r"\.get_setting\s*\(\s*['\"]" + re.escape(k) + r"['\"]")
       for k, tag in BANNED_SETTINGS.items()},
    # the proxy-API file tiers: reading the operator's token file or the
    # access-token OUT of the proxy's own config, anywhere but the ONE
    # reader. Writes are exempt by shape -- installer.py and
    # setup_xmrig.ps1 GENERATE a fresh config ("access-token": None),
    # which neither pattern matches; only a resolution-shaped read does.
    "proxy:token_file": re.compile(r"proxy_api_token\.txt"),
    "proxy:cfg_access": re.compile(
        r"\.get\s*\(\s*['\"]access-token['\"]"
        + r"|\[['\"]access-token['\"]\]"),
    "pin:sha": re.compile(re.escape(PIN_SHA)),
    "pin:url": re.compile(re.escape(PIN_URL_MARK)),
}

print("== 1. THE STATIC SCAN: NO DIRECT READS OUTSIDE THE RESOLVER ==")
_violations, _scanned = [], 0
for _root, _dirs, _files in os.walk(HERE):
    _dirs[:] = [d for d in _dirs if d not in SKIP_DIRS]
    for _fn in _files:
        _p = Path(_root) / _fn
        if _p.suffix not in SCAN_EXTS:
            continue
        rel = _p.relative_to(HERE).as_posix()
        # verifiers are exempt by shape: the tests, the harness, and
        # this file itself. install_smoke parses install.ps1's own
        # literals (never a second pin) — its file only mentions them.
        if _fn.startswith("test_") or _fn in ("conftest.py", "install_smoke.py"):
            continue
        try:
            txt = _p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        allowed = ALLOWLIST.get(rel)
        _scanned += 1
        for tag, rx in PATTERNS.items():
            m = rx.search(txt)
            if not m:
                continue
            if allowed == "*" or (isinstance(allowed, set) and tag in allowed):
                continue
            line = txt[:m.start()].count("\n") + 1
            _violations.append(f"{rel}:{line}: {tag} {m.group(0)[:56]!r}")

check(1.1, _scanned >= 50 and not _violations,
      f"scanned {_scanned} production files — " +
      (f"{len(_violations)} DIRECT READ(S) outside the resolver:" if _violations
       else "zero direct reads outside the resolver"))
for _v in _violations:
    print(f"    VIOLATION  {_v}")

print("\n== 2. THE MODULE IS IMPORTABLE STANDALONE ==")
_r = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'pool'); "
     "import operator_config; "
     "print(operator_config.xmrig_pin()['version'], "
     "operator_config.VRSC_TERRITORIES_DEFAULT)"],
    cwd=str(HERE), capture_output=True, text=True, timeout=60)
_out = (_r.stdout or "").split()
check(2.1, _r.returncode == 0 and _out[:2] == ["6.26.0", "US,CA"],
      f"a plain import (pool stack NOT loaded) serves the pin + default: "
      f"out={(_r.stdout or '').strip()[:40]!r} "
      f"err={(_r.stderr or '').strip()[-90:]!r}")

print("\n== 3. EACH CHAIN RESOLVES IN ITS DOCUMENTED ORDER ==")

print("3a. the relay lane")
os.environ.pop(oc.VRSC_STRATUM_ENV, None)
check(3.1, oc.vrsc_stratum() == "",
      "no env -> no lane (the public surface advertises no Verus entries)")
try:
    os.environ[oc.VRSC_STRATUM_ENV] = " relay.example.com:3956 "
    check(3.2, oc.vrsc_stratum() == "relay.example.com:3956",
          "the env value is the lane, stripped")
finally:
    os.environ.pop(oc.VRSC_STRATUM_ENV, None)

print("3b. the territories (db > env > built-in)")
try:
    os.environ.pop(oc.VRSC_TERRITORIES_ENV, None)
    src, raw = oc.vrsc_territories()
    check(3.3, src == "default" and raw == oc.VRSC_TERRITORIES_DEFAULT,
          "nothing set -> the built-in US,CA, source labelled 'default'")
    os.environ[oc.VRSC_TERRITORIES_ENV] = " DE,FR "
    src, raw = oc.vrsc_territories()
    check(3.4, src == "env" and raw == "DE,FR",
          "env only -> the env tier wins, source labelled 'env'")
    db.set_setting(oc.VRSC_TERRITORIES_SETTING, "JP,SG")
    src, raw = oc.vrsc_territories()
    check(3.5, src == "db" and raw == "JP,SG",
          "db set -> the db tier beats the env tier")
    check(3.6, oc.vrsc_territory_set() == {"JP", "SG"},
          "the set view splits and uppercases the codes")
    db.set_setting(oc.VRSC_TERRITORIES_SETTING, "")   # a BLANK db tier
    src, raw = oc.vrsc_territories()
    check(3.7, src == "env" and raw == "DE,FR",
          "a blank db value falls through to env (only a non-blank db wins)")
finally:
    db.set_setting(oc.VRSC_TERRITORIES_SETTING, "")
    os.environ.pop(oc.VRSC_TERRITORIES_ENV, None)

print("3c. the Pro price (stored USD, derived XMR)")
import json as _json                                    # noqa: E402
import jackpot_funding                                    # noqa: E402
db.set_setting(oc.SCOUT_PRO_PRICE_SETTING, "")
db.set_setting(xmr_price.OVERRIDE_KEY, "400")             # pin the rate at 400
xmr_price.reset()
_usd, _xmr = oc.pro_price()
check(3.8, _usd == 95.0 and abs(_xmr - 95.0 / 400.0) < 1e-9,
      "unset -> the documented $95, derived at the tracked rate "
      f"({_xmr} = 95/400)")
db.set_setting(oc.SCOUT_PRO_PRICE_SETTING, "50")
_usd, _xmr = oc.pro_price()
check(3.9, _usd == 50.0, f"the stored dollar figure is served verbatim: {_usd}")
check(3.10, abs(_xmr - 50.0 / 400.0) < 1e-9,
      f"the XMR amount derives at the tracked rate: {_xmr} = 50 / 400")
db.set_setting(oc.SCOUT_PRO_PRICE_SETTING, "0")           # a nulled price
_usd, _xmr = oc.pro_price()
check(3.11, _usd == 95.0, "a zeroed price falls back to $95, never charging $0")
db.set_setting(oc.SCOUT_PRO_PRICE_SETTING, "")
db.set_setting(xmr_price.OVERRIDE_KEY, "")
xmr_price.reset()

print("3d. the Pro knobs (the grant length + the checkout TTL)")
check(3.12, oc.pro_days() == 30 and oc.pro_checkout_minutes() == 30,
      "nothing set -> the documented defaults (30 days, a 30-minute window)")
db.set_setting(oc.SCOUT_PRO_DAYS_SETTING, "42")
db.set_setting(oc.SCOUT_PRO_CHECKOUT_MINUTES_SETTING, "90")
check(3.13, oc.pro_days() == 42 and oc.pro_checkout_minutes() == 90,
      "stored values are served verbatim: a 42-day grant, a 90-minute window")
db.set_setting(oc.SCOUT_PRO_DAYS_SETTING, "0")
db.set_setting(oc.SCOUT_PRO_CHECKOUT_MINUTES_SETTING, "1")
check(3.14, oc.pro_days() == 1 and oc.pro_checkout_minutes() == 5,
      "out-of-range values clamp to the floors (1 grant-day, the checkout's "
      "5-minute window floor) rather than granting zero days or an instant expiry")
db.set_setting(oc.SCOUT_PRO_DAYS_SETTING, "not-a-number")
db.set_setting(oc.SCOUT_PRO_CHECKOUT_MINUTES_SETTING, "")
check(3.15, oc.pro_days() == 30 and oc.pro_checkout_minutes() == 30,
      "unreadable / blank settings fall back to the defaults")
db.set_setting(oc.SCOUT_PRO_DAYS_SETTING, "")
db.set_setting(oc.SCOUT_PRO_CHECKOUT_MINUTES_SETTING, "")

print("3e. the wallets")
db.set_setting(oc.KRAKEN_DEPOSIT_SETTING, "")
os.environ.pop(oc.KRAKEN_DEPOSIT_ENV, None)
check(3.16, oc.kraken_deposit_address() == "",
      "nothing set -> unset (payout refuses loudly)")
try:
    os.environ[oc.KRAKEN_DEPOSIT_ENV] = " env-deposit "
    check(3.17, oc.kraken_deposit_address() == "env-deposit",
          "the deployment env is the same-boot fallback of an empty setting")
    db.set_setting(oc.KRAKEN_DEPOSIT_SETTING, "stored-deposit")
    check(3.18, oc.kraken_deposit_address() == "stored-deposit",
          "the stored setting beats the env fallback")
finally:
    os.environ.pop(oc.KRAKEN_DEPOSIT_ENV, None)
db.set_setting(oc.HOUSE_WALLET_SETTING, " house-wallet ")
check(3.19, oc.house_wallet() == "house-wallet",
      "the pool's own wallet is a plain stored setting, stripped")
db.set_setting(oc.HOUSE_WALLET_SETTING, "")
try:
    os.environ[oc.MINER_WALLET_ENV] = " miner-wallet "
    check(3.20, oc.miner_wallet() == "miner-wallet",
          "the miner's own address reads the deployment env, stripped")
finally:
    os.environ.pop(oc.MINER_WALLET_ENV, None)

print("3f. the proxy API (base URL + bearer token — the ONE order)")
_probe_dir = Path(tempfile.mkdtemp(prefix="oc_proxy_tiers_"))
_real_monero = oc.MONERO_DIR
try:
    oc.MONERO_DIR = _probe_dir              # the file tiers probe a sandbox dir
    os.environ.pop(oc.PROXY_API_ENV, None)
    os.environ.pop(oc.PROXY_TOKEN_ENV, None)
    db.set_setting(oc.PROXY_TOKEN_SETTING, "")
    check(3.21, oc.proxy_api_base() == "http://127.0.0.1:9090",
          "no env -> the documented loopback base")
    check(3.22, oc.proxy_token() == "",
          "no tier set -> UNSET (the caller sends no Authorization header)")
    os.environ[oc.PROXY_API_ENV] = " http://127.0.0.1:9200/ "
    check(3.23, oc.proxy_api_base() == "http://127.0.0.1:9200",
          "XMRJ_PROXY_API wins the base URL, trailing slash stripped")
    (_probe_dir / "proxy_api_token.txt").write_text(" file-token ",
                                                    encoding="utf-8")
    os.environ[oc.PROXY_TOKEN_ENV] = " env-token "
    check(3.24, oc.proxy_token() == "env-token",
          "the deployment env tier wins over the operator token file")
    os.environ.pop(oc.PROXY_TOKEN_ENV, None)
    check(3.25, oc.proxy_token() == "file-token",
          "no env -> the operator's token file tier resolves")
    (_probe_dir / "proxy_api_token.txt").unlink()
    (_probe_dir / "proxy_config.json").write_text(
        _json.dumps({"http": {"access-token": "cfg-token"}}),
        encoding="utf-8")
    check(3.26, oc.proxy_token() == "cfg-token",
          "no env, no token file -> the proxy's own enforced access-token")
    (_probe_dir / "proxy_config.json").unlink()
    db.set_setting(oc.PROXY_TOKEN_SETTING, " db-token ")
    check(3.27, oc.proxy_token() == "db-token",
          "no files -> the legacy db-setting tier resolves last")
    db.set_setting(oc.PROXY_TOKEN_SETTING, "")
    check(3.28, oc.proxy_token() == "",
          "every tier empty -> UNSET again (loud, never a guessed secret)")
finally:
    oc.MONERO_DIR = _real_monero
    shutil.rmtree(_probe_dir, ignore_errors=True)
    os.environ.pop(oc.PROXY_API_ENV, None)
    os.environ.pop(oc.PROXY_TOKEN_ENV, None)
    db.set_setting(oc.PROXY_TOKEN_SETTING, "")

print("\n== 4. THE PIN: MODULE AND POWERSHELL AGREE, TO THE BYTE ==")
_ps = (HERE / "installer" / "install.ps1").read_text(encoding="utf-8",
                                                     errors="replace")
_ps_sha = re.search(r"\$WantHash\s*=\s*\"([0-9a-f]{64})\"", _ps)
_ps_url = re.search(r"\$Url\s*=\s*\"(https://[^\"\r\n]+)\"", _ps)
check(4.1, _ps_sha is not None and _ps_sha.group(1) == oc.XMRIG_SHA256,
      "install.ps1's $WantHash is byte-identical to the module's SHA256")
check(4.2, _ps_url is not None and _ps_url.group(1) == oc.XMRIG_URL,
      "install.ps1's $Url names the module's exact release asset")
_v, _u = oc.XMRIG_VERSION, oc.XMRIG_URL
check(4.3, f"v{_v}/" in _u and f"xmrig-{_v}-windows-x64.zip" in _u,
      "the module's URL carries the module's version (no split-brain pin)")
check(4.4, oc.xmrig_pin()["layout_dir"] == "xmrig\\" + _v,
      f"the layout dir <root>/xmrig/{_v} follows the version")

print("\n== 5. THE READERS RIDE THE SAME db BINDING ==")
db.set_setting(oc.HOUSE_WALLET_SETTING, "binding-probe")
check(5.1, oc.house_wallet() == "binding-probe",
      "operator_config reads THIS process's database module (sandbox-safe)")
db.set_setting(oc.HOUSE_WALLET_SETTING, "")

report()
