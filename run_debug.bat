@echo off
cd /d "%~dp0"
title BetPlacer (hibakereso konzol)
echo ============================================
echo  BetPlacer Standalone - Indul...
echo ============================================
echo.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" main.py %*
echo.
echo ============================================
echo  A script leállt. Nyomj egy gombot...
echo ============================================
pause
