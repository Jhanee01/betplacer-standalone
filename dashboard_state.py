"""
A dashboard (főablak) adatai — a FIFA Tipster dashboard BetPlacer oldalának
standalone megfelelője.

Fájlok a program mappájában:
  betplacer_status.json        a futás állapota: munkamenet kezdete, irodánként fióknév,
                               egyenleg (+ görbe), nyitott fogadások, Telegram-állapotok
  betplacer_bets.jsonl         minden megrakási kísérlet (csak hozzáírás, nem töröl)
  betplacer_account_settled.json  a fiókok lezárt fogadásai (az elszámoláshoz)
  bet_stakes.json              irodánkénti tét (a főablakban állítható)

A „munkamenet” a PROGRAM indulásától tart: a Leállítás/Indítás nem nullázza az
„indítás óta” statisztikát, csak a program újraindítása.

Írás: a futtató mag (háttérszál). Olvasás: a főablak (5 mp-enként).
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta

from paths import APP_DIR

STATUS_PATH  = APP_DIR / "betplacer_status.json"
BETS_PATH    = APP_DIR / "betplacer_bets.jsonl"
SETTLED_PATH = APP_DIR / "betplacer_account_settled.json"
STAKES_PATH  = APP_DIR / "bet_stakes.json"

BOOKMAKERS = ("tippmixpro", "vegas")
SESSION_ENV = "BETPLACER_SESSION_START"
DAY_START_MIN = 6 * 60 + 30          # 06:30 — a nap-határ (mint a számla-szinkronban)

_lock = threading.Lock()
_status: dict = {}


def session_start() -> str:
    """A program indulásának ideje (a GUI állítja be induláskor)."""
    if not os.getenv(SESSION_ENV):
        os.environ[SESSION_ENV] = datetime.now().isoformat(timespec="seconds")
    return os.environ[SESSION_ENV]


def _atomic_write(path, text: str):
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Írás (futtató mag)
# ══════════════════════════════════════════════════════════════════════════════

def begin_run(dry_run: bool, bookmakers: list):
    """Egy futás kezdete. Ugyanabban a munkamenetben az induló egyenleg és a görbe
    megmarad (a Leállítás/Indítás nem nulláz)."""
    global _status
    session = session_start()
    prev = load_status()
    offices = {}
    if prev.get("started_at") == session:
        for bm, o in (prev.get("offices") or {}).items():
            offices[bm] = {k: o[k] for k in ("start_balance", "history") if k in o}
    with _lock:
        _status = {"started_at": session, "dry_run": dry_run, "bookmakers": list(bookmakers),
                   "offices": offices, "telegram": {}, "running": True}
        _write()


def end_run():
    with _lock:
        if _status:
            _status["running"] = False
            _write()


def _write():
    _atomic_write(STATUS_PATH, json.dumps(_status, ensure_ascii=False, indent=2))


def set_office(bm: str, **fields):
    """Iroda-adatok frissítése; az egyenleg a görbébe és (először) induló egyenlegnek."""
    with _lock:
        o = _status.setdefault("offices", {}).setdefault(bm, {})
        now = datetime.now().isoformat(timespec="seconds")
        if fields.get("balance") is not None:
            o.setdefault("start_balance", fields["balance"])
            hist = o.setdefault("history", [])
            if not hist or hist[-1][1] != fields["balance"]:
                hist.append([now, fields["balance"]])
                del hist[:-500]
            fields["balance_at"] = now
        if "open_count" in fields:
            fields["open_at"] = now
        o.update({k: v for k, v in fields.items() if v is not None})
        _write()


def set_telegram(kind: str, state: str, **extra):
    """kind: 'watcher' (a csatorna-figyelés a saját fiókkal) | 'bot' (értesítő bot);
    state: ok | missing | error | connecting."""
    with _lock:
        t = _status.setdefault("telegram", {}).setdefault(kind, {})
        t.update(state=state, at=datetime.now().isoformat(timespec="seconds"), **extra)
        _write()


def log_bet(tip, outcome: str, stake: int, error: str = "", dry_run: bool = False):
    """Megrakási kísérlet a tartós naplóba (FIFA-tipp; a Real Event kimarad)."""
    if getattr(tip, "kind", "") == "real":
        return
    players = re.findall(r"\(([^()]+)\)", f"{tip.home_team} {tip.away_team}")
    now = datetime.now()
    rec = {
        "ts": now.isoformat(timespec="seconds"),
        "bookmaker": tip.bookmaker, "outcome": outcome, "stake": stake,
        "odds": tip.odds, "strategy": getattr(tip, "strategy_key", ""),
        "label": f"{tip.time} | {tip.home_team} vs {tip.away_team} | {tip.pick_str}",
        "date": now.strftime("%Y-%m-%d"), "time": tip.time,
        "home_player": players[0] if players else "", "away_player": players[1] if len(players) > 1 else "",
        "pick": tip.pick, "line": tip.line, "minutes": getattr(tip, "minutes", ""),
        "error": error, "dry_run": dry_run,
    }
    try:
        with open(BETS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def save_settled(bm: str, rows: list):
    """A fiók lezárt fogadásai (account_sync sorok) → fájl, szerializálható alakban."""
    def ser(r):
        return {k: (v.isoformat() if isinstance(v, datetime)
                    else sorted(v) if isinstance(v, frozenset) else v)
                for k, v in r.items()}
    try:
        data = json.loads(SETTLED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[bm] = {"fetched_at": datetime.now().isoformat(timespec="seconds"),
                "rows": [ser(r) for r in rows]}
    _atomic_write(SETTLED_PATH, json.dumps(data, ensure_ascii=False))


# ── Irodánkénti tét ───────────────────────────────────────────────────────────

def _default_stake(bm: str) -> int:
    key = "BET_STAKE_VEGAS" if bm == "vegas" else "BET_STAKE"
    try:
        return int(float(os.getenv(key, os.getenv("BET_STAKE", "500"))))
    except ValueError:
        return 500


def load_stakes() -> dict:
    stakes = {bm: _default_stake(bm) for bm in BOOKMAKERS}
    try:
        data = json.loads(STAKES_PATH.read_text(encoding="utf-8"))
        for bm in BOOKMAKERS:
            if int(data.get(bm, 0)) > 0:
                stakes[bm] = int(data[bm])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return stakes


def save_stake(bm: str, stake: int):
    stakes = load_stakes()
    stakes[bm] = int(stake)
    _atomic_write(STAKES_PATH, json.dumps(stakes, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# Olvasás (főablak)
# ══════════════════════════════════════════════════════════════════════════════

def load_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _settled_rows() -> dict:
    try:
        data = json.loads(SETTLED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {bm: (data.get(bm) or {}).get("rows") or [] for bm in BOOKMAKERS}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _match(r: dict, rows: list, used: set):
    """Párosítás a számla-szinkron szabályával: játékosnevek + irány + vonal, és a
    feladás ideje ±30 percen belül."""
    players = {_norm(r.get("home_player")), _norm(r.get("away_player"))}
    try:
        line = float(r.get("line"))
        ts = datetime.fromisoformat(str(r.get("ts")))
    except (TypeError, ValueError):
        return None
    for i, a in enumerate(rows):
        if i in used or {_norm(p) for p in a.get("players") or []} != players:
            continue
        if a.get("side") != r.get("pick"):
            continue
        try:
            if abs(float(a.get("line")) - line) > 1e-6:
                continue
            placed = datetime.fromisoformat(str(a.get("placed")))
        except (TypeError, ValueError):
            continue
        if abs((placed - ts).total_seconds()) <= 1800:
            used.add(i)
            return a
    return None


def load_bets(since) -> list:
    """A munkamenet (since) óta naplózott kísérletek, legújabb elöl. A megrakottak
    `result` mezőt kapnak a fiók elszámolása alapján: win | loss | void | pending."""
    rows = []
    try:
        with open(BETS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if since and str(r.get("ts", "")) < since:
                    continue
                rows.append(r)
    except OSError:
        return []
    settled = _settled_rows()
    used = {bm: set() for bm in BOOKMAKERS}
    for r in rows:
        if r.get("outcome") != "placed":
            continue
        bm = r.get("bookmaker")
        a = _match(r, settled.get(bm, []), used.setdefault(bm, set()))
        if a:
            r.update(result=a.get("status"), confirmed=True,
                     odds=a.get("odds") or r.get("odds"), stake=a.get("stake") or r.get("stake"),
                     account_profit=float(a.get("ret") or 0) - float(a.get("stake") or 0))
        else:
            r["result"] = "pending"
    rows.reverse()
    return rows


def profit_of(r: dict) -> float:
    if r.get("confirmed"):
        return float(r.get("account_profit") or 0)
    return 0.0


def bet_day(r: dict) -> str:
    """A fogadás napja a meccs kezdése szerint, 06:30-as nap-határral."""
    try:
        ts = datetime.fromisoformat(str(r.get("ts")))
        h, m = map(int, str(r.get("time")).split(":"))
        ko = ts.replace(hour=h, minute=m, second=0)
        if ko < ts - timedelta(hours=12):     # éjfél utáni meccs, este megrakva
            ko += timedelta(days=1)
    except (TypeError, ValueError):
        return str(r.get("ts", ""))[:10]
    d = ko.date()
    if ko.hour * 60 + ko.minute < DAY_START_MIN:
        d -= timedelta(days=1)
    return d.isoformat()


def summary(bets: list, bookmaker: str) -> dict:
    placed = [r for r in bets if r.get("bookmaker") == bookmaker
              and r.get("outcome") == "placed" and not r.get("dry_run")]
    settled = [r for r in placed if r.get("result") in ("win", "loss")]
    staked = sum(float(r.get("stake") or 0) for r in settled)
    profit = sum(profit_of(r) for r in settled)
    days: dict = {}
    for r in placed:
        d = days.setdefault(bet_day(r), [0.0, 0])
        d[0] += profit_of(r)
        d[1] += 1
    return {
        "bets": len(placed),
        "open": sum(1 for r in placed if r.get("result") == "pending"),
        "open_stake": sum(float(r.get("stake") or 0) for r in placed if r.get("result") == "pending"),
        "won": sum(1 for r in settled if r["result"] == "win"),
        "lost": sum(1 for r in settled if r["result"] == "loss"),
        "staked_all": sum(float(r.get("stake") or 0) for r in placed),
        "profit": profit,
        "roi": profit / staked * 100 if staked else 0.0,
        "days": sorted((d, v[0], v[1]) for d, v in days.items()),
    }


def ago(iso) -> str:
    if not iso:
        return "—"
    try:
        secs = (datetime.now() - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return "—"
    if secs < 60:
        return "most"
    if secs < 3600:
        return f"{int(secs // 60)} perce"
    if secs < 86400:
        return f"{int(secs // 3600)} órája"
    return str(iso)[:16].replace("T", " ")
