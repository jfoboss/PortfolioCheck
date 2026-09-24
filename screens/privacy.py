"""
screens/privacy.py — режим «глазик»: скрытие сумм. Миксин MainWindow (состояние — атрибуты окна).
"""
from __future__ import annotations


from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGraphicsBlurEffect
from PySide6.QtWidgets import (
    QTableWidget, QTextEdit, QHeaderView, QSystemTrayIcon, QStyle,
)

from diagnostics import log
from ui_common import APP_NAME, MASK, get_token
from osutil import SECRET_STORE_NAME, mono_font
from widgets import BadgeDelegate


class PrivacyMixin:
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
            missing.append(f"токен T-Invest API ({SECRET_STORE_NAME})")
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
        t.setFont(mono_font(12))
        return t

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_FileDialogInfoView), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()
