# build_bundle.ps1 - THE BUNDLE BUILDER (Sep 25).
#
# Builds dist\KrakenScoutBundle from the COMMITTED trees of both repos:
#   scout\   <- git -C kraken_scout archive HEAD (509 files; no worktree
#               drift: the other thread's uncommitted hunks, scratch
#               artifacts, and battery history never ship)
#   monero\  <- git -C monero_project archive HEAD (the installer, the
#               pool view, the settlement model)
#   xmrig\   <- the LOCAL xmrig/xmrig-6.26.0 payload (6.2 MB), copied
#               from the working tree (untracked in git, so archive
#               cannot carry it); falls back to the pinned GitHub URL
#               at INSTALL time when absent from the bundle
#   one-click wrappers (Install/Uninstall bats) at the bundle root;
#   Scout's own tracked launchers ship inside scout\ via git archive
#
# THE NAME CONTRACT: the payload lands as monero_project\ so app.py's
# sibling import (Path(__file__).parent.parent / "monero_project" /
# "pool") resolves in installed copies - the jackpot plate's one-brain
# transport depends on that name (pinned by test_bundle.py).
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_bundle.ps1
#       (add -Open to open the bundle folder when done)

param([switch]$Open)

$ErrorActionPreference = "Stop"
$Root    = Split-Path -Parent $PSScriptRoot          # monero_project\
$Scout   = Join-Path (Split-Path -Parent $Root) "kraken_scout"
$Dist    = Join-Path $Root "dist"
$Bundle  = Join-Path $Dist "KrakenScoutBundle"
$Stage   = Join-Path $Dist "_stage_tar"

Write-Host "== THE BUNDLE BUILDER =="
Write-Host ("  scout  : " + $Scout)
Write-Host ("  monero : " + $Root)
Write-Host ("  out    : " + $Bundle)

if (-not (Test-Path (Join-Path $Scout "app.py"))) {
    throw "kraken_scout not found beside monero_project: $Scout"
}
$ScoutHead = (git -C $Scout rev-parse --short HEAD).Trim()
$MoneroHead = (git -C $Root rev-parse --short HEAD).Trim()
Write-Host ("  scout HEAD " + $ScoutHead + " | monero HEAD " + $MoneroHead)

# fresh bundle dir (the old one is yesterday's ship - out it goes)
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
if (Test-Path $Stage)  { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Bundle, (Join-Path $Bundle "payload"), $Stage | Out-Null

# ── 1. the committed trees (git archive = HEAD only, no drift) ────────
# archive writes to a FILE, never through the PowerShell pipe (PS 5.1
# pipes native stdout as text - a tar stream would be corrupted); and
# the tar is PINNED to Windows' own bsdtar: plain `tar` resolves to
# Git's MSYS tar here, which reads the colon in C:\... paths as a
# remote host ("Cannot connect to C: resolve failed")
$TAR = Join-Path $env:SystemRoot "System32\tar.exe"
Write-Host "[1/5] scout: git archive HEAD ..."
$TarF = Join-Path $Dist "_archive.tar"
git -C $Scout archive --format=tar -o $TarF HEAD
if ($LASTEXITCODE -ne 0) { throw "scout archive failed" }
& $TAR -xf $TarF -C $Stage
if ($LASTEXITCODE -ne 0) { throw "scout untar failed" }
$vpy = Join-Path $Stage ".venv"
if (Test-Path $vpy) { Remove-Item -Recurse -Force $vpy }   # never ships
Move-Item $Stage (Join-Path $Bundle "payload\scout")
New-Item -ItemType Directory -Force -Path $Stage | Out-Null   # the stage
# moved away with scout - a fresh one for monero (the drive caught
# the first draft untarring into a vanished directory)

Write-Host "[2/5] monero: git archive HEAD ..."
git -C $Root archive --format=tar -o $TarF HEAD
if ($LASTEXITCODE -ne 0) { throw "monero archive failed" }
& $TAR -xf $TarF -C $Stage
if ($LASTEXITCODE -ne 0) { throw "monero untar failed" }
Remove-Item -Recurse -Force -Path (Join-Path $Stage "dist") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -Path (Join-Path $Stage "xmrig") -ErrorAction SilentlyContinue
Move-Item $Stage (Join-Path $Bundle "payload\monero_project")
Remove-Item -Force $TarF -ErrorAction SilentlyContinue

# the pool layer's DB module is UNTRACKED in monero_project (the pool
# thread has not committed it yet), so the archive cannot carry it -
# but scout_jackpot_view imports it, and without it the jackpot plate's
# transport is dead in installed copies. Copy exactly the allowlisted
# file from the working tree, loudly - the archive-only rule stands
# for everything else.
$DbSrc = Join-Path $Root "pool\database.py"
$DbDst = Join-Path $Bundle "payload\monero_project\pool\database.py"
if (-not (Test-Path $DbDst)) {
    if (Test-Path $DbSrc) {
        Copy-Item $DbSrc $DbDst -Force
        Write-Warning "pool/database.py staged from the WORKING TREE (untracked in monero_project) - re-run the builder after the pool thread commits it"
    } else {
        Write-Warning "pool/database.py MISSING - installed copies will show the jackpot plate's quiet fallback line"
    }
}

# ── 2. the xmrig payload (untracked -> copy from the working tree) ────
Write-Host "[3/5] xmrig payload ..."
$xsrc = Join-Path $Root "xmrig\xmrig-6.26.0"
if (Test-Path (Join-Path $xsrc "xmrig.exe")) {
    Copy-Item -Recurse -Force $xsrc (Join-Path $Bundle "payload\xmrig-6.26.0")
} else {
    Write-Warning "local xmrig payload absent - install.ps1 will fetch the pinned release"
}

# ── 3. launcher bats ──────────────────────────────────────────────────
# The tracked bats (Launch Scout.bat, Kraken - STOP.bat, start_detached.ps1)
# already shipped inside scout\ via git archive - byte-for-byte the
# fleet's own launchers, no second copy to drift. The bundle-root
# entries below are the ONE-CLICK wrappers: Install runs install.ps1
# (the whole flow, ending on the checkbox UI), Uninstall is the
# self-deleting opt-out-and-go (no registry, no scheduled tasks, no
# Startup - deleting the folder IS the uninstall).
Write-Host "[4/5] one-click wrappers + install.ps1 ..."   # (the README follows as [5/5])
foreach ($b in @("Install Kraken Scout.bat", "Uninstall Kraken Scout.bat")) {
    Copy-Item (Join-Path $PSScriptRoot $b) (Join-Path $Bundle $b) -Force
}
Copy-Item (Join-Path $PSScriptRoot "install.ps1") (Join-Path $Bundle "install.ps1") -Force
# ── 5. the README ─────────────────────────────────────────────────────
Write-Host "[5/5] the README ..."
$Enc = New-Object System.Text.UTF8Encoding($false)
$Readme = @"
KRAKEN SCOUT BUNDLE (built from committed heads: scout $ScoutHead, monero $MoneroHead)

INSTALL   double-click  Install Kraken Scout.bat
          (or:  powershell -NoProfile -ExecutionPolicy Bypass -File payload\scout\install.ps1)
          One flow: preflight uv, stage the two app trees into
          %LOCALAPPDATA%\KrakenScout, build the venv, then the setup
          window opens - the Miner Bot checkbox is the last step.

RUN       payload\scout\Launch Kraken.bat   (console + scanner)
          payload\scout\Kraken - STOP.bat   (stop both)

UNINSTALL double-click  Uninstall Kraken Scout.bat  (here in the bundle folder)
          Self-deleting: removes %LOCALAPPDATA%\KrakenScout and this
          bundle folder. No registry keys, no services, no scheduled
          tasks, no Startup entries - deleting the folder IS the
          uninstall. (Scout's own Stop button is there too.)

NOTES     The Miner Bot is OPTIONAL (the checkbox default is ON; uncheck
          it and nothing miner-related is configured or registered).
          Payouts require manual registration at xmrjackpot.com - the
          setup window shows the start-here note when it finishes.
"@
[System.IO.File]::WriteAllText((Join-Path $Bundle "README.txt"), ($Readme + "`r`n"), $Enc)

Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
$ScoutFiles = (Get-ChildItem -Recurse -File (Join-Path $Bundle "payload\scout")).Count
$MoneroFiles = (Get-ChildItem -Recurse -File (Join-Path $Bundle "payload\monero_project")).Count
$SizeMB = [math]::Round(((Get-ChildItem -Recurse -File $Bundle | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ("BUNDLE READY: " + $Bundle)
Write-Host ("  scout files: " + $ScoutFiles + " | monero files: " + $MoneroFiles + " | total " + $SizeMB + " MB")
if ($Open) { Invoke-Item $Bundle }
