@echo off
rem ==========================================================================
rem  BetPlacer inditasa KONZOLABLAK NELKUL (pythonw) - csak a program ablaka
rem  latszik. Hibakereseshez (a kimenet egy nyitva marado ablakban):
rem  run_debug.bat
rem ==========================================================================
cd /d "%~dp0"
rem Az install.bat altal letrehozott sajat kornyezet (tamogatott Python verzio).
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py %*
    exit /b
)
where pythonw >NUL 2>&1
if errorlevel 1 (
    rem Nincs pythonw a PATH-on - a regi, konzolos modon indul.
    python main.py %*
    pause
    exit /b
)
start "" pythonw main.py %*
