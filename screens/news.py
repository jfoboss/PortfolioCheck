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
import secrets_store as SEC
from diagnostics import log
import news as NEWS
from workers import NewsWorker, AIWorker


class NewsMixin:
    # ---- новости ------------------------------------------------------------

    def refresh_news(self) -> None:
        if getattr(self, "news_worker", None) is not None and self.news_worker.isRunning():
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
        NEWS.save_cache(self.news_items, paths.data_dir() / "cache")
        self.check_alerts()                        # новости по бумагам
        self.pipeline_step("news", "идеи")
        if (self.cfg.get("ai") or {}).get("auto", True) and self.ai_key((self.cfg.get("ai") or {}).get("provider", "openrouter")):
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
                if it.published:
                    when = it.published.strftime("%H:%M") if it.published.date() == today else it.published.strftime("%d.%m")
                else:
                    when = "—"
                cells = [(tag, tag_tip, tag_color), (short, full, scolor),
                         (when, it.published.strftime("%d.%m.%Y %H:%M") if it.published else "", None),
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

    def ai_key(self, provider: str) -> str | None:
        if provider == "ollama":
            return "-"
        return SEC.get("portfolio-check.ai", provider)

    def run_ai(self, manual: bool = False) -> None:
        cfg = self.cfg.get("ai") or {}
        provider = cfg.get("provider", "openrouter"); key = self.ai_key(provider)
        items = getattr(self, "news_items", [])
        if not items:
            if manual:
                self.set_status("Сначала должна загрузиться лента новостей.")
            return
        if not key:
            msg = "ИИ-сводка: ключ не задан — Настройки → ИИ."
            self.set_status(msg); self.ai_panel.setPlainText(msg); return
        if getattr(self, "ai_worker", None) is not None and self.ai_worker.isRunning():
            return
        self.btn_ai.setEnabled(False); self.set_status(f"ИИ-сводка ({provider})…")
        for p in (self.ai_panel, self.ai_small):
            p.spinner.start("Модель читает новости — показана прошлая сводка")
        cands = (getattr(self, "_last_screen", None).ideas[:15] if getattr(self, "_last_screen", None) else [])
        self.ai_worker = AIWorker(provider, key, cfg.get("model", ""), items, self.positions, cfg.get("proxy", ""), cfg.get("base_url", ""),
                                  paths.data_dir() / "cache", float(cfg.get("min_interval_h", 6)), manual, cands)
        self.ai_worker.done.connect(self._on_ai)
        self.ai_worker.failed.connect(lambda m: (log.error("ИИ-сводка: %s", m), self.btn_ai.setEnabled(True),
                                                 [p.spinner.stop() for p in (self.ai_panel, self.ai_small)],
                                                 self.set_status(f"ИИ-сводка не удалась: {m[:120]} — показана прошлая сводка")))
        self.ai_worker.start()

    def _on_ai(self, result) -> None:
        summary, note = result if isinstance(result, tuple) else (result, "")
        self.btn_ai.setEnabled(True)
        for p in (self.ai_panel, self.ai_small):
            p.spinner.stop()
        self.ai_summary, self._ai_note = summary, note
        AI.save_summary(summary, paths.data_dir() / "cache")
        log.info("ИИ-сводка: %s/%s, бумаг %d, %s", summary.provider, summary.model, len(summary.items), note or "свежий запрос")
        self.set_status(f"ИИ-сводка {'из кэша' if note else 'готова'} ({summary.provider}).")
        self.render_ai()
        res = getattr(self, "_last_screen", None)
        if res is not None and res.ideas:
            self._on_ideas(res)     # тональность из свежей сводки подхватится в annotate_ideas
        force = getattr(self, "_exec_force", False); self._exec_force = False
        QTimer.singleShot(0, lambda: self.run_exec(manual=force))

    def render_ai(self) -> None:
        s = getattr(self, "ai_summary", None) or AI.load_summary(paths.data_dir() / "cache")
        if s is None:
            txt = "ИИ-сводка появится после загрузки новостей (Настройки → ИИ: провайдер и ключ)."
            self.ai_panel.setPlainText(txt); self.ai_small.setPlainText(txt); return
        html = AI.render_html(s, {p.ticker: p.name for p in self.positions}, self.privacy, getattr(self, "_ai_note", ""))
        self.ai_panel.setHtml(html); self.ai_small.setHtml(html)
