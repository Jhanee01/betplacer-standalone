"""
Standalone BetPlacer — verzió és frissítési beállítások.

Itt él az alkalmazás verziószáma (semver) és a GitHub repó, ahonnan a
beépített Frissítés gomb az új kiadásokat keresi. A repó PUBLIKUS, ezért a
frissítés NEM igényel tokent — a felhasználó gépén semmit nem kell beállítani.
"""

# Klasszikus semver. Minden kiadás előtt EZT kell emelni (lásd make_release.bat).
APP_VERSION = "1.0.0"

# GitHub repó, ahonnan a frissítés jön (owner/repo). Publikus → token nem kell.
GITHUB_UPDATE_OWNER = "Jhanee01"
GITHUB_UPDATE_REPO  = "betplacer-standalone"

# A kiadásokhoz csatolt frissítő-csomag fájlneve (make_release.bat ezt tölti fel).
UPDATE_ASSET_NAME = "update.zip"
