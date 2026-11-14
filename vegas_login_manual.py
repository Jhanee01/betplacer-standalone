"""Vegas kézi belépés — a gépen telepített, SIMA Chrome-ban, a session mentésével.

2026-11-11 óta a vegas.hu belépő-űrlapján Cloudflare „Igazolja, hogy Ön ember”
ellenőrzés van. A Playwright saját (automatizált) Chromiumjában ez akkor is
„Ellenőrzés sikertelen”, ha ember kattint — ezért ez a script a normál Chrome-ot
indítja egy külön profillal, és a belépés ALATT nem vezérli: a felhasználó maga lép
be. Csak utána csatlakozik (CDP), ellenőrzi a belépést, és a sessiont (sütik +
localStorage) a vegas_session.json-ba menti — a BetPlacer Vegas-motorja ezt használja.

Jelzés, hogy kész a belépés: egy sor a standard inputon (a „Kész, beléptem” gomb
ezt küldi), vagy a vegas_login_done.flag fájl létrehozása.
Kilépési kód: 0 = sikeres, 1 = nem sikerült / időtúllépés.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from paths import APP_DIR

try:
    from dotenv import load_dotenv
    load_dotenv(APP_DIR / ".env")
except ImportError:
    pass

from playwright.sync_api import sync_playwright

import vegas_engine as ve

WAIT_S = 900
# 2026-11-14: ugyanaz a Chrome (profil + CDP-port), amelyikben a Vegas-motor a fület
# vezérli — a belépés így azonnal a motor fülén is érvényes, és a Chrome nyitva marad.
CDP_PORT = ve.CDP_PORT
VEGAS_URL = ve.VEGAS_URL
PROFILE_DIR = ve.PROFILE_DIR
DONE_FLAG = APP_DIR / "vegas_login_done.flag"


def _say(msg: str) -> None:
    print(msg, flush=True)


def _vegas_windows(show: bool) -> None:
    """A Vegas-Chrome vegas.hu ablakának előhozása (belépéshez) vagy a képernyőn
    kívülre tétele (belépés után; a motor ott dolgozik)."""
    deadline = time.time() + 30
    while not ve.cdp_alive() and time.time() < deadline:
        time.sleep(1)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            pages = [pg for ctx in browser.contexts for pg in ctx.pages
                     if "vegas.hu" in (pg.url or "")]
            for pg in (pages[-1:] if show else pages):   # előhozni: a legújabbat
                ve.set_window(pg, visible=show)
            browser.close()                               # csak a kapcsolat bontása
    except Exception as e:
        _say(f"  (ablak {'előhozása' if show else 'elrejtése'} nem sikerült: {e})")


def _wait_for_signal(proc: subprocess.Popen) -> bool:
    got = threading.Event()

    def stdin_reader():
        try:
            if sys.stdin and sys.stdin.readline():
                got.set()
        except Exception:
            pass

    threading.Thread(target=stdin_reader, daemon=True).start()
    deadline = time.time() + WAIT_S
    started = time.time()
    while time.time() < deadline:
        if got.is_set() or DONE_FLAG.exists():
            return True
        # Ha ezzel a profillal már fut a Chrome (a motoré), az indító azonnal kilép,
        # és a meglévő Chrome-ban nyílik az ablak — ez NEM bezárt ablak. Akkor adjuk
        # fel, ha a böngésző tényleg nem fut (az indulásra 30 mp-et várunk).
        if (proc.poll() is not None and not ve.cdp_alive()
                and time.time() - started > 30):
            return False
        time.sleep(1)
    return False


def main() -> int:
    user = os.getenv("VEGAS_USER", "")
    pw = os.getenv("VEGAS_PASS", "")
    chrome = ve.find_chrome()
    if chrome is None:
        _say("Nem találom a telepített Google Chrome-ot — telepítsd, és próbáld újra.")
        return 1
    DONE_FLAG.unlink(missing_ok=True)
    PROFILE_DIR.mkdir(exist_ok=True)
    # `cmd /c start`: a Chrome ne a BetPlacer folyamatfájának része legyen (leállításkor
    # ne zárják be), és belépés után nyitva maradjon a motornak.
    proc = subprocess.Popen(["cmd", "/c", "start", "", str(chrome), *ve.chrome_flags(),
                             "--new-window", VEGAS_URL],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(3)                    # az új ablak betöltődjön
    _vegas_windows(show=True)        # a motor ablaka a képernyőn kívül van — előhozzuk
    if pw:
        subprocess.run("clip", input=pw.encode("utf-16-le"), check=False)
    _say("Megnyílt a Chrome a vegas.hu-val. Lépj be kézzel:")
    if user:
        _say(f"  e-mail: {user}")
    if pw:
        _say("  jelszó: a vágólapon van (Ctrl+V)")
    _say("  pipáld be az ellenőrzést, nyomd meg a Bejelentkezés gombot,")
    _say("  majd kattints a „Kész, beléptem” gombra.")

    ok = _wait_for_signal(proc)
    if pw:
        subprocess.run("clip", input=b"", check=False)      # vágólap ürítése
    if not ok:
        _say("Vegas: a belépés nem történt meg (bezárt ablak vagy lejárt idő) — "
             "a session nem változott.")
        return 1

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5).read()
    except Exception as e:
        _say(f"A Chrome-hoz nem tudok csatlakozni ({e}).")
        return 1
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "vegas.hu" in (pg.url or "")), None)
        if page is None:
            page = ctx.new_page()
            page.goto(VEGAS_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
        if not ve.is_logged_in(page):
            _say("Vegas: a Chrome-ban nem látom belépve a fiókot — lépj be, majd "
                 "indítsd újra a belépést.")
            browser.close()
            return 1
        ve.save_session(page)
        # CDP-n át a storage_state a localStorage-ot NEM tartalmazza, a Vegas viszont
        # ott tartja a belépést → közvetlenül az oldalról olvassuk ki.
        try:
            items = page.evaluate("() => Object.entries(localStorage)"
                                  ".map(([name, value]) => ({name, value}))")
            origin = page.evaluate("() => location.origin")
            state = json.loads(ve.VEGAS_SESSION_JSON.read_text(encoding="utf-8"))
            state["origins"] = [o for o in state.get("origins", []) if o.get("origin") != origin]
            state["origins"].append({"origin": origin, "localStorage": items})
            tmp = ve.VEGAS_SESSION_JSON.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, ve.VEGAS_SESSION_JSON)
        except Exception as e:
            _say(f"  localStorage mentése sikertelen: {e}")
        # A Chrome NYITVA marad: a Vegas-motor ugyanebben vezérli a fület.
        try:
            browser.close()         # csak a CDP-kapcsolat bontása
        except Exception:
            pass
    DONE_FLAG.unlink(missing_ok=True)
    _vegas_windows(show=False)
    _say("Vegas: belépve. A Chrome-ablak a képernyőn kívülre került, de fut tovább "
         "(a tálcán látszik) — ne zárd be, a BetPlacer ebben fogad.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
