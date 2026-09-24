"""
screens/ideas.py — скринер «Идеи», чёрный список, догрузка свечей кандидатов. Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations


from PySide6.QtGui import QColor

import portfolio_check as pc
import paths
import style as SY
from diagnostics import log
import news as NEWS
import screener as SCR
from ui_common import TINT_WARN, get_token
from workers import IdeasEnrichWorker, ScreenerWorker
from widgets import is_running


class IdeasMixin:
    # ---- скринер ------------------------------------------------------------

    def _blacklist(self) -> dict:
        sc = self.cfg.get("screener") or {}
        bl = sc.get("blacklist") or {}
        return {t: "" for t in bl} if isinstance(bl, list) else dict(bl)   # дефолты не домешиваем — см. config_migrate

    def _screener_filters(self):
        sc = self.cfg.get("screener") or {}
        return SCR.Filters(self.sp_min_yield.value(), float(self.sp_min_cap.value()), self.sp_max_debt.value(),
                           float(sc.get("min_avg5y_yield", 4.0)), float(sc.get("max_pe", 0)), float(sc.get("min_free_float", 0)),
                           tuple(sc.get("exclude_sectors") or ()))

    def _screener_exclude(self) -> set:
        return ({p.ticker for p in self.positions} | {p.ticker.rstrip("P") for p in self.positions} | set(self._blacklist()))

    def blacklist_selected(self) -> None:
        """Правый клик по кандидату → в чёрный список с причиной, перефильтрация без похода в сеть."""
        rows = sorted({i.row() for i in self.tbl_ideas.selectedIndexes()})
        if not rows:
            return
        tickers = [self.tbl_ideas.item(r, 1).text() for r in rows]
        from PySide6.QtWidgets import QInputDialog
        reason, ok = QInputDialog.getText(self, "В чёрный список", f"Причина для {', '.join(tickers)} (необязательно):")
        if not ok:
            return
        bl = dict(self._blacklist())
        for t in tickers:
            bl[t] = reason.strip()
        self.cfg.setdefault("screener", {})["blacklist"] = bl
        pc.save_config(self.cfg, self.config_path)
        if self.settings_win is not None:
            self.settings_win.close(); self.settings_win = None
        log.info("чёрный список скринера: +%s (%s)", tickers, reason.strip())
        self.refilter_ideas()

    def annotate_ideas(self, ideas) -> None:
        """Быстро, без сети: месячное изменение из памяти, упоминания в ленте и тональность из ИИ-сводки."""
        month = getattr(self, "_ideas_month", {})
        for idea in ideas:
            if idea.month_change is None and idea.ticker in month:
                idea.month_change = month[idea.ticker]
        s = getattr(self, "ai_summary", None)
        sent = {i["ticker"]: i["sentiment"] for i in (s.items if s else [])}
        kw = NEWS.build_keywords(ideas, (self.cfg.get("news", {}) or {}).get("aliases") or {})
        notes = {i["ticker"]: i.get("note", "") for i in (s.items if s else [])}
        for idea in ideas:
            words = kw.get(idea.ticker, [])
            idea.news_count = sum(1 for it in getattr(self, "news_items", [])
                                  if any(NEWS.mentions(f"{it.title} {it.summary}".lower(), w) for w in words))
            idea.news_sentiment = sent.get(idea.ticker, "")
            idea.news_note = notes.get(idea.ticker, "")

    def enrich_ideas(self, ideas) -> None:
        """Изменение за месяц — фоновой загрузкой свечей (как для позиций)."""
        token = get_token()
        if not token or not ideas:
            return
        if not any(getattr(i, "uid", "") for i in ideas):
            log.info("кандидаты: в кэше нет uid — свечи не запрашиваю, нужен свежий проход скринера")
            self.lbl_ideas.setText(self.lbl_ideas.text() + " <span style='color:gray'>Изменение за месяц появится после "
                                                           "свежего прохода — нажми «Найти».</span>")
            return
        if is_running(getattr(self, "ideas_worker", None)):
            return
        self.ideas_worker = IdeasEnrichWorker(token, ideas, paths.cache_dir())
        self.ideas_worker.done.connect(self._on_ideas_enriched)
        self.ideas_worker.failed.connect(lambda m: log.warning("свечи кандидатов: %s", m))
        self.ideas_worker.start()

    def _on_ideas_enriched(self, changes: dict) -> None:
        res = getattr(self, "_last_screen", None)
        if not changes:
            log.info("кандидаты: изменение за месяц не загружено")
            return
        store = getattr(self, "_ideas_month", {}); store.update(changes); self._ideas_month = store
        if res is None:
            return
        for i in res.all_ideas:
            if i.ticker in changes:
                i.month_change = changes[i.ticker]
        log.info("кандидаты: изменение за месяц загружено для %d бумаг", len(changes))
        self._on_ideas(res)      # _enriched_key уже выставлен — повторной догрузки не будет

    def _ideas_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        if not self.tbl_ideas.selectedIndexes():
            return
        m = QMenu(self)
        r = self.tbl_ideas.currentRow()
        t = self.tbl_ideas.item(r, 1).text() if r >= 0 and self.tbl_ideas.item(r, 1) else ""
        if t:
            m.addAction(f"Карточка {t}", lambda: self.open_card(t))
        m.addAction("Исключить из идей (в чёрный список)…", self.blacklist_selected)
        m.addSeparator(); m.addAction("Экспорт таблицы в CSV…", lambda: self.export_table(self.tbl_ideas, "идеи"))
        m.exec(self.tbl_ideas.viewport().mapToGlobal(pos))

    def refilter_ideas(self) -> None:
        """Перефильтровать последний результат скринера (после правки чёрного списка/фильтров), без сети."""
        res = getattr(self, "_last_screen", None)
        if res is None or not getattr(res, "all_ideas", None):
            return
        res.ideas = SCR.apply_filters(res.all_ideas, self._screener_filters(), self._screener_exclude())
        self._on_ideas(res)

    def run_screener(self, auto: bool = False) -> None:
        if is_running(getattr(self, "scr_worker", None)):
            return
        if auto:
            # свежий кэш (сутки) отдаём сразу, без похода в сеть
            cached = SCR.load_cached(paths.cache_dir(), self._screener_filters(), self._screener_exclude(), max_age_h=24)
            if cached is not None:
                self._on_ideas(cached); return
        exclude = self._screener_exclude()
        self.btn_screen.setEnabled(False); self.set_status("Скринер: загружаю список акций…")
        token = get_token()
        if not token:
            self.lbl_ideas.setText("Скринер работает через T-Invest API — нужен токен (Настройки → Доступ)."); self.pipeline_step("ideas", None); return
        self.scr_worker = ScreenerWorker(token, self.sp_min_yield.value(), float(self.sp_min_cap.value()), self.sp_max_debt.value(),
                                         exclude, paths.cache_dir(), force=not auto)
        self.scr_worker.filters = self._screener_filters()
        self.scr_worker.progress.connect(self.set_status)
        self.scr_worker.done.connect(self._on_ideas)
        self.scr_worker.failed.connect(lambda m: (log.error("скринер: %s", m), self.btn_screen.setEnabled(True),
                                                  self.set_status(f"Скринер: {m[:120]}"), self.pipeline_step("ideas", None),
                                                  self.lbl_ideas.setText(SY.span(f"<b>Скринер не отработал:</b> {m[:300]}", SY.BAD) + "<br>"
                                                                         "Проверь связь: Справка → «Проверить подключения».")))
        self.scr_worker.start()

    def _on_ideas(self, result) -> None:
        self.btn_screen.setEnabled(True)
        self.pipeline_step("ideas", None)
        if getattr(result, "all_ideas", None):
            self._last_screen = result
        ideas_now = result.ideas if hasattr(result, "ideas") else result
        self.annotate_ideas(ideas_now)
        ideas = result.ideas if hasattr(result, "ideas") else result
        fmt = lambda v, f="{:.1f}": "—" if v is None else f.format(v)
        rows = []
        for i in ideas:
            mc = getattr(i, "month_change", None)
            month = "…" if mc is None else f"{SY.arrow(mc)} {mc:+.1f}%"
            sent = getattr(i, "news_sentiment", "") or ("neutral" if getattr(i, "news_count", 0) else "")
            news = (SY.sentiment(sent)[0] + (f" {i.news_count}" if getattr(i, "news_count", 0) else "")) if sent else "—"
            rows.append(([i.name, i.ticker, i.sector, month, news, f"{i.price:,.2f}", f"{i.cap_bln:,.0f}", f"{i.div_yield:.1f}%",
                          fmt(getattr(i, "avg5y_div_yield", None), "{:.1f}%"), f"{i.div_per_share:,.2f}",
                          fmt(i.pe), fmt(i.net_debt_ebitda), fmt(i.roe, "{:.1f}%"), fmt(i.free_float, "{:.0f}%"), i.ex_div_date],
                         TINT_WARN if (i.net_debt_ebitda or 0) > 3 else None))
        self._fill(self.tbl_ideas, rows)
        self.render_payouts()                      # отсечки кандидатов на «Календаре»
        if not getattr(result, "from_cache", True):
            self.check_alerts()                    # свежий проход скринера — цены и доходность кандидатов
        # цвет и подсказки для иконок — по тикеру в строке (таблица сортируется пользователем)
        by_ticker = {i.ticker: i for i in ideas}
        for r in range(self.tbl_ideas.rowCount()):
            cell_t = self.tbl_ideas.item(r, 1)
            i = by_ticker.get(cell_t.text()) if cell_t else None
            if i is None:
                continue
            mc = getattr(i, "month_change", None)
            cm = self.tbl_ideas.item(r, 3)
            if cm is not None:
                cm.setForeground(QColor(SY.sign_color(mc or 0)))
                cm.setToolTip(f"Цена за месяц: {mc:+.1f}%" if mc is not None else "Свечи ещё грузятся")
            cn = self.tbl_ideas.item(r, 4)
            if cn is not None:
                s = getattr(i, "news_sentiment", "")
                mark_, color_, title_ = SY.sentiment(s)
                cn.setForeground(QColor(color_ if s else "gray"))
                note = getattr(i, "news_note", "")
                if s and i.news_count:
                    tip = f"{title_}; упоминаний в ленте: {i.news_count}"
                elif s:
                    tip = f"{title_} — оценка по отраслевому контексту (прямых новостей нет)"
                else:
                    tip = "В ленте нет новостей по бумаге"
                cn.setToolTip(tip + (f"\n{note}" if note else ""))
        if hasattr(result, "universe"):
            src = "из кэша" if result.from_cache else "свежий проход T-Invest"
            when = result.at[:16].replace("T", " ")
            debt = f", долг/EBITDA ≤ {self.sp_max_debt.value():.1f}" if self.sp_max_debt.value() > 0 else ""
            f = self._screener_filters()
            debt += (f", средняя дивдоходность 5 лет ≥ {f.min_avg5y_yield:.0f}%" if f.min_avg5y_yield > 0 else "") + \
                    (f", P/E ≤ {f.max_pe:.0f}" if f.max_pe > 0 else "") + (f", free float ≥ {f.min_free_float:.0f}%" if f.min_free_float > 0 else "")
            txt = (f"<b>{len(ideas)} кандидатов</b> по фильтру: дивдоходность ≥{self.sp_min_yield.value():.0f}%, кап. ≥{self.sp_min_cap.value()} млрд ₽{debt} "
                   f"({src}, {when}). Акций TQBR {result.universe}, с фундаменталом — {result.checked}, с дивдоходностью — {result.paying}. "
                   f"Исключены портфель и чёрный список ({len(self._blacklist())} бумаг, Настройки → Скринер); оранжевые — долг/EBITDA выше 3. "
                   "Правый клик по строке — в чёрный список.")
            if getattr(result, "errors", 0):
                txt += f" Ошибок запросов: {result.errors}" + (f" (последняя: {result.last_error})" if result.last_error else "") + "."
            if not ideas:
                txt += " " + SY.span("Пусто: " + (
                    "T-Invest не отдал фундаментал — см. ошибку выше и лог (Справка → Диагностика и лог); нажми «Найти кандидатов», чтобы повторить без кэша."
                    if result.paying == 0 else
                    "ослабь фильтры (например, 6% и 20 млрд) и нажми «Найти кандидатов»."), SY.WARN)
            self.lbl_ideas.setText(txt)
            log.info("скринер: %d кандидатов, проверено %d/%d, с кап. %d, платящих %d, %s", len(ideas), result.checked, result.universe,
                     result.with_cap, result.paying, src)
        self.set_status(f"Скринер: {len(ideas)} кандидатов по фильтру.")
        self._facts_ideas = ideas[:12]
        # догрузка свечей — один раз на набор кандидатов, иначе обновление таблицы запускало бы её снова
        # догрузка свечей — если есть кандидаты без месячного изменения (и мы за ними ещё не ходили)
        need = [i for i in ideas if i.month_change is None]
        key = tuple(sorted(i.ticker for i in need))
        if need and key != getattr(self, "_enriched_key", None):
            self._enriched_key = key
            self.enrich_ideas(need)
