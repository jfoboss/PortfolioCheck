"""
screens/attribution.py — панель «Откуда изменение» на экране «Стратегия» (BACKLOG п. 18): переоценка против
покупок (нетто) за период, по классам и крупнейшие вклады бумаг. Миксин MainWindow. Модель — attribution.py.
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import attribution as AT
import portfolio_check as pc
from charts import DivergingBarChart
from widgets import scroll_wrap, note
import style as SY


class AttributionMixin:
    def _build_attribution_panel(self) -> QWidget:
        w = self._doc_panel(QWidget()); lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8)
        self.cmb_attr = QComboBox()
        for label, days in AT.PERIODS:
            self.cmb_attr.addItem(label, days)
        self.cmb_attr.setCurrentIndex(2)                       # год
        self.cmb_attr.currentIndexChanged.connect(lambda _: self.render_attribution())
        top = QHBoxLayout(); top.addWidget(QLabel("<b>Откуда изменение</b>")); top.addStretch(); top.addWidget(self.cmb_attr)
        self.lbl_attr = QLabel(); self.lbl_attr.setWordWrap(True)
        self.chart_attr = DivergingBarChart("Переоценка по классам, ₽", left_label="минус ←", right_label="→ плюс",
                                            empty_text="нужно минимум два снапшота")
        self.lbl_attr_top = note()
        lay.addLayout(top); lay.addWidget(self.lbl_attr); lay.addWidget(self.chart_attr, 1); lay.addWidget(self.lbl_attr_top)
        return scroll_wrap(w)                                   # текст + бары: на низком окне — прокрутка, не наложение

    def render_attribution(self) -> None:
        if not hasattr(self, "chart_attr"):
            return
        classes = {p.ticker: p.cls for p in self.positions}
        a = AT.for_period(self.snap_dir(), self.cmb_attr.currentData(), classes, getattr(self, "payouts", []))
        money = SY.money_fn(self.privacy, signed=True)
        self.chart_attr.hide_values = self.privacy
        if a is None:
            self.lbl_attr.setText("Снапшоты копятся с каждым обновлением — разложение появится, когда их будет хотя бы два.")
            self.chart_attr.set_data([]); self.lbl_attr_top.setText(""); return
        pct = f" ({a.change / a.v0 * 100:+.1f}%)" if a.v0 else ""
        self.lbl_attr.setText(
            f"{a.start} → {a.end}: изменение <b>{money(a.change)} ₽</b>{pct} = переоценка <b>{money(a.revaluation)} ₽</b> "
            f"+ покупки/продажи (нетто) <b>{money(a.flows)} ₽</b>. Выплаты за период: {money(a.payouts)} ₽ — "
            "они либо лежат деньгами, либо уже вошли в покупки как реинвест: без истории операций брокера пополнения "
            "и реинвест не разделить.")
        rows = sorted(((pc.class_label(c), r) for c, (r, _f) in a.by_class.items() if abs(r) > 0.5), key=lambda x: -x[1])
        self.chart_attr.set_data(rows)
        if a.top:
            self.lbl_attr_top.setText("Крупнейшие вклады переоценки: " + ", ".join(
                f"{t} {money(v)}" for t, _n, v in a.top[:5]))
        else:
            self.lbl_attr_top.setText("")
