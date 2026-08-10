@echo off
rem Public Windows uninstaller entry. The implementation stays in scripts\.
setlocal EnableExtensions DisableDelayedExpansion
set "WG_ROOT=%~dp0..\.."
for %%i in ("%WG_ROOT%") do set "WG_ROOT=%%~fi"
call "%WG_ROOT%\scripts\uninstall.bat" %*
exit /b %ERRORLEVEL%
