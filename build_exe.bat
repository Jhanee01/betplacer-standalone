@echo off
setlocal
cd /d "%~dp0"
title BetPlacer - Indito exe buildelese

rem ============================================================================
rem  FEJLESZTOI eszkoz - a BetPlacer.exe indito legyartasa (PyInstaller).
rem  Az exe NEM csomagol fuggoseget - csak a main.py-t inditja konzol nelkul,
rem  a focis ikonnal. A keszult exe a mappa gyokeret kapja (BetPlacer.exe).
rem  Kell hozza: pip install pyinstaller
rem ============================================================================

echo PyInstaller build indul...
python -m PyInstaller --onefile --windowed --noconfirm ^
  --icon "assets\icon.ico" --name BetPlacer launcher.py
if errorlevel 1 (
    echo HIBA: a build sikertelen.
    pause
    exit /b 1
)

echo.
echo Exe atmasolasa a mappa gyokerebe...
copy /y "dist\BetPlacer.exe" "BetPlacer.exe" >NUL

echo.
echo ============================================
echo  KESZ: BetPlacer.exe
echo ============================================
pause
endlocal
