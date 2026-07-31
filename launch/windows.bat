@echo off
setlocal EnableExtensions

:: Waveguide Generator launcher for Windows
:: Double-click this file to start the app
::
:: `npm` on Windows is npm.cmd. Invoking one batch file from another WITHOUT
:: `call` transfers control permanently and never returns, so the `pause` below
:: was unreachable: on failure the console closed instantly and the user lost
:: the error. Always `call npm.cmd`.

cd /d "%~dp0\.."
if errorlevel 1 (
    echo ERROR: Could not enter the project folder.
    pause
    exit /b 1
)

if not exist "package.json" (
    echo ERROR: This does not look like the Waveguide Generator project folder.
    echo Current folder: %CD%
    echo Run install\install-and-update.bat first.
    pause
    exit /b 1
)

if not exist "node_modules\" (
    echo ERROR: Dependencies are not installed.
    echo Run install\install-and-update.bat first.
    pause
    exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\npm.cmd" set "PATH=C:\Program Files\nodejs;%PATH%"
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm.cmd was not found in PATH.
    echo Install Node.js LTS from https://nodejs.org/ and try again.
    pause
    exit /b 1
)

if not exist ".waveguide\logs" mkdir ".waveguide\logs" >nul 2>&1
set "WG_LAUNCH_LOG=%CD%\.waveguide\logs\launch.log"

echo Starting Waveguide Generator...
echo Log: %WG_LAUNCH_LOG%
echo.

call npm.cmd start
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
    echo.
    echo ===============================================================
    echo The app exited with error code %RESULT%.
    echo ===============================================================
    echo Things to check:
    echo   1. Re-run install\install-and-update.bat
    echo   2. Verify the backend:  npm.cmd run doctor:backend
    echo   3. Check whether a solve can run:
    echo      node scripts\run-backend-python.js server\scripts\check_solver_engine.py
    echo.
)

pause
exit /b %RESULT%
