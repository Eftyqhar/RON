@echo off
cd /d "%~dp0"
title R.O.N. HUD
echo.
echo   Starting R.O.N. holographic interface...
echo.
REM --browser app opens a frameless fullscreen Chromium window.
REM Add --no-voice to run the HUD without the microphone loop.
python ui_server.py %*
pause
