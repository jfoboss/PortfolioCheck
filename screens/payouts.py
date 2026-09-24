"""
screens/payouts.py — экран «Календарь»: ближайшие отсечки и выплаты по портфелю, денежный поток по месяцам,
отсечки у кандидатов скринера, заседания ЦБ по ключевой ставке (macro.py). Миксин MainWindow (состояние — атрибуты окна). Модель — payouts.py.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget

import macro
import paths
import payouts as PO
from charts import BarChart
from diagnostics import log
from ui_common import MASK, TINT_WARN
from widgets import icon_column, note, scroll_wrap
import style as SY

STATUS = {"declared": "✔", "expected": "≈", "paid": "✓"}                 # значок в ячейке; слово — в подсказке
STATUS_LEGEND = {"✔": ("объявлена эмитентом", SY.GOOD),
                 "≈": ("ожидается: выплата прошлого года, сдвинутая на год — ещё не объявлена", SY.MUTED),
                 "✓": ("уже была", SY.MUTED)}


class PayoutsMixin:
    def _build_payouts_tab(self) -> QWidget:
        self.payouts: list[PO.Payout] = []
        self.lbl_payouts = QLabel(); self.lbl_payouts.setWordWrap(True); self.lbl_payouts.setTextFormat(Qt.RichText)
        self.chart_cashflow = BarChart("Поток по месяцам, ₽", stacked=True)
        self.tbl_payouts = self._table(["Бумага", "Тикер", "Посл. день покупки", "Отсечка", "Выплата", "На бумагу",
                                        "Кол-во", "Сумма, ₽", "Ст."])
        self.tbl_payouts.horizontalHeaderItem(2).setToolTip("Последний торговый день, когда покупка ещё даёт право на выплату")
        self.tbl_cand_div = self._table(["Кандидат", "Тикер", "Экс-дивиденд", "Дней", "Дивдоходность", "Дивиденды, ₽"])
        self.lbl_payouts_note = note("Суммы — после НДФЛ 13%. ✔ — объявлена; ≈ — ожидается: выплата прошлого года, сдвинутая на год, эмитент её "
                                       "ещё не объявил, сумма и дата могут измениться; ✓ — уже была. Последний день покупки — последний "
                                       "торговый день, когда покупка ещё даёт право на выплату.")

        # календарь читается по дате: таблица — по отсечке, кандидаты — по числу дней (уже упорядочены)
        self.tbl_payouts.sortByColumn(3, Qt.AscendingOrder)
        self.tbl_cand_div.setSortingEnabled(False)
        mine = QWidget(); ml = QVBoxLayout(mine); ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel("<b>Отсечки и выплаты по портфелю — 12 месяцев</b>")); ml.addWidget(self.tbl_payouts, 1)
        ml.addWidget(self.lbl_payouts_note)
        cand = QWidget(); cdl = QVBoxLayout(cand); cdl.setContentsMargins(0, 0, 0, 0)
        cdl.addWidget(QLabel("<b>Ближайшие отсечки у кандидатов (экран «Идеи»)</b>")); cdl.addWidget(self.tbl_cand_div, 1)
        left = QSplitter(Qt.Vertical); left.setObjectName("payouts_left")
        left.addWidget(mine); left.addWidget(cand); left.setStretchFactor(0, 3); left.setStretchFactor(1, 2)
        flow_w = self._doc_panel(QWidget()); cl = QVBoxLayout(flow_w); cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(self.chart_cashflow, 1)
        self.macro_events: list[macro.MacroEvent] = []
        self.tbl_macro = self._table(["Событие ЦБ", "Дата", "Дней"])
        self.tbl_macro.setSortingEnabled(False)
        cbr_w = QWidget(); cbl = QVBoxLayout(cbr_w); cbl.setContentsMargins(0, 4, 0, 0)
        cbl.addWidget(QLabel("<b>Ключевая ставка: календарь ЦБ — 6 месяцев</b>")); cbl.addWidget(self.tbl_macro, 1)
        # поток и календарь ЦБ — разделителем: таблица ЦБ больше не отнимает у графика высоту молча
        chart_w = QSplitter(Qt.Vertical); chart_w.setObjectName("payouts_right")
        chart_w.addWidget(scroll_wrap(flow_w)); chart_w.addWidget(cbr_w)
        chart_w.setStretchFactor(0, 3); chart_w.setStretchFactor(1, 1)
        chart_w.setMinimumWidth(380)                 # иначе широкая таблица выдавливает график в полоску
        body = QSplitter(Qt.Horizontal); body.setObjectName("payouts"); body.addWidget(left); body.addWidget(chart_w)
        body.setStretchFactor(0, 3); body.setStretchFactor(1, 2)
        w = QWidget(); wl = QVBoxLayout(w); wl.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout(); top.addWidget(self.lbl_payouts, 1); wl.addLayout(top); wl.addWidget(body, 1)
        return w

    # ---- данные ------------------------------------------------------------

    def load_payouts_from_disk(self) -> None:
        items, at = PO.load(paths.cache_dir())
        self.payouts, self._payouts_at = items, at
        self.render_payouts()

    def load_macro_calendar(self) -> None:
        self.macro_events = macro.load_calendar(paths.cache_dir())
        self.render_payouts()

    def set_payouts(self, raw: list) -> None:
        """Свежие выплаты из API (прошлые + объявленные) → добавить «ожидается», сохранить, показать."""
        self.payouts = PO.with_expected(raw)
        self._payouts_at = ""
        try:
            PO.save(self.payouts, paths.cache_dir())
        except OSError as e:
            log.warning("календарь выплат не сохранён: %s", e)
        self.render_payouts()

    # ---- отрисовка ---------------------------------------------------------

    def render_payouts(self) -> None:
        if not hasattr(self, "tbl_payouts"):
            return
        today = date.today()
        up = PO.upcoming(self.payouts, today)
        money = SY.money_fn(self.privacy)
        rows = []
        for x in up:
            soon = x.last_buy_date and 0 <= (date.fromisoformat(x.last_buy_date) - today).days <= 14
            rows.append(([x.name, x.ticker, x.last_buy_date or "—", x.record_date, x.payment_date or "—",
                          f"{x.per_share:,.2f} {'₽' if x.currency.upper() == 'RUB' else x.currency}", MASK if self.privacy else f"{x.quantity:,.0f}",
                          money(x.amount_rub), STATUS[x.status]], TINT_WARN if soon else None))
        self._fill(self.tbl_payouts, rows)
        icon_column(self.tbl_payouts, 8, STATUS_LEGEND)

        flow = PO.monthly_cashflow(self.payouts, today)
        self.chart_cashflow.hide_values = self.privacy
        self.chart_cashflow.set_data([PO.month_label(k) for k, _, _ in flow],
                                     [("✔ объявлено", [a for _, a, _ in flow], SY.GOOD),
                                      ("≈ ожидается", [b for _, _, b in flow], SY.MUTED)])
        declared = sum(a for _, a, _ in flow); expected = sum(b for _, _, b in flow)
        month30 = [x for x in up if (date.fromisoformat(x.record_date) - today).days <= 30]
        if not self.payouts:
            head = ("Выплат пока нет: календарь заполняется при обновлении портфеля из T-Invest "
                    "(прошлые и объявленные выплаты по каждой бумаге).")
        else:
            head = (f"<b>12 месяцев:</b> объявлено {money(declared)} ₽, ожидается ещё {money(expected)} ₽ · "
                    f"<b>30 дней:</b> отсечек {len(month30)}"
                    + (f", ближайшая — {month30[0].ticker} {month30[0].record_date}" if month30 else "")
                    + (" · оранжевым — последний день покупки в ближайшие 2 недели" if any(r[1] for r in rows) else ""))
        ev = macro.upcoming(getattr(self, "macro_events", []), today)
        meet = next((e for e in ev if e.kind == macro.MEETING), None)
        if meet:
            days = (date.fromisoformat(meet.date) - today).days
            head += f" · <b>заседание ЦБ по ставке</b> — {date.fromisoformat(meet.date):%d.%m} (через {days} дн.)"
        self.lbl_payouts.setText(head)
        self._fill(self.tbl_macro, [([macro.KIND_LABEL[e.kind] + (" + среднесрочный прогноз" if e.forecast else ""), e.date,
                                      str((date.fromisoformat(e.date) - today).days)],
                                     TINT_WARN if e.kind == macro.MEETING and (date.fromisoformat(e.date) - today).days <= 7
                                     else None) for e in ev])

        res = getattr(self, "_last_screen", None)
        ideas = getattr(res, "ideas", None) or []
        crow = []
        for i in ideas:
            try:
                d = date.fromisoformat(str(i.ex_div_date)[:10])
            except ValueError:
                continue
            days = (d - today).days
            if 0 <= days <= 120:
                crow.append((days, [i.name, i.ticker, d.isoformat(), str(days), f"{i.div_yield:.1f}%",
                                    f"{i.div_per_share:,.2f}"]))
        crow.sort(key=lambda t: t[0])
        self._fill(self.tbl_cand_div, [(cells, None) for _, cells in crow])
