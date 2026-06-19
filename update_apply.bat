@echo off
setlocal
title BetPlacer - Frissites

rem ============================================================================
rem  BetPlacer onfrissito segedscript.
rem  A program (updater.py) hivja meg igy:
rem      update_apply.bat  <update.zip eleresi ut>  <futo program PID-je>
rem  Feladata: megvarja a program bezarasat, kicsomagolja az uj kodot, rairja
rem  az alkalmazas mappajara (a .env / *.session / logs erintetlen marad), majd
rem  ujrainditja a programot.
rem ============================================================================

set "ZIP=%~1"
set "PID=%~2"
set "APP=%~dp0"
set "TMPDIR=%APP%_update_tmp"

echo.
echo  Frissites elokeszitese...
echo  (Ne zard be ezt az ablakot - par masodperc.)
echo.

rem -- 1) Megvarjuk, amig a futo program (PID) bezarodik -----------------------
if not "%PID%"=="" (
    echo  Varakozas a program bezarasara...
:waitloop
    tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
    if not errorlevel 1 (
        timeout /t 1 /nobreak >NUL
        goto waitloop
    )
)
rem Biztonsagi puffer, hogy a fajlzarak feloldodjanak.
timeout /t 2 /nobreak >NUL

rem -- 2) Regi temp takaritas, majd kicsomagolas -------------------------------
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
echo  Kicsomagolas...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%TMPDIR%' -Force"
if not exist "%TMPDIR%" (
    echo.
    echo  HIBA: a frissitocsomag kicsomagolasa sikertelen.
    echo  A program valtozatlan maradt. Probald ujra kesobb.
    pause
    exit /b 1
)

rem -- 3) Uj kod rairasa (titkok / session / logok kihagyva) -------------------
echo  Uj verzio telepitese...
robocopy "%TMPDIR%" "%APP%." /E /XF .env *.session /XD logs __pycache__ _update_tmp >NUL

rem -- 4) Takaritas ------------------------------------------------------------
rmdir /s /q "%TMPDIR%" 2>NUL
del /q "%ZIP%" 2>NUL

rem -- 5) Ujrainditas ----------------------------------------------------------
echo  Kesz! A BetPlacer ujraindul...
timeout /t 1 /nobreak >NUL
start "" "%APP%run.bat"

endlocal
exit /b 0
