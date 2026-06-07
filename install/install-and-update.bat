@echo off
:: Clearer entry point for users. The implementation lives in install.bat.

cd /d "%~dp0\.."
call install\install.bat
