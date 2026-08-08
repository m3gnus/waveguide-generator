@echo off
rem Waveguide Generator v2 -- uninstaller for Windows.
rem
rem   scripts\uninstall.bat          remove the environment and the interface
rem   scripts\uninstall.bat --data   also remove saved designs and job history
rem
rem It never deletes the checkout, and it never touches anything belonging to
rem v1. v2 was built to install beside v1 with its own data directory; an
rem uninstaller that reached outside its own would make that promise a lie.
rem
rem Delayed expansion off, flat control flow, every path quoted. See the header
rem of install.bat for why.
setlocal EnableExtensions DisableDelayedExpansion

set "REMOVE_DATA="
set "ASSUME_YES="
set "DATA_DIR="
set "REMOVED_ANY="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--data" goto arg_data
if /I "%~1"=="--yes" goto arg_yes
if /I "%~1"=="-y" goto arg_yes
if /I "%~1"=="--help" goto show_usage
if /I "%~1"=="-h" goto show_usage
echo ERROR: Unknown option: %~1
exit /b 1
:arg_data
set "REMOVE_DATA=1"
shift
goto parse_args
:arg_yes
set "ASSUME_YES=1"
shift
goto parse_args

:show_usage
echo Waveguide Generator v2 uninstaller.
echo.
echo   --data   also remove the application data directory: saved designs, job
echo            history, meshes and logs. This cannot be undone.
echo   --yes    do not ask for confirmation.
echo   --help   this message
echo.
echo Without --data only the repository-local build products are removed:
echo .venv and frontend\dist. The checkout itself is left alone -- delete the
echo folder yourself when you no longer want it.
exit /b 0

:args_done
cd /d "%~dp0.."
if errorlevel 1 goto bad_directory
set "WG_ROOT=%CD%"

if not defined REMOVE_DATA goto data_dir_done
call :resolve_data_dir
if defined DATA_DIR goto data_dir_done
echo WARNING: could not ask v2 where its data directory is -- no usable Python.
echo          Remove it by hand. Unless WG2_DATA_DIR overrides it, it is:
echo            %APPDATA%\WaveguideGenerator2
echo.
:data_dir_done

set "HAS_TARGETS="
if exist ".venv\" set "HAS_TARGETS=1"
if exist "frontend\dist\" set "HAS_TARGETS=1"
if exist "frontend\.wg2-spa-staging\" set "HAS_TARGETS=1"
if exist "frontend\.wg2-dist-previous\" set "HAS_TARGETS=1"
if defined DATA_DIR set "HAS_TARGETS=1"
if not defined HAS_TARGETS goto nothing_to_remove

echo This will permanently delete:
if exist ".venv\" echo   %WG_ROOT%\.venv
if exist "frontend\dist\" echo   %WG_ROOT%\frontend\dist
if exist "frontend\.wg2-spa-staging\" echo   %WG_ROOT%\frontend\.wg2-spa-staging
if exist "frontend\.wg2-dist-previous\" echo   %WG_ROOT%\frontend\.wg2-dist-previous
if defined DATA_DIR echo   %DATA_DIR%
echo.
echo The checkout itself is kept. Delete "%WG_ROOT%" yourself when you are done.
echo.

if defined ASSUME_YES goto confirmed
set "ANSWER="
set /p "ANSWER=Type 'remove' to continue: "
if /I not "%ANSWER%"=="remove" goto aborted

:confirmed
if exist ".venv\" call :remove_tree "%WG_ROOT%\.venv"
if exist "frontend\dist\" call :remove_tree "%WG_ROOT%\frontend\dist"
if exist "frontend\.wg2-spa-staging\" call :remove_tree "%WG_ROOT%\frontend\.wg2-spa-staging"
if exist "frontend\.wg2-dist-previous\" call :remove_tree "%WG_ROOT%\frontend\.wg2-dist-previous"
if defined DATA_DIR call :remove_tree "%DATA_DIR%"
echo.
echo Done. Reinstall any time with: scripts\install-and-update.bat
exit /b 0

:nothing_to_remove
echo Nothing to remove: Waveguide Generator v2 is not installed in "%WG_ROOT%".
exit /b 0

:aborted
echo Nothing was removed.
exit /b 1

:remove_tree
if not exist "%~1" exit /b 0
rd /s /q "%~1"
if exist "%~1" goto remove_failed
echo   Removed %~1
set "REMOVED_ANY=1"
exit /b 0
:remove_failed
echo   WARNING: could not remove %~1
echo            Close anything using it -- an editor, a terminal sitting in it,
echo            a running server, or an antivirus scan -- and try again.
exit /b 0

:resolve_data_dir
rem Ask v2 itself rather than restating the rule here. The answer depends on the
rem platform and on WG2_DATA_DIR, and a second copy of that logic would drift
rem from server\platform\paths.py the first time either moved.
if exist ".venv\Scripts\python.exe" call :ask_python ".venv\Scripts\python.exe"
if defined DATA_DIR exit /b 0
where py >nul 2>&1
if not errorlevel 1 call :ask_python_launcher
if defined DATA_DIR exit /b 0
where python >nul 2>&1
if not errorlevel 1 call :ask_python "python"
exit /b 0

:ask_python
for /f "delims=" %%d in ('"%~1" -c "import sys; sys.path.insert(0, sys.argv[1]); from server.platform.paths import resolve_data_dir; print(resolve_data_dir())" "%WG_ROOT%" 2^>nul') do if not defined DATA_DIR set "DATA_DIR=%%d"
exit /b 0

:ask_python_launcher
for /f "delims=" %%d in ('py -3.13 -c "import sys; sys.path.insert(0, sys.argv[1]); from server.platform.paths import resolve_data_dir; print(resolve_data_dir())" "%WG_ROOT%" 2^>nul') do if not defined DATA_DIR set "DATA_DIR=%%d"
exit /b 0

:bad_directory
echo ERROR: Could not enter the project folder.
exit /b 1
