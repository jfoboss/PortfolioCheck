"""
widgets.py — небольшие виджеты окна: индикатор обновления на панели, бейджи в таблицах.
"""
from __future__ import annotations


from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
)

from PySide6.QtWidgets import QStyledItemDelegate, QTableWidgetItem
from PySide6.QtGui import QPainter, QPen, QBrush


class BusySpinner(QWidget):
    """Маленький индикатор «идёт обновление» в углу панели: контент под ним остаётся читаемым."""
    FRAMES = "◐◓◑◒"

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._i = 0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self.setFixedSize(18, 18); self.hide()

    def start(self, tip: str = "Обновляется…") -> None:
        self.setToolTip(tip); self._reposition(); self.show(); self.raise_()
        if not self._timer.isActive():
            self._timer.start(120)

    def stop(self) -> None:
        self._timer.stop(); self.hide()

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self.FRAMES); self._reposition(); self.update()

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p:
            self.move(max(p.width() - self.width() - 8, 0), 6)

    def showEvent(self, e) -> None:
        self._reposition(); super().showEvent(e)

    def paintEvent(self, _) -> None:
        painter = QPainter(self)
        c = self.palette().text().color(); c.setAlpha(150); painter.setPen(c)
        f = painter.font(); f.setPointSize(13); painter.setFont(f)
        painter.drawText(self.rect(), Qt.AlignCenter, self.FRAMES[self._i])


class BadgeDelegate(QStyledItemDelegate):
    """Рисует ячейку как цветной бейдж (цвет — в Qt.UserRole + 1, текст — как есть)."""

    def paint(self, painter: QPainter, option, index) -> None:
        color = index.data(Qt.UserRole + 1)
        text = index.data(Qt.DisplayRole) or ""
        if not color or not text:
            return super().paint(painter, option, index)
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(4, 4, -4, -4)
        painter.setPen(Qt.NoPen); painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(r, 5, 5)
        painter.setPen(QPen(QColor("white")))
        f = painter.font(); f.setBold(True); f.setPointSizeF(max(9.0, f.pointSizeF() - 1)); painter.setFont(f)
        painter.drawText(r, Qt.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtGui import QFontMetrics
        s = super().sizeHint(option, index)
        f = option.font; f.setBold(True)
        s.setWidth(QFontMetrics(f).horizontalAdvance(index.data(Qt.DisplayRole) or "") + 22)
        return s


class SortItem(QTableWidgetItem):
    """Ячейка таблицы, которая сортируется по числу, если в ней число («1,234.5», «+12.3%», «▲ +2.4%», «31.79 ₽»),
    и по тексту иначе. Стандартный QTableWidgetItem сравнивает строки: «18,300» оказывался больше «192,000».
    Пустые и «—» при сортировке — в конце по возрастанию."""
    _RE = __import__("re")
    _NUM = _RE.compile(r"[-+−]?\d{1,3}(?:[, ]\d{3})*(?:\.\d+)?|[-+−]?\d+(?:\.\d+)?")
    _DECOR = _RE.compile(r"[▲▼■%₽\s]|RUB|USD|EUR|CNY|HKD")

    def _key(self):
        t = self.text().strip()
        if not t or t in ("—", "…", "•••"):
            return None
        core = self._DECOR.sub("", t)
        if not self._NUM.fullmatch(core):                    # даты, время, тикеры, текст — сравниваем как текст
            return t
        return float(core.replace("−", "-").replace(",", "").replace(" ", ""))

    def __lt__(self, other) -> bool:
        a, b = self._key(), other._key() if isinstance(other, SortItem) else other.text()
        if a is None or b is None:
            return b is None and a is not None
        if isinstance(a, float) and isinstance(b, float):
            return a < b
        return str(a) < str(b)


def scroll_wrap(w, base_background: bool = True):
    """Колонка графиков в прокрутке: на низком экране — полоса прокрутки, а не наложение подписей (charts.py
    держит минимальную высоту по содержимому). Горизонтально не прокручиваем — графики сжимаются по ширине."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QFrame, QScrollArea
    sa = QScrollArea(); sa.setWidget(w); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    if base_background:
        sa.setBackgroundRole(QPalette.Base); sa.viewport().setBackgroundRole(QPalette.Base)
    sa.setMinimumHeight(0)
    # по горизонтали не прокручиваем — значит, и уже содержимого быть нельзя (иначе поля формы обрезаются)
    sa.setMinimumWidth(w.minimumSizeHint().width() + sa.verticalScrollBar().sizeHint().width() + 2)
    return sa


def icon_column(table, col: int, legend: dict) -> None:
    """Колонка статусов значками: legend — {значок: (подсказка, цвет)}. Ячейка по центру, слово — в подсказке
    (экономит ширину: «ожидается*» → «≈»). Значки без эмодзи — одинаково видны на macOS, Windows и Linux."""
    for r in range(table.rowCount()):
        it = table.item(r, col)
        if it is None:
            continue
        sym = it.text().split(" ", 1)[0]
        if sym in legend:
            tip, color = legend[sym]
            it.setToolTip(tip + ("" if " " not in it.text() else ": " + it.text().split(" ", 1)[1]))
            it.setForeground(QColor(color)); it.setTextAlignment(Qt.AlignCenter)
    hdr = table.horizontalHeaderItem(col)
    if hdr is not None:
        hdr.setToolTip(" · ".join(f"{s} — {t}" for s, (t, _) in legend.items()))
