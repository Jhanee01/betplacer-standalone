"""
Beépített önfrissítő — GitHub Release alapú, csak stdlib (urllib).

A repó PUBLIKUS, ezért NINCS token: a kiadások listája és az asset is
authentikáció nélkül letölthető. Minden hálózati hibát elnyelünk és None-t /
False-t adunk vissza — a Frissítés gomb sosem dobhat crasht a felhasználónál.

Flow:
  1) check_latest_release()  → a legújabb kiadás adatai (tag, név, leírás, asset-URL)
  2) is_newer(current, tag)  → kell-e frissíteni
  3) download_update(url, dst) → update.zip letöltése
  4) apply_update_and_restart() → update_apply.bat indítása, majd a hívó kilép
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from config import (
    APP_VERSION, GITHUB_UPDATE_OWNER, GITHUB_UPDATE_REPO, UPDATE_ASSET_NAME,
)
from paths import APP_DIR

_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_UPDATE_OWNER}/{GITHUB_UPDATE_REPO}"
    "/releases/latest"
)
_UA = {"User-Agent": "betplacer-updater", "Accept": "application/vnd.github+json"}


# ── Verzió-összehasonlítás ────────────────────────────────────────────────────

def _ver_tuple(v: str):
    """'v1.2.3' / '1.2.3' → (1, 2, 3). Nem-szám tagokat 0-ra esik vissza."""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(current: str, latest: str) -> bool:
    """True, ha a `latest` verzió újabb, mint a `current`."""
    return _ver_tuple(latest) > _ver_tuple(current)


# ── GitHub API ────────────────────────────────────────────────────────────────

def check_latest_release():
    """
    A legújabb kiadás adatai, vagy None (ha nincs net / nincs kiadás / hiba).

    Visszaad: {
        "tag": "v1.0.1",
        "name": "v1.0.1",
        "body": "release notes...",
        "asset_url": "https://github.com/.../download/v1.0.1/update.zip",
    }
    """
    try:
        req = urllib.request.Request(_API_LATEST, headers=_UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    tag = data.get("tag_name") or ""
    if not tag:
        return None

    asset_url = ""
    for asset in data.get("assets", []):
        if asset.get("name") == UPDATE_ASSET_NAME:
            asset_url = asset.get("browser_download_url", "")
            break

    return {
        "tag":       tag,
        "name":      data.get("name") or tag,
        "body":      data.get("body") or "",
        "asset_url": asset_url,
    }


def download_update(asset_url: str, dest: Path) -> bool:
    """Az update.zip letöltése `dest`-be. True, ha sikerült."""
    if not asset_url:
        return False
    try:
        req = urllib.request.Request(asset_url, headers={"User-Agent": "betplacer-updater"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        return False


# ── Frissítés alkalmazása ─────────────────────────────────────────────────────

def apply_update_and_restart(zip_path: Path) -> bool:
    """
    Elindítja az update_apply.bat-ot (új ablakban), ami megvárja a program
    bezárását, kicsomagolja a kódot, ráírja az APP_DIR-re, majd újraindít.
    A hívónak közvetlenül EZUTÁN ki kell lépnie, hogy a fájlok cserélhetők
    legyenek. True, ha a helper elindult.
    """
    bat = APP_DIR / "update_apply.bat"
    if not bat.exists():
        return False
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(bat), str(zip_path), str(os.getpid())],
            cwd=str(APP_DIR),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True
    except Exception:
        return False
