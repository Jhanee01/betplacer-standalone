"""
Stratégiánkénti tét tárolása — egyszerű JSON fájl az APP_DIR-ben.

A fő tét (BET_STAKE) a globális alapérték; ez a térkép stratégia-NÉV szerint
felülírja. A tipp `Strategy:` sora adja a kulcsot (a parser tip.strategy-be
olvassa). Ha egy stratégiához nincs külön tét, a globális alap érvényes.
"""

import json
from paths import APP_DIR

STAKES_PATH = APP_DIR / "strategy_stakes.json"
# Elrejtett (kézzel törölt) BEÉPÍTETT stratégiák — hogy ne térjenek vissza a
# táblázat újratöltésekor. Felhasználói adat: frissítéskor NEM íródik felül.
HIDDEN_PATH = APP_DIR / "hidden_strategies.json"

# Ismert stratégia-kulcsok a pipeline-ból — a tét-táblázat előtöltéséhez.
# A kulcs = dashboard stratégia-név + meccshossz (8min/12min); a CLA-nak nincs perc.
# Csak a hatókörbe eső piacok (OU + 1X2). A felhasználó szerkesztheti; ismeretlen
# (új) stratégia futás közben automatikus sort kap az alap téttel.
# (Team Running Handicap = AH, később; BET365 = nem Tippmixpro, kihagyva.)
KNOWN_STRATEGIES = [
    "Team Running 8min",          "Team Running 12min",
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


def load_hidden() -> set:
    """Az elrejtett (törölt) beépített stratégiák neve. Hibánál üres halmaz."""
    try:
        if HIDDEN_PATH.exists():
            data = json.loads(HIDDEN_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
    except Exception:
        pass
    return set()


def save_hidden(names) -> bool:
    """Az elrejtett stratégiák listájának kiírása. True, ha sikerült."""
    try:
        HIDDEN_PATH.write_text(
            json.dumps(sorted({str(n) for n in names}), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return True
    except Exception:
        return False


def delete_strategy(name: str) -> None:
    """Egy stratégia törlése: kivesszük a mentett tétjéből, és ha BEÉPÍTETT,
    elrejtjük is (hogy ne térjen vissza). Egyedi (nem beépített) nevet nem
    rejtünk el — az pusztán azzal eltűnik, hogy nincs mentett tétje."""
    name = (name or "").strip()
    if not name:
        return
    stakes = load_stakes()
    if name in stakes:
        del stakes[name]
        save_stakes(stakes)
    if name in KNOWN_STRATEGIES:
        hidden = load_hidden()
        if name not in hidden:
            hidden.add(name)
            save_hidden(hidden)
