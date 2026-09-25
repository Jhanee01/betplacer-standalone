"""
Standalone BetPlacer — Vegas.hu fogadási motor.

A FIFA Tipster dashboard BetPlacerének élesben tesztelt Vegas-kódjára épül
(bet_placer.py, STEP 8b). A Vegas sportfogadása az Altenar widgetje (nyitott
shadow DOM — a Playwright CSS-lokátorai átlátnak rajta). Az odds-gombok React-
propjaiban ott az Altenar odd-ID, ugyanaz, amit a nyilvános API is ad → a gombot
ID alapján találjuk meg, nem csapatnév szerint. A Vegas-tipp üzenetében az
`Event ID:` a Vegas saját meccs-azonosítója.

Kattintás, gépelés: ugyanaz az emberi egérmozgás / karakteres gépelés, mint a
Tippmixpro-motorban (bet_engine.human_click).
"""

import gzip
import json
import random
import re
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from bet_engine import (
    log, screenshot, human_click, _rand_ms, _park, _install_resource_blocker,
    _LAUNCH_ARGS, VIEWPORT, PAGE_GOTO_TIMEOUT,
)
from tip_parser import ParsedTip

VEGAS_LOGIN_URL  = "https://vegas.hu/sports?modal=login"
VEGAS_LEAGUE_URL = "https://vegas.hu/sports/elabdarugas/e-battles/{slug}"

# Az Altenar nyilvános API-ja (böngésző és bejelentkezés nélkül is válaszol).
_API_URL = "https://hu-sb2frontend-altenar2.biahosted.com/api/widget/GetEvents"
_API_PARAMS = {
    "culture": "hu-HU", "timezoneOffset": -120, "integration": "vegas.hu",
    "deviceType": 1, "numFormat": "hu-HU", "countryCode": "HU",
    "eventType": 0,          # 0 = kezdés előtti meccsek
    "sportId": 146,          # e-Labdarúgás
}
_API_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Origin": "https://vegas.hu",
    "Referer": "https://vegas.hu/",
    "Accept": "*/*",
    # Tömörítést kérő fejléc nélkül az API 400-at ad (a böngésző/requests mindig küldi).
    "Accept-Encoding": "gzip",
}
_TOTAL_GOALS_MARKET = 18      # "Gólok száma összesen"
_ODD_TYPE = {"OVER": 12, "UNDER": 13}

# Az odds-gombot az odd-ID alapján egy data-attribútummal jelöljük meg, hogy utána
# rendes Playwright-lokátorral (és human_click-kel) kattinthassuk.
_MARK_ODD_JS = """(oddId) => {
  function* walk(r){ for (const e of r.querySelectorAll('*')) { yield e; if (e.shadowRoot) yield* walk(e.shadowRoot); } }
  for (const b of walk(document)) {
    if (b.tagName !== 'BUTTON' || !/OddBoxButton/.test(b.className)) continue;
    const fk = Object.keys(b).find(k => k.startsWith('__reactFiber'));
    let f = fk && b[fk];
    for (let i = 0; i < 4 && f; i++, f = f.return) {
      const odd = f.memoizedProps && f.memoizedProps.odd;
      if (odd) {
        if (odd.id === oddId) {
          b.setAttribute('data-fbp-odd', String(oddId));
          return {name: odd.name, price: odd.price, status: odd.oddStatus};
        }
        break;
      }
    }
  }
  return null;
}"""

_STAKE_SEL = "input[class*='StakeInput']"
_PLACE_SEL = "button[class*='PlaceBetButton']"
_CLEAR_SEL = "button[class*='ClearAllButton']"

# Élesben (2026-09-25) a sikeres fogadás nyugtája: "ID 5460176227 / NYITOTT / Megtartom".
_OK_RE  = re.compile(r"\bID\s*\d{6,}|NYITOTT|sikeres|elfogad|leadva|megtörtént", re.I)
_BAD_RE = re.compile(r"hiba|nem sikerült|elutasít|nincs elegendő|elégtelen|túllép|"
                     r"megváltoz|felfüggeszt|nem elérhető", re.I)


def _slug(champ: str) -> str:
    """'Serie A (2x4 mins)' → 'serie-a-2x4-mins' (a Vegas liga-URL-je)."""
    return re.sub(r"[^a-z0-9]+", "-", champ.lower()).strip("-")


def _fetch_events() -> dict:
    url = f"{_API_URL}?{urllib.parse.urlencode(_API_PARAMS)}"
    req = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


def lookup(event_id: str) -> dict | None:
    """A meccs aktuális O/U piaca az API-ból. None, ha a meccs már nincs a kezdés
    előtti kínálatban (elkezdődött / levették)."""
    data = _fetch_events()
    ev = next((e for e in data.get("events", []) if str(e.get("id")) == str(event_id)), None)
    if ev is None:
        return None
    champs  = {c["id"]: c["name"] for c in data.get("champs", [])}
    markets = {m["id"]: m for m in data.get("markets", [])}
    odds    = {o["id"]: o for o in data.get("odds", [])}
    ou = next((markets[m] for m in ev.get("marketIds", [])
               if m in markets and markets[m].get("typeId") == _TOTAL_GOALS_MARKET), None)
    info = {"champ": champs.get(ev.get("champId"), ""), "line": None}
    if ou is not None:
        prices = {odds[o]["typeId"]: odds[o] for o in ou.get("oddIds", []) if o in odds}
        info["line"] = str(ou.get("sv"))
        for pick, type_id in _ODD_TYPE.items():
            info[pick] = prices.get(type_id)
    return info


# ══════════════════════════════════════════════════════════════════════════════
# Session
# ══════════════════════════════════════════════════════════════════════════════

def is_logged_in(page) -> bool:
    """Kilépett állapotban a fejlécben 'Belépés' gomb (button.header__login) látszik."""
    try:
        return page.locator("button[class*='header__login'] >> visible=true").count() == 0
    except Exception:
        return False


def _dismiss_cookie(page):
    """Cookie-ablak elvetése a legszűkebb opcióval (csak a nélkülözhetetlen sütik).
    Az ablak eltakarja a gombokat; az 'elutasítás' gombja a nyitó nézetben rejtett,
    ezért a Cookiebot saját API-ján adjuk meg ugyanezt. A döntés a contextben megmarad."""
    try:
        if page.locator("#CybotCookiebotDialog >> visible=true").count() == 0:
            return
        # A mentés után az ablak magától csak újratöltéskor tűnne el → hide().
        page.evaluate("() => { if (window.Cookiebot) {"
                      " Cookiebot.submitCustomConsent(false, false, false); Cookiebot.hide(); } }")
        page.wait_for_timeout(800)
        log("  cookie banner elvetve (csak nélkülözhetetlen sütik)")
    except Exception as e:
        log(f"  cookie banner elvetése sikertelen: {e}")


def _login_once(page, username: str, password: str):
    log("Vegas: navigálás a belépéshez...")
    page.goto(VEGAS_LOGIN_URL, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
    page.wait_for_timeout(3000)
    _dismiss_cookie(page)
    if is_logged_in(page):
        return
    try:
        page.wait_for_selector("input[name='email']", state="visible", timeout=15000)
    except PWTimeout:
        screenshot(page, "vegas_login_form_timeout")
        raise RuntimeError("Vegas: a belépő-űrlap nem jelent meg.")

    for sel, value in (("input[name='email']", username),
                       ("input[name='password']", password)):
        human_click(page, page.locator(sel).first)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(value, delay=random.randint(60, 130))
        time.sleep(_rand_ms(400, 800))
    log("  e-mail és jelszó megadva")

    human_click(page, page.locator("button[class*='login__button']").first)
    log("  login elküldve, várakozás...")
    page.wait_for_timeout(5000)
    screenshot(page, "vegas_login_utan")


def ensure_logged_in(page, username: str, password: str, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        log(f"Vegas bejelentkezési kísérlet {attempt}/{max_retries}...")
        try:
            _login_once(page, username, password)
        except Exception as e:
            log(f"  login hiba: {e}")
        page.wait_for_timeout(2000)
        if is_logged_in(page):
            log("Vegas: bejelentkezés sikeres.")
            return
    screenshot(page, "vegas_login_vegul_sikertelen")
    raise RuntimeError(f"Vegas bejelentkezés {max_retries} kísérlet után is sikertelen.")


# ══════════════════════════════════════════════════════════════════════════════
# Szelvény
# ══════════════════════════════════════════════════════════════════════════════

def _slip_text(page) -> str:
    try:
        return page.locator("div[class*='BetSlipContainer']").first.inner_text(timeout=1500)
    except Exception:
        return ""


def _clear_slip(page):
    """Üres szelvénnyel indulunk — egy korábbi (pl. félbemaradt) kiválasztás ne
    kerüljön kötésbe az új tippel."""
    btn = page.locator(f"{_CLEAR_SEL} >> visible=true").first
    if btn.count() > 0:
        human_click(page, btn)
        page.wait_for_timeout(1000)
        log("  szelvény kiürítve")


def _find_odd(page, odd_id: int) -> dict | None:
    """Megjelöli az odd-ID-hez tartozó gombot; ha a lista még nem rajzolta ki,
    lejjebb görget és újranéz."""
    for _ in range(6):
        info = page.evaluate(_MARK_ODD_JS, odd_id)
        if info:
            return info
        page.mouse.wheel(0, random.randint(500, 800))
        page.wait_for_timeout(random.randint(700, 1200))
    return None


def _detect_result(page, before: str) -> tuple[bool | None, str]:
    """A 'Fogadok' utáni állapot: (True, szöveg) siker, (False, szöveg) elutasítás,
    (None, szöveg) ha nem egyértelmű. Csak a kattintás UTÁN megjelent szövegsorokat
    nézzük — a szelvény állandó feliratai ne tűnjenek hibának (egy tévesen hibásnak
    hitt fogadás újrapróbálása dupla fogadás lenne)."""
    old_lines = set(before.splitlines())
    deadline = time.time() + 20
    text = ""
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        text = _slip_text(page)
        new = "\n".join(l for l in text.splitlines() if l not in old_lines)
        if _OK_RE.search(new):
            return True, new
        if page.locator(f"{_STAKE_SEL} >> visible=true").count() == 0:
            return True, new   # a kiválasztás lekerült a szelvényről
        if _BAD_RE.search(new):
            return False, new
    return None, text


def place_tip(page, tip: ParsedTip, username: str, password: str,
              stake: int, dry_run: bool, out: dict) -> str:
    """Egy Vegas-tipp megrakása. Visszatérés: 'ok' | 'fail' | 'notfound' |
    'line_changed' (mint a Tippmixpro-motornál). `out`-ba: 'error' (ok szövege),
    'bet_ref' (szelvény ID)."""
    log(f"Vegas tipp: {tip}")
    pick = tip.pick.upper()
    if tip.market != "OU" or pick not in _ODD_TYPE or tip.line is None:
        out["error"] = f"Vegas: csak gól alatt/felett támogatott ({tip.market} {tip.pick})"
        return "fail"
    if not tip.event_id:
        out["error"] = "Vegas: a tippben nincs Event ID"
        return "fail"

    for attempt in range(1, 4):
        if attempt > 1:
            log(f"  {attempt}. kísérlet...")

        # 1. Friss piac az API-ból: létezik-e még, és a tipp vonalán áll-e.
        try:
            info = lookup(tip.event_id)
        except Exception as e:
            log(f"  Vegas API hiba: {e}")
            time.sleep(_rand_ms(2000, 4000))
            continue
        if info is None:
            out["error"] = "a meccs már nincs kiírva a Vegason (elkezdődött / levették)"
            log(f"  {out['error']}")
            return "fail"
        if info["line"] is None:
            out["error"] = "nincs gól alatt/felett piac a meccsen"
            log(f"  {out['error']}")
            return "fail"
        if float(info["line"]) != float(tip.line):
            log(f"  LINE ELMOZDULT: tipp {tip.line} -> piacon {info['line']} — kihagyva")
            return "line_changed"
        odd = info.get(pick)
        if not odd or odd.get("oddStatus"):
            log("  a piac most felfüggesztve — újrapróbálás")
            page.wait_for_timeout(random.randint(8000, 15000))
            continue

        # 2. A liga oldala, bejelentkezés-ellenőrzéssel.
        url = VEGAS_LEAGUE_URL.format(slug=_slug(info["champ"]))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
            page.wait_for_selector("button[class*='OddBoxButton']", timeout=30000)
        except Exception as e:
            log(f"  navigáció hiba: {e}")
            screenshot(page, f"vegas_nav_hiba_{attempt}")
            continue
        page.wait_for_timeout(random.randint(1500, 3000))
        _dismiss_cookie(page)
        if not is_logged_in(page):
            log("  Vegas session kiesett — újra bejelentkezés...")
            ensure_logged_in(page, username, password)
            continue

        # 3. Tiszta szelvény, majd az odds-gomb (odd-ID alapján).
        _clear_slip(page)
        found = _find_odd(page, odd["id"])
        if not found:
            log(f"  odds-gomb nem található az oldalon (odd {odd['id']})")
            screenshot(page, f"vegas_odds_nem_talalt_{attempt}")
            continue
        log(f"  O/U {tip.line} {pick} ({found['name']} @ {found['price']}) → kattintás")
        human_click(page, page.locator(f"[data-fbp-odd='{odd['id']}']").first)

        # 4. Szelvény: pontosan egy kiválasztás, tét beírása.
        try:
            page.wait_for_selector(f"{_STAKE_SEL} >> visible=true", timeout=12000)
        except PWTimeout:
            log("  szelvény nem jelent meg")
            screenshot(page, f"vegas_szelveny_nincs_{attempt}")
            continue
        n_sel = page.locator(f"{_STAKE_SEL} >> visible=true").count()
        if n_sel != 1:
            log(f"  a szelvényen {n_sel} kiválasztás van — kiürítés és újra")
            _clear_slip(page)
            continue

        inp = page.locator(f"{_STAKE_SEL} >> visible=true").first
        human_click(page, inp)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(200)
        page.keyboard.type(str(stake), delay=random.randint(60, 100))
        page.wait_for_timeout(random.randint(600, 1200))
        log(f"  tét beírva: {stake} Ft")
        screenshot(page, "vegas_fogadas_elott")

        if dry_run:
            log("  [DRY RUN] fogadás kihagyva")
            _clear_slip(page)
            return "ok"

        # 5. Fogadok + eredmény.
        fogad = page.locator(f"{_PLACE_SEL} >> visible=true").first
        if fogad.count() == 0:
            log("  Fogadok gomb nem található!")
            screenshot(page, "vegas_fogad_gomb_nincs")
            continue
        before = _slip_text(page)
        log("  Fogadok gomb kattintás")
        human_click(page, fogad)
        ok, text = _detect_result(page, before)
        screenshot(page, "vegas_fogadas_utan")
        short = " ".join(text.split())[:200]
        if ok is False:
            log(f"  Vegas elutasította: {short}")
            out["error"] = f"a Vegas elutasította: {short}"
            _clear_slip(page)
            return "fail"
        if ok is None:
            # Nem egyértelmű — újrapróbálni nem szabad (dupla fogadás veszélye).
            log(f"  eredmény nem egyértelmű, megrakottnak vesszük: {short}")
            out["bet_ref"] = "nem egyértelmű — ellenőrizd a Vegason!"
        else:
            m = re.search(r"\bID\s*(\d{6,})", text)
            if m:
                out["bet_ref"] = m.group(1)
            log(f"  fogadás sikeresen leadva (szelvény ID: {out.get('bet_ref', '?')})")
        return "ok"

    log(f"  3 kísérlet után sem sikerült: {tip}")
    return "fail"


# ══════════════════════════════════════════════════════════════════════════════
# VegasEngine — ugyanaz a felület, mint a BetEngine (start / stop / place)
# ══════════════════════════════════════════════════════════════════════════════

class VegasEngine:
    def __init__(self, username: str, password: str, stake: int, dry_run: bool = False):
        self._username = username
        self._password = password
        self._stake    = stake
        self._dry_run  = dry_run
        self._pw       = None
        self._browser  = None
        self._page     = None
        self.last_error   = ""   # az utolsó sikertelen fogadás oka (értesítéshez)
        self.last_bet_ref = ""   # az utolsó sikeres fogadás szelvény-ID-je

    def start(self):
        self._pw      = sync_playwright().__enter__()
        self._browser = self._pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        context       = self._browser.new_context(viewport=VIEWPORT, locale="hu-HU")
        self._page    = context.new_page()
        _install_resource_blocker(self._page)
        ensure_logged_in(self._page, self._username, self._password)
        _park(self._page)   # az első tippig ne tartsunk nyitva élő oldalt
        log("VegasEngine kész.")

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.__exit__(None, None, None)
        except Exception:
            pass
        self._page    = None
        self._browser = None

    def place(self, tip: ParsedTip, stake: int = None) -> str:
        """Visszatérés: 'ok' | 'fail' | 'notfound' | 'line_changed'."""
        self.last_error = self.last_bet_ref = ""
        if self._page is None:
            return "fail"
        out: dict = {}
        try:
            return place_tip(self._page, tip, self._username, self._password,
                             self._stake if stake is None else int(stake),
                             self._dry_run, out)
        except Exception as exc:
            log(f"  kivétel: {exc}")
            out.setdefault("error", f"kivétel: {exc}")
            return "fail"
        finally:
            self.last_error   = out.get("error", "")
            self.last_bet_ref = out.get("bet_ref", "")
            _park(self._page)
