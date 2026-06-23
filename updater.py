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
import socket
import ssl
import subprocess
import sys
import urllib.error
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
# A kiadások böngészőből is elérhető oldala — tartalék letöltéshez (ha a Python
# valamiért nem éri el az API-t, de a böngésző igen).
RELEASE_PAGE_URL = (
    f"https://github.com/{GITHUB_UPDATE_OWNER}/{GITHUB_UPDATE_REPO}/releases/latest"
)
_UA = {"User-Agent": "betplacer-updater", "Accept": "application/vnd.github+json"}

# A legutóbbi hálózati hiba EMBERI nyelven (a GUI ezt mutatja a "nincs internet"
# helyett). None, ha az utolsó hívás sikeres volt.
_LAST_ERROR = None


def last_error():
    """A legutóbbi check_latest_release() hibájának szövege, vagy None."""
    return _LAST_ERROR


# ── SSL: a Windows tanúsítványtár használata (mint a böngésző) ─────────────────

def _ssl_context():
    """
    SSL-kontextus, ami a Windows tanúsítványtárát is betölti — így a Python
    ugyanazokat a gyökértanúsítványokat fogadja el, mint a böngésző. Ez oldja
    meg a vírusirtó / céges proxy HTTPS-szkennelése okozta CERTIFICATE_VERIFY_FAILED
    hibát (a böngésző megy, a Python certifi-je viszont nem ismeri az AV gyökerét).
    """
    ctx = ssl.create_default_context()
    try:
        # Additív: a certifi mellé a Windows "ROOT"/"CA" tárakat is betölti.
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
    except Exception:
        pass
    return ctx


def _classify_error(exc) -> str:
    """Egy kivételt EMBERI hibaszöveggé alakít a felhasználónak."""
    # A HTTP-hibát kezeljük előbb: a HTTPError is URLError-leszármazott, de a
    # .reason-ja itt a státuszszöveg, nem a becsomagolt belső kivétel.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return ("A GitHub ideiglenesen korlátozta a kéréseket (rate limit). "
                    "Várj pár percet, vagy tölts le böngészőből.")
        if exc.code == 404:
            return "A kiadás nem található a GitHubon (404)."
        return f"GitHub HTTP-hiba: {exc.code}."

    # urllib az SSL/socket hibákat URLError-ba CSOMAGOLJA — a valódi ok a .reason.
    inner = getattr(exc, "reason", None)
    cert = next((e for e in (exc, inner)
                 if isinstance(e, ssl.SSLCertVerificationError)), None)
    if cert is not None:
        return ("Tanúsítvány-hiba (SSL) — valószínűleg egy vírusirtó vagy céges "
                "proxy szkenneli a HTTPS-t ezen a gépen. A böngésző működik, a "
                "program viszont nem éri el a GitHubot. Töltsd le böngészőből. "
                f"(részletek: {cert})")
    if isinstance(exc, ssl.SSLError) or isinstance(inner, ssl.SSLError):
        return (f"SSL-hiba a GitHub-kapcsolatban: {inner or exc}. "
                "Töltsd le böngészőből.")

    if isinstance(exc, (socket.timeout, TimeoutError)) or \
            isinstance(inner, (socket.timeout, TimeoutError)):
        return "Időtúllépés — a GitHub nem válaszolt időben."
    if isinstance(exc, urllib.error.URLError):
        return ("Nem sikerült csatlakozni a GitHubhoz — tűzfal, proxy vagy "
                f"vírusirtó blokkolhatja a programot. Töltsd le böngészőből. "
                f"(részletek: {inner or exc})")
    return f"Ismeretlen hiba: {exc}"


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
    global _LAST_ERROR
    try:
        req = urllib.request.Request(_API_LATEST, headers=_UA)
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _LAST_ERROR = None
    except Exception as exc:
        _LAST_ERROR = _classify_error(exc)
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
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp, open(dest, "wb") as f:
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
