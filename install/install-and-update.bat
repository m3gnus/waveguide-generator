@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: Clearer entry point for users. The implementation lives in install.bat.
::
:: install.bat is NOT executed in place. It performs `git pull`, which can
:: rewrite install.bat while cmd.exe is still executing it. cmd.exe tracks its
:: position in a batch file by byte offset and re-reads the file as it goes, so
:: a script that updates itself resumes at a meaningless offset and runs
:: fragments of unrelated lines. The observed symptom was:
::
::     The system cannot find the batch label specified - update_from_git
::     'ttps:' is not recognized as an internal or external command
::
:: ("ttps:" is the tail of an https:// URL from a later line.) So: copy the
:: installer to %TEMP% and run the copy, passing the repository root. git may
:: then rewrite the repository freely. Exit code 10 means "code was updated,
:: relaunch with the new installer", which we do with a freshly taken copy.

set "WG_ROOT=%~dp0.."
for %%i in ("%WG_ROOT%") do set "WG_ROOT=%%~fi"

if not exist "%WG_ROOT%\install\install.bat" (
    echo ERROR: Could not find install\install.bat next to this script.
    echo Expected project folder: %WG_ROOT%
    exit /b 1
)

call :run_installer
set "RESULT=%ERRORLEVEL%"

if "%RESULT%"=="10" (
    call :run_installer --after-pull
    set "RESULT=!ERRORLEVEL!"
    if "!RESULT!"=="10" (
        echo.
        echo ERROR: The installer reported a second code update immediately
        echo        after updating. Stopping to avoid an update loop.
        echo        Run "git status" in %WG_ROOT% and re-run this script.
        set "RESULT=1"
    )
)

exit /b %RESULT%

:run_installer
set "WG_TMP_INSTALLER=%TEMP%\wg-install-%RANDOM%%RANDOM%.bat"
copy /y "%WG_ROOT%\install\install.bat" "%WG_TMP_INSTALLER%" >nul
if errorlevel 1 (
    echo ERROR: Could not stage the installer in %TEMP%.
    echo        Check that %TEMP% is writable.
    exit /b 1
)
call "%WG_TMP_INSTALLER%" --root "%WG_ROOT%" %*
set "RUN_RESULT=%ERRORLEVEL%"
del "%WG_TMP_INSTALLER%" >nul 2>&1
exit /b %RUN_RESULT%
