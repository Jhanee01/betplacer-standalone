"""
BetPlacer indító — vékony exe-burkoló.

Egyetlen célja: focis ikon + dupla-kattintásos indítás KONZOLABLAK NÉLKÜL.
NEM csomagol függőséget — a Python és a csomagok a gépen vannak (install.bat).
A rendszer Pythonjával (pythonw) indítja a `main.py`-t, majd azonnal kilép.

PyInstaller build (lásd build_exe.bat):
    pyinstaller --onefile --windowed --icon assets/icon.ico --name BetPlacer launcher.py
"""

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _app_dir() -> Path:
    """Az exe (vagy forrás) mappája — itt van a main.py és a .env."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _find_pythonw():
    """A konzol nélküli Python keresése. None, ha nincs telepítve."""
    # 1) pythonw a PATH-on — ugyanaz a telepítés, amivel az install.bat
    #    a csomagokat telepítette ("Add Python to PATH").
    for name in ("pythonw.exe", "pythonw"):
        p = shutil.which(name)
        if p:
            return p
    # 2) Windows Python Launcher, ablakos változat.
    p = shutil.which("pyw")
    if p:
        return p
    return None


def _error(msg: str):
    """Hibaüzenet ablakban (a windowed exe-nek nincs konzolja)."""
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "BetPlacer", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def main():
    app = _app_dir()
    target = app / "main.py"
    if not target.exists():
        _error(f"Nem találom a main.py-t:\n{target}\n\n"
               "A BetPlacer.exe-t a program mappájában kell tartani.")
        return

    pyw = _find_pythonw()
    if pyw is None:
        _error("A Python nem található a gépen.\n\n"
               "Először futtasd az install.bat-ot (Python + függőségek "
               "telepítése), majd indítsd újra a BetPlacer.exe-t.")
        return

    try:
        subprocess.Popen([pyw, str(target)], cwd=str(app),
                         creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        _error(f"Nem sikerült elindítani a BetPlacert:\n{e}")


if __name__ == "__main__":
    main()
