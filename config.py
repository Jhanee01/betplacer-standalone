"""
Standalone BetPlacer — verzió és frissítési beállítások.

Itt él az alkalmazás verziószáma (semver) és a GitHub repó, ahonnan a
beépített Frissítés gomb az új kiadásokat keresi. A repó PUBLIKUS, ezért a
frissítés NEM igényel tokent — a felhasználó gépén semmit nem kell beállítani.
"""

import os
import secrets

from paths import APP_DIR

# Klasszikus semver. Minden kiadás előtt EZT kell emelni (lásd make_release.bat).
APP_VERSION = "2.1.1"

# GitHub repó, ahonnan a frissítés jön (owner/repo). Publikus → token nem kell.
GITHUB_UPDATE_OWNER = "Jhanee01"
GITHUB_UPDATE_REPO  = "betplacer-standalone"

# A kiadásokhoz csatolt frissítő-csomag fájlneve (make_release.bat ezt tölti fel).
UPDATE_ASSET_NAME = "update.zip"


# === Remote (mobil web-vezérlő) ===
# Mobilbarát web-felület a futó BetPlacerhez: élő napló + státusz + Indítás /
# Leállítás / Újraindítás telefonról, privát Tailscale-alagúton át. A futó
# program egy háttérszálán indul (lásd remote/server.py) — nincs külön folyamat.
# Token a biztonsági kapu: a telefonról csak helyes ?token=... mellett lehet
# csatlakozni. A .env-ben felülbírálható: RIM_REMOTE_HOST / RIM_REMOTE_PORT /
# RIM_REMOTE_ENABLED. Token nélkül (és ha nincs kikapcsolva) a program egyszer
# generál egyet, és a .env-be írja.
_ENV_PATH = APP_DIR / ".env"

RIM_REMOTE_HOST = os.getenv("RIM_REMOTE_HOST", "0.0.0.0").strip() or "0.0.0.0"
try:
    RIM_REMOTE_PORT = int(os.getenv("RIM_REMOTE_PORT", "8765"))
except ValueError:
    RIM_REMOTE_PORT = 8765


def remote_off() -> bool:
    """A felhasználó kézzel kikapcsolta a remote-ot? (RIM_REMOTE_ENABLED=false)"""
    return os.getenv("RIM_REMOTE_ENABLED", "").strip().lower() in ("0", "false", "no", "off")


def remote_enabled() -> bool:
    """Engedélyezett-e a remote: explicit be/ki kapcsolás, különben token-függő."""
    env = os.getenv("RIM_REMOTE_ENABLED", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return bool(os.getenv("RIM_REMOTE_TOKEN", "").strip())


def ensure_remote_token() -> str:
    """A remote token; ha nincs és nincs kikapcsolva, generál egyet és a .env-be írja.

    Futásidőben hívandó (a .env betöltése UTÁN) — nincs import-időpontú mellékhatás,
    így a setup-varázsló friss .env-írásával nem ütközik.
    """
    token = os.getenv("RIM_REMOTE_TOKEN", "").strip()
    if token or remote_off():
        return token
    token = secrets.token_urlsafe(18)
    os.environ["RIM_REMOTE_TOKEN"] = token
    try:
        prefix = "" if (not _ENV_PATH.exists() or
                        _ENV_PATH.read_text(encoding="utf-8").endswith("\n")) else "\n"
        with open(_ENV_PATH, "a", encoding="utf-8") as f:
            f.write(f"{prefix}RIM_REMOTE_TOKEN={token}\n")
    except OSError:
        pass  # nem írható .env — a token erre a futásra él, a remote akkor is elindul
    return token
