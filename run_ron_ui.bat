@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title R.O.N. Holographic HUD Server

echo.
echo  ==============================================================
echo    R.O.N. HOLOGRAPHIC INTELLIGENCE INTERFACE
echo  ==============================================================
echo.

REM Prevent background window hiding so status and logs stay visible
set RON_SHOW_CONSOLE=1

REM --- 1. FAST PORT CHECK: Is R.O.N. HUD already running? ---
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8765); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   [OK] R.O.N. HUD is already running on http://127.0.0.1:8765/
    echo   Activating holographic interface in browser...
    echo.
    start "" http://127.0.0.1:8765/
    timeout /t 2 >nul
    exit /b 0
)

REM --- 2. PYTHON RUNTIME RESOLUTION ---
set "PYTHON_EXE=python"
where %PYTHON_EXE% >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    REM Try py launcher
    where py >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "PYTHON_EXE=py -3"
    ) else (
        REM Search common Windows Python paths
        for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
            if exist "%%D\python.exe" set "PYTHON_EXE=%%D\python.exe"
        )
        if "!PYTHON_EXE!"=="python" (
            for /d %%D in ("%ProgramFiles%\Python3*") do (
                if exist "%%D\python.exe" set "PYTHON_EXE=%%D\python.exe"
            )
        )
    )
)

REM Verify chosen Python executable works
%PYTHON_EXE% --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   [ERROR] Python 3 was not found in your system PATH!
    echo   Please install Python 3.10+ from python.org and check
    echo   "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM --- 3. LAUNCH HUD SERVER ---
echo   [OK] Starting HUD server and launching browser...
echo.

%PYTHON_EXE% ui_server.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   [ALERT] R.O.N. HUD exited with status code %ERRORLEVEL%.
    pause
)
