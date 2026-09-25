"""
Stratégiánkénti tét tárolása — egyszerű JSON fájl az APP_DIR-ben.

Az iroda alap tétje (BET_STAKE / BET_STAKE_VEGAS) a globális alapérték; ez a
térkép stratégia-NÉV szerint felülírja. A tipp `Strategy:` sora + meccshossz adja
a kulcsot (tip.strategy_key). Ha egy stratégiához nincs külön tét, az iroda alap
tétje érvényes.

Irodánként külön tétek: a TippmixPro-kulcsok a régi formában maradnak (visszafelé
kompatibilis a 2.x-es fájlokkal), a Vegas-kulcsok "vegas|" előtagot kapnak —
UGYANABBAN a fájlban, így a frissítő (update_apply.bat) kizárási listája is marad.
"""

import json
from paths import APP_DIR

STAKES_PATH = APP_DIR / "strategy_stakes.json"
# Elrejtett (kézzel törölt) BEÉPÍTETT stratégiák — hogy ne térjenek vissza a
# táblázat újratöltésekor. Felhasználói adat: frissítéskor NEM íródik felül.
HIDDEN_PATH = APP_DIR / "hidden_strategies.json"

# Ismert stratégia-kulcsok a pipeline-ból — a tét-táblázat előtöltéséhez.
# A kulcs = dashboard stratégia-név + meccshossz (8min/12min); a CLA-nak nincs perc.
# A felhasználó szerkesztheti; ismeretlen (új) stratégia futás közben automatikus
# sort kap az alap téttel. Mindkét irodán ugyanez a két stratégia fut.
KNOWN_STRATEGIES = [
    "Team Running 8min", "Team Running 12min",
]

BOOKMAKERS = ("tippmixpro", "vegas")


def _prefix(bookmaker: str) -> str:
    return "" if bookmaker == "tippmixpro" else f"{bookmaker}|"


def _split(key: str) -> tuple[str, str]:
    """Tárolt kulcs → (iroda, stratégia-név)."""
    for bm in BOOKMAKERS:
        p = _prefix(bm)
        if p and key.startswith(p):
            return bm, key[len(p):]
    return "tippmixpro", key


def _read_all() -> dict:
    """A teljes (minden irodás) térkép, int értékekkel. Hibánál üres dict."""
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


def load_stakes(bookmaker: str = "tippmixpro") -> dict:
    """Az iroda mentett stratégia→tét térképe (előtag nélküli nevekkel)."""
    out = {}
    for k, v in _read_all().items():
        bm, name = _split(k)
        if bm == bookmaker:
            out[name] = v
    return out


def save_stakes(stakes: dict, bookmaker: str = "tippmixpro") -> bool:
    """Az iroda térképének kiírása (csak pozitív egész tétek); a MÁSIK iroda
    tétjei érintetlenek maradnak. True, ha sikerült."""
    merged = {k: v for k, v in _read_all().items() if _split(k)[0] != bookmaker}
    for k, v in stakes.items():
        try:
            iv = int(float(v))
            if iv > 0:
                merged[_prefix(bookmaker) + str(k)] = iv
        except (ValueError, TypeError):
            pass
    try:
        STAKES_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _read_hidden() -> set:
    try:
        if HIDDEN_PATH.exists():
            data = json.loads(HIDDEN_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
    except Exception:
        pass
    return set()


def load_hidden(bookmaker: str = "tippmixpro") -> set:
    """Az iroda elrejtett (törölt) beépített stratégiáinak neve."""
    return {name for bm, name in map(_split, _read_hidden()) if bm == bookmaker}


def save_hidden(names, bookmaker: str = "tippmixpro") -> bool:
    """Az iroda elrejtett stratégiáinak kiírása (a másik irodáé marad)."""
    keep = {k for k in _read_hidden() if _split(k)[0] != bookmaker}
    keep |= {_prefix(bookmaker) + str(n) for n in names}
    try:
        HIDDEN_PATH.write_text(
            json.dumps(sorted(keep), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def delete_strategy(name: str, bookmaker: str = "tippmixpro") -> None:
    """Egy stratégia törlése: kivesszük a mentett tétjéből, és ha BEÉPÍTETT,
    elrejtjük is (hogy ne térjen vissza). Egyedi (nem beépített) nevet nem
    rejtünk el — az pusztán azzal eltűnik, hogy nincs mentett tétje."""
    name = (name or "").strip()
    if not name:
        return
    stakes = load_stakes(bookmaker)
    if name in stakes:
        del stakes[name]
        save_stakes(stakes, bookmaker)
    if name in KNOWN_STRATEGIES:
        hidden = load_hidden(bookmaker)
        if name not in hidden:
            hidden.add(name)
            save_hidden(hidden, bookmaker)
