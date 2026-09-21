#!/usr/bin/env python3
"""
app.py — десктопное окно над portfolio_check.py (macOS, PySide6).

  Вкладки: Сводка (доли vs цели) · Сигналы (правила) · Позиции (таблица) · Отчёт (markdown)
  Все настройки (цели, классификация, правила, токен, сертификаты) — через «Настройки…».
  Конфиг живёт в ~/Library/Application Support/PortfolioCheck/config.yaml и пишется только приложением.
  Токен хранится в macOS Keychain (keyring), не в файлах.
  Загрузка портфеля — в фоновом потоке, окно не блокируется.
  «Файл → Открыть снапшот» — просмотр сохранённого JSON без сети (и для демо).

Запуск из исходников:  python app.py [--config config.yaml] [--snapshot snap.json]
Сборка .app:           см. build_mac.sh
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer, QSettings
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QIcon
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QGraphicsBlurEffect, QButtonGroup, QRadioButton, QListWidget, QListWidgetItem, QCheckBox,
                               QDoubleSpinBox, QSpinBox, QComboBox)
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel, QProgressBar,
    QMessageBox, QFileDialog, QHeaderView, QSystemTrayIcon, QStyle, QToolBar, QStatusBar, QSplitter,
)

import portfolio_check as pc
from settings_dialog import SettingsDialog, KEYRING_SERVICE, KEYRING_USER
from forecast import ForecastTab
from charts import PieChart, BarChart, RangeBarChart, DivergingBarChart, TreemapChart, LineChart
import paths
import history as H
import diagnostics as DIAG
import ai as AI
import secrets_store as SEC
import strategy as ST
from diagnostics import log
import news as NEWS
import screener as SCR

APP_NAME = "PortfolioCheck"
# Полупрозрачные системно-нейтральные оттенки: читаются и на светлом, и на тёмном фоне
TINT_GOOD = QColor(52, 199, 89, 60)     # системный зелёный
TINT_WARN = QColor(255, 159, 10, 70)    # системный оранжевый
TINT_BAD = QColor(255, 69, 58, 60)      # системный красный
MASK, mask_text = pc.MASK, pc.mask_text

SUPPORT_DIR = paths.LOCAL_DIR          # локальное: storage.json (указатель на папку данных)
DATA_DIR = paths.data_dir()            # данные: config.yaml, ca-bundle.pem, snapshots/ — iCloud или локально


# ---------------------------------------------------------------------------
# Токен в Keychain
# ---------------------------------------------------------------------------

def get_token() -> str | None:
    """Токен T-Invest: задаётся только через Настройки → Доступ; Keychain читается один раз за сессию."""
    return SEC.get(KEYRING_SERVICE, KEYRING_USER)


def ensure_config(path: Path) -> None:
    """Первый запуск: если конфига в Application Support нет — копируем дефолтный из бандла/исходников."""
    if path.exists():
        return
    default = Path(__file__).with_name("config.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    if default.exists():
        path.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        pc.save_config({"account": {"id": ""}, "targets": {}, "instruments": {}, "fx_fallback": {},
                        "rules": {"drawdown_alert_pct": 10, "buy_candidates_classes": [], "frozen": {},
                                  "position_caps": {}, "single_name_max_pct": 11, "tail_threshold_rub": 30000}},
                       str(path))


# ---------------------------------------------------------------------------
# Фоновая загрузка
# ---------------------------------------------------------------------------

from PySide6.QtWidgets import QStyledItemDelegate
from PySide6.QtGui import QPainter, QPen, QBrush


class BusySpinner(QWidget):
    """Маленький индикатор «идёт обновление» в углу панели: контент под ним остаётся читаемым."""
    FRAMES = "◐◓◑◒"

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._i = 0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self.setFixedSize(18, 18); self.hide()

    def start(self, tip: str = "Обновляется…") -> None:
        self.setToolTip(tip); self._reposition(); self.show(); self.raise_()
        if not self._timer.isActive():
            self._timer.start(120)

    def stop(self) -> None:
        self._timer.stop(); self.hide()

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self.FRAMES); self._reposition(); self.update()

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p:
            self.move(max(p.width() - self.width() - 8, 0), 6)

    def showEvent(self, e) -> None:
        self._reposition(); super().showEvent(e)

    def paintEvent(self, _) -> None:
        painter = QPainter(self)
        c = self.palette().text().color(); c.setAlpha(150); painter.setPen(c)
        f = painter.font(); f.setPointSize(13); painter.setFont(f)
        painter.drawText(self.rect(), Qt.AlignCenter, self.FRAMES[self._i])


class BadgeDelegate(QStyledItemDelegate):
    """Рисует ячейку как цветной бейдж (цвет — в Qt.UserRole + 1, текст — как есть)."""

    def paint(self, painter: QPainter, option, index) -> None:
        color = index.data(Qt.UserRole + 1)
        text = index.data(Qt.DisplayRole) or ""
        if not color or not text:
            return super().paint(painter, option, index)
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(4, 4, -4, -4)
        painter.setPen(Qt.NoPen); painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(r, 5, 5)
        painter.setPen(QPen(QColor("white")))
        f = painter.font(); f.setBold(True); f.setPointSizeF(max(9.0, f.pointSizeF() - 1)); painter.setFont(f)
        painter.drawText(r, Qt.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtGui import QFontMetrics
        s = super().sizeHint(option, index)
        f = option.font; f.setBold(True)
        s.setWidth(QFontMetrics(f).horizontalAdvance(index.data(Qt.DisplayRole) or "") + 22)
        return s


class FairPriceWorker(QThread):
    """Пересчёт цен эталонов для заблокированных после правки настроек — без перезагрузки портфеля."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list):
        super().__init__()
        self.token, self.cfg, self.positions = token, cfg, positions

    def run(self) -> None:
        try:
            self.done.emit(pc.refresh_fair_prices(self.token, self.cfg, self.positions))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class HistoryWorker(QThread):
    """Свечи по всем позициям (с кэшем) → доходности по периодам и восстановленная история портфеля."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list, cache_dir: Path):
        super().__init__()
        self.token, self.cfg, self.positions, self.cache_dir = token, cfg, positions, cache_dir

    def run(self) -> None:
        try:
            Client, *_ = pc._sdk()
            pc._setup_grpc_roots()
            candles: dict[str, list] = {}
            with Client(self.token) as client:
                fx = {k: float(v) for k, v in pc.fetch_fx(client, self.cfg.get("fx_fallback", {})).items()}
                for p in self.positions:
                    if self.isInterruptionRequested():
                        return
                    if p.figi and p.instrument_type in ("share", "etf", "bond", "currency"):
                        candles[p.figi] = H.fetch_candles(client, p.figi, self.cache_dir)
            changes = {p.figi: H.period_changes(candles.get(p.figi, []), float(p.cur_price)) for p in self.positions}
            empty = [p.ticker for p in self.positions if not candles.get(p.figi)]
            hist = H.backfilled_history(self.positions, candles, fx)
            self.done.emit((changes, hist, empty))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class NewsWorker(QThread):
    done = Signal(object)

    def __init__(self, feeds: list, positions: list, aliases: dict, bundle: Path):
        super().__init__()
        self.feeds, self.positions, self.aliases, self.bundle = feeds, positions, aliases, bundle

    def run(self) -> None:
        self.done.emit(NEWS.fetch_news(self.feeds, self.positions, self.aliases, self.bundle))


class LongHistoryWorker(QThread):
    """Свечи за несколько лет по позициям портфеля → среднегодовой рост цены (CAGR) по каждой бумаге."""
    done = Signal(object)          # ({figi: cagr %}, покрытые годы, сколько бумаг без истории)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list, cache_dir: Path, years: float):
        super().__init__()
        self.token, self.cfg, self.positions, self.cache_dir, self.years = token, cfg, positions, cache_dir, years

    def run(self) -> None:
        try:
            Client, *_ = pc._sdk(); pc._setup_grpc_roots()
            out, covered, missing = {}, [], 0
            days = int(self.years * 365) + 30
            with Client(self.token) as client:
                for i, p in enumerate(self.positions):
                    if self.isInterruptionRequested():
                        return
                    if not p.figi or p.instrument_type not in ("share", "etf", "bond", "currency"):
                        continue
                    self.progress.emit(f"История {self.years:.0f} г: {i + 1}/{len(self.positions)} ({p.ticker})")
                    series = H.fetch_candles(client, p.figi, self.cache_dir, days=days)
                    val, cov = H.cagr(series, float(p.cur_price), self.years)
                    if val is None:
                        missing += 1
                    else:
                        out[p.figi] = val; covered.append(cov)
            self.done.emit((out, (sum(covered) / len(covered)) if covered else 0.0, missing))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {str(e)[:200]}")


class IdeasEnrichWorker(QThread):
    """Догрузка свечей по кандидатам скринера: изменение за месяц. Как для позиций — в фоне, с кэшем."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, ideas: list, cache_dir: Path, limit: int = 40):
        super().__init__()
        self.token, self.ideas, self.cache_dir, self.limit = token, ideas, cache_dir, limit

    def run(self) -> None:
        try:
            Client, *_ = pc._sdk(); pc._setup_grpc_roots()
            out = {}
            with Client(self.token) as client:
                for idea in self.ideas[:self.limit]:
                    if self.isInterruptionRequested():
                        return
                    if not idea.uid:
                        continue
                    series = H.fetch_candles(client, idea.uid, self.cache_dir, days=120, by_uid=True)
                    out[idea.ticker] = H.period_changes(series, idea.price).get("month")
            self.done.emit(out)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {str(e)[:200]}")


class ScreenerWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, token: str, min_yield: float, min_cap: float, max_debt: float, exclude: set, cache_dir: Path, force: bool = False):
        super().__init__()
        self.token, self.min_yield, self.min_cap, self.max_debt, self.exclude, self.cache_dir = token, min_yield, min_cap, max_debt, exclude, cache_dir
        self.force = force
        self.filters = None

    def run(self) -> None:
        try:
            def prog(i, n, t):
                if self.isInterruptionRequested():
                    raise InterruptedError("остановлено при выходе")
                self.progress.emit(f"Скринер: {i}/{n} ({t})")
            res = SCR.screen(self.token, self.min_yield, self.min_cap, self.exclude, self.max_debt, progress=prog,
                             cache_dir=self.cache_dir, cache_hours=0 if self.force else 24, filters=self.filters)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class AIWorker(QThread):
    done = Signal(object)      # (Summary, note)
    failed = Signal(str)

    def __init__(self, provider, key, model, news_items, positions, proxy, base_url, cache_dir, min_interval_h, force, candidates=None):
        super().__init__()
        self.args = (provider, key, model, news_items, positions, proxy, base_url)
        self.cache_dir, self.min_interval_h, self.force, self.candidates = cache_dir, min_interval_h, force, candidates or []

    def run(self) -> None:
        provider, key, model, news_items, positions, proxy, base_url = self.args
        cache = AI.ResponseCache(self.cache_dir)
        fp = AI.news_fingerprint(news_items, provider, model, self.candidates)
        subj = AI.subjects_fingerprint(positions, self.candidates)
        hit, why = cache.get("news", fp, self.min_interval_h, self.force, subj)
        if hit:
            self.done.emit((AI.Summary(**hit), f"из кэша: {why}")); return
        try:
            s = AI.summarize(*self.args, candidates=self.candidates)
            cache.put("news", fp, __import__("dataclasses").asdict(s), subj)
            self.done.emit((s, ""))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {str(e)[:300]}")


class ExecWorker(QThread):
    """Executive summary по всем экранам по очереди: (kind, facts) → ExecSummary."""
    one = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, provider, key, model, proxy, base_url, jobs: list, cache_dir, min_interval_h, force):
        super().__init__()
        self.provider, self.key, self.model, self.proxy, self.base_url, self.jobs = provider, key, model, proxy, base_url, jobs
        self.cache_dir, self.min_interval_h, self.force = cache_dir, min_interval_h, force

    def run(self) -> None:
        cache = AI.ResponseCache(self.cache_dir)
        for kind, facts in self.jobs:
            if self.isInterruptionRequested():
                return
            fp = AI.exec_fingerprint(kind, facts, self.provider, self.model)
            hit, why = cache.get(f"exec_{kind}", fp, self.min_interval_h, self.force)
            if hit:
                self.one.emit((AI.ExecSummary(**hit), f"из кэша: {why}")); continue
            try:
                s = AI.exec_summary(kind, facts, self.provider, self.key, self.model, self.proxy, self.base_url)
                cache.put(f"exec_{kind}", fp, __import__("dataclasses").asdict(s))
                self.one.emit((s, ""))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"{kind}: {type(e).__name__}: {str(e)[:200]}")
        self.done.emit()


class LoadWorker(QThread):
    done = Signal(object)      # (positions, income_estimate)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict):
        super().__init__()
        self.token, self.cfg = token, cfg

    def run(self) -> None:
        try:
            self.done.emit(pc.load_portfolio_full(self.token, self.cfg))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Окно
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        try:
            self.cfg = pc.load_config(config_path)
        except Exception as e:  # noqa: BLE001
            log.error("конфиг %s не прочитан: %s — использую дефолтный", config_path, e)
            self.config_path = str(paths.LOCAL_DIR / "config.yaml")
            ensure_config(Path(self.config_path))
            self.cfg = pc.load_config(self.config_path)
        self.positions: list[pc.Position] = []
        self.worker: LoadWorker | None = None
        self.settings_win: SettingsDialog | None = None
        self._manual_refresh = False
        self.privacy = bool(self.cfg.get("refresh", {}).get("privacy_on_launch", True))
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(lambda: self.refresh(manual=False))

        self.setWindowTitle("Портфель — проверка правил")
        scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        if scr is not None:
            self.resize(min(1400, scr.width() - 40), min(900, scr.height() - 60))
            self.setMinimumSize(min(900, scr.width() - 40), min(600, scr.height() - 60))
        else:
            self.resize(1100, 720)
        self._build_menu()
        self._build_ui()
        self._build_tray()
        self.set_privacy(self.privacy)
        self.render_ai(); self.render_exec_cached(); self.update_ai_visibility()
        self.apply_refresh_schedule()
        if paths.fallback_reason:
            log.warning(paths.fallback_reason)
            self.show_banner(
                "<b>Нет доступа к iCloud Drive — работаю с локальной копией настроек.</b><br>"
                "macOS не выдала приложению разрешение на папку iCloud Drive. Самый надёжный способ: "
                "<b>Системные настройки → Конфиденциальность и безопасность → Полный доступ к диску</b> → добавить PortfolioCheck "
                "(кнопка «+» или перетащить приложение из папки Программы) → включить. Затем «Проверить снова» "
                "(или перезапустить приложение).",
                "Открыть настройки доступа", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
        self.restore_layout()                      # размер окна, положение разделителей, активный экран
        if self.needs_setup():
            QTimer.singleShot(400, lambda: self.run_wizard(first=True))
        self.load_from_disk()                      # мгновенно: последний снапшот, свечи, новости, сводки — без сети
        if self.cfg.get("refresh", {}).get("on_launch", True):
            if self._ready_for_api():
                if self._snapshot_is_stale():
                    QTimer.singleShot(300, lambda: self.refresh(manual=False))
                else:
                    self.set_status(f"Данные свежие ({self._snapshot_age_text()}) — обновление по расписанию.")
            else:
                QTimer.singleShot(300, self._explain_not_ready)

    # ---- UI -----------------------------------------------------------------

    def _build_menu(self) -> None:
        # «Настройки…» уезжает в меню приложения (⌘,) — macOS делает это сам по роли действия
        self.act_prefs = QAction("Настройки…", self)
        self.act_prefs.setMenuRole(QAction.PreferencesRole)
        self.act_prefs.setShortcut(QKeySequence.Preferences if not QKeySequence(QKeySequence.Preferences).isEmpty()
                                   else QKeySequence("Ctrl+,"))
        self.act_prefs.triggered.connect(self.open_settings)

        self.act_refresh = QAction("Обновить", self)
        self.act_refresh.setShortcut(QKeySequence.Refresh)
        self.act_refresh.triggered.connect(lambda: self.refresh(manual=True))

        self.act_privacy = QAction("Скрыть суммы", self)
        self.act_privacy.setCheckable(True)
        self.act_privacy.setChecked(self.privacy)
        self.act_privacy.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.act_privacy.setToolTip("Спрятать размер портфеля и суммы (⌘⇧H). Проценты и названия остаются.")
        self.act_privacy.toggled.connect(self.set_privacy)

        act_open = QAction("Открыть снапшот…", self); act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.open_snapshot)
        act_save = QAction("Сохранить снапшот…", self); act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.save_snapshot)

        m_file = self.menuBar().addMenu("Файл")
        act_wizard = QAction("Мастер настройки…", self); act_wizard.triggered.connect(lambda: self.run_wizard(first=False))
        m_file.addAction(act_wizard); m_file.addSeparator()
        m_file.addAction(act_open); m_file.addAction(act_save)
        m_file.addSeparator(); m_file.addAction(self.act_prefs)
        m_view = self.menuBar().addMenu("Вид")
        m_view.addAction(self.act_refresh)
        m_view.addSeparator(); m_view.addAction(self.act_privacy)
        act_exec = QAction("ИИ-сводки по экранам", self); act_exec.setShortcut(QKeySequence("Ctrl+Shift+I"))
        act_exec.triggered.connect(lambda: self.run_exec(manual=True)); m_view.addSeparator(); m_view.addAction(act_exec)

    def _build_ui(self) -> None:
        tb = QToolBar("Основная")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(18, 18))
        self.act_refresh.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.act_prefs.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.act_privacy.setIcon(self.style().standardIcon(QStyle.SP_DialogNoButton))
        tb.addAction(self.act_refresh)
        tb.addAction(self.act_privacy)
        tb.addAction(self.act_prefs)
        self.addToolBar(tb)
        self.setUnifiedTitleAndToolBarOnMac(True)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100); self.bar.setValue(0); self.bar.setVisible(False)
        self.bar.setMaximumWidth(220); self.bar.setTextVisible(True); self.bar.setFormat("%p%")
        self.lbl_pipeline = QLabel(); self.lbl_pipeline.setStyleSheet("color: gray")
        sb = QStatusBar(); sb.addPermanentWidget(self.lbl_pipeline); sb.addPermanentWidget(self.bar)
        self.setStatusBar(sb)
        self.set_status("Нет данных — нажми «Обновить» или открой снапшот.")

        # Баннер для проблем, которые нужно решить руками (доступ к папке данных и т.п.)
        self.banner = QWidget(); self.banner.setAutoFillBackground(True)
        pal = self.banner.palette(); pal.setColor(QPalette.Window, QColor(255, 159, 10, 45)); self.banner.setPalette(pal)
        bl = QHBoxLayout(self.banner); bl.setContentsMargins(12, 8, 12, 8)
        self.banner_text = QLabel(); self.banner_text.setWordWrap(True); self.banner_text.setTextFormat(Qt.RichText)
        self.banner_btn = QPushButton(); self.banner_btn.clicked.connect(self._banner_action)
        self.banner_retry = QPushButton("Проверить снова"); self.banner_retry.clicked.connect(self._banner_retry)
        bl.addWidget(self.banner_text, 1); bl.addWidget(self.banner_btn); bl.addWidget(self.banner_retry)
        self.banner.hide()

        self.tabs = QTabWidget()

        # ================= виджеты (те же, что раньше — только компоновка другая) =================
        self.tbl_classes = self._table(["Класс", "Сумма, ₽", "Доля", "Цель", "Статус"])
        self.tbl_classes.setSortingEnabled(False)
        self.chart_ranges = RangeBarChart("Доли классов и целевые коридоры")
        self.chart_rebalance = DivergingBarChart("Докупить / продать (до границы коридора)")
        self.txt_signals = self._text()
        self.tbl_pos = self._table(["Инструмент", "Тикер", "Класс", "Отрасль", "Кол-во", "Средняя", "Текущая", "Стоимость, ₽", "P/L %"])
        self.pie_currency = PieChart("По валюте и металлам")
        self.pie_companies = PieChart("По компаниям", max_slices=12)
        self.pie_sectors = PieChart("По отраслям")
        self.txt_report = self._text()
        self.lbl_blocked = QLabel(); self.lbl_blocked.setWordWrap(True); self.lbl_blocked.setTextFormat(Qt.RichText)
        self.tbl_blocked = self._table(["Инструмент", "Эталон", "Кол-во", "У брокера, ₽/шт", "Внебиржа, ₽/шт", "Реальная, ₽/шт",
                                        "У брокера, ₽", "Внебиржа, ₽", "Реальная, ₽", "Потеря при продаже, ₽", "Потеря, %"])
        self.bar_blocked = BarChart("По позициям: у брокера · внебиржа · реальная")
        self.pie_blocked = PieChart("Реальная стоимость: вернёшь / потеряешь", max_slices=3)
        self.treemap = TreemapChart()
        self.treemap_small = TreemapChart(); self.treemap_small.setMinimumHeight(110)
        self.lbl_heat = QLabel(); self.lbl_heat.setStyleSheet("color: gray")
        self.chart_history = LineChart("Стоимость портфеля", "₽", zero_based=False)
        self.lbl_history = QLabel(); self.lbl_history.setWordWrap(True); self.lbl_history.setStyleSheet("color: gray")
        self.news_tbl = self._news_table(); self.news_tbl_small = self._news_table()
        self.lbl_news = QLabel(); self.lbl_news.setWordWrap(True); self.lbl_news.setStyleSheet("color: gray")
        self.tbl_ideas = self._table(["Компания", "Тикер", "Отрасль", "Мес.", "Новости", "Цена", "Кап., млрд", "Дивдоходность",
                                      "Дивдох. 5 лет", "Дивиденды, ₽", "P/E", "Долг/EBITDA", "ROE", "Free float", "Экс-дивиденд"])
        self.tbl_ideas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_ideas.customContextMenuRequested.connect(self._ideas_menu)
        self.btn_blacklist = QPushButton("Исключить"); self.btn_blacklist.clicked.connect(self.blacklist_selected)
        self.btn_blacklist.setToolTip("В чёрный список скринера с причиной (Настройки → Скринер)")
        self.lbl_ideas = QLabel("Скринер ещё не запускался — он идёт последним шагом фоновой загрузки (проход по T-Invest занимает ~10–20 секунд). "
                                "Или нажми «Найти кандидатов».")
        self.lbl_ideas.setWordWrap(True); self.lbl_ideas.setStyleSheet("color: gray")

        # период тепловой карты
        self.heat_group = QButtonGroup(self)
        heat_row = QHBoxLayout(); heat_row.addWidget(QLabel("Период:"))
        for key, label, _ in H.PERIODS + [("all", "Всё время", 0), ("div", "Дивиденды за 12 мес", 0)]:
            rb = QRadioButton(label); rb.setProperty("period", key); self.heat_group.addButton(rb); heat_row.addWidget(rb)
            if key == "day":
                rb.setChecked(True)
        self.cb_group_sectors = QCheckBox("группировать по отраслям"); self.cb_group_sectors.setChecked(True)
        self.cb_group_sectors.toggled.connect(lambda _: self.render_heatmap())
        heat_row.addWidget(self.cb_group_sectors); heat_row.addStretch()
        self.heat_group.buttonClicked.connect(lambda _: self.render_heatmap())

        # новости: фильтр и кнопка
        self.news_filter = QComboBox(); self.news_filter.addItem("Все мои бумаги + макро", "")
        self.news_filter.currentIndexChanged.connect(lambda _: self.render_news())
        self.btn_news = QPushButton("Обновить"); self.btn_news.setToolTip("Перечитать RSS-источники")
        self.btn_news.clicked.connect(self.refresh_news)
        # скринер: фильтры и кнопка
        self.sp_min_yield = QDoubleSpinBox(); self.sp_min_yield.setRange(0, 50); self.sp_min_yield.setSuffix(" %"); self.sp_min_yield.setValue(8)
        self.sp_min_cap = QSpinBox(); self.sp_min_cap.setRange(0, 100000); self.sp_min_cap.setSuffix(" млрд ₽"); self.sp_min_cap.setValue(50)
        self.sp_max_debt = QDoubleSpinBox(); self.sp_max_debt.setRange(0, 20); self.sp_max_debt.setDecimals(1); self.sp_max_debt.setValue(3.0)
        self.sp_max_debt.setSpecialValueText("любой"); self.sp_max_debt.setToolTip("Чистый долг / EBITDA — 0 = не фильтровать")
        self.btn_screen = QPushButton("Найти"); self.btn_screen.setToolTip("Пройти по акциям TQBR заново (минуя кэш)")
        self.btn_screen.clicked.connect(lambda: self.run_screener(auto=False))

        # панели executive summary (по одной на экран)
        self.exec_panels = {}
        for kind in ("overview", "portfolio", "market", "strategy"):
            t = self._text(); t.setFont(QFont(self.font().family(), 12)); t.setMaximumHeight(150)
            t.setPlainText("Сводка появится после загрузки данных (Настройки → ИИ).")
            t.setPlaceholderText("")
            self.exec_panels[kind] = t

        # ================= экран 1: Обзор =================
        self.kpi = {}
        kpi_row = QHBoxLayout(); kpi_row.setSpacing(8)
        for key, title in (("total", "Стоимость"), ("day", "За день"), ("month", "За месяц"), ("income", "Доход 12 мес"),
                           ("signals", "Сигналы"), ("goal", "Цель")):
            card = self._doc_panel(QWidget()); vl = QVBoxLayout(card); vl.setContentsMargins(12, 8, 12, 8); vl.setSpacing(2)
            t = QLabel(title); t.setStyleSheet("color: gray; font-size: 11px")
            v = QLabel("—"); v.setStyleSheet("font-size: 18px; font-weight: 600")
            s = QLabel(""); s.setStyleSheet("color: gray; font-size: 11px")
            vl.addWidget(t); vl.addWidget(v); vl.addWidget(s)
            self.kpi[key] = (v, s); kpi_row.addWidget(card, 1)
        kpi_w = QWidget(); kpi_w.setLayout(kpi_row)

        left = QSplitter(Qt.Vertical); left.setObjectName("overview_left")
        left.addWidget(self.tbl_classes)
        sum_charts = self._doc_panel(QWidget()); scl = QHBoxLayout(sum_charts); scl.setContentsMargins(4, 4, 4, 4)
        scl.addWidget(self.chart_ranges, 1); scl.addWidget(self.chart_rebalance, 1)
        left.addWidget(sum_charts); left.setStretchFactor(0, 3); left.setStretchFactor(1, 2)
        right = QSplitter(Qt.Vertical); right.setObjectName("overview_right")
        sig_w = QWidget(); sl = QVBoxLayout(sig_w); sl.setContentsMargins(0, 0, 0, 0)
        self.lbl_signals_title = QLabel("<b>Сигналы</b>"); sl.addWidget(self.lbl_signals_title); sl.addWidget(self.txt_signals, 1)
        right.addWidget(sig_w)
        nw = QWidget(); nwl = QVBoxLayout(nw); nwl.setContentsMargins(0, 0, 0, 0)
        self.ai_small = self._text(); self.ai_small.setFont(QFont(self.font().family(), 12)); self.ai_small.setMaximumHeight(170)
        self.lbl_ai_small_title = QLabel("<b>ИИ-сводка по новостям</b>")
        nwl.addWidget(self.lbl_ai_small_title); nwl.addWidget(self.ai_small)
        nwl.addWidget(QLabel("<b>Новости по бумагам</b>")); nwl.addWidget(self.news_tbl_small, 1)
        right.addWidget(nw)
        mid = QSplitter(Qt.Horizontal); mid.setObjectName("overview_mid"); mid.addWidget(left); mid.addWidget(right)
        mid.setStretchFactor(0, 3); mid.setStretchFactor(1, 2)
        heat_small = self._doc_panel(QWidget()); hsl = QVBoxLayout(heat_small); hsl.setContentsMargins(4, 4, 4, 4)
        hsl.addWidget(QLabel("<b>Тепловая карта — день</b>")); hsl.addWidget(self.treemap_small, 1)
        overview = QWidget(); ovl = QVBoxLayout(overview); ovl.setContentsMargins(8, 8, 8, 8); ovl.setSpacing(8)
        ov_split = QSplitter(Qt.Vertical); ov_split.setObjectName("overview"); ov_split.addWidget(mid); ov_split.addWidget(heat_small)
        ov_split.setStretchFactor(0, 3); ov_split.setStretchFactor(1, 1)
        ovl.addWidget(kpi_w); ovl.addWidget(self.exec_panels["overview"]); ovl.addWidget(ov_split, 1)
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea(); scroll.setWidget(overview); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        overview.setMinimumHeight(560)
        self.tabs.addTab(scroll, "Обзор")

        # ================= экран 2: Портфель =================
        pies = self._doc_panel(QWidget()); pl = QVBoxLayout(pies); pl.setContentsMargins(4, 4, 4, 4)
        for pie in (self.pie_currency, self.pie_companies, self.pie_sectors):
            pl.addWidget(pie, 1)
        pos_top = QSplitter(Qt.Horizontal); pos_top.addWidget(self.tbl_pos); pos_top.addWidget(pies)
        pos_top.setStretchFactor(0, 3); pos_top.setStretchFactor(1, 1)
        blocked_w = self._doc_panel(QWidget()); bl = QVBoxLayout(blocked_w); bl.setContentsMargins(8, 8, 8, 8)
        bl.addWidget(self.lbl_blocked)
        b_split = QSplitter(Qt.Horizontal); b_split.setObjectName("blocked"); b_split.addWidget(self.tbl_blocked)
        b_charts = QWidget(); bcl = QHBoxLayout(b_charts); bcl.setContentsMargins(0, 0, 0, 0)
        bcl.addWidget(self.bar_blocked, 3); bcl.addWidget(self.pie_blocked, 2)
        b_split.addWidget(b_charts); b_split.setStretchFactor(0, 3); b_split.setStretchFactor(1, 2)
        bl.addWidget(b_split, 1)
        self.pos_split = QSplitter(Qt.Vertical); self.pos_split.setObjectName("portfolio")
        self.pos_split.addWidget(pos_top); self.pos_split.addWidget(blocked_w)
        self.pos_split.setStretchFactor(0, 3); self.pos_split.setStretchFactor(1, 2)
        pf = QWidget(); pfl = QVBoxLayout(pf); pfl.setContentsMargins(8, 8, 8, 8)
        pfl.addWidget(self.exec_panels["portfolio"]); pfl.addWidget(self.pos_split, 1)
        self.tabs.addTab(pf, "Портфель")

        # ================= экран 3: Рынок =================
        heat_w = self._doc_panel(QWidget()); hl = QVBoxLayout(heat_w); hl.setContentsMargins(8, 8, 8, 8)
        hl.addLayout(heat_row); hl.addWidget(self.treemap, 1); hl.addWidget(self.lbl_heat)
        news_w = QWidget(); nl = QVBoxLayout(news_w); nl.setContentsMargins(0, 0, 0, 0)
        nrow = QHBoxLayout(); nrow.addWidget(QLabel("<b>Новости</b>")); nrow.addWidget(self.news_filter, 1); nrow.addWidget(self.btn_news)
        self.ai_panel = self._text(); self.ai_panel.setFont(QFont(self.font().family(), 12))
        self.btn_ai = QPushButton("ИИ"); self.btn_ai.setToolTip("Пересчитать ИИ-сводки сейчас (⌘⇧I)")
        self.btn_ai.clicked.connect(lambda: (setattr(self, "_exec_force", True), self.run_ai(manual=True)))
        nrow.addWidget(self.btn_ai)
        self.news_split_ai = self.ai_panel   # прячем панель целиком; таблица займёт всё место
        news_split = QSplitter(Qt.Vertical); news_split.setObjectName("news"); news_split.addWidget(self.ai_panel); news_split.addWidget(self.news_tbl)
        news_split.setStretchFactor(0, 1); news_split.setStretchFactor(1, 2)
        nl.addLayout(nrow); nl.addWidget(news_split, 1); nl.addWidget(self.lbl_news)
        ideas_w = QWidget(); il = QVBoxLayout(ideas_w); il.setContentsMargins(0, 0, 0, 0)
        irow = QHBoxLayout(); irow.addWidget(QLabel("<b>Идеи</b>"))
        for lbl, wdg, tip in (("див ≥", self.sp_min_yield, "Дивидендная доходность TTM"), ("кап ≥", self.sp_min_cap, "Капитализация"),
                              ("долг ≤", self.sp_max_debt, "Чистый долг / EBITDA")):
            l = QLabel(lbl); l.setToolTip(tip); wdg.setToolTip(tip)
            irow.addWidget(l); irow.addWidget(wdg)
        irow.addStretch()
        irow.addWidget(self.btn_blacklist); irow.addWidget(self.btn_screen)
        for sb in (self.sp_min_yield, self.sp_min_cap, self.sp_max_debt):
            sb.valueChanged.connect(lambda _: self.refilter_ideas())
        il.addLayout(irow); il.addWidget(self.tbl_ideas, 1); il.addWidget(self.lbl_ideas)
        market_bottom = QSplitter(Qt.Horizontal); market_bottom.setObjectName("market_bottom")
        market_bottom.addWidget(news_w); market_bottom.addWidget(ideas_w)
        market = QSplitter(Qt.Vertical); market.setObjectName("market"); market.addWidget(heat_w); market.addWidget(market_bottom)
        market.setStretchFactor(0, 3); market.setStretchFactor(1, 2)
        mk = QWidget(); mkl = QVBoxLayout(mk); mkl.setContentsMargins(8, 8, 8, 8)
        mkl.addWidget(self.exec_panels["market"]); mkl.addWidget(market, 1)
        self.tabs.addTab(mk, "Рынок")

        # ================= экран 4: Стратегия =================
        self.forecast = ForecastTab(self.cfg.get("forecast"))
        self.forecast.paramsChanged.connect(self.on_forecast_changed)
        self.forecast.request_refresh = self._push_growth_to_forecast
        self.forecast.request_long_history = lambda years: self.apply_growth_base(fetch_if_needed=True)
        hist_w = self._doc_panel(QWidget()); hsl2 = QVBoxLayout(hist_w); hsl2.setContentsMargins(8, 8, 8, 8)
        hsl2.addWidget(self.chart_history, 1); hsl2.addWidget(self.lbl_history)
        strat_bottom = QSplitter(Qt.Horizontal); strat_bottom.addWidget(hist_w); strat_bottom.addWidget(self.txt_report)
        strat_bottom.setStretchFactor(0, 3); strat_bottom.setStretchFactor(1, 2)
        strategy = QSplitter(Qt.Vertical); strategy.setObjectName("strategy")
        strategy.addWidget(self.forecast); strategy.addWidget(strat_bottom)
        strategy.setStretchFactor(0, 3); strategy.setStretchFactor(1, 2)
        st = QWidget(); stl = QVBoxLayout(st); stl.setContentsMargins(8, 8, 8, 8)
        stl.addWidget(self.exec_panels["strategy"]); stl.addWidget(strategy, 1)
        self.tabs.addTab(st, "Стратегия")

        for panel in list(self.exec_panels.values()) + [self.ai_panel, self.ai_small]:
            panel.spinner = BusySpinner(panel)

        self.about = DIAG.AboutTab(self)
        self.tabs.addTab(self.about, "О программе")

        central = QWidget(); cl_ = QVBoxLayout(central); cl_.setContentsMargins(0, 0, 0, 0); cl_.setSpacing(0)
        cl_.addWidget(self.banner); cl_.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def show_banner(self, html: str, button: str = "", url: str = "") -> None:
        self.banner_text.setText(html)
        self.banner_btn.setText(button); self.banner_btn.setVisible(bool(button)); self._banner_url = url
        self.banner.show()

    def _banner_action(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        if getattr(self, "_banner_url", ""):
            QDesktopServices.openUrl(QUrl(self._banner_url))

    def _banner_retry(self) -> None:
        """Повторно проверить доступ к iCloud-папке и, если он появился, перейти на неё."""
        paths.fallback_reason = ""
        if paths.icloud_available() and paths._accessible(paths.ICLOUD_DIR):
            new = paths.set_data_dir(paths.ICLOUD_DIR)
            self.on_storage_changed(new)
            self.banner.hide()
            self.set_status(f"Доступ есть — данные в {new}")
        else:
            self.set_status("Доступа к iCloud Drive пока нет. После выдачи разрешения может потребоваться перезапуск приложения.")

    WORKERS = ("worker", "hist_worker", "news_worker", "scr_worker", "fair_worker", "ai_worker", "exec_worker",
               "ideas_worker", "long_worker")

    def closeEvent(self, event) -> None:
        """Сохранить размеры окна и остановить фоновые потоки (Qt при живом QThread вызывает abort())."""
        self.save_layout()
        self.auto_timer.stop()
        running = [getattr(self, n) for n in self.WORKERS if getattr(self, n, None) is not None and getattr(self, n).isRunning()]
        for wk in running:
            wk.requestInterruption()          # воркеры проверяют флаг между запросами
        for wk in running:
            if not wk.wait(8000):
                log.warning("поток %s не остановился за 8 с — завершаю принудительно", type(wk).__name__)
                wk.terminate(); wk.wait(2000)
        if self.settings_win is not None:
            self.settings_win.close()
        super().closeEvent(event)

    def on_forecast_changed(self, params: dict) -> None:
        self.cfg["forecast"] = params
        pc.save_config(self.cfg, self.config_path)

    def set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    # ---- мастер настройки ---------------------------------------------------

    def needs_setup(self) -> bool:
        """Новый пользователь: мастер ещё не проходили и классификации бумаг нет (существующие конфиги не трогаем)."""
        return not self.cfg.get("setup_done") and not self.cfg.get("instruments")

    def run_wizard(self, first: bool = False) -> None:
        from wizard import SetupWizard
        wz = SetupWizard(self.cfg, self)
        if wz.exec():
            fc_old = self.cfg.get("forecast") or {}
            self.cfg = wz.cfg
            self.cfg["forecast"] = {**fc_old, **(wz.cfg.get("forecast") or {})}
            pc.save_config(self.cfg, self.config_path)
            if self.settings_win is not None:
                self.settings_win.close(); self.settings_win = None
            # прогноз подхватывает цель и пополнение
            for k in ("target_monthly_real", "monthly_contribution"):
                if k in self.cfg["forecast"] and k in self.forecast.w:
                    self.forecast.w[k].setValue(self.cfg["forecast"][k])
            if "target_year" in self.cfg["forecast"]:
                self.forecast.sp_target_year.setValue(int(self.cfg["forecast"]["target_year"]))
            if self.positions:
                self._reclassify(); self.render()
            log.info("мастер настройки пройден: шаблон %s", self.cfg.get("strategy_preset"))
            if first and not self._ready_for_api():
                self._explain_not_ready()
        elif first:
            self.cfg["setup_done"] = True           # отказался — не донимаем при каждом запуске
            pc.save_config(self.cfg, self.config_path)

    # ---- размеры окна и разделителей ----------------------------------------

    SPLITTERS = ("summary_split", "pos_split", "blocked_split", "news_split_ai")

    @staticmethod
    def _qsettings() -> QSettings:
        return QSettings("PortfolioCheck", APP_NAME)

    def _all_splitters(self) -> dict:
        """Все разделители окна — по устойчивому имени, чтобы положение переживало перезапуск."""
        out = {}
        for i, sp in enumerate(self.findChildren(QSplitter)):
            name = sp.objectName() or f"splitter{i}"
            out[name] = sp
        return out

    def save_layout(self) -> None:
        s = self._qsettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("window_state", self.saveState())
        s.setValue("tab", self.tabs.currentIndex())
        for name, sp in self._all_splitters().items():
            s.setValue(f"splitter/{name}", sp.saveState())
        s.sync()

    def restore_layout(self) -> None:
        s = self._qsettings()
        g = s.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
            scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
            if scr is not None and not scr.intersects(self.geometry()):   # монитор отключили — вернуть на экран
                self.move(scr.topLeft() + QPoint(40, 40))
                self.resize(min(1400, scr.width() - 80), min(900, scr.height() - 80))
        st = s.value("window_state")
        if st is not None:
            self.restoreState(st)
        for name, sp in self._all_splitters().items():
            v = s.value(f"splitter/{name}")
            if v is not None:
                sp.restoreState(v)
        tab = s.value("tab")
        if tab is not None:
            try:
                self.tabs.setCurrentIndex(int(tab))
            except Exception:  # noqa: BLE001
                pass

    # ---- старт с диска ------------------------------------------------------

    def _latest_snapshot(self) -> Path | None:
        files = sorted(self.snap_dir().glob("*.json"))
        return files[-1] if files else None

    def _snapshot_is_stale(self) -> bool:
        f = self._latest_snapshot()
        if f is None:
            return True
        minutes = int(self.cfg.get("refresh", {}).get("interval_min", 60)) or 60
        age = datetime.now().timestamp() - f.stat().st_mtime
        return age > minutes * 60

    def _snapshot_age_text(self) -> str:
        f = self._latest_snapshot()
        if f is None:
            return "нет данных"
        age = int((datetime.now().timestamp() - f.stat().st_mtime) / 60)
        return f"{age} мин назад" if age < 120 else f"{age // 60} ч назад"

    def load_from_disk(self) -> None:
        """Всё, что есть на диске: снапшот портфеля, кэш свечей → heatmap, новости, ИИ-сводки."""
        f = self._latest_snapshot()
        if f is None:
            return
        try:
            self.positions = pc.load_snapshot(str(f))
        except Exception as e:  # noqa: BLE001
            log.error("снапшот %s не прочитан: %s", f, e); return
        self._reclassify(); pc.apply_blocked_flags(self.positions, self.cfg); pc.fill_fair_prices_local(self.positions, self.cfg)
        # свечи из кэша → доходности по периодам и история, без API
        cache_dir = paths.data_dir() / "cache"
        candles = {p.figi: H.load_cached(cache_dir, p.figi) for p in self.positions if p.figi}
        candles = {k: v for k, v in candles.items() if v}
        if candles:
            self.period_changes = {p.figi: H.period_changes(candles.get(p.figi, []), float(p.cur_price)) for p in self.positions}
            fx = {k.upper(): float(v) for k, v in (self.cfg.get("fx_fallback") or {}).items()}; fx.setdefault("RUB", 1.0)
            self.backfilled = H.backfilled_history(self.positions, candles, fx)
        # новости из кэша
        try:
            self.news_items = NEWS.load_cache(cache_dir)
        except Exception:  # noqa: BLE001
            self.news_items = []
        # идеи скринера из кэша — чтобы таблица не была пустой до первого прохода
        cached = SCR.load_cached(cache_dir, self._screener_filters(), self._screener_exclude())
        self.render()
        if self.news_items:
            self._fill_news_filter(); self.render_news()
        if cached is not None:
            self._on_ideas(cached)
            if self.news_items and self.ai_enabled() and not getattr(self, "ai_summary", None):
                QTimer.singleShot(3000, self.run_ai)     # тональность кандидатов; при совпадении данных — из кэша
        else:
            self.lbl_ideas.setText("Кандидатов в кэше нет — скринер запустится в фоне (проход по T-Invest ~10–20 секунд) "
                                   "или нажми «Найти».")
            QTimer.singleShot(2000, lambda: self.run_screener(auto=True))
        QTimer.singleShot(0, lambda: self.apply_growth_base(fetch_if_needed=True))
        self.set_status(f"Показаны данные с диска ({self._snapshot_age_text()}).")
        log.info("старт с диска: снапшот %s, свечей %d, новостей %d", f.name, len(candles), len(self.news_items))

    # ---- конвейер фоновой загрузки: портфель → свечи → новости → идеи ----------
    PIPELINE = [("portfolio", "портфель"), ("history", "свечи"), ("news", "новости"), ("ideas", "идеи")]

    def pipeline_begin(self) -> None:
        self._pipe_done: set[str] = set()
        self.bar.setVisible(True); self.bar.setValue(0)
        self._pipeline_render("портфель")

    def pipeline_step(self, key: str, running: str | None) -> None:
        """key — что закончилось; running — что стартует (подпись), None — конвейер завершён."""
        if not hasattr(self, "_pipe_done"):        # загрузка не через refresh (снапшот, тест) — начать конвейер тихо
            self._pipe_done = set()
        self._pipe_done.add(key)
        self.bar.setValue(int(100 * len(self._pipe_done) / len(self.PIPELINE)))
        self._pipeline_render(running)
        if running is None:
            self.bar.setValue(100)
            QTimer.singleShot(2500, lambda: (self.bar.setVisible(False), self.lbl_pipeline.setText("")))

    def _pipeline_render(self, running: str | None) -> None:
        parts = []
        for key, label in self.PIPELINE:
            if key in getattr(self, "_pipe_done", set()):
                parts.append(f"{label} ✓")
            elif running == label:
                parts.append(f"{label} …")
            else:
                parts.append(label)
        self.lbl_pipeline.setText("  ·  ".join(parts))

    # ---- контекст для «О программе» -----------------------------------------
    @staticmethod
    def data_dir() -> Path:
        return paths.data_dir()

    @staticmethod
    def token_present():
        return get_token()

    @staticmethod
    def storage_mode() -> str:
        return {"icloud": "iCloud Drive", "local": "локально", "custom": "своя папка"}[paths.mode_of(paths.data_dir())]

    def feeds(self) -> list:
        return self.cfg.get("news", {}).get("feeds") or NEWS.DEFAULT_FEEDS

    @staticmethod
    def _doc_panel(w: QWidget) -> QWidget:
        """Панель с графиками на «документном» фоне (Base) — как таблицы, а не как окно."""
        w.setAutoFillBackground(True)
        w.setBackgroundRole(QPalette.Base)
        return w

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
        def col(v):
            return "#34C759" if v > 0 else ("#FF453A" if v < 0 else "gray")
        v, s = self.kpi["total"]; v.setText(f"{self.rub(total)} ₽"); s.setText(f"{len(self.positions)} позиций")
        for key in ("day", "month"):
            pct = weighted(key); v, s = self.kpi[key]
            if pct is None:
                v.setText("—"); s.setText("свечи грузятся")
            else:
                v.setText(f"{pct:+.2f}%"); v.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {col(pct)}")
                s.setText(f"≈ {self.rub(total * Decimal(str(pct)) / 100)} ₽")
        fx = {k.upper(): Decimal(str(x)) for k, x in (self.cfg.get("fx_fallback") or {}).items()}
        inc = sum(p.div_12m * p.quantity * fx.get(p.currency, Decimal(1)) for p in self.positions if p.div_12m > 0)
        v, s = self.kpi["income"]; v.setText(f"{self.rub(inc)} ₽")
        s.setText(f"{float(inc / total * 100):.1f}% к портфелю, {self.rub(inc / 12)} ₽/мес" if total else "")
        n = getattr(self, "_last_alerts", 0); v, s = self.kpi["signals"]
        v.setText(str(n)); v.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {'#FF9F0A' if n else '#34C759'}")
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
        cfg = self.cfg.get("ai") or {}
        provider = cfg.get("provider", "openrouter"); key = self.ai_key(provider)
        if not key or not self.positions:
            if manual:
                self.set_status("ИИ-сводки: нужен ключ (Настройки → ИИ) и загруженный портфель.")
            return
        if getattr(self, "exec_worker", None) is not None and self.exec_worker.isRunning():
            return
        jobs = [("overview", self._facts_overview()), ("portfolio", self._facts_portfolio()),
                ("market", self._facts_market()), ("strategy", self._facts_strategy())]
        for kind, _ in jobs:
            self.exec_panels[kind].spinner.start("Модель готовит сводку — показана прошлая")
        self.set_status(f"ИИ-сводки по экранам ({provider})… показаны прошлые")
        self.exec_worker = ExecWorker(provider, key, cfg.get("model", ""), cfg.get("proxy", ""), cfg.get("base_url", ""), jobs,
                                      paths.data_dir() / "cache", float(cfg.get("min_interval_h", 6)), manual)
        self.exec_worker.one.connect(self._on_exec_one)
        self.exec_worker.failed.connect(lambda m: (log.error("ИИ-сводка экрана: %s", m), self.set_status(f"ИИ-сводка: {m[:120]}"),
                                                   [p.spinner.stop() for p in self.exec_panels.values()]))
        self.exec_worker.done.connect(lambda: (self.set_status("ИИ-сводки по экранам готовы."),
                                               [p.spinner.stop() for p in self.exec_panels.values()]))
        self.exec_worker.start()

    def _on_exec_one(self, result) -> None:
        s, note = result if isinstance(result, tuple) else (result, "")
        self.exec_panels[s.kind].spinner.stop()
        AI.save_exec(s, paths.data_dir() / "cache")
        self.exec_panels[s.kind].setHtml(AI.render_exec_html(s, mask_text if self.privacy else None, note))

    def ai_enabled(self) -> bool:
        """Есть ли чем делать сводки: провайдер с ключом (или Ollama)."""
        cfg = self.cfg.get("ai") or {}
        return bool(self.ai_key(cfg.get("provider", "openrouter")))

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
            s = AI.load_exec(kind, paths.data_dir() / "cache")
            if s:
                panel.setHtml(AI.render_exec_html(s, mask_text if self.privacy else None))

    # ---- скринер ------------------------------------------------------------

    def _blacklist(self) -> dict:
        sc = self.cfg.get("screener") or {}
        bl = sc.get("blacklist") or {}
        if isinstance(bl, list):
            bl = {t: "" for t in bl}
        return bl if sc.get("blacklist_customized") else {**SCR.DEFAULT_BLACKLIST, **bl}

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
        sc = self.cfg.setdefault("screener", {}); sc["blacklist"] = bl; sc["blacklist_customized"] = True
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
            words = [w.lower() for w in kw.get(idea.ticker, [])]
            idea.news_count = sum(1 for it in getattr(self, "news_items", [])
                                  if any(w in f"{it.title} {it.summary}".lower() for w in words))
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
        if getattr(self, "ideas_worker", None) is not None and self.ideas_worker.isRunning():
            return
        self.ideas_worker = IdeasEnrichWorker(token, ideas, paths.data_dir() / "cache")
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
        m.addAction("Исключить из идей (в чёрный список)…", self.blacklist_selected)
        m.exec(self.tbl_ideas.viewport().mapToGlobal(pos))

    def refilter_ideas(self) -> None:
        """Перефильтровать последний результат скринера (после правки чёрного списка/фильтров), без сети."""
        res = getattr(self, "_last_screen", None)
        if res is None or not getattr(res, "all_ideas", None):
            return
        res.ideas = SCR.apply_filters(res.all_ideas, self._screener_filters(), self._screener_exclude())
        self._on_ideas(res)

    def run_screener(self, auto: bool = False) -> None:
        if getattr(self, "scr_worker", None) is not None and self.scr_worker.isRunning():
            return
        if auto:
            # свежий кэш (сутки) отдаём сразу, без похода в сеть
            cached = SCR.load_cached(paths.data_dir() / "cache", self._screener_filters(), self._screener_exclude(), max_age_h=24)
            if cached is not None:
                self._on_ideas(cached); return
        exclude = self._screener_exclude()
        self.btn_screen.setEnabled(False); self.set_status("Скринер: загружаю список акций…")
        token = get_token()
        if not token:
            self.lbl_ideas.setText("Скринер работает через T-Invest API — нужен токен (Настройки → Доступ)."); self.pipeline_step("ideas", None); return
        self.scr_worker = ScreenerWorker(token, self.sp_min_yield.value(), float(self.sp_min_cap.value()), self.sp_max_debt.value(),
                                         exclude, paths.data_dir() / "cache", force=not auto)
        self.scr_worker.filters = self._screener_filters()
        self.scr_worker.progress.connect(self.set_status)
        self.scr_worker.done.connect(self._on_ideas)
        self.scr_worker.failed.connect(lambda m: (log.error("скринер: %s", m), self.btn_screen.setEnabled(True),
                                                  self.set_status(f"Скринер: {m[:120]}"), self.pipeline_step("ideas", None),
                                                  self.lbl_ideas.setText(f"<span style='color:#FF453A'><b>Скринер не отработал:</b> {m[:300]}</span><br>"
                                                                         "Проверь связь с MOEX ISS: «О программе» → «Проверить подключения».")))
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
            month = "…" if mc is None else f"{'▲' if mc > 0 else ('▼' if mc < 0 else '■')} {mc:+.1f}%"
            sent = getattr(i, "news_sentiment", "") or ("neutral" if getattr(i, "news_count", 0) else "")
            news = (AI.SENT_MARK.get(sent, "") + (f" {i.news_count}" if getattr(i, "news_count", 0) else "")) if sent else "—"
            rows.append(([i.name, i.ticker, i.sector, month, news, f"{i.price:,.2f}", f"{i.cap_bln:,.0f}", f"{i.div_yield:.1f}%",
                          fmt(getattr(i, "avg5y_div_yield", None), "{:.1f}%"), f"{i.div_per_share:,.2f}",
                          fmt(i.pe), fmt(i.net_debt_ebitda), fmt(i.roe, "{:.1f}%"), fmt(i.free_float, "{:.0f}%"), i.ex_div_date],
                         TINT_WARN if (i.net_debt_ebitda or 0) > 3 else None))
        self._fill(self.tbl_ideas, rows)
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
                cm.setForeground(QColor("#34C759" if (mc or 0) > 0 else ("#FF453A" if (mc or 0) < 0 else "gray")))
                cm.setToolTip(f"Цена за месяц: {mc:+.1f}%" if mc is not None else "Свечи ещё грузятся")
            cn = self.tbl_ideas.item(r, 4)
            if cn is not None:
                s = getattr(i, "news_sentiment", "")
                cn.setForeground(QColor(AI.SENT_COLOR.get(s, "gray")))
                note = getattr(i, "news_note", "")
                if s and i.news_count:
                    tip = f"{AI.SENT_TITLE.get(s, '')}; упоминаний в ленте: {i.news_count}"
                elif s:
                    tip = f"{AI.SENT_TITLE.get(s, '')} — оценка по отраслевому контексту (прямых новостей нет)"
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
                txt += " <span style='color:#FF9F0A'>Пусто: " + (
                    "T-Invest не отдал фундаментал — см. ошибку выше и лог в «О программе»; нажми «Найти кандидатов», чтобы повторить без кэша."
                    if result.paying == 0 else
                    "ослабь фильтры (например, 6% и 20 млрд) и нажми «Найти кандидатов».") + "</span>"
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

    # ---- приватный режим ----------------------------------------------------

    def rub(self, v) -> str:
        return MASK if self.privacy else f"{v:,.0f}"

    def qty(self, v) -> str:
        return MASK if self.privacy else f"{v:,.0f}"

    def set_privacy(self, on: bool) -> None:
        self.privacy = on
        self.act_privacy.setText("Показать суммы" if on else "Скрыть суммы")
        self.act_privacy.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton if on else QStyle.SP_DialogNoButton))
        # Прогноз — целиком цифры, его проще размыть, чем маскировать поле за полем
        if on:
            eff = QGraphicsBlurEffect(self.forecast); eff.setBlurRadius(14)   # родитель обязателен: иначе PySide удалит эффект
            self.forecast.setGraphicsEffect(eff)
        else:
            self.forecast.setGraphicsEffect(None)
        for pie in (self.pie_currency, self.pie_companies, self.pie_sectors):
            pie.hide_total = on
            pie.update()
        if hasattr(self, "treemap"):
            self.treemap.hide_values = on; self.treemap.update()
        if hasattr(self, "exec_panels"):
            self.render_exec_cached()
        if hasattr(self, "chart_history"):
            self.chart_history.hide_values = on; self.chart_history.update()
        if self.positions:
            self.render()


    def _ready_for_api(self) -> bool:
        return bool(get_token()) and self.bundle_path().exists()

    def _explain_not_ready(self) -> None:
        has_token, has_bundle = bool(get_token()), self.bundle_path().exists()
        log.warning("автообновление не запущено: токен %s, бандл %s (%s)", has_token, has_bundle, self.bundle_path())
        if has_token and has_bundle:
            return
        missing = []
        if not has_token:
            missing.append("токен T-Invest API (Keychain)")
        if not has_bundle:
            missing.append(f"бандл сертификатов ({self.bundle_path()})")
        self.show_banner("<b>Автообновление не запущено — не хватает: " + " и ".join(missing) + ".</b><br>"
                         "Задать: Настройки → Доступ (токен вводится один раз на каждом Mac; бандл собирается кнопкой).",
                         "Открыть Настройки → Доступ", "")
        self._banner_url = ""
        try:
            self.banner_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.banner_btn.clicked.connect(self._open_access_settings)

    def _open_access_settings(self) -> None:
        self.open_settings()
        self.settings_win.tabs.setCurrentIndex(self.settings_win.tabs.count() - 1)

    def apply_refresh_schedule(self) -> None:
        minutes = int(self.cfg.get("refresh", {}).get("interval_min", 60))
        self.auto_timer.stop()
        if minutes > 0:
            self.auto_timer.start(minutes * 60 * 1000)

    def _news_table(self) -> QTableWidget:
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Бумага", "Ист.", "Когда", "Новость"])
        hh = t.horizontalHeader()
        for i in range(3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        t.setItemDelegateForColumn(0, BadgeDelegate(t)); t.setItemDelegateForColumn(1, BadgeDelegate(t))
        t.verticalHeader().setVisible(False); t.setShowGrid(False); t.setAlternatingRowColors(True); t.setWordWrap(True)
        t.setEditTriggers(QTableWidget.NoEditTriggers); t.setSelectionBehavior(QTableWidget.SelectRows)
        t.itemActivated.connect(lambda item, tt=t: self._open_news_from(tt, item))
        return t

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setSortingEnabled(True)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setAlternatingRowColors(True)
        return t

    @staticmethod
    def _text() -> QTextEdit:
        t = QTextEdit()
        t.setReadOnly(True)
        t.setFont(QFont("Menlo", 12))
        return t

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_FileDialogInfoView), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    # ---- Действия -----------------------------------------------------------

    def open_settings(self) -> None:
        if self.settings_win is None:
            self.settings_win = SettingsDialog(self.cfg, self.bundle_path(), self)
            self.settings_win.configChanged.connect(self.on_config_changed)
            self.settings_win.storageChanged.connect(self.on_storage_changed)
            self.settings_win.accessChanged.connect(self.maybe_start_after_setup)
            self.settings_win.refresh_names(self._names_from_positions())
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()

    def maybe_start_after_setup(self) -> None:
        """Вызывается после изменений на вкладках Доступ/ИИ: если всё на месте — убрать баннер и обновить."""
        self.update_ai_visibility()
        if self.ai_enabled() and getattr(self, "news_items", None) and not getattr(self, "ai_summary", None):
            QTimer.singleShot(0, self.run_ai)          # ключ появился — сразу сделать сводки по уже загруженной ленте
        if self._ready_for_api():
            self.banner.hide()
            self.refresh(manual=False)

    def on_config_changed(self, cfg: dict) -> None:
        old_refs = (self.cfg.get("reference_tickers"), self.cfg.get("blocked_tickers"))
        cfg.setdefault("forecast", self.cfg.get("forecast", {}))   # окно настроек прогноз не трогает
        self.cfg = cfg
        pc.save_config(self.cfg, self.config_path)
        self.apply_refresh_schedule()
        self.update_ai_visibility()
        self.refilter_ideas()
        if self.positions:
            self._reclassify()
            pc.apply_blocked_flags(self.positions, self.cfg)
            pc.fill_fair_prices_local(self.positions, self.cfg)   # что можно — сразу, без API
            self.render()
            if old_refs != (cfg.get("reference_tickers"), cfg.get("blocked_tickers")):
                self.recalc_fair_prices()                          # остальное — фоновым запросом

    def recalc_fair_prices(self) -> None:
        token = get_token()
        if not token or not self.bundle_path().exists():
            return
        if getattr(self, "fair_worker", None) is not None and self.fair_worker.isRunning():
            return
        self.set_status("Пересчитываю цены заблокированных…")
        self.fair_worker = FairPriceWorker(token, self.cfg, self.positions)
        self.fair_worker.done.connect(self._on_fair_done)
        self.fair_worker.failed.connect(lambda m: (log.error("цены эталонов: %s", m), self.set_status(f"Не удалось пересчитать цены эталонов: {m[:100]}")))
        self.fair_worker.start()

    def _on_fair_done(self, positions) -> None:
        self.positions = positions
        self.render()
        self.set_status("Цены заблокированных пересчитаны.")

    def _names_from_positions(self) -> dict[str, str]:
        names = {}
        for p in self.positions:
            names[p.ticker] = p.name
            if p.isin:
                names[p.isin] = p.name
        return names

    def _reclassify(self) -> None:
        """Пересчитать классы позиций по новому конфигу без похода в API."""
        mapping = {str(k): v for k, v in self.cfg.get("instruments", {}).items()}
        for p in self.positions:
            p.cls = mapping.get(p.ticker) or mapping.get(p.isin) or "unclassified"

    def refresh(self, manual: bool = True) -> None:
        self._manual_refresh = manual
        if self.worker is not None and self.worker.isRunning():
            return
        token = get_token()
        if not token or not self.bundle_path().exists():
            self.set_status("Нужны токен и бандл сертификатов — Настройки → Доступ.")
            if manual:
                self.open_settings()
                self.settings_win.tabs.setCurrentIndex(self.settings_win.tabs.count() - 1)
            return
        self.act_refresh.setEnabled(False)
        self.pipeline_begin()
        self.set_status("Загружаю портфель…")
        self.worker = LoadWorker(token, self.cfg)
        self.worker.done.connect(self.on_loaded)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_loaded(self, result) -> None:
        positions, income, macro = (tuple(result) + (None, None))[:3] if isinstance(result, tuple) else (result, None, None)
        auto = ST.auto_classify(positions, self.cfg)
        if auto:
            pc.save_config(self.cfg, self.config_path)
            log.info("автоклассификация: %d бумаг (%s)", len(auto), ", ".join(auto[:15]))
            self._auto_note = f"{len(auto)} бумаг классифицированы автоматически — проверь в Настройки → Инструменты"
        self.positions = positions
        if auto:
            self._reclassify()
        self._income_estimate = float(income) if income else None
        self._macro = macro
        self.act_refresh.setEnabled(True)
        self.bar.setVisible(False)
        names = self._names_from_positions()
        merged = {**self.cfg.get("instrument_names", {}), **names}
        if merged != self.cfg.get("instrument_names"):
            self.cfg["instrument_names"] = merged
            pc.save_config(self.cfg, self.config_path)
        if self.settings_win is not None:
            self.settings_win.cfg = self.cfg
            self.settings_win.refresh_names(names)
        n_uncl = sum(1 for p in positions if p.cls == "unclassified")
        hint = f" — без класса: {n_uncl}, назначь в Настройки → Инструменты" if n_uncl else ""
        log.info("портфель обновлён: позиций %d, без класса %d, доход из API %s, макро %s",
                 len(positions), n_uncl, "есть" if self._income_estimate else "нет", "есть" if self._macro else "нет")
        extra = getattr(self, "_auto_note", "")
        self.set_status(f"Обновлено {datetime.now():%d.%m.%Y %H:%M}, позиций: {len(positions)}{hint}" + (f"; {extra}" if extra else ""))
        self._auto_note = ""
        self.render()
        self.autosave()
        self.pipeline_step("portfolio", "свечи")
        self.start_history()
        if getattr(self, "_last_alerts", 0) and self.tray.isVisible() and self.cfg.get("refresh", {}).get("notify", True):
            self.tray.showMessage(APP_NAME, f"Сигналов: {self._last_alerts}. Открой вкладку «Сигналы».",
                                  QSystemTrayIcon.Information, 5000)

    def on_failed(self, msg: str) -> None:
        log.error("загрузка портфеля: %s", msg)
        self.act_refresh.setEnabled(True)
        self.bar.setVisible(False); self.lbl_pipeline.setText("")
        short = msg.splitlines()[0][:120]
        self.set_status(f"Не удалось обновить ({datetime.now():%H:%M}): {short}")
        if self._manual_refresh:   # фоновые ошибки — только в статус-бар, без модальных окон
            QMessageBox.critical(self, "Не удалось загрузить портфель", msg)

    def open_snapshot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Снапшот", str(self.snap_dir()), "JSON (*.json)")
        if path:
            self.positions = pc.load_snapshot(path)
            self.set_status(f"Снапшот: {Path(path).name}")
            self.render()

    def save_snapshot(self) -> None:
        if not self.positions:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить снапшот",
                                              str(self.snap_dir() / f"{datetime.now():%Y-%m-%d}.json"), "JSON (*.json)")
        if path:
            pc.save_snapshot(self.positions, path)

    def autosave(self) -> None:
        """Каждое обновление из API — в ~/Library/Application Support/PortfolioCheck/snapshots/<дата>.json"""
        d = self.snap_dir()
        d.mkdir(parents=True, exist_ok=True)
        pc.save_snapshot(self.positions, str(d / f"{datetime.now():%Y-%m-%d}.json"))

    @staticmethod
    def snap_dir() -> Path:
        return paths.data_dir() / "snapshots"

    @staticmethod
    def bundle_path() -> Path:
        return paths.data_dir() / "ca-bundle.pem"

    def on_storage_changed(self, new_dir: Path) -> None:
        """Настройки → Общие сменили папку данных: перечитать конфиг оттуда и перезапустить окно настроек."""
        self.config_path = str(new_dir / "config.yaml")
        ensure_config(Path(self.config_path))
        self.cfg = pc.load_config(self.config_path)
        if self.settings_win is not None:
            self.settings_win.close(); self.settings_win = None
        if self.positions:
            self._reclassify(); pc.apply_blocked_flags(self.positions, self.cfg); self.render()
        self.set_status(f"Папка данных: {new_dir}")

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
            pass
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
            pass
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


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DATA_DIR / "config.yaml"))
    ap.add_argument("--snapshot", help="открыть JSON-снапшот вместо загрузки из API")
    args = ap.parse_args()

    DIAG.setup_logging(paths.data_dir())      # лог с самого старта, до создания окна
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = Path(__file__).with_name("icon.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    ensure_config(Path(args.config))
    w = MainWindow(args.config)
    app.aboutToQuit.connect(lambda: w.close())
    if args.snapshot:
        w.positions = pc.load_snapshot(args.snapshot)
        w.status.setText(f"Снапшот: {Path(args.snapshot).name}")
        w.render()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
