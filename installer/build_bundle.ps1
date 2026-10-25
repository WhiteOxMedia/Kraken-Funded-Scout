# build_bundle.ps1 - THE BUNDLE BUILDER (Sep 25; whitelist since Oct 25).
#
# THE RELEASE CONTRACT: the staged tree is pruned to
# installer\release_manifest.json before it is zipped. The manifest is
# DEFAULT-DENY — a file ships only when it is named there — so the archive
# below is an input, never the artifact. Tests, audit and phase documents,
# CI files, the pool server/engine, the proxy poller and the developer
# xmrig config cannot reach a customer even if a future commit adds them.
# installer\finalize_bundle.py enforces it (and fails the build on any
# content violation: credentials, operator wallets, localhost endpoints,
# preprod versions). Regenerate the file list with
# installer\generate_release_manifest.py when Scout's imports change.
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
# SCOPE NOTE (static surface): This builder ships the public
# KrakenScoutBundle.zip (scout + monero_project pool view + xmrig + one-
# click wrappers). It is OUT OF SCOPE for the pool/static scout-bundle
# surface (app.js, scout_ids.js, scout_paywall.js,
# scout_paywall_wire_patch.js, scout_checkout.js, wf_dashboard.js, and
# the other template-referenced static JS files). Those files ship
# through make_bundle.py -> dist_site_review/xmrjackpot_review/static/.
# If a future edit intends to ship the pool/static JS here instead,
# that must be an explicit, documented decision — not an accidental
# carry from the monero_project working tree.
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

# EARLY GUARD (fail before assembling the payload): the topic guard in
# finalize_bundle.py can only scan source-tree CONTENT for the live topic when
# push_topic.txt is present - without it the content arm is silent and the
# served zip is PUBLIC, so proceed-on-denylist-alone is not allowed. Catch it
# here, before the git archives and the xmrig copy, with a clear message.
$TopicFile = Join-Path $Scout "push_topic.txt"
if (-not (Test-Path $TopicFile)) {
    $msg = "BUILD ABORTED: push_topic.txt is missing at $TopicFile" +
           " - the topic guard cannot scan the staged payload for the live" +
           " ntfy topic without it (the served zip is PUBLIC). Restore it or" +
           " run finalize_bundle.py's own check for the exact requirement."
    Write-Host "== EARLY GUARD: $msg" -ForegroundColor Red
    exit 1
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
Write-Host "[1/6] scout: git archive HEAD ..."
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

Write-Host "[2/6] monero: git archive HEAD ..."
git -C $Root archive --format=tar -o $TarF HEAD
if ($LASTEXITCODE -ne 0) { throw "monero archive failed" }
& $TAR -xf $TarF -C $Stage
if ($LASTEXITCODE -ne 0) { throw "monero untar failed" }
Remove-Item -Recurse -Force -Path (Join-Path $Stage "dist") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -Path (Join-Path $Stage "xmrig") -ErrorAction SilentlyContinue
Move-Item $Stage (Join-Path $Bundle "payload\monero_project")
Remove-Item -Force $TarF -ErrorAction SilentlyContinue

# THE POOL DOES NOT SHIP. The old build copied pool/database.py into the
# payload so Scout's local jackpot plate could read an operator database;
# neither the plate nor the pool belongs in a customer artifact, and the
# release manifest excludes the whole pool tree. Scout shows its own
# "jackpot view is not wired" line when the sibling is absent, which is
# the correct customer-facing behaviour (the pool is operator-only).

# ── 2. the xmrig payload (untracked -> copy from the working tree) ────
Write-Host "[3/6] xmrig payload ..."
$xsrc = Join-Path $Root "xmrig\xmrig-6.26.0"
if (Test-Path (Join-Path $xsrc "xmrig.exe")) {
    Copy-Item -Recurse -Force $xsrc (Join-Path $Bundle "payload\xmrig-6.26.0")
    # THE DEVELOPER CONFIG NEVER SHIPS: this working-tree copy mines to
    # 127.0.0.1:3333 under the operator's rig id. The release manifest
    # would prune it anyway; removing it here keeps the staged tree honest
    # before the guard runs (and the miner bot writes a customer config).
    Remove-Item -Force (Join-Path $Bundle "payload\xmrig-6.26.0\config.json") -ErrorAction SilentlyContinue
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
Write-Host "[4/6] one-click wrappers + install.ps1 ..."   # (the README follows as [5/6])
foreach ($b in @("Install Kraken Scout.bat", "Uninstall Kraken Scout.bat")) {
    Copy-Item (Join-Path $PSScriptRoot $b) (Join-Path $Bundle $b) -Force
}
Copy-Item (Join-Path $PSScriptRoot "install.ps1") (Join-Path $Bundle "install.ps1") -Force
# THE PAYLOAD'S COPY OF THE SAME FILE. The monero payload is `git archive
# HEAD`, which can lag the working tree; the root installer above comes from
# the working tree. Leaving the two out of step shipped an artifact whose
# root installer had one xmrig pin and whose payload copy had another (the
# broken one), so sync it here instead of hoping HEAD catches up.
$PayloadInstaller = Join-Path $Bundle "payload\monero_project\installer\install.ps1"
if (Test-Path $PayloadInstaller) {
    Copy-Item (Join-Path $PSScriptRoot "install.ps1") $PayloadInstaller -Force
}
# ── 5. the README ─────────────────────────────────────────────────────
Write-Host "[5/6] the README ..."
$Enc = New-Object System.Text.UTF8Encoding($false)
$Readme = @"
KRAKEN SCOUT BUNDLE (built from committed heads: scout $ScoutHead, monero $MoneroHead)

INSTALL   double-click  Install Kraken Scout.bat  (here in the bundle folder)
          (or:  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1)
          One flow: preflight uv, stage the two app trees into
          %LOCALAPPDATA%\KrakenScout, build the venv, then the setup
          window opens - the Miner Bot checkbox is the last step.

RUN       %LOCALAPPDATA%\KrakenScout\scout\Launch Kraken.bat   (console + scanner)
          %LOCALAPPDATA%\KrakenScout\scout\Kraken - STOP.bat   (stop both)

UNINSTALL double-click  Uninstall Kraken Scout.bat  (here in the bundle folder)
          Self-deleting: removes %LOCALAPPDATA%\KrakenScout and this
          bundle folder. No registry keys, no services, no scheduled
          tasks, no Startup entries - deleting the folder IS the
          uninstall. (Scout's own Stop button is there too.)

NOTES     The Miner Bot is OPTIONAL (the checkbox default is ON; uncheck
          it and nothing miner-related is configured or registered).
          Payouts require manual registration at xmrjackpot.com - the
          setup window shows the start-here note when it finishes.
          PHONE PUSH  The scout's private ntfy topic is NOT shipped -
          the operator provisions push_topic.txt in the scout folder
          after install (rotation hygiene, Oct 7).
"@
[System.IO.File]::WriteAllText((Join-Path $Bundle "README.txt"), ($Readme + "`r`n"), $Enc)

Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue

# ── 6. THE TOPIC GUARD + THE PUBLIC ARTIFACT (Oct 7, Phase 0 of the
# ntfy topic rotation) ────────────────────────────────────────────────
# installer\finalize_bundle.py fail-closes the build on ANY private
# ntfy-topic carrier (denylist filenames, .workshop/, the live topic
# bytes read from the scout's untracked push_topic.txt - never a
# hardcoded secret, so the guard follows every rotation), then writes
# the SERVED artifact: KrakenScoutBundle.zip + .sha256 + .zip.json
# manifest in one pass (the folder alone was never the leak - the
# pool serves the zip unauthenticated, and it had drifted from the
# folder since Sep 26).
Write-Host "[6/6] topic guard + public artifact (installer\finalize_bundle.py) ..."
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
& $Py (Join-Path $PSScriptRoot "finalize_bundle.py") $Bundle $Dist $Scout $Root
if ($LASTEXITCODE -ne 0) { throw "bundle finalize failed (topic guard hit, or the zip/manifest step)" }

$ScoutFiles = (Get-ChildItem -Recurse -File (Join-Path $Bundle "payload\scout")).Count
$MoneroFiles = (Get-ChildItem -Recurse -File (Join-Path $Bundle "payload\monero_project")).Count
$SizeMB = [math]::Round(((Get-ChildItem -Recurse -File $Bundle | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ("BUNDLE READY: " + $Bundle)
Write-Host ("  scout files: " + $ScoutFiles + " | monero files: " + $MoneroFiles + " | total " + $SizeMB + " MB")
# THE EDGE CAN STILL SERVE THE OLD ZIP: Cloudflare caches it with
# max-age=14400, so a finished build is not a shipped build. This is a
# REMINDER, not a call - the builder must never need a Cloudflare token
# (test_bundle.py runs it), so the purge stays an explicit next step.
Write-Host "  NEXT: python cdn_purge.py   (the edge keeps the old zip for 4h otherwise)"
if ($Open) { Invoke-Item $Bundle }
