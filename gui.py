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
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QColor, QTextCursor, QPixmap, QImage, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QFrame, QTabWidget, QTextEdit, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)

from config import APP_VERSION
from paths import APP_DIR
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


def apply_app_theme(app):
    """qt_material (theme_fdb900.xml) + style.qss overlay + Segoe UI font.
    A főablak ÉS a beállítási varázsló is ezt hívja → azonos kinézet."""
    theme = ASSETS / "theme_fdb900.xml"
    qss   = ASSETS / "style.qss"
    try:
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme=str(theme),
                         css_file=str(qss) if qss.exists() else None)
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


class BetPlacerWindow(QMainWindow):
    # ── Háttérszálból a fő szálra: minden UI-frissítés ezeken át ────────────────
    sigLog     = Signal(str, str)          # msg, kind
    sigFoot    = Signal(str)               # státuszsor szöveg
    sigConsole = Signal(str)               # nyers konzol szöveg
    sigStatus  = Signal(str, object, str, str)  # key, tip, status, detail
    sigRunning = Signal(bool)              # futási állapot változott
    sigUpdate  = Signal(str, object)       # frissítés-gomb állapot, info

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BetPlacer")
        self.resize(720, 760)
        self.setMinimumSize(560, 560)
        _icon = ASSETS / "icon.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))

        self._running  = False
        self._loop     = None
        self._thread   = None
        self._stop_evt = threading.Event()
        self._tip_rows = {}      # dedup-kulcs → tippek táblázat sor-index

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

        self._build_ui()
        self._install_stdout_redirect()
        self._log("BetPlacer kész. Kattints az Indítás gombra.", "muted")
        QTimer.singleShot(4000, lambda: self._check_updates(silent=True))

    # ══════════════════════════════════════════════════════════════════════════
    # UI felépítés
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(8)

        # ── Fejléc ──────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(_make_logo_label(30))
        title = QLabel("BetPlacer")
        title.setStyleSheet("color:#ffffff; font-size:18px; font-weight:600;")
        hdr.addWidget(title)
        hdr.addStretch(1)

        self._update_btn = QPushButton("Frissítés keresése")
        self._update_btn.setStyleSheet(BTN_OUTLINE)
        self._update_btn.setCursor(Qt.PointingHandCursor)
        self._update_btn.clicked.connect(self._on_update_click)
        hdr.addWidget(self._update_btn)

        self._ver_lbl = QLabel(f"v{APP_VERSION}")
        self._ver_lbl.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        hdr.addWidget(self._ver_lbl)

        self._status_lbl = QLabel("● LEÁLLÍTVA")
        self._status_lbl.setStyleSheet(f"color:{C_FG}; font-weight:600;")
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)

        root.addWidget(self._hline())

        # ── Info sor: csatorna · tét · indítás ───────────────────────────────
        info = QHBoxLayout()
        channel = os.getenv("TELEGRAM_CHANNEL", "")
        dry_run = os.getenv("BET_DRY_RUN", "0") == "1"

        ch_cap = QLabel("Csatorna:")
        ch_cap.setStyleSheet(f"color:{C_FG}; font-size:14px;")
        info.addWidget(ch_cap)
        self._channel_entry = QLineEdit(channel)
        self._channel_entry.setFixedWidth(160)
        self._channel_entry.setToolTip(
            "Telegram csatorna chat ID (pl. -1003404037430) vagy @csatornanév.\n"
            "Indításkor mentődik a .env-be; futás közben zárolt.")
        info.addWidget(self._channel_entry)

        info.addSpacing(12)
        stake_lbl = QLabel("Alap tét:")
        stake_lbl.setStyleSheet(f"color:{C_FG}; font-size:14px;")
        info.addWidget(stake_lbl)

        self._stake_entry = QLineEdit(os.getenv("BET_STAKE", "500"))
        self._stake_entry.setFixedWidth(80)
        self._stake_entry.setAlignment(Qt.AlignRight)
        info.addWidget(self._stake_entry)
        ft_lbl = QLabel("Ft")
        ft_lbl.setStyleSheet(f"color:{C_FG}; font-size:14px;")
        info.addWidget(ft_lbl)

        if dry_run:
            dr = QLabel("  [DRY RUN]")
            dr.setStyleSheet(f"color:{C_ACCENT}; font-weight:600; font-size:14px;")
            info.addWidget(dr)

        info.addStretch(1)
        self._btn = QPushButton("▶  Indítás")
        self._btn.setStyleSheet(BTN_START)
        self._btn.setFont(QFont("Segoe UI Semibold", 11))
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._toggle)
        info.addWidget(self._btn)
        root.addLayout(info)

        # ── Mai tippek táblázat ──────────────────────────────────────────────
        tips_hdr = QLabel("Mai tippek")
        tips_hdr.setStyleSheet(f"color:{C_FG}; font-weight:600; font-size:14px; margin-top:4px;")
        root.addWidget(tips_hdr)

        self._tree = QTableWidget(0, 4)
        self._tree.setHorizontalHeaderLabels(["Idő", "Meccs", "Pick", "Állapot"])
        self._tree.verticalHeader().setVisible(False)
        self._tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.NoSelection)
        self._tree.setFocusPolicy(Qt.NoFocus)
        hh = self._tree.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._tree.setMaximumHeight(190)
        root.addWidget(self._tree)

        # ── Fülek: Napló · Konzol · Tétek ────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #3a3a3a; border-radius:8px; top:-1px;}")
        # Napló (színes)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet("QTextEdit{" + PANEL_QSS + "}")
        self._tabs.addTab(self._log_view, "Napló")

        # Konzol (nyers stdout/stderr)
        self._raw_view = QPlainTextEdit()
        self._raw_view.setReadOnly(True)
        self._raw_view.setMaximumBlockCount(4000)
        self._raw_view.setStyleSheet("QPlainTextEdit{" + PANEL_QSS + "}")
        self._tabs.addTab(self._raw_view, "Konzol (részletes)")

        # Tétek (stratégiánként)
        self._tabs.addTab(self._build_stake_tab(), "Tétek (stratégiánként)")
        root.addWidget(self._tabs, 1)

        # ── Státuszsor ──────────────────────────────────────────────────────
        self._foot_lbl = QLabel("")
        self._foot_lbl.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        root.addWidget(self._foot_lbl)

    def _hline(self):
        ln = QFrame()
        ln.setFrameShape(QFrame.HLine)
        ln.setStyleSheet("color:#2a2d3a; background:#2a2d3a; max-height:1px;")
        return ln

    def _build_stake_tab(self):
        """Stratégia → tét táblázat. A globális Alap tét a fallback."""
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(2, 6, 2, 2)

        hint = QLabel("Stratégiánkénti tét (Ft). Ami nincs itt felsorolva, "
                      "az az Alap tétet kapja. Indításkor mentődik.")
        hint.setStyleSheet(f"color:{C_FG}; font-size:12px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._stake_table = QTableWidget(0, 2)
        self._stake_table.setHorizontalHeaderLabels(["Stratégia", "Tét (Ft)"])
        self._stake_table.verticalHeader().setVisible(False)
        sh = self._stake_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.Stretch)
        sh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        lay.addWidget(self._stake_table)

        row = QHBoxLayout()
        add_btn = QPushButton("+ Stratégia")
        add_btn.setStyleSheet(BTN_OUTLINE)
        add_btn.clicked.connect(lambda: self._add_stake_row("", ""))
        row.addWidget(add_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._load_stake_table()
        return wrap

    # ── Stratégiánkénti tét táblázat kezelése ──────────────────────────────────

    def _load_stake_table(self):
        """Mentett + ismert stratégiák betöltése a táblázatba."""
        saved = stake_store.load_stakes()
        default = os.getenv("BET_STAKE", "500")
        names = list(dict.fromkeys(list(saved.keys()) + stake_store.KNOWN_STRATEGIES))
        self._stake_table.setRowCount(0)
        for name in names:
            self._add_stake_row(name, str(saved.get(name, "")), placeholder=default)

    def _add_stake_row(self, name: str, stake: str, placeholder: str = ""):
        r = self._stake_table.rowCount()
        self._stake_table.insertRow(r)
        name_item = QTableWidgetItem(name)
        self._stake_table.setItem(r, 0, name_item)
        stake_item = QTableWidgetItem(stake)
        if not stake and placeholder:
            stake_item.setToolTip(f"Üres = alap tét ({placeholder} Ft)")
        self._stake_table.setItem(r, 1, stake_item)

    def _collect_stake_table(self) -> dict:
        out = {}
        for r in range(self._stake_table.rowCount()):
            n_item = self._stake_table.item(r, 0)
            s_item = self._stake_table.item(r, 1)
            name = (n_item.text().strip() if n_item else "")
            sval = (s_item.text().strip() if s_item else "")
            if not name or not sval:
                continue
            try:
                iv = int(float(sval))
                if iv > 0:
                    out[name] = iv
            except (ValueError, TypeError):
                pass
        return out

    def _ensure_strategy_row(self, strategy: str):
        """Futás közben felbukkanó új stratégiához sor (láthatóság, alap tét)."""
        if not strategy:
            return
        for r in range(self._stake_table.rowCount()):
            it = self._stake_table.item(r, 0)
            if it and it.text().strip() == strategy:
                return
        self._add_stake_row(strategy, "", placeholder=os.getenv("BET_STAKE", "500"))

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
        color = TAG_COLORS.get(kind, C_FG)
        ts = datetime.now().strftime("%H:%M:%S")
        body = html.escape(msg).replace("\n", "<br>&nbsp;&nbsp;")
        cur = self._log_view.textCursor()
        cur.movePosition(QTextCursor.End)
        self._log_view.setTextCursor(cur)
        self._log_view.insertHtml(
            f'<span style="color:{C_MUTED}">[{ts}]</span> '
            f'<span style="color:{color}">{body}</span><br>')
        self._log_view.ensureCursorVisible()

    def _append_console(self, s: str):
        cur = self._raw_view.textCursor()
        cur.movePosition(QTextCursor.End)
        self._raw_view.setTextCursor(cur)
        self._raw_view.insertPlainText(s)
        self._raw_view.ensureCursorVisible()

    def _foot_lbl_set(self, text: str):
        self._foot_lbl.setText(text)

    def _apply_status(self, key: str, tip, status: str, detail: str):
        self._ensure_strategy_row(getattr(tip, "strategy_key", "") or getattr(tip, "strategy", ""))
        label, color = STATUS_LABELS.get(status, (status, C_ACCENT))
        if status == "retry" and detail:
            label = f"● esemény vár {detail}"
        elif status == "fail" and detail:
            label = f"✗ sikertelen ({detail})"
        meccs = f"{tip.home_clean}–{tip.away_clean}"
        values = [tip.time, meccs, tip.pick_str, label]
        colors = [None, None, None, color]

        r = self._tip_rows.get(key)
        if r is None or r >= self._tree.rowCount():
            r = self._tree.rowCount()
            self._tree.insertRow(r)
            self._tip_rows[key] = r
        for c, val in enumerate(values):
            item = QTableWidgetItem(str(val))
            if colors[c]:
                item.setForeground(QColor(colors[c]))
            self._tree.setItem(r, c, item)
        self._tree.scrollToBottom()

    def _apply_running(self, running: bool):
        if running:
            self._status_lbl.setText("● FIGYELÉS AKTÍV")
            self._status_lbl.setStyleSheet(f"color:{C_GREEN}; font-weight:600;")
            self._btn.setText("■  Leállítás")
            self._btn.setStyleSheet(BTN_STOP)
            self._stake_entry.setEnabled(False)
            self._channel_entry.setEnabled(False)
            self._stake_table.setEnabled(False)
        else:
            self._status_lbl.setText("● LEÁLLÍTVA")
            self._status_lbl.setStyleSheet(f"color:{C_FG}; font-weight:600;")
            self._btn.setText("▶  Indítás")
            self._btn.setStyleSheet(BTN_START)
            self._stake_entry.setEnabled(True)
            self._channel_entry.setEnabled(True)
            self._stake_table.setEnabled(True)

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
        raw = self._stake_entry.text().strip()
        try:
            stake = int(float(raw))
            if stake <= 0:
                raise ValueError
        except (ValueError, TypeError):
            QMessageBox.critical(self, "Hibás tét",
                                 "Az alap tét csak pozitív egész szám lehet (Ft).")
            return
        channel = self._channel_entry.text().strip()
        if not channel:
            QMessageBox.critical(
                self, "Hiányzó csatorna",
                "Add meg a Telegram csatorna chat ID-t (pl. -1003404037430) "
                "vagy a @csatornanevet.")
            return

        self._stake_entry.setText(str(stake))
        os.environ["BET_STAKE"] = str(stake)
        self._persist_env("BET_STAKE", str(stake))

        # Csatorna mentése + érvényesítése erre a futásra (a core .env-ből olvas).
        os.environ["TELEGRAM_CHANNEL"] = channel
        self._persist_env("TELEGRAM_CHANNEL", channel)
        self._log(f"Csatorna: {channel}", "muted")

        # Stratégiánkénti tétek mentése (a core induláskor olvassa be).
        stakes = self._collect_stake_table()
        stake_store.save_stakes(stakes)
        if stakes:
            self._log("Stratégiánkénti tét mentve: " + ", ".join(
                f"{k}={v}" for k, v in stakes.items()), "muted")
        self._log(f"Alap tét: {stake} Ft", "muted")

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

            def _done():
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
                    if not silent:
                        QMessageBox.warning(
                            self, "Frissítés",
                            "Nem sikerült ellenőrizni a frissítést.\n"
                            "Ellenőrizd az internetkapcsolatot, és próbáld újra.")
            QTimer.singleShot(0, _done)

        threading.Thread(target=_work, daemon=True).start()

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
        if QMessageBox.question(self, "Frissítés elérhető", msg) != QMessageBox.Yes:
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
                    QTimer.singleShot(0, lambda: self._update_failed(
                        "A letöltés sikertelen. Próbáld újra később."))
                    return
                if not updater.apply_update_and_restart(zip_path):
                    QTimer.singleShot(0, lambda: self._update_failed(
                        "A frissítő segéd nem indult el."))
                    return
                QTimer.singleShot(0, self._quit_for_update)
            except Exception as e:
                QTimer.singleShot(0, lambda: self._update_failed(str(e)))

        threading.Thread(target=_work, daemon=True).start()

    def _update_failed(self, reason: str):
        self._log(f"Frissítés sikertelen: {reason}", "error")
        QMessageBox.critical(self, "Frissítés", reason)
        self.sigUpdate.emit("available", self._update_info)

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
