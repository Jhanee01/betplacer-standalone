"""
Real Event tipp-üzenet értelmezése (valódi foci / kézi / kosár meccsek).

A Jhanee Tipster dashboard Real Event csatornáinak formátuma:

    League: Soccer - Israel - Liga Alef
    Strategy: Real Event
    Time: 09-30 19:00
    Match: Maccabi Ironi Ashdod FC vs Holon Yermiyahu FC
    Event ID: 17831222
    Odds ID: 4572784734

    Market data:
    • DNB | Vegas @ 2.20

    Pick:
    • Maccabi Ironi Ashdod FC (DNB) @ 2.20

A megrakás AZONOSÍTÓ alapján történik (Event ID + Odds ID) — pontosan úgy, mint a
dashboard BetPlacerében: Vegas = Altenar event/odd ID, Tippmixpro = meccs-ID /
bettingOfferId (a gomb data-ubt-label-je).
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

SOURCE_LABEL = {"real_vegas": "Real Vegas", "real_tippmixpro": "Real TippmixPro"}
# League sor első szava -> belső sportág + Tippmixpro sportId (a meccsoldal URL-jéhez)
SPORT = {"soccer": ("foci", 1), "handball": ("kezi", 7), "basketball": ("kosar", 8)}


@dataclass
class RealTip:
    league:    str
    sport:     str             # "foci" | "kezi" | "kosar"
    sport_id:  int             # Tippmixpro sportId (1 / 7 / 8)
    kickoff:   datetime        # a meccs kezdése (helyi idő)
    home_team: str
    away_team: str
    event_id:  str
    odds_id:   str
    piac:      str             # pl. "AH -2", "OU +2.5", "SZOGLET TT1 +2", "DNB"
    pick:      str             # a Pick sor szövege (pl. "OVER 2.5", "Ashdod (DNB)")
    odds:      float
    bookmaker: str = "vegas"   # "vegas" | "tippmixpro" — a Market data sorból
    source:    str = ""        # "real_vegas" | "real_tippmixpro" (a figyelő tölti)
    kind:      str = "real"
    strategy:  str = "Real Event"

    # ── a FIFA-tippel (ParsedTip) közös felület a core-nak és a GUI-nak ──────────
    market = property(lambda self: self.piac)
    line = None

    @property
    def time(self) -> str:
        return self.kickoff.strftime("%H:%M")

    @property
    def strategy_key(self) -> str:
        return "Real Event"

    @property
    def home_clean(self) -> str:
        return self.home_team

    @property
    def away_clean(self) -> str:
        return self.away_team

    @property
    def bookmaker_label(self) -> str:
        return SOURCE_LABEL.get(self.source or f"real_{self.bookmaker}", "Real Event")

    @property
    def pick_str(self) -> str:
        return f"{self.piac} · {self.pick}"

    def seconds_to_kickoff(self) -> float:
        return (self.kickoff - datetime.now()).total_seconds()

    def fmt_line(self) -> str:
        return (f"{self.kickoff:%m-%d %H:%M} | {self.home_team} vs {self.away_team} | "
                f"{self.piac} — {self.pick} @ {self.odds}")

    def __str__(self) -> str:
        return self.fmt_line()


def _kickoff(mmdd_hhmm: str) -> Optional[datetime]:
    """'09-30 19:00' -> datetime (az év a maihoz igazítva, dec/jan átfordulással)."""
    try:
        now = datetime.now()
        d = datetime.strptime(f"{now.year}-{mmdd_hhmm.strip()}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if d - now > timedelta(days=180):
        d = d.replace(year=now.year - 1)
    elif now - d > timedelta(days=180):
        d = d.replace(year=now.year + 1)
    return d


def parse_real_tip(text: str) -> Optional[RealTip]:
    """RealTip, vagy None ha az üzenet nem (megrakható) Real Event tipp."""
    text = text.replace("**", "").replace("__", "").replace("`", "").replace("*", "")
    if not re.search(r"Strategy:\s*Real Event", text):
        return None
    league_m = re.search(r"League:\s*(.+)", text)
    time_m   = re.search(r"Time:\s*(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})", text)
    match_m  = re.search(r"Match:\s*(.+?)\s+vs\s+(.+)", text)
    event_m  = re.search(r"Event ID:\s*(\d+)", text)
    odds_id_m = re.search(r"Odds ID:\s*(\d+)", text)
    market_m = re.search(r"Market data:\s*\n\s*[•\-]\s*(.+?)\s*\|\s*(Vegas|Tippmixpro)\s*@\s*([\d.]+)",
                         text, re.IGNORECASE)
    pick_m   = re.search(r"Pick:\s*\n\s*[•\-]\s*(.+?)\s*@\s*([\d.]+)", text)
    if not all([league_m, time_m, match_m, event_m, odds_id_m, market_m, pick_m]):
        return None
    league = league_m.group(1).strip()
    sport, sport_id = SPORT.get(league.split()[0].lower(), ("foci", 1))
    kickoff = _kickoff(time_m.group(1))
    if kickoff is None:
        return None
    return RealTip(
        league=league, sport=sport, sport_id=sport_id, kickoff=kickoff,
        home_team=match_m.group(1).strip(), away_team=match_m.group(2).strip(),
        event_id=event_m.group(1), odds_id=odds_id_m.group(1),
        piac=market_m.group(1).strip(), pick=pick_m.group(1).strip(),
        odds=float(pick_m.group(2)),
        bookmaker="vegas" if market_m.group(2).lower() == "vegas" else "tippmixpro",
    )
