@echo off
rem Включает/выключает автозапуск при входе в Windows.
rem Ярлык ведёт прямо на pythonw.exe, поэтому окно консоли не мелькает.
chcp 65001 >nul
setlocal
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\VocabPopup.lnk"

if exist "%LNK%" (
    del "%LNK%"
    echo Avtozapusk VYKLYUCHEN.
    goto :end
)

for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    set "PYW=%%P"
    goto :found
)
echo Ne nashel pythonw.exe v PATH. Ustanovite Python ili zapuskayte start.bat vruchnuyu.
goto :end

:found
powershell -NoProfile -Command ^
  "$s=New-Object -ComObject WScript.Shell;" ^
  "$l=$s.CreateShortcut('%LNK%');" ^
  "$l.TargetPath='%PYW%';" ^
  "$l.Arguments='main.py';" ^
  "$l.WorkingDirectory='%~dp0';" ^
  "$l.WindowStyle=7;" ^
  "$l.Description='Vocab Popup - kartochki angliyskih slov';" ^
  "$l.Save()"
echo Avtozapusk VKLYUCHEN. Programma budet startovat pri vhode v Windows.

:end
echo.
pause
