# Bookstore Scraper (HyPass) - OFFLINE Deployment (Windows)
# Air-gapped / no-internet install: no git, no PyPI, no pre-installed Python.
# Usage: extract the whole folder, then run install-offline.bat as Administrator.
# NOTE: ASCII-only on purpose. Chinese comments break parsing on zh-TW
#       Windows PowerShell 5.1 (CP950) when the file has no UTF-8 BOM.

param([switch]$Proxy)
$ErrorActionPreference = "Stop"
trap { Write-Host "`nERROR: $_" -ForegroundColor Red; pause; exit 1 }

$AppDir      = $PSScriptRoot
$ServiceName = "bookstore-scraper"
$LogDir      = Join-Path $AppDir "logs"
$Offline     = Join-Path $AppDir "offline"
$Wheels      = Join-Path $Offline "wheels"
$PyHome      = Join-Path $AppDir "python"            # private bundled Python 3.12
$PyExe       = Join-Path $PyHome "python.exe"
$Venv        = Join-Path $AppDir ".venv"
$VenvPy      = Join-Path $Venv "Scripts\python.exe"

function Info { param($m) Write-Host "  -> $m" }
function Warn { param($m) Write-Host "  !! $m" -ForegroundColor Yellow }

# --- [1/7] Admin + offline assets ---
Write-Host "=== [1/7] Check Administrator + offline assets ==="
$cp = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $cp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run as Administrator (double-click install-offline.bat)."; exit 1
}
Info "OK (Administrator)"
if (-not (Test-Path (Join-Path $Wheels '*.whl')))         { Write-Error "Offline wheels not found: $Wheels"; exit 1 }
if (-not (Test-Path (Join-Path $Offline 'WinSW.NET4.exe'))) { Write-Error "WinSW.NET4.exe not found in $Offline"; exit 1 }
Info ("Offline assets OK (wheels: " + (Get-ChildItem $Wheels -Filter *.whl).Count + ")")

# --- [2/7] Python 3.12 (prefer existing 3.12; else install bundled SYSTEM-WIDE) ---
# Wheels are cp312-specific, so we need exactly 3.12. Prefer a 3.12 already on PATH.
# Otherwise install the bundled Python SYSTEM-WIDE (same approach as the online
# deploy) and reload PATH. We deliberately avoid a private per-user TargetDir:
# a leftover same-version registration from a deleted folder blocks re-install and
# is not on PATH, which breaks detection on the next run.
Write-Host "`n=== [2/7] Python 3.12 ==="
function _Find312 {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        try { $v = (python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null) } catch { $v = "" }
        if ($v -eq "3.12") { return "python" }
    }
    return $null
}
$BasePy = _Find312
if ($BasePy) { Info "Using existing Python 3.12 (in PATH)" }
else {
    $pyInstaller = Join-Path $Offline "python-3.12.10-amd64.exe"
    if (-not (Test-Path $pyInstaller)) { Write-Error "Bundled Python installer missing: $pyInstaller"; exit 1 }
    Info "No Python 3.12 found. Installing bundled Python 3.12.10 (system-wide) ..."
    Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    $BasePy = _Find312
    if (-not $BasePy) { Write-Error "Python 3.12 not available after install. On Server 2012 R2 a missing UCRT (api-ms-win-crt-*.dll) can cause this; install KB2999226 / Windows Update then re-run."; exit 1 }
}
try { $pv = (& $BasePy --version 2>&1) } catch { $pv = "" }
if ($pv -notmatch "Python 3\.12") {
    Warn "Python 3.12 not usable: $pv"
    Warn "Server 2012 R2: usually missing UCRT (api-ms-win-crt-*.dll). Install KB2999226 or run Windows Update, then re-run."
    Write-Error "Python not usable."; exit 1
}
Info "Python: $pv"

# --- [3/7] venv (from the chosen Python 3.12) ---
Write-Host "`n=== [3/7] venv ==="
if (-not (Test-Path $VenvPy)) { & $BasePy -m venv $Venv; Info "Created venv" } else { Info "venv exists" }

# --- [4/7] Dependencies (offline, --no-index) ---
Write-Host "`n=== [4/7] Install dependencies (offline) ==="
Set-Location $AppDir
& $VenvPy -m pip install --no-index --find-links $Wheels -e ".[undetected]"
if ($LASTEXITCODE -ne 0) { Write-Error "Offline pip install FAILED (exit $LASTEXITCODE). Missing wheels? See pip output above."; exit 1 }
Info "Dependencies installed (offline)"

# --- [5/7] WinSW (pre-placed, no download) ---
Write-Host "`n=== [5/7] WinSW ==="
Copy-Item (Join-Path $Offline 'WinSW.NET4.exe') (Join-Path $AppDir "deploy\$ServiceName.exe") -Force
Info "WinSW -> deploy\$ServiceName.exe"

# --- [6/7] settings + logs + service ---
Write-Host "`n=== [6/7] settings + service ==="
$settings = Join-Path $AppDir 'configs\settings.yaml'
if ($Proxy) {
    (Get-Content $settings) -replace '^\s+enabled: false', '  enabled: true' | Set-Content $settings
    Info "Proxy enabled (port 8102)"
} else { Info "Proxy disabled (Fetch API only)" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
try { & $VenvPy -m src.cli service stop      *>$null } catch {}
Start-Sleep 1
try { & $VenvPy -m src.cli service uninstall *>$null } catch {}
Start-Sleep 2
& $VenvPy -m src.cli service install
Start-Sleep 2
& $VenvPy -m src.cli service start
Start-Sleep 3
& $VenvPy -m src.cli service status

# --- [7/7] Verify ---
Write-Host "`n=== [7/7] Verify ==="
try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:8101/" -UseBasicParsing -TimeoutSec 5
    Info "Fetch API: HTTP $($h.StatusCode) (listening)"
} catch {
    if ($_.Exception.Response) { Info "Fetch API: HTTP $([int]$_.Exception.Response.StatusCode) (listening)" }
    else { Warn "Fetch API: NOT responding on 8101 - check $LogDir"; Warn "  $($_.Exception.Message)" }
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "Offline install done." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  App:  $AppDir"
Write-Host "  API:  http://localhost:8101"
Write-Host "  Logs: $LogDir"
Write-Host "  Service: .venv\Scripts\python -m src.cli service status|start|stop"
Write-Host ""
pause
