"""
A BetPlacer adat-mappájának feloldása (.env, telegram_session, logs).

Forrásból futva: a standalone_betplacer mappa.
PyInstaller exe-ből futva: az exe mappája — hogy a beállítások, a Telegram-session
és a logok az exe MELLETT, látható helyen legyenek, ne a _internal bundle-ben
(amit egy újratelepítés felülírhat).
"""
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_dir()
