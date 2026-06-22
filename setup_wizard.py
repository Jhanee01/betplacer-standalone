"""
First-run setup wizard — PySide6 (a főablakkal AZONOS dashboard-stílus).

5 lépés: Telegram telefonszám → kód (+2FA) → Tippmixpro → értesítő bot → tét.
A Telegram API kulcsok és a csatorna hardcode-olva (üzemeltető állítja be).
Az auth-logika változatlan: _AsyncRunner háttér event loop + telegram_watcher/notifier.
"""

import asyncio
import sys
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QFrame, QCheckBox, QMessageBox,
)

from paths import APP_DIR
from gui import (
    apply_app_theme, _make_logo_label, ASSETS,
    BTN_OUTLINE, C_FG, C_ACCENT, C_MUTED, C_GREEN, C_RED,
)

# ── Üzemeltető tölti ki ───────────────────────────────────────────────────────
TELEGRAM_API_ID   = 38650083
TELEGRAM_API_HASH = "3fb9fa8f5e758ee0f36849c6b68cb832"
TELEGRAM_CHANNEL  = -1003404037430   # csatorna/csoport ID — minden tipp megrakásra kerül
# ─────────────────────────────────────────────────────────────────────────────

ENV_PATH = APP_DIR / ".env"

# Másodlagos (Vissza) gomb: halvány keret; link-gomb: keret nélküli szöveg.
BTN_SECONDARY = ("QPushButton{background:transparent;color:#8d8d9f;"
                 "border:1px solid #3a3a3a;border-radius:6px;padding:6px 14px;}"
                 "QPushButton:hover{color:#e8e8e8;border:1px solid #8d8d9f;}")
BTN_LINK = ("QPushButton{background:transparent;color:#8d8d9f;border:none;}"
            "QPushButton:hover{color:#e8e8e8;}")


def _env_quote(value: str) -> str:
    """.env-biztos idézés (single-quote = teljesen literális a python-dotenv-ben)."""
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _AsyncRunner:
    """Egyetlen, folyamatosan futó háttér event loop a Telegram-műveletekhez."""
    def __init__(self):
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Coroutine futtatása a háttér-loopon; blokkol az eredményig (worker szálból)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self):
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


class SetupWizard(QDialog):
    STEPS = 5
    _invoke = Signal(object)   # háttérszál → fő szál: emit(lambda) → main threaden fut

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BetPlacer — Első beállítás")
        self.setModal(True)
        self.setFixedWidth(520)
        self.setMinimumHeight(560)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        self._invoke.connect(lambda fn: fn())

        # State
        self._runner    = _AsyncRunner()
        self._tg_client = None
        self._phone     = ""
        self._tg_pass   = ""
        self._tp_user   = ""
        self._tp_pass   = ""
        self._notify_token   = ""
        self._notify_chat_id = ""
        self._saved     = False

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 20, 26, 20)
        root.setSpacing(10)

        # Fejléc
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(_make_logo_label(30))
        title = QLabel("BetPlacer")
        title.setStyleSheet("color:#ffffff; font-size:18px; font-weight:600;")
        hdr.addWidget(title)
        sub = QLabel("Beállítási varázsló")
        sub.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        sub.setAlignment(Qt.AlignBottom)
        hdr.addWidget(sub)
        hdr.addStretch(1)
        root.addLayout(hdr)

        ln = QFrame(); ln.setFrameShape(QFrame.HLine)
        ln.setStyleSheet("color:#2a2d3a; background:#2a2d3a; max-height:1px;")
        root.addWidget(ln)

        # Tartalom (lépésenként újraépül)
        self._content = QWidget()
        self._lay = QVBoxLayout(self._content)
        self._lay.setContentsMargins(0, 6, 0, 0)
        self._lay.setSpacing(8)
        root.addWidget(self._content, 1)

        self._show(1)

    # ── Lezárás ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._close_tg()
        super().closeEvent(event)

    def _close_tg(self):
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

    # ── UI helperek ──────────────────────────────────────────────────────────

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)   # azonnal eltűnik a képről (a deleteLater késik)
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _dots(self, current: int):
        row = QHBoxLayout(); row.addStretch(1)
        for i in range(1, self.STEPS + 1):
            d = QLabel("●")
            d.setStyleSheet(f"color:{C_ACCENT if i <= current else '#3a3a3a'}; font-size:12px;")
            row.addWidget(d)
        row.addStretch(1)
        self._lay.addLayout(row)

    def _title(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color:{C_FG}; font-size:16px; font-weight:600;")
        self._lay.addWidget(l)

    def _desc(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        l.setWordWrap(True)
        self._lay.addWidget(l)

    def _sep(self):
        ln = QFrame(); ln.setFrameShape(QFrame.HLine)
        ln.setStyleSheet("color:#2a2d3a; background:#2a2d3a; max-height:1px;")
        self._lay.addWidget(ln)

    def _field(self, label, hint="", password=False):
        l = QLabel(label)
        l.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        self._lay.addWidget(l)
        e = QLineEdit()
        if password:
            e.setEchoMode(QLineEdit.Password)
        self._lay.addWidget(e)
        if hint:
            h = QLabel(hint)
            h.setStyleSheet(f"color:{C_FG}; font-size:11px;")
            h.setWordWrap(True)
            self._lay.addWidget(h)
        return e

    def _status_label(self):
        l = QLabel("")
        l.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        l.setWordWrap(True)
        self._lay.addWidget(l)
        return l

    def _nav(self):
        self._lay.addStretch(1)
        row = QHBoxLayout()
        self._lay.addLayout(row)
        return row

    @staticmethod
    def _btn(text, cmd, style=BTN_OUTLINE):
        b = QPushButton(text)
        b.setStyleSheet(style)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(cmd)
        return b

    def _set_status(self, lbl, text, color=C_FG):
        lbl.setText(text)
        lbl.setStyleSheet(f"color:{color}; font-size:12px;")

    # ── Lépésváltó ─────────────────────────────────────────────────────────────

    def _show(self, step: int):
        self._step = step
        self._clear_layout(self._lay)
        {1: self._p_phone, 2: self._p_code, 3: self._p_tippmix,
         4: self._p_bot, 5: self._p_stake}[step]()

    # ── 1: Telefonszám ─────────────────────────────────────────────────────────

    def _p_phone(self):
        self._dots(1)
        self._title("Telegram bejelentkezés")
        self._desc("Add meg a Telegram-fiókodhoz tartozó telefonszámot.\n"
                   "A Telegram egy megerősítő kódot küld (SMS vagy Telegram-üzenet).")
        self._sep()
        self._e_phone  = self._field("Telefonszám", hint="Pl. +36301234567")
        self._e_tgpass = self._field(
            "2FA jelszó (kétlépcsős azonosítás)",
            hint="CSAK akkor töltsd ki, ha a Telegram a kód mellé jelszót is kér. "
                 "Ha nincs 2FA-d (elég a kód) — hagyd ÜRESEN.",
            password=True)
        self._phone_status = self._status_label()
        nav = self._nav()
        nav.addStretch(1)
        nav.addWidget(self._btn("Kód kérése", self._request_code))

    def _request_code(self):
        phone = self._e_phone.text().strip()
        if not phone:
            QMessageBox.critical(self, "Hiba", "Add meg a telefonszámot!")
            return
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            QMessageBox.critical(self, "Konfiguráció hiányzik",
                                 "Hiányoznak az API kulcsok a setup_wizard.py-ból.")
            return
        self._phone   = phone
        self._tg_pass = self._e_tgpass.text().strip()
        self._set_status(self._phone_status, "Kód küldése...", C_ACCENT)

        def _run():
            from telegram_watcher import send_code_request
            try:
                client = self._runner.submit(
                    send_code_request(TELEGRAM_API_ID, TELEGRAM_API_HASH, phone))
                self._tg_client = client
                self._invoke.emit(lambda: self._show(2))
            except Exception as e:
                self._invoke.emit(
                    lambda: self._set_status(self._phone_status, f"Hiba: {e}", C_RED))

        threading.Thread(target=_run, daemon=True).start()

    # ── 2: Megerősítő kód ──────────────────────────────────────────────────────

    def _p_code(self):
        self._dots(2)
        self._title("Megerősítő kód")
        self._desc(f"Telegram kódot küldött a(z)  {self._phone}  számra.")
        self._sep()
        self._e_code  = self._field("Megerősítő kód", hint="5 számjegyű kód")
        self._code_st = self._status_label()
        nav = self._nav()
        nav.addWidget(self._btn("Vissza", lambda: self._show(1), BTN_SECONDARY))
        nav.addStretch(1)
        nav.addWidget(self._btn("Bejelentkezés", self._sign_in))

    def _sign_in(self):
        code = self._e_code.text().strip()
        if not code:
            QMessageBox.critical(self, "Hiba", "Add meg a kódot!")
            return
        self._set_status(self._code_st, "Bejelentkezés...", C_ACCENT)
        client, phone, pwd = self._tg_client, self._phone, self._tg_pass

        def _run():
            from telegram_watcher import sign_in
            status, msg = self._runner.submit(sign_in(client, phone, code, pwd))
            self._invoke.emit(lambda: self._after_sign_in(status, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _after_sign_in(self, status: str, msg: str):
        if status == "ok":
            self._show(3)
        elif status == "need_2fa":
            self._p_code_2fa(msg)
        else:
            self._set_status(self._code_st, msg or "Bejelentkezés sikertelen.", C_RED)

    def _p_code_2fa(self, msg: str):
        """A kód jó volt, de 2FA jelszó kell — külön nézet."""
        self._clear_layout(self._lay)
        self._dots(2)
        self._title("Kétlépcsős azonosítás (2FA)")
        self._desc(msg or "Add meg a Telegram-jelszavadat.")
        self._sep()
        self._e_2fa   = self._field("2FA jelszó", password=True)
        self._code_st = self._status_label()
        nav = self._nav()
        nav.addWidget(self._btn("Vissza", lambda: self._show(2), BTN_SECONDARY))
        nav.addStretch(1)
        nav.addWidget(self._btn("Bejelentkezés", self._sign_in_2fa))

    def _sign_in_2fa(self):
        pwd = self._e_2fa.text().strip()
        if not pwd:
            QMessageBox.critical(self, "Hiba", "Add meg a 2FA jelszót!")
            return
        self._set_status(self._code_st, "2FA ellenőrzés...", C_ACCENT)
        client = self._tg_client

        def _run():
            from telegram_watcher import sign_in_2fa
            status, msg = self._runner.submit(sign_in_2fa(client, pwd))
            if status == "ok":
                self._invoke.emit(lambda: self._show(3))
            else:
                self._invoke.emit(
                    lambda: self._set_status(self._code_st, msg, C_RED))

        threading.Thread(target=_run, daemon=True).start()

    # ── 3: Tippmixpro ──────────────────────────────────────────────────────────

    def _p_tippmix(self):
        self._dots(3)
        self._title("Tippmixpro bejelentkezés")
        self._desc("A BetPlacer ezzel a fiókkal fogja feladni a fogadásokat.")
        self._sep()
        self._e_tp_user = self._field("Felhasználónév / e-mail")
        self._e_tp_pass = self._field("Jelszó", password=True)
        nav = self._nav()
        nav.addWidget(self._btn("Vissza", lambda: self._show(2), BTN_SECONDARY))
        nav.addStretch(1)
        nav.addWidget(self._btn("Tovább", self._save_tippmix))

    def _save_tippmix(self):
        u = self._e_tp_user.text().strip()
        p = self._e_tp_pass.text().strip()
        if not u or not p:
            QMessageBox.critical(self, "Hiba", "Töltsd ki mindkét mezőt!")
            return
        self._tp_user, self._tp_pass = u, p
        self._show(4)

    # ── 4: Értesítő bot ────────────────────────────────────────────────────────

    def _p_bot(self):
        self._dots(4)
        self._title("Értesítő bot (sikertelen fogadás)")
        self._desc(
            "Ha egy fogadást nem sikerül megrakni, egy Telegram-bot azonnal értesít "
            "— push-üzenettel a telefonodon.\n\n"
            "1) Telegramban nyisd meg a @BotFather-t → küldd: /newbot → kövesd a lépéseket\n"
            "2) Másold be ide a kapott TOKEN-t (pl. 123456:AAH…)\n"
            "3) Nyisd meg a saját új botodat és nyomj rá a Start-ra\n"
            "4) Kattints alább az „Összekapcsolás\" gombra")
        self._sep()
        self._e_bot_token = self._field("Bot token", hint="A @BotFather-től kapott token")
        self._bot_status  = self._status_label()
        skip = self._btn("Most kihagyom (később beállítható)", self._skip_bot, BTN_LINK)
        self._lay.addWidget(skip, alignment=Qt.AlignHCenter)
        nav = self._nav()
        nav.addWidget(self._btn("Vissza", lambda: self._show(3), BTN_SECONDARY))
        nav.addStretch(1)
        nav.addWidget(self._btn("Összekapcsolás", self._connect_bot))

    def _connect_bot(self):
        token = self._e_bot_token.text().strip()
        if not token:
            QMessageBox.critical(self, "Hiba",
                                 "Add meg a bot tokent, vagy kattints a „Most kihagyom\" gombra.")
            return
        self._set_status(self._bot_status, "Bot ellenőrzése és Start keresése…", C_ACCENT)

        def _run():
            import notifier
            me = notifier.get_me(token)
            if not me["ok"]:
                self._invoke.emit(lambda: self._set_status(
                    self._bot_status, f"Érvénytelen token: {me['error']}", C_RED))
                return
            chat_id = notifier.detect_chat_id(token)
            if chat_id is None:
                self._invoke.emit(lambda: self._set_status(
                    self._bot_status,
                    f"@{me['username']} rendben — most nyisd meg a botot, nyomj "
                    "Start-ot, majd kattints újra az Összekapcsolásra.", C_ACCENT))
                return
            sent = notifier.send_message(
                token, chat_id,
                "✅ BetPlacer összekapcsolva — az értesítések működnek.\n"
                "Ide fog érkezni a riasztás, ha egy fogadást nem sikerül megrakni.")
            self._notify_token   = token
            self._notify_chat_id = str(chat_id)
            if sent:
                self._invoke.emit(lambda: self._set_status(
                    self._bot_status,
                    "✓ Teszt-üzenet elküldve — nézd meg a Telegramod! Tovább…", C_GREEN))
            self._invoke.emit(lambda: self._show(5))

        threading.Thread(target=_run, daemon=True).start()

    def _skip_bot(self):
        self._notify_token   = ""
        self._notify_chat_id = ""
        self._show(5)

    # ── 5: Tét + mentés ────────────────────────────────────────────────────────

    def _p_stake(self):
        self._dots(5)
        self._title("Tét beállítás")
        self._desc("Minden fogadásnál ekkora tétet helyez el (stratégiánként a "
                   "főablakban felülírható).")
        self._sep()
        self._e_stake = self._field(
            "Alap tét (Ft)",
            hint="Indításkor a  --stake <összeg>  flaggel is megadható")
        self._e_stake.setText("500")

        self._dry_cb = QCheckBox("  Teszt mód (dry run) — nem fogad élesben")
        self._dry_cb.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        self._lay.addWidget(self._dry_cb)

        self._sep()

        # Összefoglaló kártya (a dashboard 'card' stílusa)
        card = QFrame(); card.setObjectName("card")
        cl = QVBoxLayout(card); cl.setSpacing(2)
        ct = QLabel("Összefoglalás")
        ct.setStyleSheet(f"color:{C_ACCENT}; font-size:11px; font-weight:600;")
        cl.addWidget(ct)
        bot_txt = "beállítva ✓" if self._notify_token else "kihagyva"
        for k, v in [("Tippmixpro", self._tp_user),
                     ("Telegram", self._phone),
                     ("Értesítő bot", bot_txt)]:
            row = QHBoxLayout()
            kl = QLabel(f"{k}:"); kl.setFixedWidth(96)
            kl.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
            vl = QLabel(v); vl.setStyleSheet(f"color:{C_FG}; font-size:12px;")
            row.addWidget(kl); row.addWidget(vl); row.addStretch(1)
            cl.addLayout(row)
        self._lay.addWidget(card)

        nav = self._nav()
        nav.addWidget(self._btn("Vissza", lambda: self._show(4), BTN_SECONDARY))
        nav.addStretch(1)
        nav.addWidget(self._btn("✓  Mentés és indítás", self._finish))

    def _finish(self):
        stake = self._e_stake.text().strip()
        try:
            int(stake)
        except ValueError:
            QMessageBox.critical(self, "Hiba", "A tét csak szám lehet!")
            return

        ENV_PATH.write_text(
            f"TIPPMIXPRO_USER={_env_quote(self._tp_user)}\n"
            f"TIPPMIXPRO_PASS={_env_quote(self._tp_pass)}\n"
            f"BET_STAKE={stake}\n"
            f"BET_DRY_RUN={'1' if self._dry_cb.isChecked() else '0'}\n"
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
        self._saved = True
        QMessageBox.information(self, "Kész!",
                               "Beállítások elmentve.\nA BetPlacer most elindul.")
        self._close_tg()
        self.accept()


def run_wizard():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_app_theme(app)
    dlg = SetupWizard()
    dlg.exec()
