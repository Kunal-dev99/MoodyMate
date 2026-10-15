# Start the MoodyMate backend (Windows / OneDrive-safe).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1 [-Port 8000]
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"

if (-not (Test-Path $Backend)) { Write-Error "backend/ not found at $Backend"; exit 1 }

# uvicorn --reload spawns a process tree that doesn't always die on Ctrl+C.
# Clear any orphan still holding the port before starting.
$owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $owners) {
    if ($procId -and $procId -ne 0) {
        Write-Host "Port $Port held by PID $procId - stopping process tree"
        & taskkill /PID $procId /T /F | Out-Null
    }
}

Set-Location $Backend
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { Write-Error "venv missing: create it with 'python -m venv backend\.venv'"; exit 1 }

& $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $Port
