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


def _fmt_tip_line(tip) -> str:
    """Egységes egysoros tipp-formátum az értesítésekhez (tét NÉLKÜL, játékosnevekkel).
    A standalone home_team/away_team már tartalmazza a játékost zárójelben
    (pl. 'Germany (Manuel)'). Pl.: '14:52 | Germany (Manuel) vs Scotland (John) | OU UNDER 6.5'."""
    if tip.market == "OU" and tip.line is not None:
        market_pick = f"OU {tip.pick} {tip.line}"
    else:
        market_pick = f"{tip.market} {tip.pick}"
    return f"{tip.time} | {tip.home_team} vs {tip.away_team} | {market_pick}"


def _read_config() -> SimpleNamespace:
    """Beállítások beolvasása env-ből + hiányzó kulcsok kigyűjtése."""
    username = os.getenv("TIPPMIXPRO_USER", "")
    password = os.getenv("TIPPMIXPRO_PASS", "")
    stake    = int(float(os.getenv("BET_STAKE", "500")))
    dry_run  = os.getenv("BET_DRY_RUN", "0") == "1"
    api_id   = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone    = os.getenv("TELEGRAM_PHONE", "")
    strategy = os.getenv("TELEGRAM_STRATEGY_FILTER", "")

    ch_raw = os.getenv("TELEGRAM_CHANNEL", "")
    try:
        channel = int(ch_raw)
    except ValueError:
        channel = ch_raw

    missing = [k for k, v in {
        "TIPPMIXPRO_USER": username, "TIPPMIXPRO_PASS": password,
        "TELEGRAM_API_ID": str(api_id), "TELEGRAM_API_HASH": api_hash,
        "TELEGRAM_PHONE": phone, "TELEGRAM_CHANNEL": ch_raw,
    }.items() if not v or v == "0"]

    return SimpleNamespace(
        username=username, password=password, stake=stake, dry_run=dry_run,
        api_id=api_id, api_hash=api_hash, phone=phone, strategy=strategy,
        channel=channel, missing=missing,
    )


async def run_session(log, foot=None, stop_event=None, on_status=None):
    """
    A teljes futás: Playwright indítás → Telegram figyelés → tippek megrakása.

    log:        callable(msg: str, kind: str = "info")  — kötelező
    foot:       callable(text: str)                      — opcionális státuszsor
    stop_event: threading.Event                          — opcionális leállítás
    on_status:  callable(key: str, tip, status: str, detail: str) — opcionális
                tipp-állapot jelzés a GUI „Mai tippek" paneljéhez.
                status: "pending" | "placing" | "retry" | "ok" | "fail"
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

    # Stratégiánkénti tét — induláskori pillanatkép (a GUI futás közben zárolja a
    # szerkesztést). Ha egy stratégiához nincs külön tét, a globális cfg.stake él.
    from stake_store import load_stakes
    stake_map = load_stakes()
    if stake_map:
        log("Stratégiánkénti tét: " + ", ".join(
            f"{k}={v}Ft" for k, v in stake_map.items()), "muted")

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

    async def _notify_ok(tip):
        if not (notify_on_ok and notify_bot_ready):
            return
        await _send_notify(f"✅ Tipp megrakva\n{_fmt_tip_line(tip)}")

    async def _notify_fail(tip, reason: str = ""):
        if not (notify_on_fail and notify_bot_ready):
            return
        msg = f"❌ Sikertelen fogadás\n{_fmt_tip_line(tip)}"
        if reason:
            msg += f"\nOk: {reason}"
        await _send_notify(msg)

    # Gép ébren tartása a teljes figyelési munkamenet alatt.
    if _prevent_sleep():
        log("Alvásgátlás bekapcsolva — a gép futás közben nem alszik el "
            "(a kijelző elsötétülhet).", "muted")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop     = asyncio.get_running_loop()

    log("Playwright engine indul (Chromium betöltés ~10-20 mp)...", "muted")
    engine = BetEngine(cfg.username, cfg.password, cfg.stake, cfg.dry_run)
    try:
        await loop.run_in_executor(executor, engine.start)
    except Exception as e:
        log(f"Tippmixpro bejelentkezés sikertelen: {e}", "error")
        traceback.print_exc()
        # FONTOS: a start() hibázhat a Chromium elindítása UTÁN is
        # (pl. sikertelen login) — ilyenkor is be kell zárni a böngészőt.
        try:
            await loop.run_in_executor(executor, engine.stop)
        except Exception:
            pass
        executor.shutdown(wait=False)
        _allow_sleep()
        return

    log("Bejelentkezés sikeres — Telegram figyelés indul.", "ok")
    _foot("Figyelés aktív")

    processed: set = set()
    tasks:     set = set()

    async def handle_tip(tip: ParsedTip, key: str):
        """Egyetlen tipp teljes életciklusa — külön taskban fut."""
        delay = random.uniform(30, 120)
        log(f"Új tipp érkezett — {delay:.0f} mp múlva rakjuk meg:\n"
            f"  {tip.time}  {tip.home_team} vs {tip.away_team}\n"
            f"  {tip.pick_str} @ {tip.odds}", "tip")
        _foot(f"Várakozás {delay:.0f} mp...")
        _status(key, tip, "pending")
        await asyncio.sleep(delay)
        if stop_event and stop_event.is_set():
            return

        mode          = "[DRY RUN] " if cfg.dry_run else ""
        event_retries = 0
        tip_stake     = stake_map.get(tip.strategy_key, cfg.stake)
        if tip_stake != cfg.stake:
            log(f"  tét ehhez a stratégiához ({tip.strategy_key}): {tip_stake} Ft", "muted")

        while True:
            log(f"Megrakás: {tip.pick_str} — {tip_stake} Ft...", "info")
            _foot("Fogadás folyamatban...")
            _status(key, tip, "placing")
            try:
                result = await loop.run_in_executor(executor, lambda: engine.place(tip, tip_stake))
            except Exception as e:
                log(f"Kivétel a fogadás során: {e}", "error")
                traceback.print_exc()
                _foot("Hiba")
                _status(key, tip, "fail", "kivétel")
                await _notify_fail(tip, "kivétel a fogadás során")
                return

            if result == "ok":
                log(f"[BET_OK] {mode}{tip}", "ok")
                _foot("Fogadás OK ✓")
                _status(key, tip, "ok")
                if not cfg.dry_run:
                    await _notify_ok(tip)
                return

            if result == "fail":
                log(f"[BET_FAIL] {tip}", "fail")
                _foot("Fogadás sikertelen")
                _status(key, tip, "fail")
                await _notify_fail(tip)
                return

            # result == "notfound" — az esemény nincs (még) az oldalon.
            # CSAK ezt a tippet érinti: async várakozás, közben más tipp mehet.
            event_retries += 1
            if event_retries > MAX_EVENT_RETRIES:
                log(f"[BET_FAIL] esemény {MAX_EVENT_RETRIES}x nem volt megtalálható: {tip}", "fail")
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
        # Dedup kulcs: az üzenet csak időpontot tartalmaz (dátumot nem),
        # ezért a feldolgozás dátumát is beletesszük — így nincs napok közti ütközés.
        today = f"{datetime.now():%Y-%m-%d}"
        key = (f"{today}|{tip.time}|{tip.home_team}|"
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
            channel=cfg.channel, on_tip=on_tip,
            strategy_filter=cfg.strategy, stop_event=stop_event,
            log=log,   # a figyelő eseményei a fő (színes) Naplóra is kerüljenek
        )
    finally:
        # Folyamatban lévő tipp-taskok leállítása, majd a böngésző bezárása.
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await loop.run_in_executor(executor, engine.stop)
        executor.shutdown(wait=False)
        _allow_sleep()   # a gép visszamehet alvásba
        log("Leállt.", "muted")
