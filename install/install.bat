@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\.."

set "NODEJS_HINT=C:\Program Files\nodejs"
set "BEMPP_CL_URL=git+https://github.com/bempp/bempp-cl.git@d4f23c4b77b4e86e0b2c9da42db39fea2995bb33"

echo ===============================================================
echo WG - Waveguide Generator Install / Update
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

call :update_from_git
if errorlevel 1 exit /b 1

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

            %%p -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON_BIN=%%p"
                set "PYTHON_PATH=!CANDIDATE_PATH!"
                set "PYTHON_VERSION=!CANDIDATE_VERSION!"
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

echo Checking Metal BEM backend...
set "METAL_BEM_READY=0"
set "PYTHONPATH=%CD%\server;%PYTHONPATH%"
.venv\Scripts\python.exe -c "import sys; from solver.metal_solver import metal_backend_status; status = metal_backend_status(); print((status.get('reason') or 'Metal BEM backend is ready.') if status.get('available') else (status.get('reason') or 'Metal BEM backend is not available on this host.')); sys.exit(0 if status.get('available') else 1)"
if errorlevel 1 (
    echo   Metal BEM is not ready.
    echo   Installing BEMPP/OpenCL fallback dependencies.
) else (
    set "METAL_BEM_READY=1"
    echo   Metal BEM is ready.
    echo   Skipping bempp-cl and OpenCL fallback setup.
)
echo.

if "%METAL_BEM_READY%"=="1" goto :opencl_done

echo Installing bempp-cl fallback ^(needed when Metal BEM is unavailable^)...
.venv\Scripts\python.exe -m pip install --quiet pyopencl
if errorlevel 1 (
    echo   WARNING: pyopencl automatic install failed.
    echo            You can retry later with:
    echo              .venv\Scripts\python.exe -m pip install pyopencl
    goto :opencl_done
)
.venv\Scripts\python.exe -m pip install %BEMPP_CL_URL%
if errorlevel 1 (
    echo   WARNING: bempp-cl automatic install failed.
    echo            You can retry later with:
    echo              .venv\Scripts\python.exe -m pip install %BEMPP_CL_URL%
    goto :opencl_done
) else (
    echo   bempp-cl installed.
)
echo.

:: ── OpenCL runtime check ──────────────────────────────────────────
echo Checking OpenCL runtime for bempp-cl simulations...
.venv\Scripts\python.exe -c "import pyopencl; assert pyopencl.get_platforms()" >nul 2>&1
if not errorlevel 1 (
    echo   OpenCL is available.
    goto :opencl_done
)
echo   No OpenCL platform found.

:: Check whether any OpenCL ICD is registered (GPU drivers present but pyopencl failed)
reg query "HKLM\SOFTWARE\Khronos\OpenCL\Vendors" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   WARNING: OpenCL vendor entries exist in registry but pyopencl could not use them.
    echo            Try updating your GPU drivers, then restart and re-run a simulation.
    goto :opencl_done
)

:: No ICDs at all — try winget to install Intel CPU OpenCL runtime
where winget >nul 2>&1
if errorlevel 1 goto :opencl_warn

echo   Attempting to install Intel OpenCL CPU Runtime via winget...
winget install --id Intel.OpenCLRuntimeForIntel --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
if errorlevel 1 goto :opencl_warn

:: Re-test after winget install
.venv\Scripts\python.exe -c "import pyopencl; assert pyopencl.get_platforms()" >nul 2>&1
if not errorlevel 1 (
    echo   OpenCL ^(Intel CPU runtime^) is now available.
    goto :opencl_done
)

:opencl_warn
echo.
echo   WARNING: No OpenCL runtime is available. Acoustic simulations will not work.
echo   To fix, choose one of:
echo     1. Update your GPU drivers ^(NVIDIA/AMD drivers include OpenCL^)
echo     2. Install Intel OpenCL CPU Runtime ^(works on Intel CPUs^):
echo          winget install Intel.OpenCLRuntimeForIntel
echo     3. On a VM: enable GPU passthrough or use a physical machine.

:opencl_done
echo.

echo Recording backend interpreter contract...
if not exist ".waveguide" mkdir ".waveguide"
set "PREFERRED_PYTHON_FILE=%CD%\.waveguide\backend-python.path"
> "%PREFERRED_PYTHON_FILE%" echo %CD%\.venv\Scripts\python.exe
echo   Preferred backend interpreter: %CD%\.venv\Scripts\python.exe
echo   Marker file: %PREFERRED_PYTHON_FILE%
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

echo Running backend dependency preflight...
node scripts\preflight-backend-runtime.js --strict
if errorlevel 1 (
    echo   WARNING: Backend preflight detected missing/unsupported required checks.
    echo            Fix the reported items, then re-run:
    echo              npm.cmd run preflight:backend:strict
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
git pull --ff-only
if errorlevel 1 (
    echo.
    echo ERROR: Code update failed.
    echo        This installer only performs safe fast-forward updates.
    echo        If you have local changes, commit or stash them before updating.
    exit /b 1
)
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
