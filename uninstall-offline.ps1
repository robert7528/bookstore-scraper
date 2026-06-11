# Bookstore Scraper (HyPass) - OFFLINE Uninstall (Windows)
# Clean removal of the offline-installed service + venv. ASCII-only on purpose
# (zh-TW PowerShell 5.1 / CP950 mangles non-BOM UTF-8 comments).
#
# Usage: run uninstall-offline.bat as Administrator (or this .ps1 directly).
#   .\uninstall-offline.ps1                 # remove service + .venv + logs + WinSW
#   .\uninstall-offline.ps1 -RemovePython   # ALSO uninstall the bundled Python 3.12.10
#
# By default Python is LEFT in place: the offline installer reuses an existing 3.12,
# so leaving it is correct and lets a later reinstall run cleanly. Only use
# -RemovePython for a full wipe (other apps may depend on that Python).

param([switch]$RemovePython)
$ErrorActionPreference = "Continue"

$AppDir      = $PSScriptRoot
$ServiceName = "bookstore-scraper"
$VenvPy      = Join-Path $AppDir ".venv\Scripts\python.exe"
$WinSW       = Join-Path $AppDir "deploy\$ServiceName.exe"

function Info { param($m) Write-Host "  -> $m" }
function Warn { param($m) Write-Host "  !! $m" -ForegroundColor Yellow }

$cp = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $cp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run as Administrator (double-click uninstall-offline.bat)."; exit 1
}

# --- [1/3] Stop + uninstall the Windows service ---
Write-Host "=== [1/3] Stop + uninstall service ==="
if (Test-Path $VenvPy) {
    try { & $VenvPy -m src.cli service stop      *>$null } catch {}
    try { & $VenvPy -m src.cli service uninstall *>$null } catch {}
} elseif (Test-Path $WinSW) {
    try { & $WinSW stop      *>$null } catch {}
    try { & $WinSW uninstall *>$null } catch {}
} else {
    Warn "No venv/WinSW found; trying sc.exe"
    cmd /c "sc stop $ServiceName >nul 2>nul"
    cmd /c "sc delete $ServiceName >nul 2>nul"
}
Start-Sleep 2
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Warn "Service '$ServiceName' still present - remove manually: sc.exe delete $ServiceName"
} else { Info "Service removed" }

# --- [2/3] Remove generated files ---
Write-Host "`n=== [2/3] Remove generated files ==="
# release any file locks from this install's python
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$AppDir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
foreach ($p in @((Join-Path $AppDir ".venv"), (Join-Path $AppDir "logs"), $WinSW, (Join-Path $AppDir "python"))) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue; Info "Removed: $p" }
}

# --- [3/3] Python ---
Write-Host "`n=== [3/3] Python ==="
if ($RemovePython) {
    $pyInstaller = Join-Path $AppDir "offline\python-3.12.10-amd64.exe"
    if (Test-Path $pyInstaller) {
        Warn "Uninstalling Python 3.12.10 (other apps may depend on it!) ..."
        # /uninstall properly clears the registration so a later reinstall is not blocked
        Start-Process $pyInstaller -ArgumentList "/uninstall /quiet" -Wait
        Info "Python 3.12.10 uninstalled"
    } else { Warn "Bundled installer not found in offline\; skip Python uninstall" }
} else {
    Info "Python left in place (reused on reinstall). Use -RemovePython to also remove it."
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "Uninstall done." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Service + .venv + logs removed."
Write-Host "  For a full wipe you can now delete the folder: $AppDir"
Write-Host ""
pause
