"""
screens/summaries.py — ИИ-сводки по экранам (один пакетный запрос). Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


import portfolio_check as pc
import paths
import activity
import history as H
import ai as AI
from diagnostics import log
from ui_common import mask_text
from workers import ExecWorker
from widgets import is_running


class ExecSummaryMixin:
    # ---- executive summary по экранам ---------------------------------------

    def _facts_overview(self) -> str:
        total, by_cls = pc.class_shares(self.positions)
        ch = getattr(self, "period_changes", {}) or {}
        def weighted(period):
            vals = [(float(p.value_rub), ch.get(p.figi, {}).get(period)) for p in self.positions if p.cls != "cash"]
            vals = [(v, c) for v, c in vals if c is not None]; w = sum(v for v, _ in vals)
            return (sum(v * c for v, c in vals) / w) if w else None
        lines = [f"Стоимость портфеля: {total:,.0f} ₽; позиций {len(self.positions)}.",
                 f"Изменение за день: {weighted('day')}; за месяц: {weighted('month')} (проценты, взвешенно по стоимости)."]
        lines.append("Доли классов и целевые коридоры инвестора (правила):")
        for cls, rng in self.cfg["targets"].items():
            share = float(by_cls.get(cls, Decimal(0)) / total * 100) if total else 0
            lines.append(f"- {pc.class_label(cls)}: {share:.1f}% при коридоре {rng['min']}–{rng['max']}%")
        rules = pc.check_rules(self.positions, total, self.cfg.get("rules", {}))
        for key, title in (("buy_candidates", "Просадки ≥ порога (кандидаты на добор по правилам)"), ("frozen_but_dipped", "Просели, но заморожены правилами"),
                           ("concentration", "Концентрация"), ("caps", "Превышение лимитов")):
            if rules.get(key):
                lines.append(title + ": " + "; ".join(l.lstrip("- ") for l in rules[key][:6]))
        fx = {k.upper(): Decimal(str(x)) for k, x in (self.cfg.get("fx_fallback") or {}).items()}
        inc = sum(p.div_12m * p.quantity * fx.get(p.currency, Decimal(1)) for p in self.positions if p.div_12m > 0)
        lines.append(f"Доход за 12 мес после налога: {inc:,.0f} ₽ ({float(inc / total * 100) if total else 0:.1f}% к портфелю).")
        movers = sorted([(p.name, ch.get(p.figi, {}).get("day")) for p in self.positions if ch.get(p.figi, {}).get("day") is not None], key=lambda x: x[1])
        if movers:
            lines.append("Худшие за день: " + ", ".join(f"{n} {c:+.1f}%" for n, c in movers[:3]) + "; лучшие: " + ", ".join(f"{n} {c:+.1f}%" for n, c in movers[-3:]))
        return "\n".join(lines)

    def _facts_portfolio(self) -> str:
        total = sum(p.value_rub for p in self.positions) or Decimal(1)
        lines = ["Позиции (доля, результат за всё время):"]
        for p in sorted(self.positions, key=lambda x: -x.value_rub)[:25]:
            lines.append(f"- {p.name} [{pc.class_label(p.cls)}, {pc.sector_group(p)}]: {float(p.value_rub / total * 100):.1f}%, {p.pnl_pct:+.1f}%")
        lines.append("По отраслям: " + "; ".join(f"{n} {v / float(total) * 100:.0f}%" for n, v in sorted(pc.breakdown_sectors(self.positions), key=lambda x: -x[1])))
        lines.append("По валютам/металлам: " + "; ".join(f"{n} {v / float(total) * 100:.0f}%" for n, v in pc.breakdown_currency(self.positions)))
        br = pc.blocked_report(self.positions)
        if br:
            t = pc.blocked_totals(br)
            lines.append(f"Заблокировано {t['n']} поз.: реальная стоимость {t['fair_all']:,.0f} ₽, внебиржей можно выручить {t['otc_all']:,.0f} ₽, "
                         f"потеря при продаже сейчас {t['loss']:,.0f} ₽ ({t['loss_pct']:.0f}%).")
        lines.append(f"Правило концентрации инвестора: не более {self.cfg.get('rules', {}).get('single_name_max_pct', 11)}% в одном эмитенте.")
        return "\n".join(lines)

    def _facts_market(self) -> str:
        ch = getattr(self, "period_changes", {}) or {}
        lines = []
        for period, label in (("day", "за день"), ("month", "за месяц")):
            acc: dict[str, list] = {}
            for p in self.positions:
                c = ch.get(p.figi, {}).get(period)
                if c is not None:
                    acc.setdefault(pc.sector_group(p), []).append((float(p.value_rub), c))
            if acc:
                lines.append(f"Отрасли {label}: " + "; ".join(f"{g} {sum(v * c for v, c in xs) / sum(v for v, _ in xs):+.1f}%" for g, xs in acc.items()))
        s = getattr(self, "ai_summary", None)
        if s:
            lines.append("Тональность новостей по бумагам: " + "; ".join(f"{i['ticker']} {i['sentiment']}: {i['note']}" for i in s.items[:12]))
            if s.macro:
                lines.append("Макро из новостей: " + s.macro)
        for it in getattr(self, "news_items", [])[:25]:
            lines.append(f"- [{','.join(it.matched) or 'макро'}] {it.title}")
        if getattr(self, "_facts_ideas", None):
            lines.append("Кандидаты скринера (дивдоходность TTM, долг/EBITDA): " + "; ".join(
                f"{i.ticker} {i.div_yield:.1f}%/{i.net_debt_ebitda if i.net_debt_ebitda is not None else '—'}" for i in self._facts_ideas[:8]))
        return "\n".join(lines) or "Данных по рынку пока нет."

    def _facts_strategy(self) -> str:
        import forecast as F
        p = self.forecast.params(); rows = F.simulate(p); gy = F.goal_year(rows, p["target_monthly_real"])
        lines = [f"Параметры плана: капитал {p['start_capital']:,.0f} ₽, доход сейчас {p['base_income']:,.0f} ₽/год, пополнение {p['monthly_contribution']:,.0f} ₽/мес, "
                 f"реинвест {p['reinvest_pct']:.0f}%, рост выплат {p['income_growth_pct']:.1f}%/год, доходность новых денег {p['yield_new_money_pct']:.1f}%, "
                 f"инфляция {p['inflation_pct']:.1f}%, цель {p['target_monthly_real']:,.0f} ₽/мес в сегодняшних деньгах, горизонт {int(p['years'])} лет.",
                 f"Год достижения цели: {datetime.now().year + gy if gy is not None else 'за горизонтом'}; к концу горизонта реальный доход {rows[-1].income_real / 12:,.0f} ₽/мес."]
        for r_pct, name in ((0, "без реинвеста"), (50, "реинвест 50%")):
            g = F.goal_year(F.simulate(p, r_pct), p["target_monthly_real"])
            lines.append(f"Сценарий {name}: цель {'в ' + str(datetime.now().year + g) if g is not None else 'не достигается'}.")
        snaps = H.snapshot_history(self.snap_dir())
        if len(snaps) >= 2:
            lines.append(f"Факт по снапшотам: {snaps[0][0]} {snaps[0][1]:,.0f} ₽ → {snaps[-1][0]} {snaps[-1][1]:,.0f} ₽.")
        m = getattr(self, "_macro", None)
        if m:
            lines.append(f"ЦБ РФ {m.period}: инфляция {m.inflation}% г/г, ставка {m.key_rate}%.")
        return "\n".join(lines)

    def run_exec(self, manual: bool = False) -> None:
        conf = self.ai_conf()
        if conf is None or not self.positions:
            activity.get().skip_pending(["ai_exec"])
            if manual:
                self.set_status("ИИ-сводки: нужен ключ (Настройки → ИИ) и загруженный портфель.")
            return
        if is_running(getattr(self, "exec_worker", None)):
            return
        jobs = [("overview", self._facts_overview()), ("portfolio", self._facts_portfolio()),
                ("market", self._facts_market()), ("strategy", self._facts_strategy())]
        for kind, _ in jobs:
            self.exec_panels[kind].spinner.start("Модель готовит сводку — показана прошлая")
        self.set_status(f"ИИ-сводки по экранам ({conf.provider})… показаны прошлые")
        self.exec_worker = activity.get().track(ExecWorker(conf, jobs, paths.cache_dir(), manual), "ai_exec",
                                                f"ИИ-сводки по экранам ({len(jobs)})")
        self.exec_worker.one.connect(self._on_exec_one)
        self.exec_worker.failed.connect(lambda m: (log.error("ИИ-сводка экрана: %s", m), self.set_status(f"ИИ-сводка: {m[:120]}"),
                                                   [p.spinner.stop() for p in self.exec_panels.values()]))
        self.exec_worker.done.connect(lambda: (self.set_status("ИИ-сводки по экранам готовы."),
                                               [p.spinner.stop() for p in self.exec_panels.values()]))
        self.exec_worker.start()

    def _on_exec_one(self, result) -> None:
        s, note = result if isinstance(result, tuple) else (result, "")
        self.exec_panels[s.kind].spinner.stop()
        AI.save_exec(s, paths.cache_dir())
        self.exec_panels[s.kind].setHtml(AI.render_exec_html(s, mask_text if self.privacy else None, note))

    def ai_enabled(self) -> bool:
        """Есть ли чем делать сводки: провайдер с ключом (или Ollama)."""
        return self.ai_conf() is not None

    def update_ai_visibility(self) -> None:
        """Без ключа ИИ-панели и кнопка спрятаны — вместо пустых мест."""
        on = self.ai_enabled()
        for panel in self.exec_panels.values():
            panel.setVisible(on)
        self.ai_panel.setVisible(on); self.btn_ai.setVisible(on)
        self.ai_small.setVisible(on); self.lbl_ai_small_title.setVisible(on)
        self.news_split_ai.setVisible(on)

    def render_exec_cached(self) -> None:
        for kind, panel in self.exec_panels.items():
            s = AI.load_exec(kind, paths.cache_dir())
            if s:
                panel.setHtml(AI.render_exec_html(s, mask_text if self.privacy else None))
