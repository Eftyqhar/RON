@echo off
cd /d "%~dp0"
echo Registering R.O.N. in Windows Startup (shell:startup)...
python startup_manager.py enable
echo.
echo [DONE] R.O.N. will now automatically start silently on Windows boot!
echo.
pause
