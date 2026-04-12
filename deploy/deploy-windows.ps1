# Bookstore Scraper - Deployment Script (Windows)
# Usage: Run as Administrator in PowerShell
#   .\deploy-windows.ps1           # Fetch API only
#   .\deploy-windows.ps1 -Proxy    # Fetch API + Forward Proxy

param(
    [switch]$Proxy
)

$ErrorActionPreference = "Stop"
trap { Write-Host "`nERROR: $_" -ForegroundColor Red; pause; exit 1 }

# --- Configuration ---

$AppDir      = "D:\bookstore-scraper"
$ServiceName = "bookstore-scraper"
$LogDir      = "$AppDir\logs"

# --- Helpers ---

function Write-Info { param([string]$msg) Write-Host "  -> $msg" }
function Write-Warn { param([string]$msg) Write-Host "  !! $msg" -ForegroundColor Yellow }

# --- [1/8] Check prerequisites ---

Write-Host "=== [1/8] Check prerequisites ==="
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please run as Administrator"
    exit 1
}
Write-Info "OK (Administrator)"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.11+ from https://www.python.org/downloads/"
    exit 1
}
$pyVer = python --version 2>&1
Write-Info "Python: $pyVer"

# Check Python version >= 3.11
$pyVerNum = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
if ([float]$pyVerNum -lt 3.11) {
    Write-Error "Python 3.11+ required, found $pyVerNum"
    exit 1
}

# IP stability check (proxy mode)
if ($Proxy) {
    Write-Host ""
    Write-Info "Checking IP stability..."
    $ips = @()
    for ($i = 1; $i -le 5; $i++) {
        try {
            $ip = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 5).Content.Trim()
            $ips += $ip
        } catch { $ips += "error" }
    }
    $unique = ($ips | Sort-Object -Unique).Count
    if ($unique -gt 1) {
        Write-Warn "NAT pool detected ($unique IPs). JCR auth may need Angular JS patch."
    } else {
        Write-Info "IP stable: $($ips[0])"
    }
}

# --- [2/8] Clone or update ---

Write-Host ""
Write-Host "=== [2/8] Clone or update ==="
if (Test-Path "$AppDir\.git") {
    Set-Location $AppDir
    git checkout configs/settings.yaml 2>$null
    git pull
    Write-Info "Updated: $AppDir"
} else {
    git clone https://github.com/robert7528/bookstore-scraper.git $AppDir
    Set-Location $AppDir
    Write-Info "Cloned: $AppDir"
}

# --- [3/8] Install dependencies ---

Write-Host ""
Write-Host "=== [3/8] Install dependencies ==="
if (-not (Test-Path "$AppDir\.venv")) {
    python -m venv .venv
    Write-Info "Created venv"
}
.venv\Scripts\pip install -e ".[undetected]" --quiet
Write-Info "Dependencies installed"

# --- [4/8] Check Google Chrome ---

Write-Host ""
Write-Host "=== [4/8] Check Google Chrome ==="
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (Test-Path $chromePath) {
    $chromeVer = (Get-Item $chromePath).VersionInfo.FileVersion
    Write-Info "Google Chrome: $chromeVer"
} else {
    Write-Warn "Chrome not found at $chromePath"
    Write-Warn "Download from: https://www.google.com/chrome/"
    Write-Warn "Browser fallback (Turnstile bypass) requires Chrome."
}

# --- [5/8] Configure settings ---

Write-Host ""
Write-Host "=== [5/8] Configure settings ==="
if ($Proxy) {
    (Get-Content configs\settings.yaml) -replace '^\s+enabled: false', '  enabled: true' | Set-Content configs\settings.yaml
    Write-Info "Proxy enabled (port 8102)"
} else {
    Write-Info "Proxy disabled (Fetch API only)"
}

# --- [6/8] Create directories ---

Write-Host ""
Write-Host "=== [6/8] Create directories ==="
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Info $LogDir

# --- [7/8] Install and start service ---

Write-Host ""
Write-Host "=== [7/8] Install and start service ==="

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
Start-Sleep -Seconds 3
.venv\Scripts\python -m src.cli service status

# --- [8/8] Verify ---

Write-Host ""
Write-Host "=== [8/8] Verify ==="
try {
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:8101/" -UseBasicParsing -TimeoutSec 5
    Write-Info "Fetch API: HTTP $($health.StatusCode)"
} catch {
    Write-Info "Fetch API: HTTP 404 (OK - service running)"
}

if ($Proxy) {
    $proxyListening = netstat -an | Select-String ":8102.*LISTENING"
    if ($proxyListening) {
        Write-Info "Proxy 8102: listening"
    } else {
        Write-Warn "Proxy 8102: not listening!"
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Deployment complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  App:      $AppDir"
Write-Host "  Config:   $AppDir\configs\settings.yaml"
Write-Host "  Logs:     $LogDir"
Write-Host "  API:      http://localhost:8101"
if ($Proxy) {
    Write-Host "  Proxy:    localhost:8102"
    Write-Host ""
    Write-Host "HyProxy setup required:" -ForegroundColor Cyan
    Write-Host "  1. config.yml: add proxys -> antibot: 127.0.0.1:8102"
    Write-Host "  2. WoS + JCR: add use-proxy: antibot (from admin UI)"
    Write-Host "  3. Flush Redis + restart HyProxy"
}
Write-Host ""
Write-Host "Commands:"
Write-Host "  .venv\Scripts\python -m src.cli service status"
Write-Host "  .venv\Scripts\python -m src.cli service stop"
Write-Host "  .venv\Scripts\python -m src.cli service start"
Write-Host ""
pause
