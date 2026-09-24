"""
screens/overview.py — отрисовка экранов по загруженным позициям. Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
import re
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView,
)

import portfolio_check as pc
from ui_common import TINT_GOOD, TINT_WARN, TINT_BAD, MASK, mask_text


class RenderMixin:
    # ---- Рендер -------------------------------------------------------------

    def render(self) -> None:
        total, by_cls = pc.class_shares(self.positions)
        targets = self.cfg["targets"]
        rules = pc.check_rules(self.positions, total, self.cfg.get("rules", {}))

        # Сводка
        order = ["gold", "real_estate", "real_estate_wh", "real_estate_other",
                 "equity_core", "equity_spec", "ballast", "foreign", "cash", "unclassified"]
        self._fill(self.tbl_classes, [])
        rows = []
        for cls in order:
            v = by_cls.get(cls, Decimal(0))
            if not v:
                continue
            share = v / total * 100 if total else Decimal(0)
            tgt, status, color = "", "", None
            if cls in targets:
                lo, hi = targets[cls]["min"], targets[cls]["max"]
                tgt = f"{lo}–{hi}%" if lo != hi else f"{lo}%"
                if share < Decimal(str(lo)):
                    status, color = f"недовес {((Decimal(str(lo)) - share) / 100 * total):,.0f} ₽", TINT_WARN
                elif share > Decimal(str(hi)):
                    status, color = f"перевес {((share - Decimal(str(hi))) / 100 * total):,.0f} ₽", TINT_BAD
                else:
                    status, color = "в коридоре", TINT_GOOD
            if self.privacy:
                status = re.sub(r"\d[\d,\s]*\s?₽", MASK + " ₽", status)
            rows.append(([pc.class_label(cls), self.rub(v), f"{share:.1f}%", tgt, status], color))
        self._fill(self.tbl_classes, rows)

        # Графики сводки: доли vs коридоры, и дельты в ₽
        range_rows, rebalance_rows = [], []
        for cls in order:
            v = by_cls.get(cls, Decimal(0))
            if not v and cls not in targets:
                continue
            share = float(v / total * 100) if total else 0.0
            rng = targets.get(cls)
            lo = float(rng["min"]) if rng else None; hi = float(rng["max"]) if rng else None
            range_rows.append((pc.class_label(cls), share, lo, hi))
            if rng and total:
                if share < lo:
                    rebalance_rows.append((pc.class_label(cls), float((Decimal(str(lo)) - Decimal(str(share))) / 100 * total)))
                elif share > hi:
                    rebalance_rows.append((pc.class_label(cls), -float((Decimal(str(share)) - Decimal(str(hi))) / 100 * total)))
        self.chart_ranges.set_data(range_rows)
        rebalance_rows.sort(key=lambda x: -x[1])
        self.chart_rebalance.set_data(rebalance_rows)
        self.chart_rebalance.hide_values = self.privacy

        # Сигналы
        titles = {
            "buy_candidates": "Кандидаты на добор",
            "frozen_but_dipped": "Просели, но заморожены — не трогать",
            "caps": "Превышение лимитов",
            "concentration": "Концентрация в одном эмитенте",
            "tail": "Хвост — кандидаты на чистку",
            "unclassified": "Без класса — назначь в Настройки → Инструменты",
        }
        parts = [f"**Итого: {self.rub(total)} ₽**", ""]
        n_alerts = 0
        for key, title in titles.items():
            if rules.get(key):
                n_alerts += len(rules[key])
                lines = [mask_text(l) for l in rules[key]] if self.privacy else rules[key]
                parts += [f"### {title}", ""] + lines + [""]
        if n_alerts == 0:
            parts.append("Сигналов нет — всё по правилам.")
        self.txt_signals.setMarkdown("\n".join(parts))
        self.lbl_signals_title.setText(f"<b>Сигналы ({n_alerts})</b>" if n_alerts else "<b>Сигналы</b> — всё по правилам")
        self._last_alerts = n_alerts

        # Позиции
        rows = []
        for p in sorted(self.positions, key=lambda x: -x.value_rub):
            color = TINT_BAD if p.pnl_pct <= -10 else (TINT_GOOD if p.pnl_pct > 0 else None)
            rows.append(([p.name, p.ticker, pc.class_label(p.cls), pc.sector_label(p.sector) if p.cls not in ("gold", "cash") else "",
                          self.qty(p.quantity), f"{p.avg_price:,.2f}", f"{p.cur_price:,.2f}", self.rub(p.value_rub), f"{p.pnl_pct:+.1f}"], color))
        self._fill(self.tbl_pos, rows)
        self.pie_currency.set_data(pc.breakdown_currency(self.positions))
        self.pie_companies.set_data(pc.breakdown_companies(self.positions))
        self.pie_sectors.set_data(pc.breakdown_sectors(self.positions))

        # Заблокировано
        br = pc.blocked_report(self.positions)
        dash = lambda v: "—" if v is None else self.rub(v)
        rows = []
        for r in br:
            p = r.p
            loss = r.loss_if_sold
            loss_pct = f"{float(loss / r.fair * 100):.0f}%" if (loss is not None and r.fair) else "—"
            color = TINT_WARN if (r.fair is None or r.otc is None) else (TINT_BAD if loss and loss > 0 else TINT_GOOD)
            rows.append(([p.name, p.ref_ticker or "—", self.qty(p.quantity),
                          f"{p.cur_price:,.2f}", f"{p.otc_price:,.2f}" + (f" ({p.otc_source})" if p.otc_source else "") if p.otc_price else "—",
                          f"{p.fair_price:,.2f}" if p.fair_price else "—",
                          self.rub(r.broker), dash(r.otc), dash(r.fair), dash(loss), loss_pct], color))
        self._fill(self.tbl_blocked, rows)
        if br:
            t = pc.blocked_totals(br)
            txt = (f"<b>Заблокировано {t['n']} поз.</b> Реальная стоимость по эталонам: <b>{self.rub(t['fair_all'])} ₽</b>. "
                   f"Продать внебиржей сейчас можно за <b>{self.rub(t['otc_all'])} ₽</b>. У брокера в портфеле: {self.rub(t['broker_all'])} ₽.")
            if t["n_full"]:
                txt += (f"<br>По {t['n_full']} поз., где есть обе оценки: <span style='color:#ff453a'><b>потеря при продаже сейчас "
                        f"{self.rub(t['loss'])} ₽ — {t['loss_pct']:.0f}% реальной цены</b></span>.")
            missing = t["n"] - t["n_full"]
            if missing:
                txt += (f"<br><span style='color:gray'>У {missing} поз. нет эталона или внебиржевой котировки (оранжевые строки) — "
                        "эталон задаётся в Настройки → Инструменты; внебиржевую цену API отдаёт только там, где брокер её выставляет.</span>")
            self.lbl_blocked.setText(txt)
            cats = [r.p.name for r in br]
            self.bar_blocked.set_data(cats, [
                ("у брокера", [float(r.broker) for r in br], "#7F8C8D"),
                ("внебиржа", [float(r.otc or 0) for r in br], "#D68910"),
                ("реальная", [float(r.fair or 0) for r in br], "#534AB7"),
            ])
            self.bar_blocked.hide_values = self.privacy
            recover = float(t["otc"]); lost = float(t["loss"]) if t["loss"] > 0 else 0.0
            self.pie_blocked.set_data([("вернёшь при продаже сейчас", recover), ("потеряешь", lost)])
            self.pie_blocked.hide_total = self.privacy
        else:
            self.lbl_blocked.setText("Заблокированных позиций нет. Отметить вручную: Настройки → Инструменты, колонка «Заблок.».")
            self.bar_blocked.set_data([], []); self.pie_blocked.set_data([])

        # Тепловая карта и история — по тем данным, что уже есть
        self.render_heatmap(); self.render_history()

        # Отчёт
        self.txt_report.setMarkdown(pc.render(self.positions, self.cfg, mask=self.privacy))

        fx_dec = {k.upper(): Decimal(str(v)) for k, v in (self.cfg.get("fx_fallback") or {}).items()}
        self.forecast.set_portfolio(float(total), getattr(self, "_income_estimate", None), getattr(self, "_macro", None),
                                    pc.new_money_yield(self.positions, self.cfg, fx_dec),
                                    pc.price_growth_target_weighted(self.positions, self.cfg, getattr(self, "period_changes", {}) or {}))
        self._last_alerts = n_alerts

    @staticmethod
    def _fill(table: QTableWidget, rows: list) -> None:
        sortable = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, (cells, color) in enumerate(rows):
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c > 0:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if color:
                    it.setBackground(color)
                table.setItem(r, c, it)
        table.setSortingEnabled(sortable)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
