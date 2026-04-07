# Bookstore Scraper - Deployment Script (Windows)
# Usage: Run as Administrator in PowerShell
#   .\deploy-windows.ps1

$ErrorActionPreference = "Stop"
trap { Write-Host "`nERROR: $_" -ForegroundColor Red; pause; exit 1 }

# --- Configuration ---

$AppDir      = "D:\bookstore-scraper"
$ServiceName = "bookstore-scraper"
$LogDir      = "$AppDir\logs"

# --- Helpers ---

function Write-Info { param([string]$msg) Write-Host "  -> $msg" }

# --- [1/5] Check prerequisites ---

Write-Host "=== [1/5] Check prerequisites ==="
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please run as Administrator"
    exit 1
}
Write-Info "OK (Administrator)"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.11+ first."
    exit 1
}
$pyVer = python --version 2>&1
Write-Info "Python: $pyVer"

# --- [2/5] Clone or update ---

Write-Host ""
Write-Host "=== [2/5] Clone or update ==="
if (Test-Path "$AppDir\.git") {
    Set-Location $AppDir
    git pull
    Write-Info "Updated: $AppDir"
} else {
    git clone https://github.com/robert7528/bookstore-scraper.git $AppDir
    Set-Location $AppDir
    Write-Info "Cloned: $AppDir"
}

# --- [3/6] Install dependencies ---

Write-Host ""
Write-Host "=== [3/6] Install dependencies ==="
if (-not (Test-Path "$AppDir\.venv")) {
    python -m venv .venv
    Write-Info "Created venv"
}
.venv\Scripts\pip install -e ".[undetected]" --quiet
Write-Info "Dependencies installed"

# --- [4/6] Check Google Chrome ---

Write-Host ""
Write-Host "=== [4/6] Check Google Chrome ==="
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (Test-Path $chromePath) {
    $chromeVer = (Get-Item $chromePath).VersionInfo.FileVersion
    Write-Info "Google Chrome: $chromeVer"
} else {
    Write-Host "  WARNING: Google Chrome not found at $chromePath" -ForegroundColor Yellow
    Write-Host "  Download from: https://www.google.com/chrome/" -ForegroundColor Yellow
    Write-Host "  Browser fallback (Turnstile bypass) requires Chrome." -ForegroundColor Yellow
}

# --- [5/6] Create directories ---

Write-Host ""
Write-Host "=== [5/6] Create directories ==="
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Info $LogDir

# --- [6/6] Install and start service ---

Write-Host ""
Write-Host "=== [6/6] Install and start service ==="

# Stop existing service if running
try { .venv\Scripts\python -m src.cli service stop *>$null } catch {}
Start-Sleep -Seconds 1

# Uninstall if exists
try { .venv\Scripts\python -m src.cli service uninstall *>$null } catch {}
Start-Sleep -Seconds 2

# Install and start
.venv\Scripts\python -m src.cli service install
Start-Sleep -Seconds 2
.venv\Scripts\python -m src.cli service start
Start-Sleep -Seconds 2
.venv\Scripts\python -m src.cli service status

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  App:      $AppDir"
Write-Host "  Config:   $AppDir\configs\settings.yaml"
Write-Host "  Logs:     $LogDir"
Write-Host "  API:      http://localhost:8101"
Write-Host "  Docs:     http://localhost:8101/docs"
Write-Host ""
Write-Host "Commands:"
Write-Host "  .venv\Scripts\python -m src.cli service status"
Write-Host "  .venv\Scripts\python -m src.cli service stop"
Write-Host "  .venv\Scripts\python -m src.cli service start"
Write-Host ""
pause
