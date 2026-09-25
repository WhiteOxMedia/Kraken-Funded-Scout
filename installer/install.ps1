# install.ps1 - KRAKEN SCOUT IN ONE FLOW (Sep 25).
#
#   1. preflight uv (winget install astral-sh.uv when absent)
#   2. robocopy the payload to %LOCALAPPDATA%\KrakenScout
#   3. uv sync the venv (Scout's own launcher contract)
#   4. end on the checkbox UI (installer_ui.py - the Miner Bot choice)
#
# The Miner Bot stays OPTIONAL exactly as the protocol orders it: the
# checkbox default is ON, and unchecking it configures and registers
# NOTHING (manual registration at xmrjackpot.com stays the human act).
#
# Switches:
#   -Target <dir>   install somewhere other than %LOCALAPPDATA%\KrakenScout
#   -NoUi           print manual instructions instead of the checkbox window
#   -SkipVenv       stage only (for drive tests / air-gapped smoke)
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
#       (the bundle root in the shipped bundle; INSTALL bat wraps it;
#       in this repo it lives beside build_bundle.ps1, which stages it
#       to the bundle root when building)

param(
    [string]$Target = "",
    [switch]$NoUi,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$Src   = Join-Path $PSScriptRoot "payload\scout"    # repo layout during dev
if (-not (Test-Path (Join-Path $Src "app.py"))) {
    $Src = $PSScriptRoot                          # bundle-root layout when shipped
}
$PayDir = Split-Path -Parent $Src            # payload\ (repo) or bundle root (shipped)
if (-not $Target) {
    $Target = Join-Path $env:LOCALAPPDATA "KrakenScout"
}
$DstScout  = Join-Path $Target "scout"
$DstMonero = Join-Path $Target "monero_project"   # THE NAME CONTRACT:
# app.py's jackpot plate imports the sibling as monero_project\pool -
# any other name breaks the plate's one-brain transport (pinned by
# test_bundle.py).

Write-Host "== KRAKEN SCOUT SETUP =="
Write-Host ("  from : " + $PayDir)
Write-Host ("  to   : " + $Target)

if (-not (Test-Path (Join-Path $Src "app.py"))) {
    throw "payload looks wrong - no scout\app.py beside install.ps1"
}

# ── 1. preflight uv ───────────────────────────────────────────────────
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "[1/4] uv missing - installing via winget ..."
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget could not install uv - install it from https://docs.astral.sh/uv/ and re-run" }
    # this session's PATH may predate the install
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv was installed but is not on PATH yet - open a new terminal and re-run"
    }
} else {
    Write-Host "[1/4] uv found"
}

# ── 2. stage the payload ──────────────────────────────────────────────
# Robocopy failure codes 0-7 are success-shaped (0-3 no copies, 4-7
# copies happened); 8+ is a real failure. Restarting over an existing
# install is supported: the trees are replaced, opt-in state is not
# touched (it lives in monero_project\installer\install_config.json).
Write-Host "[2/4] staging app trees ..."
robocopy (Join-Path $PayDir "scout") $DstScout /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy scout failed rc=$LASTEXITCODE" }
robocopy (Join-Path $PayDir "monero_project") $DstMonero /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy monero failed rc=$LASTEXITCODE" }
Write-Host ("  staged: " + $DstScout)
Write-Host ("  staged: " + $DstMonero)

# ── 3. the xmrig payload (present in the bundle, else pinned URL) ─────
Write-Host "[3/4] the miner payload ..."
$DstXmrig = Join-Path $Target "xmrig\xmrig-6.26.0"
if (Test-Path (Join-Path $PayDir "xmrig-6.26.0\xmrig.exe")) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DstXmrig) | Out-Null
    robocopy (Join-Path $PayDir "xmrig-6.26.0") $DstXmrig /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy xmrig failed rc=$LASTEXITCODE" }
    Write-Host "  staged local xmrig 6.26.0"
} else {
    $Url = "https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-msvc-win64.zip"
    $Zip = Join-Path $env:TEMP "xmrig-6.26.0-msvc-win64.zip"
    Write-Host ("  fetching pinned xmrig 6.26.0 (official release) ...")
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
        # THE PINNED HASH (review triage, Sep 25): the downloaded miner
        # payload is verified against the hash of the exact release the
        # bundle ships — the same bytes the local payload's exe hashes
        # to — before a single byte is extracted.
        $WantHash = "6fa80698d7268f6e88aa88c06fb27ee99e1bcee747c2e76911e6206a5b1aeeb3"
        $GotHash = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLower()
        if ($GotHash -ne $WantHash) {
            Remove-Item $Zip -ErrorAction SilentlyContinue
            throw "xmrig download failed its SHA256 pin ($($GotHash.Substring(0,16))...) - not extracting"
        }
        Write-Host "  sha256 verified against the pinned release hash"
        Expand-Archive -Path $Zip -DestinationPath (Join-Path $Target "xmrig") -Force
        Remove-Item -Force $Zip
        Write-Host "  fetched xmrig 6.26.0"
    } catch {
        Write-Warning ("xmrig download failed (" + $_.Exception.Message + ") - the Miner Bot can fetch it later; Scout itself is unaffected")
    }
}

# ── 4. the venv ───────────────────────────────────────────────────────
if ($SkipVenv) {
    Write-Host "[4/4] -SkipVenv: venv build skipped (drive mode)"
} else {
    Write-Host "[4/4] building the venv (uv sync, this is the long step) ..."
    Push-Location $DstScout
    try {
        & uv sync
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed rc=$LASTEXITCODE" }
    } finally {
        Pop-Location
    }
    & (Join-Path $DstScout ".venv\Scripts\python.exe") -m py_compile (Join-Path $DstScout "app.py")
    if ($LASTEXITCODE -ne 0) { throw "staged app.py does not compile" }
    # the staged copy ships without a pool DB (no jackpot.db in the
    # payload) - give it the empty schema so the checkbox's welcome-
    # ticket grant cannot crash on a missing table; the live pool on
    # this box keeps its own DB untouched
    $StagedDb = Join-Path $DstMonero "pool\jackpot.db"
    if (-not (Test-Path $StagedDb)) {
        try {
            & (Join-Path $DstScout ".venv\Scripts\python.exe") -c "import sys; sys.path.insert(0, r'$DstMonero\pool'); import database; database.init_db()"
            if ($LASTEXITCODE -ne 0) { Write-Warning "staged pool DB not initialized - the Miner Bot ticket grant may need the pool server up first" }
        } catch { Write-Warning "staged pool DB not initialized - the Miner Bot ticket grant may need the pool server up first" }
    }
}

# ── 5. THE CHECKBOX (the flow's last step) ────────────────────────────
$UiPy  = Join-Path $DstMonero "installer\installer_ui.py"
$PyExe = Join-Path $DstScout ".venv\Scripts\python.exe"
if ($SkipVenv) { $PyExe = "python" }
if ($NoUi -or -not (Test-Path $PyExe)) {
    Write-Host ""
    Write-Host "== STAGED. The Miner Bot choice remains =="
    Write-Host ("  run: " + $PyExe + " " + $UiPy)
    Write-Host "  (or headless: installer.py --opt-in --wallet <address> / --opt-out)"
    Write-Host ""
    # references, not quotes - the phrase's home is the installer constant
    Write-Host "  Getting started: see the xmrjackpot.com note the setup window shows."
    return
}
Write-Host "== staged and compiled - opening the setup window (the checkbox) =="
Push-Location $DstMonero
try {
    & $PyExe $UiPy
    if ($LASTEXITCODE -ne 0) { Write-Warning ("setup window exited " + $LASTEXITCODE + " - headless path: installer.py --opt-in --wallet <address>") }
} finally {
    Pop-Location
}
Write-Host ("Setup complete. Launch Scout from " + (Join-Path $DstScout "Launch Kraken.bat"))
