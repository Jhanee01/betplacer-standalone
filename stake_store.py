"""
Stratégiánkénti tét tárolása — egyszerű JSON fájl az APP_DIR-ben.

A fő tét (BET_STAKE) a globális alapérték; ez a térkép stratégia-NÉV szerint
felülírja. A tipp `Strategy:` sora adja a kulcsot (a parser tip.strategy-be
olvassa). Ha egy stratégiához nincs külön tét, a globális alap érvényes.
"""

import json
from paths import APP_DIR

STAKES_PATH = APP_DIR / "strategy_stakes.json"

# Ismert stratégia-kulcsok a pipeline-ból — a tét-táblázat előtöltéséhez.
# A kulcs = dashboard stratégia-név + meccshossz (8min/12min); a CLA-nak nincs perc.
# Csak a hatókörbe eső piacok (OU + 1X2). A felhasználó szerkesztheti; ismeretlen
# (új) stratégia futás közben automatikus sort kap az alap téttel.
# (Team Running Handicap = AH, később; BET365 = nem Tippmixpro, kihagyva.)
KNOWN_STRATEGIES = [
    "Team Running 8min",          "Team Running 12min",
    "25 All Time 8min",          "25 All Time 12min",
    "Döntetlen 8min",            "Döntetlen 12min",
    "Team Running Handicap 8min", "Team Running Handicap 12min",
    "CLA",
]


def load_stakes() -> dict:
    """A mentett stratégia→tét térkép (int értékekkel). Hibánál üres dict."""
    try:
        if STAKES_PATH.exists():
            data = json.loads(STAKES_PATH.read_text(encoding="utf-8"))
            out = {}
            for k, v in data.items():
                try:
                    iv = int(float(v))
                    if iv > 0:
                        out[str(k)] = iv
                except (ValueError, TypeError):
                    pass
            return out
    except Exception:
        pass
    return {}


def save_stakes(stakes: dict) -> bool:
    """A térkép kiírása JSON-be (csak pozitív egész tétek). True, ha sikerült."""
    clean = {}
    for k, v in stakes.items():
        try:
            iv = int(float(v))
            if iv > 0:
                clean[str(k)] = iv
        except (ValueError, TypeError):
            pass
    try:
        STAKES_PATH.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
