@echo off
rem ===========================================================================
rem  MoodyMate backend: install everything that is missing, then start it.
rem
rem  Usage:  scripts\start-backend.bat [port]          (default port 8000)
rem          Double-clicking works too.
rem
rem  Steps (each is skipped when already done):
rem    1. Python 3.12   - offers to install it with winget if it is missing
rem    2. backend\.venv - creates the virtual environment
rem    3. Packages      - pip install -e ".[dev]"; re-runs when pyproject.toml changes
rem    4. backend\.env  - copied from .env.example on first run (ENV=local, dev sign-in)
rem    5. PostgreSQL    - starts the docker compose "db" service if nothing listens
rem                       on localhost:5432, then waits until it accepts connections
rem    6. Migrations    - alembic upgrade head (tables and seed data)
rem    7. Starts uvicorn with auto-reload on http://127.0.0.1:<port>
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "BACKEND=%ROOT%\backend"
set "VENV=%BACKEND%\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "MARKER=%VENV%\.installed-pyproject.sha256"

echo.
echo === MoodyMate backend setup ===
echo Repository: %ROOT%
if not exist "%BACKEND%\pyproject.toml" (
  echo [error] backend\pyproject.toml not found. Run this script from the repository's scripts folder.
  goto :fail
)

rem ---------------------------------------------------------------------------
echo.
echo [1/7] Python 3.12
rem ---------------------------------------------------------------------------
set "PY="
call :find_python
if not defined PY (
  echo Python 3.12 was not found. The backend requires Python 3.12 exactly.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo winget is not available. Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH", then run this script again.
    goto :fail
  )
  choice /C YN /M "Install Python 3.12 now with winget"
  if errorlevel 2 goto :fail
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [error] winget could not install Python 3.12.
    goto :fail
  )
  call :find_python
  if not defined PY (
    echo Python 3.12 is installed but this window cannot see it yet.
    echo Close this window and run the script again.
    goto :fail
  )
)
echo Using: !PY!

rem ---------------------------------------------------------------------------
echo.
echo [2/7] Virtual environment (backend\.venv)
rem ---------------------------------------------------------------------------
if exist "%VPY%" (
  "%VPY%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo The existing .venv is not Python 3.12 - recreating it.
    rmdir /s /q "%VENV%"
  )
)
if not exist "%VPY%" (
  echo Creating backend\.venv ...
  !PY! -m venv "%VENV%"
  if errorlevel 1 (
    echo [error] Could not create the virtual environment.
    goto :fail
  )
) else (
  echo Already present.
)

rem ---------------------------------------------------------------------------
echo.
echo [3/7] Python packages
rem ---------------------------------------------------------------------------
set "WANT="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%BACKEND%\pyproject.toml" SHA256 ^| findstr /v /c:"CertUtil"') do if not defined WANT set "WANT=%%H"
rem No hash (certutil missing) means always install rather than wrongly skip.
if not defined WANT set "WANT=unknown-!RANDOM!"
set "HAVE="
if exist "%MARKER%" set /p HAVE=<"%MARKER%"
if /i "!WANT!"=="!HAVE!" (
  echo Up to date with pyproject.toml.
) else (
  echo Installing backend packages - the first run takes a few minutes ...
  "%VPY%" -m pip install --upgrade pip
  if errorlevel 1 goto :pip_failed
  pushd "%BACKEND%"
  "%VPY%" -m pip install -e ".[dev]"
  set "PIPERR=!errorlevel!"
  popd
  if not "!PIPERR!"=="0" goto :pip_failed
  >"%MARKER%" echo !WANT!
  echo Packages installed.
)

rem ---------------------------------------------------------------------------
echo.
echo [4/7] Settings file (backend\.env)
rem ---------------------------------------------------------------------------
if exist "%BACKEND%\.env" (
  echo Using the existing backend\.env
) else (
  copy /y "%BACKEND%\.env.example" "%BACKEND%\.env" >nul
  echo Created backend\.env from .env.example: ENV=local, dev sign-in, local database.
)

rem ---------------------------------------------------------------------------
echo.
echo [5/7] PostgreSQL on localhost:5432
rem ---------------------------------------------------------------------------
call :db_up
if not errorlevel 1 (
  echo PostgreSQL is accepting connections.
  goto :migrate
)
where docker >nul 2>&1
if errorlevel 1 (
  echo [error] Nothing is listening on localhost:5432 and Docker is not installed.
  echo Either install Docker Desktop ^(https://www.docker.com/products/docker-desktop/^)
  echo or run PostgreSQL 16 yourself with user/password moodymate and database moodymate_dev.
  goto :fail
)
docker info >nul 2>&1
if errorlevel 1 (
  echo [error] Docker is installed but not running. Start Docker Desktop, wait until it is ready,
  echo then run this script again.
  goto :fail
)
echo Starting the database container ^(docker compose up -d db^) ...
pushd "%ROOT%"
docker compose up -d db
set "DOCKERR=!errorlevel!"
popd
if not "!DOCKERR!"=="0" (
  echo [error] docker compose could not start the database.
  goto :fail
)
echo Waiting for PostgreSQL to accept connections ...
for /l %%N in (1,1,30) do (
  call :db_up
  if not errorlevel 1 goto :db_ready
  timeout /t 2 /nobreak >nul
)
echo [error] PostgreSQL did not become ready within 60 seconds. Check: docker compose logs db
goto :fail
:db_ready
echo PostgreSQL is ready.

:migrate
rem ---------------------------------------------------------------------------
echo.
echo [6/7] Database migrations
rem ---------------------------------------------------------------------------
rem ENV is required (D-13). backend\.env normally sets it; default to local only when
rem neither the window nor .env does, so an edited .env is never overridden.
if not defined ENV findstr /b /c:"ENV=" "%BACKEND%\.env" >nul 2>&1 || set "ENV=local"
pushd "%BACKEND%"
"%VPY%" -m alembic upgrade head
set "MIGERR=!errorlevel!"
popd
if not "!MIGERR!"=="0" (
  echo [error] Migrations failed. Check DATABASE_URL in backend\.env and that PostgreSQL is reachable.
  goto :fail
)

rem ---------------------------------------------------------------------------
echo.
echo [7/7] Starting the backend
rem ---------------------------------------------------------------------------
rem uvicorn --reload can leave a process behind: free the port first.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  if not "%%P"=="0" (
    echo Port %PORT% held by PID %%P - stopping it.
    taskkill /PID %%P /T /F >nul 2>&1
  )
)
echo.
echo   API:      http://127.0.0.1:%PORT%/api/v1/health
echo   API docs: http://127.0.0.1:%PORT%/docs
echo   Stop with Ctrl+C.
echo.
cd /d "%BACKEND%"
"%VPY%" -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port %PORT%
exit /b %errorlevel%

rem ===========================================================================
:find_python
rem Sets PY to a command that runs Python 3.12, or leaves it empty.
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && (set "PY=py -3.12" & exit /b 0)
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1 && (set "PY=python" & exit /b 0)
set "CAND=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%CAND%" (set "PY="%CAND%"" & exit /b 0)
set "CAND=%ProgramFiles%\Python312\python.exe"
if exist "%CAND%" (set "PY="%CAND%"" & exit /b 0)
exit /b 1

:db_up
rem errorlevel 0 when something accepts TCP connections on localhost:5432.
"%VPY%" -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(('127.0.0.1',5432)))" >nul 2>&1
exit /b %errorlevel%

:pip_failed
echo [error] Package installation failed. Check your internet connection and the messages above.
goto :fail

:fail
echo.
echo Backend did not start.
pause
exit /b 1
