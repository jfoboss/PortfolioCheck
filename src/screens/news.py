"""
screens/news.py — лента новостей и ИИ-сводка по ней. Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
import re
from PySide6.QtWidgets import (
    QTableWidgetItem,
)

import paths
import ai as AI
from diagnostics import log
import news as NEWS
from workers import NewsWorker, AIWorker
from widgets import is_running


class NewsMixin:
    # ---- новости ------------------------------------------------------------

    def refresh_news(self) -> None:
        if is_running(getattr(self, "news_worker", None)):
            return
        if not self.positions:
            self.pipeline_step("news", "идеи"); self.run_screener(auto=True); return
        feeds = self.cfg.get("news", {}).get("feeds") or NEWS.DEFAULT_FEEDS
        aliases = self.cfg.get("news", {}).get("aliases") or {}
        self.btn_news.setEnabled(False); self.set_status("Загружаю новости…")
        self.news_worker = NewsWorker(feeds, self.positions, aliases, self.bundle_path())
        self.news_worker.done.connect(self._on_news); self.news_worker.start()

    def _on_news(self, result) -> None:
        self.news_items, failed = result
        if failed:
            log.warning("недоступные RSS: %s", ", ".join(failed))
        self.btn_news.setEnabled(True)
        self.set_status(f"Новости: {len(self.news_items)} по твоим бумагам" + (f", недоступны источники: {len(failed)}" if failed else ""))
        NEWS.save_cache(self.news_items, paths.cache_dir())
        self.check_alerts()                        # новости по бумагам
        self.pipeline_step("news", "идеи")
        if (self.cfg.get("ai") or {}).get("auto", True) and self.ai_conf():
            QTimer.singleShot(0, self.run_ai)
        QTimer.singleShot(0, lambda: self.run_screener(auto=True))
        self._fill_news_filter()
        self.render_news()

    def _fill_news_filter(self) -> None:
        cur = self.news_filter.currentData()
        self.news_filter.blockSignals(True); self.news_filter.clear(); self.news_filter.addItem("Все мои бумаги + макро", "")
        tickers = sorted({t for it in self.news_items for t in it.matched})
        names = {p.ticker: p.name for p in self.positions}
        for t in tickers:
            self.news_filter.addItem(names.get(t, t), t)
        self.news_filter.setCurrentIndex(max(0, self.news_filter.findData(cur))); self.news_filter.blockSignals(False)

    def render_news(self) -> None:
        want = self.news_filter.currentData()
        names = {p.ticker: p.name for p in self.positions}
        items = [it for it in getattr(self, "news_items", []) if not want or want in it.matched]
        today = datetime.now().date()
        for table, subset in ((self.news_tbl, items), (self.news_tbl_small, [i for i in getattr(self, "news_items", []) if i.matched][:12])):
            table.setRowCount(len(subset))
            for r, it in enumerate(subset):
                # бумага: короткий тикер (или два), полное название — в подсказке; макро — серый бейдж
                if it.matched:
                    tag = self._short_ticker(it.matched[0]) + (f" +{len(it.matched) - 1}" if len(it.matched) > 1 else "")
                    tag_tip = "\n".join(names.get(t, t) for t in it.matched)
                    tag_color = "#534AB7"
                else:
                    tag, tag_tip, tag_color = "макро", "Ставка ЦБ, инфляция, рубль, дивиденды", "#7F8C8D"
                short, full, scolor = NEWS.source_badge(it.source)
                pub = it.published.astimezone() if it.published else None    # местное время: у лент свои пояса (Frank — UTC)
                if pub:
                    when = pub.strftime("%H:%M") if pub.date() == today else pub.strftime("%d.%m")
                else:
                    when = "—"
                cells = [(tag, tag_tip, tag_color), (short, full, scolor),
                         (when, pub.strftime("%d.%m.%Y %H:%M") if pub else "", None),
                         (it.title, it.summary or it.title, None)]
                for c, (val, tip, color) in enumerate(cells):
                    item = QTableWidgetItem(val)
                    item.setData(Qt.UserRole, it.link); item.setToolTip(tip)
                    if color:
                        item.setData(Qt.UserRole + 1, color)
                        item.setTextAlignment(Qt.AlignCenter)
                    if c == 2:
                        item.setForeground(QColor("gray"))
                    table.setItem(r, c, item)
            table.resizeRowsToContents()
        if not items:
            self.lbl_news.setText("Лента подгружается автоматически после обновления портфеля. Источники и алиасы — Настройки → Новости.")
        else:
            self.lbl_news.setText("Двойной клик по строке — открыть в браузере; при наведении — аннотация.")

    def _short_ticker(self, t: str) -> str:
        """Короткая метка для бейджа: для ISIN (ЗПИФ, облигации) — хвост названия («ЗПИФ Фонд-СКЛ» → «СКЛ»)."""
        if len(t) <= 6:
            return t
        name = next((p.name for p in self.positions if p.ticker == t), "")
        if name:
            tail = re.split(r"[\s\-—]+", name.strip())[-1]
            if 2 <= len(tail) <= 7:
                return tail.upper()
        return t[:6]

    def _open_news_from(self, table, item) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        link = table.item(item.row(), 0).data(Qt.UserRole)
        if link:
            QDesktopServices.openUrl(QUrl(link))

    # ---- ИИ-сводка ----------------------------------------------------------

    def ai_conf(self) -> "AI.AIConfig | None":
        """Настройки ИИ + ключ из хранилища; None — ключа нет (ИИ выключен)."""
        return AI.resolve(self.cfg.get("ai"))

    def run_ai(self, manual: bool = False) -> None:
        conf = self.ai_conf()
        items = getattr(self, "news_items", [])
        if not items:
            if manual:
                self.set_status("Сначала должна загрузиться лента новостей.")
            return
        if conf is None:
            msg = "ИИ-сводка: ключ не задан — Настройки → ИИ."
            self.set_status(msg); self.ai_panel.setPlainText(msg); return
        if is_running(getattr(self, "ai_worker", None)):
            return
        self.btn_ai.setEnabled(False); self.set_status(f"ИИ-сводка ({conf.provider})…")
        for p in (self.ai_panel, self.ai_small):
            p.spinner.start("Модель читает новости — показана прошлая сводка")
        cands = (getattr(self, "_last_screen", None).ideas[:15] if getattr(self, "_last_screen", None) else [])
        self.ai_worker = AIWorker(conf, items, self.positions, paths.cache_dir(), manual, cands)
        self.ai_worker.done.connect(self._on_ai)
        self.ai_worker.failed.connect(lambda m: self._on_ai_failed(m, conf))
        self.ai_worker.start()

    def _on_ai_failed(self, msg: str, conf: "AI.AIConfig") -> None:
        """Ошибка свежего запроса: называем модель, к которой ходили, и чья сводка осталась на экране — иначе
        шапка прошлой сводки («openrouter/free») выглядит так, будто настройка модели не применилась."""
        model = conf.model or AI.default_model(conf.provider)
        log.error("ИИ-сводка (%s/%s): %s", conf.provider, model, msg)
        self.btn_ai.setEnabled(True)
        for p in (self.ai_panel, self.ai_small):
            p.spinner.stop()
        old = getattr(self, "ai_summary", None) or AI.load_summary(paths.cache_dir())
        shown = (f" — показана прошлая сводка ({old.model}, {old.at.replace('T', ' ')})" if old is not None
                 else "")
        self.set_status(f"ИИ-сводка ({model}) не удалась: {msg[:160]}{shown}")

    def _on_ai(self, result) -> None:
        summary, note = result if isinstance(result, tuple) else (result, "")
        self.btn_ai.setEnabled(True)
        for p in (self.ai_panel, self.ai_small):
            p.spinner.stop()
        self.ai_summary, self._ai_note = summary, note
        AI.save_summary(summary, paths.cache_dir())
        log.info("ИИ-сводка: %s/%s, бумаг %d, %s", summary.provider, summary.model, len(summary.items), note or "свежий запрос")
        self.set_status(f"ИИ-сводка {'из кэша' if note else 'готова'} ({summary.provider}).")
        self.render_ai()
        res = getattr(self, "_last_screen", None)
        if res is not None and res.ideas:
            self._on_ideas(res)     # тональность из свежей сводки подхватится в annotate_ideas
        force = getattr(self, "_exec_force", False); self._exec_force = False
        QTimer.singleShot(0, lambda: self.run_exec(manual=force))

    def render_ai(self) -> None:
        s = getattr(self, "ai_summary", None) or AI.load_summary(paths.cache_dir())
        if s is None:
            txt = "ИИ-сводка появится после загрузки новостей (Настройки → ИИ: провайдер и ключ)."
            self.ai_panel.setPlainText(txt); self.ai_small.setPlainText(txt); return
        html = AI.render_html(s, {p.ticker: p.name for p in self.positions}, self.privacy, getattr(self, "_ai_note", ""))
        self.ai_panel.setHtml(html); self.ai_small.setHtml(html)
