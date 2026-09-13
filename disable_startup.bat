@echo off
cd /d "%~dp0"
echo Removing R.O.N. from Windows Startup...
python startup_manager.py disable
echo.
echo [DONE] R.O.N. auto-startup has been disabled.
echo.
pause
