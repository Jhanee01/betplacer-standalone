"""
First-run setup wizard — FIFA Tipster visual style.
4 lépés: Telegram bejelentkezés → kód → Tippmixpro → tét
Csatorna és stratégia szűrő hardcodeolva van (üzemeltető állítja be).
"""

import asyncio
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

# ── Üzemeltető tölti ki ───────────────────────────────────────────────────────
TELEGRAM_API_ID   = 38650083
TELEGRAM_API_HASH = "3fb9fa8f5e758ee0f36849c6b68cb832"
TELEGRAM_CHANNEL  = -1003404037430   # csatorna/csoport ID — minden tipp megrakásra kerül
# ─────────────────────────────────────────────────────────────────────────────

from paths import APP_DIR
ENV_PATH = APP_DIR / ".env"


def _env_quote(value: str) -> str:
    """
    .env-biztos idézés. Single-quote = teljesen literális a python-dotenv-ben
    (se behelyettesítés, se escape) — így a $, #, szóköz stb. nem romlik el.
    Ha a value egyszeres idézőjelet tartalmaz, dupla idézőjelre váltunk.
    """
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _AsyncRunner:
    """
    Egyetlen, FOLYAMATOSAN futó háttér event loop a Telegram-műveletekhez.

    Korábban a varázsló minden lépéshez külön loopot indított-állított le, ezért:
      • a kód kérése és a belépés között a kapcsolat eldobódott (idle-drop) →
        telethon újracsatlakozási hibák,
      • a végén a kliens nem volt lezárva → „Event loop is closed" / „Task was
        destroyed" hibaözön a lezárt loopon.
    Itt a loop végig fut (run_forever), a kapcsolat életben marad, a végén pedig
    tisztán lekapcsolunk.
    """
    def __init__(self):
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Coroutine futtatása a háttér-loopon; blokkol az eredményig (worker szálból hívd)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self):
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

# ── Színek (FIFA Tipster stílus) ─────────────────────────────────────────────
C_BG       = "#12121f"
C_CARD     = "#1e2028"
C_BORDER   = "#2a2d3a"
C_FG       = "#d9d9d9"
C_MUTED    = "#8d8d9f"
C_ACCENT   = "#FDB900"
C_GREEN    = "#3ba560"
C_RED      = "#d44a3a"
C_BTN_BG   = "#0a1e0a"
C_ENTRY_BG = "#16161f"

FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_HDR   = ("Segoe UI", 13, "bold")
FONT_BODY  = ("Segoe UI", 11)
FONT_SMALL = ("Segoe UI", 10)
FONT_BTN   = ("Segoe UI", 11, "bold")


# ─────────────────────────────────────────────────────────────────────────────
# Widget helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sep(parent, pady=8):
    tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", pady=pady)


def _label(parent, text, color=C_FG, font=FONT_BODY, anchor="w", pady=0):
    l = tk.Label(parent, text=text, bg=C_BG, fg=color, font=font, anchor=anchor)
    l.pack(fill="x", pady=pady)
    return l


def _entry_field(parent, label_text: str, hint: str = "", show: str = "") -> tk.Entry:
    outer = tk.Frame(parent, bg=C_BG)
    outer.pack(fill="x", pady=4)
    tk.Label(outer, text=label_text, bg=C_BG, fg=C_FG,
             font=FONT_BODY).pack(anchor="w")
    entry = tk.Entry(outer, show=show, bg=C_ENTRY_BG, fg=C_FG,
                     insertbackground=C_FG, relief="flat", bd=0,
                     font=FONT_BODY, highlightthickness=1,
                     highlightbackground=C_BORDER, highlightcolor=C_ACCENT)
    entry.pack(fill="x", ipady=6, pady=(2, 0))
    if hint:
        tk.Label(outer, text=hint, bg=C_BG, fg=C_MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(1, 0))
    return entry


def _primary_btn(parent, text: str, cmd, side="right") -> tk.Button:
    btn = tk.Button(parent, text=text, command=cmd,
                    bg=C_BTN_BG, fg=C_ACCENT,
                    activebackground="#1a3a1a", activeforeground=C_ACCENT,
                    relief="flat", bd=0, padx=20, pady=8,
                    font=FONT_BTN, cursor="hand2",
                    highlightthickness=1, highlightbackground=C_ACCENT)
    btn.pack(side=side, padx=(4, 0))
    return btn


def _secondary_btn(parent, text: str, cmd, side="left") -> tk.Button:
    btn = tk.Button(parent, text=text, command=cmd,
                    bg=C_CARD, fg=C_MUTED,
                    activebackground=C_BORDER, activeforeground=C_FG,
                    relief="flat", bd=0, padx=16, pady=8,
                    font=FONT_BODY, cursor="hand2",
                    highlightthickness=1, highlightbackground=C_BORDER)
    btn.pack(side=side)
    return btn


def _step_dots(parent, current: int, total: int):
    f = tk.Frame(parent, bg=C_BG)
    f.pack(anchor="center", pady=(0, 12))
    for i in range(1, total + 1):
        color = C_ACCENT if i <= current else C_BORDER
        tk.Label(f, text="●", bg=C_BG, fg=color,
                 font=("Segoe UI", 11)).pack(side="left", padx=3)


# ─────────────────────────────────────────────────────────────────────────────
# Wizard (4 lépés)
# ─────────────────────────────────────────────────────────────────────────────

class SetupWizard:
    STEPS = 5

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BetPlacer — Első beállítás")
        self.root.configure(bg=C_BG)
        self.root.resizable(False, True)        # függőlegesen átméretezhető (biztonsági tartalék)
        self.root.geometry("500x540")
        self.root.minsize(500, 500)
        self.root.eval("tk::PlaceWindow . center")

        # Állandó fejléc
        hdr = tk.Frame(self.root, bg=C_BG)
        hdr.pack(fill="x", padx=28, pady=(22, 0))
        tk.Label(hdr, text="⚽  BetPlacer", bg=C_BG, fg=C_ACCENT,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(hdr, text="Beállítási varázsló", bg=C_BG, fg=C_MUTED,
                 font=FONT_SMALL).pack(side="left", padx=(10, 0),
                                       anchor="s", pady=(0, 3))
        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill="x", pady=(12, 0))

        self._content = tk.Frame(self.root, bg=C_BG)
        self._content.pack(fill="both", expand=True, padx=28, pady=16)

        # State
        self._runner    = _AsyncRunner()   # állandó háttér event loop
        self._tg_client = None
        self._phone     = ""
        self._tg_pass   = ""
        self._tp_user   = ""
        self._tp_pass   = ""
        self._notify_token   = ""
        self._notify_chat_id = ""

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show(1)
        self.root.mainloop()

    def _on_close(self):
        """Ablak bezárása (X vagy mentés után): a Telegram-kliens tiszta lezárása."""
        self._close_tg()
        self.root.destroy()

    def _close_tg(self):
        """A Telegram-kliens lekapcsolása és a háttér-loop leállítása (hibaözön nélkül)."""
        client = self._tg_client
        if client is not None:
            try:
                coro = client.disconnect()
                if coro is not None:
                    self._runner.submit(coro)
            except Exception:
                pass
            self._tg_client = None
        self._runner.close()

    def _clear(self):
        for w in self._content.winfo_children():
            w.destroy()

    def _show(self, step: int):
        self._step = step
        self._clear()
        {1: self._p_phone,
         2: self._p_code,
         3: self._p_tippmix,
         4: self._p_bot,
         5: self._p_stake}[step]()

    # ── 1: Telefonszám ───────────────────────────────────────────────────────

    def _p_phone(self):
        _step_dots(self._content, 1, self.STEPS)
        _label(self._content, "Telegram bejelentkezés",
               color=C_FG, font=FONT_HDR, pady=(0, 4))
        _label(self._content,
               "Add meg a Telegram-fiókodhoz tartozó telefonszámot.\n"
               "A Telegram egy megerősítő kódot küld (SMS vagy Telegram-üzenet).",
               color=C_MUTED, font=FONT_SMALL, pady=(0, 8))
        _sep(self._content)

        self._e_phone  = _entry_field(self._content, "Telefonszám",
                                      hint="Pl. +36301234567")
        self._e_tgpass = _entry_field(
            self._content,
            "2FA jelszó (kétlépcsős azonosítás)",
            hint="CSAK akkor töltsd ki, ha a Telegram a kód mellé jelszót is kér.\n"
                 "Ha nincs 2FA-d (elég a kód) — hagyd ÜRESEN.",
            show="*")
        self._phone_status = _label(self._content, "", color=C_MUTED,
                                    font=FONT_SMALL, pady=4)

        nav = tk.Frame(self._content, bg=C_BG)
        nav.pack(side="bottom", fill="x", pady=(16, 0))
        _primary_btn(nav, "Kód kérése  →", self._request_code)

    def _request_code(self):
        phone = self._e_phone.get().strip()
        if not phone:
            messagebox.showerror("Hiba", "Add meg a telefonszámot!")
            return
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            messagebox.showerror("Konfiguráció hiányzik",
                                 "Hiányoznak az API kulcsok a setup_wizard.py-ból.")
            return
        self._phone   = phone
        self._tg_pass = self._e_tgpass.get().strip()
        self._phone_status.config(text="Kód küldése...", fg=C_ACCENT)
        self.root.update()

        def _run():
            from telegram_watcher import send_code_request
            try:
                client = self._runner.submit(
                    send_code_request(TELEGRAM_API_ID, TELEGRAM_API_HASH, phone))
                self._tg_client = client
                self.root.after(0, lambda: self._show(2))
            except Exception as e:
                self.root.after(0, lambda: self._phone_status.config(
                    text=f"Hiba: {e}", fg=C_RED))

        threading.Thread(target=_run, daemon=True).start()

    # ── 2: Megerősítő kód ────────────────────────────────────────────────────

    def _p_code(self):
        _step_dots(self._content, 2, self.STEPS)
        _label(self._content, "Megerősítő kód",
               color=C_FG, font=FONT_HDR, pady=(0, 4))
        _label(self._content,
               f"Telegram kódot küldött a(z)  {self._phone}  számra.",
               color=C_MUTED, font=FONT_SMALL, pady=(0, 8))
        _sep(self._content)

        self._e_2fa     = None      # 2FA mező csak akkor jelenik meg, ha kell
        self._e_code    = _entry_field(self._content, "Megerősítő kód",
                                       hint="5 számjegyű kód")
        self._code_st   = _label(self._content, "", color=C_MUTED,
                                 font=FONT_SMALL, pady=4)

        nav = tk.Frame(self._content, bg=C_BG)
        nav.pack(side="bottom", fill="x", pady=(16, 0))
        _secondary_btn(nav, "← Vissza", lambda: self._show(1))
        _primary_btn(nav, "Bejelentkezés  →", self._sign_in)

    def _sign_in(self):
        code = self._e_code.get().strip()
        if not code:
            messagebox.showerror("Hiba", "Add meg a kódot!")
            return
        self._code_st.config(text="Bejelentkezés...", fg=C_ACCENT)
        self.root.update()

        client = self._tg_client
        phone  = self._phone
        pwd    = self._tg_pass

        def _run():
            from telegram_watcher import sign_in
            status, msg = self._runner.submit(sign_in(client, phone, code, pwd))
            self.root.after(0, lambda: self._after_sign_in(status, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _after_sign_in(self, status: str, msg: str):
        if status == "ok":
            self._show(3)
        elif status == "need_2fa":
            self._prompt_2fa(msg)
        else:
            # bad_code / expired / error — konkrét, érthető üzenet
            self._code_st.config(text=msg or "Bejelentkezés sikertelen.", fg=C_RED)

    def _prompt_2fa(self, msg: str):
        """A kód jó volt, de 2FA jelszó kell — itt helyben bekérjük."""
        self._code_st.config(text=msg, fg=C_ACCENT)
        if self._e_2fa is not None:
            self._e_2fa.focus_set()
            return
        self._e_2fa = _entry_field(self._content,
                                   "2FA jelszó (kétlépcsős azonosítás)", show="*")
        self._e_2fa.focus_set()
        nav2 = tk.Frame(self._content, bg=C_BG)
        nav2.pack(side="bottom", fill="x", pady=(8, 0))
        _primary_btn(nav2, "2FA bejelentkezés  →", self._sign_in_2fa)

    def _sign_in_2fa(self):
        pwd = self._e_2fa.get().strip()
        if not pwd:
            messagebox.showerror("Hiba", "Add meg a 2FA jelszót!")
            return
        self._code_st.config(text="2FA ellenőrzés...", fg=C_ACCENT)
        self.root.update()

        client = self._tg_client

        def _run():
            from telegram_watcher import sign_in_2fa
            status, msg = self._runner.submit(sign_in_2fa(client, pwd))
            if status == "ok":
                self.root.after(0, lambda: self._show(3))
            else:
                self.root.after(0, lambda: self._code_st.config(text=msg, fg=C_RED))

        threading.Thread(target=_run, daemon=True).start()

    # ── 3: Tippmixpro ────────────────────────────────────────────────────────

    def _p_tippmix(self):
        _step_dots(self._content, 3, self.STEPS)
        _label(self._content, "Tippmixpro bejelentkezés",
               color=C_FG, font=FONT_HDR, pady=(0, 4))
        _label(self._content,
               "A BetPlacer ezzel a fiókkal fogja feladni a fogadásokat.",
               color=C_MUTED, font=FONT_SMALL, pady=(0, 8))
        _sep(self._content)

        self._e_tp_user = _entry_field(self._content, "Felhasználónév / e-mail")
        self._e_tp_pass = _entry_field(self._content, "Jelszó", show="*")

        nav = tk.Frame(self._content, bg=C_BG)
        nav.pack(side="bottom", fill="x", pady=(16, 0))
        _secondary_btn(nav, "← Vissza", lambda: self._show(2))
        _primary_btn(nav, "Tovább  →", self._save_tippmix)

    def _save_tippmix(self):
        u = self._e_tp_user.get().strip()
        p = self._e_tp_pass.get().strip()
        if not u or not p:
            messagebox.showerror("Hiba", "Töltsd ki mindkét mezőt!")
            return
        self._tp_user = u
        self._tp_pass = p
        self._show(4)

    # ── 4: Értesítő bot ────────────────────────────────────────────────────────

    def _p_bot(self):
        _step_dots(self._content, 4, self.STEPS)
        _label(self._content, "Értesítő bot (sikertelen fogadás)",
               color=C_FG, font=FONT_HDR, pady=(0, 4))
        _label(self._content,
               "Ha egy fogadást nem sikerül megrakni, egy Telegram-bot azonnal\n"
               "értesít — push-üzenettel a telefonodon.\n\n"
               "1) Telegramban nyisd meg a @BotFather-t → küldd: /newbot → kövesd a lépéseket\n"
               "2) Másold be ide a kapott TOKEN-t (pl. 123456:AAH…)\n"
               "3) Nyisd meg a saját új botodat és nyomj rá a Start-ra\n"
               "4) Kattints alább az „Összekapcsolás\" gombra",
               color=C_MUTED, font=FONT_SMALL, pady=(0, 8))
        _sep(self._content)

        self._e_bot_token = _entry_field(self._content, "Bot token",
                                         hint="A @BotFather-től kapott token")
        self._bot_status  = _label(self._content, "", color=C_MUTED,
                                    font=FONT_SMALL, pady=4)

        nav = tk.Frame(self._content, bg=C_BG)
        nav.pack(side="bottom", fill="x", pady=(16, 0))
        _secondary_btn(nav, "← Vissza", lambda: self._show(3))
        _primary_btn(nav, "Összekapcsolás  →", self._connect_bot)

        skip = tk.Frame(self._content, bg=C_BG)
        skip.pack(side="bottom", fill="x")
        tk.Button(skip, text="Most kihagyom (később beállítható)",
                  command=self._skip_bot, bg=C_BG, fg=C_MUTED,
                  activebackground=C_BG, activeforeground=C_FG,
                  relief="flat", bd=0, font=FONT_SMALL, cursor="hand2").pack(anchor="center")

    def _connect_bot(self):
        token = self._e_bot_token.get().strip()
        if not token:
            messagebox.showerror("Hiba",
                                 "Add meg a bot tokent, vagy kattints a „Most kihagyom\" gombra.")
            return
        self._bot_status.config(text="Bot ellenőrzése és Start keresése…", fg=C_ACCENT)
        self.root.update()

        def _run():
            import notifier
            me = notifier.get_me(token)
            if not me["ok"]:
                self.root.after(0, lambda: self._bot_status.config(
                    text=f"Érvénytelen token: {me['error']}", fg=C_RED))
                return
            chat_id = notifier.detect_chat_id(token)
            if chat_id is None:
                self.root.after(0, lambda: self._bot_status.config(
                    text=f"@{me['username']} rendben — most nyisd meg a botot, "
                         "nyomj Start-ot, majd kattints újra az Összekapcsolásra.",
                    fg=C_ACCENT))
                return
            # Teszt push: a user rögtön lássa a telefonján, hogy működik.
            sent = notifier.send_message(
                token, chat_id,
                "✅ BetPlacer összekapcsolva — az értesítések működnek.\n"
                "Ide fog érkezni a riasztás, ha egy fogadást nem sikerül megrakni.")
            self._notify_token   = token
            self._notify_chat_id = str(chat_id)
            if sent:
                self.root.after(0, lambda: self._bot_status.config(
                    text="✓ Teszt-üzenet elküldve — nézd meg a Telegramod! Tovább…",
                    fg=C_GREEN))
                self.root.after(900, lambda: self._show(5))
            else:
                self.root.after(0, lambda: self._show(5))

        threading.Thread(target=_run, daemon=True).start()

    def _skip_bot(self):
        self._notify_token   = ""
        self._notify_chat_id = ""
        self._show(5)

    # ── 5: Tét + mentés ──────────────────────────────────────────────────────

    def _p_stake(self):
        _step_dots(self._content, 5, self.STEPS)
        _label(self._content, "Tét beállítás",
               color=C_FG, font=FONT_HDR, pady=(0, 4))
        _label(self._content, "Minden fogadásnál ekkora tétet helyez el.",
               color=C_MUTED, font=FONT_SMALL, pady=(0, 8))
        _sep(self._content)

        self._e_stake = _entry_field(self._content, "Alap tét (Ft)",
                                     hint="Indításkor a  --stake <összeg>  flaggel is megadható")
        self._e_stake.insert(0, "500")

        self._dry_var = tk.BooleanVar(value=False)
        dry = tk.Frame(self._content, bg=C_BG)
        dry.pack(fill="x", pady=(8, 0))
        tk.Checkbutton(dry, text="  Teszt mód (dry run) — nem fogad élesben",
                       variable=self._dry_var, bg=C_BG, fg=C_FG,
                       selectcolor=C_ENTRY_BG, activebackground=C_BG,
                       activeforeground=C_ACCENT, font=FONT_BODY,
                       cursor="hand2").pack(anchor="w")

        _sep(self._content, pady=10)

        # Összefoglaló kártya
        card = tk.Frame(self._content, bg=C_CARD,
                        highlightthickness=1, highlightbackground=C_BORDER)
        card.pack(fill="x")
        tk.Label(card, text="Összefoglalás", bg=C_CARD, fg=C_ACCENT,
                 font=FONT_SMALL).pack(anchor="w", padx=12, pady=(8, 2))
        bot_txt = "beállítva ✓" if self._notify_token else "kihagyva"
        for k, v in [("Tippmixpro", self._tp_user),
                     ("Telegram", self._phone),
                     ("Értesítő bot", bot_txt)]:
            row = tk.Frame(card, bg=C_CARD)
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=f"{k}:", width=10, anchor="w",
                     bg=C_CARD, fg=C_MUTED, font=FONT_SMALL).pack(side="left")
            tk.Label(row, text=v, anchor="w",
                     bg=C_CARD, fg=C_FG, font=FONT_SMALL).pack(side="left")
        tk.Frame(card, bg=C_CARD, height=8).pack()

        nav = tk.Frame(self._content, bg=C_BG)
        nav.pack(side="bottom", fill="x", pady=(14, 0))
        _secondary_btn(nav, "← Vissza", lambda: self._show(4))
        _primary_btn(nav, "✓  Mentés és indítás", self._finish)

    def _finish(self):
        stake = self._e_stake.get().strip()
        try:
            int(stake)
        except ValueError:
            messagebox.showerror("Hiba", "A tét csak szám lehet!")
            return

        ENV_PATH.write_text(
            f"TIPPMIXPRO_USER={_env_quote(self._tp_user)}\n"
            f"TIPPMIXPRO_PASS={_env_quote(self._tp_pass)}\n"
            f"BET_STAKE={stake}\n"
            f"BET_DRY_RUN={'1' if self._dry_var.get() else '0'}\n"
            f"TELEGRAM_API_ID={TELEGRAM_API_ID}\n"
            f"TELEGRAM_API_HASH={TELEGRAM_API_HASH}\n"
            f"TELEGRAM_PHONE={_env_quote(self._phone)}\n"
            f"TELEGRAM_CHANNEL={TELEGRAM_CHANNEL}\n"
            f"TELEGRAM_STRATEGY_FILTER=\n"
            f"NOTIFY_ON_FAIL=1\n"
            f"NOTIFY_BOT_TOKEN={_env_quote(self._notify_token)}\n"
            f"NOTIFY_CHAT_ID={_env_quote(self._notify_chat_id)}\n",
            encoding="utf-8",
        )
        messagebox.showinfo("Kész!", "Beállítások elmentve.\nA BetPlacer most elindul.")
        # Tiszta Telegram-lezárás, hogy a háttér-taskok ne szórjanak hibát.
        self._on_close()


def run_wizard():
    SetupWizard()
