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
CDP_PORT = 9335
VEGAS_URL = "https://vegas.hu/sports"
PROFILE_DIR = APP_DIR / "vegas_chrome_profile"
DONE_FLAG = APP_DIR / "vegas_login_done.flag"
CHROME_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def _say(msg: str) -> None:
    print(msg, flush=True)


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
    while time.time() < deadline:
        if got.is_set() or DONE_FLAG.exists():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(1)
    return False


def main() -> int:
    user = os.getenv("VEGAS_USER", "")
    pw = os.getenv("VEGAS_PASS", "")
    chrome = next((p for p in CHROME_CANDIDATES if p.is_file()), None)
    if chrome is None:
        _say("Nem találom a telepített Google Chrome-ot — telepítsd, és próbáld újra.")
        return 1
    DONE_FLAG.unlink(missing_ok=True)
    PROFILE_DIR.mkdir(exist_ok=True)
    proc = subprocess.Popen([str(chrome), f"--user-data-dir={PROFILE_DIR}",
                             f"--remote-debugging-port={CDP_PORT}",
                             "--no-first-run", "--no-default-browser-check",
                             "--new-window", VEGAS_URL])
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
        browser.close()             # CDP-kapcsolat bontása
    DONE_FLAG.unlink(missing_ok=True)
    proc.terminate()                # a Chrome bezárása (a profil megmarad)
    _say("Vegas: belépve, session elmentve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
