' Kraken Scout - windowless start for the Kraken desktop shortcut (Sep 21 rebuild).
'
' WHY THIS EXISTS: the desktop "Kraken" shortcut points HERE (launch_kraken_hidden.vbs),
' not at Launch Kraken.bat - wscript is a GUI-subsystem host, so running the
' launcher through this file never flashes a launcher console window. The
' launcher (launch_stack.ps1) still starts every missing component in its
' SAVED mode: visible, one titled "Kraken - <component>" window per piece,
' output tee'd into logs\<stem>_console.log (drop a visible_stack.off marker
' to run the fleet all-hidden).
'
' HISTORY: the original (Sep 15) was lost in the Sep 20 workspace deletion
' event; rebuilt Sep 21 from the launcher contract it serves. The bat
' (Launch Kraken.bat) remains for a console-bearing launch and does the
' venv sanity check first; this vbs goes straight to the launcher.
'
' FALSE-DOUBLES NOTE (Sep 21): if a double-click ever appears to "open two
' consoles", it is the launcher MUTEX talking: a second click while the
' first launcher run is still verifying shows "[SKIP] ALREADY RUNNING" and
' exits; the components themselves are single-instance (pid locks + spawn
' mutexes in start_detached.ps1).
'
' Sep 23: the two pinned click-path behaviors are back (the battery's
' lifecycle contract holds the spec): the venv self-check runs hidden
' BEFORE the launcher, and the launcher's checklist is transcribed to
' logs\launch_stack.log — the click keeps its paper trail.

Dim sh: Set sh = CreateObject("WScript.Shell")
Dim fso: Set fso = CreateObject("Scripting.FileSystemObject")

Const SCOUT = "C:\Users\birds\Workspace\kraken_scout"

' venv self-check, hidden (same contract as Launch Kraken.bat)
Dim py: py = SCOUT & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then
    sh.Run "cmd /c cd /d """ & SCOUT & """ && uv sync", 0, True
Else
    If sh.Run("cmd /c """ & py & """ --version >nul 2>&1", 0, True) <> 0 Then
        ' interpreter broken: rebuild then
        sh.Run "cmd /c cd /d """ & SCOUT & """ && rmdir /s /q .venv & uv sync", 0, True
    End If
End If

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\birds\Workspace\kraken_scout"
' the launcher, hidden, its checklist transcribed to the log file
' (cmd-level redirection: Write-Host bypasses the PowerShell pipeline,
' so a Tee-Object wrapper would see nothing).
sh.Run "cmd /c cd /d """ & SCOUT & """ && powershell -NoProfile -ExecutionPolicy Bypass -File launch_stack.ps1 > logs\launch_stack.log 2>&1", 0, False
