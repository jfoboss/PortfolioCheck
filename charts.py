"""charts.py — простые графики на QPainter: линейный и круговой. QtCharts в PySide6-Essentials нет."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QBrush, QFontMetrics
from PySide6.QtWidgets import QWidget

PALETTE = ["#534AB7", "#0F6E56", "#C0392B", "#D68910", "#1F77B4", "#8E44AD", "#16A085", "#7F8C8D",
           "#2C3E50", "#E67E22", "#27AE60", "#95A5A6"]


def fmt_money(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:.1f} млн"
    if v >= 1e3:
        return f"{v / 1e3:.0f} тыс"
    return f"{v:.0f}"


class LineChart(QWidget):
    def __init__(self, title: str, y_label: str, parent=None, zero_based: bool = True):
        super().__init__(parent)
        self.title, self.y_label = title, y_label
        self.series: list[tuple[str, list[float], str, bool]] = []
        self.x_labels: list[str] = []
        self.hline: tuple[str, float] | None = None
        self.zero_based = zero_based          # False — ось Y начинается чуть ниже минимума (для истории стоимости)
        self.hide_values = False
        self.setMinimumHeight(140)

    def set_data(self, x_labels, series, hline=None) -> None:
        """series: (имя, значения — None допустим как пропуск, цвет, пунктир)."""
        self.x_labels, self.series, self.hline = x_labels, series, hline
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); grid = QColor(fg); grid.setAlpha(40)
        W, H = self.width(), self.height()
        left, right, top, bottom = 96, 16, 34, 30
        plot = QRectF(left, top, W - left - right, H - top - bottom)
        p.setPen(fg); p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(QRectF(0, 4, W, 24), Qt.AlignHCenter, self.title)
        if not self.series or not self.x_labels:
            return
        vals = [v for _, s, _, _ in self.series for v in s if v is not None] + ([self.hline[1]] if self.hline else [])
        if not vals:
            return
        ymax = max(vals) * 1.05 or 1
        ymin = 0.0 if self.zero_based else max(0.0, min(vals) - (ymax - min(vals)) * 0.15)
        span = (ymax - ymin) or 1
        n = len(self.x_labels)
        X = lambda i: plot.left() + plot.width() * i / max(n - 1, 1)
        Y = lambda v: plot.bottom() - plot.height() * (v - ymin) / span
        p.setFont(QFont(self.font().family(), 9))
        for k in range(5):
            v = ymin + span * k / 4; yy = Y(v)
            p.setPen(QPen(grid, 1)); p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(fg); p.drawText(QRectF(0, yy - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, "•••" if self.hide_values else fmt_money(v))
        step = max(1, n // 10)
        for i in range(0, n, step):
            p.drawText(QRectF(X(i) - 30, plot.bottom() + 4, 60, 18), Qt.AlignHCenter, self.x_labels[i])
        p.save(); p.translate(12, plot.center().y()); p.rotate(-90)
        p.drawText(QRectF(-100, -8, 200, 16), Qt.AlignCenter, self.y_label); p.restore()
        if self.hline:
            name, v = self.hline
            p.setPen(QPen(QColor("#C0392B"), 1.5, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), Y(v)), QPointF(plot.right(), Y(v)))
            p.drawText(QRectF(plot.left() + 4, Y(v) - 16, 300, 14), Qt.AlignLeft, name)
        for name, s, color, dashed in self.series:
            p.setPen(QPen(QColor(color), 2.2, Qt.DashLine if dashed else Qt.SolidLine))
            path = QPainterPath(); started = False
            for i, v in enumerate(s):
                if v is None:
                    started = False; continue
                pt = QPointF(X(i), Y(v))
                if not started:
                    path.moveTo(pt); started = True
                else:
                    path.lineTo(pt)
            p.drawPath(path)
            # одиночная точка (один снапшот) — рисуем маркер, иначе её не видно
            pts = [i for i, v in enumerate(s) if v is not None]
            if len(pts) == 1:
                p.setBrush(QBrush(QColor(color))); p.drawEllipse(QPointF(X(pts[0]), Y(s[pts[0]])), 4, 4)
        lx, ly = plot.left() + 6, top + 4
        for name, s, color, dashed in self.series:
            p.setPen(QPen(QColor(color), 2.2, Qt.DashLine if dashed else Qt.SolidLine))
            p.drawLine(QPointF(lx, ly + 7), QPointF(lx + 22, ly + 7))
            p.setPen(fg); p.drawText(QRectF(lx + 28, ly, W - lx - 40, 14), Qt.AlignLeft | Qt.AlignVCenter, name)
            ly += 16


class PieChart(QWidget):
    """Кольцевая диаграмма с легендой справа: (название, сумма). Мелкие доли сворачиваются в «Прочее»."""

    def __init__(self, title: str, max_slices: int = 10, parent=None):
        super().__init__(parent)
        self.title, self.max_slices = title, max_slices
        self.items: list[tuple[str, float]] = []
        self.hide_total = False
        self.setMinimumSize(260, 150)

    def set_data(self, items: list[tuple[str, float]]) -> None:
        items = sorted([(n, float(v)) for n, v in items if v > 0], key=lambda x: -x[1])
        if len(items) > self.max_slices:
            head, tail = items[: self.max_slices - 1], items[self.max_slices - 1:]
            items = head + [("Прочее", sum(v for _, v in tail))]
        self.items = items
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color()
        W, H = self.width(), self.height()
        p.setPen(fg); p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(QRectF(0, 4, W, 24), Qt.AlignHCenter, self.title)
        total = sum(v for _, v in self.items)
        if not total:
            p.setFont(QFont(self.font().family(), 10)); p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, "нет данных"); return
        d = min(W * 0.38, H - 44); d = max(d, 60)
        rect = QRectF(16, 34 + (H - 34 - d) / 2, d, d)
        start = 90 * 16
        p.setPen(Qt.NoPen)
        for i, (name, v) in enumerate(self.items):
            span = -int(round(v / total * 360 * 16))
            p.setBrush(QBrush(QColor(PALETTE[i % len(PALETTE)])))
            p.drawPie(rect, start, span); start += span
        hole = rect.adjusted(d * 0.28, d * 0.28, -d * 0.28, -d * 0.28)
        p.setBrush(self.palette().base()); p.drawEllipse(hole)
        p.setPen(fg); p.setFont(QFont(self.font().family(), 9))
        p.drawText(hole, Qt.AlignCenter, "•••" if self.hide_total else fmt_money(total))
        # легенда
        lx, ly = rect.right() + 18, 40
        rowh = 16
        p.setFont(QFont(self.font().family(), 9))
        for i, (name, v) in enumerate(self.items):
            if ly + rowh > H:
                break
            p.setBrush(QBrush(QColor(PALETTE[i % len(PALETTE)]))); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(lx, ly + 3, 10, 10), 2, 2)
            p.setPen(fg)
            label = f"{name}  {v / total * 100:.1f}%"
            p.drawText(QRectF(lx + 16, ly, W - lx - 20, rowh), Qt.AlignLeft | Qt.AlignVCenter, label)
            ly += rowh


class BarChart(QWidget):
    """Горизонтальные сгруппированные бары: категории (строки) × серии (цвета). Значения — деньги."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.categories: list[str] = []
        self.series: list[tuple[str, list[float], str]] = []   # (имя, значения по категориям, цвет)
        self.hide_values = False
        self.setMinimumHeight(160)

    def set_data(self, categories: list[str], series: list[tuple[str, list[float], str]]) -> None:
        self.categories, self.series = categories, series
        self.setMinimumHeight(120)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); grid = QColor(fg); grid.setAlpha(40)
        W, H = self.width(), self.height()
        p.setPen(fg); p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(QRectF(0, 4, W, 24), Qt.AlignHCenter, self.title)
        if not self.categories or not self.series:
            p.setFont(QFont(self.font().family(), 10)); p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, "нет данных"); return
        left = min(260, int(W * 0.32)); right = 90; top = 52; bottom = 10
        plot = QRectF(left, top, W - left - right, H - top - bottom)
        vmax = max([v for _, vals, _ in self.series for v in vals] + [1.0])
        n, k = len(self.categories), len(self.series)
        group_h = max(18.0, min(14 * k + 12, plot.height() / max(1, n)))
        bar_h = max(4.0, (group_h - 8) / max(1, k))
        p.setFont(QFont(self.font().family(), 9))
        # легенда
        lx = left
        for name, _, color in self.series:
            p.setBrush(QBrush(QColor(color))); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(lx, 32, 10, 10), 2, 2)
            p.setPen(fg); p.drawText(QRectF(lx + 14, 30, 220, 14), Qt.AlignLeft | Qt.AlignVCenter, name)
            lx += 14 + 8 * len(name) + 24
        for i, cat in enumerate(self.categories):
            y0 = plot.top() + i * group_h
            p.setPen(fg)
            label = QFontMetrics(p.font()).elidedText(cat, Qt.ElideRight, left - 16)
            p.drawText(QRectF(4, y0, left - 12, group_h - 12), Qt.AlignRight | Qt.AlignVCenter, label)
            for j, (name, vals, color) in enumerate(self.series):
                v = vals[i] if i < len(vals) else 0.0
                y = y0 + j * bar_h
                w = plot.width() * (v / vmax) if vmax else 0
                p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(color)))
                p.drawRoundedRect(QRectF(plot.left(), y + 2, max(w, 1 if v > 0 else 0), bar_h - 4), 3, 3)
                p.setPen(fg)
                p.drawText(QRectF(plot.left() + w + 6, y, right + 40, bar_h), Qt.AlignLeft | Qt.AlignVCenter,
                           "•••" if self.hide_values else (fmt_money(v) if v > 0 else "—"))
            p.setPen(QPen(grid, 1)); p.drawLine(QPointF(4, y0 + group_h - 6), QPointF(W - 4, y0 + group_h - 6))


class RangeBarChart(QWidget):
    """Доля класса (бар) на фоне целевого коридора (полоса). Показывает, где класс относительно цели."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.rows: list[tuple[str, float, float | None, float | None]] = []   # (имя, доля %, min %, max %)
        self.setMinimumHeight(160)

    def set_data(self, rows) -> None:
        self.rows = rows
        self.setMinimumHeight(120)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); grid = QColor(fg); grid.setAlpha(40)
        W, H = self.width(), self.height()
        p.setPen(fg); p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(QRectF(0, 4, W, 24), Qt.AlignHCenter, QFontMetrics(p.font()).elidedText(self.title, Qt.ElideRight, int(W - 8)))
        if not self.rows:
            return
        left = min(240, int(W * 0.32)); right = 60; top = 40
        plot = QRectF(left, top, W - left - right, H - top - 8)
        rowh = max(14.0, min(26.0, plot.height() / max(1, len(self.rows))))   # строки ужимаются под высоту
        vmax = max([r[1] for r in self.rows] + [r[3] or 0 for r in self.rows] + [10.0]) * 1.1
        X = lambda v: plot.left() + plot.width() * v / vmax
        p.setFont(QFont(self.font().family(), 9))
        for k in range(0, int(vmax) + 1, 10):
            p.setPen(QPen(grid, 1)); p.drawLine(QPointF(X(k), plot.top()), QPointF(X(k), plot.bottom()))
            p.setPen(fg); p.drawText(QRectF(X(k) - 20, plot.top() - 14, 40, 12), Qt.AlignHCenter, f"{k}%")
        for i, (name, share, lo, hi) in enumerate(self.rows):
            y = plot.top() + i * rowh
            p.setPen(fg)
            label = QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, left - 16)
            p.drawText(QRectF(4, y, left - 12, rowh - 6), Qt.AlignRight | Qt.AlignVCenter, label)
            in_range = True
            if lo is not None or hi is not None:
                a, b = (lo or 0.0), (hi if hi is not None else vmax)
                band = QColor(fg); band.setAlpha(55)
                p.setPen(Qt.NoPen); p.setBrush(QBrush(band))
                p.drawRoundedRect(QRectF(X(a), y + 1, max(X(b) - X(a), 3), rowh - 8), 3, 3)   # коридор — широкая полоса
                in_range = (lo is None or share >= lo) and (hi is None or share <= hi)
                color = QColor("#34C759") if in_range else (QColor("#FF9F0A") if (lo is not None and share < lo) else QColor("#FF453A"))
            else:
                color = QColor(fg); color.setAlpha(120)
            p.setPen(Qt.NoPen); p.setBrush(QBrush(color))
            p.drawRoundedRect(QRectF(plot.left(), y + 8, max(X(share) - plot.left(), 1), rowh - 22), 2, 2)  # доля — тонкий бар
            p.setPen(fg); p.drawText(QRectF(X(share) + 6, y, 60, rowh - 6), Qt.AlignLeft | Qt.AlignVCenter, f"{share:.1f}%")


class DivergingBarChart(QWidget):
    """Расходящиеся бары: вправо — докупить, влево — продать (₽ до ближайшей границы коридора)."""

    def __init__(self, title: str, parent=None, left_label: str = "продать ←", right_label: str = "→ докупить",
                 empty_text: str = "всё в коридорах"):
        super().__init__(parent)
        self.title = title
        self.left_label, self.right_label, self.empty_text = left_label, right_label, empty_text
        self.rows: list[tuple[str, float]] = []   # (имя, дельта ₽: + вправо, − влево)
        self.hide_values = False
        self.setMinimumHeight(160)

    def set_data(self, rows) -> None:
        self.rows = rows
        self.setMinimumHeight(120)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); grid = QColor(fg); grid.setAlpha(60)
        W, H = self.width(), self.height()
        p.setPen(fg); p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(QRectF(0, 4, W, 24), Qt.AlignHCenter, QFontMetrics(p.font()).elidedText(self.title, Qt.ElideRight, int(W - 8)))
        if not self.rows:
            p.setFont(QFont(self.font().family(), 10)); p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, self.empty_text); return
        left = min(200, int(W * 0.28)); right = 16; top = 40
        plot = QRectF(left, top, W - left - right, H - top - 8)
        rowh = max(14.0, min(26.0, plot.height() / max(1, len(self.rows))))
        vmax = max(abs(v) for _, v in self.rows) or 1.0
        cx = plot.center().x(); half = plot.width() / 2 - 70
        p.setFont(QFont(self.font().family(), 9))
        p.setPen(fg)
        p.drawText(QRectF(plot.left(), plot.top() - 16, half, 12), Qt.AlignRight, self.left_label)
        p.drawText(QRectF(cx + 4, plot.top() - 16, half, 12), Qt.AlignLeft, self.right_label)
        p.setPen(QPen(grid, 1)); p.drawLine(QPointF(cx, plot.top()), QPointF(cx, plot.bottom()))
        for i, (name, v) in enumerate(self.rows):
            y = plot.top() + i * rowh
            p.setPen(fg)
            label = QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, left - 16)
            p.drawText(QRectF(4, y, left - 12, rowh - 6), Qt.AlignRight | Qt.AlignVCenter, label)
            w = half * abs(v) / vmax
            color = QColor("#34C759") if v > 0 else QColor("#FF453A")
            p.setPen(Qt.NoPen); p.setBrush(QBrush(color))
            rect = QRectF(cx, y + 6, w, rowh - 18) if v > 0 else QRectF(cx - w, y + 6, w, rowh - 18)
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(fg)
            txt = "•••" if self.hide_values else fmt_money(abs(v))
            if v > 0:
                p.drawText(QRectF(cx + w + 6, y, 80, rowh - 6), Qt.AlignLeft | Qt.AlignVCenter, txt)
            else:
                p.drawText(QRectF(cx - w - 86, y, 80, rowh - 6), Qt.AlignRight | Qt.AlignVCenter, txt)


def _squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap (Bruls et al.): прямоугольники для values (уже отсортированы по убыванию)."""
    rects: list[tuple[float, float, float, float]] = []
    total = sum(values) or 1.0
    scale = w * h / total
    vals = [v * scale for v in values]

    def worst(row, length):
        s = sum(row)
        if not row or s == 0 or length == 0:
            return float("inf")
        mx, mn = max(row), min(row)
        return max(length * length * mx / (s * s), s * s / (length * length * mn))

    def layout(row, x, y, w, h):
        s = sum(row)
        out = []
        if w >= h:                       # кладём столбцом слева
            cw = s / h if h else 0
            cy = y
            for v in row:
                rh = v / cw if cw else 0
                out.append((x, cy, cw, rh)); cy += rh
            return out, (x + cw, y, w - cw, h)
        else:                            # кладём строкой сверху
            rh = s / w if w else 0
            cx = x
            for v in row:
                cw = v / rh if rh else 0
                out.append((cx, y, cw, rh)); cx += cw
            return out, (x, y + rh, w, h - rh)

    row: list[float] = []
    i = 0
    while i < len(vals):
        length = min(w, h)
        if not row or worst(row + [vals[i]], length) <= worst(row, length):
            row.append(vals[i]); i += 1
        else:
            placed, (x, y, w, h) = layout(row, x, y, w, h); rects += placed; row = []
    if row:
        placed, _ = layout(row, x, y, w, h); rects += placed
    return rects


class TreemapChart(QWidget):
    """Тепловая карта: площадь — стоимость позиции, цвет — доходность за период (красный ← 0 → зелёный)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, float, float | None]] = []   # (название, стоимость, %)
        self.hide_values = False
        self.full_at = 10.0
        self.mode = "diverging"     # "diverging" — доходность ±; "positive" — только вверх (дивиденды), серый → зелёный
        self.value_fmt = lambda pct: f"{pct:+.1f}%"
        self.setMinimumHeight(140)

    def set_data(self, items, groups: dict[str, str] | None = None) -> None:
        """items: (название, площадь, %); groups: название → группа (отрасль). С группами — двухуровневая карта."""
        self.items = sorted([(n, float(v), c) for n, v, c in items if v > 0], key=lambda t: -t[1])
        self.groups = groups or {}
        self.update()

    # Гамма как у Finviz: нейтральный серый в нуле → чистый зелёный / красный к ±10 %
    NEUTRAL = (94, 94, 99)      # системный серый
    GREEN = (48, 209, 88)       # системный зелёный
    RED = (255, 69, 58)         # системный красный
    FULL_AT = 10.0              # % — полная насыщенность; задаётся снаружи под период (день ±3, год ±30)

    @classmethod
    def _color(cls, pct: float | None, full_at: float | None = None, mode: str = "diverging") -> QColor:
        if pct is None:
            return QColor(160, 160, 165)
        if mode == "positive":
            t = max(0.0, min(1.0, pct / (full_at or cls.FULL_AT))) ** 0.8
            return QColor(*(int(n + (e - n) * t) for n, e in zip(cls.NEUTRAL, cls.GREEN)))
        k = max(-1.0, min(1.0, pct / (full_at or cls.FULL_AT)))
        k = (abs(k) ** 0.7) * (1 if k >= 0 else -1)   # чуть быстрее уходим от серого — мелкие движения видны
        end = cls.GREEN if k >= 0 else cls.RED
        t = abs(k)
        return QColor(*(int(n + (e - n) * t) for n, e in zip(cls.NEUTRAL, end)))

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        if not self.items:
            p.setPen(self.palette().text().color()); p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, "нет данных"); return
        if getattr(self, "groups", None):
            self._paint_grouped(p, W, H); return
        rects = _squarify([v for _, v, _ in self.items], 2, 2, W - 4, H - 4)
        for (name, val, pct), (x, y, w, h) in zip(self.items, rects):
            self._tile(p, name, val, pct, x, y, w, h)

    def _paint_grouped(self, p: QPainter, W: float, H: float) -> None:
        """Двухуровневая карта: площадь делится между группами, внутри группы — между бумагами.
        В заголовке группы — её сумма и доля от всей карты."""
        fg = self.palette().text().color()
        total = sum(v for _, v, _ in self.items) or 1.0
        by_group: dict[str, list] = {}
        for name, val, pct in self.items:
            by_group.setdefault(self.groups.get(name, "Прочее"), []).append((name, val, pct))
        order = sorted(by_group.items(), key=lambda kv: -sum(v for _, v, _ in kv[1]))
        HEAD = 18
        grects = _squarify([sum(v for _, v, _ in items) for _, items in order], 2, 2, W - 4, H - 4)
        for (gname, items), (gx, gy, gw, gh) in zip(order, grects):
            # рамка и заголовок группы
            frame = QColor(fg); frame.setAlpha(90)
            p.setPen(QPen(frame, 1)); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(gx + 1, gy + 1, max(gw - 2, 0), max(gh - 2, 0)))
            p.setPen(fg); p.setFont(QFont(self.font().family(), 9, QFont.Bold))
            if gw > 40 and gh > HEAD + 6:
                gsum = sum(v for _, v, _ in items)
                fm = QFontMetrics(p.font())
                amount = "" if self.hide_values else fmt_money(gsum)
                share = f"  {gsum / total * 100:.0f}%" if total else ""
                right = (amount + share).strip()
                rw = fm.horizontalAdvance(right) + 6
                label = fm.elidedText(gname.upper(), Qt.ElideRight, max(int(gw - 12 - rw), 20))
                p.drawText(QRectF(gx + 5, gy + 2, gw - 10, HEAD - 2), Qt.AlignLeft | Qt.AlignVCenter, label)
                if gw - 12 > rw + fm.horizontalAdvance(label):
                    dim = QColor(fg); dim.setAlpha(170); p.setPen(dim)
                    p.setFont(QFont(self.font().family(), 9))
                    p.drawText(QRectF(gx + 5, gy + 2, gw - 10, HEAD - 2), Qt.AlignRight | Qt.AlignVCenter, right)
                    p.setPen(fg)
            inner = (gx + 2, gy + HEAD, max(gw - 4, 1), max(gh - HEAD - 2, 1))
            if gh <= HEAD + 6:
                inner = (gx + 2, gy + 2, max(gw - 4, 1), max(gh - 4, 1))
            for (name, val, pct), (x, y, w, h) in zip(items, _squarify([v for _, v, _ in items], *inner)):
                self._tile(p, name, val, pct, x, y, w, h)

    def _tile(self, p: QPainter, name, val, pct, x, y, w, h) -> None:
        r = QRectF(x + 1.5, y + 1.5, max(w - 3, 0), max(h - 3, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(self._color(pct, self.full_at, self.mode))); p.drawRoundedRect(r, 4, 4)
        if True:
            if w > 46 and h > 28:
                p.setPen(QColor("white"))
                fs = 11 if w > 120 and h > 60 else 9
                p.setFont(QFont(self.font().family(), fs, QFont.Bold))
                label = QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, int(w - 10))
                p.drawText(QRectF(x + 5, y + 4, w - 10, 16), Qt.AlignLeft, label)
                p.setFont(QFont(self.font().family(), fs))
                txt = ("—" if pct is None else self.value_fmt(pct)) + ("" if self.hide_values or h < 44 else f"\n{fmt_money(val)}")
                p.drawText(QRectF(x + 5, y + 20, w - 10, h - 24), Qt.AlignLeft | Qt.AlignTop, txt)
