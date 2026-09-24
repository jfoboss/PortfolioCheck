"""
widgets.py — небольшие виджеты окна: индикатор обновления на панели, бейджи в таблицах.
"""
from __future__ import annotations


from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
)

from PySide6.QtWidgets import QStyledItemDelegate
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
