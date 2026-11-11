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
import os
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
from paths import APP_DIR

# A Vegas bejelentkezett sessionje (sütik + localStorage) — gépenként, a frissítés
# nem írja felül, nem kerül a repóba (.gitignore, make_release kizárja).
VEGAS_SESSION_JSON = APP_DIR / "vegas_session.json"

VEGAS_LOGIN_URL  = "https://vegas.hu/sports?modal=login"
# 2026-11-05: az új oldalon a régi /sports/elabdarugas/e-battles/<liga> cím a sport-
# főoldalra visz (ott nincs rajta a meccs) — a meccs saját oldalát nyitjuk meg.
VEGAS_EVENT_URL  = "https://vegas.hu/sports?page=event&sportId=146&eventId={event_id}"

# Az Altenar nyilvános API-ja (böngésző és bejelentkezés nélkül is válaszol).
_API_URL = "https://hu-sb2frontend-altenar2.biahosted.com/api/widget/GetEvents"
# 2026-11-03 este a Vegas új oldalra váltott: az Altenar „integration” neve
# "vegas.hu" → "vegashu2" (a régivel az API ÜRES listát ad), a timezoneOffset pedig
# a helyi időszámítást követi (téli idő: -60, nyári: -120).
VEGAS_INTEGRATION = "vegashu2"


def _tz_offset() -> int:
    from datetime import datetime as _dt
    off = _dt.now().astimezone().utcoffset()
    return -int(off.total_seconds() // 60) if off is not None else -60


_API_PARAMS = {
    "culture": "hu-HU", "timezoneOffset": _tz_offset(), "integration": VEGAS_INTEGRATION,
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
    _API_PARAMS["timezoneOffset"] = _tz_offset()   # időszámítás-váltáskor is
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
    """Belépve a fejlécben a felhasználó-menü ([data-qid=navigation-user]) látszik.

    2026-11-05: a Vegas új fejlécet kapott — a régi „button.header__login” megszűnt,
    ezért a régi (tagadó) ellenőrzés MINDIG belépettnek látta az oldalt, és a bot be
    sem jelentkezett. Most pozitív jelre nézünk."""
    try:
        if page.locator("[data-qid='nav-login-btn'] >> visible=true").count() > 0:
            return False
        return page.locator("[data-qid='navigation-user'] >> visible=true").count() > 0
    except Exception:
        return False


def _notify(text: str) -> None:
    try:
        import os
        import notifier
        tok, chat = os.getenv("NOTIFY_BOT_TOKEN", ""), os.getenv("NOTIFY_CHAT_ID", "")
        if tok and chat:
            notifier.send_message(tok, chat, text)
    except Exception:
        pass


_TERMS_FAIL_NOTIFIED = [0.0]


def _accept_terms_updated(page) -> bool:
    """Az új oldal óta (2026-11-05) a módosult szabályzat a „Megváltozott a
    szabályzatunk” ablakban jön (.terms-updated…), és letakarja az oldalt. Bezáró
    gombja nincs, csak „Elfogadás”, ami a görgethető szöveg végéig görgetés után
    aktív, és egy ablakban több dokumentum jöhet egymás után. A felhasználó kérésére
    (kb. hetente változik) automatikusan elfogadjuk: kép, napló, Telegram."""
    box = page.locator("[class*='terms-updated__terms-box'] >> visible=true")
    try:
        if box.count() == 0:
            return False
        screenshot(page, "vegas_szabalyzat_ablak")
        accepted = 0
        for _ in range(6):
            if box.count() == 0:
                break
            terms = page.locator("[class*='terms-updated__terms-box--terms']").first
            terms.hover()
            for _ in range(30):
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(250)
                if terms.evaluate("e => e.scrollTop + e.clientHeight >= e.scrollHeight - 2"):
                    break
            btn = page.locator("[class*='terms-updated'] [role='button']:has-text('Elfogadás')"
                               " >> visible=true")
            if btn.count() == 0:
                raise RuntimeError("az Elfogadás gomb nem található")
            for _ in range(10):
                if "disabled" not in (btn.first.get_attribute("class") or ""):
                    break
                page.wait_for_timeout(500)
            human_click(page, btn.first)
            page.wait_for_timeout(2000)
            accepted += 1
        if box.count() > 0:
            raise RuntimeError(f"az ablak {accepted} elfogadás után is kint maradt")
    except Exception as e:
        log(f"  Vegas: „Megváltozott a szabályzatunk” elfogadása sikertelen: {e}")
        if time.time() - _TERMS_FAIL_NOTIFIED[0] > 3600:
            _TERMS_FAIL_NOTIFIED[0] = time.time()
            screenshot(page, "vegas_szabalyzat_sikertelen")
            _notify("⚠️ Vegas: a „Megváltozott a szabályzatunk” ablakot a BetPlacer nem tudta "
                    "elfogadni. Amíg kint van, nem tud a Vegason fogadni — fogadd el kézzel a vegas.hu-n.")
        return False
    log("  Vegas: a megváltozott szabályzat automatikusan ELFOGADVA")
    screenshot(page, "vegas_szabalyzat_elfogadva")
    _notify("ℹ️ Vegas: a BetPlacer elfogadta a megváltozott szabályzatot "
            "(„Megváltozott a szabályzatunk”). Érdemes elolvasni: vegas.hu")
    return True


def _cookie_consent(page, wait_ms: int = 0) -> None:
    """A Cookiebot döntés leadása (csak nélkülözhetetlen sütik) — akkor is, ha az
    ablak MÉG nem jelent meg. 2026-11-05: friss böngészőben a süti-ablak a belépő-
    űrlap megnyitása UTÁN ugrott fel, letakarta az űrlapot, és a belépés elbukott."""
    try:
        if wait_ms:
            try:
                page.wait_for_function("() => !!window.Cookiebot", timeout=wait_ms)
            except Exception:
                pass
        done = page.evaluate("""() => {
            const cb = window.Cookiebot;
            if (!cb) return 'none';
            const d = document.querySelector('#CybotCookiebotDialog');
            const shown = !!d && getComputedStyle(d).display !== 'none';
            if (cb.hasResponse && !shown) return 'had';
            cb.submitCustomConsent(false, false, false);
            cb.hide();
            return 'set';
        }""")
        if done == "set":
            page.wait_for_timeout(800)
            log("  cookie banner elvetve (csak nélkülözhetetlen sütik)")
    except Exception as e:
        log(f"  cookie banner elvetése sikertelen: {e}")


def _accept_terms(page) -> bool:
    if _accept_terms_updated(page):
        return True
    """A frissített „Részvételi Szabályzat" elfogadása (a felhasználó kérésére
    automatikusan, 2026-09-30). Belépéskor felugró ablak; amíg nincs elfogadva, a
    belépés nem fejeződik be. CSAK akkor kattint, ha ugyanabban az ablakban van a
    „Frissült a Részvételi Szabályzat" szöveg ÉS az „Elfogadás" gomb (más ablak —
    pl. süti — gombját így sosem nyomja meg). Naplóz + Telegram-értesítést küld."""
    try:
        # A gombot IS tartalmazó div kell: a .last enélkül a legbelső, csak szöveget
        # tartalmazó bekezdés lett, amiben nincs gomb (2026-10-24: ezért nem fogadott).
        doboz = page.locator("div:has-text('Frissült a Részvételi Szabályzat'):has(button)"
                             " >> visible=true")
        if doboz.count() == 0:
            return False
        btn = doboz.last.get_by_role("button", name=re.compile(r"^\s*Elfogadás\s*$", re.I))
        if btn.count() == 0 or not btn.first.is_visible():
            return False
        human_click(page, btn.first)
        page.wait_for_timeout(1500)
        log("  Vegas: a frissített Részvételi Szabályzat automatikusan ELFOGADVA")
        screenshot(page, "vegas_szabalyzat_elfogadva")
        try:
            import os
            import notifier
            tok, chat = os.getenv("NOTIFY_BOT_TOKEN", ""), os.getenv("NOTIFY_CHAT_ID", "")
            if tok and chat:
                notifier.send_message(tok, chat, "ℹ️ Vegas: a BetPlacer elfogadta a frissített "
                                      "Részvételi Szabályzatot. Érdemes elolvasni: vegas.hu")
        except Exception:
            pass
        return True
    except Exception as e:
        log(f"  Vegas szabályzat-ablak kezelése sikertelen: {e}")
        return False


# A Vegas „popup-center” reklámablakai (pl. 2026-10-14: „LEGYEN TIÉD AZ iPHONE 17e!”)
# oldalmegnyitáskor ugranak fel és eltakarják az odds-gombot. CSAK a bezáró gombra
# kattintunk (X vagy alsó „Bezárás”), a „RÉSZLETEK” gombra soha.
_POPUP_CLOSE_SEL = ("[data-popup-center-dialog] [data-popup-center-close-button], "
                    "[data-popup-center-dialog] .popup-center__metaDismiss")


def _close_popups(page):
    """Bezárja a felugró reklámablakokat (egymás után több is jöhet)."""
    for _ in range(3):
        try:
            btn = page.locator(f"{_POPUP_CLOSE_SEL} >> visible=true")
            if btn.count() == 0:
                return
            title = page.locator("[data-popup-center-dialog]").first.get_attribute("aria-label") or ""
            human_click(page, btn.first)
            page.wait_for_timeout(1000)
            log(f"  Vegas reklámablak bezárva: {title[:60]}")
        except Exception as e:
            log(f"  Vegas reklámablak bezárása sikertelen: {e}")
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                pass
            return


def _dismiss_cookie(page):
    """Cookie-ablak elvetése a legszűkebb opcióval (csak a nélkülözhetetlen sütik).
    Az ablak eltakarja a gombokat; az 'elutasítás' gombja a nyitó nézetben rejtett,
    ezért a Cookiebot saját API-ján adjuk meg ugyanezt. A döntés a contextben megmarad.
    Emellett a 60 percenként felugró „Emlékeztető” (felelős játék) ablakot is bezárja a
    „Tovább játszom” gombbal — különben az ablak eltakarja az odds-gombot és a szelvény
    nem nyílik meg. A frissített Részvételi Szabályzat ablakát elfogadja (_accept_terms),
    a reklámablakokat bezárja (_close_popups)."""
    _accept_terms(page)
    _close_popups(page)
    try:
        cont = page.get_by_role("button", name=re.compile(r"Tovább játszom", re.I))
        if cont.count() > 0 and cont.first.is_visible():
            human_click(page, cont.first)
            page.wait_for_timeout(1000)
            log("  Vegas emlékeztető ablak bezárva (Tovább játszom)")
    except Exception as e:
        log(f"  Vegas emlékeztető bezárása sikertelen: {e}")
    _cookie_consent(page)


# ── Session megtartása + kézi belépés (2026-11-11) ────────────────────────────
# A belépő-űrlapon Cloudflare „Igazolja, hogy Ön ember” ellenőrzés van; a program
# ehhez NEM nyúl. A belépést a felhasználó végzi el kézzel (Vegas belépés gomb →
# vegas_login_manual.py, sima Chrome-ban), a motor a mentett sessiont használja,
# és belépve rendszeresen frissíti.

class VegasManualLoginNeeded(RuntimeError):
    """A Vegas belépéshez emberi ellenőrzés kell — újrapróbálkozásnak nincs értelme."""


MANUAL_LOGIN_MSG = ("Vegas: kézi belépés kell (Cloudflare-ellenőrzés) — "
                    "kattints a „Vegas belépés” gombra")
_MANUAL_NOTIFIED = [0.0]
_SESSION_APPLIED = [0.0]   # a contextbe utoljára betöltött sessionfájl mtime-ja


def session_mtime() -> float:
    try:
        return VEGAS_SESSION_JSON.stat().st_mtime
    except OSError:
        return 0.0


def save_session(page) -> None:
    """A context sütijei + localStorage-a a sessionfájlba (atomi csere)."""
    try:
        tmp = VEGAS_SESSION_JSON.with_suffix(".tmp")
        page.context.storage_state(path=str(tmp))
        os.replace(tmp, VEGAS_SESSION_JSON)
        _SESSION_APPLIED[0] = session_mtime()
    except Exception as e:
        log(f"  Vegas session mentése sikertelen: {e}")


def _apply_session_file(page) -> bool:
    """Frissebb (pl. kézi belépésből származó) sessionfájl betöltése a meglévő
    contextbe. True, ha betöltött valamit — utána újra kell tölteni az oldalt."""
    mtime = session_mtime()
    if not mtime or mtime <= _SESSION_APPLIED[0]:
        return False
    try:
        state = json.loads(VEGAS_SESSION_JSON.read_text(encoding="utf-8"))
        page.context.add_cookies(state.get("cookies", []))
        origin = page.evaluate("() => location.origin")
        for o in state.get("origins", []):
            if o.get("origin") == origin:
                page.evaluate("""(items) => { for (const it of items)
                                     localStorage.setItem(it.name, it.value); }""",
                              o.get("localStorage", []))
        _SESSION_APPLIED[0] = mtime
        log("  Vegas: frissebb mentett session betöltve (kézi belépésből)")
        return True
    except Exception as e:
        log(f"  Vegas sessionfájl betöltése sikertelen: {e}")
        return False


def _human_check_pending(page, wait_ms: int = 4000) -> bool:
    """True, ha az űrlapon Cloudflare emberi ellenőrzés van, és magától (a program
    beavatkozása nélkül) nem engedett át. A widgethez a program nem nyúl."""
    def present():
        try:
            if any("challenges.cloudflare.com" in (f.url or "") for f in page.frames):
                return True
            return page.locator("input[name='cf-turnstile-response']").count() > 0
        except Exception:
            return False

    def passed():
        try:
            return page.evaluate("""() => [...document.querySelectorAll(
                    'input[name="cf-turnstile-response"]')].some(i => i.value)""")
        except Exception:
            return False

    page.wait_for_timeout(wait_ms)
    if not present():
        return False
    for _ in range(10):
        if passed():
            return False
        page.wait_for_timeout(1000)
    return True


def _login_once(page, username: str, password: str):
    log("Vegas: navigálás a belépéshez...")
    page.goto(VEGAS_LOGIN_URL, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
    page.wait_for_timeout(3000)
    if _apply_session_file(page):
        page.reload(wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
        page.wait_for_timeout(3000)
    _cookie_consent(page, wait_ms=10000)   # a süti-döntés MÉG az űrlap előtt
    _dismiss_cookie(page)
    if is_logged_in(page):
        return
    # Új fejléc (2026-11-05): a ?modal=login már nem nyitja meg az űrlapot, a
    # fejléc „Bejelentkezés” gombjára kell kattintani; a mező neve username lett.
    user_sel = "input[name='username'], input[name='email']"
    try:
        if page.locator(f"{user_sel} >> visible=true").count() == 0:
            login_btn = page.locator("[data-qid='nav-login-btn'] >> visible=true")
            if login_btn.count() > 0:
                human_click(page, login_btn.first)
        page.wait_for_selector(f"{user_sel} >> visible=true", timeout=15000)
    except PWTimeout:
        screenshot(page, "vegas_login_form_timeout")
        raise RuntimeError("Vegas: a belépő-űrlap nem jelent meg.")
    _cookie_consent(page)                  # ha közben mégis felugrott
    if page.locator(f"{user_sel} >> visible=true").count() == 0:
        screenshot(page, "vegas_login_form_eltunt")
        raise RuntimeError("Vegas: a belépő-űrlapot egy felugró ablak bezárta.")
    if _human_check_pending(page):
        screenshot(page, "vegas_emberi_ellenorzes")
        raise VegasManualLoginNeeded(MANUAL_LOGIN_MSG)

    for sel, value in ((f"{user_sel} >> visible=true", username),
                       ("input[name='password'] >> visible=true", password)):
        human_click(page, page.locator(sel).first)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(value, delay=random.randint(60, 130))
        time.sleep(_rand_ms(400, 800))
    log("  e-mail és jelszó megadva")

    submit = page.locator("button.btn-action:has-text('Bejelentkezés') >> visible=true")
    if submit.count() == 0:
        submit = page.locator("button[class*='login__button'] >> visible=true")
    if submit.count() == 0:
        screenshot(page, "vegas_login_gomb_nincs")
        raise RuntimeError("Vegas: a belépés gomb nem látszik (eltakarja egy ablak?)")
    human_click(page, submit.first)
    log("  login elküldve, várakozás...")
    page.wait_for_timeout(5000)
    if _accept_terms(page):          # a szabályzat-ablak a belépés után ugrik fel
        page.wait_for_timeout(2000)
    _close_popups(page)              # a reklámablak is
    screenshot(page, "vegas_login_utan")


def ensure_logged_in(page, username: str, password: str, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        log(f"Vegas bejelentkezési kísérlet {attempt}/{max_retries}...")
        try:
            _login_once(page, username, password)
        except VegasManualLoginNeeded as e:
            log(f"  {e}")
            if time.time() - _MANUAL_NOTIFIED[0] > 3600:
                _MANUAL_NOTIFIED[0] = time.time()
                _notify("⚠️ Vegas: a belépéshez emberi ellenőrzés (Cloudflare) kell, a "
                        "BetPlacer nem tud belépni. Lépj be kézzel: BetPlacer → Vegas belépés. "
                        "Utána a program magától folytatja.")
            raise
        except Exception as e:
            log(f"  login hiba: {e}")
        page.wait_for_timeout(2000)
        if is_logged_in(page):
            log("Vegas: bejelentkezés sikeres.")
            _accept_terms_updated(page)
            screenshot(page, "vegas_bejelentkezve")
            save_session(page)
            _MANUAL_NOTIFIED[0] = 0.0
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

    # Az utolsó kísérlet kudarcának fajtája: ha a meccs / odds-gomb nem volt az oldalon,
    # "notfound" — a hívó később újrapróbálja (2026-10-10: a 6 órával előre kiadott
    # 22:58-as tipp gombja még nem volt a liga-oldalon, később elsőre ment).
    last = "fail"
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
            out["error"] = "a meccs nincs kiírva a Vegason (még nem / már nem)"
            log(f"  {out['error']}")
            return "notfound"
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

        # 2. A meccs oldala, bejelentkezés-ellenőrzéssel.
        url = VEGAS_EVENT_URL.format(event_id=tip.event_id)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
            try:
                page.wait_for_selector("button[class*='OddBoxButton']", timeout=12000)
            except Exception:
                # 2026-10-21: az „Emlékeztető” ablak blokkolta az oldal betöltését —
                # előbb bezárjuk, és csak utána várunk újra.
                _dismiss_cookie(page)
                page.wait_for_selector("button[class*='OddBoxButton']", timeout=30000)
        except Exception as e:
            log(f"  navigáció hiba: {e}")
            screenshot(page, f"vegas_nav_hiba_{attempt}")
            last = "fail"
            continue
        page.wait_for_timeout(random.randint(1500, 3000))
        _dismiss_cookie(page)
        if not is_logged_in(page):
            log("  Vegas session kiesett — újra bejelentkezés...")
            ensure_logged_in(page, username, password)
            last = "fail"
            continue

        # 3. Tiszta szelvény, majd az odds-gomb (odd-ID alapján).
        _dismiss_cookie(page)
        _clear_slip(page)
        found = _find_odd(page, odd["id"])
        if not found:
            log(f"  odds-gomb nem található az oldalon (odd {odd['id']})")
            screenshot(page, f"vegas_odds_nem_talalt_{attempt}")
            out["error"] = "az odds-gomb nem található a Vegas liga-oldalán"
            last = "notfound"
            continue
        last = "fail"
        log(f"  O/U {tip.line} {pick} ({found['name']} @ {found['price']}) → kattintás")
        _close_popups(page)    # késve felugró reklám ne kapja el a kattintást
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
        _dismiss_cookie(page)
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
    return last


# ══════════════════════════════════════════════════════════════════════════════
# Real Event — valódi foci / kézi / kosár meccsek (a dashboard BetPlacer mintájára)
# ══════════════════════════════════════════════════════════════════════════════
# A tipp az Altenar event-ID-t és odd-ID-t hordozza. A meccsoldal címe a nevekből
# képzett slug; a vegas.hu resolve-végpontja igazolja, hogy PONTOSAN erre az
# event-ID-re mutat (különben nem fogadunk). A csukott piacot a fejlécére kattintva
# nyitjuk ki (emberi egérmozgással).

_DETAILS_URL = "https://hu-sb2frontend-altenar2.biahosted.com/api/widget/GetEventDetails"
_RESOLVE_URL = "https://vegas.hu/altenarSEO/resolve?path="
_AKTIV = 0                 # oddStatus: 0 = fogadható
# Ennyivel eshet legfeljebb az odds a tippben közölthöz képest (a fair ár nincs az
# üzenetben; az edge-küszöb 5% EV, így kis esés még value marad).
REAL_ODDS_TOLERANCE = 0.03

_EXPAND_MARKET_JS = """(nev) => {
  function* walk(r){ for (const e of r.querySelectorAll('*')) { yield e; if (e.shadowRoot) yield* walk(e.shadowRoot); } }
  for (const h of walk(document)) {
    const c = (h.className || '').toString();
    if (/EventDetailsMarketHeader-/.test(c) && (h.innerText || '').split('\\n')[0].trim() === nev) {
      h.scrollIntoView({block: 'center'}); h.setAttribute('data-fbp-market', '1'); return true;
    }
  }
  return false;
}"""


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


def _event_details(event_id: str) -> dict:
    _API_PARAMS["timezoneOffset"] = _tz_offset()
    p = {k: v for k, v in _API_PARAMS.items() if k not in ("eventType", "sportId")}
    p["eventId"] = event_id
    return _get_json(f"{_DETAILS_URL}?{urllib.parse.urlencode(p)}")


def _slug_hu(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _event_path(raw: dict) -> str | None:
    """A meccsoldal címe, a resolve-végponttal igazolva (None: nem azonosítható)."""
    comp = raw.get("competitors") or []
    nevek = [f"{comp[0]['name']} vs {comp[1]['name']}"] if len(comp) >= 2 else []
    nevek.append(raw.get("name") or "")
    for nev in nevek:
        path = "/sports/%s/%s/%s/%s" % (_slug_hu(raw["sport"]["name"]),
                                        _slug_hu(raw["category"]["name"]),
                                        _slug_hu(raw["champ"]["name"]), _slug_hu(nev))
        try:
            req = urllib.request.Request(_RESOLVE_URL + urllib.parse.quote(path),
                                         headers={"User-Agent": _API_HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log(f"  Vegas resolve hiba: {e}")
            continue
        if str((res.get("params") or {}).get("eventId")) == str(raw.get("id")):
            return path
    return None


def place_real_tip(page, tip, username: str, password: str,
                   stake: int, dry_run: bool, out: dict) -> str:
    """Real Event tipp a Vegason. 'ok' | 'fail' | 'skipped' (odds esett / levették)."""
    eid, oid = int(tip.event_id), int(tip.odds_id)
    log(f"Real Event (Vegas) tipp: {tip}")
    for attempt in range(1, 4):
        if attempt > 1:
            log(f"  {attempt}. kísérlet...")
        try:
            raw = _event_details(eid)
        except Exception as e:
            log(f"  Vegas API hiba: {e}")
            time.sleep(_rand_ms(2000, 4000))
            continue
        odd = next((o for o in raw.get("odds", []) if o.get("id") == oid), None)
        if odd is None:
            out["error"] = "a kimenetel már nincs kiírva (levették / elkezdődött)"
            log(f"  {out['error']}")
            return "skipped"
        if odd.get("oddStatus") != _AKTIV or not odd.get("price"):
            log("  a kimenetel most felfüggesztve — újrapróbálás")
            page.wait_for_timeout(random.randint(8000, 15000))
            continue
        price = float(odd["price"])
        if price < float(tip.odds) * (1 - REAL_ODDS_TOLERANCE):
            out["error"] = (f"ODDS ESETT: tipp {tip.odds} → most {price:.2f} — "
                            f"fogadás kihagyva")
            log(f"  {out['error']}")
            return "skipped"
        piacnev = next((m.get("name") for m in raw.get("markets", [])
                        if oid in [i for sor in (m.get("desktopOddIds") or []) for i in sor]
                        or oid in (m.get("oddIds") or [])), None)

        path = _event_path(raw)
        if not path:
            out["error"] = "a meccsoldal címe nem azonosítható biztosan"
            log(f"  {out['error']}")
            return "fail"
        try:
            page.goto("https://vegas.hu" + path, wait_until="domcontentloaded",
                      timeout=PAGE_GOTO_TIMEOUT)
            try:
                page.wait_for_selector("button[class*='OddBoxButton']", timeout=12000)
            except Exception:
                _dismiss_cookie(page)   # „Emlékeztető” ablak blokkolhatja a betöltést
                page.wait_for_selector("button[class*='OddBoxButton']", timeout=30000)
        except Exception as e:
            log(f"  navigáció hiba: {e}")
            screenshot(page, f"real_vegas_nav_hiba_{attempt}")
            continue
        page.wait_for_timeout(random.randint(1500, 3000))
        _dismiss_cookie(page)
        if not is_logged_in(page):
            log("  Vegas session kiesett — újra bejelentkezés...")
            ensure_logged_in(page, username, password)
            continue

        _clear_slip(page)
        found = page.evaluate(_MARK_ODD_JS, oid)
        if not found and piacnev and page.evaluate(_EXPAND_MARKET_JS, piacnev):
            page.wait_for_timeout(random.randint(400, 900))
            human_click(page, page.locator("[data-fbp-market='1']").first)
            log(f"  piac kinyitva: {piacnev}")
            page.wait_for_timeout(random.randint(1000, 1800))
            found = _find_odd(page, oid)
        if not found:
            log(f"  odds-gomb nem található az oldalon (odd {oid}, piac {piacnev})")
            screenshot(page, f"real_vegas_odds_nem_talalt_{attempt}")
            continue
        log(f"  {tip.piac} — {found['name']} @ {found['price']} → kattintás")
        _close_popups(page)    # késve felugró reklám ne kapja el a kattintást
        human_click(page, page.locator(f"[data-fbp-odd='{oid}']").first)

        try:
            page.wait_for_selector(f"{_STAKE_SEL} >> visible=true", timeout=12000)
        except PWTimeout:
            log("  szelvény nem jelent meg")
            screenshot(page, f"real_vegas_szelveny_nincs_{attempt}")
            continue
        if page.locator(f"{_STAKE_SEL} >> visible=true").count() != 1:
            log("  a szelvényen nem pontosan egy kiválasztás van — kiürítés és újra")
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
        screenshot(page, "real_vegas_fogadas_elott")
        if dry_run:
            log("  [DRY RUN] fogadás kihagyva")
            _clear_slip(page)
            return "ok"
        fogad = page.locator(f"{_PLACE_SEL} >> visible=true").first
        if fogad.count() == 0:
            log("  Fogadok gomb nem található!")
            screenshot(page, "real_vegas_fogad_gomb_nincs")
            continue
        _dismiss_cookie(page)
        before = _slip_text(page)
        log("  Fogadok gomb kattintás")
        human_click(page, fogad)
        ok, text = _detect_result(page, before)
        screenshot(page, "real_vegas_fogadas_utan")
        short = " ".join(text.split())[:200]
        if ok is False:
            out["error"] = f"a Vegas elutasította: {short}"
            log(f"  {out['error']}")
            _clear_slip(page)
            return "fail"
        if ok is None:
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
        # 2026-10-21: az Altenar Cloudflare-je 403-mal tiltja a „HeadlessChrome” UA-t
        # (a sportfogadás-modul be sem töltődik) → normál Chrome UA.
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{self._browser.version} Safari/537.36")
        extra = {}
        if session_mtime():
            # a mentett (akár kézi belépésből származó) session → nem kell belépni
            extra["storage_state"] = str(VEGAS_SESSION_JSON)
            _SESSION_APPLIED[0] = session_mtime()
        try:
            context = self._browser.new_context(viewport=VIEWPORT, locale="hu-HU",
                                                user_agent=ua, **extra)
        except Exception as e:
            if not extra:
                raise
            log(f"  Vegas sessionfájl nem tölthető be ({e}) — session nélkül indul")
            context = self._browser.new_context(viewport=VIEWPORT, locale="hu-HU",
                                                user_agent=ua)
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

    def save_session(self) -> None:
        """Belépve a frissülő sütik mentése (a fiókadat-frissítés hívja)."""
        if self._page is not None and is_logged_in(self._page):
            save_session(self._page)

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

    def place_real(self, tip, stake: int = None) -> str:
        """Real Event tipp. Visszatérés: 'ok' | 'fail' | 'skipped'."""
        self.last_error = self.last_bet_ref = ""
        if self._page is None:
            return "fail"
        out: dict = {}
        try:
            return place_real_tip(self._page, tip, self._username, self._password,
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
