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

:: Keep a log. Double-clicking a .bat gives a console that closes on exit, so
:: without this the failure a user needs to report is simply gone.
if not exist "%WG_ROOT%\.waveguide\logs" mkdir "%WG_ROOT%\.waveguide\logs" >nul 2>&1
set "WG_LOG=%WG_ROOT%\.waveguide\logs\install.log"
echo Logging to: %WG_LOG%
echo.

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

echo.
if "%RESULT%"=="0" (
    echo Install log: %WG_LOG%
) else (
    echo ===============================================================
    echo Install failed with error code %RESULT%.
    echo Full log: %WG_LOG%
    echo ===============================================================
    echo Include that log when reporting the problem. It contains no
    echo credentials; it does contain the project path.
)

:: Only pause when launched by double-click. When a console was already
:: attached, cmdcmdline contains the script path with /c and pausing would
:: block scripted or CI runs.
echo %CMDCMDLINE% | find /i "%~nx0" >nul 2>&1
if not errorlevel 1 pause

exit /b %RESULT%

:run_installer
set "WG_TMP_INSTALLER=%TEMP%\wg-install-%RANDOM%%RANDOM%.bat"
copy /y "%WG_ROOT%\install\install.bat" "%WG_TMP_INSTALLER%" >nul
if errorlevel 1 (
    echo ERROR: Could not stage the installer in %TEMP%.
    echo        Check that %TEMP% is writable.
    exit /b 1
)
:: Show progress live AND keep a full transcript. A plain cmd pipe would set
:: ERRORLEVEL from the right-hand side of the pipe, destroying the installer's
:: exit code -- which the exit-10 relaunch depends on. PowerShell's Tee-Object
:: keeps $LASTEXITCODE from the called script. PowerShell ships with Windows,
:: so this adds no dependency.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '%WG_TMP_INSTALLER%' --root '%WG_ROOT%' %* 2>&1 | Tee-Object -FilePath '%WG_LOG%' -Append; exit $LASTEXITCODE"
set "RUN_RESULT=%ERRORLEVEL%"
del "%WG_TMP_INSTALLER%" >nul 2>&1
exit /b %RUN_RESULT%
