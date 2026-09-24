"""
screens/scenarios_panel.py — вкладка «Сценарии» на экране «Портфель» (BACKLOG п. 23): что будет со стоимостью при
движении индекса Мосбиржи и курса доллара — по бетам прошлого года. Миксин MainWindow. Модели — scenarios.py,
factors.py; загрузка факторов — FactorsWorker.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import factors as FA
import history as H
import macro
import paths
import portfolio_check as pc
import scenarios as SC
from charts import DivergingBarChart
from diagnostics import log
import style as SY
from widgets import scroll_wrap
from workers import FactorsWorker


class ScenariosMixin:
    def _build_scenarios_panel(self) -> QWidget:
        w = self._doc_panel(QWidget()); lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        for label, di, du in SC.PRESETS:
            b = QPushButton(label); b.clicked.connect(lambda _=False, di=di, du=du: self._set_scenario(di, du))
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.sp_sc_index, self.sp_sc_usd = QDoubleSpinBox(), QDoubleSpinBox()
        for sb, label in ((self.sp_sc_index, "Индекс Мосбиржи:"), (self.sp_sc_usd, "Доллар к рублю:")):
            sb.setRange(-90, 300); sb.setDecimals(1); sb.setSuffix(" %"); sb.setSingleStep(5)
            sb.valueChanged.connect(lambda _: self.render_scenarios())
            row2.addWidget(QLabel(label)); row2.addWidget(sb)
        row2.addStretch()
        lay.addLayout(row2)
        self.lbl_sc = QLabel(); self.lbl_sc.setWordWrap(True); self.lbl_sc.setTextFormat(Qt.RichText)
        self.chart_sc = DivergingBarChart("Изменение по классам, ₽", left_label="минус ←", right_label="→ плюс",
                                          empty_text="задай сценарий")
        self.tbl_sc = self._table(["Бумага", "Тикер", "Изменение, ₽", "Изменение, %", "β к индексу", "β к доллару", "R²"])
        self.tbl_sc.sortByColumn(2, Qt.AscendingOrder)       # сначала самые большие потери
        body = QHBoxLayout(); body.addWidget(scroll_wrap(self._doc_panel(self._wrap_one(self.chart_sc))), 2); body.addWidget(self.tbl_sc, 3)
        lay.addWidget(self.lbl_sc); lay.addLayout(body, 1)
        self._set_scenario(*SC.PRESETS[0][1:])
        return w

    @staticmethod
    def _wrap_one(chart) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(chart, 1)
        return w

    def _set_scenario(self, d_index: float, d_usd: float) -> None:
        for sb, v in ((self.sp_sc_index, d_index), (self.sp_sc_usd, d_usd)):
            sb.blockSignals(True); sb.setValue(v); sb.blockSignals(False)
        self.render_scenarios()

    def start_factors(self) -> None:
        """Догрузить индекс и доллар (кэш старше суток) и календарь ЦБ (старше недели). В демо — не ходим в сеть."""
        cache = paths.data_dir() / "cache"
        if paths.DEMO_DIR is not None or (FA.fresh(cache, FA.IMOEX) and FA.fresh(cache, FA.USDRUB)
                                          and macro.calendar_fresh(cache)):
            return
        if getattr(self, "factors_worker", None) is not None and self.factors_worker.isRunning():
            return
        self.factors_worker = FactorsWorker(cache)
        self.factors_worker.done.connect(self._on_factors)
        self.factors_worker.start()

    def _on_factors(self, status: dict) -> None:
        bad = {k: v for k, v in status.items() if v != "ok"}
        if bad:
            log.warning("факторы сценариев: %s", bad)
        self._sc_key = None                           # пересчитать беты
        self.render_scenarios()
        self.load_macro_calendar()                    # календарь ЦБ → экран «Календарь» и оповещения
        self.check_alerts()

    def _scenario_betas(self):
        cache = paths.data_dir() / "cache"
        files = [cache / f"candles_{x}.json" for x in [p.figi for p in self.positions if p.figi] + [FA.IMOEX, FA.USDRUB]]
        key = (tuple((p.ticker, float(p.value_rub)) for p in self.positions),
               max((f.stat().st_mtime for f in files if f.exists()), default=0))
        if getattr(self, "_sc_key", None) != key:
            candles = {p.figi: H.load_cached(cache, p.figi) for p in self.positions if p.figi}
            idx, usd = FA.load(cache, FA.IMOEX), FA.load(cache, FA.USDRUB)
            self._sc_key = key
            self._sc_betas = SC.betas(self.positions, candles, idx, usd) if idx and usd else None
        return self._sc_betas

    def render_scenarios(self) -> None:
        if not hasattr(self, "tbl_sc"):
            return
        b = self._scenario_betas() if self.positions else None
        if not b:
            self.lbl_sc.setText("Для сценариев нужны история индекса Мосбиржи и курса доллара (MOEX ISS и ЦБ, без "
                                "токена) — они загружаются после обновления портфеля. Проверить доступ: Справка → "
                                "«Проверить подключения».")
            self.chart_sc.set_data([]); self._fill(self.tbl_sc, []); return
        bs, missing = b
        r = SC.apply(bs, self.sp_sc_index.value(), self.sp_sc_usd.value(), missing, pc.class_label)
        money = SY.money_fn(self.privacy, signed=True)
        pct = r.change / r.total * 100 if r.total else 0.0
        self.lbl_sc.setText(
            f"Оценка: <b>{money(r.change)} ₽</b> ({pct:+.1f}% от оценённой части портфеля). "
            "<span style='color:gray'>Линейное приближение по связям прошлого года (β); в кризис связи меняются — "
            "это ориентир масштаба, не прогноз.</span>"
            + (f"<br><span style='color:gray'>Без истории цен, не оценены: {', '.join(r.missing)}.</span>" if r.missing else ""))
        self.chart_sc.hide_values = self.privacy
        self.chart_sc.set_data(sorted(((c, v) for c, v in r.by_class.items() if abs(v) > 0.5), key=lambda x: -x[1]))
        beta = {x.ticker: x for x in bs}
        self._fill(self.tbl_sc, [([name, t, money(d), f"{dp:+.1f}", f"{beta[t].bm:.2f}", f"{beta[t].bfx:.2f}",
                                   f"{beta[t].r2:.2f}"], None) for t, name, d, dp in r.rows])
