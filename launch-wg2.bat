@echo off
rem Double-click in Explorer, or run from a command prompt, to start Waveguide
rem Generator v2. This is the Windows counterpart of launch-wg2.command.
rem
rem Unlike v1's install.bat this script never pulls over itself, so it does not
rem need install-and-update.bat's copy-to-TEMP dance: cmd.exe tracks its position
rem in a batch file by byte offset, and only a script that rewrites itself
rem resumes at a meaningless one.
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
cd /d "%REPO_DIR%" || goto :bad_directory

if not exist "launch\serve.py" goto :bad_directory
if not exist "server\" goto :bad_directory
if not exist "frontend\dist\index.html" goto :missing_interface

rem WG2_PYTHON can explicitly select another interpreter. Otherwise the launcher
rem creates and validates the repository-local v2 environment.
set "PYTHON="
if defined WG2_PYTHON goto :use_explicit_python

call :environment_is_ready
if not errorlevel 1 goto :environment_ready

call :find_bootstrap_python
if errorlevel 1 goto :no_python

echo Preparing the Waveguide Generator v2 Python environment...
"!BOOTSTRAP_PYTHON!" scripts\bootstrap.py
if errorlevel 1 goto :bootstrap_failed

:environment_ready
set "PYTHON=%REPO_DIR%\.venv\Scripts\python.exe"
goto :run

:use_explicit_python
if not exist "%WG2_PYTHON%" goto :bad_explicit_python
set "PYTHON=%WG2_PYTHON%"

:run
call :python_can_serve
if errorlevel 1 goto :unusable_environment

echo Starting Waveguide Generator v2...
echo Close this window or press Control-C to stop it.
echo.
"%PYTHON%" launch\serve.py %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" call :pause_when_double_clicked
exit /b %RESULT%


rem ---------------------------------------------------------------------------
rem Probes live in subroutines on purpose. v1 learned that a probe whose quoted
rem argument contains parentheses is mis-parsed by cmd.exe's block parser when it
rem is inlined into a parenthesised block: the check reported failure while the
rem identical command exited 0 at the prompt, so a healthy .venv was discarded
rem and rebuilt on every run. Keep them flat.
rem ---------------------------------------------------------------------------

:environment_is_ready
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" scripts\bootstrap.py --check >nul 2>&1
exit /b %ERRORLEVEL%

:python_can_serve
"%PYTHON%" -c "import fastapi, uvicorn" >nul 2>&1
exit /b %ERRORLEVEL%

:is_python_313
rem Exit 0 when the given interpreter is CPython 3.13, which is the only series
rem scripts\bootstrap.py accepts.
"%~1" -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 13))" >nul 2>&1
exit /b %ERRORLEVEL%

:find_bootstrap_python
rem The py launcher resolves a specific series directly, so try it first.
set "BOOTSTRAP_PYTHON="
set "STORE_ALIAS_SEEN="
where py >nul 2>&1
if not errorlevel 1 call :try_py_launcher
if defined BOOTSTRAP_PYTHON exit /b 0

rem Consider EVERY match from `where`, not just the first. On a default Windows
rem install the first hit is the Microsoft Store execution alias in
rem %LOCALAPPDATA%\Microsoft\WindowsApps: a zero-byte stub that opens the Store
rem instead of running Python. Taking it produces either a Store popup or a
rem baffling spawn failure much later, so skip aliases and keep looking.
for %%p in (python3.13 python3 python) do (
    if not defined BOOTSTRAP_PYTHON (
        for /f "delims=" %%w in ('where %%p 2^>nul') do (
            if not defined BOOTSTRAP_PYTHON call :consider_candidate "%%w"
        )
    )
)
if defined BOOTSTRAP_PYTHON exit /b 0
exit /b 1

:try_py_launcher
for /f "delims=" %%w in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do (
    if not defined BOOTSTRAP_PYTHON set "BOOTSTRAP_PYTHON=%%w"
)
exit /b 0

:consider_candidate
echo %~1| find /i "\WindowsApps\" >nul 2>&1
if not errorlevel 1 goto :candidate_is_store_alias
call :is_python_313 "%~1"
if errorlevel 1 exit /b 0
set "BOOTSTRAP_PYTHON=%~1"
exit /b 0

:candidate_is_store_alias
set "STORE_ALIAS_SEEN=1"
exit /b 0

:pause_when_double_clicked
rem Only pause when launched by double-click. When a console was already
rem attached, cmdcmdline contains the script path with /c and pausing would
rem block scripted or CI runs.
echo %CMDCMDLINE% | find /i "%~nx0" >nul 2>&1
if not errorlevel 1 pause
exit /b 0


rem ---------------------------------------------------------------------------
rem Failure paths
rem ---------------------------------------------------------------------------

:bad_directory
echo.
echo ERROR: launch-wg2.bat must remain in the waveguide-generator v2 folder.
echo        Current folder: %CD%
call :pause_when_double_clicked
exit /b 1

:missing_interface
rem Running v2 is not supposed to require Node. Releases ship the built SPA as
rem an attached archive, so point there first and leave the local build as the
rem developer path rather than the only one.
echo.
echo ERROR: The built interface is missing.
echo.
echo Download waveguide-generator-v2-spa-^<version^>.tar.gz from the release:
echo   https://github.com/m3gnus/waveguide-generator/releases
echo and extract it so that frontend\dist\index.html exists:
echo   tar -xzf waveguide-generator-v2-spa-^<version^>.tar.gz -C frontend
echo.
echo If you are working on the interface itself, build it instead:
echo   cd frontend ^&^& npm install ^&^& npm run build
call :pause_when_double_clicked
exit /b 1

:bad_explicit_python
echo.
echo ERROR: WG2_PYTHON does not point at an existing interpreter:
echo        %WG2_PYTHON%
call :pause_when_double_clicked
exit /b 1

:no_python
echo.
echo ERROR: CPython 3.13 is required and was not found.
echo        Install it from https://www.python.org/downloads/windows/
echo        and tick "Add python.exe to PATH", then run this launcher again.
echo.
echo        Check what is installed with: py -0p
if defined STORE_ALIAS_SEEN call :explain_store_alias
call :pause_when_double_clicked
exit /b 1

:explain_store_alias
echo.
echo        A Microsoft Store "App execution alias" for Python was found in
echo        %%LOCALAPPDATA%%\Microsoft\WindowsApps and skipped. That entry is a
echo        stub that opens the Store; it is not a Python installation.
echo        Turn it off in: Settings ^> Apps ^> Advanced app settings ^>
echo        App execution aliases ^> switch off "python.exe" and "python3.exe".
exit /b 0

:bootstrap_failed
echo.
echo ERROR: The v2 Python environment could not be prepared.
echo        Review the installation errors above, then run:
echo          py -3.13 scripts\bootstrap.py
call :pause_when_double_clicked
exit /b 1

:unusable_environment
echo.
echo ERROR: The selected Python environment cannot import FastAPI and Uvicorn:
echo        %PYTHON%
echo        Run: py -3.13 scripts\bootstrap.py --force
call :pause_when_double_clicked
exit /b 1
