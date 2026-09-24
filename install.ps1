# codex-os3 installer for Windows (BETA: not yet tested on a real Windows + rabbit-agent setup)
#   irm https://raw.githubusercontent.com/Nesbesss/codex-os3/main/install.ps1 | iex
# Options (when run as a file): -Uninstall [-Purge]  -NoTray  -NoWait  -Port N
# Env: CODEX_OS3_SRC=<local checkout>, CODEX_OS3_REF=<branch|tag>
param([switch]$Uninstall, [switch]$Purge, [switch]$NoTray, [switch]$NoWait, [int]$Port = 0)
$ErrorActionPreference = "Stop"

$Repo = "Nesbesss/codex-os3"
$Ref = if ($env:CODEX_OS3_REF) { $env:CODEX_OS3_REF } else { "main" }
$HomeDir = if ($env:CODEX_OS3_HOME) { $env:CODEX_OS3_HOME } else { Join-Path $env:USERPROFILE ".codex-os3" }
$AppDir = Join-Path $HomeDir "app"
$TaskName = "codex-os3 router"
$TrayTask = "codex-os3 tray"

function Ok($m) { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "  [x]  $m" -ForegroundColor Red; exit 1 }

if ($Uninstall) {
    foreach ($t in $TaskName, $TrayTask) { schtasks /End /TN $t 2>$null | Out-Null; schtasks /Delete /TN $t /F 2>$null | Out-Null }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*codex_os3*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
    if ($Purge) { Remove-Item -Recurse -Force $HomeDir -ErrorAction SilentlyContinue; Ok "removed all data" }
    Ok "uninstalled"; exit 0
}

Write-Host "codex-os3 installer (Windows, beta)" -ForegroundColor White

# --- python --------------------------------------------------------------------------
$Py = $null
foreach ($c in "python", "python3", "py") {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if (-not $cmd -or $cmd.Source -like "*WindowsApps*") { continue }  # skip the Microsoft Store stub
    $extra = @()
    if ($c -eq "py") { $extra = @("-3") }
    try { $out = @(& $cmd.Source @extra -c "import sys; print(sys.version_info[0] * 100 + sys.version_info[1]); print(sys.executable)" 2>$null) }
    catch { continue }
    if ($out.Count -ge 2 -and [int]$out[0] -ge 309) { $Py = $out[1].Trim(); break }
}
if (-not $Py) { Die "Python 3.9+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run." }
$PyW = Join-Path (Split-Path $Py) "pythonw.exe"
if (-not (Test-Path $PyW)) { $PyW = $Py }
Ok "python: $Py"

# --- codex cli -------------------------------------------------------------------------
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Die "install Node.js (https://nodejs.org) first — the Codex CLI needs it" }
    Write-Host "Installing the Codex CLI"
    npm install -g @openai/codex | Out-Null
}
$Codex = (Get-Command codex -ErrorAction SilentlyContinue).Source
if (-not $Codex) { Die "codex not on PATH after install (open a new terminal and re-run)" }
Ok "codex: $Codex"
& $Codex login status *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Log in to Codex with your ChatGPT account"; & $Codex login; if ($LASTEXITCODE -ne 0) { Die "codex login failed" } }
Ok "codex is logged in"

# --- rabbit-agent ----------------------------------------------------------------------
if (Test-Path (Join-Path $env:USERPROFILE ".rabbit-agent")) { Ok "rabbit-agent found on this machine" }
else { Warn "no rabbit OS3 node on this machine yet — install it from OS3 first; the router must run on the machine you pick as the LLM device" }

# --- code ------------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
$New = Join-Path $HomeDir "app.new"
Remove-Item -Recurse -Force $New -ErrorAction SilentlyContinue
if ($env:CODEX_OS3_SRC) {
    Copy-Item -Recurse $env:CODEX_OS3_SRC $New
} else {
    $Zip = Join-Path $HomeDir "src.zip"
    try { Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$Ref" -OutFile $Zip -UseBasicParsing }
    catch {
        if (Get-Command gh -ErrorAction SilentlyContinue) { gh api "repos/$Repo/zipball/$Ref" > $Zip } else { Die "could not download $Repo@$Ref" }
    }
    $Tmp = Join-Path $HomeDir "src.tmp"; Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    Expand-Archive $Zip $Tmp; Move-Item (Get-ChildItem $Tmp | Select-Object -First 1).FullName $New
    Remove-Item -Recurse -Force $Tmp, $Zip
}
if (-not (Test-Path (Join-Path $New "codex_os3\__init__.py"))) { Die "download looks incomplete" }
$Running = $false
try { Invoke-RestMethod "http://127.0.0.1:$(if ($Port) { $Port } else { 11435 })/health" -TimeoutSec 2 | Out-Null; $Running = $true } catch {}
Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
Move-Item $New $AppDir
Ok "installed to $AppDir"

Push-Location $AppDir
$env:CODEX_OS3_HOME = $HomeDir
if ($Port) { & $Py -c "from codex_os3 import config; config.save({'port': $Port})" }
& $Py -c "from codex_os3 import config; config.save({'codex_bin': r'$Codex'}); config.ensure_key()"
$Port = [int](& $Py -c "from codex_os3 import config; print(config.load()['port'])")

# --- service: a Task Scheduler task at logon, restarted if it stops ---------------------
if ($Running) {
    & $Py -m codex_os3 reload | Out-Null; Ok "upgraded (Windows reload has a ~1 s gap)"
} else {
    $action = New-ScheduledTaskAction -Execute $PyW -Argument "-m codex_os3 serve" -WorkingDirectory $AppDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    [Environment]::SetEnvironmentVariable("CODEX_OS3_HOME", $HomeDir, "User")
    Start-ScheduledTask -TaskName $TaskName
    Ok "scheduled task '$TaskName' (starts at logon, restarts if it stops)"
}
$up = $false
for ($i = 0; $i -lt 30 -and -not $up; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2 | Out-Null; $up = $true } catch { Start-Sleep 1 }
}
if (-not $up) { Die "router did not start — see $HomeDir\service.log" }
Ok "router answering on http://127.0.0.1:$Port"

# --- tray app --------------------------------------------------------------------------
if (-not $NoTray) {
    $tray = Join-Path $AppDir "app\windows\tray.ps1"
    $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tray`""
    Register-ScheduledTask -TaskName $TrayTask -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Force | Out-Null
    Start-ScheduledTask -TaskName $TrayTask
    Ok "tray icon installed (bottom-right, next to the clock)"
}

& $Py -m codex_os3 setup-info
if ($NoWait) { Pop-Location; exit 0 }
Start-Process "http://localhost:$Port/#setup"
Write-Host "Waiting for OS3 to connect… (save the connection in OS3 and send it a message; Ctrl-C to skip)"
& $Py -m codex_os3 wait-for-os3 1800 | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "OS3 is connected — you're done" } else { Warn "no request from OS3 yet; the dashboard shows when it connects" }
Pop-Location
