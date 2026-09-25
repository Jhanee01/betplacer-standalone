"""
Parse Telegram tip messages.

Két piac-formátumot ismer:

  O/U (gól over/under):
    League: Esoccer Battle 8 min
    Strategy: 25 All Time
    Time: 21:22
    Match: Morocco (Kivu17) vs Netherlands (Linox)
    Market: OU
    Event ID: 303148022322821888

    Market data:
    • O/U 4.5 | Over @ 2.07 / Under @ 1.62

    Pick:
    • UNDER 4.5 @ 1.62

  1X2 (kimenetel — Hazai/Döntetlen/Vendég):
    League: Esoccer Battle 8 min
    Strategy: Döntetlen
    Time: 21:22
    Match: Morocco (Kivu17) vs Netherlands (Linox)
    Market: 1X2
    Event ID: 303148022322821888

    Pick:
    • DRAW @ 3.10

A Vegas-tippekben van egy `Bookmaker: Vegas` sor is (a TippmixPro-tippekben nincs)
→ ParsedTip.bookmaker.

A `Market:` sor opcionális és visszafelé kompatibilis: ha hiányzik, a Pick sor
formátuma dönt (OVER/UNDER → OU; HOME/DRAW/AWAY → 1X2). Az 1X2 pick a kanonikus
HOME/DRAW/AWAY tokeneket ÉS a magyar Hazai/Döntetlen/Vendég címkéket is elfogadja.
"""

import re
from dataclasses import dataclass
from typing import Optional


BOOKMAKER_LABEL = {"tippmixpro": "TippmixPro", "vegas": "Vegas"}


@dataclass
class ParsedTip:
    league:     str
    strategy:   str
    time:       str             # "HH:MM"
    home_team:  str             # "Morocco (Kivu17)"
    away_team:  str             # "Netherlands (Linox)"
    pick:       str             # OU: "OVER"|"UNDER"  |  1X2: "HOME"|"DRAW"|"AWAY"
    odds:       float           # 1.62
    market:     str = "OU"      # "OU" | "1X2"
    line:       Optional[float] = None  # OU gólvonal (pl. 4.5); 1X2-nél None
    event_id:   str = ""        # Tippmixpro / Vegas event ID (pl. "303148022322821888")
    bookmaker:  str = "tippmixpro"  # "tippmixpro" | "vegas" — a `Bookmaker:` sorból
                                    # (hiányában tippmixpro); indításkor a csatorna
                                    # irodájával vetjük össze

    @property
    def home_clean(self) -> str:
        """Country name without player: 'Morocco (Kivu17)' → 'Morocco'"""
        return re.sub(r'\s*\(.*?\)', '', self.home_team).strip()

    @property
    def away_clean(self) -> str:
        return re.sub(r'\s*\(.*?\)', '', self.away_team).strip()

    @property
    def minutes(self) -> str:
        """A meccshossz a League sorból: '8min' | '12min' | '' (pl. CLA).
        A dashboard 8/12 perces változatait ez különbözteti meg."""
        l = self.league.lower().replace(" ", "")
        if "12min" in l:
            return "12min"
        if "8min" in l:
            return "8min"
        return ""

    @property
    def strategy_key(self) -> str:
        """A stratégiánkénti tét KULCSA: stratégia-név + meccshossz.
        Pl. 'Team Running' + '8min' → 'Team Running 8min'. CLA-nál csak 'CLA'."""
        return f"{self.strategy} {self.minutes}".strip()

    @property
    def bookmaker_label(self) -> str:
        """Az iroda megjelenített neve (napló, értesítés, táblázat)."""
        return BOOKMAKER_LABEL.get(self.bookmaker, self.bookmaker)

    @property
    def pick_str(self) -> str:
        """Rövid, ember-olvasható pick (logokhoz). OU: 'UNDER 4.5'; 1X2: 'DRAW'."""
        if self.market == "OU" and self.line is not None:
            return f"{self.pick} {self.line}"
        return self.pick

    def __str__(self) -> str:
        if self.market == "OU" and self.line is not None:
            return (f"{self.time} | {self.home_team} vs {self.away_team} | "
                    f"O/U {self.line} {self.pick} @ {self.odds}")
        return (f"{self.time} | {self.home_team} vs {self.away_team} | "
                f"{self.market} {self.pick} @ {self.odds}")


# Magyar 1X2 címke → kanonikus token
_X2_LABELS = {
    "HOME": "HOME", "HAZAI": "HOME",
    "DRAW": "DRAW", "DÖNTETLEN": "DRAW", "DONTETLEN": "DRAW",
    "AWAY": "AWAY", "VENDÉG": "AWAY", "VENDEG": "AWAY",
}

_OU_PICK_RE = re.compile(
    r"Pick:\s*[•\-]\s*(OVER|UNDER)\s+([\d.]+)\s*@\s*([\d.]+)",
    re.IGNORECASE,
)
_X2_PICK_RE = re.compile(
    r"Pick:\s*[•\-]\s*"
    r"(HOME|DRAW|AWAY|HAZAI|DÖNTETLEN|DONTETLEN|VENDÉG|VENDEG)"
    # A kulcsszó után jöhet még szó az odds előtt (pl. „Vendég győzelem @ 2.75",
    # „Hazai győzelem @ 2.08") — bármit elnyelünk a @-ig.
    r"[^@\n]*@\s*([\d.]+)",
    re.IGNORECASE,
)


def parse_tip(text: str) -> Optional[ParsedTip]:
    """Return a ParsedTip or None if the message doesn't match."""
    # Markdown-tűrés: a bot félkövérrel küldheti a címkéket (*Pick:* vagy **Pick:**),
    # ami elrontaná a regexeket. A csillagokat (egyes ÉS dupla) és a kódjelet eltávolítjuk.
    # Az EGYSZERES aláhúzást meghagyjuk — játékosnévben előfordulhat (pl. "Da_Va").
    text = text.replace("**", "").replace("__", "").replace("`", "").replace("*", "")

    league_m   = re.search(r"League:\s*(.+)",            text)
    strategy_m = re.search(r"Strategy:\s*(.+)",          text)
    time_m     = re.search(r"Time:\s*(\d{1,2}:\d{2})",  text)
    match_m    = re.search(r"Match:\s*(.+?)\s+vs\s+(.+)",text)
    event_m    = re.search(r"Event ID:\s*(.+)",          text)
    book_m     = re.search(r"Bookmaker:\s*(.+)",         text)

    if not all([league_m, strategy_m, time_m, match_m]):
        return None

    common = dict(
        league    = league_m.group(1).strip(),
        strategy  = strategy_m.group(1).strip(),
        time      = time_m.group(1).strip(),
        home_team = match_m.group(1).strip(),
        away_team = match_m.group(2).strip(),
        event_id  = event_m.group(1).strip() if event_m else "",
        bookmaker = ("vegas" if book_m and "vegas" in book_m.group(1).lower()
                     else "tippmixpro"),
    )

    # A pick FORMÁTUMA dönt a piacról (a tokenek nem fednek át), így a `Market:`
    # sor nélkül is helyesen felismeri — visszafelé kompatibilis a régi O/U-val.
    ou_m = _OU_PICK_RE.search(text)
    if ou_m:
        return ParsedTip(
            **common,
            market = "OU",
            pick   = ou_m.group(1).upper(),
            line   = float(ou_m.group(2)),
            odds   = float(ou_m.group(3)),
        )

    x2_m = _X2_PICK_RE.search(text)
    if x2_m:
        token = x2_m.group(1).strip().upper()
        return ParsedTip(
            **common,
            market = "1X2",
            pick   = _X2_LABELS.get(token, token),
            line   = None,
            odds   = float(x2_m.group(2)),
        )

    return None
