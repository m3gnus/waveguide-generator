@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\.."

set "NODEJS_HINT=C:\Program Files\nodejs"

echo ===============================================================
echo WG - Waveguide Generator Setup
echo ===============================================================
echo.

echo Verifying project folder...
set "ROOT_INVALID="
for %%f in (package.json install\install.bat server\requirements.txt server\requirements-gmsh.txt launch\windows.bat) do (
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

call :ensure_node
if errorlevel 1 exit /b 1

echo Installing frontend dependencies...
if exist "package-lock.json" (
    call npm.cmd ci
    if errorlevel 1 (
        echo ERROR: npm ci failed.
        exit /b 1
    )
) else (
    echo WARNING: package-lock.json was not found.
    echo          Falling back to npm install...
    call npm.cmd install
    if errorlevel 1 (
        echo ERROR: npm install failed.
        exit /b 1
    )
)
echo   Frontend dependencies installed.
echo.

echo Checking Python 3...
set "PYTHON_BIN="
set "PYTHON_VERSION="
set "PYTHON_PATH="
set "FIRST_PYTHON_CMD="
set "FIRST_PYTHON_VERSION="
set "FIRST_PYTHON_PATH="

for %%p in (py python3 python) do (
    if not defined PYTHON_BIN (
        where %%p >nul 2>&1
        if not errorlevel 1 (
            set "CANDIDATE_PATH="
            for /f "delims=" %%w in ('where %%p 2^>nul') do (
                if not defined CANDIDATE_PATH set "CANDIDATE_PATH=%%w"
            )

            set "CANDIDATE_VERSION="
            for /f "delims=" %%v in ('%%p -c "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))" 2^>nul') do (
                if not defined CANDIDATE_VERSION set "CANDIDATE_VERSION=%%v"
            )

            if not defined FIRST_PYTHON_CMD (
                set "FIRST_PYTHON_CMD=%%p"
                set "FIRST_PYTHON_PATH=!CANDIDATE_PATH!"
                set "FIRST_PYTHON_VERSION=!CANDIDATE_VERSION!"
            )

            %%p -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON_BIN=%%p"
                set "PYTHON_PATH=!CANDIDATE_PATH!"
                set "PYTHON_VERSION=!CANDIDATE_VERSION!"
            )
        )
    )
)

if not defined PYTHON_BIN (
    echo ERROR: Python 3.10 or newer is required.
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
    echo.
    echo Recommended checks:
    echo   1. Open Command Prompt and run: py -0p
    echo   2. If Python is missing, install from https://www.python.org/downloads/windows/
    echo      and tick "Add python.exe to PATH"
    exit /b 1
)

echo   Python command: %PYTHON_BIN%
if defined PYTHON_VERSION echo   Python version: %PYTHON_VERSION%
if defined PYTHON_PATH echo   Python path: %PYTHON_PATH%
echo.

echo Creating Python virtual environment (.venv)...
if exist ".venv\" (
    echo   .venv already exists, skipping creation.
) else (
    %PYTHON_BIN% -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create .venv using %PYTHON_BIN%.
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

echo Installing bempp-cl ^(needed for simulations^)...
.venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
if errorlevel 1 (
    echo   WARNING: bempp-cl automatic install failed.
    echo            You can retry later with:
    echo              .venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
) else (
    echo   bempp-cl installed.
)
echo.

echo ===============================================================
echo Setup complete.
echo ===============================================================
echo To start the app:
echo   - Double-click launch\windows.bat
echo   - Or run: npm.cmd start
echo.
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
