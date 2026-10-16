@echo off
rem Start the MoodyMate frontend (Windows, double-click or cmd).
rem Usage: scripts\start-frontend.bat [port]     default port 3000
setlocal EnableExtensions
set "PORT=%~1"
if "%PORT%"=="" set "PORT=3000"
set "FRONTEND=%~dp0..\frontend"

if not exist "%FRONTEND%\" (
  echo frontend\ not found at %FRONTEND%
  goto :fail
)

where npm >nul 2>&1 || (
  echo npm not found. Install Node.js 22 LTS and reopen this window.
  goto :fail
)

rem Free the port if an old dev server still holds it.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  if not "%%P"=="0" (
    echo Port %PORT% held by PID %%P - stopping process tree
    taskkill /PID %%P /T /F >nul 2>&1
  )
)

cd /d "%FRONTEND%"

if not exist "node_modules\" (
  echo Installing frontend dependencies ^(first run^)...
  call npm ci || goto :fail
)

rem Start from a clean build cache (OneDrive placeholders can corrupt .next).
if exist ".next\" rmdir /s /q ".next" 2>nul

if not exist ".env.local" (
  echo Note: frontend\.env.local not found - using the default API at http://localhost:8000
)

echo Starting frontend on http://localhost:%PORT%  (Ctrl+C to stop)
call npm run dev -- --port %PORT%
goto :eof

:fail
echo.
echo Frontend did not start.
pause
exit /b 1
