@echo off
cd /d "%~dp0"

echo ============================================
echo  BetPlacer Standalone - Telepites
echo ============================================
echo.
echo FONTOS: Ne zard be ezt az ablakot!
echo         A telepites 5-15 percet is eltarthat.
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo HIBA: A Python nem talalhato!
    echo Telepitsd a Pythont: https://www.python.org/downloads/
    echo Fontos: pipald be az "Add Python to PATH" opciot!
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if %errorlevel% neq 0 (
    echo HIBA: Tul regi Python verzio - 3.11 vagy ujabb szukseges!
    for /f "delims=" %%v in ('python --version') do echo Jelenleg telepitve: %%v
    echo Toltsd le a legujabbat: https://www.python.org/downloads/
    echo Fontos: pipald be az "Add Python to PATH" opciot!
    echo.
    pause
    exit /b 1
)

echo [1/3] Python csomagok telepitese... (1-2 perc)
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo HIBA: pip install sikertelen!
    pause
    exit /b 1
)
echo [1/3] OK
echo.

echo [2/3] Playwright Chromium letoltese... (5-10 perc, ~150 MB)
echo       Az ablak lefagyottnak tunhet - ez normal, varj!
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo.
    echo HIBA: playwright install sikertelen!
    pause
    exit /b 1
)
echo [2/3] OK
echo.

echo ============================================
echo  TELEPITES KESZ! Bezarhatod ezt az ablakot.
echo ============================================
echo.
echo Inditashoz kattints duplán a run.bat fajlra.
echo.
pause
