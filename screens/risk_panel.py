"""
screens/risk_panel.py — панель «Риск» на экране «Портфель» (BACKLOG п. 22). Миксин MainWindow. Модель — risk.py.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

import history as H
import paths
import risk as RK
from widgets import note


class RiskMixin:
    def _build_risk_panel(self) -> QWidget:
        w = self._doc_panel(QWidget()); lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8)
        self.lbl_risk = QLabel(); self.lbl_risk.setWordWrap(True)
        self.tbl_risk = self._table(["Бумага", "Тикер", "Вес, %", "Волатильность, % год", "Вклад в риск, %"])
        self.tbl_risk.sortByColumn(4, Qt.DescendingOrder)   # по вкладу в риск; числа сортируются как числа (SortItem)
        self.lbl_risk_pairs = note()
        lay.addWidget(self.lbl_risk); lay.addWidget(self.tbl_risk, 1); lay.addWidget(self.lbl_risk_pairs)
        return w

    def _risk_report(self):
        """Расчёт — по кэшу свечей; повторно только если изменились позиции или свечи (перерисовок много)."""
        cache = paths.cache_dir()
        files = [cache / f"candles_{p.figi}.json" for p in self.positions if p.figi]
        key = (tuple((p.ticker, float(p.value_rub)) for p in self.positions),
               max((f.stat().st_mtime for f in files if f.exists()), default=0))
        if getattr(self, "_risk_key", None) != key:
            candles = {p.figi: H.load_cached(cache, p.figi) for p in self.positions if p.figi}
            self._risk_key, self._risk = key, RK.analyze(self.positions, candles)
        return self._risk

    def render_risk(self) -> None:
        if not hasattr(self, "tbl_risk"):
            return
        r = self._risk_report() if self.positions else None
        if r is None:
            self.lbl_risk.setText("Риск считается по истории цен за год — появится, когда загрузятся свечи хотя бы двух бумаг.")
            self._fill(self.tbl_risk, []); self.lbl_risk_pairs.setText(""); return
        self.lbl_risk.setText(
            f"<b>За год ({r.days} торговых дней):</b> волатильность портфеля <b>{r.vol:.1f}%</b> в год · "
            f"максимальная просадка сегодняшнего состава <b>{r.max_drawdown:.1f}%</b> · эффективное число бумаг "
            f"<b>{r.effective_n:.1f}</b> · диверсификация гасит риск "
            + ("<b>полностью</b>. " if r.diversification == float("inf") else f"в <b>{r.diversification:.2f}×</b>. ")
            + "<span style='color:gray'>Это описание прошлого года, не прогноз.</span>"
            + (f"<br><span style='color:gray'>Без истории цен, в расчёт не вошли: {', '.join(r.missing)}.</span>"
               if r.missing else ""))
        self._fill(self.tbl_risk, [([name, t, f"{wt:.1f}", f"{vol:.1f}", f"{c:.1f}"], None)
                                   for t, name, wt, vol, c in r.rows])
        if r.pairs:
            self.lbl_risk_pairs.setText("Сильнее всего движутся вместе: " + ", ".join(
                f"{a}–{b} {c:+.2f}" for a, b, c in r.pairs) + ". Корреляция 1 — синхронно, 0 — независимо.")
