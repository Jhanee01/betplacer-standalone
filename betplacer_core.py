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
from datetime import datetime, timedelta
from types import SimpleNamespace

import account_sync
import dashboard_state as ds


# ── Esemény-újrapróbálás (csak az adott tippre vonatkozik) ────────────────────
TIGHT_KICKOFF_S   = 480   # ennyi mp-nél közelebbi kezdésnél a véletlen várakozás elmarad
EVENT_RETRY_WAIT  = 300   # 5 perc — NEM blokkolja a többi tippet (async sleep)
MAX_EVENT_RETRIES = 3
# Késői újrapróbálás: ha a MAX_EVENT_RETRIES elfogyott, de a meccs még messze van, nem
# adjuk fel — LATE_RETRY_WAIT-enként újra nézzük, amíg a kezdésig LATE_RETRY_MIN_S-nál
# több marad. 2026-10-10: a 16:09-kor kiadott 22:58-as Vegas tipp gombja még nem volt
# kint, 15 perc után feladta; 17:01-kor elsőre ment.
LATE_RETRY_WAIT   = 600
LATE_RETRY_MIN_S  = 180

# ── Számla (főablak) ──────────────────────────────────────────────────────────
# A fiók adatai (név, egyenleg, nyitott fogadások) ennyi mp-enként frissülnek
# tétlenül; az eredményt a fiók lezárt fogadásaiból olvassuk: első lekérés a
# kezdés + 15 (8 perces) / + 20 (12 perces) perc után, utána SETTLE_RETRY_S-enként,
# legfeljebb SETTLE_GIVE_UP_S-ig.
INFO_REFRESH_S    = 300
SETTLE_AFTER_MIN  = {"8min": 15, "12min": 20}
SETTLE_RETRY_S    = 300
SETTLE_GIVE_UP_S  = 3 * 3600


# ── Gép ébren tartása futás közben (Windows) ──────────────────────────────────
# A számítógép NEM tud scriptet futtatni, amíg ténylegesen alszik (a CPU áll).
# Amíg viszont a BetPlacer fut, megkérjük a Windowst, hogy ne aludjon el — a
# kijelző közben elsötétülhet, de a rendszer ébren marad és tovább rak.
_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _seconds_to_kickoff(hhmm: str):
    """Hány mp van a meccs kezdéséig. A tipp élőben érkezik, ezért a kezdés a
    "HH:MM" következő előfordulása (éjfél után másnap). None, ha nem értelmezhető.
    A kezdés körüli pár percet (még friss tipp) nem toljuk át másnapra."""
    try:
        h, m = map(int, str(hhmm).strip().split(":"))
        now = datetime.now()
        ko = now.replace(hour=h, minute=m, second=0, microsecond=0)
        left = (ko - now).total_seconds()
        if left < -600:
            left += 86400
        return left
    except Exception:
        return None


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
                       default_channel="-1003404037430"),   # v5.0.0: a felületen nem állítható
    "vegas":      dict(user="VEGAS_USER", pw="VEGAS_PASS",
                       channel="TELEGRAM_CHANNEL_VEGAS", stake="BET_STAKE_VEGAS",
                       enabled="VEGAS_ENABLED", default_on="0",
                       default_channel="-1003886878395"),
}


# ── Források: egy forrás = egy figyelt Telegram-csatorna + iroda + alap tét ────
# FIFA-források: a fenti irodasorok (a .env-kulcsaik változatlanok, visszafelé
# kompatibilis). Real Event források (v3.1.0): a Jhanee Tipster Real Vegas / Real
# TippmixPro csatornái — valódi foci/kézi/kosár meccsek. A belépést az iroda
# FIFA-fiókjával közösen használják (egy iroda = egy böngésző, egy belépés).
REAL_ENV = {
    "real_tippmixpro": dict(channel="REAL_CHANNEL_TIPPMIXPRO", stake="REAL_STAKE_TIPPMIXPRO",
                            enabled="REAL_TIPPMIXPRO_ENABLED", default_on="0",
                            default_channel="-1004347346270", bookmaker="tippmixpro"),
    "real_vegas":      dict(channel="REAL_CHANNEL_VEGAS", stake="REAL_STAKE_VEGAS",
                            enabled="REAL_VEGAS_ENABLED", default_on="0",
                            default_channel="-1004432306659", bookmaker="vegas"),
}
SOURCE_ENV = {**{bm: dict(e, bookmaker=bm) for bm, e in BOOKMAKER_ENV.items()}, **REAL_ENV}


def source_bookmaker(src: str) -> str:
    return SOURCE_ENV[src]["bookmaker"]


def bookmaker_channel(src: str) -> str:
    """A forrás (FIFA-iroda vagy Real Event) csatornája a .env-ből, hiányában az alapérték."""
    e = SOURCE_ENV[src]
    return os.getenv(e["channel"], "").strip() or e["default_channel"]


def bookmaker_enabled(src: str) -> bool:
    e = SOURCE_ENV[src]
    return os.getenv(e["enabled"], e["default_on"]).strip().lower() in ("1", "true", "yes", "on")


def _fmt_tip_line(tip) -> str:
    if hasattr(tip, "fmt_line"):          # Real Event tipp
        return tip.fmt_line()
    """Egységes egysoros tipp-formátum az értesítésekhez (tét NÉLKÜL, játékosnevekkel).
    A standalone home_team/away_team már tartalmazza a játékost zárójelben
    (pl. 'Germany (Manuel)'). Pl.: '14:52 | Germany (Manuel) vs Scotland (John) | OU UNDER 6.5'."""
    if tip.market == "OU" and tip.line is not None:
        market_pick = f"OU {tip.pick} {tip.line}"
    else:
        market_pick = f"{tip.market} {tip.pick}"
    return f"{tip.time} | {tip.home_team} vs {tip.away_team} | {market_pick}"


def _tip_summary(tip) -> str:
    """A sikeres megrakás értesítésének törzse (a FIFA Tipster dashboarddal azonos):
    '2026-10-28 12:18 Leipzig (BlackStar98) vs Frankfurt (Edwardson)\\nTipp: Over 4.5, Odds: 1.95'."""
    when = f"{datetime.now():%Y-%m-%d} {tip.time}"
    if tip.market == "OU" and tip.line is not None:
        pick = f"{tip.pick.capitalize()} {tip.line}"
    else:
        pick = tip.pick.capitalize()
    return f"{when} {tip.home_team} vs {tip.away_team}\nTipp: {pick}, Odds: {tip.odds}"


def _parse_channel(raw: str):
    try:
        return int(raw)
    except ValueError:
        return raw


def _read_config() -> SimpleNamespace:
    """Beállítások beolvasása env-ből + hiányzó kulcsok kigyűjtése.

    `sources`: a BEKAPCSOLT források {forrás: SimpleNamespace(bookmaker, channel,
    stake)}; `creds`: {iroda: (felhasználó, jelszó)} — motor csak ezekhez az
    irodákhoz indul (a FIFA és a Real Event ugyanazt a belépést használja)."""
    dry_run  = os.getenv("BET_DRY_RUN", "0") == "1"
    api_id   = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone    = os.getenv("TELEGRAM_PHONE", "")
    strategy = os.getenv("TELEGRAM_STRATEGY_FILTER", "")

    required = {"TELEGRAM_API_ID": str(api_id), "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_PHONE": phone}
    sources, creds = {}, {}
    from config import REAL_EVENT_ENABLED
    for src, e in SOURCE_ENV.items():
        if not bookmaker_enabled(src):
            continue
        if src in REAL_ENV and not REAL_EVENT_ENABLED:
            continue            # a Real Event egyelőre használaton kívül
        bm, acc = e["bookmaker"], BOOKMAKER_ENV[e["bookmaker"]]
        ch_raw = bookmaker_channel(src)
        sources[src] = SimpleNamespace(
            bookmaker=bm, channel=_parse_channel(ch_raw),
            stake=int(float(os.getenv(e["stake"], os.getenv("BET_STAKE", "500")))),
        )
        creds[bm] = (os.getenv(acc["user"], ""), os.getenv(acc["pw"], ""))
        required.update({acc["user"]: creds[bm][0], acc["pw"]: creds[bm][1],
                         e["channel"]: ch_raw})

    missing = [k for k, v in required.items() if not v or v == "0"]
    if not sources:
        missing.append("legalább egy bekapcsolt forrás (FIFA vagy Real Event)")

    return SimpleNamespace(
        dry_run=dry_run, api_id=api_id, api_hash=api_hash, phone=phone,
        strategy=strategy, sources=sources, creds=creds, missing=missing,
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

    from tip_parser import BOOKMAKER_LABEL, SOURCE_LABEL
    for src in SOURCE_ENV:
        state = "AKTÍV" if src in cfg.sources else "KIKAPCSOLVA"
        log(f"{SOURCE_LABEL[src]}: {state}", "ok" if src in cfg.sources else "muted")

    # Stratégiánkénti tét — induláskori pillanatkép irodánként (a GUI futás közben
    # zárolja a szerkesztést). Ha egy stratégiához nincs külön tét, az iroda alap
    # tétje él.
    from stake_store import load_stakes
    stake_maps = {src: load_stakes(src) for src in cfg.sources}
    for src, sm in stake_maps.items():
        if sm:
            log(f"[{SOURCE_LABEL[src]}] Stratégiánkénti tét: " + ", ".join(
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
            ds.set_telegram("bot", "ok" if ok else "error",
                            error="" if ok else "az üzenet nem ment ki")
        except Exception as e:
            log(f"Értesítés küldése sikertelen: {e}", "warn")
            ds.set_telegram("bot", "error", error=str(e)[:160])

    # Az értesítés első sora megmondja, melyik irodán (melyik fiókkal) történt.
    async def _notify_ok(tip, bet_ref: str = ""):
        if not (notify_on_ok and notify_bot_ready):
            return
        if getattr(tip, "kind", "") == "real":
            msg = f"✅ Tipp megrakva — {tip.bookmaker_label}\n{_fmt_tip_line(tip)}"
        else:
            msg = f"✅ Tipp megrakása sikeres: {tip.bookmaker_label}\n{_tip_summary(tip)}"
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

    async def _notify_skipped(tip, reason: str):
        if not (notify_on_fail and notify_bot_ready):
            return
        await _send_notify(f"⚠️ Tipp NEM lett megrakva — {tip.bookmaker_label}\n"
                           f"{_fmt_tip_line(tip)}\nOk: {reason}")

    async def _notify_line_changed(tip):
        if not (notify_on_fail and notify_bot_ready):
            return
        await _send_notify(
            f"⚠️ Vonal megváltozott — a tipp NEM lett megrakva — {tip.bookmaker_label}\n"
            f"{_fmt_tip_line(tip)}\n"
            f"A(z) {tip.line} gólvonal már nem szerepel a kínálatban.")

    # Főablak: a futás kezdete + az értesítő bot elérhetősége
    ds.begin_run(cfg.dry_run, sorted(cfg.creds))
    if not notify_bot_ready:
        ds.set_telegram("bot", "missing", error="nincs beállítva értesítő bot")
    else:
        try:
            import notifier
            me = notifier.get_me(notify_bot_token)
            ds.set_telegram("bot", "ok" if me.get("ok") else "error",
                            bot=me.get("username", ""), error=me.get("error", ""))
        except Exception as e:
            ds.set_telegram("bot", "error", error=str(e)[:160])

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
    for bm, (username, password) in cfg.creds.items():
        label = BOOKMAKER_LABEL[bm]
        log(f"[{label}] Playwright engine indul (Chromium betöltés ~10-20 mp)...", "muted")
        ex  = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        base = next(s.stake for s in cfg.sources.values() if s.bookmaker == bm)
        eng = engine_cls[bm](username, password, base, cfg.dry_run)
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
        ds.end_run()
        _allow_sleep()
        return

    # ── Számla: fióknév, egyenleg, nyitott fogadások, elszámolás ──────────────
    import account_info
    awaiting: dict = {bm: [] for bm in engines}     # iroda → [esedékesség (epoch)]
    next_info: dict = {bm: 0.0 for bm in engines}
    last_settle: dict = {bm: 0.0 for bm in engines}

    async def _refresh_info(bm: str):
        try:
            info = await loop.run_in_executor(
                executors[bm], lambda: account_info.read_info(engines[bm], bm))
        except Exception as e:
            log(f"[{BOOKMAKER_LABEL[bm]}] fiókadatok nem olvashatók: {str(e)[:120]}", "muted")
            return
        if info:
            ds.set_office(bm, **info)
            if info.get("open_count") and not awaiting[bm]:
                # nyitott fogadás (pl. a program előző futásából) → elszámolás figyelése
                awaiting[bm].append(datetime.now().timestamp() + SETTLE_RETRY_S)
        next_info[bm] = datetime.now().timestamp() + INFO_REFRESH_S

    for bm in engines:
        await _refresh_info(bm)

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
        src   = tip.source or bm
        real  = getattr(tip, "kind", "") == "real"
        tag   = f"[{tip.bookmaker_label}]"
        engine, executor = engines[bm], executors[bm]
        delay = random.uniform(30, 120)   # tippenként külön sorsolva
        # Szoros kezdésnél a várakozás nem csúsztathatja a megrakást a kezdés utánra:
        # legkésőbb TIGHT_KICKOFF_S-mal a kezdés előtt induljon.
        left = tip.seconds_to_kickoff() if real else _seconds_to_kickoff(tip.time)
        if left is not None and delay > left - TIGHT_KICKOFF_S:
            delay = max(0.0, left - TIGHT_KICKOFF_S)
            log(f"{tag} Szoros kezdés ({left / 60:.1f} perc múlva) — rövidített várakozás", "info")
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
        base_stake    = cfg.sources[src].stake
        place_fn      = engine.place_real if real else engine.place

        def _stake_now() -> int:
            """FIFA: az iroda tétje a főablakból (megrakás előtt frissen olvasva, így az
            átírt tét a következő tipptől él); Real Event: a stratégiánkénti tét."""
            if real:
                return stake_maps[src].get(tip.strategy_key, base_stake)
            return ds.load_stakes()[bm]

        while True:
            async with place_lock:
                if stop_event and stop_event.is_set():
                    return
                tip_stake = _stake_now()
                log(f"{tag} Megrakás: {tip.pick_str} — {tip_stake} Ft...", "info")
                _foot("Fogadás folyamatban...")
                _status(key, tip, "placing")
                try:
                    result = await loop.run_in_executor(
                        executor, lambda: place_fn(tip, tip_stake))
                except Exception as e:
                    log(f"{tag} Kivétel a fogadás során: {e}", "error")
                    traceback.print_exc()
                    _foot("Hiba")
                    _status(key, tip, "fail", "kivétel")
                    ds.log_bet(tip, "failed", tip_stake, "kivétel a fogadás során", cfg.dry_run)
                    await _notify_fail(tip, "kivétel a fogadás során")
                    return
            reason  = getattr(engine, "last_error", "")
            bet_ref = getattr(engine, "last_bet_ref", "")

            if result == "ok":
                ref = f"  (szelvény ID: {bet_ref})" if bet_ref else ""
                log(f"{tag} [BET_OK] {mode}{tip}{ref}", "ok")
                _foot("Fogadás OK ✓")
                _status(key, tip, "ok")
                ds.log_bet(tip, "placed", tip_stake, dry_run=cfg.dry_run)
                if not cfg.dry_run:
                    if not real:
                        account_sync.record_placed(tip, tip_stake, bet_ref,
                                                   _seconds_to_kickoff(tip.time))
                        left = _seconds_to_kickoff(tip.time)
                        if left is not None:
                            after = SETTLE_AFTER_MIN.get(tip.minutes, 15) * 60
                            awaiting[bm].append(datetime.now().timestamp() + left + after)
                    await _notify_ok(tip, bet_ref)
                next_info[bm] = 0.0      # az egyenleg és a nyitott fogadások frissítése
                return

            if result == "fail":
                log(f"{tag} [BET_FAIL] {tip}" + (f" — {reason}" if reason else ""), "fail")
                _foot("Fogadás sikertelen")
                _status(key, tip, "fail")
                ds.log_bet(tip, "failed", tip_stake, reason, cfg.dry_run)
                await _notify_fail(tip, reason)
                return

            if result == "skipped":
                # Real Event: az odds a tipp óta esett / a kimenetelt levették —
                # szándékosan nem rakjuk meg (nem próbáljuk újra).
                log(f"{tag} [BET_SKIP] {tip} — {reason}", "warn")
                _foot("Kihagyva")
                _status(key, tip, "skipped", "odds esett" if "ODDS" in reason else "levéve")
                await _notify_skipped(tip, reason)
                return

            if result == "line_changed":
                # A gólvonal elmozdult a tipp kiadása óta (pl. 8.5 → 5.5) —
                # más vonalra rakni más fogadás lenne, ezért KIHAGYJUK.
                log(f"{tag} [BET_SKIP] gólvonal megváltozott — kihagyva: {tip}", "warn")
                _foot("Vonal változott — kihagyva")
                _status(key, tip, "skipped", "vonal változott")
                ds.log_bet(tip, "skipped", tip_stake,
                           f"elmozdult a vonal ({tip.line} már nincs a kínálatban)", cfg.dry_run)
                await _notify_line_changed(tip)
                return

            # result == "notfound" — az esemény nincs (még) az oldalon.
            # CSAK ezt a tippet érinti: async várakozás, közben más tipp mehet.
            event_retries += 1
            if event_retries > MAX_EVENT_RETRIES:
                # Ha a meccs még messze van, ritkábban tovább próbáljuk (csendben)
                left = _seconds_to_kickoff(tip.time)
                if left is not None and left - LATE_RETRY_WAIT > LATE_RETRY_MIN_S:
                    log(f"  esemény nincs az oldalon ({event_retries - 1}x), a meccs "
                        f"{left / 60:.0f} perc múlva kezdődik — {LATE_RETRY_WAIT // 60} perc "
                        f"múlva újra", "warn")
                    _foot("Esemény vár (késői újrapróbálás)")
                    _status(key, tip, "retry", f"késői {event_retries - MAX_EVENT_RETRIES}.")
                    await asyncio.sleep(LATE_RETRY_WAIT)
                    if stop_event and stop_event.is_set():
                        return
                    continue
                log(f"{tag} [BET_FAIL] esemény {event_retries - 1}x nem volt megtalálható: {tip}", "fail")
                _foot("Esemény nem található")
                _status(key, tip, "fail", "esemény nincs")
                ds.log_bet(tip, "failed", tip_stake, reason or "az esemény nem található", cfg.dry_run)
                await _notify_fail(tip, reason or "esemény nem található")
                return
            log(f"  esemény nincs az oldalon — {EVENT_RETRY_WAIT // 60} perc múlva újra "
                f"({event_retries}/{MAX_EVENT_RETRIES})", "warn")
            _foot(f"Esemény vár ({event_retries}/{MAX_EVENT_RETRIES})")
            _status(key, tip, "retry", f"{event_retries}/{MAX_EVENT_RETRIES}")
            await asyncio.sleep(EVENT_RETRY_WAIT)   # nem-blokkoló: más tippek mehetnek
            if stop_event and stop_event.is_set():
                return

    # ── Napi számla-szinkron jelentés ─────────────────────────────────────────
    # Reggel (account_sync.SYNC_REPORT_AFTER után) naponta egyszer: a belépett
    # irodák fiókjából lekéri az előző nap kiértékelt fogadásait, összeveti a
    # placed_bets.jsonl naplóval, és az értesítő botnak elküldi. A lekérés a már
    # belépett böngészőben, az iroda saját szálán fut (nincs második belépés).
    account_sync.tracking_since()

    async def _sync_report(day: str) -> bool:
        since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        results = {}
        for bm in engines:
            label = BOOKMAKER_LABEL[bm]
            try:
                async with place_lock:
                    rows = await loop.run_in_executor(
                        executors[bm],
                        lambda bm=bm: account_sync.fetch_account(engines[bm], bm, since))
                results[bm] = account_sync.compare(bm, day, rows)
            except Exception as e:
                log(f"[{label}] Számla-szinkron: a fiók nem kérdezhető le — {e}", "warn")
                results[bm] = {"error": str(e)}
        # Ha minden iroda hibázott, délig később újrapróbáljuk.
        if all(r.get("error") for r in results.values()) and datetime.now().hour < 12:
            return False
        text = account_sync.build_message(day, results)
        log(text, "muted")
        if notify_bot_ready:
            await _send_notify(text)
        account_sync.mark_sent(day)
        return True

    async def _sync_loop():
        while not (stop_event and stop_event.is_set()):
            day = account_sync.due_report_day()
            if day:
                try:
                    await _sync_report(day)
                except Exception as e:
                    log(f"Számla-szinkron hiba: {e}", "warn")
            await asyncio.sleep(600)

    if not cfg.dry_run:
        sync_task = asyncio.create_task(_sync_loop())
        tasks.add(sync_task)
        sync_task.add_done_callback(tasks.discard)

    async def _account_loop():
        """Fiókadatok 5 percenként; esedékes elszámolásnál a fiók lezárt fogadásai.
        Megrakás közben nem fut (place_lock), és mindig az iroda saját szálán."""
        since = ds.session_start()[:10]
        while not (stop_event and stop_event.is_set()):
            now = datetime.now().timestamp()
            for bm in engines:
                dues = awaiting[bm]
                dues[:] = [d for d in dues if now - d < SETTLE_GIVE_UP_S]
                settle = (not cfg.dry_run and any(d <= now for d in dues)
                          and now - last_settle[bm] >= SETTLE_RETRY_S)
                if not settle and now < next_info[bm]:
                    continue
                async with place_lock:
                    if settle:
                        last_settle[bm] = now
                        try:
                            rows = await loop.run_in_executor(
                                executors[bm],
                                lambda bm=bm: account_sync.fetch_account(engines[bm], bm, since))
                            ds.save_settled(bm, rows)
                            log(f"[{BOOKMAKER_LABEL[bm]}] Számla-elszámolás: "
                                f"{len(rows)} lezárt fogadás beolvasva", "muted")
                        except Exception as e:
                            log(f"[{BOOKMAKER_LABEL[bm]}] Számla-elszámolás sikertelen: "
                                f"{str(e)[:120]}", "warn")
                    await _refresh_info(bm)
                if settle and not ds.load_status().get("offices", {}).get(bm, {}).get("open_count"):
                    dues[:] = [d for d in dues if d > now]   # minden lezárult
            await asyncio.sleep(30)

    acc_task = asyncio.create_task(_account_loop())
    tasks.add(acc_task)
    acc_task.add_done_callback(tasks.discard)

    channel_ok: dict = {}       # csatorna-felirat → tagja-e a fiók (elérhető-e)

    def _watch_log(msg: str, kind: str = "info"):
        """A figyelő eseményei a naplóba + a csatorna-figyelés állapota a főablaknak.
        Csatornánként: ha a saját Telegram-fiók nem tagja a csatornának, az nem
        található → a főablak „nem tag” jelzést mutat (a tippeket nem látná)."""
        import re as _re
        m = _re.match(r"\[([^\]]+)\] Csatorna (rendben|nem található)", msg)
        if m:
            channel_ok[m.group(1)] = m.group(2) == "rendben"
        if "Figyelés aktív" in msg or "kapcsolat helyreállt" in msg:
            missing = [c for c, ok in channel_ok.items() if not ok]
            ds.set_telegram("watcher", "partial" if missing else "ok", channels=dict(channel_ok),
                            error=("Nem tagja a csatornának: " + ", ".join(missing)) if missing else "")
        elif "Csatlakozás a Telegramhoz" in msg:
            channel_ok.clear()
            ds.set_telegram("watcher", "connecting", channels={}, error="")
        elif "Egyik csatorna sem" in msg:
            ds.set_telegram("watcher", "error", channels=dict(channel_ok),
                            error="A fiók egyik tipp-csatornának sem tagja")
        elif "munkamenet lejárt" in msg:
            ds.set_telegram("watcher", "error", error="A Telegram-bejelentkezés lejárt — "
                            "futtasd újra a beállító varázslót")
        elif "kapcsolat megszakadt" in msg:
            ds.set_telegram("watcher", "error", error="újracsatlakozás folyamatban")
        log(msg, kind)

    def on_tip(tip: ParsedTip):
        """A watcher hívja (szinkron, gyorsan visszatér). Taskot indít."""
        if tip.bookmaker not in engines:
            log(f"[{tip.bookmaker_label}] az iroda nem aktív — tipp kihagyva: {tip}", "muted")
            return
        # Dedup kulcs: az üzenet csak időpontot tartalmaz (dátumot nem),
        # ezért a feldolgozás dátumát is beletesszük — így nincs napok közti ütközés.
        today = f"{datetime.now():%Y-%m-%d}"
        if getattr(tip, "kind", "") == "real":
            key = f"{today}|real|{tip.bookmaker}|{tip.odds_id}"
        else:
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
            channels={s.channel: src for src, s in cfg.sources.items()
                      if s.bookmaker in engines},
            on_tip=on_tip,
            strategy_filter=cfg.strategy, stop_event=stop_event,
            log=_watch_log,   # a figyelő eseményei a naplóba + állapot a főablaknak
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
        ds.set_telegram("watcher", "off")
        ds.end_run()
        _allow_sleep()   # a gép visszamehet alvásba
        log("Leállt.", "muted")
