#!/usr/bin/env python3
"""
app.py — десктопное окно над portfolio_check.py (macOS, PySide6).

  Вкладки: Сводка (доли vs цели) · Сигналы (правила) · Позиции (таблица) · Отчёт (markdown)
  Все настройки (цели, классификация, правила, токен, сертификаты) — через «Настройки…».
  Конфиг живёт в ~/Library/Application Support/PortfolioCheck/config.yaml и пишется только приложением.
  Токен хранится в Keychain (macOS) или диспетчере учётных данных (Windows), не в файлах.
  Загрузка портфеля — в фоновом потоке, окно не блокируется.
  «Файл → Открыть снапшот» — просмотр сохранённого JSON без сети (и для демо).

Запуск из исходников:  python app.py [--config config.yaml] [--snapshot snap.json]
Сборка .app:           см. build_mac.sh
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QSettings
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QIcon
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QButtonGroup, QRadioButton, QCheckBox, QDoubleSpinBox, QSpinBox, QComboBox)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLabel, QProgressBar, QMessageBox, QFileDialog, QSystemTrayIcon,
    QStyle, QToolBar, QStatusBar, QSplitter, QSizePolicy,
)

import portfolio_check as pc
from settings_dialog import SettingsDialog
from forecast import ForecastTab
from charts import PieChart, BarChart, RangeBarChart, DivergingBarChart, TreemapChart, LineChart
import paths
import history as H
import diagnostics as DIAG
import ai as AI
import strategy as ST
from diagnostics import log
import news as NEWS
import screener as SCR
import config_migrate as CM
from osutil import native_keys, IS_MAC

from ui_common import APP_NAME, get_token, ensure_config
from widgets import BusySpinner
from workers import FairPriceWorker, LoadWorker
from screens.history import HistoryMixin
from screens.news import NewsMixin
from screens.summaries import ExecSummaryMixin
from screens.ideas import IdeasMixin
from screens.privacy import PrivacyMixin
from screens.overview import RenderMixin
from screens.payouts import PayoutsMixin
from screens.card import CardMixin
from screens.attribution import AttributionMixin
from screens.palette import PaletteMixin
from screens.exporting import ExportMixin


# ---------------------------------------------------------------------------
# Окно
# ---------------------------------------------------------------------------

class MainWindow(HistoryMixin, NewsMixin, ExecSummaryMixin, IdeasMixin, PrivacyMixin, RenderMixin, PayoutsMixin, CardMixin, AttributionMixin, PaletteMixin, ExportMixin, QMainWindow):
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        try:
            self.cfg = CM.load_and_migrate(config_path)
        except Exception as e:  # noqa: BLE001
            log.error("конфиг %s не прочитан: %s — использую дефолтный", config_path, e)
            self.config_path = str(paths.LOCAL_DIR / "config.yaml")
            ensure_config(Path(self.config_path))
            self.cfg = CM.load_and_migrate(self.config_path)
        self.positions: list[pc.Position] = []
        self.worker: LoadWorker | None = None
        self.settings_win: SettingsDialog | None = None
        self._manual_refresh = False
        self.privacy = bool(self.cfg.get("refresh", {}).get("privacy_on_launch", True))
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(lambda: self.refresh(manual=False))

        self.setWindowTitle("Портфель — проверка правил" + (" — ДЕМО" if paths.DEMO_DIR is not None else ""))
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
        if paths.fallback_reason and not IS_MAC:
            log.warning(paths.fallback_reason)
            self.show_banner("<b>Папка данных недоступна — работаю с локальной копией настроек.</b><br>"
                             + paths.fallback_reason, retry=False)
        elif paths.fallback_reason:
            log.warning(paths.fallback_reason)
            self.show_banner(
                "<b>Нет доступа к iCloud Drive — работаю с локальной копией настроек.</b><br>"
                "macOS не выдала приложению разрешение на папку iCloud Drive. Самый надёжный способ: "
                "<b>Системные настройки → Конфиденциальность и безопасность → Полный доступ к диску</b> → добавить PortfolioCheck "
                "(кнопка «+» или перетащить приложение из папки Программы) → включить. Затем «Проверить снова» "
                "(или перезапустить приложение).",
                "Открыть настройки доступа" if IS_MAC else "",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" if IS_MAC else "", retry=True)
        AI.CATALOG_DIR = paths.data_dir() / "cache"   # модель по умолчанию для OpenRouter — из живого каталога
        AI.load_rate_block()                           # дневной лимит, пойманный до перезапуска, ещё действует
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
        # ⌘R на macOS, Ctrl+R и F5 на Windows (у стандартного Refresh на macOS — неочевидное сочетание)
        self.act_refresh.setShortcuts([QKeySequence("Ctrl+R"), QKeySequence(Qt.Key_F5)])
        self.act_refresh.triggered.connect(lambda: self.refresh(manual=True))

        self.act_privacy = QAction("Скрыть суммы", self)
        self.act_privacy.setCheckable(True)
        self.act_privacy.setChecked(self.privacy)
        self.act_privacy.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.act_privacy.setToolTip(f"Спрятать размер портфеля и суммы ({native_keys('Ctrl+Shift+H')}). "
                                    "Проценты и названия остаются.")
        self.act_privacy.toggled.connect(self.set_privacy)

        act_open = QAction("Открыть снапшот…", self); act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.open_snapshot)
        act_save = QAction("Сохранить снапшот…", self); act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.save_snapshot)

        # «О программе» — отдельное окно; на macOS пункт уезжает в меню приложения (роль AboutRole)
        self.act_about = QAction("О программе PortfolioCheck", self)
        self.act_about.setMenuRole(QAction.AboutRole)
        self.act_about.triggered.connect(self.open_about)
        act_quit = QAction("Выход", self); act_quit.setMenuRole(QAction.QuitRole)
        act_quit.setShortcut(QKeySequence.Quit if not QKeySequence(QKeySequence.Quit).isEmpty() else QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)

        m_file = self.menuBar().addMenu("Файл"); self.m_file = m_file
        act_wizard = QAction("Мастер настройки…", self); act_wizard.triggered.connect(lambda: self.run_wizard(first=False))
        m_file.addAction(act_wizard); m_file.addSeparator()
        m_file.addAction(act_open); m_file.addAction(act_save)
        act_exp = QAction("Экспорт истории стоимости (CSV)…", self); act_exp.triggered.connect(lambda: self.export_history())
        m_file.addAction(act_exp)
        m_file.addSeparator(); m_file.addAction(self.act_prefs)
        m_file.addSeparator(); m_file.addAction(act_quit)

        m_view = self.menuBar().addMenu("Вид")
        self.act_palette = QAction("Команды и поиск…", self); self.act_palette.setShortcut(QKeySequence("Ctrl+K"))
        self.act_palette.setToolTip("Тикер → карточка, «SBERP новости», экраны, действия, разделы настроек")
        self.act_palette.triggered.connect(self.open_palette)
        m_view.addAction(self.act_palette); m_view.addSeparator()
        m_view.addAction(self.act_refresh)
        m_view.addSeparator(); m_view.addAction(self.act_privacy)
        act_exec = QAction("ИИ-сводки по экранам", self); act_exec.setShortcut(QKeySequence("Ctrl+Shift+I"))
        act_exec.triggered.connect(lambda: self.run_exec(manual=True)); m_view.addSeparator(); m_view.addAction(act_exec)
        m_view.addSeparator()
        self.screen_actions = []                 # ⌘1…⌘5 — экраны; заполняется после сборки вкладок
        self.m_view = m_view

        m_win = self.menuBar().addMenu("Окно")
        act_min = QAction("Свернуть", self); act_min.setShortcut(QKeySequence("Ctrl+M")); act_min.triggered.connect(self.showMinimized)
        act_full = QAction("Во весь экран", self)
        act_full.setShortcut(QKeySequence.FullScreen if not QKeySequence(QKeySequence.FullScreen).isEmpty() else QKeySequence("F11"))
        act_full.triggered.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        m_win.addAction(act_min); m_win.addAction(act_full)

        m_help = self.menuBar().addMenu("Справка")
        act_diag = QAction("Диагностика и лог…", self); act_diag.triggered.connect(self.open_about)
        act_check = QAction("Проверить подключения", self)
        act_check.triggered.connect(lambda: (self.open_about(), self.about_win.tab.run_checks()))
        m_help.addAction(act_diag); m_help.addAction(act_check); m_help.addSeparator(); m_help.addAction(self.act_about)

    def _build_screen_actions(self) -> None:
        """Вид → экраны по ⌘1…⌘N (на macOS Ctrl в Qt — это ⌘)."""
        for i in range(self.tabs.count()):
            a = QAction(self.tabs.tabText(i), self)
            if i < 9:
                a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            a.triggered.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))
            self.m_view.addAction(a); self.screen_actions.append(a)

    def open_about(self) -> None:
        if getattr(self, "about_win", None) is None:
            self.about_win = DIAG.AboutWindow(self, self)
        self.about_win.show(); self.about_win.raise_(); self.about_win.activateWindow()

    def _build_ui(self) -> None:
        tb = QToolBar("Основная")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(18, 18))
        self.act_refresh.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.act_prefs.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.act_privacy.setIcon(self.style().standardIcon(QStyle.SP_DialogNoButton))
        self.toolbar = tb
        # «Настроить…» — только в гостевом режиме (демо без токена), см. enter_guest_mode
        self.act_setup = QAction("Настроить…", self)
        self.act_setup.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.act_setup.setToolTip("Подключить свой портфель: мастер стратегии и токен T-Invest — пара минут")
        self.act_setup.triggered.connect(self.leave_guest)
        self.act_setup.setVisible(False)
        tb.addAction(self.act_setup)
        tb.addAction(self.act_refresh)
        tb.addAction(self.act_privacy)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); tb.addWidget(spacer)
        tb.addAction(self.act_prefs)             # настройки — справа, как в приложениях macOS
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

        # ================= экран: Календарь выплат =================
        self.tabs.addTab(self._build_payouts_tab(), "Календарь")

        # ================= экран 3: Рынок =================
        heat_w = self._doc_panel(QWidget()); hl = QVBoxLayout(heat_w); hl.setContentsMargins(8, 8, 8, 8)
        hl.addLayout(heat_row); hl.addWidget(self.treemap, 1); hl.addWidget(self.lbl_heat)
        news_w = QWidget(); nl = QVBoxLayout(news_w); nl.setContentsMargins(0, 0, 0, 0)
        nrow = QHBoxLayout(); nrow.addWidget(QLabel("<b>Новости</b>")); nrow.addWidget(self.news_filter, 1); nrow.addWidget(self.btn_news)
        self.ai_panel = self._text(); self.ai_panel.setFont(QFont(self.font().family(), 12))
        self.btn_ai = QPushButton("ИИ"); self.btn_ai.setToolTip(f"Пересчитать ИИ-сводки сейчас ({native_keys('Ctrl+Shift+I')})")
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
        market = QSplitter(Qt.Vertical); market.setObjectName("market"); market.addWidget(heat_w); market.addWidget(news_w)
        market.setStretchFactor(0, 3); market.setStretchFactor(1, 2)
        mk = QWidget(); mkl = QVBoxLayout(mk); mkl.setContentsMargins(8, 8, 8, 8)
        mkl.addWidget(self.exec_panels["market"]); mkl.addWidget(market, 1)
        self.tabs.addTab(mk, "Рынок")

        # ================= экран 4: Идеи (скринер) — отдельно: таблице нужна вся ширина =================
        idw = QWidget(); idl = QVBoxLayout(idw); idl.setContentsMargins(8, 8, 8, 8); idl.addWidget(ideas_w, 1)
        self.tabs.addTab(idw, "Идеи")

        # ================= экран 5: Стратегия =================
        self.forecast = ForecastTab(self.cfg.get("forecast"))
        self.forecast.paramsChanged.connect(self.on_forecast_changed)
        self.forecast.request_refresh = self._push_growth_to_forecast
        self.forecast.request_long_history = lambda years: self.apply_growth_base(fetch_if_needed=True)
        hist_w = self._doc_panel(QWidget()); hsl2 = QVBoxLayout(hist_w); hsl2.setContentsMargins(8, 8, 8, 8)
        hsl2.addWidget(self.chart_history, 1); hsl2.addWidget(self.lbl_history)
        strat_bottom = QSplitter(Qt.Horizontal); strat_bottom.setObjectName("strategy_bottom")
        strat_bottom.addWidget(hist_w); strat_bottom.addWidget(self._build_attribution_panel())
        strat_bottom.addWidget(self.txt_report)
        strat_bottom.setStretchFactor(0, 3); strat_bottom.setStretchFactor(1, 3); strat_bottom.setStretchFactor(2, 2)
        strategy = QSplitter(Qt.Vertical); strategy.setObjectName("strategy")
        strategy.addWidget(self.forecast); strategy.addWidget(strat_bottom)
        strategy.setStretchFactor(0, 3); strategy.setStretchFactor(1, 2)
        st = QWidget(); stl = QVBoxLayout(st); stl.setContentsMargins(8, 8, 8, 8)
        stl.addWidget(self.exec_panels["strategy"]); stl.addWidget(strategy, 1)
        self.tabs.addTab(st, "Стратегия")

        for panel in list(self.exec_panels.values()) + [self.ai_panel, self.ai_small]:
            panel.spinner = BusySpinner(panel)

        self._build_screen_actions()
        self._connect_cards()
        self._install_export_menus()

        central = QWidget(); cl_ = QVBoxLayout(central); cl_.setContentsMargins(0, 0, 0, 0); cl_.setSpacing(0)
        cl_.addWidget(self.banner); cl_.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def show_banner(self, html: str, button: str = "", url: str = "", retry: bool = False) -> None:
        """retry — кнопка «Проверить снова» (повторная проверка доступа к iCloud); нужна только баннеру про iCloud."""
        self.banner_text.setText(html)
        self.banner_btn.setText(button); self.banner_btn.setVisible(bool(button)); self._banner_url = url
        self.banner_retry.setVisible(retry)
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
        if getattr(self, "about_win", None) is not None:
            self.about_win.close()
        for c in list(getattr(self, "_cards", {}).values()):
            c.close()
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
        if paths.DEMO_DIR is not None:             # демо не меняет размеры окна настоящего профиля
            return
        s = self._qsettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("window_state", self.saveState())
        s.setValue("tab_name", self.tabs.tabText(self.tabs.currentIndex()))   # по имени: порядок вкладок меняется
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
        name = s.value("tab_name")
        for i in range(self.tabs.count()):
            if name and self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)

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
        """Всё, что есть на диске: снапшот портфеля, кэш свечей → heatmap, новости, ИИ-сводки, календарь выплат."""
        self.load_payouts_from_disk()
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
        positions, income, macro, payouts_raw = ((tuple(result) + (None, None, None))[:4] if isinstance(result, tuple)
                                                 else (result, None, None, None))
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
        if payouts_raw is not None:
            self.set_payouts(payouts_raw)
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
        self.render_attribution()                  # после сохранения: сегодняшний снапшот — конец периода
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

    # ---- гостевой режим: первый запуск без токена и данных — демо вместо пустого окна ----

    guest = False

    def enter_guest_mode(self) -> None:
        """Демо-портфель с одной кнопкой «Настроить…»: обновлять, скрывать и настраивать в демо нечего."""
        self.guest = True
        self.act_setup.setVisible(True)
        for a in (self.act_refresh, self.act_privacy, self.act_prefs):
            a.setVisible(False)                   # и на тулбаре, и в меню (на macOS «Настройки» — в меню приложения)
        self.m_file.insertAction(self.m_file.actions()[0], self.act_setup)
        self.setWindowTitle("Портфель — проверка правил — демо")
        self.show_banner("<b>Это демо на вымышленном портфеле</b> — так программа выглядит с данными. "
                         "Чтобы увидеть свой портфель, нажми «Настроить…»: мастер стратегии (можно взять шаблон) и "
                         "токен T-Invest только на чтение — пара минут.", "Настроить…", "")
        self.banner_btn.clicked.disconnect()
        self.banner_btn.clicked.connect(self.leave_guest)

    def leave_guest(self) -> None:
        switch_to_real_profile(self)

    def on_storage_changed(self, new_dir: Path) -> None:
        """Настройки → Общие сменили папку данных: перечитать конфиг оттуда и перезапустить окно настроек."""
        self.config_path = str(new_dir / "config.yaml")
        ensure_config(Path(self.config_path))
        self.cfg = CM.load_and_migrate(self.config_path)
        if self.settings_win is not None:
            self.settings_win.close(); self.settings_win = None
        if self.positions:
            self._reclassify(); pc.apply_blocked_flags(self.positions, self.cfg); self.render()
        self.set_status(f"Папка данных: {new_dir}")

# ---------------------------------------------------------------------------
# Гостевой режим (первый запуск) и переход к настоящему профилю
# ---------------------------------------------------------------------------

WINDOWS: list = []          # живые окна: при переходе из демо окно пересоздаётся на настоящем профиле


def should_start_as_guest() -> bool:
    """Показать демо вместо пустого окна: токена нет и ни одного снапшота (новый пользователь или так и не
    настроил). Если снапшоты есть (например, новый Mac без токена) — показываем свои данные, как раньше."""
    if get_token():
        return False
    return not any((paths.data_dir() / "snapshots").glob("*.json"))


def switch_to_real_profile(old: "MainWindow") -> "MainWindow":
    """Из демо — в настоящий профиль без перезапуска: новое окно на реальном конфиге (для нового пользователя
    сразу откроется мастер), демо-окно закрывается, временная папка удаляется."""
    import demo
    import secrets_store as SEC
    paths.DEMO_DIR = None
    SEC.DISABLED = False
    SEC._cache.clear()                           # секреты читаются заново — уже из Keychain / диспетчера
    cfg = paths.data_dir() / "config.yaml"
    ensure_config(cfg)
    w = MainWindow(str(cfg))
    w.show()
    WINDOWS.append(w)
    old.close()
    if old in WINDOWS:
        WINDOWS.remove(old)
    demo.cleanup_dir(old._demo_dir) if getattr(old, "_demo_dir", None) else None
    log.info("гостевой режим: переход к настоящему профилю (%s)", cfg)
    return w


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="путь к config.yaml (по умолчанию — в папке данных)")
    ap.add_argument("--snapshot", help="открыть JSON-снапшот вместо загрузки из API")
    ap.add_argument("--demo", action="store_true",
                    help="вымышленный портфель без токена и сети; данные во временной папке, настоящий профиль не трогается")
    args = ap.parse_args()
    if args.demo:
        import demo
        if args.config:
            ap.error("--demo и --config несовместимы: демо всегда работает на своём временном конфиге")
        args.config = str(demo.setup())            # до первого обращения к папке данных и Keychain
    guest = False
    if not args.demo and not args.config:
        DIAG.setup_logging(paths.data_dir())  # лог настоящего профиля — и в гостевом режиме
        guest = should_start_as_guest()
        if guest:
            import demo
            args.config = str(demo.setup())
        else:
            args.config = str(paths.data_dir() / "config.yaml")
    else:
        DIAG.setup_logging(paths.data_dir())  # лог с самого старта, до создания окна
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = Path(__file__).with_name("icon.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    ensure_config(Path(args.config))
    w = MainWindow(args.config)
    WINDOWS.append(w)
    app.aboutToQuit.connect(lambda: [x.close() for x in list(WINDOWS)])
    if guest:
        w._demo_dir = paths.DEMO_DIR
        w.enter_guest_mode()
        app.aboutToQuit.connect(demo.cleanup)
    if args.demo:
        w.show_banner("<b>Демо-режим:</b> вымышленный портфель, без токена и сети. Изменения живут до выхода "
                      "и удаляются вместе с временной папкой.")
        app.aboutToQuit.connect(demo.cleanup)
    if args.snapshot:
        w.positions = pc.load_snapshot(args.snapshot)
        w.status.setText(f"Снапшот: {Path(args.snapshot).name}")
        w.render()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
