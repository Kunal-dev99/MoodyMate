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
rem    5. PostgreSQL    - signs in with DATABASE_URL from backend\.env. If nothing runs on
rem                       its port: starts Docker Desktop and the compose "db" service, or
rem                       offers to install Docker Desktop or PostgreSQL 16 with winget
rem                       (and then creates MoodyMate's user and database). If another
rem                       database holds the port: offers to create MoodyMate's user and
rem                       database in it, or to use a free port. If MoodyMate's own
rem                       container has stale credentials: offers to reset it.
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
  "%VPY%" -m pip install --upgrade pip "setuptools>=68" wheel
  if errorlevel 1 goto :pip_failed
  pushd "%BACKEND%"
  rem --no-build-isolation: in pip's isolated build environment the editable build hangs
  rem indefinitely inside this OneDrive folder; building with the venv's setuptools does not.
  "%VPY%" -m pip install --no-build-isolation -e ".[dev]"
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

rem ENV is required (D-13). backend\.env normally sets it; default to local only when
rem neither the window nor .env does, so an edited .env is never overridden.
if not defined ENV findstr /b /c:"ENV=" "%BACKEND%\.env" >nul 2>&1 || set "ENV=local"

rem ---------------------------------------------------------------------------
echo.
echo [5/7] PostgreSQL
rem ---------------------------------------------------------------------------
rem The port comes from DATABASE_URL in backend\.env, read the same way the app reads it.
rem The relative python path avoids for /f quoting trouble with spaces in the repo path.
set "DBPORT="
pushd "%BACKEND%"
for /f "delims=" %%P in ('.venv\Scripts\python.exe -m scripts.db_check --port') do set "DBPORT=%%P"
popd
if not defined DBPORT (
  echo [error] Could not read DATABASE_URL from backend\.env - see the message above.
  goto :fail
)

:db_check
call :db_login
if "%DBCODE%"=="0" goto :migrate
if "%DBCODE%"=="1" goto :db_none
if "%DBCODE%"=="2" goto :db_conflict
if "%DBCODE%"=="3" goto :db_conflict
echo [error] Unexpected database error - see the message above.
goto :fail

:db_none
rem Nothing listens on the port. Docker is preferred: start it if it is installed.
call :docker_ready
if not errorlevel 1 goto :db_start
echo.
echo No database is running on port %DBPORT% and Docker is not ready.
echo Choose how to get PostgreSQL 16 for MoodyMate:
echo   [D] Docker Desktop ^(recommended^). Installs Docker Desktop with winget if it is missing,
echo       starts it and runs MoodyMate's database in a container. Needs admin rights;
echo       Windows may need a restart after the first install.
echo   [P] PostgreSQL 16 for Windows. Installs PostgreSQL 16 with winget on port %DBPORT%,
echo       then creates MoodyMate's user and database in it. Needs admin rights.
echo   [N] Stop.
choice /C DPN /M "Your choice"
if errorlevel 3 goto :db_manual
if errorlevel 2 goto :install_pg
goto :install_docker

:install_docker
where docker >nul 2>&1
if not errorlevel 1 goto :docker_launch
call :need_winget || goto :fail
echo.
echo Installing Docker Desktop - it downloads about 600 MB, so this takes a while ...
winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo winget reported a problem - checking whether Docker Desktop is usable anyway.
rem This window does not see the new PATH yet: add Docker's default install folder.
set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
:docker_launch
call :docker_ready
if errorlevel 1 (
  echo [error] Docker Desktop is not ready. If Windows asked for a restart, restart now.
  echo Then open Docker Desktop once, accept its terms, wait until it says it is running,
  echo and run this script again.
  goto :fail
)
goto :db_start

:install_pg
call :need_winget || goto :fail
set "MM_PGPASS="
pushd "%BACKEND%"
for /f "delims=" %%W in ('.venv\Scripts\python.exe -m scripts.db_check --new-password') do set "MM_PGPASS=%%W"
popd
if not defined MM_PGPASS (
  echo [error] Could not generate a password - see the message above.
  goto :fail
)
echo.
echo Installing PostgreSQL 16 on port %DBPORT% - Windows asks for admin rights; this takes
echo a few minutes ...
winget install -e --id PostgreSQL.PostgreSQL.16 --accept-package-agreements --accept-source-agreements --override "--mode unattended --unattendedmodeui minimal --superpassword %MM_PGPASS% --serverport %DBPORT% --disable-components stackbuilder"
if errorlevel 1 (
  echo [error] winget could not install PostgreSQL 16. If it is already installed, it may be
  echo stopped or on another port: start it from the Services app, or set the port in
  echo DATABASE_URL in backend\.env, then run this script again.
  goto :fail
)
rem Keep the superuser password outside the repository so it can never be committed.
if not exist "%LOCALAPPDATA%\MoodyMate\" mkdir "%LOCALAPPDATA%\MoodyMate"
>"%LOCALAPPDATA%\MoodyMate\postgres-superuser.txt" echo PostgreSQL 16 on port %DBPORT%: superuser postgres, password %MM_PGPASS%
echo.
echo PostgreSQL 16 is installed. Its superuser is "postgres" with password %MM_PGPASS%
echo The password is saved in %LOCALAPPDATA%\MoodyMate\postgres-superuser.txt
echo Waiting for PostgreSQL to start ...
for /l %%N in (1,1,60) do (
  call :db_login quiet
  if not "!DBCODE!"=="1" goto :pg_started
  timeout /t 2 /nobreak >nul
)
echo [error] PostgreSQL 16 did not start within 2 minutes. Check the Services app for
echo "postgresql-x64-16" and start it, then run this script again.
goto :fail
:pg_started
set "PGADMINUSER=postgres"
set "PGADMINPASSWORD=%MM_PGPASS%"
call :db_setup
set "PGADMINPASSWORD="
set "MM_PGPASS="
if not "%SETUPCODE%"=="0" (
  echo [error] Could not create MoodyMate's user and database - see the message above.
  goto :fail
)
goto :db_check

:db_start
rem Start MoodyMate's own database container on the port.
where docker >nul 2>&1
if errorlevel 1 goto :db_none
docker info >nul 2>&1
if errorlevel 1 goto :db_none
echo Starting MoodyMate's database container on port %DBPORT% ^(docker compose up -d db^) ...
set "MOODYMATE_DB_PORT=%DBPORT%"
pushd "%ROOT%"
docker compose up -d db
set "DOCKERR=!errorlevel!"
popd
if not "!DOCKERR!"=="0" (
  echo [error] docker compose could not start the database. Check: docker compose logs db
  goto :fail
)
echo Waiting for PostgreSQL to accept MoodyMate's sign-in ...
for /l %%N in (1,1,30) do (
  call :db_login quiet
  if "!DBCODE!"=="0" goto :db_ready
  if "!DBCODE!"=="2" goto :db_conflict
  if "!DBCODE!"=="3" goto :db_conflict
  timeout /t 2 /nobreak >nul
)
echo [error] PostgreSQL did not become ready within 60 seconds. Check: docker compose logs db
goto :fail
:db_ready
echo PostgreSQL is ready.
goto :migrate

:db_conflict
rem Something answers on the port but refuses MoodyMate's user, password or database.
rem Either another PostgreSQL holds the port, or MoodyMate's container keeps data that
rem was created with other credentials. Find out which before offering a fix.
set "OWNERPID="
set "OWNER="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%DBPORT% .*LISTENING"') do if not defined OWNERPID set "OWNERPID=%%P"
if defined OWNERPID for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %OWNERPID%" /FO CSV /NH') do set "OWNER=%%~N"
if not defined OWNER set "OWNER=an unknown program"
set "OURS="
where docker >nul 2>&1 && docker info >nul 2>&1 && call :ours_running
echo.
if /i "%OWNER%"=="postgres.exe" goto :db_other
if not defined OURS goto :db_other

echo MoodyMate's database container is running, but its stored data was created with a
echo different user, password or database name than backend\.env expects.
echo Resetting deletes MoodyMate's LOCAL development database and creates it again;
echo the migrations then reload the seed data. Nothing outside this container is touched.
choice /C YN /M "Reset MoodyMate's local database"
if errorlevel 2 goto :db_manual
pushd "%ROOT%"
docker compose down -v
popd
goto :db_start

:db_other
echo Port %DBPORT% is used by another database ^(%OWNER%, PID %OWNERPID%^), not MoodyMate's.
echo It does not accept the user and password in backend\.env. Choose a fix:
echo   [C] Create MoodyMate's user and database in that PostgreSQL. You type the password
echo       of its "postgres" superuser ^(chosen when it was installed^). Only the user and
echo       database named in backend\.env are created or updated; nothing else changes.
echo   [F] Run MoodyMate's own database on a free port instead ^(Docker, or a new
echo       PostgreSQL 16^). Only the port in backend\.env changes.
echo   [N] Stop.
choice /C CFN /M "Your choice"
if errorlevel 3 goto :db_manual
if errorlevel 2 goto :db_move
set "PGADMINUSER=postgres"
set "PGADMINPASSWORD="
call :db_setup
if "%SETUPCODE%"=="0" goto :db_check
echo Could not create MoodyMate's user and database - see the message above.
echo.
goto :db_other

:db_move
set "NEWPORT="
pushd "%BACKEND%"
for /f "delims=" %%P in ('.venv\Scripts\python.exe -m scripts.db_check --move-port') do set "NEWPORT=%%P"
popd
if not defined NEWPORT (
  echo [error] Could not update backend\.env - see the message above.
  goto :fail
)
set "DBPORT=%NEWPORT%"
echo backend\.env now points at port %DBPORT%.
goto :db_check

:db_manual
echo To fix it yourself, either:
echo   - set DATABASE_URL in backend\.env to the user, password, port and database of a
echo     PostgreSQL 16 you control, or
echo   - stop the other database on port %DBPORT% and run this script again.
goto :fail

:migrate
rem ---------------------------------------------------------------------------
echo.
echo [6/7] Database migrations
rem ---------------------------------------------------------------------------
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

:db_login
rem Sets DBCODE from scripts.db_check: 0 signed in, 1 nothing listening, 2 sign-in
rem refused, 3 database missing, 4 other. "quiet" hides the one-line diagnosis.
pushd "%BACKEND%"
if "%~1"=="quiet" (
  "%VPY%" -m scripts.db_check >nul 2>&1
) else (
  "%VPY%" -m scripts.db_check
)
set "DBCODE=%errorlevel%"
popd
exit /b 0

:ours_running
rem Sets OURS when MoodyMate's own compose "db" container is running.
pushd "%ROOT%"
for /f %%C in ('docker compose ps --status running -q db 2^>nul') do set "OURS=%%C"
popd
exit /b 0

:db_setup
rem Creates or updates the DATABASE_URL user and database, signed in as PGADMINUSER.
rem Prompts for the superuser password when PGADMINPASSWORD is empty. Sets SETUPCODE.
pushd "%BACKEND%"
"%VPY%" -m scripts.db_check --setup
set "SETUPCODE=%errorlevel%"
popd
exit /b 0

:docker_ready
rem errorlevel 0 when docker answers, starting Docker Desktop first if it is installed.
where docker >nul 2>&1 || exit /b 1
docker info >nul 2>&1 && exit /b 0
set "DD=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DD%" exit /b 1
echo Starting Docker Desktop - the first start can take a few minutes ...
start "" "%DD%"
for /l %%N in (1,1,60) do (
  timeout /t 3 /nobreak >nul
  docker info >nul 2>&1 && exit /b 0
)
exit /b 1

:need_winget
where winget >nul 2>&1 && exit /b 0
echo [error] winget is not available. Install "App Installer" from the Microsoft Store,
echo or install Docker Desktop or PostgreSQL 16 yourself, then run this script again.
exit /b 1

:pip_failed
echo [error] Package installation failed. Check your internet connection and the messages above.
goto :fail

:fail
echo.
echo Backend did not start.
pause
exit /b 1
