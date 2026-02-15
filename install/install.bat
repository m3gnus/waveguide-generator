@echo off
:: Waveguide Generator — one-time installer for Windows
:: Run from the project root: install\install.bat

cd /d "%~dp0\.."

echo ╔══════════════════════════════════════════════════════════════╗
echo ║  WG - Waveguide Generator — Setup                           ║
echo ╚══════════════════════════════════════════════════════════════╝
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
echo Installing frontend dependencies...
call npm ci
if errorlevel 1 (
    echo ERROR: npm ci failed.
    pause
    exit /b 1
)
echo   Done.
echo.

:: ── Python ─────────────────────────────────────────────────────────
echo Checking Python 3...
set PYTHON_BIN=
for %%p in (py python3 python) do (
    if not defined PYTHON_BIN (
        where %%p >nul 2>&1
        if not errorlevel 1 (
            %%p -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>&1
            if not errorlevel 1 (
                set PYTHON_BIN=%%p
            )
        )
    )
)

if not defined PYTHON_BIN (
    echo ERROR: Python 3.10 through 3.13 is required.
    echo        Install from https://www.python.org/ ^(tick "Add python.exe to PATH"^)
    echo        and re-run this script.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PYTHON_BIN% --version') do echo   Python: %%v
echo.

:: ── Virtual environment ────────────────────────────────────────────
echo Creating Python virtual environment (.venv)...
if exist ".venv\" (
    echo   .venv already exists, skipping creation.
) else (
    %PYTHON_BIN% -m venv .venv
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
