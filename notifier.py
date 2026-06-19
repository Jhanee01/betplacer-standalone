"""
Telegram Bot API alapú értesítés-küldő.

Független a fő (Telethon userbot) bejelentkezéstől: egy KÜLÖN bot küldi az
üzenetet a felhasználónak, így az BEJÖVŐ üzenetként érkezik → push-értesítést
ad a telefonon (a Saved Messages-szel ellentétben, ami a saját, kimenő
üzenetekről nem ad értesítést).

Csak az stdlib-et használja (urllib) — nincs extra függőség.
"""

import json
import urllib.parse
import urllib.request

_API = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, params: dict, timeout: int = 15) -> dict:
    url  = _API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_me(token: str) -> dict:
    """Token-ellenőrzés. Visszaad: {'ok': bool, 'username': str, 'error': str}."""
    try:
        r = _call(token, "getMe", {})
        if r.get("ok"):
            return {"ok": True, "username": r["result"].get("username", ""), "error": ""}
        return {"ok": False, "username": "", "error": r.get("description", "ismeretlen hiba")}
    except Exception as e:
        return {"ok": False, "username": "", "error": str(e)}


def detect_chat_id(token: str):
    """
    A legutóbbi üzenet (pl. /start) küldőjének chat_id-ja a getUpdates-ből.
    A felhasználónak előbb meg kell nyomnia a Start-ot a botnál.
    Visszaad: chat_id (int) vagy None, ha még nincs üzenet.
    """
    try:
        r = _call(token, "getUpdates", {"limit": 100, "timeout": 0})
        if not r.get("ok"):
            return None
        chat_id = None
        for upd in r.get("result", []):
            msg = upd.get("message") or upd.get("edited_message")
            if msg and "chat" in msg:
                chat_id = msg["chat"]["id"]   # a legutolsó üzenet küldője nyer
        return chat_id
    except Exception:
        return None


def send_message(token: str, chat_id, text: str) -> bool:
    """Üzenet küldése a boton keresztül. True, ha sikerült."""
    try:
        r = _call(token, "sendMessage", {"chat_id": chat_id, "text": text})
        return bool(r.get("ok"))
    except Exception:
        return False
