"""
screens/history.py — история стоимости, изменения по периодам, тепловая карта. Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QTimer

import portfolio_check as pc
import paths
import history as H
from diagnostics import log
from ui_common import get_token
from workers import HistoryWorker, LongHistoryWorker
import style as SY


class HistoryMixin:
    # ---- история / heatmap --------------------------------------------------

    def start_history(self) -> None:
        token = get_token()
        if not token or not self.bundle_path().exists() or not self.positions:
            self.pipeline_step("history", "новости"); self.refresh_news(); return
        if getattr(self, "hist_worker", None) is not None and self.hist_worker.isRunning():
            return
        self.hist_worker = HistoryWorker(token, self.cfg, self.positions, paths.data_dir() / "cache")
        self.hist_worker.done.connect(self._on_history)
        self.hist_worker.failed.connect(lambda m: (log.error("история/свечи: %s", m), self.set_status(f"История не загружена: {m[:100]}"),
                                                   self.pipeline_step("history", "новости"), self.refresh_news()))
        self.hist_worker.start()

    def _on_history(self, result) -> None:
        self.pipeline_step("history", "новости")
        QTimer.singleShot(0, lambda: self.apply_growth_base(fetch_if_needed=False))
        QTimer.singleShot(0, self.refresh_news)
        self.period_changes, self.backfilled = result[0], result[1]
        empty = result[2] if len(result) > 2 else []
        if empty:
            log.info("свечей нет по %d позициям: %s", len(empty), ", ".join(empty[:12]))
        log.info("свечи загружены: инструментов с историей %d", sum(1 for v in self.period_changes.values() if v.get("year") is not None))
        self.render_heatmap(); self.render_history()
        self.start_factors()                       # индекс и доллар для сценариев (без токена, раз в сутки)

    def render_heatmap(self) -> None:
        btn = self.heat_group.checkedButton()
        period = btn.property("period") if btn else "day"
        items = []
        fx = {k.upper(): Decimal(str(v)) for k, v in (self.cfg.get("fx_fallback") or {}).items()}
        for p in self.positions:
            if p.cls == "cash" or p.value_rub <= 0:
                continue
            if period == "all":
                pct = float(p.pnl_pct) if p.avg_price > 0 else None
            elif period == "div":
                pct = pc.dividend_yield(p, fx)
                if pct is None:
                    continue                                   # в дивидендной карте только то, что платит
                # площадь — рубли выплат за 12 мес, а не стоимость позиции
                items.append((p.name, float(p.div_12m * p.quantity * fx.get(p.currency, Decimal(1))), pct))
                continue
            else:
                pct = (getattr(self, "period_changes", {}) or {}).get(p.figi, {}).get(period)
            items.append((p.name, float(p.value_rub), pct))
        self.treemap.hide_values = self.privacy
        if period == "div":
            self.treemap.mode = "positive"; self.treemap.full_at = 15.0
            self.treemap.value_fmt = lambda v: f"{v:.1f}% дивиденды"
        else:
            self.treemap.mode = "diverging"
            self.treemap.full_at = {"day": 3.0, "week": 6.0, "month": 12.0, "year": 35.0, "all": 60.0}.get(period, 10.0)
            self.treemap.value_fmt = lambda v: f"{v:+.1f}%"
        groups = {p.name: pc.sector_group(p) for p in self.positions} if self.cb_group_sectors.isChecked() else None
        self.treemap.set_data(items, groups)
        # малая карта на Обзоре — всегда «день», без группировки
        day_items = [(p.name, float(p.value_rub), (getattr(self, "period_changes", {}) or {}).get(p.figi, {}).get("day"))
                     for p in self.positions if p.cls != "cash" and p.value_rub > 0]
        self.treemap_small.hide_values = self.privacy; self.treemap_small.full_at = 3.0; self.treemap_small.mode = "diverging"
        self.treemap_small.value_fmt = lambda v: f"{v:+.1f}%"; self.treemap_small.set_data(day_items)
        self.render_kpi()
        known = [c for _, _, c in items if c is not None]
        if period == "div":
            total = sum(v for _, v, _ in items)
            paying_value = sum(float(p.value_rub) for p in self.positions if pc.dividend_yield(p, fx) is not None)
            avg = total / paying_value * 100 if paying_value else 0
            silent = sum(1 for p in self.positions if p.cls != "cash" and p.value_rub > 0 and pc.dividend_yield(p, fx) is None)
            self.lbl_heat.setText(f"Площадь — выплаты за 12 мес (после налога), цвет — доходность к стоимости позиции. "
                                  f"Итого {self.rub(total)} ₽/год по {len(items)} поз., {avg:.1f}% к их стоимости; "
                                  f"не платят или API не отдаёт выплаты — {silent} поз. (на карте их нет).")
        elif items and known:
            w = sum(v for _, v, c in items if c is not None)
            avg = sum(v * c for _, v, c in items if c is not None) / w if w else 0
            self.lbl_heat.setText(f"Взвешенно по стоимости: {avg:+.2f}% за период; без данных — {len(items) - len(known)} поз. (серые).")
        else:
            self.lbl_heat.setText("Доходности по периодам появятся после загрузки свечей (идёт в фоне после обновления).")

    def long_growth_from_cache(self, years: float) -> tuple[dict, float, int]:
        """CAGR по позициям из уже сохранённых свечей — мгновенно, без сети."""
        cache_dir = paths.data_dir() / "cache"
        vals, covered, missing = {}, [], 0
        for p in self.positions:
            if not p.figi:
                continue
            series = H.load_cached(cache_dir, p.figi)
            v, cov = H.cagr(series, float(p.cur_price), years) if series else (None, 0.0)
            if v is None:
                missing += 1
            else:
                vals[p.figi] = v; covered.append(cov)
        return vals, (sum(covered) / len(covered)) if covered else 0.0, missing

    def apply_growth_base(self, fetch_if_needed: bool = True) -> None:
        """Показать переоценку по выбранной базе: сначала из кэша, и догрузить в фоне, если истории не хватает."""
        years = self.forecast.growth_base()
        if years <= 1 or not self.positions:
            self._push_growth_to_forecast(); return
        vals, covered, missing = self.long_growth_from_cache(years)
        if vals:
            self._long_values = {"years": years, "values": vals, "covered": covered, "missing": missing}
            self._push_growth_to_forecast()
            log.info("переоценка из кэша: база %s г, бумаг %d, глубина %.1f г", years, len(vals), covered)
        if fetch_if_needed and (not vals or covered < years * 0.8):
            self.start_long_history(float(years))     # кэша нет или он короче базы — догружаем в фоне

    def start_long_history(self, years: float) -> None:
        """Загрузить свечи за несколько лет и пересчитать переоценку как среднегодовой рост."""
        token = get_token()
        if not token or not self.positions:
            self.set_status("Для средней за несколько лет нужны токен и загруженный портфель."); return
        if getattr(self, "long_worker", None) is not None and self.long_worker.isRunning():
            return
        self.set_status(f"Загружаю историю за {years:.0f} г…")
        self.long_worker = LongHistoryWorker(token, self.cfg, self.positions, paths.data_dir() / "cache", years)
        self.long_worker.progress.connect(self.set_status)
        self.long_worker.done.connect(lambda r: self._on_long_history(r, years))
        self.long_worker.failed.connect(lambda m: (log.error("длинная история: %s", m), self.set_status(f"История не загружена: {m[:100]}")))
        self.long_worker.start()

    def _on_long_history(self, result, years: float) -> None:
        values, covered, missing = result
        self._long_values = {"years": years, "values": values, "covered": covered, "missing": missing}
        log.info("история %s г: бумаг с историей %d, средняя глубина %.1f г, без истории %d", years, len(values), covered, missing)
        self.set_status(f"История за {years:.0f} г загружена ({len(values)} бумаг, глубина {covered:.1f} г).")
        self._push_growth_to_forecast()

    def _push_growth_to_forecast(self) -> None:
        """Пересчитать и передать в прогноз значения из портфеля (капитал, доход, доходность, переоценка)."""
        if not self.positions:
            return
        fx_dec = {k.upper(): Decimal(str(v)) for k, v in (self.cfg.get("fx_fallback") or {}).items()}
        total = sum(p.value_rub for p in self.positions)
        lv = getattr(self, "_long_values", None)
        base = self.forecast.growth_base()          # 1 / 3 / 5 лет
        if base > 1 and lv and abs(lv["years"] - base) < 0.1:
            growth = pc.price_growth_target_weighted(self.positions, self.cfg, {}, values=lv["values"])
            self.forecast.growth_covered = lv["covered"]
        else:
            growth = pc.price_growth_target_weighted(self.positions, self.cfg, self.period_changes)
            self.forecast.growth_covered = 1.0
        self.forecast.set_portfolio(float(total), getattr(self, "_income_estimate", None), getattr(self, "_macro", None),
                                    pc.new_money_yield(self.positions, self.cfg, fx_dec), growth)

    def render_kpi(self) -> None:
        """Карточки на Обзоре."""
        if not self.positions:
            return
        total = sum(p.value_rub for p in self.positions)
        ch = getattr(self, "period_changes", {}) or {}
        def weighted(period):
            vals = [(float(p.value_rub), ch.get(p.figi, {}).get(period)) for p in self.positions if p.cls != "cash"]
            vals = [(v, c) for v, c in vals if c is not None]
            w = sum(v for v, _ in vals)
            return (sum(v * c for v, c in vals) / w) if w else None
        v, s = self.kpi["total"]; v.setText(f"{self.rub(total)} ₽"); s.setText(f"{len(self.positions)} позиций")
        for key in ("day", "month"):
            pct = weighted(key); v, s = self.kpi[key]
            if pct is None:
                v.setText("—"); s.setText("свечи грузятся")
            else:
                v.setText(f"{pct:+.2f}%"); v.setStyleSheet(f"{SY.KPI_CSS}; color: {SY.sign_color(pct)}")
                s.setText(f"≈ {self.rub(total * Decimal(str(pct)) / 100)} ₽")
        fx = {k.upper(): Decimal(str(x)) for k, x in (self.cfg.get("fx_fallback") or {}).items()}
        inc = sum(p.div_12m * p.quantity * fx.get(p.currency, Decimal(1)) for p in self.positions if p.div_12m > 0)
        v, s = self.kpi["income"]; v.setText(f"{self.rub(inc)} ₽")
        s.setText(f"{float(inc / total * 100):.1f}% к портфелю, {self.rub(inc / 12)} ₽/мес" if total else "")
        n = getattr(self, "_last_alerts", 0); v, s = self.kpi["signals"]
        v.setText(str(n)); v.setStyleSheet(f"{SY.KPI_CSS}; color: {SY.WARN if n else SY.GOOD}")
        s.setText("требуют внимания" if n else "всё по правилам")
        try:
            import forecast as F
            p = self.forecast.params(); rows = F.simulate(p); gy = F.goal_year(rows, p["target_monthly_real"])
            v, s = self.kpi["goal"]; v.setText(str(datetime.now().year + gy) if gy is not None else "> горизонта")
            s.setText(f"{self.rub(p['target_monthly_real'])} ₽/мес в сегодняшних ₽")
        except Exception:  # noqa: BLE001
            pass

    def render_history(self) -> None:
        snaps = H.snapshot_history(self.snap_dir())
        back = getattr(self, "backfilled", []) or []
        if not snaps and not back:
            self.chart_history.set_data([], []); self.lbl_history.setText("История появится после первых обновлений."); return
        dates = sorted({d for d, _ in back} | {d for d, _ in snaps})
        labels = [f"{d[8:10]}.{d[5:7]}" for d in dates]
        series = []
        if back:
            bm = dict(back); last = None; vals = []
            for d in dates:
                last = bm.get(d, last); vals.append(last)
            series.append(("сегодняшний состав по ценам прошлых дней (приближение)", vals, "#7F8C8D", True))
        if snaps:
            sm = dict(snaps)
            first = min(sm)
            vals = [sm.get(d) if d >= first else None for d in dates]      # до первого снапшота — пусто, не ноль
            last = None
            for i, d in enumerate(dates):
                if d >= first:
                    last = vals[i] if vals[i] is not None else last; vals[i] = last
            series.append(("факт по снапшотам приложения", vals, "#534AB7", False))
        self.chart_history.hide_values = self.privacy
        self.chart_history.set_data(labels, series)
        self.lbl_history.setText("В API нет истории стоимости портфеля. Пунктир — сегодняшний состав, оценённый по ценам прошлых дней "
                                 "(покупки/продажи за год не учтены). Сплошная — реальные значения из снапшотов, копится с первого запуска.")
