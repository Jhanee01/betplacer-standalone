"""A standalone BetPlacer főnézete — a FIFA Tipster dashboard BetPlacer oldalának
megfelelője (ugyanaz a kinézet és működés). A különbség: a tippek a Telegram-
csatornákból jönnek, ezért két Telegram-állapot van (a csatorna-figyelés a saját
fiókkal + az értesítő bot), és a fejlécben ott a Beállítások és a Frissítés gomb.

Adat: dashboard_state (a futtató mag írja), a tét: dashboard_state.load_stakes().
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF, QSize
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QFont, QTextCursor,
                           QIcon, QPixmap)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
    QSpinBox, QPlainTextEdit, QGridLayout, QSizePolicy,
)

import dashboard_state as ds
from dashboard_state import load_status, load_bets, summary, ago, profit_of

GOLD, GOOD, BAD, WARN = "#FDB900", "#43c98a", "#ef5a5a", "#ff9a3d"
MUTED, FAINT, FG = "#8d8d8d", "#5f5f5f", "#e8e8e8"

OFFICES = [("tippmixpro", "TippmixPro"), ("vegas", "Vegas")]

_STYLE = f"""
QFrame#bpVLine {{ background: #4f5b62; max-width: 3px; min-width: 3px; border: none; }}
QLabel {{ background: transparent; color: {FG}; }}
QLabel[role="label"] {{ color: #ffffff; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QLabel[role="muted"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="eyebrow"] {{ color: {GOLD}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#bpTitle {{ font-size: 26px; font-weight: 700; }}
QFrame#bpCard {{ background: #1e1e1e; border: 2px solid #4f5b62; border-radius: 10px; }}
QFrame#bpStake {{ background: rgba(253,185,0,0.10); border: 1px solid rgba(253,185,0,0.35); border-radius: 10px; }}
QPushButton#bpOffice {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
    text-align: left; padding: 8px 10px; }}
QPushButton#bpOffice:hover {{ background: rgba(255,255,255,0.04); }}
QPushButton#bpOffice[active="true"] {{ background: rgba(253,185,0,0.12); border: 1px solid rgba(253,185,0,0.35); }}
QPushButton#bpBtn {{ background: #232323; border: 1px solid #3a3a3a; border-radius: 7px;
    color: {FG}; padding: 6px 12px; font-size: 12px; font-weight: 700; }}
QPushButton#bpBtn:hover {{ border-color: #5a5a5a; }}
QPushButton#bpGold {{ background: rgba(253,185,0,0.12); border: 1px solid rgba(253,185,0,0.45);
    border-radius: 7px; color: {GOLD}; padding: 6px 12px; font-size: 12px; font-weight: 700; }}
QPushButton#bpGold:hover {{ border-color: {GOLD}; }}
QPushButton#bpSeg {{ background: transparent; border: 1px solid #3a3a3a; color: {MUTED};
    padding: 4px 10px; font-size: 11px; font-weight: 700; }}
QPushButton#bpSeg:checked {{ background: {GOLD}; color: #111; border-color: {GOLD}; }}
QSpinBox {{ background: #111; border: 1px solid #3a3a3a; border-radius: 6px; color: {FG};
    padding: 4px 6px; font-size: 14px; font-weight: 700; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 8px; background: #1a1a1a; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background: #FDB900; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def ft(v: float | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.0f}".replace(",", " ")
    if sign:
        return ("+" if v > 0 else "−" if v < 0 else "") + s + " Ft"
    return ("−" if v < 0 else "") + s + " Ft"


def _lbl(text: str = "", role: str | None = None, size: int | None = None,
         weight: int | None = None, color: str | None = None) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    css = []
    if size:
        css.append(f"font-size: {size}px;")
    if weight:
        css.append(f"font-weight: {weight};")
    if color:
        css.append(f"color: {color};")
    if css:
        lab.setStyleSheet(" ".join(css))
    return lab


MASK = "*****"


def _eye_icon(hidden: bool) -> QIcon:
    """Szem ikon QPainterrel (a betűtípus-glyphek nem mindenhol jelennek meg);
    elrejtett állapotban áthúzva."""
    pix = QPixmap(40, 28)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(MUTED if hidden else GOLD)
    p.setPen(QPen(col, 2.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    eye = QPainterPath(QPointF(3, 14))
    eye.quadTo(QPointF(20, -3), QPointF(37, 14))
    eye.quadTo(QPointF(20, 31), QPointF(3, 14))
    p.drawPath(eye)
    p.setBrush(col)
    p.drawEllipse(QPointF(20, 14), 5, 5)
    if hidden:
        p.setPen(QPen(col, 3))
        p.drawLine(QPointF(7, 25), QPointF(33, 3))
    p.end()
    return QIcon(pix)


def _card(name: str = "bpCard") -> QFrame:
    f = QFrame()
    f.setObjectName(name)
    return f


def _vline() -> QFrame:
    f = QFrame()
    f.setObjectName("bpVLine")
    return f


class _Chart(QWidget):
    """Profit Battle-naponként (oszlop) vagy halmozva (vonal) — zöld plusz, piros mínusz."""

    def __init__(self):
        super().__init__()
        self.days: list[tuple[str, float, int]] = []
        self.cumulative = False
        self.setMinimumHeight(150)

    def set_data(self, days, cumulative: bool):
        self.days, self.cumulative = days, cumulative
        self.update()

    @staticmethod
    def _step(span: float) -> float:
        raw = max(span, 4000) / 4      # legalább 1000 Ft-os rács (üres napon se legyen „+0e”)
        mag = 10 ** len(str(int(raw))) / 10
        for m in (1, 2, 5, 10):
            if raw <= m * mag:
                return m * mag
        return 10 * mag

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        L, R, T, B = 58, 10, 14, 26
        if not self.days:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Még nincs elszámolt fogadás a BetPlacer indulása óta")
            return
        vals = [d[1] for d in self.days]
        if self.cumulative:
            acc, out = 0.0, []
            for v in vals:
                acc += v
                out.append(acc)
            vals = out
        lo, hi = min(0.0, *vals), max(0.0, *vals)
        step = self._step(hi - lo)
        y_min = (lo // step) * step
        y_max = -((-hi) // step) * step
        if y_max == y_min:
            y_max = y_min + step

        def y(v):
            return T + (y_max - v) / (y_max - y_min) * (H - T - B)

        small = QFont(self.font())
        small.setPointSizeF(8)
        p.setFont(small)
        v = y_min
        while v <= y_max + 1e-6:
            p.setPen(QPen(QColor("#4f5b62"), 2.5 if v == 0 else 1.5))   # mint a blokk-elválasztók
            p.drawLine(QPointF(L, y(v)), QPointF(W - R, y(v)))
            p.setPen(QColor("#7a7a7a"))
            p.drawText(QRectF(0, y(v) - 8, L - 8, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v / 1000:+.0f}e" if v else "0")
            v += step

        n = len(vals)
        slot = (W - L - R) / n
        today = datetime.now().date().isoformat()
        for i, (day, _, cnt) in enumerate(self.days):
            p.setPen(QColor("#7a7a7a"))
            p.drawText(QRectF(L + i * slot, H - B + 6, slot, 16), Qt.AlignmentFlag.AlignCenter,
                       day[5:] + (" (ma)" if day == today else ""))

        if not self.cumulative:
            bw = slot * 0.56
            for i, val in enumerate(vals):
                col = QColor(GOOD if val >= 0 else BAD)
                x = L + i * slot + (slot - bw) / 2
                top, bot = min(y(0), y(val)), max(y(0), y(val))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(QRectF(x, top, bw, max(1.0, bot - top)), 3, 3)
                p.setPen(QColor("#cfcfcf"))
                ty = top - 16 if val >= 0 else bot + 2
                p.drawText(QRectF(x - 20, ty, bw + 40, 14), Qt.AlignmentFlag.AlignCenter,
                           f"{val / 1000:+.1f}".replace(".", ","))
        else:
            col = QColor(GOOD if vals[-1] >= 0 else BAD)
            pts = [QPointF(L + i * slot + slot / 2, y(v)) for i, v in enumerate(vals)]
            area = QPainterPath(QPointF(pts[0].x(), y(0)))
            for pt in pts:
                area.lineTo(pt)
            area.lineTo(QPointF(pts[-1].x(), y(0)))
            fill = QColor(col)
            fill.setAlpha(26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            p.setPen(QPen(col, 2.2))
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
            for i, pt in enumerate(pts):
                c = QColor(GOOD if vals[i] >= 0 else BAD)
                p.setPen(QPen(c, 2))
                p.setBrush(c if i == n - 1 else QColor("#232629"))
                r = 5 if i == n - 1 else 3
                p.drawEllipse(pt, r, r)
            p.setPen(col)
            p.drawText(QRectF(pts[-1].x() - 140, pts[-1].y() - 24, 132, 16),
                       Qt.AlignmentFlag.AlignRight, ft(vals[-1], sign=True))


class _Spark(QWidget):
    """Egyenleg-görbe a jobb oldali panelen."""

    def __init__(self):
        super().__init__()
        self.values: list[float] = []
        self.setFixedHeight(44)

    def set_values(self, values):
        self.values = values
        self.update()

    def paintEvent(self, _):
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1
        pts = [QPointF(3 + i / (len(self.values) - 1) * (W - 6), 4 + (hi - v) / span * (H - 8))
               for i, v in enumerate(self.values)]
        col = QColor(GOOD if self.values[-1] >= self.values[0] else BAD)
        fill = QColor(col)
        fill.setAlpha(26)
        area = QPainterPath(QPointF(pts[0].x(), H))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(pts[-1].x(), H))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(area)
        p.setPen(QPen(col, 1.8))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setBrush(col)
        p.drawEllipse(pts[-1], 3, 3)


_STATE = {   # outcome/result → (felirat, szín)
    "win":     ("Nyert", GOOD),
    "loss":    ("Vesztett", BAD),
    "half_win":  ("Félig nyert", GOOD),
    "half_loss": ("Félig vesztett", BAD),
    "void":    ("Érvénytelen", MUTED),
    "pending": ("Megrakva, elszámolásra vár", GOLD),
    "skipped": ("Kihagyva", WARN),
    "failed":  ("Sikertelen megrakás", BAD),
    "retry":   ("Sikertelen, újrapróbálja", WARN),
}


class MainView(QWidget):
    toggle_requested = Signal(str)
    settings_requested = Signal()
    update_requested = Signal()

    def __init__(self, version: str = "", parent=None):
        super().__init__(parent)
        self._version = version
        # a többi nézettel azonos háttér (qt-material téma), saját szín nincs
        self.setObjectName("contentView")
        self.setStyleSheet(_STYLE)
        self._office = "tippmixpro"
        self._running = False
        self._cumulative = False
        self._bets_key = None
        self._hidden = {"name": False, "balance": False, "bank": False}   # szem-gombok

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_center(), 1)
        root.addWidget(_vline())
        root.addWidget(self._build_right())
        self._build_console()
        self._set_cumulative(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(5000)
        self.refresh()

    # ── felépítés ────────────────────────────────────────────────────
    def _build_center(self) -> QWidget:
        # A középső oszlop nem görget: fix elrendezés, csak a fogadáslista görget belül.
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(26, 20, 26, 20)
        lay.setSpacing(16)

        # irodaváltó fülek (a Konzol nézet fül-stílusában)
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self._office_btns: dict[str, QPushButton] = {}
        for key, name in OFFICES:
            btn = QPushButton(name.upper())
            btn.setFixedHeight(40)
            btn.setMinimumWidth(150)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._select(k))
            tabs.addWidget(btn)
            self._office_btns[key] = btn
        # Real Event: később érkezik — addig szürke, nem kattintható fül
        real = QPushButton("REAL EVENT")
        real.setFixedHeight(40)
        real.setMinimumWidth(150)
        real.setEnabled(False)
        real.setToolTip("Hamarosan elérhető")
        real.setStyleSheet("QPushButton{background:transparent;color:#5f5f5f;border:2px solid #4f5b62;"
                           "border-radius:6px;font-size:15px;font-weight:700;padding:0 18px;}")
        tabs.addWidget(real)
        tabs.addStretch()
        self.settings_btn = QPushButton("Beállítások")
        self.settings_btn.setObjectName("bpGold")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip("Irodák be/ki, belépési adatok, értesítő bot")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.update_btn = QPushButton("Frissítés keresése")
        self.update_btn.setObjectName("bpGold")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.update_requested.emit)
        tabs.addWidget(self.settings_btn)
        tabs.addWidget(self.update_btn)
        tabs.addWidget(_lbl(f"v{self._version}", size=12, color=MUTED))
        lay.addLayout(tabs)

        # fejléc: fiók + jobbra az állapot, Indítás/Leállítás, Konzol
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        self._eyebrow = _lbl("", role="eyebrow")
        self._title = QLabel("—")
        self._title.setObjectName("bpTitle")
        self._meta = _lbl("", role="muted")
        self._meta.setWordWrap(True)
        left.addWidget(self._eyebrow)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self._title)
        title_row.addWidget(self._eye_btn("name"), 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch()
        left.addLayout(title_row)
        left.addWidget(self._meta)
        head.addLayout(left, 1)
        self._pill = _lbl("", size=11, weight=700)
        self._toggle_btn = QPushButton("Indítás")
        self._toggle_btn.setObjectName("bpGold")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(lambda: self.toggle_requested.emit("bet_placer"))
        con_btn = QPushButton("Konzol")
        con_btn.setObjectName("bpGold")      # látható konzolnál arany, elrejtve szürke
        con_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        con_btn.clicked.connect(self._toggle_console)
        self._con_btn = con_btn
        self._tg_pill = _lbl("", size=11, weight=700)
        self._bot_pill = _lbl("", size=11, weight=700)
        for wdg in (self._tg_pill, self._bot_pill, self._pill, self._toggle_btn, con_btn):
            head.addWidget(wdg, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # mutatók
        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self._kpi: dict[str, tuple] = {}
        for key, title in (("bets", "FOGADÁS"), ("wl", "NYERT – VESZTETT"),
                           ("staked", "TÉT ÖSSZESEN"), ("profit", "PROFIT"), ("roi", "ROI")):
            c = _card()
            v = QVBoxLayout(c)
            v.setContentsMargins(14, 12, 14, 12)
            v.setSpacing(4)
            t = _lbl(title, role="label")
            val = _lbl("—", size=20, weight=700)
            sub = _lbl("", size=11, color=MUTED)
            v.addWidget(t)
            v.addWidget(val)
            v.addWidget(sub)
            kpis.addWidget(c, 1)
            self._kpi[key] = (val, sub)
        lay.addLayout(kpis)

        # diagram
        chart_card = _card()
        cv = QVBoxLayout(chart_card)
        cv.setContentsMargins(18, 14, 18, 12)
        ch = QHBoxLayout()
        self._chart_title = _lbl("", role="label")
        ch.addWidget(self._chart_title)
        ch.addStretch()
        self._seg_daily = QPushButton("Napi")
        self._seg_cum = QPushButton("Halmozott")
        for b, cum in ((self._seg_daily, False), (self._seg_cum, True)):
            b.setObjectName("bpSeg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cum: self._set_cumulative(c))
            ch.addWidget(b)
        cv.addLayout(ch)
        self._chart = _Chart()
        cv.addWidget(self._chart, 1)
        lay.addWidget(chart_card, 3)

        # fogadások — a lista a maradék helyen, belül görget
        lay.addWidget(_lbl("FOGADÁSOK (BETPLACER INDULÁSA ÓTA)", size=15, weight=700))
        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_body = QWidget()
        list_body.setStyleSheet("background: transparent;")
        self._bets_box = QVBoxLayout(list_body)
        self._bets_box.setContentsMargins(0, 0, 6, 0)
        self._bets_box.setSpacing(8)
        self._bets_box.addStretch()
        list_scroll.setWidget(list_body)
        lay.addWidget(list_scroll, 2)
        return body

    def _build_right(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(264)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 20, 18, 14)
        lay.setSpacing(10)

        lay.addWidget(_lbl("EGYENLEG", role="label"))
        bal_card = _card()
        bv = QVBoxLayout(bal_card)
        bv.setContentsMargins(14, 12, 14, 12)
        bv.setSpacing(3)
        self._balance = _lbl("—", size=22, weight=700)
        self._balance_sub = _lbl("", size=11, color=MUTED)
        self._balance_sub.setWordWrap(True)
        self._spark = _Spark()
        bal_row = QHBoxLayout()
        bal_row.addWidget(self._balance)
        bal_row.addStretch()
        bal_row.addWidget(self._eye_btn("balance"), 0, Qt.AlignmentFlag.AlignVCenter)
        bv.addLayout(bal_row)
        bv.addWidget(self._balance_sub)
        bv.addWidget(self._spark)
        lay.addWidget(bal_card)
        self._open_row = self._row(lay, "Nyitott fogadások tétje")
        self._bank_row = self._row(lay, "Bankroll", eye="bank")

        lay.addSpacing(8)
        lay.addWidget(_lbl("TÉT", role="label"))
        sc = _card("bpStake")
        sv = QVBoxLayout(sc)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(8)
        srow = QHBoxLayout()
        srow.addWidget(_lbl("Jelenlegi tét", size=12, weight=700, color=MUTED))
        srow.addStretch()
        self._stake_lbl = _lbl("—", size=20, weight=700, color=GOLD)
        srow.addWidget(self._stake_lbl)
        sv.addLayout(srow)
        erow = QHBoxLayout()
        self._stake_spin = QSpinBox()
        self._stake_spin.setRange(100, 1_000_000)
        self._stake_spin.setSingleStep(100)
        self._stake_spin.setSuffix(" Ft")
        self._stake_spin.setGroupSeparatorShown(True)
        save = QPushButton("Mentés")
        save.setObjectName("bpGold")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(lambda: self._save_stake(self._stake_spin.value()))
        erow.addWidget(self._stake_spin, 1)
        erow.addWidget(save)
        sv.addLayout(erow)
        self._suggest = _lbl("", size=16, color=FG)
        self._suggest.setWordWrap(True)
        self._suggest_btn = QPushButton("Átállítom")
        self._suggest_btn.setObjectName("bpBtn")
        self._suggest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._suggest_btn.clicked.connect(lambda: self._save_stake(self._suggested))
        sv.addWidget(self._suggest)
        sv.addWidget(self._suggest_btn)
        self._suggested = 0
        lay.addWidget(sc)

        lay.addStretch()
        return w

    def _row(self, lay, title: str, eye: str | None = None) -> QLabel:
        c = _card()
        h = QHBoxLayout(c)
        h.setContentsMargins(12, 9, 12, 9)
        h.addWidget(_lbl(title, size=12, color=MUTED))
        h.addStretch()
        val = _lbl("—", size=12, weight=700)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(val)
        if eye:
            h.addWidget(self._eye_btn(eye))
        lay.addWidget(c)
        return val

    def _eye_btn(self, key: str) -> QPushButton:
        """Szem-gomb: elrejti / megmutatja az adatot (fióknév, egyenleg, bankroll)."""
        btn = QPushButton()
        btn.setFixedSize(26, 22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("Elrejtés / megjelenítés")
        btn.setStyleSheet("QPushButton{background:transparent;border:none;padding:0;}")
        btn.setIconSize(QSize(20, 14))
        btn.setIcon(_eye_icon(False))
        btn.clicked.connect(lambda _=False, k=key, b=btn: self._toggle_hidden(k, b))
        return btn

    def _toggle_hidden(self, key: str, btn: QPushButton):
        self._hidden[key] = not self._hidden[key]
        btn.setIcon(_eye_icon(self._hidden[key]))
        self.refresh()

    def _build_console(self):
        self._console = QFrame(self)
        self._console.setStyleSheet(
            "QFrame#bpConsole{background:#0a0a0a;border:2px solid #4f5b62;border-radius:10px;}")
        self._console.setObjectName("bpConsole")
        v = QVBoxLayout(self._console)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 7, 8, 7)
        hdr.addWidget(_lbl("BETPLACER — ÉLŐ NAPLÓ", size=13, weight=700, color=GOLD))
        hdr.addStretch()
        mn = QPushButton("–")
        mn.setFixedSize(22, 20)
        mn.setStyleSheet(f"QPushButton{{background:transparent;border:none;color:{MUTED};font-size:16px;}}")
        mn.clicked.connect(self._minimize_console)
        hdr.addWidget(mn)
        v.addLayout(hdr)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setStyleSheet("QPlainTextEdit{background:#0a0a0a;color:#cfcfcf;border:none;"
                                "border-top:2px solid #4f5b62;padding:6px 10px;font-size:11px;}")
        v.addWidget(self._log)
        self._console_min = False
        self._place_console()

    # ── konzol ───────────────────────────────────────────────────────
    def _place_console(self):
        w = min(460, max(300, self.width() - 520))
        h = 34 if self._console_min else 240
        self._console.setGeometry(self.width() - w - 22, self.height() - h - 22, w, h)
        self._log.setVisible(not self._console_min)
        self._console.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_console()

    def _toggle_console(self):
        visible = not self._console.isVisible()
        self._console.setVisible(visible)
        self._console_min = False
        self._place_console()
        self._con_btn.setObjectName("bpGold" if visible else "bpBtn")
        self._con_btn.style().unpolish(self._con_btn)
        self._con_btn.style().polish(self._con_btn)

    def _minimize_console(self):
        self._console_min = not self._console_min
        self._place_console()

    _KIND_COLOR = {"ok": GOOD, "fail": BAD, "error": BAD, "warn": WARN, "tip": GOLD,
                   "muted": "#8d8d8d", "info": "#cfcfcf"}

    def append_log(self, msg: str, kind: str = "info"):
        """A futtató mag naplója (msg, kind) — időbélyeggel, a fajta szerinti színnel."""
        cur = self._log.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QColor(FAINT))
        cur.setCharFormat(fmt)
        cur.insertText(datetime.now().strftime("%H:%M:%S") + " ")
        fmt.setForeground(QColor(self._KIND_COLOR.get(kind, "#cfcfcf")))
        cur.setCharFormat(fmt)
        cur.insertText(msg.rstrip() + "\n")
        self._log.setTextCursor(cur)
        self._log.ensureCursorVisible()

    # ── állapot ──────────────────────────────────────────────────────
    def set_running(self, running: bool):
        self._running = running
        self.refresh()

    def _select(self, key: str):
        self._office = key
        self._bets_key = None
        self.refresh()

    def _set_cumulative(self, cum: bool):
        self._cumulative = cum
        # a qt-material :checked szabálya felülírná a közös stíluslapot → gombonként
        for b, on in ((self._seg_daily, not cum), (self._seg_cum, cum)):
            b.setChecked(on)
            b.setStyleSheet(f"QPushButton{{background:{GOLD};color:#000;border:2px solid {GOLD};"
                            "border-radius:6px;padding:4px 12px;font-size:11px;font-weight:700;}" if on else
                            f"QPushButton{{background:transparent;color:{GOLD};border:2px solid {GOLD};"
                            "border-radius:6px;padding:4px 12px;font-size:11px;font-weight:700;}"
                            "QPushButton:hover{background:rgba(253,185,0,0.15);}")
        self.refresh()

    def _save_stake(self, value: int):
        if value <= 0:
            return
        ds.save_stake(self._office, value)
        self.refresh()

    def refresh(self):
        st = load_status()    # leállítva a legutóbbi futás adatai látszanak
        if st.get("started_at") != ds.session_start():
            # előző munkamenet: a fiók és az egyenleg még informatív, de az „indítás
            # óta” számok a program újraindításával nulláról indulnak
            st = {"offices": {bm: {k: v for k, v in o.items()
                                   if k not in ("start_balance", "history")}
                              for bm, o in (st.get("offices") or {}).items()}}
        offices = st.get("offices", {})
        active = set(st.get("bookmakers") or [])
        stakes = ds.load_stakes()
        bets = load_bets(st.get("started_at")) if st else []

        # irodaváltó fülek
        for key, btn in self._office_btns.items():
            if key == self._office:
                btn.setStyleSheet(f"background:{GOLD};color:#000;border:2px solid {GOLD};"
                                  "border-radius:6px;font-size:15px;font-weight:700;padding:0 18px;")
            else:
                btn.setStyleSheet(f"QPushButton{{background:transparent;color:{GOLD};border:2px solid {GOLD};"
                                  "border-radius:6px;font-size:15px;font-weight:700;padding:0 18px;}"
                                  "QPushButton:hover{background:rgba(253,185,0,0.15);}")
        tg = st.get("telegram", {})

        # fejléc
        name = dict(OFFICES)[self._office]
        o = offices.get(self._office, {})
        self._eyebrow.setText(f"{name.upper()} FIÓK")
        self._title.setText(MASK if self._hidden["name"] and o.get("account")
                            else o.get("account") or "—")
        meta = []
        if st.get("started_at"):
            meta.append(f"Indítás óta: {st['started_at'][:16].replace('T', ' ')}")
        meta.append(f"Egyenleg frissítve: {ago(o.get('balance_at'))}")
        if self._running and self._office not in active:
            meta.append("ezen az irodán most nem rak meg tippet")
        self._meta.setText("   ·   ".join(meta))
        if self._running:
            dry = st.get("dry_run")
            self._pill.setText("TESZT MÓD" if dry else "FUT")
            c = WARN if dry else GOOD
            self._pill.setStyleSheet(f"font-size:12px;font-weight:700;color:{c};border:1px solid {c};"
                                     "border-radius:11px;padding:5px 10px;margin-top:4px;")
            self._toggle_btn.setText("Leállítás")
        else:
            self._pill.setText("ÁLL")
            self._pill.setStyleSheet(f"font-size:12px;font-weight:700;color:{FAINT};border:1px solid {FAINT};"
                                     "border-radius:11px;padding:5px 10px;margin-top:4px;")
            self._toggle_btn.setText("Indítás")

        # mutatók + diagram
        s = summary(bets, self._office)
        self._kpi["bets"][0].setText(str(s["bets"]))
        self._kpi["bets"][1].setText(f"{s['open']} nyitott")
        self._kpi["wl"][0].setText(f"{s['won']} – {s['lost']}")
        n = s["won"] + s["lost"]
        self._kpi["wl"][1].setText(f"{s['won'] / n * 100:.0f}% találat" if n else "még nincs elszámolt")
        self._kpi["staked"][0].setText(ft(s["staked_all"]))
        self._kpi["staked"][1].setText(f"egységnyi tét: {ft(stakes[self._office])}")
        pc = GOOD if s["profit"] >= 0 else BAD
        self._kpi["profit"][0].setText(ft(s["profit"], sign=True))
        self._kpi["profit"][0].setStyleSheet(f"font-size:20px;font-weight:700;color:{pc};")
        today = next((d for d in s["days"] if d[0] == datetime.now().date().isoformat()), None)
        self._kpi["profit"][1].setText(f"ma: {ft(today[1], sign=True)}" if today else "ma: 0 Ft")
        self._kpi["roi"][0].setText(f"{s['roi']:+.1f}%".replace(".", ","))
        self._kpi["roi"][0].setStyleSheet(f"font-size:20px;font-weight:700;color:{pc};")
        self._kpi["roi"][1].setText("indítás óta")
        self._chart_title.setText("HALMOZOTT PROFIT AZ INDÍTÁS ÓTA (FT)" if self._cumulative
                                  else "PROFIT NAPONKÉNT (FT)")
        self._chart.set_data(s["days"], self._cumulative)

        # fogadások listája
        mine = [r for r in bets if r.get("bookmaker") == self._office][:40]
        key = (self._office, tuple((r.get("ts"), r.get("outcome"), r.get("result")) for r in mine))
        if key != self._bets_key:
            self._bets_key = key
            self._render_bets(mine)

        # jobb oldal
        bal = o.get("balance")
        hide_bal = self._hidden["balance"]
        self._balance.setText(f"{MASK} Ft" if hide_bal and bal is not None else ft(bal))
        self._spark.setVisible(not hide_bal)
        if hide_bal and bal is not None:
            self._balance_sub.setText(f"indításkor {MASK} Ft · {MASK} Ft")
        elif bal is not None and o.get("start_balance") is not None:
            diff = bal - o["start_balance"]
            self._balance_sub.setText(f"indításkor {ft(o['start_balance'])} · "
                                      f"<span style='color:{GOOD if diff >= 0 else BAD}'>{ft(diff, sign=True)}</span>")
        else:
            self._balance_sub.setText("a BetPlacer a bejelentkezés után olvassa ki")
        self._spark.set_values([h[1] for h in o.get("history", [])])
        # a számla nyitott fogadásai (a kézzel megrakottak is); amíg a BetPlacer még
        # nem olvasta ki, a saját naplója szerinti nyitott tét
        open_stake = o.get("open_stake", s["open_stake"])
        if "open_count" in o:
            self._open_row.setText(f"{ft(open_stake)} ({o['open_count']} db)")
            self._open_row.setToolTip(f"A számláról olvasva: {ago(o.get('open_at'))}")
        else:
            self._open_row.setText(ft(open_stake))
        bank = (bal + open_stake) if bal is not None else None
        self._bank_row.setText(f"{MASK} Ft" if self._hidden["bank"] and bank is not None else ft(bank))

        stake = stakes[self._office]
        self._stake_lbl.setText(ft(stake))
        if not self._stake_spin.hasFocus():
            self._stake_spin.setValue(stake)
        self._suggested = 0
        if bank:
            two = round(bank * 0.02 / 100) * 100
            diff = two - stake
            if abs(diff) >= 500:
                self._suggested = stake + int(diff / 500) * 500
        self._suggest.setText(f"Javaslat: <b>{ft(self._suggested)}</b>"
                              if self._suggested else "")
        self._suggest.setVisible(bool(self._suggested))
        self._suggest_btn.setVisible(bool(self._suggested))

        # Telegram: a csatorna-figyelés (saját fiók) és az értesítő bot, a FUT/ÁLL előtt
        pill = ("font-size:12px;font-weight:700;color:{c};border:1px solid {c};"
                "border-radius:11px;padding:5px 10px;margin-top:4px;")
        w = (tg.get("watcher") or {}) if self._running else {}
        chans = w.get("channels") or {}
        not_member = [c for c, ok in chans.items() if not ok]
        text, col = {"ok": ("TELEGRAM: FIGYEL", GOOD),
                     "partial": (f"TELEGRAM: NEM TAG — {', '.join(not_member).upper()}", WARN),
                     "connecting": ("TELEGRAM: CSATLAKOZÁS", WARN),
                     "error": ("TELEGRAM: HIBA", BAD)}.get(w.get("state"), ("TELEGRAM: —", FAINT))
        self._tg_pill.setText(text)
        self._tg_pill.setStyleSheet(pill.format(c=col))
        lines = [f"{c}: {'tag, figyeli' if ok else 'NEM TAG — a tippjeit nem látja'}"
                 for c, ok in chans.items()]
        if w.get("error"):
            lines.append(w["error"])
        if not_member:
            lines.append("Lépj be a csatornába a saját Telegram-fiókoddal, majd indítsd újra.")
        self._tg_pill.setToolTip("\n".join(lines) or "A tipp-csatornák figyelése a saját Telegram-fiókkal")
        b = (tg.get("bot") or {}) if self._running else {}
        text, col = {"ok": ("ÉRTESÍTŐ BOT: KAPCSOLÓDVA", GOOD),
                     "missing": ("ÉRTESÍTŐ BOT: NINCS BEÁLLÍTVA", WARN),
                     "error": ("ÉRTESÍTŐ BOT: HIBA", BAD)}.get(b.get("state"), ("ÉRTESÍTŐ BOT: —", FAINT))
        self._bot_pill.setText(text)
        self._bot_pill.setStyleSheet(pill.format(c=col))
        self._bot_pill.setToolTip(b.get("error") or (f"@{b['bot']}" if b.get("bot") else ""))

    def _render_bets(self, rows: list[dict]):
        while self._bets_box.count():
            item = self._bets_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            empty = _lbl("Még nincs megrakási kísérlet ezen az irodán a BetPlacer indulása óta.",
                         role="muted")
            self._bets_box.addWidget(empty)
            self._bets_box.addStretch()
            return
        for r in rows:
            state = r.get("result") if r.get("outcome") == "placed" else r.get("outcome")
            text, col = _STATE.get(state, (state or "?", MUTED))
            card = _card()
            v = QVBoxLayout(card)
            v.setContentsMargins(14, 10, 14, 10)
            v.setSpacing(4)
            top = QHBoxLayout()
            top.setSpacing(10)
            # időpont és állapot a cím ELŐTT/mellett — a jobb szélt a lebegő konzol takarhatja
            top.addWidget(_lbl(str(r.get("ts", ""))[11:16], size=11, color=FAINT))
            top.addWidget(_lbl(r.get("label", ""), size=13, weight=700))
            top.addWidget(_lbl(text, size=11, weight=700, color=col))
            top.addStretch()
            v.addLayout(top)
            detail = f"tét {ft(r.get('stake'))}"
            if r.get("odds"):
                detail += f" · odds {r['odds']}"
            if r.get("outcome") == "placed" and r.get("result") in ("win", "loss", "half_win", "half_loss", "void"):
                detail += f" · {ft(profit_of(r), sign=True)}"
                detail += " · számla szerint"
            if r.get("error"):
                detail += f" · {r['error']}"
            if r.get("dry_run"):
                detail += " · teszt mód"
            d = _lbl(detail, size=11, color=MUTED)
            d.setWordWrap(True)
            v.addWidget(d)
            self._bets_box.addWidget(card)
        self._bets_box.addStretch()
