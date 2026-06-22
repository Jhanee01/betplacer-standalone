"""
Standalone BetPlacer — fogadási motor.

Az eredeti C:\\Fifa Tipster\\bet_placer\\bet_placer.py bevált kódjára épül.
Egyetlen különbség: eseménykeresés event_id helyett csapatnév alapján történik,
mert a Telegram üzenetből nincs event_id, csak csapatnevek.
"""

import math
import random
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from tip_parser import ParsedTip
from paths import APP_DIR

TIPPMIXPRO_URL  = "https://www.tippmixpro.hu"
SPORTS_LIST_URL = ("https://www.tippmixpro.hu/hu/fogadas/i/"
                   "e-sport/esports/96/e-labdarugas/121/esport")
LOGS_DIR = APP_DIR / "logs"

# Oldalbetöltés időkorlátja (ms). 30 mp lassú mobilneten kevés volt a nehéz
# listaoldalhoz (sok kép/banner/élő widget) → Page.goto Timeout → BET_FAIL.
# 60 mp-re emelve, hogy gyenge kapcsolaton is beférjen.
PAGE_GOTO_TIMEOUT = 60000


# ══════════════════════════════════════════════════════════════════════════════
# Erőforrásszűrő — kevesebb adat, gyorsabb betöltés
# ══════════════════════════════════════════════════════════════════════════════
# A Tippmixpro listaoldal nehéz: rengeteg kép, VB-bannerek, élő widgetek,
# reklám- és követő-scriptek. Ezek lassítják a betöltést (timeout-veszély) ÉS
# eszik a mobilnetet. A kép/média/font erőforrásokat és a reklám/követő
# kéréseket eldobjuk — a működéshez (DOM, oddsok, szelvény) nem kellenek.
# A stylesheet-eket SZÁNDÉKOSAN meghagyjuk: a Playwright láthatóság-ellenőrzései
# (is_visible / bounding_box) a CSS-elrendezésre támaszkodnak.
_BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
_BLOCK_URL_KEYWORDS = (
    "googletagmanager", "google-analytics", "googlesyndication",
    "doubleclick", "facebook.net", "connect.facebook", "hotjar",
    "cdn.cookielaw", "onetrust", "criteo", "adservice", "adnxs",
    "scorecardresearch", "quantserve", "taboola", "outbrain",
)


def _install_resource_blocker(page):
    """Kép/média/font + reklám/követő kérések eldobása (route-szűrő)."""
    def _route(route):
        try:
            req = route.request
            if req.resource_type in _BLOCK_RESOURCE_TYPES:
                return route.abort()
            url = req.url.lower()
            if any(k in url for k in _BLOCK_URL_KEYWORDS):
                return route.abort()
            return route.continue_()
        except Exception:
            # Bármi gond esetén inkább engedjük át a kérést, mint hogy elakadjon.
            try:
                return route.continue_()
            except Exception:
                pass
    try:
        page.route("**/*", _route)
        log("Erőforrásszűrő bekapcsolva (kép/média/font + reklám eldobva).")
    except Exception as e:
        log(f"Erőforrásszűrő nem aktiválható (folytatjuk): {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Logging + screenshot
# ══════════════════════════════════════════════════════════════════════════════

def log(msg: str):
    print(f"[bet] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


SCREENSHOT_LIMIT = 500   # ennyi png felett a logs mappa ürül


def screenshot(page, label: str):
    LOGS_DIR.mkdir(exist_ok=True)
    # Rotáció: ha túl sok kép gyűlt össze, töröljük az egészet.
    try:
        existing = list(LOGS_DIR.glob("*.png"))
        if len(existing) >= SCREENSHOT_LIMIT:
            for f in existing:
                try:
                    f.unlink()
                except Exception:
                    pass
            log(f"  {SCREENSHOT_LIMIT}+ screenshot — logs mappa kiürítve")
    except Exception:
        pass
    path = LOGS_DIR / f"{datetime.now().strftime('%H%M%S')}_{label}.png"
    try:
        page.screenshot(path=str(path))
        log(f"  kép → {path.name}")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Emberi egérmozgás  (eredeti kód)
# ══════════════════════════════════════════════════════════════════════════════

def _rand_ms(lo: int, hi: int) -> float:
    return random.uniform(lo, hi) / 1000.0


def human_click(page, locator):
    try:
        locator.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    box = None
    try:
        box = locator.bounding_box(timeout=3000)
    except Exception:
        pass
    if box:
        tx = box["x"] + box["width"]  * random.uniform(0.35, 0.65)
        ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        try:
            sx = random.uniform(tx - 300, tx + 300)
            sy = random.uniform(ty - 200, ty + 200)
        except Exception:
            sx, sy = tx - 100, ty - 50
        steps = random.randint(5, 9)
        for i in range(1, steps + 1):
            t = i / steps
            cx = sx + (tx - sx) * t + math.sin(t * math.pi) * random.uniform(-20, 20)
            cy = sy + (ty - sy) * t + math.sin(t * math.pi) * random.uniform(-15, 15)
            page.mouse.move(cx, cy)
            time.sleep(_rand_ms(15, 40))
        page.mouse.move(tx, ty)
        time.sleep(_rand_ms(300, 700))
        page.mouse.click(tx, ty)
    else:
        locator.click(timeout=5000)
    time.sleep(_rand_ms(200, 500))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Session kezelés  (eredeti kód, username/password paraméterként)
# ══════════════════════════════════════════════════════════════════════════════

def is_logged_in(page) -> bool:
    try:
        btn = page.locator("button.ButtonLogin").first
        return not (btn.count() > 0 and btn.is_visible())
    except Exception:
        return False


def _dismiss_cookie(page):
    for sel in [
        "button:has-text('ÖSSZES ELUTASÍTÁSA')",
        "button:has-text('Elfogad')",
        "button:has-text('Rendben')",
    ]:
        try:
            b = page.locator(sel).first
            if b.count() > 0 and b.is_visible():
                b.click(timeout=2000)
                log("  cookie banner elvetve")
                return
        except Exception:
            pass


def _dismiss_popup(page):
    """VB 2026 (és hasonló) promóciós popup bezárása.

    Nem-blokkoló: ha nincs popup, azonnal visszatér és a folyamat megy tovább.
    Több bezáró-gombot is megpróbál (X gomb, 'Bezárom' gomb)."""
    for sel in [
        "button.ModalCloseButton",
        "button.CloseFirstAccessButton",
        "button:has-text('Bezárom')",
        "div.CmsPopup button.ModalCloseButton",
    ]:
        try:
            b = page.locator(sel).first
            if b.count() > 0 and b.is_visible():
                b.click(timeout=2000)
                log(f"  promóciós popup bezárva ({sel})")
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def do_login_once(page, username: str, password: str):
    log("Navigálás a főoldalra...")
    try:
        page.goto(TIPPMIXPRO_URL, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
    except Exception as e:
        if "ERR_ABORTED" in str(e) or "net::" in str(e):
            log("  navigáció redirect/abort — folytatjuk")
        else:
            raise
    page.wait_for_timeout(2000)
    _dismiss_popup(page)
    _dismiss_cookie(page)

    try:
        login_btn = page.locator("button.ButtonLogin").first
        page.wait_for_selector("button.ButtonLogin", timeout=10000)
        log("  login gomb megtalálva, kattintás...")
        human_click(page, login_btn)
    except PWTimeout:
        screenshot(page, "login_gomb_timeout")
        raise RuntimeError("Login gomb nem jelent meg 10 másodpercen belül.")

    try:
        page.wait_for_selector("form.LoginContentForm", timeout=8000)
    except PWTimeout:
        screenshot(page, "login_form_timeout")
        raise RuntimeError("Login form nem jelent meg.")

    page.wait_for_timeout(500)

    user_inp = page.locator("input[name='username']").first
    user_inp.click(timeout=3000)
    page.wait_for_timeout(300)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(username, delay=random.randint(60, 120))
    log("  username megadva")
    time.sleep(_rand_ms(400, 700))

    pass_inp = page.locator("input[name='password']").first
    pass_inp.click(timeout=3000)
    page.wait_for_timeout(300)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(password, delay=random.randint(70, 130))
    log("  jelszó megadva")
    time.sleep(_rand_ms(400, 800))

    submit = page.locator("button#LoginButton-Header").first
    human_click(page, submit)
    log("  login elküldve, várakozás...")
    page.wait_for_timeout(3000)
    screenshot(page, "login_utan")


def ensure_logged_in(page, username: str, password: str, max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        log(f"Bejelentkezési kísérlet {attempt}/{max_retries}...")
        try:
            do_login_once(page, username, password)
        except Exception as e:
            log(f"  login hiba: {e}")
        page.wait_for_timeout(2000)
        if is_logged_in(page):
            log("Bejelentkezés sikeres.")
            return
        log("  session nem jött létre, várakozás 2mp...")
        page.wait_for_timeout(2000)
    screenshot(page, "login_vegul_sikertelen")
    raise RuntimeError(f"Bejelentkezés {max_retries} kísérlet után is sikertelen.")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Sports oldal navigáció  (eredeti kód)
# ══════════════════════════════════════════════════════════════════════════════

def find_sports_frame(page):
    for _ in range(80):
        for fr in page.frames:
            if fr.url and "sports2.tippmixpro.hu" in fr.url:
                return fr
        page.wait_for_timeout(250)
    return None


def load_all_events(frame):
    for _ in range(12):
        try:
            btn = frame.locator("button[class*='MatchList__MainButton']").first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=2000)
                frame.wait_for_timeout(900)
            else:
                break
        except Exception:
            break


def navigate_to_sports(page, username: str, password: str):
    log("  navigálás az esports listára...")
    page.goto(SPORTS_LIST_URL, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
    page.wait_for_timeout(2000)
    if not is_logged_in(page):
        log("  session kiesett navigáció közben, újra bejelentkezés...")
        ensure_logged_in(page, username, password)
        page.goto(SPORTS_LIST_URL, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
        page.wait_for_timeout(2000)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Esemény keresés csapatnév alapján  (ÚJ — nincs event_id)
# ══════════════════════════════════════════════════════════════════════════════

def _team_in_text(txt_lower: str, name: str) -> bool:
    """
    Szóhatár-alapú illesztés: a 'Morocco' ne illeszkedjen pl. a 'Moroccoland'-ra,
    csökkentve a részszó miatti téves esemény-egyezést.
    A txt_lower már kisbetűs; a nevet kisbetűsítjük és regex-escape-eljük.
    """
    pat = r'(?<![0-9a-z])' + re.escape(name.lower()) + r'(?![0-9a-z])'
    return re.search(pat, txt_lower) is not None


def find_event_by_teams(frame, tip: ParsedTip):
    """
    Csapatnév alapján keresi meg az esemény konténerét.
    Próbálja teljes névvel, majd csak az ország névvel (zárójel nélkül).
    """
    candidates = [
        (tip.home_team, tip.away_team),
        (tip.home_clean, tip.away_team),
        (tip.home_team, tip.away_clean),
        (tip.home_clean, tip.away_clean),
    ]

    for h, a in candidates:
        # 1. kísérlet: ismert EventItem osztály konténerek
        for cls in ("EventItem", "MatchItem", "EventRow", "EventBlock"):
            try:
                evs = frame.locator(f"[class*='{cls}']").all()
                for ev in evs:
                    try:
                        txt = ev.inner_text(timeout=400).lower()
                        if _team_in_text(txt, h) and _team_in_text(txt, a):
                            if ev.locator("button[class*='OddsButton']").count() > 0:
                                log(f"  esemény találva ({cls}): {h} vs {a}")
                                return ev
                    except Exception:
                        pass
            except Exception:
                pass

        # 2. kísérlet: hazai csapat szöveg csomópontja → DOM-ban felfelé
        try:
            nodes = frame.locator(f"text='{h}'").all()
            for node in nodes[:5]:
                for depth in range(1, 9):
                    try:
                        ev = node.locator(f"xpath=ancestor::div[{depth}]").first
                        if ev.count() == 0:
                            break
                        txt = ev.inner_text(timeout=400).lower()
                        if _team_in_text(txt, a):
                            if ev.locator("button[class*='OddsButton']").count() > 0:
                                log(f"  esemény találva (DOM walk, depth={depth}): {h} vs {a}")
                                return ev
                    except Exception:
                        break
        except Exception:
            pass

    log(f"  esemény nem található: {tip.home_clean} vs {tip.away_clean}")
    return None


def _inner(locator, timeout=1000):
    try:
        if locator.count() == 0:
            return None
        t = locator.first.inner_text(timeout=timeout).strip()
        return t if t else None
    except Exception:
        return None


def find_event_element(frame, event_id: str):
    """Eredeti bet_placer.py kód — event_id alapján keresi az eseményt."""
    node = frame.locator(f"[data-ubt-label='{event_id}']").first
    if node.count() == 0:
        node = frame.locator(f"a[href*='{event_id}']").first
    if node.count() == 0:
        return None
    for class_hint in ("EventItem", "MatchItem", "EventRow", "EventBlock"):
        ev = node.locator(f"xpath=ancestor-or-self::div[contains(@class,'{class_hint}')]").first
        try:
            if ev.count() > 0 and ev.locator("button[class*='OddsButton']").count() > 0:
                return ev
        except Exception:
            pass
    for depth in range(1, 9):
        try:
            ev = node.locator(f"xpath=ancestor::div[{depth}]").first
            if ev.count() > 0 and ev.locator("button[class*='OddsButton']").count() > 0:
                return ev
        except Exception:
            break
    return None


def click_odds_by_teams(page, frame, tip: ParsedTip):
    """
    Ha van event_id: az eredeti bet_placer.py módszerrel keres (biztonságos).
    Ha nincs event_id: csapatnév alapú fallback.

    Piac szerint kattint:
      OU   — OddsParameter (gólvonal) szerint, idx 0/1 = Over/Under; inner_text fallback
      1X2  — a gomb OddsButton__ShortText felirata szerint (hazai/döntetlen/vendég)

    Visszatérési értékek:
      True  — sikeres kattintás
      False — esemény megvan, de gomb nem (nem retryolható)
      None  — esemény nincs az oldalon (retryolható)
    """
    # Elsődleges: event_id alapú keresés (eredeti, bevált kód)
    if tip.event_id:
        ev = find_event_element(frame, tip.event_id)
        if ev is None:
            log(f"  esemény nem található event_id alapján: {tip.event_id}")
            screenshot(page, "esemeny_nem_talalt")
            return None
    else:
        # Fallback: csapatnév alapú keresés (ha még nincs event_id az üzenetben)
        ev = find_event_by_teams(frame, tip)
        if ev is None:
            screenshot(page, "esemeny_nem_talalt")
            return None

    market = (tip.market or "OU").strip().upper()
    pick   = (tip.pick or "").strip().upper()

    try:
        btn_count = ev.locator("button[class*='OddsButton']").count()
        log(f"  [debug] OddsButton count={btn_count}")
    except Exception:
        pass

    if market == "OU":
        line = str(tip.line)
        # 1. kísérlet: OddsParameter span alapján
        params = ev.locator("span[class*='OddsParameter']")
        for i in range(params.count()):
            try:
                txt = params.nth(i).inner_text(timeout=800).strip().replace(",", ".")
                if txt != line:
                    continue
                block = params.nth(i).locator("xpath=ancestor::div[1]").first
                btns  = block.locator("button[class*='OddsButton']")
                idx   = 0 if pick == "OVER" else 1
                btn   = btns.nth(idx)
                if btn.count() > 0:
                    log(f"  O/U {line} {pick} → kattintás")
                    human_click(page, btn)
                    return True
            except Exception:
                pass

        # 2. kísérlet: button inner_text alapján
        log("  OddsParameter fallback, inner_text keresés...")
        btns = ev.locator("button[class*='OddsButton']")
        for i in range(btns.count()):
            try:
                raw  = btns.nth(i).inner_text(timeout=800)
                norm = raw.casefold().replace("ö","o").replace("á","a").replace("é","e").replace("í","i").replace("ő","o").replace("ú","u").replace("ü","u")
                hit = (
                    (pick == "OVER"  and ("tobb" in norm or "over" in norm or ("mint" in norm and "kevesebb" not in norm))) or
                    (pick == "UNDER" and ("kevesebb" in norm or "under" in norm or "kevés" in raw.casefold()))
                )
                if hit:
                    log(f"  O/U {pick} (inner_text) → kattintás")
                    human_click(page, btns.nth(i))
                    return True
            except Exception:
                pass

    elif market == "1X2":
        # A kimenetel-gombok felirata (ShortText) alapján: Hazai/Döntetlen/Vendég.
        btns = ev.locator("button[class*='OddsButton']")
        for i in range(btns.count()):
            try:
                label = (_inner(btns.nth(i).locator("span[class*='OddsButton__ShortText']")) or "").casefold()
                hit = (
                    (pick == "HOME" and ("hazai"     in label or "home" in label)) or
                    (pick == "DRAW" and ("döntetlen" in label or "draw" in label)) or
                    (pick == "AWAY" and ("vendég"    in label or "away" in label))
                )
                if hit:
                    log(f"  1X2 {pick} → kattintás")
                    human_click(page, btns.nth(i))
                    return True
            except Exception:
                pass

    else:
        log(f"  ismeretlen piac: {market}")

    log(f"  odds gomb nem található: {market} {pick} {tip.line if tip.line is not None else ''}")
    screenshot(page, f"odds_nem_talalt_{market}_{pick}")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6-7 — Szelvény kitöltése és megerősítés  (eredeti kód)
# ══════════════════════════════════════════════════════════════════════════════

def wait_for_betslip(page, frame=None, timeout_s: int = 12):
    selectors = [
        "input.StakeInput__Input",
        "div.BetslipSelection--Single",
        "div[class*='BetslipSelection']",
        "button.BetslipFooter__PlaceBetButton",
    ]
    contexts = [c for c in [page, frame] if c is not None]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for ctx in contexts:
            for sel in selectors:
                try:
                    el = ctx.locator(sel).first
                    if el.count() > 0:
                        ctx_name = "frame" if ctx is not page else "page"
                        log(f"  szelveny megjelent ({ctx_name}: {sel})")
                        return ctx
                except Exception:
                    pass
        time.sleep(0.4)
    log("  szelveny nem jelent meg (session kiesett?)")
    return None


def ensure_real_tab(ctx):
    try:
        real = ctx.locator("div.BetslipGroup--Real").first
        if real.count() > 0:
            return
        tab = ctx.locator("div.OM-BetslipRealSimulateTab").locator("text=Valodi").first
        if tab.count() == 0:
            tab = ctx.locator("div.OM-BetslipRealSimulateTab button").first
        if tab.count() > 0:
            tab.click(timeout=2000)
            ctx.wait_for_timeout(800)
            log("  Real tabra valtva")
    except Exception as e:
        log(f"  Real tab ellenorzes hiba (folytatjuk): {e}")


def fill_stake(page, ctx, stake: int):
    inp = ctx.locator("input.StakeInput__Input").first
    if inp.count() == 0:
        inp = ctx.locator("input[name^='stake.']").first
    if inp.count() == 0:
        raise RuntimeError("Tet input nem talalhato!")
    human_click(page, inp)
    page.wait_for_timeout(300)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    page.keyboard.type(str(stake), delay=random.randint(60, 100))
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    log(f"  tet beirva: {stake} Ft")


def confirm_bet(page, ctx, dry_run: bool) -> bool:
    if dry_run:
        log("  [DRY RUN] fogadas kihagyva")
        screenshot(page, "dryrun_szelveny")
        return True
    ensure_real_tab(ctx)
    fogad = ctx.locator("button.BetslipFooter__PlaceBetButton").first
    if fogad.count() == 0:
        screenshot(page, "fogad_gomb_nem_talalt")
        log("  Fogadok gomb nem talalhato!")
        return False
    screenshot(page, "fogadas_elott")
    log("  Fogadok gomb kattintas")
    human_click(page, fogad)
    page.wait_for_timeout(2500)
    screenshot(page, "fogadas_utan")
    return True


def detect_bet_result(page, ctx) -> bool:
    try:
        ctx.wait_for_selector("input.StakeInput__Input", state="hidden", timeout=4000)
        log("  fogadas sikeresen leadva (szelv kiurult)")
        return True
    except PWTimeout:
        pass
    fogad = ctx.locator("button.BetslipFooter__PlaceBetButton").first
    if fogad.count() > 0 and fogad.is_visible():
        log("  fogadas latszólag sikertelen (Fogadok gomb meg lathato)")
        screenshot(page, "fogadas_hiba")
        return False
    log("  fogadas leadva")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Egy tipp teljes flow-ja  (eredeti logika, csapatnév alapú kereséssel)
# ══════════════════════════════════════════════════════════════════════════════

def place_tip(page, tip: ParsedTip, username: str, password: str,
              stake: int, dry_run: bool) -> str:
    """
    Egy tipp megrakásának EGY teljes próbálkozása.

    Visszatérési érték (string):
      "ok"       — sikeres fogadás
      "fail"     — esemény megvan, de a fogadás nem ment (NE retry-old)
      "notfound" — az esemény nincs (még) az oldalon → a hívó async réteg
                   dönt a várakozásról/újrapróbálásról, ez NEM blokkol más tippet.

    A session-kiesés / szelvény-hiba miatti gyors újrapróbálás itt belül marad
    (másodperces nagyságrend), de az 5 perces esemény-várakozás már a hívóé.
    """
    log(f"Tipp: {tip}")

    MAX_ATTEMPTS = 3
    attempt      = 0

    while attempt < MAX_ATTEMPTS:
        attempt += 1
        if attempt > 1:
            log(f"  {attempt}. kísérlet...")

        # A listaoldal betöltése lassú neten időtúllépésbe futhat (Page.goto
        # Timeout). Ez ATMENETI hiba — a 3-próbás cikluson belül újrapróbáljuk,
        # nem adjuk fel azonnal (különben egyetlen lassú betöltés = BET_FAIL).
        try:
            navigate_to_sports(page, username, password)
        except Exception as e:
            log(f"  listaoldal betöltés sikertelen ({e}) — újrapróba")
            page.wait_for_timeout(2000)
            continue

        fr = find_sports_frame(page)
        if fr is None:
            log("  sports iframe nem töltött be!")
            continue

        try:
            fr.wait_for_selector("span[class*='MatchListGroup__Tournament']", timeout=20000)
        except PWTimeout:
            log("  meccs lista timeout")
            continue

        load_all_events(fr)

        clicked = click_odds_by_teams(page, fr, tip)
        if clicked is None:
            # Esemény nincs az oldalon — a hívó vár és újrapróbálja (csak ezt a tippet).
            log("  esemény nincs az oldalon (notfound)")
            return "notfound"

        if not clicked:
            return "fail"

        screenshot(page, f"odds_utan_kiserlet{attempt}")
        ctx = wait_for_betslip(page, frame=fr, timeout_s=12)
        if ctx is None:
            log("  szelveny nem jelent meg — session kiesett, ujra bejelentkezes...")
            screenshot(page, f"session_kiesett_kiserlet{attempt}")
            ensure_logged_in(page, username, password)
            continue

        try:
            ensure_real_tab(ctx)
            fill_stake(page, ctx, stake)
            if not confirm_bet(page, ctx, dry_run=dry_run):
                log("  megerosites sikertelen")
                continue
            if dry_run:
                return "ok"
            if detect_bet_result(page, ctx):
                return "ok"
            log("  fogadas nem sikerult, ujraprobales...")
        except Exception as e:
            log(f"  szelveny hiba: {e}")
            screenshot(page, f"szelveny_hiba_kiserlet{attempt}")

    log(f"  3 kísérlet után sem sikerült: {tip}")
    return "fail"


# ══════════════════════════════════════════════════════════════════════════════
# BetEngine osztály (long-running, browser stays open between bets)
# ══════════════════════════════════════════════════════════════════════════════

class BetEngine:
    def __init__(self, username: str, password: str, stake: int, dry_run: bool = False):
        self._username = username
        self._password = password
        self._stake    = stake
        self._dry_run  = dry_run
        self._pw       = None
        self._browser  = None
        self._page     = None

    def start(self):
        self._pw      = sync_playwright().__enter__()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page    = self._browser.new_page(viewport={"width": 1800, "height": 1000})
        _install_resource_blocker(self._page)
        ensure_logged_in(self._page, self._username, self._password)
        log("BetEngine kész.")

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
        """Visszatérés: 'ok' | 'fail' | 'notfound'.

        `stake`: ha meg van adva, ezzel a téttel rak (stratégiánkénti tét);
        egyébként az induláskori alap tét (self._stake)."""
        if self._page is None:
            return "fail"
        bet_stake = self._stake if stake is None else int(stake)
        try:
            return place_tip(
                self._page, tip,
                self._username, self._password,
                bet_stake, self._dry_run,
            )
        except Exception as exc:
            log(f"  kivétel: {exc}")
            try:
                ensure_logged_in(self._page, self._username, self._password)
            except Exception:
                pass
            return "fail"
