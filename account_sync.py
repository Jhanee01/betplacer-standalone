"""
Napi számla-szinkron jelentés (v4.1.0).

A BetPlacer minden SIKERES megrakást feljegyez a `placed_bets.jsonl`-be (a program
mappájában). Reggelente (SYNC_REPORT_AFTER után, naponta egyszer) a bekapcsolt és
belépett irodák fiókjából lekéri az előző nap kiértékelt fogadásait, összeveti a
naplóval, és az eredményt a felhasználó értesítő botjával elküldi:

    🧾 Számla-szinkron — 2026.10.13.
    TippmixPro: megrakott 8 | számlán 8 | ✅ nincs eltérés
    Számla: 6–2 | tét 8 000 Ft | +3 870 Ft | ROI +48,4%

A lekérés a MÁR BELÉPETT böngészőben fut (az iroda saját motor-szálán, egy külön
lapon) — nincs második belépés, ami kiléptetné a futó munkamenetet. Csak olvas.

Párosítás: a két játékos + irány + vonal + kezdés (±20 perc). A tipp event-ID-ja
nem egyezik a fiók azonosítóival, a csapatnevek pedig eltérnek („Man City” vs
„Manchester City”), ezért a játékosnév a kulcs.

Nap-határ: 06:30 — az ennél korábbi kezdésű (éjszakai) meccsek az előző naphoz
tartoznak.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from paths import APP_DIR

LEDGER_PATH = APP_DIR / "placed_bets.jsonl"
STATE_PATH  = APP_DIR / "sync_report_state.json"

DAY_START_MIN      = 6 * 60 + 30     # 06:30 — a nap-határ
SYNC_REPORT_AFTER  = 7 * 60 + 30     # 07:30 — ekkorra az éjszakai meccsek is kiértékelődnek
KICKOFF_TOL_MIN    = 20
ODDS_TOL           = 0.05
LIST_MAX           = 5
LEDGER_KEEP_DAYS   = 30

BOOKMAKER_LABEL = {"tippmixpro": "TippmixPro", "vegas": "Vegas"}


# ── Napló ─────────────────────────────────────────────────────────────────────

def _players(text: str) -> list:
    """'Germany (Manuel) vs Scotland (John)' → ['manuel', 'john']"""
    return [p.strip().lower() for p in re.findall(r"\(([^()]+)\)", text or "")]


def record_placed(tip, stake: int, bet_ref: str = "", seconds_to_kickoff=None):
    """Egy sikeres megrakás feljegyzése (FIFA-tipp; a Real Event kimarad)."""
    now = datetime.now()
    kickoff = now + timedelta(seconds=seconds_to_kickoff) if seconds_to_kickoff is not None else None
    rec = {
        "placed_at": now.isoformat(timespec="seconds"),
        "bookmaker": tip.bookmaker,
        "strategy": getattr(tip, "strategy", ""),
        "kickoff": kickoff.replace(second=0, microsecond=0).isoformat() if kickoff else "",
        "match": f"{tip.home_team} vs {tip.away_team}",
        "market": tip.market,
        "pick": tip.pick,
        "line": tip.line,
        "odds": tip.odds,
        "stake": stake,
        "bet_ref": bet_ref,
    }
    try:
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _load_ledger() -> list:
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _prune_ledger():
    """A LEDGER_KEEP_DAYS napnál régebbi sorok törlése (a fájl ne nőjön végtelenül)."""
    recs = _load_ledger()
    limit = (datetime.now() - timedelta(days=LEDGER_KEEP_DAYS)).isoformat()
    keep = [r for r in recs if str(r.get("placed_at", "")) >= limit]
    if len(keep) == len(recs):
        return
    try:
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep),
                       encoding="utf-8")
        tmp.replace(LEDGER_PATH)
    except OSError:
        pass


# ── Állapot ───────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict):
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def tracking_since() -> str:
    """Mióta naplózunk (ISO). Első híváskor most-ra áll — a korábbi, napló nélküli
    fogadásokat nem jelezzük „tipp nélküli” számlás tételként."""
    state = _load_state()
    if not state.get("tracking_since"):
        state["tracking_since"] = datetime.now().isoformat(timespec="seconds")
        _save_state(state)
    return state["tracking_since"]


def day_of(dt: datetime) -> str:
    d = dt.date()
    if dt.hour * 60 + dt.minute < DAY_START_MIN:
        d -= timedelta(days=1)
    return d.isoformat()


def due_report_day(now: datetime = None):
    """Az elküldendő nap (az előző nap), vagy None, ha még korai / már kiment."""
    now = now or datetime.now()
    if now.hour * 60 + now.minute < SYNC_REPORT_AFTER:
        return None
    day = (now.date() - timedelta(days=1)).isoformat()
    state = _load_state()
    if state.get("last_sent", "") >= day:
        return None
    since = state.get("tracking_since", "")
    if since and since[:10] > day:
        return None          # aznap még nem naplóztunk — nincs mit összevetni
    return day


def mark_sent(day: str):
    state = _load_state()
    state["last_sent"] = day
    _save_state(state)


# ── Fiók-lekérés (az iroda motor-szálán fut!) ─────────────────────────────────

_TMX_NAPLO_URL = "https://www.tippmixpro.hu/hu/fogadas/i/naplo/settled/1"
_VEGAS_MYBETS_URL = "https://vegas.hu/sports/my-bets"
# Altenar: a NEM nyitott fogadás-státuszok (1 = nyert, 2 = vesztes, 8 = visszatérített)
_VEGAS_SETTLED = [1, 2, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 18, 19, 21]


def _capture(context, page, url: str, needle: str) -> dict:
    """Megnyitja az oldalt, és elkapja az első `needle`-t tartalmazó API-kérést."""
    first: dict = {}

    def _on_request(req):
        if needle in req.url and "url" not in first:
            first.update(url=req.url, headers=dict(req.headers), body=req.post_data or "")

    context.on("request", _on_request)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(60):
            if "url" in first:
                break
            page.wait_for_timeout(500)
    finally:
        context.remove_listener("request", _on_request)
    if "url" not in first:
        raise RuntimeError("a fogadási napló nem töltődött be (kilépett a munkamenet?)")
    first["headers"] = {k: v for k, v in first["headers"].items()
                        if not k.startswith(":") and k.lower() != "content-length"}
    return first


def _fetch_tmx(context, page, since: str) -> list:
    first = _capture(context, page, _TMX_NAPLO_URL, "/settled-bets")
    base = first["url"].split("?", 1)[0]
    before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    bets, seen = [], set()
    for _ in range(30):
        resp = context.request.get(base, headers=first["headers"], params={
            "limit": 100, "placedBefore": before, "betStatus": "", "sportIds": ""})
        if not resp.ok:
            raise RuntimeError(f"settled-bets HTTP {resp.status}")
        page_bets = resp.json()
        if not isinstance(page_bets, list):
            raise RuntimeError("settled-bets: váratlan válasz")
        fresh = [b for b in page_bets if b.get("id") not in seen]
        seen.update(b.get("id") for b in fresh)
        bets.extend(fresh)
        oldest = min((str(b.get("placedDate") or "") for b in page_bets), default="")
        if len(page_bets) < 100 or not fresh or not oldest or oldest[:10] < since:
            break
        before = oldest.rstrip("Z")[:19]
    return [_tmx_row(b) for b in bets]


def _fetch_vegas(context, page, since: str) -> list:
    first = _capture(context, page, _VEGAS_MYBETS_URL, "widgetExpandedBetHistory")
    date_from = (datetime.strptime(since, "%Y-%m-%d") - timedelta(hours=2)
                 ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_to = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    bets, seen = [], set()
    for page_no in range(1, 41):
        body = dict(json.loads(first["body"] or "{}"), dateFrom=date_from, dateTo=date_to,
                    pageNumber=page_no, pageSize=50, statuses=_VEGAS_SETTLED)
        # 2026-10-21 óta a context.request (nem böngésző-kérés) 403-at kap a Cloudflare-től
        # → az oldalon belüli fetch. Kereszt-domaines: csak a nem tiltott fejlécek mennek,
        # sütik nélkül (a credentials:'include' „Failed to fetch”-et ad).
        headers = {k: v for k, v in first["headers"].items()
                   if k.lower() in ("authorization", "content-type", "accept")}
        resp = page.evaluate(
            """async ([url, headers, body]) => {
                const r = await fetch(url, {method: 'POST', headers, body, credentials: 'omit'});
                return {status: r.status, text: await r.text()};
            }""", [first["url"], headers, json.dumps(body)])
        if resp["status"] != 200:
            raise RuntimeError(f"bet-history HTTP {resp['status']}")
        data = json.loads(resp["text"])
        page_bets = data.get("bets") if isinstance(data, dict) else None
        if not isinstance(page_bets, list):
            raise RuntimeError("bet-history: váratlan válasz")
        fresh = [b for b in page_bets if b.get("id") not in seen]
        seen.update(b.get("id") for b in fresh)
        bets.extend(fresh)
        if data.get("isLastPage") or not fresh:
            break
    return [_vegas_row(b) for b in bets]


def fetch_account(engine, bookmaker: str, since: str) -> list:
    """Az iroda kiértékelt fogadásai `since` (YYYY-MM-DD) óta, egységes sorokként.
    A motor saját szálán kell hívni (run_in_executor az iroda executorával)."""
    context = engine._page.context
    page = context.new_page()
    try:
        fn = _fetch_vegas if bookmaker == "vegas" else _fetch_tmx
        return fn(context, page, since)
    finally:
        try:
            page.close()
        except Exception:
            pass


def _local(iso: str):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


_TMX_STATUS = {"WON": "win", "LOST": "loss", "CANCELLED": "void", "VOID": "void",
               "REFUNDED": "void", "HALF_WON": "win", "HALF_LOST": "loss"}
_VEGAS_STATUS = {1: "win", 2: "loss", 8: "void"}


def _tmx_row(b: dict) -> dict:
    sels = b.get("selections") or []
    s = sels[0] if len(sels) == 1 and b.get("type") == "SINGLE" else {}
    name = str(s.get("betName") or "").strip().lower()
    m = re.search(r"([\d.]+)\s*$", str(s.get("marketName") or ""))
    return {
        "placed": _local(b.get("placedDate") or ""),
        "kickoff": _local(s.get("eventDate") or ""),
        "players": frozenset(_players(s.get("eventName"))),
        "side": "OVER" if name.startswith("több") else "UNDER" if name.startswith("kevesebb") else "",
        "line": m.group(1) if m else "",
        "status": _TMX_STATUS.get(str(b.get("status")), str(b.get("status")).lower()),
        "stake": float(b.get("totalBetAmount") or 0),
        "ret": float(b.get("overallBetReturns") or 0),
        "odds": float(b.get("totalPriceValue") or 0),
        "label": f"{s.get('eventName', '?')} | {str(s.get('betName') or '').strip()}",
    }


def _vegas_row(b: dict) -> dict:
    sels = b.get("selections") or []
    s = sels[0] if len(sels) == 1 and b.get("type") == 0 else {}
    name = str(s.get("name") or "").strip().lower()
    try:
        line = str(json.loads(s.get("spec") or "{}").get("1") or "")
    except ValueError:
        line = ""
    return {
        "placed": _local(b.get("createdDate") or ""),
        "kickoff": _local(s.get("eventDate") or ""),
        "players": frozenset(_players(s.get("eventName"))),
        "side": "OVER" if name.endswith("felett") else "UNDER" if name.endswith("alatt") else "",
        "line": line,
        "status": _VEGAS_STATUS.get(b.get("status"), f"státusz {b.get('status')}"),
        "stake": float(b.get("totalStake") or 0),
        "ret": float(b.get("totalWin") or 0),
        "odds": float(b.get("totalOdds") or s.get("price") or 0),
        "label": f"{s.get('eventName', '?')} | {str(s.get('name') or '').strip()}",
    }


# ── Összevetés + üzenet ───────────────────────────────────────────────────────

def _line_eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def compare(bookmaker: str, day: str, rows: list) -> dict:
    since = tracking_since()
    placed = []
    for r in _load_ledger():
        if r.get("bookmaker") != bookmaker:
            continue
        ko = _local(r.get("kickoff") or "") if r.get("kickoff") else None
        if ko is None or day_of(ko) != day:
            continue
        placed.append(dict(r, _ko=ko, _players=frozenset(_players(r.get("match")))))

    rows = [x for x in rows if len(x["players"]) == 2 and x["kickoff"] is not None]
    used = set()
    for p in placed:
        best, best_diff = None, None
        for i, x in enumerate(rows):
            if i in used or x["players"] != p["_players"]:
                continue
            if p.get("market") == "OU" and (x["side"] != p.get("pick")
                                            or not _line_eq(x["line"], p.get("line"))):
                continue
            diff = abs((x["kickoff"] - p["_ko"]).total_seconds()) / 60
            if diff <= KICKOFF_TOL_MIN and (best_diff is None or diff < best_diff):
                best, best_diff = i, diff
        p["_acc"] = best
        if best is not None:
            used.add(best)

    since_dt = _local(since) or datetime.min
    day_rows = [x for i, x in enumerate(rows) if i in used or day_of(x["kickoff"]) == day]
    extra = [x for i, x in enumerate(rows)
             if i not in used and day_of(x["kickoff"]) == day
             and x["placed"] is not None and x["placed"] >= since_dt]
    missing = [p for p in placed if p["_acc"] is None]
    odds = [p for p in placed if p["_acc"] is not None and rows[p["_acc"]]["odds"]
            and abs(rows[p["_acc"]]["odds"] - float(p.get("odds") or 0)) > ODDS_TOL]
    acc = [x for i, x in enumerate(rows) if i in used] + extra
    stake = sum(x["stake"] for x in acc)
    ret = sum(x["ret"] for x in acc)
    return {
        "placed": len(placed), "matched": len(placed) - len(missing),
        "missing": missing, "extra": extra, "odds_diff": len(odds),
        "won": sum(x["status"] == "win" for x in acc),
        "lost": sum(x["status"] == "loss" for x in acc),
        "stake": stake, "profit": ret - stake,
        "roi": (ret - stake) / stake * 100 if stake else 0.0,
        "_day_rows": len(day_rows),
    }


def _ft(v: float, sign: bool = False) -> str:
    return (f"{v:+,.0f}" if sign else f"{v:,.0f}").replace(",", " ") + " Ft"


def _ko_txt(p) -> str:
    return f"{p['_ko']:%H:%M} {p.get('match')} | {p.get('pick')} {p.get('line') or ''}".strip()


def build_message(day: str, results: dict) -> str:
    """results: {iroda: compare()-eredmény | {'error': str}}"""
    lines = [f"🧾 Számla-szinkron — {day.replace('-', '.')}."]
    for bm, r in results.items():
        label = BOOKMAKER_LABEL.get(bm, bm)
        lines.append("")
        if r.get("error"):
            lines.append(f"{label}: ⚠️ a fiók nem kérdezhető le — {r['error']}")
            continue
        n = len(r["missing"]) + len(r["extra"])
        if not r["placed"] and not r["extra"]:
            lines.append(f"{label}: nem volt megrakott tipp.")
            continue
        lines.append(f"{label}: megrakott {r['placed']} | számlán {r['matched']} | "
                     + ("✅ nincs eltérés" if n == 0 else f"⚠️ eltérés: {n}"))
        if r["missing"]:
            lines.append(f"Megrakottnak jelölt, de nincs a számlán ({len(r['missing'])}):")
            lines.extend(f"• {_ko_txt(p)}" for p in r["missing"][:LIST_MAX])
        if r["extra"]:
            lines.append(f"Számlán van, de nem a BetPlacer rakta ({len(r['extra'])}):")
            lines.extend(f"• {x['kickoff']:%H:%M} {x['label']}" for x in r["extra"][:LIST_MAX])
        if r["odds_diff"]:
            lines.append(f"ℹ️ Odds-eltérés (tipp vs megrakott): {r['odds_diff']}")
        if r["won"] or r["lost"]:
            roi = f"{r['roi']:+.1f}".replace(".", ",")
            lines.append(f"Számla: {r['won']}–{r['lost']} | tét {_ft(r['stake'])} | "
                         f"{_ft(r['profit'], sign=True)} | ROI {roi}%")
    return "\n".join(lines)
