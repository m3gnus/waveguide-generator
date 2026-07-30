@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem This script may be executed from a copy outside the repository. cmd.exe
rem tracks its position in a batch file by byte offset and re-reads the file as
rem it goes, so a script that git-pulls over itself resumes at a garbage offset
rem and executes fragments of unrelated lines. install-and-update.bat therefore
rem copies this file to a temporary location and passes the repository root in
rem --root. Never assume %~dp0 is inside the repository.
set "WG_ROOT="
set "INSTALL_AFTER_PULL="
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--after-pull" (
    set "INSTALL_AFTER_PULL=1"
    shift
    goto parse_args
)
if /I "%~1"=="--root" (
    set "WG_ROOT=%~2"
    shift
    shift
    goto parse_args
)
shift
goto parse_args
:args_done

if defined WG_ROOT (
    cd /d "%WG_ROOT%"
) else (
    cd /d "%~dp0\.."
)
if errorlevel 1 (
    echo ERROR: Could not enter the project folder.
    exit /b 1
)

set "NODEJS_HINT=C:\Program Files\nodejs"

echo ===============================================================
echo WG - Waveguide Generator Install / Update
echo ===============================================================
echo.

echo Verifying project folder...
set "ROOT_INVALID="
for %%f in (package.json package-lock.json install\install.bat server\requirements.txt server\requirements-gmsh.txt server\requirements-bempp.txt launch\windows.bat) do (
    if not exist "%%f" (
        echo   - Missing: %%f
        set "ROOT_INVALID=1"
    )
)
if defined ROOT_INVALID (
    echo.
    echo ERROR: This does not look like the full Waveguide Generator project folder.
    echo Current folder: %CD%
    echo.
    echo Fix steps:
    echo   1. Download the full project ZIP from GitHub.
    echo   2. Extract the ZIP completely.
    echo   3. Open the extracted folder ^(usually waveguide-generator-main^).
    echo   4. Run install\install.bat again.
    echo.
    echo GitHub: https://github.com/m3gnus/waveguide-generator
    exit /b 1
)
echo   Project folder looks good.
echo.

if defined INSTALL_AFTER_PULL (
    echo Code update already applied; continuing with the updated installer.
    echo.
) else (
    call :update_from_git
    if errorlevel 1 exit /b 1
    if defined CODE_UPDATED (
        rem The pull just rewrote the files on disk, possibly including this
        rem script. Do not call the updated installer from here: this process is
        rem still reading the old byte offsets. Hand control back to
        rem install-and-update.bat, which re-copies and re-launches.
        echo   Code updated. Relaunching with the updated installer...
        echo.
        exit /b 10
    )
)

call :ensure_git_for_dependencies
if errorlevel 1 exit /b 1

call :ensure_node
if errorlevel 1 exit /b 1

echo Installing frontend dependencies...
call npm.cmd ci
if errorlevel 1 (
    echo ERROR: npm ci failed.
    exit /b 1
)
echo   Frontend dependencies installed.
echo.

echo Checking Python 3...
set "WG_SETUP_PYTHON="
set "PYTHON_VERSION="
set "PYTHON_PATH="
set "FIRST_PYTHON_CMD="
set "FIRST_PYTHON_VERSION="
set "FIRST_PYTHON_PATH="

rem Consider EVERY match from `where`, not just the first. On Windows the first
rem hit is frequently the Microsoft Store execution alias in
rem %LOCALAPPDATA%\Microsoft\WindowsApps: a zero-byte stub that opens the Store
rem instead of running Python. Taking it produces either a Store popup or a
rem baffling spawn failure much later, so skip aliases and keep looking.
set "STORE_ALIAS_SEEN="

for %%p in (py python3 python) do (
    if not defined WG_SETUP_PYTHON (
        for /f "delims=" %%w in ('where %%p 2^>nul') do (
            if not defined WG_SETUP_PYTHON (
                set "IS_STORE_ALIAS="
                echo %%w| find /i "\WindowsApps\" >nul 2>&1
                if not errorlevel 1 set "IS_STORE_ALIAS=1"

                if defined IS_STORE_ALIAS (
                    set "STORE_ALIAS_SEEN=1"
                ) else (
                    set "CANDIDATE_VERSION="
                    for /f "delims=" %%v in ('"%%w" -c "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))" 2^>nul') do (
                        if not defined CANDIDATE_VERSION set "CANDIDATE_VERSION=%%v"
                    )

                    if not defined FIRST_PYTHON_CMD (
                        set "FIRST_PYTHON_CMD=%%p"
                        set "FIRST_PYTHON_PATH=%%w"
                        set "FIRST_PYTHON_VERSION=!CANDIDATE_VERSION!"
                    )

                    "%%w" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
                    if not errorlevel 1 (
                        set "WG_SETUP_PYTHON=%%w"
                        set "PYTHON_PATH=%%w"
                        set "PYTHON_VERSION=!CANDIDATE_VERSION!"
                    )
                )
            )
        )
    )
)

if not defined WG_SETUP_PYTHON (
    echo ERROR: Python 3.10 through 3.14 is required.
    if defined FIRST_PYTHON_CMD (
        echo        Detected command: !FIRST_PYTHON_CMD!
        if defined FIRST_PYTHON_PATH echo        Detected path: !FIRST_PYTHON_PATH!
        if defined FIRST_PYTHON_VERSION (
            echo        Detected version: !FIRST_PYTHON_VERSION!
            echo        This version is outside the supported range.
        )
    ) else (
        echo        No usable Python was detected in PATH.
    )
    if defined STORE_ALIAS_SEEN (
        echo.
        echo        A Microsoft Store "App execution alias" for Python was found in
        echo        %%LOCALAPPDATA%%\Microsoft\WindowsApps and skipped. That entry is a
        echo        stub that opens the Store; it is not a Python installation.
        echo        Turn it off in: Settings ^> Apps ^> Advanced app settings ^>
        echo        App execution aliases ^> switch off "python.exe" and "python3.exe".
    )
    echo.
    echo Recommended checks:
    echo   1. Open Command Prompt and run: py -0p
    echo   2. If Python is missing, install from https://www.python.org/downloads/windows/
    echo      and tick "Add python.exe to PATH"
    exit /b 1
)
if defined STORE_ALIAS_SEEN (
    echo   Note: skipped a Microsoft Store Python execution alias in WindowsApps.
)

echo   Python interpreter: %WG_SETUP_PYTHON%
if defined PYTHON_VERSION echo   Python version: %PYTHON_VERSION%
if defined PYTHON_PATH echo   Python path: %PYTHON_PATH%
echo.

echo Creating Python virtual environment (.venv)...
rem The validity probe MUST NOT be inlined into a parenthesised block. Its
rem Python expression contains parentheses inside a quoted argument, which
rem cmd.exe's block parser mishandles: the probe reported failure even when the
rem identical command exited 0 at the prompt. The installer then discarded a
rem perfectly good .venv on every run, rebuilt it from scratch (~3 minutes), and
rem left a numbered backup directory behind each time. Keep it in a subroutine.
if exist ".venv\" (
    call :validate_venv
    if errorlevel 1 (
        call :preserve_incompatible_venv
        if errorlevel 1 exit /b 1
    ) else (
        echo   Existing .venv is valid; reusing it.
    )
)
if not exist ".venv\" (
    "%WG_SETUP_PYTHON%" -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create .venv using "%WG_SETUP_PYTHON%".
        exit /b 1
    )
    echo   Created.
)

echo Installing backend dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r server\requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install core backend dependencies.
    exit /b 1
)
echo   Core backend requirements installed.
echo.

echo Installing gmsh Python package ^(required for /api/mesh/build^)...
.venv\Scripts\python.exe -m pip install --quiet -r server\requirements-gmsh.txt
if errorlevel 1 (
    echo   Default gmsh install failed. Retrying with gmsh.info snapshot index...
    .venv\Scripts\python.exe -m pip install --quiet --pre --force-reinstall --no-cache-dir --extra-index-url https://gmsh.info/python-packages-dev -r server\requirements-gmsh.txt
    if errorlevel 1 (
        echo ERROR: Could not install gmsh Python package automatically.
        echo Try manually:
        echo   .venv\Scripts\python.exe -m pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server\requirements-gmsh.txt
        exit /b 1
    ) else (
        echo   gmsh Python package installed from gmsh.info snapshot index.
    )
) else (
    echo   gmsh Python package installed from default index.
)

.venv\Scripts\python.exe -c "import gmsh; print(gmsh.__version__)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: gmsh Python package is still not importable in .venv.
    echo /api/mesh/build requires Python gmsh.
    exit /b 1
)
for /f "tokens=*" %%v in ('.venv\Scripts\python.exe -c "import gmsh; print(gmsh.__version__)"') do echo   gmsh Python version: %%v
echo.

echo Building Metal native release helper when available...
node scripts\run-backend-python.js server\scripts\build_metal_native_release.py
if errorlevel 1 (
    echo ERROR: Metal native release helper build failed.
    echo        Apple Silicon installs require this for the fast Metal BEM solve path.
    echo        Re-run after fixing the issue above, or run: npm run build:metal-helper
    exit /b 1
) else (
    echo   Metal native helper check complete.
)
echo.

echo Checking Metal BEM backend...
set "PYTHONPATH=%CD%\server;%PYTHONPATH%"
.venv\Scripts\python.exe -c "import sys; from solver.metal_solver import metal_backend_status; status = metal_backend_status(); print((status.get('reason') or 'Metal BEM backend is ready.') if status.get('available') else (status.get('reason') or 'Metal BEM backend is not available on this host.')); sys.exit(0 if status.get('available') else 1)"
if errorlevel 1 (
    set "METAL_READY=0"
    echo   WARNING: Metal BEM is not ready.
) else (
    set "METAL_READY=1"
    echo   Metal BEM is ready.
)
echo.

set "SOLVER_BACKEND_SUMMARY=Metal or Bempp solve backend: not ready"
if "%METAL_READY%"=="1" (
    echo Skipping Bempp install because Metal BEM is ready.
    set "SOLVER_BACKEND_SUMMARY=Metal or Bempp solve backend: Metal BEM ready (Bempp install skipped)"
) else (
    echo Installing Bempp cross-platform backend...
    .venv\Scripts\python.exe -m pip install --quiet -r server\requirements-bempp.txt
    if errorlevel 1 (
        echo ERROR: Bempp install failed and no Metal solve backend is ready.
        echo   Manual command:
        echo     .venv\Scripts\python.exe -m pip install -r server\requirements-bempp.txt
        exit /b 1
    ) else (
        .venv\Scripts\python.exe -c "import hornlab_bempp_bem" >nul 2>&1
        if errorlevel 1 (
            echo ERROR: Bempp installed but is not importable.
            echo   Manual command:
            echo     .venv\Scripts\python.exe -m pip install -r server\requirements-bempp.txt
            exit /b 1
        ) else (
            rem Importing the hornlab_bempp_bem wrapper proves nothing about
            rem whether a solve can run: the wrapper is pure Python and imports
            rem fine even when the bempp-cl engine cannot load. Verify the
            rem engine the solve path actually uses.
            .venv\Scripts\python.exe server\scripts\check_solver_engine.py
            if errorlevel 1 (
                echo.
                echo ERROR: Bempp is installed but no solve can run on this host.
                echo        Fix the issue reported above, then re-run this installer.
                exit /b 1
            )
            set "SOLVER_BACKEND_SUMMARY=Metal or Bempp solve backend: Bempp ready"
        )
    )
)
echo.

echo Recording backend interpreter contract...
if not exist ".waveguide" mkdir ".waveguide"
set "PREFERRED_PYTHON_FILE=%CD%\.waveguide\backend-python.path"
> "%PREFERRED_PYTHON_FILE%" echo %CD%\.venv\Scripts\python.exe
echo   Preferred backend interpreter: %CD%\.venv\Scripts\python.exe
echo   Marker file: %PREFERRED_PYTHON_FILE%
echo.

echo Running backend dependency preflight...
node scripts\preflight-backend-runtime.js --strict
if errorlevel 1 (
    echo ERROR: Backend preflight detected missing/unsupported required checks.
    echo        Fix the reported items, then re-run:
    echo          npm.cmd run preflight:backend:strict
    exit /b 1
) else (
    echo   Backend preflight: required checks ready.
)
echo.

echo ===============================================================
echo Install / update complete.
echo ===============================================================
echo To start the app:
echo   - Double-click launch\windows.bat
echo   - Or run: npm.cmd start
echo.
echo %SOLVER_BACKEND_SUMMARY%
echo.
exit /b 0

:update_from_git
if not exist ".git\" (
    echo Code update skipped: this folder is not a Git clone.
    echo ZIP downloads can be repaired by this script, but updating requires downloading a fresh ZIP.
    echo.
    exit /b 0
)

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is a Git clone, but Git is not installed or not available in PATH.
    echo        Install Git for Windows, then run install\install.bat again.
    exit /b 1
)

for /f "tokens=*" %%v in ('git --version') do echo   %%v
echo Checking for code updates...

rem A branch with no upstream cannot be pulled. That is a normal state for a
rem local working branch, so skip the update instead of aborting the install
rem with a misleading "you have local changes" message.
git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >nul 2>&1
if errorlevel 1 (
    for /f "tokens=*" %%b in ('git rev-parse --abbrev-ref HEAD') do (
        echo   Code update skipped: branch %%b has no upstream configured.
    )
    echo   Dependencies below are still installed and verified.
    echo.
    exit /b 0
)

set "BEFORE_COMMIT="
set "AFTER_COMMIT="
for /f "tokens=*" %%h in ('git rev-parse HEAD') do set "BEFORE_COMMIT=%%h"
git pull --ff-only
if errorlevel 1 (
    echo.
    echo ERROR: Code update failed.
    echo        This installer only performs safe fast-forward updates.
    echo        Common causes:
    echo          - Uncommitted local changes: commit or stash them, then retry.
    echo          - The branch has diverged from its upstream and cannot
    echo            fast-forward. Reconcile it manually, then retry.
    echo        Diagnose with: git status  and  git log --oneline -5
    exit /b 1
)
for /f "tokens=*" %%h in ('git rev-parse HEAD') do set "AFTER_COMMIT=%%h"
if /I not "!BEFORE_COMMIT!"=="!AFTER_COMMIT!" (
    echo   Code updated.
    set "CODE_UPDATED=1"
)
echo.
exit /b 0

:validate_venv
rem Exit 0 when .venv is a usable virtual environment on a supported Python.
rem The check lives in install\check_venv.py rather than a `python -c` one-liner
rem so that no parentheses or comparison operators reach cmd.exe's parser.
if not exist ".venv\Scripts\python.exe" exit /b 1
if not exist "install\check_venv.py" exit /b 1
.venv\Scripts\python.exe "install\check_venv.py" >nul 2>&1
exit /b %ERRORLEVEL%

:preserve_incompatible_venv
rem Keep exactly one backup. The previous scheme appended !RANDOM!!RANDOM! and
rem never cleaned up, so repeated runs accumulated multi-hundred-MB directories
rem indefinitely. Also sweep up any backups left by that older scheme.
echo   Existing .venv is broken or uses an unsupported Python.
if exist ".venv.incompatible\" rd /s /q ".venv.incompatible" >nul 2>&1
for /d %%d in (".venv.incompatible.*") do (
    echo   Removing stale backup %%~nxd
    rd /s /q "%%d" >nul 2>&1
)
echo   Preserving the current one as .venv.incompatible
move ".venv" ".venv.incompatible" >nul
if errorlevel 1 (
    echo ERROR: Could not preserve the incompatible .venv.
    echo        Close any program using it ^(editor, terminal, antivirus scan^)
    echo        and run the installer again.
    exit /b 1
)
exit /b 0

:ensure_git_for_dependencies
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is required to install pinned backend dependencies.
    echo        This also applies when the project was downloaded as a ZIP.
    echo        Install Git from https://git-scm.com/ and run this installer again.
    exit /b 1
)
exit /b 0

:ensure_node
echo Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    if exist "%NODEJS_HINT%\node.exe" set "PATH=%NODEJS_HINT%;%PATH%"
)

where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not available in PATH.
    echo Install command:
    echo   winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    echo After install, open a new Command Prompt and run install\install.bat again.
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo   Node.js: %%v

node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 20 || (major === 20 && minor >= 19) ? 0 : 1)"
if errorlevel 1 (
    echo ERROR: Node.js 20.19 or newer is required.
    echo        Install a current LTS version from https://nodejs.org/ and run this installer again.
    exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    if exist "%NODEJS_HINT%\npm.cmd" set "PATH=%NODEJS_HINT%;%PATH%"
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm.cmd is not available.
    echo If you are running from PowerShell, this may be an execution policy issue with npm.ps1.
    echo Run this installer from Command Prompt ^(cmd.exe^) or use npm.cmd directly.
    exit /b 1
)
for /f "tokens=*" %%v in ('npm.cmd --version') do echo   npm: %%v
echo.
exit /b 0
