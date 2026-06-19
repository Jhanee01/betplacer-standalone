@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title BetPlacer - Kiadas keszitese

rem ============================================================================
rem  FEJLESZTOI eszkoz - uj kiadas (release) keszitese.
rem  1) Kiolvassa az APP_VERSION-t a config.py-bol
rem  2) update.zip-et keszit a forrasbol (.env / *.session / logok kihagyva)
rem  3) gh release create v<VER> update.zip  (vagy felulirja, ha a tag letezik)
rem  Kell hozza: telepitett 'gh' CLI + bejelentkezve (gh auth login).
rem ============================================================================

rem -- Verzio a config.py-bol --------------------------------------------------
for /f "delims=" %%v in ('python -c "import config; print(config.APP_VERSION)"') do set "VER=%%v"
if "%VER%"=="" (
    echo HIBA: nem sikerult kiolvasni az APP_VERSION-t a config.py-bol.
    pause
    exit /b 1
)
echo Verzio: v%VER%
echo.

rem -- update.zip osszerakasa (csak a top-level forrasfajlok) -------------------
echo Csomag keszitese (update.zip)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$st='_release_staging';" ^
  "if(Test-Path $st){Remove-Item -Recurse -Force $st};" ^
  "New-Item -ItemType Directory $st | Out-Null;" ^
  "$excludeNames=@('.env','update.zip');" ^
  "$excludeExt=@('.pyc','.session');" ^
  "Get-ChildItem -File | Where-Object { $excludeNames -notcontains $_.Name -and $excludeExt -notcontains $_.Extension } | Copy-Item -Destination $st;" ^
  "if(Test-Path 'update.zip'){Remove-Item -Force 'update.zip'};" ^
  "Compress-Archive -Path ($st+'\*') -DestinationPath 'update.zip' -Force;" ^
  "Remove-Item -Recurse -Force $st"

if not exist "update.zip" (
    echo HIBA: az update.zip nem jott letre.
    pause
    exit /b 1
)
echo Csomag kesz: update.zip
echo.

rem -- Release kiadasa ---------------------------------------------------------
echo gh release create v%VER% ...
gh release create v%VER% update.zip --title "v%VER%" --notes "BetPlacer v%VER%"
if errorlevel 1 (
    echo.
    echo A tag valoszinuleg mar letezik - asset felulirasa --clobber-rel...
    gh release upload v%VER% update.zip --clobber
)

echo.
echo ============================================
echo  KESZ. Kiadva: v%VER%
echo ============================================
pause
endlocal
