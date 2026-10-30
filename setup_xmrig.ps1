# ─────────────────────────────────────────────────────────────────────────
# SETUP_XMRIG: build a valid xmrig config.json (Oct 29 rewrite).
#
# The previous version was an UNASSIGNED PowerShell here-string: running it
# evaluated the string and wrote NOTHING — xmrig then read no config at
# all (and the JSON it carried was invalid anyway: "d ponds", duplicate
# "recv-diff" keys, unknown xmrig fields).
#
# This version builds the same values the installer's customer-neutral
# template writes (installer/installer.py::_neutral_config): the public
# stratum, the wallet as the pool user, the local HTTP API enabled — then
# serializes and WRITES the result explicitly.
#
# Usage:  powershell -File setup_xmrig.ps1 -Wallet 4YourMoneroAddress90to120chars
#         (no argument builds the placeholder, exactly as the installer does)
# ─────────────────────────────────────────────────────────────────────────
param([string]$Wallet = "YOUR_MONERO_ADDRESS")

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Wallet)) { $Wallet = "YOUR_MONERO_ADDRESS" }

$xmrigDir   = Join-Path $PSScriptRoot "xmrig\xmrig-6.26.0"
$ConfigPath = Join-Path $xmrigDir "config.json"

$stratum = $env:XMRJ_STRATUM_URL
if ([string]::IsNullOrWhiteSpace($stratum)) { $stratum = "mine.xmrjackpot.com:3333" }

# SAME TEMPLATE AS THE INSTALLER (_neutral_config): no rig id is invented,
# the pass is the pool's ordinary placeholder, the local HTTP API is on
# so xmrig's JSON stats stay reachable at 127.0.0.1:18000.
$config = [ordered]@{
  api        = [ordered]@{ id = $null; "worker-id" = $null }
  http       = [ordered]@{ enabled = $true; host = "127.0.0.1"; port = 18000;
                           "access-token" = $null; restricted = $true }
  autosave   = $true
  background = $false
  colors     = $false
  title      = $true
  randomx    = [ordered]@{ init = -1; mode = "auto"; "1gb-pages" = $false;
                           rdmsr = $false; wrmsr = $false }
  cpu        = [ordered]@{ enabled = $true; "huge-pages" = $true;
                           "hw-aes" = $null; priority = $null;
                           "memory-pool" = $false; yield = $true; asm = $true }
  pools      = @([ordered]@{
    algo             = $null
    coin             = "monero"
    url              = $stratum
    user             = $Wallet
    pass             = "x"
    "rig-id"         = $null
    nicehash         = $false
    keepalive        = $true
    enabled          = $true
    tls              = $false
    "tls-fingerprint" = $null
  })
  "print-time"        = 60
  "health-print-time" = 60
  retries             = 5
  "retry-pause"       = 5
  watch               = $false
}

New-Item -ItemType Directory -Force -Path $xmrigDir | Out-Null
# EXPLICIT SERIALIZE + WRITE — this is the step the old here-string skipped.
# UTF8Encoding($false), NOT -Encoding UTF8: PS5.1's UTF8 writes a BOM and
# the config must read byte-clean (the installer's json.dumps does).
$json = $config | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($ConfigPath, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "xmrig config written: $ConfigPath"
Write-Host "pool: $stratum  user: $Wallet"
