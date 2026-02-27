@echo off
:: Waveguide Generator — one-time installer for Windows
:: Run from the project root: install\install.bat

setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\.."

echo ╔══════════════════════════════════════════════════════════════╗
echo ║  WG - Waveguide Generator — Setup                           ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

:: ── Project folder sanity check ───────────────────────────────────
echo Verifying project folder...
set ROOT_INVALID=
for %%f in (package.json install\install.bat server\requirements.txt launch\windows.bat) do (
    if not exist "%%f" (
        echo   - Missing: %%f
        set ROOT_INVALID=1
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
    pause
    exit /b 1
)
echo   Project folder looks good.
echo.

:: ── Node.js ────────────────────────────────────────────────────────
echo Checking Node.js...
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed.
    echo        Install from https://nodejs.org/ and re-run this script.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo   Node.js: %%v

where npm >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm is not installed ^(should come with Node.js^).
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('npm --version') do echo   npm:     %%v
echo.

:: ── Frontend dependencies ──────────────────────────────────────────
if not exist "package.json" (
    echo ERROR: package.json not found in this folder.
    echo        Make sure you are running install\install.bat from the full project folder.
    pause
    exit /b 1
)

echo Installing frontend dependencies...
if exist "package-lock.json" (
    call npm ci
    if errorlevel 1 (
        echo ERROR: npm ci failed.
        pause
        exit /b 1
    )
) else (
    echo WARNING: package-lock.json was not found.
    echo          This usually means the project was not downloaded or extracted completely.
    echo          Falling back to npm install...
    call npm install
    if errorlevel 1 (
        echo ERROR: npm install failed.
        pause
        exit /b 1
    )
)
echo   Done.
echo.

:: ── Python ─────────────────────────────────────────────────────────
echo Checking Python 3...
set PYTHON_BIN=
set PYTHON_VERSION=
set PYTHON_PATH=
set FIRST_PYTHON_CMD=
set FIRST_PYTHON_VERSION=
set FIRST_PYTHON_PATH=

for %%p in (py python3 python) do (
    if not defined PYTHON_BIN (
        where %%p >nul 2>&1
        if not errorlevel 1 (
            set CANDIDATE_PATH=
            for /f "delims=" %%w in ('where %%p 2^>nul') do (
                if not defined CANDIDATE_PATH set CANDIDATE_PATH=%%w
            )

            set CANDIDATE_VERSION=
            for /f "delims=" %%v in ('%%p -c "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))" 2^>nul') do (
                if not defined CANDIDATE_VERSION set CANDIDATE_VERSION=%%v
            )

            if not defined FIRST_PYTHON_CMD (
                set FIRST_PYTHON_CMD=%%p
                set FIRST_PYTHON_PATH=!CANDIDATE_PATH!
                set FIRST_PYTHON_VERSION=!CANDIDATE_VERSION!
            )

            %%p -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
            if not errorlevel 1 (
                set PYTHON_BIN=%%p
                set PYTHON_PATH=!CANDIDATE_PATH!
                set PYTHON_VERSION=!CANDIDATE_VERSION!
            )
        )
    )
)

if not defined PYTHON_BIN (
    echo ERROR: Python 3.10 through 3.14 is required.
    if defined FIRST_PYTHON_CMD (
        echo        Detected command: !FIRST_PYTHON_CMD!
        if defined FIRST_PYTHON_PATH echo        Detected path: !FIRST_PYTHON_PATH!
        if defined FIRST_PYTHON_VERSION (
            echo        Detected version: !FIRST_PYTHON_VERSION!
            echo        This version is outside the supported range.
        )
    ) else (
        echo        No Python command was detected in PATH.
    )

    if defined FIRST_PYTHON_PATH (
        echo !FIRST_PYTHON_PATH! | findstr /I "\\WindowsApps\\" >nul
        if not errorlevel 1 (
            echo.
            echo NOTE: Detected Windows Store App Execution Alias path.
            echo       Disable aliases for python.exe/python3.exe in:
            echo       Settings ^> Apps ^> Advanced app settings ^> App execution aliases
        )
    )

    echo.
    echo Recommended checks:
    echo   1. Open Command Prompt and run: py -0p
    echo   2. If Python is missing, install from https://www.python.org/downloads/windows/
    echo      and tick "Add python.exe to PATH"
    pause
    exit /b 1
)

echo   Python command: %PYTHON_BIN%
if defined PYTHON_VERSION echo   Python version: %PYTHON_VERSION%
if defined PYTHON_PATH echo   Python path: %PYTHON_PATH%
echo.

:: ── Virtual environment ────────────────────────────────────────────
echo Creating Python virtual environment (.venv)...
if exist ".venv\" (
    echo   .venv already exists, skipping creation.
) else (
    %PYTHON_BIN% -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create .venv using %PYTHON_BIN%.
        pause
        exit /b 1
    )
    echo   Created.
)

echo Installing backend dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r server\requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install backend dependencies.
    pause
    exit /b 1
)
echo   Done.
echo.

:: ── Optional: bempp-cl ─────────────────────────────────────────────
set /p INSTALL_BEM="Install bempp-cl BEM solver? (needed for simulations, takes 5-10 min) [y/N]: "
if /i "%INSTALL_BEM%"=="y" (
    echo Installing bempp-cl...
    .venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
    echo   Done.
) else (
    echo   Skipped. Install later with:
    echo     .venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
)
echo.

echo ╔══════════════════════════════════════════════════════════════╗
echo ║  Setup complete!                                             ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
echo To start the app:
echo   * Double-click  launch\windows.bat
echo   * Or run:       npm start
echo.
pause
