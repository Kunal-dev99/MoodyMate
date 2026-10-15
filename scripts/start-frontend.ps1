# Start the MoodyMate frontend (Windows / OneDrive-safe).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1 [-Port 3000]
param([int]$Port = 3000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"

if (-not (Test-Path $Frontend)) { Write-Error "frontend/ not found at $Frontend"; exit 1 }

# Clear any orphan dev server still holding the port.
$owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $owners) {
    if ($procId -and $procId -ne 0) {
        Write-Host "Port $Port held by PID $procId - stopping process tree"
        & taskkill /PID $procId /T /F | Out-Null
    }
}

# Wipe .next. OneDrive turns files into cloud placeholders that 'rmdir /q' can choke on.
# Remove-Item -Force follows them silently.
$NextDir = Join-Path $Frontend ".next"
if (Test-Path $NextDir) {
    Remove-Item -LiteralPath $NextDir -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Location $Frontend
npm run dev -- --port $Port
