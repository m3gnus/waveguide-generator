@echo off
rem Waveguide Generator -- installer / updater for Windows.
rem
rem Run installers\windows\install-and-update.bat, not this file. This one performs
rem `git pull`, which can rewrite it while cmd.exe is still executing it, and
rem cmd tracks its position in a batch file by byte offset: a script that
rem updates itself resumes at a meaningless offset and runs fragments of
rem unrelated lines. v1 saw exactly that, as "The system cannot find the batch
rem label specified - update_from_git" followed by "'ttps:' is not recognized"
rem (the tail of an https:// URL from a later line). install-and-update.bat
rem therefore copies this file to %TEMP%, runs the copy, and passes the
rem repository root in --root. Never assume %~dp0 is inside the repository.
rem
rem Delayed expansion is deliberately OFF, and every branch is a goto rather
rem than a parenthesised block. Both rules exist to survive user paths:
rem cmd expands %VAR% when it PARSES a block, so a path containing ')' closes
rem the block early; and with delayed expansion ON every expanded value is
rem rescanned for '!' and '^', so a path under "C:\My ! Projects" silently
rem loses characters. Flat control flow plus quoted "%VAR%" is immune to both.
setlocal EnableExtensions DisableDelayedExpansion

set "WG_ROOT="
set "AFTER_PULL="
set "LAUNCH=1"
set "FORCE="
set "SKIP_SPA="
set "SPA_ARCHIVE="
set "SPA_BASE_URL="
set "SPA_VERSION="
set "TAG="
set "GIT_VERSION="
set "SPA_SUMMARY="
set "VCREDIST_SUMMARY="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--after-pull" goto arg_after_pull
if /I "%~1"=="--root" goto arg_root
if /I "%~1"=="--no-launch" goto arg_no_launch
if /I "%~1"=="--force" goto arg_force
if /I "%~1"=="--skip-spa" goto arg_skip_spa
if /I "%~1"=="--spa-archive" goto arg_spa_archive
if /I "%~1"=="--spa-base-url" goto arg_spa_base_url
if /I "%~1"=="--version" goto arg_version
if /I "%~1"=="--tag" goto arg_tag
if /I "%~1"=="--help" goto show_usage
if /I "%~1"=="-h" goto show_usage
echo ERROR: Unknown option: %~1
echo        Run with --help to see what is accepted.
exit /b 1

:arg_after_pull
set "AFTER_PULL=1"
shift
goto parse_args
:arg_root
set "WG_ROOT=%~2"
shift
shift
goto parse_args
:arg_no_launch
set "LAUNCH="
shift
goto parse_args
:arg_force
set "FORCE=1"
shift
goto parse_args
:arg_skip_spa
set "SKIP_SPA=1"
shift
goto parse_args
:arg_spa_archive
set "SPA_ARCHIVE=%~2"
shift
shift
goto parse_args
:arg_spa_base_url
set "SPA_BASE_URL=%~2"
shift
shift
goto parse_args
:arg_version
set "SPA_VERSION=%~2"
shift
shift
goto parse_args
:arg_tag
set "TAG=%~2"
shift
shift
goto parse_args

:show_usage
echo Waveguide Generator installer.
echo.
echo   --tag vX.Y.Z        check the repository out at a release tag first
echo   --version X.Y.Z     install the SPA for this version
echo   --spa-archive PATH  install a downloaded SPA archive instead of fetching one
echo   --spa-base-url URL  directory holding the SPA archive and its .sha256
echo   --skip-spa          leave frontend\dist alone ^(for interface development^)
echo   --force             rebuild the environment and reinstall the SPA
echo   --no-launch         finish without starting the application
echo   --help              this message
echo.
echo Uninstall with: installers\windows\uninstall.bat
exit /b 0

:args_done
if defined WG_ROOT goto enter_named_root
cd /d "%~dp0.."
if errorlevel 1 goto bad_directory
goto root_ready
:enter_named_root
cd /d "%WG_ROOT%"
if errorlevel 1 goto bad_directory
:root_ready
set "WG_ROOT=%CD%"

echo ===============================================================
echo  Waveguide Generator -- install / update
echo ===============================================================

echo.
echo Verifying the project folder...
set "ROOT_INVALID="
for %%f in (launch\serve.py launchers\statusapp\__main__.py launchers\windows\launch-wg.bat scripts\bootstrap.py scripts\fetch_spa.py shared\version.json server\requirements-lock.txt server\requirements-pins.txt) do if not exist "%%f" call :report_missing "%%f"
if defined ROOT_INVALID goto bad_project_folder
echo   Looks good: %WG_ROOT%

echo.
echo Checking prerequisites...
call :ensure_git
if errorlevel 1 exit /b 1
call :check_vcredist

call :find_bootstrap_python
if errorlevel 1 goto no_python
echo   Python: %BOOTSTRAP_PYTHON%

echo.
echo Checking for code updates...
if defined AFTER_PULL goto after_pull_notice
call :update_from_git
if errorlevel 1 exit /b 1
if defined CODE_UPDATED goto request_relaunch
goto updates_done
:after_pull_notice
echo   Code already updated; continuing with the updated installer.
:updates_done

rem The interface comes before the environment on purpose: fetch_spa.py is
rem standard library only, so it runs on the interpreter found above and a
rem missing or corrupted release asset fails in seconds instead of after a
rem multi-minute pip install.
echo.
echo Installing the prebuilt interface...
if defined SKIP_SPA goto spa_skipped
set "SPA_ARGS="
if defined SPA_VERSION set SPA_ARGS=%SPA_ARGS% --version "%SPA_VERSION%"
if defined SPA_ARCHIVE set SPA_ARGS=%SPA_ARGS% --archive "%SPA_ARCHIVE%"
if defined SPA_BASE_URL set SPA_ARGS=%SPA_ARGS% --base-url "%SPA_BASE_URL%"
if defined FORCE set SPA_ARGS=%SPA_ARGS% --force
"%BOOTSTRAP_PYTHON%" "%WG_ROOT%\scripts\fetch_spa.py"%SPA_ARGS%
if errorlevel 1 goto spa_failed
set "SPA_SUMMARY=Interface: installed."
goto spa_done
:spa_skipped
set "SPA_SUMMARY=Interface: left untouched (--skip-spa)."
echo   Skipped by request. frontend\dist is whatever you built there.
goto spa_done
:spa_failed
if defined FORCE goto spa_replacement_failed
if defined SPA_ARCHIVE goto spa_replacement_failed
if defined SPA_BASE_URL goto spa_replacement_failed
rem Keep an offline fallback only when it was checksum-verified for this exact
rem version. Local builds and stale releases require explicit --skip-spa.
set "SPA_CHECK_ARGS=--check"
if defined SPA_VERSION set SPA_CHECK_ARGS=%SPA_CHECK_ARGS% --version "%SPA_VERSION%"
"%BOOTSTRAP_PYTHON%" "%WG_ROOT%\scripts\fetch_spa.py" %SPA_CHECK_ARGS%
if errorlevel 1 goto spa_fatal
echo.
echo   WARNING: the release interface could not be installed ^(see above^), but
echo            the installed interface is a checksum-verified copy of this
echo            version, so it is being kept.
set "SPA_SUMMARY=Interface: kept the matching checksum-verified frontend\dist."
goto spa_done
:spa_done

echo.
echo Preparing the Python environment...
set "BOOTSTRAP_ARGS="
if defined FORCE set "BOOTSTRAP_ARGS=--force"
"%BOOTSTRAP_PYTHON%" "%WG_ROOT%\scripts\bootstrap.py" %BOOTSTRAP_ARGS%
if errorlevel 1 goto bootstrap_failed
if not exist ".venv\Scripts\python.exe" goto bootstrap_failed

echo.
echo Checking that a solve can run...
".venv\Scripts\python.exe" "%WG_ROOT%\scripts\check_backends.py"
if errorlevel 1 goto no_solve_backend

echo.
echo ===============================================================
echo  Install complete.
echo ===============================================================
echo   %SPA_SUMMARY%
if defined VCREDIST_SUMMARY echo   %VCREDIST_SUMMARY%
echo.
echo Start it any time with launchers\windows\launch-wg.bat.
echo It picks the first free port from 3100 to 3109 and shows the local URL.
echo.
echo Remove it again with:
echo   installers\windows\uninstall.bat            ^(environment and interface^)
echo   installers\windows\uninstall.bat --data     ^(also saved designs and job history^)
echo.
if not defined LAUNCH goto finished_without_launch
echo Starting Waveguide Generator...
call "%WG_ROOT%\launchers\windows\launch-wg.bat"
exit /b %ERRORLEVEL%

:finished_without_launch
echo Not starting it ^(--no-launch^).
exit /b 0


rem ---------------------------------------------------------------------------
rem Subroutines. Probes live here rather than inline because cmd.exe mis-parses
rem a quoted argument containing parentheses inside a parenthesised block --
rem v1 discarded a healthy .venv on every Windows run to exactly that.
rem ---------------------------------------------------------------------------

:report_missing
echo   - Missing: %~1
set "ROOT_INVALID=1"
exit /b 0

:ensure_git
where git >nul 2>&1
if errorlevel 1 goto git_missing
for /f "tokens=3" %%v in ('git --version') do if not defined GIT_VERSION set "GIT_VERSION=%%v"
if not defined GIT_VERSION goto git_version_unknown
for /f "tokens=1,2 delims=." %%a in ("%GIT_VERSION%") do call :note_git_series %%a %%b
rem Both halves, because `if %GIT_MINOR% LSS 20` with GIT_MINOR undefined is not
rem a false comparison -- it is a syntax error that aborts the installer with
rem "LSS was unexpected at this time."
if not defined GIT_MAJOR goto git_version_unknown
if not defined GIT_MINOR goto git_version_unknown
if %GIT_MAJOR% LSS 2 goto git_too_old
if %GIT_MAJOR% GTR 2 goto git_ok
if %GIT_MINOR% LSS 20 goto git_too_old
:git_ok
echo   Git: %GIT_VERSION%
exit /b 0
:git_version_unknown
echo   Git: version could not be parsed; continuing.
exit /b 0
:git_too_old
echo ERROR: Git 2.20 or newer is required; found %GIT_VERSION%.
echo        Older versions cannot fetch the pinned module commits reliably.
echo        winget install --id Git.Git -e
exit /b 1
:git_missing
echo ERROR: Git is required, and not just to update this checkout: the four
echo        pinned HornLab modules are installed straight from Git.
echo        winget install --id Git.Git -e
echo        or download it from https://git-scm.com/
echo        Then open a NEW Command Prompt and run this installer again.
exit /b 1

:note_git_series
set "GIT_MAJOR=%~1"
set "GIT_MINOR=%~2"
exit /b 0

:check_vcredist
rem numba and llvmlite -- which bempp assembles through, and bempp is the only
rem solve backend on Windows -- link against runtime DLLs that Windows does not
rem install by default. v1 shipped an installer that said "Bempp ready" on a box
rem missing them and every solve then died on "Numba could not be imported".
rem
rem Warn rather than refuse: this looks only in System32, and check_backends.py
rem below asks ctypes whether they really load, which is the authoritative
rem answer and is what makes the failure fatal.
if not exist "%SystemRoot%\System32\vcruntime140.dll" goto vcredist_missing
if not exist "%SystemRoot%\System32\vcruntime140_1.dll" goto vcredist_missing
if not exist "%SystemRoot%\System32\msvcp140.dll" goto vcredist_missing
echo   Microsoft Visual C++ runtime: present
exit /b 0
:vcredist_missing
echo   WARNING: the Microsoft Visual C++ Redistributable ^(x64^) was not found.
echo            bempp is the only solve backend on Windows and its compiled
echo            extensions cannot load without it. Install it with:
echo              winget install --id Microsoft.VCRedist.2015+.x64 --scope user
echo            or from https://aka.ms/vs/17/release/vc_redist.x64.exe
set "VCREDIST_SUMMARY=Microsoft Visual C++ Redistributable (x64) was not found in System32."
exit /b 0

:is_python_313
rem Exit 0 when the given interpreter is CPython 3.13, the only series
rem scripts\bootstrap.py accepts.
"%~1" -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 13))" >nul 2>&1
exit /b %ERRORLEVEL%

:find_bootstrap_python
set "BOOTSTRAP_PYTHON="
set "STORE_ALIAS_SEEN="
where py >nul 2>&1
if not errorlevel 1 call :try_py_launcher
if defined BOOTSTRAP_PYTHON exit /b 0
rem Consider EVERY match from `where`, not just the first. On a default Windows
rem install the first hit is the Microsoft Store execution alias in
rem %LOCALAPPDATA%\Microsoft\WindowsApps: a zero-byte stub that opens the Store
rem instead of running Python. Taking it produces a Store popup or a baffling
rem spawn failure much later, so skip aliases and keep looking.
for %%p in (python3.13 python3 python) do call :scan_python_command %%p
if defined BOOTSTRAP_PYTHON exit /b 0
exit /b 1

:scan_python_command
if defined BOOTSTRAP_PYTHON exit /b 0
for /f "delims=" %%w in ('where %~1 2^>nul') do call :consider_candidate "%%w"
exit /b 0

:try_py_launcher
for /f "delims=" %%w in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do if not defined BOOTSTRAP_PYTHON set "BOOTSTRAP_PYTHON=%%w"
exit /b 0

:consider_candidate
if defined BOOTSTRAP_PYTHON exit /b 0
rem Test for the Store alias by substring replacement rather than
rem `echo ... | find`. The piped form re-parses the candidate as command text,
rem so an interpreter path containing '&' would run its tail as a command.
set "CANDIDATE=%~1"
if not "%CANDIDATE:\WindowsApps\=%"=="%CANDIDATE%" goto candidate_is_store_alias
call :is_python_313 "%CANDIDATE%"
if errorlevel 1 exit /b 0
set "BOOTSTRAP_PYTHON=%CANDIDATE%"
exit /b 0
:candidate_is_store_alias
set "STORE_ALIAS_SEEN=1"
exit /b 0

:update_from_git
set "CODE_UPDATED="
if not exist ".git\" goto no_git_clone
if defined TAG goto checkout_tag

git symbolic-ref --quiet HEAD >nul 2>&1
if errorlevel 1 goto detached_head
git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >nul 2>&1
if errorlevel 1 goto no_upstream
rem Report a dirty tree directly. Letting `git pull --ff-only` discover it
rem produces a message about merging, which sends people to the wrong fix.
for /f "delims=" %%s in ('git status --porcelain -uno') do goto dirty_tree

set "BEFORE_COMMIT="
set "AFTER_COMMIT="
for /f "delims=" %%h in ('git rev-parse HEAD') do if not defined BEFORE_COMMIT set "BEFORE_COMMIT=%%h"
git pull --ff-only
if errorlevel 1 goto pull_failed
for /f "delims=" %%h in ('git rev-parse HEAD') do if not defined AFTER_COMMIT set "AFTER_COMMIT=%%h"
if /I "%BEFORE_COMMIT%"=="%AFTER_COMMIT%" goto already_current
echo   Code updated.
set "CODE_UPDATED=1"
exit /b 0

:already_current
echo   Already up to date.
exit /b 0
:no_git_clone
echo   Skipped: this folder is not a Git clone, so there is nothing to update.
exit /b 0
:detached_head
echo   Skipped: HEAD is detached, which is what --tag leaves behind.
echo   Switch to a branch to resume updates:  git checkout main
exit /b 0
:no_upstream
echo   Skipped: this branch has no upstream configured, so there is nothing to
echo   fast-forward from. Everything below still runs.
exit /b 0
:dirty_tree
echo   Skipped: this checkout has uncommitted changes, and only fast-forward
echo   updates are safe here. Commit or stash them, then run this again:
echo     git status
echo   Everything below still runs against the code you have.
exit /b 0
:pull_failed
echo.
echo ERROR: Code update failed. This installer only fast-forwards.
echo        The branch has diverged from its upstream and cannot fast-forward.
echo        Reconcile it by hand, then run this again. Diagnose with:
echo          git status
echo          git log --oneline -5
exit /b 1

:checkout_tag
for /f "delims=" %%s in ('git status --porcelain -uno') do goto tag_needs_clean_tree
git fetch --tags --quiet
if errorlevel 1 goto tag_fetch_failed
git rev-parse --verify --quiet "refs/tags/%TAG%" >nul
if errorlevel 1 goto tag_unknown
git checkout --quiet "%TAG%"
if errorlevel 1 goto tag_checkout_failed
echo   Checked out %TAG% ^(detached HEAD; fast-forward updates are off until you
echo   switch back to a branch^).
set "CODE_UPDATED=1"
exit /b 0
:tag_needs_clean_tree
echo ERROR: Cannot check out %TAG%: this checkout has uncommitted changes.
echo        Commit or stash them first:  git status
echo        Or install without switching tags by omitting --tag.
exit /b 1
:tag_fetch_failed
echo ERROR: Could not fetch tags from the remote.
exit /b 1
:tag_unknown
echo ERROR: No such tag: %TAG%
echo        List what exists with:  git tag --list "v*"
exit /b 1
:tag_checkout_failed
echo ERROR: Could not check out %TAG%.
exit /b 1


rem ---------------------------------------------------------------------------
rem Failure paths
rem ---------------------------------------------------------------------------

:request_relaunch
rem The pull just rewrote the files on disk, possibly including this script. Do
rem not call the updated installer from here: this process is still reading the
rem old byte offsets. Hand control back to install-and-update.bat, which
rem re-copies and re-launches.
echo   Relaunching with the updated installer...
exit /b 10

:bad_directory
echo ERROR: Could not enter the project folder.
exit /b 1

:bad_project_folder
echo.
echo ERROR: This does not look like a complete Waveguide Generator checkout.
echo        Current folder: %WG_ROOT%
echo.
echo        Clone it rather than downloading a ZIP -- the installer updates
echo        itself with Git, and the pinned HornLab modules install from Git too:
echo          git clone https://github.com/m3gnus/waveguide-generator.git
exit /b 1

:no_python
echo.
echo ERROR: CPython 3.13 is required and was not found.
echo        v2 locks its dependency set against one interpreter series on
echo        purpose, so a near miss is rejected rather than half-installed.
echo        Install it from https://www.python.org/downloads/windows/ and tick
echo        "Add python.exe to PATH", then open a NEW Command Prompt and run
echo        this installer again.
echo.
echo        Check what is installed with: py -0p
if defined STORE_ALIAS_SEEN call :explain_store_alias
exit /b 1

:explain_store_alias
echo.
echo        A Microsoft Store "App execution alias" for Python was found in
echo        %%LOCALAPPDATA%%\Microsoft\WindowsApps and skipped. That entry is a
echo        stub that opens the Store; it is not a Python installation.
echo        Turn it off in: Settings ^> Apps ^> Advanced app settings ^>
echo        App execution aliases ^> switch off "python.exe" and "python3.exe".
exit /b 0

:spa_fatal
echo.
echo ERROR: The prebuilt interface could not be installed, and the existing
echo        frontend\dist is not a checksum-verified copy of the requested version.
echo        v2 will not start with a stale or unverified interface. See above.
echo.
echo        If you are working on the interface itself, build it locally:
echo          cd frontend ^&^& npm ci ^&^& npm run build
echo        then run this installer again with --skip-spa.
exit /b 1

:spa_replacement_failed
echo.
echo ERROR: The requested SPA replacement could not be installed.
echo        The existing interface was left intact, but an explicit --force,
echo        --spa-archive, or --spa-base-url request is never successful when
echo        its download or checksum verification fails. See the reason above.
exit /b 1

:bootstrap_failed
echo.
echo ERROR: The Python environment could not be prepared; review the errors above.
echo        Common causes: no network access to PyPI or GitHub, or a proxy that
echo        blocks Git over HTTPS ^(the four HornLab modules install from Git^).
echo        Retry a clean build with:
echo          "%BOOTSTRAP_PYTHON%" scripts\bootstrap.py --force
exit /b 1

:no_solve_backend
echo.
echo ERROR: No solve backend is usable, so simulations would all fail.
echo        Fix the reason reported above, then run this installer again.
exit /b 1
