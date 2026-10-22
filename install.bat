@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  BetPlacer Standalone - Telepites
echo ============================================
echo.
echo FONTOS: Ne zard be ezt az ablakot!
echo         A telepites 5-15 percet is eltarthat.
echo.

rem ============================================================================
rem  A program a sajat mappajaban levo .venv-bol fut, egy TAMOGATOTT Pythonnal
rem  (3.11-3.13). A tul uj Python (pl. 3.15) alatt a Playwright nem indul
rem  ("'lazy_import' object is not callable") - ezert ha a gepen nincs megfelelo
rem  verzio, a telepito maga letolti es felrakja a Python 3.13-at (csak a
rem  felhasznalonak, admin jog nelkul, a meglevo Pythont nem bantja).
rem ============================================================================
set "PY_VER=3.13.12"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PY_LOCAL=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "BASE_PY="

echo [1/4] Megfelelo Python keresese (3.11 - 3.13)...

rem -- Mar meglevo, jo .venv: ujrahasznaljuk -----------------------------------
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
    if not errorlevel 1 goto venv_ready
    echo       A regi .venv nem megfelelo verzioju - ujra letrehozom.
    rmdir /s /q "%~dp0.venv"
)

rem -- Telepitett Pythonok: py launcher, korabban felrakott 3.13, PATH ---------
for %%v in (3.13 3.12 3.11) do (
    if not defined BASE_PY (
        py -%%v -c "import sys" >nul 2>&1
        if not errorlevel 1 set "BASE_PY=py -%%v"
    )
)
if not defined BASE_PY if exist "%PY_LOCAL%" set "BASE_PY="%PY_LOCAL%""
if not defined BASE_PY (
    python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
    if not errorlevel 1 set "BASE_PY=python"
)
if defined BASE_PY goto make_venv

rem -- Nincs megfelelo Python: letoltjuk es felrakjuk a 3.13-at ---------------
echo       Nincs megfelelo Python a gepen - Python %PY_VER% telepitese...
echo       (letoltes ~27 MB, utana csendes telepites, 1-3 perc)
set "PY_SETUP=%TEMP%\python-%PY_VER%-amd64.exe"
curl.exe -L --fail -o "%PY_SETUP%" "%PY_URL%"
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%'"
)
if not exist "%PY_SETUP%" (
    echo.
    echo HIBA: a Python letoltese sikertelen. Ellenorizd az internetkapcsolatot,
    echo vagy telepitsd kezzel a Python 3.13-at: https://www.python.org/downloads/release/python-31312/
    pause
    exit /b 1
)
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Shortcuts=0
del /q "%PY_SETUP%" 2>nul
if not exist "%PY_LOCAL%" (
    echo.
    echo HIBA: a Python telepitese sikertelen.
    echo Telepitsd kezzel a Python 3.13-at: https://www.python.org/downloads/release/python-31312/
    pause
    exit /b 1
)
set "BASE_PY="%PY_LOCAL%""

:make_venv
for /f "delims=" %%v in ('%BASE_PY% --version') do echo       Hasznalt Python: %%v
echo       Sajat kornyezet letrehozasa (.venv)...
%BASE_PY% -m venv "%~dp0.venv"
if not exist "%VENV_PY%" (
    echo.
    echo HIBA: a .venv letrehozasa sikertelen.
    pause
    exit /b 1
)

:venv_ready
for /f "delims=" %%v in ('"%VENV_PY%" --version') do echo [1/4] OK - %%v
echo.

echo [2/4] Python csomagok telepitese... (1-2 perc)
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo HIBA: pip install sikertelen!
    pause
    exit /b 1
)
"%VENV_PY%" -c "import PySide6, qt_material, telethon, playwright, dotenv, fastapi, uvicorn"
if errorlevel 1 (
    echo.
    echo HIBA: a csomagok telepitese utan sem importalhatok!
    echo Python: %VENV_PY%
    pause
    exit /b 1
)
echo [2/4] OK
echo.

echo [3/4] Playwright Chromium letoltese... (5-10 perc, ~150 MB)
echo       Az ablak lefagyottnak tunhet - ez normal, varj!
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo HIBA: playwright install sikertelen!
    pause
    exit /b 1
)
echo [3/4] OK
echo.

echo [4/4] Kesz.
echo.
echo ============================================
echo  TELEPITES KESZ! Bezarhatod ezt az ablakot.
echo ============================================
echo.
echo Inditashoz kattints duplan a BetPlacer.exe (vagy a run.bat) fajlra.
echo.
pause
endlocal
