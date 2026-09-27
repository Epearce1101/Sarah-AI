@echo off
REM Launch Sarah AI. The launcher runs in its own console, minimized to the
REM taskbar as "Sarah V10 - running": it shows the live backend/UI log and
REM its title shows status. Closing that console shuts Sarah down.
if /i not "%~1"=="--console" (
    start "Sarah V10 - running" /min cmd /c ""%~f0" --console"
    exit /b
)
title Sarah V10 - running
"%~dp0Backend\.venv\Scripts\python.exe" "%~dp0start.py"
if errorlevel 1 (
    echo.
    echo Sarah stopped with an error - see the log above.
    pause
)
