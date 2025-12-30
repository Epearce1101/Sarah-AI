@echo off
cd /d "%~dp0"
echo [SarahVision] Building sarahvision.exe...

REM 1) Make sure pyinstaller is installed in your venv:
REM    pip install pyinstaller

pyinstaller --onefile --noconsole -n sarahvision sarahvision.py

if not exist "dist\sarahvision.exe" (
    echo [SarahVision] Build FAILED. Check PyInstaller output.
    pause
    goto :eof
)

copy /Y "dist\sarahvision.exe" ".\sarahvision.exe" >nul
echo [SarahVision] sarahvision.exe created in:
echo    %~dp0
pause
