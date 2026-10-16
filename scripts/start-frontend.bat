@echo off
rem ===========================================================================
rem  MoodyMate frontend: install everything that is missing, then start it.
rem
rem  Usage:  scripts\start-frontend.bat [port]         (default port 3000)
rem          Double-clicking works too.
rem
rem  Steps (each is skipped when already done):
rem    1. Node.js 20+      - offers to install Node.js LTS with winget if missing
rem                          or too old (CI uses Node 22)
rem    2. Packages         - npm ci; re-runs when package-lock.json changes
rem    3. frontend\.env.local - copied from .env.example on first run
rem                          (points the app at http://localhost:8000)
rem    4. Backend check    - warns if the API is not reachable yet
rem                          (start scripts\start-backend.bat in another window)
rem    5. Starts the Next.js dev server on http://localhost:<port>
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
set "PORT=%~1"
if "%PORT%"=="" set "PORT=3000"

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "FRONTEND=%ROOT%\frontend"
set "MARKER=%FRONTEND%\node_modules\.installed-package-lock.sha256"

echo.
echo === MoodyMate frontend setup ===
echo Repository: %ROOT%
if not exist "%FRONTEND%\package.json" (
  echo [error] frontend\package.json not found. Run this script from the repository's scripts folder.
  goto :fail
)

rem ---------------------------------------------------------------------------
echo.
echo [1/5] Node.js 20 or newer
rem ---------------------------------------------------------------------------
call :node_ok
if errorlevel 1 (
  echo Node.js 20 or newer was not found.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo winget is not available. Install Node.js 22 LTS from https://nodejs.org/
    echo then run this script again.
    goto :fail
  )
  choice /C YN /M "Install Node.js LTS now with winget"
  if errorlevel 2 goto :fail
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [error] winget could not install Node.js.
    goto :fail
  )
  rem This window does not see the new PATH yet: add the default install folder.
  set "PATH=%ProgramFiles%\nodejs;%PATH%"
  call :node_ok
  if errorlevel 1 (
    echo Node.js is installed but this window cannot see it yet.
    echo Close this window and run the script again.
    goto :fail
  )
)
for /f "delims=" %%V in ('node --version') do echo Using Node.js %%V

rem ---------------------------------------------------------------------------
echo.
echo [2/5] npm packages
rem ---------------------------------------------------------------------------
set "WANT="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%FRONTEND%\package-lock.json" SHA256 ^| findstr /v /c:"CertUtil"') do if not defined WANT set "WANT=%%H"
rem No hash (certutil missing) means always install rather than wrongly skip.
if not defined WANT set "WANT=unknown-!RANDOM!"
set "HAVE="
if exist "%MARKER%" set /p HAVE=<"%MARKER%"
if exist "%FRONTEND%\node_modules\" if /i "!WANT!"=="!HAVE!" (
  echo Up to date with package-lock.json.
  goto :env
)
echo Installing frontend packages ^(npm ci^) - the first run takes a few minutes ...
pushd "%FRONTEND%"
call npm ci
set "NPMERR=!errorlevel!"
popd
if not "!NPMERR!"=="0" (
  echo [error] npm ci failed. Close any editor or dev server using frontend\node_modules,
  echo check your internet connection, and run this script again.
  goto :fail
)
>"%MARKER%" echo !WANT!
echo Packages installed.

:env
rem ---------------------------------------------------------------------------
echo.
echo [3/5] Settings file (frontend\.env.local)
rem ---------------------------------------------------------------------------
if exist "%FRONTEND%\.env.local" (
  echo Using the existing frontend\.env.local
) else (
  copy /y "%FRONTEND%\.env.example" "%FRONTEND%\.env.local" >nul
  echo Created frontend\.env.local from .env.example: API at http://localhost:8000
)

rem ---------------------------------------------------------------------------
echo.
echo [4/5] Backend
rem ---------------------------------------------------------------------------
node -e "require('http').get('http://localhost:8000/api/v1/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))" >nul 2>&1
if errorlevel 1 (
  echo The backend is not answering on http://localhost:8000 yet.
  echo Start it in another window with scripts\start-backend.bat - the sign-in page needs it.
) else (
  echo Backend is up.
)

rem ---------------------------------------------------------------------------
echo.
echo [5/5] Starting the frontend
rem ---------------------------------------------------------------------------
rem Free the port if an old dev server still holds it.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  if not "%%P"=="0" (
    echo Port %PORT% held by PID %%P - stopping it.
    taskkill /PID %%P /T /F >nul 2>&1
  )
)
rem Start from a clean build cache: OneDrive placeholders can corrupt .next.
if exist "%FRONTEND%\.next\" rmdir /s /q "%FRONTEND%\.next" 2>nul
echo.
echo   App:  http://localhost:%PORT%   (sign in with a dev persona)
echo   Stop with Ctrl+C.
echo.
cd /d "%FRONTEND%"
call npm run dev -- --port %PORT%
exit /b %errorlevel%

rem ===========================================================================
:node_ok
rem errorlevel 0 when node is on PATH and its major version is 20 or newer.
where node >nul 2>&1 || exit /b 1
node -e "process.exit(+process.versions.node.split('.')[0] >= 20 ? 0 : 1)" >nul 2>&1
exit /b %errorlevel%

:fail
echo.
echo Frontend did not start.
pause
exit /b 1
