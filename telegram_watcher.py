"""
Telethon-based Telegram channel watcher.
Listens for new tip messages and triggers bet placement.
"""

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import PeerChannel

from tip_parser import parse_tip, ParsedTip
from paths import APP_DIR

SESSION_FILE = str(APP_DIR / "telegram_session")

# Milyen gyakran írjon „életjelet" (még figyelek…), ha közben nincs esemény.
# Sűrű (1 perc), hogy — a FIFA Tipster beépített betplacerhez hasonlóan —
# folyamatosan látszódjon, hogy él és figyel, nem csak tippkor.
HEARTBEAT_SEC = 60


def _log(msg: str):
    print(f"[tg]  {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


async def start_watcher(
    api_id:   int,
    api_hash: str,
    phone:    str,
    channel,          # str (@name) vagy int (channel ID)
    on_tip,           # callable(tip: ParsedTip) — szinkron, gyorsan visszatér
    strategy_filter: str = "",
    stop_event=None,  # threading.Event — ha beállítják, gracefully lekapcsol
    log=None,         # callable(msg, kind) — színes Napló (opcionális)
):
    """Connect to Telegram and listen for tips in the given channel.

    A handler szinkron `on_tip(tip)`-et hív, ami gyorsan visszatér (taskot indít),
    így egy tipp feldolgozása nem blokkolja a következő üzenetek fogadását.

    Ha `stop_event` meg van adva, a figyelő figyeli azt, és beállításakor
    gracefully lekapcsolódik (a hívó finally ága így le tudja zárni a böngészőt).

    `log(msg, kind)` ha meg van adva, minden fontos eseményt a fő (színes)
    Naplóra is kiír — nem csak a részletes konzolra. Így a felhasználó mindenről
    visszajelzést kap (csatlakozás, beérkező üzenet, tipp, kapcsolat, életjel).
    """
    def emit(msg: str, kind: str = "info"):
        """Részletes konzol ÉS (ha van) a színes Napló — egyszerre."""
        _log(msg)
        if log:
            try:
                log(msg, kind)
            except Exception:
                pass

    client = TelegramClient(SESSION_FILE, api_id, api_hash)
    # FONTOS: connect() + ellenőrzés a client.start(phone=...) HELYETT.
    # A start() lejárt session esetén interaktív input()-ra várna a konzolon,
    # ami GUI módban (konzol nélkül) csendes lefagyást okoz.
    emit("Csatlakozás a Telegramhoz...", "muted")
    await client.connect()
    if not await client.is_user_authorized():
        emit("A Telegram munkamenet lejárt vagy hiányzik.", "error")
        emit("Futtasd újra a beállítási varázslót: python main.py --setup", "muted")
        await client.disconnect()
        return
    emit(f"Csatlakozva. Figyelt csatorna: {channel}", "ok")

    # KRITIKUS: get_dialogs() MINDIG, csatlakozás után.
    # Két dolgot old meg egyszerre:
    #   1) Felébreszti az update-stream-et. Egy csupasz connect() után a szerver
    #      gyakran NEM küld új-üzenet eseményeket, amíg nincs egy magas szintű
    #      hívás (get_dialogs / catch_up) — enélkül a handler SOHA nem sül el,
    #      hiába tag a fiók és érkezik tipp (örök „Figyelés aktív", nulla reakció).
    #   2) Feltölti az entitás-cache-t → a nyers számszerű csatorna-ID feloldható.
    emit("Csatornalista betöltése (event-stream ébresztés)...", "muted")
    try:
        await client.get_dialogs()
    except Exception as e:
        emit(f"Dialógusok betöltése sikertelen: {e}", "warn")
    try:
        await client.catch_up()
    except Exception:
        pass

    # Resolve the channel entity (most már a cache-ből biztosan megvan).
    try:
        entity = await client.get_entity(channel)
    except Exception as e:
        emit(f"Csatorna nem található: {channel} — {e}", "error")
        emit("Ellenőrizd, hogy a bejelentkezett Telegram-fiók TAGJA-e a csatornának.", "muted")
        return

    title = getattr(entity, "title", None) or str(channel)
    emit(f"Csatorna rendben: „{title}\"", "ok")

    # Élő statisztika a heartbeathez.
    stats = {"msgs": 0, "tips": 0, "skipped": 0, "last_msg": "—"}

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        # raw_text: a sima szöveg markdown jelek NÉLKÜL. A .text visszarakná a
        # formázást (pl. **Pick:**), ami elrontja a parser pick-regexét → a tipp
        # tévesen „nem tipp-formátum"-ként kihullana. Fallback a .text-re.
        text = event.message.raw_text or event.message.text or ""
        stats["msgs"]    += 1
        stats["last_msg"] = datetime.now().strftime("%H:%M:%S")
        preview = " ".join(text.split())[:80] or "(nincs szöveg)"
        emit(f"Üzenet érkezett: {preview}", "info")

        tip = parse_tip(text)
        if tip is None:
            stats["skipped"] += 1
            emit("  → nem tipp-formátum, kihagyva.", "muted")
            return

        if strategy_filter and strategy_filter.lower() not in tip.strategy.lower():
            stats["skipped"] += 1
            emit(f"  → stratégia-szűrő kizárta: {tip.strategy}", "muted")
            return

        stats["tips"] += 1
        emit(f"Tipp felismerve: {tip}", "tip")
        on_tip(tip)   # szinkron, gyorsan visszatér (a munkát külön task végzi)

    emit("Figyelés aktív. Várom a tippeket…", "ok")

    # ── Egységes figyelő-ciklus: életjel + kapcsolatfigyelés + leállítás ────────
    # (A korábbi run_until_disconnected helyett — így CLI módban is van heartbeat,
    #  és látszik, ha a kapcsolat megszakad vagy visszajön.)
    last_beat     = time.monotonic()
    was_connected = True
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                emit("Leállítási jelzés — lekapcsolódás...", "muted")
                break

            connected = client.is_connected()
            if connected and not was_connected:
                emit("Telegram kapcsolat helyreállt — figyelés folytatódik.", "ok")
            elif not connected and was_connected:
                emit("Telegram kapcsolat megszakadt — automatikus újracsatlakozás...", "warn")
            was_connected = connected

            now = time.monotonic()
            if now - last_beat >= HEARTBEAT_SEC:
                last_beat = now
                allapot = "kapcsolódva" if connected else "újracsatlakozás folyamatban"
                emit(f"⏳ Figyelek ({allapot}) — eddig {stats['msgs']} üzenet, "
                     f"{stats['tips']} tipp, {stats['skipped']} kihagyva. "
                     f"Utolsó üzenet: {stats['last_msg']}", "muted")

            await asyncio.sleep(1.0)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def send_code_request(api_id: int, api_hash: str, phone: str) -> TelegramClient:
    """First step of auth: send verification code. Returns the client."""
    client = TelegramClient(SESSION_FILE, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
    return client


async def sign_in(client: TelegramClient, phone: str, code: str, password: str = ""):
    """
    Bejelentkezés a kapott kóddal (és ha meg van adva, 2FA jelszóval).

    Visszatérés: (status, message)
      status: "ok"        — sikeres
              "need_2fa"   — a kód jó, de 2FA jelszó kell (a hívó kérje be)
              "bad_code"   — hibás kód
              "expired"    — lejárt kód
              "error"      — egyéb hiba (a message tartalmazza)
    """
    from telethon.errors import (
        SessionPasswordNeededError, PhoneCodeInvalidError,
        PhoneCodeExpiredError, PhoneCodeEmptyError,
    )
    code = (code or "").replace(" ", "").strip()
    try:
        await client.sign_in(phone=phone, code=code)
        return "ok", ""
    except PhoneCodeInvalidError:
        return "bad_code", "Hibás kód — ellenőrizd a számjegyeket (csak az 5 szám)."
    except PhoneCodeEmptyError:
        return "bad_code", "Nem adtál meg kódot."
    except PhoneCodeExpiredError:
        return "expired", "A kód lejárt — kérj újat (Vissza → Kód kérése) és írd be gyorsan."
    except SessionPasswordNeededError:
        if not password:
            hint = await _get_2fa_hint(client)
            msg = "Kétlépcsős azonosítás (2FA) — add meg a Telegram-jelszavadat."
            if hint:
                msg += f"  Emlékeztető: „{hint}\""
            return "need_2fa", msg
        try:
            await client.sign_in(password=password)
            return "ok", ""
        except Exception as e:
            return "error", f"Hibás 2FA jelszó: {e}"
    except Exception as e:
        return "error", f"Bejelentkezési hiba: {e}"


async def sign_in_2fa(client: TelegramClient, password: str):
    """Csak a 2FA jelszó beküldése (a kód már elfogadva). Visszatérés: (status, message)."""
    try:
        await client.sign_in(password=password)
        return "ok", ""
    except Exception as e:
        return "error", f"Hibás 2FA jelszó: {e}"


async def _get_2fa_hint(client: TelegramClient) -> str:
    """A Telegramban tárolt 2FA jelszó-emlékeztető (hint) lekérése, ha van."""
    try:
        from telethon.tl.functions.account import GetPasswordRequest
        info = await client(GetPasswordRequest())
        return getattr(info, "hint", "") or ""
    except Exception:
        return ""
