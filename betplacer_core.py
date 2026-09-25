"""
Közös futtatási logika a GUI és a parancssori (--no-gui) mód számára.

Korábban a main.py _run() és a gui.py _async_main() szinte azonos kódot
tartalmazott — ez most egyetlen run_session() coroutine-ban él.

Kulcs viselkedés:
  • Egy tipp megrakása külön asyncio taskban fut → több tipp párhuzamosan
    várakozhat (a 30-120 mp-es késleltetés és az esemény-újrapróbálás
    NEM blokkolja a többi tippet).
  • A Playwright motor végig egyetlen háttérszálon fut (max_workers=1),
    mert a sync Playwright nem szálbiztos.
"""

import asyncio
import concurrent.futures
import ctypes
import os
import random
import sys
import traceback
from datetime import datetime
from types import SimpleNamespace


# ── Esemény-újrapróbálás (csak az adott tippre vonatkozik) ────────────────────
EVENT_RETRY_WAIT  = 300   # 5 perc — NEM blokkolja a többi tippet (async sleep)
MAX_EVENT_RETRIES = 3


# ── Gép ébren tartása futás közben (Windows) ──────────────────────────────────
# A számítógép NEM tud scriptet futtatni, amíg ténylegesen alszik (a CPU áll).
# Amíg viszont a BetPlacer fut, megkérjük a Windowst, hogy ne aludjon el — a
# kijelző közben elsötétülhet, de a rendszer ébren marad és tovább rak.
_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _prevent_sleep() -> bool:
    """Megakadályozza a rendszer elalvását. True, ha sikerült (csak Windowson)."""
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        return True
    except Exception:
        return False


def _allow_sleep():
    """Visszaengedi a rendszert az alvásba (a futás végén)."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


# ── Irodák ────────────────────────────────────────────────────────────────────
# Irodánként: saját csatorna, alap tét, belépés és be/ki kapcsoló (.env kulcsok).
# A TippmixPro alapból BE (a 2.x-es telepítések változatlanul futnak), a Vegas KI.
# default_channel: ha a .env-ben nincs csatorna (a TippmixPro-ét a varázsló írja be;
# a Vegas-tippeket a FIFA Tipster a volt Tükör-csatornába küldi).
BOOKMAKER_ENV = {
    "tippmixpro": dict(user="TIPPMIXPRO_USER", pw="TIPPMIXPRO_PASS",
                       channel="TELEGRAM_CHANNEL", stake="BET_STAKE",
                       enabled="TIPPMIXPRO_ENABLED", default_on="1",
                       default_channel=""),
    "vegas":      dict(user="VEGAS_USER", pw="VEGAS_PASS",
                       channel="TELEGRAM_CHANNEL_VEGAS", stake="BET_STAKE_VEGAS",
                       enabled="VEGAS_ENABLED", default_on="0",
                       default_channel="-1003886878395"),
}


def bookmaker_channel(bookmaker: str) -> str:
    """Az iroda csatornája a .env-ből, hiányában az alapérték."""
    e = BOOKMAKER_ENV[bookmaker]
    return os.getenv(e["channel"], "").strip() or e["default_channel"]


def bookmaker_enabled(bookmaker: str) -> bool:
    e = BOOKMAKER_ENV[bookmaker]
    return os.getenv(e["enabled"], e["default_on"]).strip().lower() in ("1", "true", "yes", "on")


def _fmt_tip_line(tip) -> str:
    """Egységes egysoros tipp-formátum az értesítésekhez (tét NÉLKÜL, játékosnevekkel).
    A standalone home_team/away_team már tartalmazza a játékost zárójelben
    (pl. 'Germany (Manuel)'). Pl.: '14:52 | Germany (Manuel) vs Scotland (John) | OU UNDER 6.5'."""
    if tip.market == "OU" and tip.line is not None:
        market_pick = f"OU {tip.pick} {tip.line}"
    else:
        market_pick = f"{tip.market} {tip.pick}"
    return f"{tip.time} | {tip.home_team} vs {tip.away_team} | {market_pick}"


def _parse_channel(raw: str):
    try:
        return int(raw)
    except ValueError:
        return raw


def _read_config() -> SimpleNamespace:
    """Beállítások beolvasása env-ből + hiányzó kulcsok kigyűjtése.

    `books`: a BEKAPCSOLT irodák {iroda: SimpleNamespace(username, password,
    channel, stake)} — csak ezekhez indul motor és csak ezek csatornáját figyeljük."""
    dry_run  = os.getenv("BET_DRY_RUN", "0") == "1"
    api_id   = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone    = os.getenv("TELEGRAM_PHONE", "")
    strategy = os.getenv("TELEGRAM_STRATEGY_FILTER", "")

    required = {"TELEGRAM_API_ID": str(api_id), "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_PHONE": phone}
    books = {}
    for bm, e in BOOKMAKER_ENV.items():
        if not bookmaker_enabled(bm):
            continue
        ch_raw = bookmaker_channel(bm)
        books[bm] = SimpleNamespace(
            username=os.getenv(e["user"], ""), password=os.getenv(e["pw"], ""),
            channel=_parse_channel(ch_raw),
            stake=int(float(os.getenv(e["stake"], os.getenv("BET_STAKE", "500")))),
        )
        required.update({e["user"]: books[bm].username, e["pw"]: books[bm].password,
                         e["channel"]: ch_raw})

    missing = [k for k, v in required.items() if not v or v == "0"]
    if not books:
        missing.append("legalább egy bekapcsolt iroda (TippmixPro / Vegas)")

    return SimpleNamespace(
        dry_run=dry_run, api_id=api_id, api_hash=api_hash, phone=phone,
        strategy=strategy, books=books, missing=missing,
    )


async def run_session(log, foot=None, stop_event=None, on_status=None):
    """
    A teljes futás: Playwright indítás → Telegram figyelés → tippek megrakása.

    log:        callable(msg: str, kind: str = "info")  — kötelező
    foot:       callable(text: str)                      — opcionális státuszsor
    stop_event: threading.Event                          — opcionális leállítás
    on_status:  callable(key: str, tip, status: str, detail: str) — opcionális
                tipp-állapot jelzés a GUI „Mai tippek" paneljéhez.
                status: "pending" | "placing" | "retry" | "ok" | "fail" | "skipped"
    """
    from bet_engine import BetEngine
    from telegram_watcher import start_watcher
    from tip_parser import ParsedTip

    def _foot(text):
        if foot:
            foot(text)

    def _status(key, tip, status, detail=""):
        if on_status:
            on_status(key, tip, status, detail)

    cfg = _read_config()
    if cfg.missing:
        log(f"Hiányzó beállítások: {', '.join(cfg.missing)}", "error")
        log("Futtasd újra a beállítási varázslót: python main.py --setup", "muted")
        return

    from tip_parser import BOOKMAKER_LABEL
    for bm in BOOKMAKER_ENV:
        state = "AKTÍV" if bm in cfg.books else "KIKAPCSOLVA"
        log(f"{BOOKMAKER_LABEL[bm]}: {state}", "ok" if bm in cfg.books else "muted")

    # Stratégiánkénti tét — induláskori pillanatkép irodánként (a GUI futás közben
    # zárolja a szerkesztést). Ha egy stratégiához nincs külön tét, az iroda alap
    # tétje él.
    from stake_store import load_stakes
    stake_maps = {bm: load_stakes(bm) for bm in cfg.books}
    for bm, sm in stake_maps.items():
        if sm:
            log(f"[{BOOKMAKER_LABEL[bm]}] Stratégiánkénti tét: " + ", ".join(
                f"{k}={v}Ft" for k, v in sm.items()), "muted")

    # ── Telegram bot értesítés sikertelen fogadáskor ──────────────────────────
    # Egy KÜLÖN bot küldi (Bot API), így BEJÖVŐ üzenetként push-értesítést ad a
    # telefonon. A bot tokent és a chat_id-t a setup wizard állítja be.
    # Kikapcsolható: NOTIFY_ON_FAIL=0.
    notify_bot_token = os.getenv("NOTIFY_BOT_TOKEN", "").strip()
    notify_chat_id   = os.getenv("NOTIFY_CHAT_ID", "").strip()
    notify_on_fail   = os.getenv("NOTIFY_ON_FAIL", "1") != "0"
    notify_on_ok     = os.getenv("NOTIFY_ON_OK", "1") != "0"   # sikeres megrakás is — alapból BE
    notify_bot_ready = bool(notify_bot_token) and bool(notify_chat_id)
    if (notify_on_fail or notify_on_ok) and not notify_bot_ready:
        log("Értesítő bot nincs beállítva — nem lesz Telegram-értesítés. "
            "(python main.py --setup)", "muted")

    async def _send_notify(msg: str):
        try:
            import notifier
            ok = await loop.run_in_executor(
                None, lambda: notifier.send_message(notify_bot_token, notify_chat_id, msg))
            log("Telegram értesítés elküldve (bot)." if ok
                else "Telegram értesítés sikertelen (bot).",
                "muted" if ok else "warn")
        except Exception as e:
            log(f"Értesítés küldése sikertelen: {e}", "warn")

    # Az értesítés első sora megmondja, melyik irodán (melyik fiókkal) történt.
    async def _notify_ok(tip, bet_ref: str = ""):
        if not (notify_on_ok and notify_bot_ready):
            return
        msg = f"✅ Tipp megrakva — {tip.bookmaker_label}\n{_fmt_tip_line(tip)}"
        if bet_ref:
            msg += f"\nSzelvény ID: {bet_ref}"
        await _send_notify(msg)

    async def _notify_fail(tip, reason: str = ""):
        if not (notify_on_fail and notify_bot_ready):
            return
        msg = f"❌ Sikertelen fogadás — {tip.bookmaker_label}\n{_fmt_tip_line(tip)}"
        if reason:
            msg += f"\nOk: {reason}"
        await _send_notify(msg)

    async def _notify_line_changed(tip):
        if not (notify_on_fail and notify_bot_ready):
            return
        await _send_notify(
            f"⚠️ Vonal megváltozott — a tipp NEM lett megrakva — {tip.bookmaker_label}\n"
            f"{_fmt_tip_line(tip)}\n"
            f"A(z) {tip.line} gólvonal már nem szerepel a kínálatban.")

    # Gép ébren tartása a teljes figyelési munkamenet alatt.
    if _prevent_sleep():
        log("Alvásgátlás bekapcsolva — a gép futás közben nem alszik el "
            "(a kijelző elsötétülhet).", "muted")

    loop = asyncio.get_running_loop()

    # Irodánként saját motor, saját háttérszálon (a sync Playwright szálhoz kötött).
    # Egy iroda sikertelen belépése nem állítja le a másikat.
    from vegas_engine import VegasEngine
    engine_cls = {"tippmixpro": BetEngine, "vegas": VegasEngine}
    engines:   dict = {}
    executors: dict = {}
    for bm, book in cfg.books.items():
        label = BOOKMAKER_LABEL[bm]
        log(f"[{label}] Playwright engine indul (Chromium betöltés ~10-20 mp)...", "muted")
        ex  = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        eng = engine_cls[bm](book.username, book.password, book.stake, cfg.dry_run)
        try:
            await loop.run_in_executor(ex, eng.start)
        except Exception as e:
            log(f"[{label}] Bejelentkezés sikertelen: {e} — ez az iroda most kimarad.",
                "error")
            traceback.print_exc()
            # FONTOS: a start() hibázhat a Chromium elindítása UTÁN is
            # (pl. sikertelen login) — ilyenkor is be kell zárni a böngészőt.
            try:
                await loop.run_in_executor(ex, eng.stop)
            except Exception:
                pass
            ex.shutdown(wait=False)
            continue
        log(f"[{label}] Bejelentkezés sikeres.", "ok")
        engines[bm], executors[bm] = eng, ex

    if not engines:
        log("Egyik irodába sem sikerült belépni — a figyelés nem indul.", "error")
        _allow_sleep()
        return

    log("Telegram figyelés indul.", "ok")
    _foot("Figyelés aktív")

    # A két iroda fogadásai soha nem futnak egyszerre (egymás után, a saját
    # véletlen késleltetésük szerint).
    place_lock = asyncio.Lock()

    processed: set = set()
    tasks:     set = set()

    async def handle_tip(tip: ParsedTip, key: str):
        """Egyetlen tipp teljes életciklusa — külön taskban fut."""
        bm    = tip.bookmaker
        tag   = f"[{tip.bookmaker_label}]"
        engine, executor = engines[bm], executors[bm]
        delay = random.uniform(30, 120)   # tippenként külön sorsolva
        log(f"{tag} Új tipp érkezett — {delay:.0f} mp múlva rakjuk meg:\n"
            f"  {tip.time}  {tip.home_team} vs {tip.away_team}\n"
            f"  {tip.pick_str} @ {tip.odds}", "tip")
        _foot(f"Várakozás {delay:.0f} mp...")
        _status(key, tip, "pending")
        await asyncio.sleep(delay)
        if stop_event and stop_event.is_set():
            return

        mode          = "[DRY RUN] " if cfg.dry_run else ""
        event_retries = 0
        base_stake    = cfg.books[bm].stake
        tip_stake     = stake_maps[bm].get(tip.strategy_key, base_stake)
        if tip_stake != base_stake:
            log(f"  tét ehhez a stratégiához ({tip.strategy_key}): {tip_stake} Ft", "muted")

        while True:
            async with place_lock:
                if stop_event and stop_event.is_set():
                    return
                log(f"{tag} Megrakás: {tip.pick_str} — {tip_stake} Ft...", "info")
                _foot("Fogadás folyamatban...")
                _status(key, tip, "placing")
                try:
                    result = await loop.run_in_executor(
                        executor, lambda: engine.place(tip, tip_stake))
                except Exception as e:
                    log(f"{tag} Kivétel a fogadás során: {e}", "error")
                    traceback.print_exc()
                    _foot("Hiba")
                    _status(key, tip, "fail", "kivétel")
                    await _notify_fail(tip, "kivétel a fogadás során")
                    return
            reason  = getattr(engine, "last_error", "")
            bet_ref = getattr(engine, "last_bet_ref", "")

            if result == "ok":
                ref = f"  (szelvény ID: {bet_ref})" if bet_ref else ""
                log(f"{tag} [BET_OK] {mode}{tip}{ref}", "ok")
                _foot("Fogadás OK ✓")
                _status(key, tip, "ok")
                if not cfg.dry_run:
                    await _notify_ok(tip, bet_ref)
                return

            if result == "fail":
                log(f"{tag} [BET_FAIL] {tip}" + (f" — {reason}" if reason else ""), "fail")
                _foot("Fogadás sikertelen")
                _status(key, tip, "fail")
                await _notify_fail(tip, reason)
                return

            if result == "line_changed":
                # A gólvonal elmozdult a tipp kiadása óta (pl. 8.5 → 5.5) —
                # más vonalra rakni más fogadás lenne, ezért KIHAGYJUK.
                log(f"{tag} [BET_SKIP] gólvonal megváltozott — kihagyva: {tip}", "warn")
                _foot("Vonal változott — kihagyva")
                _status(key, tip, "skipped", "vonal változott")
                await _notify_line_changed(tip)
                return

            # result == "notfound" — az esemény nincs (még) az oldalon.
            # CSAK ezt a tippet érinti: async várakozás, közben más tipp mehet.
            event_retries += 1
            if event_retries > MAX_EVENT_RETRIES:
                log(f"{tag} [BET_FAIL] esemény {MAX_EVENT_RETRIES}x nem volt megtalálható: {tip}", "fail")
                _foot("Esemény nem található")
                _status(key, tip, "fail", "esemény nincs")
                await _notify_fail(tip, "esemény nem található")
                return
            log(f"  esemény nincs az oldalon — {EVENT_RETRY_WAIT // 60} perc múlva újra "
                f"({event_retries}/{MAX_EVENT_RETRIES})", "warn")
            _foot(f"Esemény vár ({event_retries}/{MAX_EVENT_RETRIES})")
            _status(key, tip, "retry", f"{event_retries}/{MAX_EVENT_RETRIES}")
            await asyncio.sleep(EVENT_RETRY_WAIT)   # nem-blokkoló: más tippek mehetnek
            if stop_event and stop_event.is_set():
                return

    def on_tip(tip: ParsedTip):
        """A watcher hívja (szinkron, gyorsan visszatér). Taskot indít."""
        if tip.bookmaker not in engines:
            log(f"[{tip.bookmaker_label}] az iroda nem aktív — tipp kihagyva: {tip}", "muted")
            return
        # Dedup kulcs: az üzenet csak időpontot tartalmaz (dátumot nem),
        # ezért a feldolgozás dátumát is beletesszük — így nincs napok közti ütközés.
        today = f"{datetime.now():%Y-%m-%d}"
        key = (f"{today}|{tip.bookmaker}|{tip.time}|{tip.home_team}|"
               f"{tip.away_team}|{tip.pick}|{tip.line}")
        if key in processed:
            return
        processed.add(key)
        # Korlátos memória: ha túlnő, a korábbi napok kulcsait eldobjuk
        # (a maiak megmaradnak, így a mai duplikátumok továbbra is kiszűrődnek).
        if len(processed) > 1000:
            for k in [k for k in processed if not k.startswith(today)]:
                processed.discard(k)
        task = asyncio.create_task(handle_tip(tip, key))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    try:
        await start_watcher(
            api_id=cfg.api_id, api_hash=cfg.api_hash, phone=cfg.phone,
            # csak a sikeresen belépett irodák csatornáit figyeljük
            channels={cfg.books[bm].channel: bm for bm in engines},
            on_tip=on_tip,
            strategy_filter=cfg.strategy, stop_event=stop_event,
            log=log,   # a figyelő eseményei a fő (színes) Naplóra is kerüljenek
        )
    finally:
        # Folyamatban lévő tipp-taskok leállítása, majd a böngészők bezárása.
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for bm, eng in engines.items():
            await loop.run_in_executor(executors[bm], eng.stop)
            executors[bm].shutdown(wait=False)
        _allow_sleep()   # a gép visszamehet alvásba
        log("Leállt.", "muted")
