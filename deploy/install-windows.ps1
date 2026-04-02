# Windows Service 安裝腳本 (使用 NSSM)
# 需先安裝 NSSM: https://nssm.cc/ 或 choco install nssm

$AppDir = "D:\bookstore-scraper"
$ServiceName = "BookstoreScraper"
$Python = "$AppDir\.venv\Scripts\python.exe"

Write-Host "=== Installing bookstore-scraper as Windows Service ==="

# Create venv and install
Set-Location $AppDir
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
.venv\Scripts\pip install -e .

# Check NSSM
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: NSSM not found. Install it first:"
    Write-Host "  choco install nssm"
    Write-Host "  or download from https://nssm.cc/"
    exit 1
}

# Remove existing service if any
nssm stop $ServiceName 2>$null
nssm remove $ServiceName confirm 2>$null

# Install service
nssm install $ServiceName $Python "-m" "src.cli" "serve"
nssm set $ServiceName AppDirectory $AppDir
nssm set $ServiceName DisplayName "Bookstore Scraper API"
nssm set $ServiceName Description "HyFSE Python Driver - TLS fingerprint proxy"
nssm set $ServiceName AppStdout "$AppDir\logs\service.log"
nssm set $ServiceName AppStderr "$AppDir\logs\error.log"
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateBytes 10485760

# Create log directory
New-Item -ItemType Directory -Force -Path "$AppDir\logs" | Out-Null

# Start
nssm start $ServiceName

Write-Host "=== Done ==="
Write-Host "Status:  nssm status $ServiceName"
Write-Host "Logs:    Get-Content $AppDir\logs\service.log -Tail 50"
Write-Host "Stop:    nssm stop $ServiceName"
Write-Host "Remove:  nssm remove $ServiceName confirm"
