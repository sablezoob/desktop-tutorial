@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Ustanovka zavisimostey...
python -m pip install -r requirements.txt
if errorlevel 1 goto :err
echo.
echo Zagruzka startovogo slovarya...
python seed.py
echo.
echo Gotovo. Zapuskayte start.bat
goto :end
:err
echo.
echo Ne udalos ustanovit zavisimosti. Proverte, chto Python est v PATH.
:end
pause
