# build-offline-package.ps1
# Build the Windows OFFLINE install package (zip) for air-gapped deployment.
# Run on a Windows build machine WITH internet + Python/pip available.
#
# Safety: the source tree is exported via `git archive HEAD`, so ONLY git-tracked
#         files are included. Anything gitignored (docs/private, CLAUDE.md, .claude,
#         AI_Development_Report.md, ...) is automatically excluded and never leaks
#         into a public release asset.
#
# Output: <OutDir>\bookstore-scraper-offline.zip  (extract -> run install-offline.bat)
#
# Usage:  .\deploy\build-offline-package.ps1 [-OutDir <dir>] [-PyVer 3.12.10]

param(
    [string]$OutDir = "$env:USERPROFILE\Desktop",
    [string]$PyVer  = "3.12.10",
    [string]$WinSWUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repo  = Split-Path $PSScriptRoot -Parent          # deploy\.. = repo root
$stage = Join-Path $env:TEMP ("bsp-offline-" + [guid]::NewGuid().ToString("N"))
$pkg   = Join-Path $stage "pkg"
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Write-Host "== [1/4] Export git-tracked source (excludes gitignored files) =="
$srctar = Join-Path $stage "src.tar"
git -C $repo archive --format=tar -o $srctar HEAD
tar.exe -xf $srctar -C $pkg
Write-Host "  -> source exported"

Write-Host "== [2/4] Download cp312/win_amd64 wheels (offline deps) =="
$wheels = Join-Path $pkg "offline\wheels"
New-Item -ItemType Directory -Force -Path $wheels | Out-Null
# Build on Windows so win32 markers resolve correctly (uvloop excluded, etc.)
python -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 -d $wheels `
    fastapi "uvicorn[standard]" curl-cffi pyyaml pydantic selectolax click psutil selenium requests setuptools wheel
# undetected-chromedriver is sdist-only -> prebuild a pure-python wheel (py3-none-any)
python -m pip wheel --no-deps undetected-chromedriver -w $wheels
Write-Host ("  -> wheels: " + (Get-ChildItem $wheels -Filter *.whl).Count)

Write-Host "== [3/4] Download Python installer + WinSW =="
$off = Join-Path $pkg "offline"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-amd64.exe" -OutFile (Join-Path $off "python-$PyVer-amd64.exe")
Invoke-WebRequest $WinSWUrl -OutFile (Join-Path $off "WinSW.NET4.exe")
Write-Host "  -> python + winsw downloaded"

Write-Host "== [4/4] Zip =="
$zip = Join-Path $OutDir "bookstore-scraper-offline.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
# tar/libarchive -> forward-slash entries (PS5.1 .NET ZipFile uses backslash, which is broken)
tar.exe -a -c -f $zip -C $pkg .
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("Done: $zip  ({0:N1} MB)" -f ((Get-Item $zip).Length / 1MB))
