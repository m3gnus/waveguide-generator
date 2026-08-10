@echo off
rem Waveguide Generator v2 -- the Windows entry point. Double-click this file.
rem
rem The implementation lives in install.bat, which is NOT executed in place.
rem It performs `git pull`, which can rewrite install.bat while cmd.exe is still
rem executing it. cmd tracks its position in a batch file by byte offset and
rem re-reads the file as it goes, so a script that updates itself resumes at a
rem meaningless offset and runs fragments of unrelated lines. v1's symptom was:
rem
rem     The system cannot find the batch label specified - update_from_git
rem     'ttps:' is not recognized as an internal or external command
rem
rem ("ttps:" being the tail of an https:// URL from a later line.) So: copy the
rem installer to %TEMP%, run the copy, and pass the repository root explicitly.
rem git may then rewrite the repository freely. Exit code 10 means "code was
rem updated, relaunch with the new installer", which we do from a fresh copy.
rem
rem Delayed expansion is off and control flow is flat, so that a repository
rem under "C:\My ! Projects (2)" neither loses characters nor closes a block
rem early. See the header of install.bat.
setlocal EnableExtensions DisableDelayedExpansion

set "WG_ROOT=%~dp0..\.."
for %%i in ("%WG_ROOT%") do set "WG_ROOT=%%~fi"
set "WG_ARGS=%*"

rem Did the caller ask us not to start the app? Detected by substring
rem replacement rather than `echo %WG_ARGS% | find`, because the piped form
rem re-parses the arguments as command text. `set "X=%*"` with no arguments
rem *undefines* X, so an unguarded comparison would leave both sides
rem unexpanded, find them unequal, and silently never launch.
set "WG_NO_LAUNCH_REQUESTED="
if not defined WG_ARGS goto args_scanned
if not "%WG_ARGS:--no-launch=%"=="%WG_ARGS%" set "WG_NO_LAUNCH_REQUESTED=1"
:args_scanned

if not exist "%WG_ROOT%\scripts\install.bat" goto missing_installer

rem Keep a transcript. Double-clicking a .bat gives a console that closes on
rem exit, so without this the failure a user needs to report is simply gone.
rem Default first, then improve on it. An undefined %APPDATA% expands to itself
rem in a batch file rather than to nothing, so building the good path first and
rem correcting it afterwards would depend on the order of two lines to avoid
rem creating a directory literally named "%APPDATA%".
set "WG_LOG_DIR=%TEMP%"
if defined APPDATA set "WG_LOG_DIR=%APPDATA%\WaveguideGenerator2\logs"
if not exist "%WG_LOG_DIR%" mkdir "%WG_LOG_DIR%" >nul 2>&1
if not exist "%WG_LOG_DIR%" set "WG_LOG_DIR=%TEMP%"
set "WG_LOG=%WG_LOG_DIR%\install.log"
echo Logging to: %WG_LOG%
echo.

call :run_installer %WG_ARGS%
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="10" goto report

call :run_installer --after-pull %WG_ARGS%
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="10" goto report
echo.
echo ERROR: The installer reported a second code update immediately after
echo        updating. Stopping to avoid an update loop.
echo        Run "git status" in "%WG_ROOT%" and run this script again.
set "RESULT=1"

:report
echo.
if not "%RESULT%"=="0" goto failed
echo Install log: %WG_LOG%
if defined WG_NO_LAUNCH_REQUESTED goto finish
rem install.bat was told --no-launch so that the server does not run inside the
rem transcript pipeline below, where its output would be teed into a growing
rem log file for as long as it stays up. Start it here instead, unpiped.
echo.
echo Starting Waveguide Generator v2...
call "%WG_ROOT%\launchers\windows\launch-wg2.bat"
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
rem Exit 2 is the documented "another instance already owns the lock" answer,
rem not a failure: serve.py has already opened the browser on the running one.
if "%RESULT%"=="2" exit /b 2
rem Anything else goes through :finish so the window stays open long enough to
rem read. launchers\windows\launch-wg2.bat's own pause tests whether cmd's command line names
rem *itself*, which it does not when it is reached through this script.
goto finish

:failed
echo ===============================================================
echo Install failed with error code %RESULT%.
echo Full log: %WG_LOG%
echo ===============================================================
echo Include that log when reporting the problem. It contains no
echo credentials; it does contain the project path.

:finish
rem Only pause when launched by double-click. When a console was already
rem attached, cmdcmdline contains the script path with /c, and pausing would
rem block a scripted or CI run.
echo %CMDCMDLINE% | find /i "%~nx0" >nul 2>&1
if not errorlevel 1 pause
exit /b %RESULT%

:missing_installer
echo ERROR: Could not find scripts\install.bat in the project checkout.
echo        Expected project folder: %WG_ROOT%
exit /b 1

:run_installer
set "WG_TMP_INSTALLER=%TEMP%\wg2-install-%RANDOM%%RANDOM%.bat"
copy /y "%WG_ROOT%\scripts\install.bat" "%WG_TMP_INSTALLER%" >nul
if errorlevel 1 goto stage_failed
rem Preserve each caller argument as one environment value. Interpolating %%*
rem into the PowerShell program would discard cmd's grouping quotes around a
rem value containing spaces and would let PowerShell parse metacharacters in it.
set "WG_INSTALL_ARG_COUNT=0"
:collect_installer_args
if "%~1"=="" goto installer_args_ready
set "WG_INSTALL_ARG_%WG_INSTALL_ARG_COUNT%=%~1"
set /a WG_INSTALL_ARG_COUNT+=1 >nul
shift
goto collect_installer_args
:installer_args_ready
rem Show progress live AND keep a full transcript. A plain cmd pipe takes its
rem ERRORLEVEL from the right-hand side, destroying the installer's exit code --
rem which the exit-10 relaunch depends on. PowerShell's Tee-Object preserves
rem $LASTEXITCODE from the called script, and PowerShell ships with Windows, so
rem this adds no dependency.
rem
rem Paths reach PowerShell through the environment, never interpolated by cmd:
rem '%WG_ROOT%' would be substituted before PowerShell ever parsed the line, so
rem a path containing a quote or an apostrophe would rewrite the script.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wgArgs = @(for ($i = 0; $i -lt [int]$env:WG_INSTALL_ARG_COUNT; $i++) { [Environment]::GetEnvironmentVariable('WG_INSTALL_ARG_' + $i) }); & $env:WG_TMP_INSTALLER --root $env:WG_ROOT --no-launch @wgArgs 2>&1 | Tee-Object -FilePath $env:WG_LOG -Append; exit $LASTEXITCODE"
set "RUN_RESULT=%ERRORLEVEL%"
del "%WG_TMP_INSTALLER%" >nul 2>&1
exit /b %RUN_RESULT%

:stage_failed
echo ERROR: Could not stage the installer in "%TEMP%".
echo        Check that it exists and is writable.
exit /b 1
