@echo off
rem Start the MoodyMate backend (Windows, double-click or cmd).
rem Usage: scripts\start-backend.bat [port]      default port 8000
setlocal EnableExtensions
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"
set "BACKEND=%~dp0..\backend"

if not exist "%BACKEND%\" (
  echo backend\ not found at %BACKEND%
  goto :fail
)

set "PYTHON=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Python virtualenv missing. Create it once with:
  echo   python -m venv backend\.venv
  echo   backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
  goto :fail
)

rem ENV is required (D-13). Use backend\.env if present, otherwise default to local dev.
if not exist "%BACKEND%\.env" if "%ENV%"=="" set "ENV=local"

rem uvicorn --reload leaves a process tree behind on some exits: free the port first.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  if not "%%P"=="0" (
    echo Port %PORT% held by PID %%P - stopping process tree
    taskkill /PID %%P /T /F >nul 2>&1
  )
)

cd /d "%BACKEND%"
echo Applying database migrations...
"%PYTHON%" -m alembic upgrade head || goto :fail

echo Starting backend on http://127.0.0.1:%PORT%  (Ctrl+C to stop)
"%PYTHON%" -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port %PORT%
goto :eof

:fail
echo.
echo Backend did not start.
pause
exit /b 1
