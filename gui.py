"""
BetPlacer Standalone — GUI ablak (PySide6, FIFA Tipster dashboard stílus).

A dashboard kinézetét hozza: qt_material (theme_fdb900.xml) alap + a dashboard
style.qss overlay (pill-gombok, arany accent, lekerekített kártyák). A stílus-
fájlok a projekt `assets/` mappájában élnek (a standalone külön gépre települ,
ezért NEM hivatkozhat futásidőben a dashboard mappájára).

Szálkezelés: a teljes futás (Playwright + Telethon) háttérszálon, asyncio
loopban fut. A háttérszál SOHA nem nyúl a widgetekhez közvetlenül — minden UI
frissítés Qt signal-on keresztül megy a fő szálra (queued connection).
"""

import asyncio
import html
import os
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QIcon, QColor, QTextCursor, QPixmap, QImage, QFont, QIntValidator,
    QPainter, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QFrame, QTabWidget, QStackedWidget, QScrollArea, QTextEdit, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
    QDialog, QGridLayout,
)

from config import APP_VERSION
from paths import APP_DIR
from betplacer_core import (BOOKMAKER_ENV, SOURCE_ENV, REAL_ENV, bookmaker_enabled,
                            bookmaker_channel, source_bookmaker)
from tip_parser import BOOKMAKER_LABEL, SOURCE_LABEL
from config import REAL_EVENT_ENABLED
import stake_store

ASSETS = APP_DIR / "assets"

# Színek a style.qss-szel összhangban (státusz/log színezéshez).
C_ACCENT = "#FDB900"
C_GREEN  = "#3ba560"
C_RED    = "#d44a3a"
C_BLUE   = "#5794f2"
C_MUTED  = "#8d8d9f"
C_FG     = "#e8e8e8"

TAG_COLORS = {
    "info":  C_FG,    "muted": C_MUTED, "tip":  C_ACCENT, "ok":   C_GREEN,
    "fail":  C_RED,   "warn":  "#f2cc0c", "error": C_RED,
}

# ── Gomb- és panelstílusok — PONTOSAN a dashboard (ui/dashboard_view.py) szerint ──
# Start/Stop toggle: sötét háttér + színes vékony keret + színes szöveg (nem tömör pill).
BTN_START = ("QPushButton{background-color:#0a1e0a;color:#FDB900;"
             "border:1px solid #FDB900;border-radius:6px;padding:7px 18px;font-weight:600;}"
             "QPushButton:hover{background-color:#13260f;}")
BTN_STOP  = ("QPushButton{background-color:#3a1c1c;color:#d44a3a;"
             "border:1px solid #d44a3a;border-radius:6px;padding:7px 18px;font-weight:600;}"
             "QPushButton:hover{background-color:#4a2020;}")
# Másodlagos: átlátszó háttér + arany vékony keret (mint a NAPRAKÉSZ / szűrőgombok).
BTN_OUTLINE = ("QPushButton{background:transparent;color:#FDB900;border:1px solid #FDB900;"
               "border-radius:6px;padding:6px 14px;}"
               "QPushButton:hover{background:rgba(253,185,0,0.15);}"
               "QPushButton:disabled{color:#6b6b6b;border:1px solid #3a3a3a;}")
# Panel/kártya: szürkés háttér + vékony világos keret (a tippek/napló dobozokhoz).
PANEL_QSS = ("background:#272727;border:1px solid #3a3a3a;border-radius:8px;"
             "font-family:Consolas,'Courier New';font-size:12px;")

# Tipp-állapot → (felirat, szín). Csak Segoe UI-ban biztosan meglévő jeleket
# használunk (●, ✓, ✗); az emoji-szerű jelek (⏳, ⟳) kimaradnak, hogy ne legyen négyzet.
STATUS_LABELS = {
    "pending": ("● várakozás",   C_ACCENT),
    "placing": ("● megrakás…",   C_BLUE),
    "retry":   ("● esemény vár", C_ACCENT),
    "ok":      ("✓ OK",          C_GREEN),
    "fail":    ("✗ sikertelen",  C_RED),
    "skipped": ("✗ kihagyva",    C_ACCENT),
}


class _Tee:
    """stdout/stderr átirányítás: az eredeti streamre ÉS a Konzol fülre (signal)."""
    def __init__(self, emit_fn, tee=None):
        self._emit = emit_fn
        self._tee  = tee

    def write(self, s):
        if self._tee is not None:
            try:
                self._tee.write(s)
                self._tee.flush()
            except Exception:
                pass
        if s:
            try:
                self._emit(s)
            except Exception:
                pass

    def flush(self):
        if self._tee is not None:
            try:
                self._tee.flush()
            except Exception:
                pass

    def isatty(self):
        # Néhány könyvtár (pl. az uvicorn naplózó-formázója) lekérdezi a stdout
        # isatty()-jét; e nélkül AttributeError-rel hasalna el a beállításuk.
        return False


def apply_app_theme(app):
    """qt_material (theme_fdb900.xml) + style.qss overlay + Segoe UI font.
    A főablak ÉS a beállítási varázsló is ezt hívja → azonos kinézet."""
    theme = ASSETS / "theme_fdb900.xml"
    qss   = ASSETS / "style.qss"
    try:
        from qt_material import apply_stylesheet
        # v5.0.0: csak a qt_material téma — PONTOSAN mint a FIFA Tipster dashboard (az
        # nem tölt be style.qss-t); az overlay sötétebb hátteret és más gombokat adott.
        apply_stylesheet(app, theme=str(theme))
    except Exception:
        try:
            if qss.exists():
                app.setStyleSheet(qss.read_text(encoding="utf-8"))
        except Exception:
            pass
    # A qt_material a Roboto fontot rakja mindenre, amiben nincsenek meg a
    # ▶ / ■ / ● / ✓ jelek → üres négyzet. Segoe UI-ra váltunk (Windowson megvan).
    app.setFont(QFont("Segoe UI", 10))


def _make_logo_label(size: int = 28) -> QLabel:
    """A dashboard focilabda-logója: foci.png, fehér háttér flood-fill-lel eltávolítva."""
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setStyleSheet("background: transparent;")
    src = QPixmap(str(ASSETS / "foci.png"))
    if src.isNull():
        return lbl
    src = src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    img = src.toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    transparent = QColor(0, 0, 0, 0)

    def is_bg(x, y):
        c = img.pixelColor(x, y)
        return c.red() > 230 and c.green() > 230 and c.blue() > 230

    visited, queue = set(), deque()
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if is_bg(sx, sy):
            queue.append((sx, sy)); visited.add((sx, sy))
    while queue:
        x, y = queue.popleft()
        img.setPixelColor(x, y, transparent)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited and is_bg(nx, ny):
                visited.add((nx, ny)); queue.append((nx, ny))
    lbl.setPixmap(QPixmap.fromImage(img))
    return lbl


# Másodlagos (semleges) gomb a dialógusokhoz — halvány keret.
BTN_SECONDARY = ("QPushButton{background:transparent;color:#8d8d9f;"
                 "border:1px solid #3a3a3a;border-radius:6px;padding:6px 14px;}"
                 "QPushButton:hover{color:#e8e8e8;border:1px solid #8d8d9f;}")


class NotifyBotDialog(QDialog):
    """
    Értesítő bot beállítása utólag, a programon belül (a wizard 4. lépésének
    önálló változata). Beírod a @BotFather tokent, Start a botnál, majd
    „Összekapcsolás" → token-ellenőrzés + chat_id detektálás + teszt-üzenet,
    végül mentés a .env-be (NOTIFY_BOT_TOKEN / NOTIFY_CHAT_ID / NOTIFY_ON_FAIL).

    persist_env(key, value): a főablak .env-mentő helpere.
    log(msg, kind):          opcionális naplózó a főablakból.
    """
    _invoke = Signal(object)   # háttérszál → fő szál: emit(lambda) → main threaden fut

    def __init__(self, parent=None, persist_env=None, log=None):
        super().__init__(parent)
        self._persist_env = persist_env or (lambda k, v: None)
        self._log = log or (lambda *a, **k: None)
        self._busy = False
        self.setWindowTitle("Értesítő bot beállítása")
        self.setModal(True)
        self.setFixedWidth(500)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        self._invoke.connect(lambda fn: fn())

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(10)

        title = QLabel("Értesítő bot (sikertelen fogadás)")
        title.setStyleSheet(f"color:{C_FG}; font-size:16px; font-weight:600;")
        root.addWidget(title)

        desc = QLabel(
            "Ha egy fogadást nem sikerül megrakni, egy Telegram-bot azonnal értesít "
            "— push-üzenettel a telefonodon.\n\n"
            "1) Telegramban nyisd meg a @BotFather-t → küldd: /newbot → kövesd a lépéseket\n"
            "2) Másold be ide a kapott TOKEN-t (pl. 123456:AAH…)\n"
            "3) Nyisd meg a saját új botodat és nyomj rá a Start-ra\n"
            "4) Kattints alább az „Összekapcsolás\" gombra")
        desc.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        desc.setWordWrap(True)
        root.addWidget(desc)

        ln = QFrame(); ln.setFrameShape(QFrame.HLine)
        ln.setStyleSheet("color:#2a2d3a; background:#2a2d3a; max-height:1px;")
        root.addWidget(ln)

        tok_lbl = QLabel("Bot token")
        tok_lbl.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        root.addWidget(tok_lbl)
        self._token_entry = QLineEdit(os.getenv("NOTIFY_BOT_TOKEN", ""))
        self._token_entry.setPlaceholderText("A @BotFather-től kapott token")
        root.addWidget(self._token_entry)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        if os.getenv("NOTIFY_CHAT_ID", "").strip():
            self._set_status("Jelenleg már be van állítva egy értesítő bot. "
                             "Új token összekapcsolásával felülírható.", C_MUTED)

        root.addStretch(1)
        row = QHBoxLayout()
        self._close_btn = QPushButton("Bezárás")
        self._close_btn.setStyleSheet(BTN_SECONDARY)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)
        row.addWidget(self._close_btn)
        row.addStretch(1)
        self._connect_btn = QPushButton("Összekapcsolás")
        self._connect_btn.setStyleSheet(BTN_OUTLINE)
        self._connect_btn.setCursor(Qt.PointingHandCursor)
        self._connect_btn.clicked.connect(self._connect)
        row.addWidget(self._connect_btn)
        root.addLayout(row)

    def _set_status(self, text: str, color=C_FG):
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color}; font-size:12px;")

    def _connect(self):
        if self._busy:
            return
        token = self._token_entry.text().strip()
        if not token:
            QMessageBox.critical(self, "Hiba", "Add meg a bot tokent.")
            return
        self._busy = True
        self._connect_btn.setEnabled(False)
        self._set_status("Bot ellenőrzése és Start keresése…", C_ACCENT)

        def _work():
            import notifier
            me = notifier.get_me(token)
            if not me["ok"]:
                self._invoke.emit(lambda: self._fail(
                    f"Érvénytelen token: {me['error']}"))
                return
            chat_id = notifier.detect_chat_id(token)
            if chat_id is None:
                self._invoke.emit(lambda: self._fail(
                    f"@{me['username']} rendben — most nyisd meg a botot, nyomj "
                    "Start-ot, majd kattints újra az Összekapcsolásra."))
                return
            sent = notifier.send_message(
                token, chat_id,
                "✅ BetPlacer összekapcsolva — az értesítések működnek.\n"
                "Ide fog érkezni a riasztás, ha egy fogadást nem sikerül megrakni.")
            self._invoke.emit(lambda: self._success(token, str(chat_id), sent))

        threading.Thread(target=_work, daemon=True).start()

    def _fail(self, msg: str):
        self._busy = False
        self._connect_btn.setEnabled(True)
        self._set_status(msg, C_RED)

    def _success(self, token: str, chat_id: str, sent: bool):
        self._busy = False
        os.environ["NOTIFY_BOT_TOKEN"] = token
        os.environ["NOTIFY_CHAT_ID"]   = chat_id
        os.environ["NOTIFY_ON_FAIL"]   = "1"
        self._persist_env("NOTIFY_BOT_TOKEN", token)
        self._persist_env("NOTIFY_CHAT_ID", chat_id)
        self._persist_env("NOTIFY_ON_FAIL", "1")
        self._log("Értesítő bot beállítva.", "ok")
        if sent:
            QMessageBox.information(
                self, "Kész!",
                "Az értesítő bot összekapcsolva.\n\nKüldtünk egy teszt-üzenetet — "
                "nézd meg a Telegramod. Ha a program épp fut, a beállítás a "
                "következő indításkor lép érvénybe.")
        else:
            QMessageBox.warning(
                self, "Mentve",
                "A token elmentve, de a teszt-üzenetet nem sikerült elküldeni. "
                "Ellenőrizd az internetkapcsolatot és hogy nyomtál-e Start-ot a botnál.")
        self.accept()


def _env_quote(value: str) -> str:
    """.env-biztos idézés (single-quote = teljesen literális a python-dotenv-ben)."""
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Irodasor feliratai: kikapcsolt irodánál elhalványulnak.
_ROW_LBL_QSS = (f"QLabel{{color:{C_FG}; font-size:14px;}}"
                "QLabel:disabled{color:#5a5a5a;}")


class AccountDialog(QDialog):
    """Egy iroda belépési adatai (felhasználó / e-mail + jelszó) → .env."""

    def __init__(self, bookmaker: str, parent=None, persist_env=None, log=None):
        super().__init__(parent)
        self._bm = bookmaker
        self._e = BOOKMAKER_ENV[bookmaker]
        self._persist_env = persist_env or (lambda k, v: None)
        self._log = log or (lambda *a, **k: None)
        label = BOOKMAKER_LABEL[bookmaker]
        self.setWindowTitle(f"{label} fiók")
        self.setModal(True)
        self.setFixedWidth(420)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(10)
        title = QLabel(f"{label} belépési adatok")
        title.setStyleSheet(f"color:{C_FG}; font-size:16px; font-weight:600;")
        root.addWidget(title)
        desc = QLabel("A BetPlacer ezzel a fiókkal lép be és rak fogadást ennél az "
                      "irodánál. Az adatok csak ezen a gépen, a .env fájlban tárolódnak.")
        desc.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        desc.setWordWrap(True)
        root.addWidget(desc)

        user_lbl = QLabel("E-mail cím" if bookmaker == "vegas" else "Felhasználónév")
        user_lbl.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        root.addWidget(user_lbl)
        self._user = QLineEdit(os.getenv(self._e["user"], ""))
        root.addWidget(self._user)
        pw_lbl = QLabel("Jelszó")
        pw_lbl.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        root.addWidget(pw_lbl)
        self._pw = QLineEdit(os.getenv(self._e["pw"], ""))
        self._pw.setEchoMode(QLineEdit.Password)
        root.addWidget(self._pw)

        row = QHBoxLayout()
        cancel = QPushButton("Mégsem")
        cancel.setStyleSheet(BTN_SECONDARY)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch(1)
        save = QPushButton("Mentés")
        save.setStyleSheet(BTN_OUTLINE)
        save.clicked.connect(self._save)
        row.addWidget(save)
        root.addLayout(row)

    def _save(self):
        user, pw = self._user.text().strip(), self._pw.text()
        if not user or not pw:
            QMessageBox.critical(self, "Hiba", "Add meg a felhasználót és a jelszót.")
            return
        for key, val in ((self._e["user"], user), (self._e["pw"], pw)):
            os.environ[key] = val
            self._persist_env(key, _env_quote(val))   # különleges karakter a jelszóban
        self._log(f"{BOOKMAKER_LABEL[self._bm]} fiók mentve.", "ok")
        self.accept()


class SettingsDialog(QDialog):
    """Beállítások: irodák be/ki + belépési adatok, értesítő bot. A tipp-csatornák
    azonosítóit a program adja (a varázsló / alapértékek), itt nem kell állítani.
    Futás közben csak megtekinthető (a futtató mag induláskor olvas)."""

    def __init__(self, parent, running: bool, persist_env, log, open_notify):
        super().__init__(parent)
        self._persist_env, self._log = persist_env, log
        self.setWindowTitle("Beállítások")
        self.setModal(True)
        self.setFixedWidth(460)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)
        title = QLabel("Irodák")
        title.setStyleSheet(f"color:{C_FG}; font-size:16px; font-weight:600;")
        root.addWidget(title)
        hint = QLabel("Kikapcsolt irodánál a BetPlacer nem lép be, és a csatornáját sem figyeli."
                      + ("\nFutás közben nem módosítható — előbb állítsd le." if running else ""))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")
        root.addWidget(hint)
        self._toggles: dict = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        for r, bm in enumerate(BOOKMAKER_ENV):
            name = QLabel(BOOKMAKER_LABEL[bm].upper())
            name.setStyleSheet(f"color:{C_FG}; font-size:14px; font-weight:700;")
            toggle = QPushButton()
            toggle.setCheckable(True)
            toggle.setFixedWidth(140)
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setChecked(bookmaker_enabled(bm) and _has_account(bm))
            toggle.toggled.connect(lambda on, t=toggle: _style_toggle(t, on))
            toggle.clicked.connect(lambda on, bm=bm: self._on_clicked(bm, on))
            _style_toggle(toggle, toggle.isChecked())
            acc = QPushButton("Fiók")
            acc.setStyleSheet(BTN_OUTLINE)
            acc.setCursor(Qt.PointingHandCursor)
            acc.clicked.connect(lambda _=False, bm=bm: self._open_account(bm))
            for w in (toggle, acc):
                w.setEnabled(not running)
            grid.addWidget(name, r, 0)
            grid.addWidget(toggle, r, 1)
            grid.addWidget(acc, r, 2)
            self._toggles[bm] = toggle
        root.addLayout(grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#4f5b62; background:#4f5b62; max-height:2px;")
        root.addWidget(sep)
        bot_row = QHBoxLayout()
        bot_lbl = QLabel("Értesítő bot (Telegram-értesítés a megrakásokról)")
        bot_lbl.setWordWrap(True)
        bot_lbl.setStyleSheet(f"color:{C_FG}; font-size:13px;")
        bot_btn = QPushButton("Beállítás")
        bot_btn.setStyleSheet(BTN_OUTLINE)
        bot_btn.setCursor(Qt.PointingHandCursor)
        bot_btn.clicked.connect(open_notify)
        bot_row.addWidget(bot_lbl, 1)
        bot_row.addWidget(bot_btn)
        root.addLayout(bot_row)
        if os.getenv("BET_DRY_RUN", "0") == "1":
            dr = QLabel("TESZT MÓD (dry run) — a szelvényt kitölti, de nem fogad élesben.")
            dr.setWordWrap(True)
            dr.setStyleSheet(f"color:{C_ACCENT}; font-size:12px; font-weight:600;")
            root.addWidget(dr)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Bezárás")
        ok.setStyleSheet(BTN_OUTLINE)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        root.addLayout(row)
        self._running = running

    def _open_account(self, bm: str):
        AccountDialog(bm, self, persist_env=self._persist_env, log=self._log).exec()

    def _on_clicked(self, bm: str, on: bool):
        """Bekapcsoláskor fiók nélkül rögtön a Fiók ablak; adat nélkül visszaáll."""
        if on and not _has_account(bm):
            self._open_account(bm)
            if not _has_account(bm):
                self._toggles[bm].setChecked(False)
                self._log(f"{BOOKMAKER_LABEL[bm]}: belépési adat nélkül nem kapcsolható be.", "warn")

    def accept(self):
        if not self._running:
            for bm, t in self._toggles.items():
                key, val = BOOKMAKER_ENV[bm]["enabled"], "1" if t.isChecked() else "0"
                os.environ[key] = val
                self._persist_env(key, val)
        super().accept()

    def reject(self):
        self.accept()


def _has_account(bm: str) -> bool:
    e = BOOKMAKER_ENV[bm]
    return bool(os.getenv(e["user"], "")) and bool(os.getenv(e["pw"], ""))


def _style_toggle(t: QPushButton, on: bool):
    t.setText("AKTÍV" if on else "KIKAPCSOLVA")
    t.setStyleSheet(
        f"QPushButton{{background:#0a1e0a;color:{C_GREEN};border:1px solid {C_GREEN};"
        "border-radius:6px;padding:6px 10px;font-weight:700;}"
        "QPushButton:disabled{color:#2f6b45;border:1px solid #2f6b45;}" if on else
        f"QPushButton{{background:transparent;color:{C_MUTED};border:1px solid #3a3a3a;"
        "border-radius:6px;padding:6px 10px;font-weight:700;}"
        "QPushButton:disabled{color:#5a5a5a;}")


class BetPlacerWindow(QMainWindow):
    # ── Háttérszálból a fő szálra: minden UI-frissítés ezeken át ────────────────
    sigLog     = Signal(str, str)          # msg, kind
    sigFoot    = Signal(str)               # státuszsor szöveg
    sigConsole = Signal(str)               # nyers konzol szöveg
    sigStatus  = Signal(str, object, str, str)  # key, tip, status, detail
    sigRunning = Signal(bool)              # futási állapot változott
    sigUpdate  = Signal(str, object)       # frissítés-gomb állapot, info
    sigCheckResult  = Signal(object, bool, bool)  # info, checked, silent
    sigUpdateFailed = Signal(str)          # frissítés-hiba szöveg
    sigQuitForUpdate = Signal()            # kilépés a frissítéshez

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BetPlacer")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))

        import dashboard_state
        dashboard_state.session_start()     # a munkamenet a program indulásától tart

        self._running  = False
        self._loop     = None
        self._thread   = None
        self._stop_evt = threading.Event()

        self._checking     = False
        self._update_info  = None
        self._update_state = "idle"

        # Signalok bekötése (queued, mert háttérszálból is jönnek)
        self.sigLog.connect(self._append_log)
        self.sigFoot.connect(self._foot_lbl_set)
        self.sigConsole.connect(self._append_console)
        self.sigStatus.connect(self._apply_status)
        self.sigRunning.connect(self._apply_running)
        self.sigUpdate.connect(self._apply_update_button)
        self.sigCheckResult.connect(self._on_check_result)
        self.sigUpdateFailed.connect(self._update_failed)
        self.sigQuitForUpdate.connect(self._quit_for_update)

        self.remote_server = None

        from main_view import MainView
        self._view = MainView(APP_VERSION)
        self._view.toggle_requested.connect(lambda _name: self._toggle())
        self._view.settings_requested.connect(self._open_settings)
        self._view.update_requested.connect(self._on_update_click)
        self.setCentralWidget(self._view)
        self._update_btn = self._view.update_btn

        self._install_stdout_redirect()
        self._log("BetPlacer kész. Kattints az Indítás gombra.", "muted")
        QTimer.singleShot(4000, lambda: self._check_updates(silent=True))
        self._start_remote_server()

    def _open_settings(self):
        SettingsDialog(self, self._running, self._persist_env, self._log,
                       self._open_notify_setup).exec()

    # ══════════════════════════════════════════════════════════════════════════
    # stdout/stderr átirányítás
    # ══════════════════════════════════════════════════════════════════════════

    def _install_stdout_redirect(self):
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = _Tee(self.sigConsole.emit, tee=self._orig_stdout)
        sys.stderr = _Tee(self.sigConsole.emit, tee=self._orig_stderr)

    def _restore_stdout(self):
        try:
            if getattr(self, "_orig_stdout", None) is not None:
                sys.stdout = self._orig_stdout
            if getattr(self, "_orig_stderr", None) is not None:
                sys.stderr = self._orig_stderr
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # Slotok (fő szál) — a signalok ide érkeznek
    # ══════════════════════════════════════════════════════════════════════════

    def _append_log(self, msg: str, kind: str):
        if self.remote_server is not None:
            self.remote_server.push_log(msg, kind)
        self._view.append_log(msg, kind)

    def _append_console(self, s: str):
        """A nyers kimenet (pl. a böngésző-motor lépései) a napi szövegfájlba:
        naplo/konzol_ÉÉÉÉ-HH-NN.txt — hibakereséshez."""
        try:
            d = APP_DIR / "naplo"
            d.mkdir(exist_ok=True)
            with open(d / f"konzol_{datetime.now():%Y-%m-%d}.txt", "a", encoding="utf-8") as f:
                f.write(s)
        except OSError:
            pass

    def _foot_lbl_set(self, text: str):
        pass    # a régi státuszsor helyett a főnézet mutatja az állapotot

    def _apply_status(self, key: str, tip, status: str, detail: str):
        self._view.refresh()    # a fogadáslista a naplófájlból frissül

    def _apply_running(self, running: bool):
        if self.remote_server is not None:
            self.remote_server.set_running(running)
        self._view.set_running(running)

    # ── A core/háttér ezeket hívja (háttérszálból) → signal emit ───────────────

    def _log(self, msg: str, kind: str = "info"):
        self.sigLog.emit(msg, kind)

    def _foot(self, text: str):
        self.sigFoot.emit(text)

    def _on_status(self, key: str, tip, status: str, detail: str = ""):
        self.sigStatus.emit(key, tip, status, detail)

    # ══════════════════════════════════════════════════════════════════════════
    # Indítás / leállítás
    # ══════════════════════════════════════════════════════════════════════════

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        active = [bm for bm in BOOKMAKER_ENV if bookmaker_enabled(bm) and _has_account(bm)]
        if not active:
            QMessageBox.information(
                self, "Nincs aktív iroda",
                "Kapcsolj be legalább egy irodát (TippmixPro / Vegas) a Beállításokban, "
                "és add meg a belépési adatait.")
            self._open_settings()
            active = [bm for bm in BOOKMAKER_ENV if bookmaker_enabled(bm) and _has_account(bm)]
            if not active:
                return
        for bm in BOOKMAKER_ENV:
            self._log(f"[{BOOKMAKER_LABEL[bm]}] " + ("AKTÍV" if bm in active else "KIKAPCSOLVA"),
                      "muted")
        self._running = True
        self._stop_evt.clear()
        self.sigRunning.emit(True)
        self._log("Indítás...", "info")
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def _persist_env(self, key: str, value: str):
        """Egy kulcs frissítése a .env-ben (a többi sor érintetlen marad)."""
        try:
            path = APP_DIR / ".env"
            if not path.exists():
                return
            lines = path.read_text(encoding="utf-8").splitlines()
            out, found = [], False
            for ln in lines:
                if ln.startswith(f"{key}="):
                    out.append(f"{key}={value}")
                    found = True
                else:
                    out.append(ln)
            if not found:
                out.append(f"{key}={value}")
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
        except Exception as e:
            self._log(f"{key} mentése a .env-be sikertelen: {e}", "warn")

    def _stop(self):
        self._log("Leállítás... (a böngésző bezárása pár másodperc)", "warn")
        self._stop_evt.set()
        self._running = False
        self.sigRunning.emit(False)

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            self._log(f"Kritikus hiba: {e}", "error")
        finally:
            self._running = False
            self.sigRunning.emit(False)

    async def _async_main(self):
        from betplacer_core import run_session
        await run_session(
            log=self._log,
            foot=self._foot,
            stop_event=self._stop_evt,
            on_status=self._on_status,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Remote (mobil web-vezérlő) — élő napló + Újraindítás telefonról
    # ══════════════════════════════════════════════════════════════════════════

    def _start_remote_server(self):
        """A Remote webszerver indítása háttérszálon (ha engedélyezett).

        Token nélkül / kikapcsolva nem indul. Bármilyen hiba esetén csak naplózunk —
        a remote opcionális ráadás, nem törheti meg a fő (asztali) működést.
        """
        try:
            import config
            if config.remote_off():
                self._log("Remote kikapcsolva (RIM_REMOTE_ENABLED=false).", "muted")
                return
            token = config.ensure_remote_token()
            if not token:
                self._log("Remote kikapcsolva (nincs token).", "muted")
                return

            from remote.server import RemoteServer, run_server
            # A RemoteServer a GUI-szálon él, self-hez kötve (a teljes futás alatt él).
            self.remote_server = RemoteServer(self)
            self.remote_server.set_running(self._running)
            # A telefonról jövő „Újraindítás" a GUI-szálon fut (queued connection,
            # mert a jel az uvicorn asyncio-száláról érkezik).
            self.remote_server.restart_requested.connect(self._remote_restart)

            host = config.RIM_REMOTE_HOST
            want_port = config.RIM_REMOTE_PORT
            # Foglalt port (pl. bennragadt korábbi példány) ne döntse el a remote-ot:
            # keresünk egy szabadot a kívánt porttól felfelé.
            port = self._free_port(host, want_port)
            if port is None:
                self.remote_server = None
                self._log(f"Remote nem indult: a {want_port}–{want_port + 9} portok "
                          "mind foglaltak (fut már egy másik BetPlacer példány?).",
                          "warn")
                return
            if port != want_port:
                self._log(f"A {want_port} port foglalt — a remote a {port} "
                          "porton indul.", "muted")

            def _serve():
                try:
                    run_server(host, port, self.remote_server, token)
                except (OSError, SystemExit) as e:
                    self._log(f"Remote: a webszerver nem indult el "
                              f"(port {port} foglalt?): {e}", "warn")
                except Exception as e:
                    self._log(f"Remote: a webszerver leállt: {e}", "warn")

            threading.Thread(target=_serve, daemon=True, name="remote-server").start()
            # A 0.0.0.0 csak BIND-cím (minden interfész) — böngészőben nem nyitható.
            # Megjelenítéshez a 127.0.0.1-et használjuk.
            disp_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
            print(f"[Remote] Webszerver: http://{disp_host}:{port}/?token={token}")
            self._log(f"Remote elérhető — helyben: "
                      f"http://127.0.0.1:{port}/?token={token}", "muted")
            ts_ip = self._tailscale_ip()
            if ts_ip:
                self._log(f"Telefonról: http://{ts_ip}:{port}/?token={token}", "muted")
            else:
                self._log("Telefonról: a Tailscale-cím (100.x.y.z) ugyanezzel a "
                          "?token=... végződéssel.", "muted")
        except Exception as e:
            self.remote_server = None
            self._log(f"Remote nem indult el: {e}", "warn")

    def _tailscale_ip(self):
        """A gép Tailscale-IP-je (100.64.0.0/10), ha megtalálható — különben None."""
        import socket
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
        except Exception:
            ips = []
        for ip in ips:
            parts = ip.split(".")
            if len(parts) == 4 and parts[0] == "100":
                try:
                    if 64 <= int(parts[1]) <= 127:
                        return ip
                except ValueError:
                    pass
        return None

    def _free_port(self, host: str, start: int, span: int = 10):
        """Az első szabad TCP-port `start`-tól felfelé (span-ig), vagy None."""
        import socket
        bind_host = "" if host in ("0.0.0.0", "") else host
        for p in range(start, start + span):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind((bind_host, p))
                return p
            except OSError:
                continue
            finally:
                s.close()
        return None

    def _remote_restart(self):
        """Telefonról kért újraindítás (GUI-szál): leállít, megvárja a leállást, indít."""
        self._log("Újraindítás kérve telefonról…", "warn")
        if self._running:
            self._stop()
        self._wait_then_start()

    def _wait_then_start(self, attempt: int = 0):
        """Megvárja, míg a futó session háttérszála ténylegesen leáll, majd indít."""
        if self._thread is not None and self._thread.is_alive() and attempt < 60:
            QTimer.singleShot(500, lambda: self._wait_then_start(attempt + 1))
            return
        if not self._running:
            self._start()

    # ══════════════════════════════════════════════════════════════════════════
    # Frissítés (GitHub Release alapú önfrissítő)
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_update_button(self, state: str, info):
        self._update_state = state
        tag = (info or {}).get("tag", "") if info else ""
        cfg = {
            "checking":    ("Keresés…",            "disabled", C_MUTED),
            "idle":        ("Frissítés keresése",  "normal",   C_MUTED),
            "uptodate":    ("✓ Naprakész",          "normal",   C_GREEN),
            "available":   (f"↓ Frissítés: {tag}",  "normal",   C_ACCENT),
            "downloading": ("Letöltés…",           "disabled", C_ACCENT),
        }
        text, st, fg = cfg.get(state, ("Frissítés keresése", "normal", C_MUTED))
        self._update_btn.setText(text)
        self._update_btn.setEnabled(st == "normal")

    def _open_notify_setup(self):
        dlg = NotifyBotDialog(self, persist_env=self._persist_env, log=self._log)
        dlg.exec()

    def _on_update_click(self):
        if self._update_state == "available" and self._update_info:
            self._do_update()
        elif self._update_state not in ("checking", "downloading"):
            self._check_updates(silent=False)

    def _check_updates(self, silent: bool = True):
        if self._checking:
            return
        self._checking = True
        self.sigUpdate.emit("checking", None)

        def _work():
            info = None
            checked = False
            try:
                import updater
                rel = updater.check_latest_release()
                if rel is not None:
                    checked = True
                    if updater.is_newer(APP_VERSION, rel["tag"]):
                        info = rel
            except Exception:
                pass
            # Visszajelzés a fő szálra SZIGNÁLON át — a QTimer.singleShot egy
            # háttérszálról nem sülne el (nincs Qt event loop), így a gomb
            # örökre „Keresés…”-en ragadna.
            self.sigCheckResult.emit(info, checked, silent)

        threading.Thread(target=_work, daemon=True).start()

    def _on_check_result(self, info, checked: bool, silent: bool):
        """A frissítés-ellenőrzés eredménye — a fő szálon fut (sigCheckResult)."""
        self._checking = False
        self._update_info = info
        if info:
            self.sigUpdate.emit("available", info)
            self._log(f"Új verzió elérhető: {info['tag']} — "
                      f"kattints a Frissítés gombra.", "tip")
        elif checked:
            self.sigUpdate.emit("uptodate", None)
            if not silent:
                self._log("A program naprakész.", "muted")
        else:
            self.sigUpdate.emit("idle", None)
            try:
                import updater
                reason = updater.last_error() or "Ismeretlen hiba."
            except Exception:
                reason = "Ismeretlen hiba."
            self._log(f"Frissítés-ellenőrzés sikertelen: {reason}", "error")
            if not silent:
                self._offer_browser_download(
                    "Nem sikerült automatikusan ellenőrizni a frissítést.\n\n"
                    f"{reason}")

    def _amber_icon(self, glyph: str = "?") -> QPixmap:
        """Dashboard-arany (#FDB900) kör-ikon a Qt kék alapikonja helyett."""
        size = 64
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        amber = QColor(C_ACCENT)
        pen = QPen(amber)
        pen.setWidth(4)
        p.setPen(pen)
        p.drawEllipse(5, 5, size - 10, size - 10)
        f = QFont("Segoe UI", 30)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, glyph)
        p.end()
        return pm

    def _styled_dialog(self, title: str, text: str, glyph: str = "?"):
        """QMessageBox a dashboard kinézetével: arany ikon + sárga keretes gombok.

        A hívó adja hozzá a gombokat (addButton) és hívja az exec()-et.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIconPixmap(self._amber_icon(glyph))
        box.setStyleSheet(BTN_OUTLINE)
        return box

    def _do_update(self):
        data = self._update_info
        if not data or not data.get("asset_url"):
            QMessageBox.warning(
                self, "Frissítés",
                "Ehhez a kiadáshoz nincs letölthető frissítőcsomag (update.zip).")
            return

        notes = (data.get("body") or "").strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        msg = f"Új verzió érhető el: {data['tag']}\n"
        if notes:
            msg += f"\n{notes}\n"
        msg += ("\nFrissíted most? A program letölti az új verziót, bezár, "
                "és automatikusan újraindul.")
        box = self._styled_dialog("Frissítés elérhető", msg)
        yes = box.addButton("Frissítés most", QMessageBox.YesRole)
        box.addButton("Később", QMessageBox.NoRole)
        box.exec()
        if box.clickedButton() is not yes:
            return

        if self._running:
            self._stop()
        self.sigUpdate.emit("downloading", None)
        self._log("Frissítés letöltése...", "info")

        def _work():
            try:
                import updater
                zip_path = APP_DIR / "update.zip"
                if not updater.download_update(data["asset_url"], zip_path):
                    self.sigUpdateFailed.emit(
                        "A letöltés sikertelen. Próbáld újra később.")
                    return
                if not updater.apply_update_and_restart(zip_path):
                    self.sigUpdateFailed.emit("A frissítő segéd nem indult el.")
                    return
                self.sigQuitForUpdate.emit()
            except Exception as e:
                self.sigUpdateFailed.emit(str(e))

        threading.Thread(target=_work, daemon=True).start()

    def _update_failed(self, reason: str):
        self._log(f"Frissítés sikertelen: {reason}", "error")
        self._offer_browser_download(
            f"A frissítés nem sikerült.\n\n{reason}")
        self.sigUpdate.emit("available", self._update_info)

    def _offer_browser_download(self, message: str):
        """
        Hiba esetén felajánlja a böngészős letöltést: a GitHub kiadás-oldalát
        nyitja meg (ez a böngészőben akkor is működik, ha a Python — vírusirtó
        vagy proxy miatt — nem éri el az API-t). A felhasználó onnan letölti az
        update.zip-et, és kézzel telepíti (kicsomagolás a program mappájába).
        """
        try:
            import updater
            url = updater.RELEASE_PAGE_URL
        except Exception:
            url = ("https://github.com/Jhanee01/betplacer-standalone/"
                   "releases/latest")
        box = self._styled_dialog(
            "Frissítés", message + "\n\nMegnyitod a letöltést böngészőben?",
            glyph="!")
        yes = box.addButton("Letöltés böngészőből", QMessageBox.AcceptRole)
        box.addButton("Mégse", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            try:
                webbrowser.open(url)
                self._log("Letöltési oldal megnyitva a böngészőben.", "tip")
            except Exception:
                self._log(f"Nyisd meg kézzel: {url}", "muted")

    def _quit_for_update(self):
        self._log("Bezárás a frissítéshez — a program mindjárt újraindul.", "muted")
        self._restore_stdout()
        self.close()

    # ── Bezárás ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._running:
            self._stop()
        self._restore_stdout()
        super().closeEvent(event)


class BetPlacerGUI:
    """Belépési burkoló — main.py: BetPlacerGUI().run()."""

    def __init__(self):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setApplicationName("BetPlacer")
        self._apply_theme()
        self._win = BetPlacerWindow()

    def _apply_theme(self):
        apply_app_theme(self._app)

    def run(self):
        self._win.show()
        self._app.exec()
