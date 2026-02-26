@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ╔══════════════════════════════════════════════════════════════╗
echo ║  WG - Waveguide Generator — Quick Setup                    ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

echo Verifying project folder...
set HAS_ERROR=
if not exist "package.json" (
    echo   - Missing: package.json
    set HAS_ERROR=1
)
if not exist "install\install.bat" (
    echo   - Missing: install\install.bat
    set HAS_ERROR=1
)
if not exist "server\requirements.txt" (
    echo   - Missing: server\requirements.txt
    set HAS_ERROR=1
)
if not exist "launch\windows.bat" (
    echo   - Missing: launch\windows.bat
    set HAS_ERROR=1
)

if defined HAS_ERROR (
    echo.
    echo ERROR: This does not look like the full Waveguide Generator project folder.
    echo Current folder: %CD%
    echo.
    echo Fix steps:
    echo   1. Download the full project ZIP from GitHub.
    echo   2. Extract the ZIP completely.
    echo   3. Open the extracted folder ^(usually waveguide-generator-main^).
    echo   4. Double-click SETUP-WINDOWS.bat again.
    echo.
    echo GitHub: https://github.com/m3gnus/waveguide-generator
    pause
    exit /b 1
)

echo   Project folder looks good.
echo.
call install\install.bat
if errorlevel 1 (
    echo.
    echo Setup ended with an error.
    exit /b 1
)

exit /b 0
