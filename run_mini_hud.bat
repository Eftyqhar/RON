@echo off
cd /d "%~dp0"

REM If run with --console flag, keep interactive console open for debugging
if "%1"=="--console" goto :console
if "%1"=="-c" goto :console

REM Silent Background Launch (No Command Prompt / Zero Terminal Window)
start "" wscript.exe "%~dp0run_ron_silent.vbs"
exit /b

:console
title R.O.N. Mini-HUD Console
echo ===================================================
echo   R.O.N. Mini-HUD — Debug Console Mode
echo ===================================================
python run_mini_hud.py %*
pause
