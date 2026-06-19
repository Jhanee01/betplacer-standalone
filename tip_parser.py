"""
Parse Telegram tip messages in the standard format:

    League: Esoccer Battle 8 min
    Strategy: Basic 25
    Time: 21:22
    Match: Morocco (Kivu17) vs Netherlands (Linox)

    Market data:
    • O/U 4.5 | Over @ 2.07 / Under @ 1.62

    Pick:
    • UNDER 4.5 @ 1.62
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedTip:
    league:     str
    strategy:   str
    time:       str       # "HH:MM"
    home_team:  str       # "Morocco (Kivu17)"
    away_team:  str       # "Netherlands (Linox)"
    pick:       str       # "OVER" | "UNDER"
    line:       float     # 4.5
    odds:       float     # 1.62
    event_id:   str = ""  # Tippmixpro event ID (pl. "esoccer-12345")

    @property
    def home_clean(self) -> str:
        """Country name without player: 'Morocco (Kivu17)' → 'Morocco'"""
        return re.sub(r'\s*\(.*?\)', '', self.home_team).strip()

    @property
    def away_clean(self) -> str:
        return re.sub(r'\s*\(.*?\)', '', self.away_team).strip()

    def __str__(self) -> str:
        return (f"{self.time} | {self.home_team} vs {self.away_team} | "
                f"O/U {self.line} {self.pick} @ {self.odds}")


def parse_tip(text: str) -> Optional[ParsedTip]:
    """Return a ParsedTip or None if the message doesn't match."""
    # Markdown-tűrés: a bot félkövérrel küldheti a címkéket (**Pick:**, __Match__),
    # ami elrontaná a regexeket. A dupla jelölőket és a kódjelet eltávolítjuk.
    # Az EGYSZERES aláhúzást meghagyjuk — játékosnévben előfordulhat (pl. "Da_Va").
    text = text.replace("**", "").replace("__", "").replace("`", "")

    league_m   = re.search(r"League:\s*(.+)",            text)
    strategy_m = re.search(r"Strategy:\s*(.+)",          text)
    time_m     = re.search(r"Time:\s*(\d{1,2}:\d{2})",  text)
    match_m    = re.search(r"Match:\s*(.+?)\s+vs\s+(.+)",text)
    event_m    = re.search(r"Event ID:\s*(.+)",          text)
    pick_m     = re.search(
        r"Pick:\s*[•\-]\s*(OVER|UNDER)\s+([\d.]+)\s*@\s*([\d.]+)",
        text, re.IGNORECASE
    )

    if not all([league_m, strategy_m, time_m, match_m, pick_m]):
        return None

    return ParsedTip(
        league    = league_m.group(1).strip(),
        strategy  = strategy_m.group(1).strip(),
        time      = time_m.group(1).strip(),
        home_team = match_m.group(1).strip(),
        away_team = match_m.group(2).strip(),
        pick      = pick_m.group(1).upper(),
        line      = float(pick_m.group(2)),
        odds      = float(pick_m.group(3)),
        event_id  = event_m.group(1).strip() if event_m else "",
    )
