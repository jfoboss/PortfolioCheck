"""
diagnostics.py — логирование и окно «О программе»: окружение, пути, хвост лога, проверка подключений,
«Скопировать диагностику» (без токена и без сумм — можно отправлять как есть).
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QFont
from osutil import SECRET_STORE, mono_font
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QDialog

APP_VERSION = "1.3"
LOG_NAME = "portfolio-check.log"
log = logging.getLogger("portfolio")


def setup_logging(data_dir: Path) -> Path:
    """Файл-лог с ротацией + перехват необработанных исключений и предупреждений Python."""
    logs = data_dir / "logs"; logs.mkdir(parents=True, exist_ok=True)
    path = logs / LOG_NAME
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
        logging.captureWarnings(True)

    def hook(exc_type, exc, tb):
        log.error("Необработанное исключение:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook
    if not getattr(setup_logging, "_announced", False):
        log.info("=== запуск PortfolioCheck %s ===", APP_VERSION)
        setup_logging._announced = True
    return path


def environment_info(data_dir: Path, config_path: str, bundle: Path, has_token: bool, storage_mode: str) -> str:
    try:
        import PySide6; qt = PySide6.__version__
    except Exception:  # noqa: BLE001
        qt = "?"
    try:
        from importlib.metadata import version as _v
        sdk = _v("tinkoff-investments")
    except Exception:  # noqa: BLE001
        sdk = "?"
    try:
        import grpc; grpcv = grpc.__version__
    except Exception:  # noqa: BLE001
        grpcv = "?"
    frozen = getattr(sys, "frozen", False)
    lines = [
        f"PortfolioCheck {APP_VERSION} ({'собранное .app' if frozen else 'из исходников'})",
        f"macOS {platform.mac_ver()[0] or platform.platform()} · {platform.machine()}",
        f"Python {platform.python_version()} · PySide6 {qt} · tinkoff-investments {sdk} · grpcio {grpcv}",
        f"Хранилище: {storage_mode} — {data_dir}",
        f"Конфиг: {config_path} ({'есть' if Path(config_path).exists() else 'НЕТ'})",
        f"CA-бандл: {bundle} ({'есть' if bundle.exists() else 'НЕТ'})",
        f"Токен в {SECRET_STORE}: {'есть' if has_token else 'НЕТ'}",
        f"Время: {datetime.now():%Y-%m-%d %H:%M:%S}",
    ]
    return "\n".join(lines)


def tail(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return "(лог пуст)"
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception as e:  # noqa: BLE001
        return f"(не удалось прочитать лог: {e})"


class ConnectivityWorker(QThread):
    """Проверка всех внешних точек по очереди; результат — построчный отчёт."""
    line = Signal(str)
    done = Signal()

    def __init__(self, token: str | None, bundle: Path, feeds: list[str]):
        super().__init__()
        self.token, self.bundle, self.feeds = token, bundle, feeds

    def run(self) -> None:
        import time
        def check(name, fn):
            t = time.time()
            try:
                extra = fn()
                self.line.emit(f"✓ {name}: ок{(' — ' + extra) if extra else ''} ({time.time() - t:.1f}с)")
                log.info("check %s: ok %s", name, extra or "")
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {str(e)[:200]}"
                self.line.emit(f"✗ {name}: {msg}")
                log.error("check %s: %s", name, msg)

        check(f"Токен в {SECRET_STORE}", lambda: (_ for _ in ()).throw(RuntimeError("не задан")) if not self.token else "задан")
        check("CA-бандл", lambda: f"{self.bundle.read_text(errors='replace').count('BEGIN CERTIFICATE')} серт." if self.bundle.exists()
              else (_ for _ in ()).throw(RuntimeError("не собран")))

        def api():
            import portfolio_check as pc
            Client, *_ = pc._sdk(); pc._setup_grpc_roots()
            with Client(self.token) as c:
                accs = c.users.get_accounts().accounts
                return f"счетов: {len(accs)}"
        if self.token:
            check("T-Invest API (GetAccounts)", api)

        def cbr():
            from macro import fetch_cbr_macro
            m = fetch_cbr_macro(self.bundle); return f"{m.period}: инфляция {m.inflation}%, ставка {m.key_rate}%"
        check("ЦБ РФ (инфляция)", cbr)

        def fundamentals():
            import portfolio_check as pc
            from tinkoff.invest.schemas import GetAssetFundamentalsRequest, InstrumentStatus
            Client, *_ = pc._sdk(); pc._setup_grpc_roots()
            with Client(self.token) as c:
                shares = [s for s in c.instruments.shares(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE).instruments if s.class_code == "TQBR"]
                sber = next((s for s in shares if s.ticker == "SBER"), shares[0])
                f = c.instruments.get_asset_fundamentals(GetAssetFundamentalsRequest(assets=[sber.asset_uid])).fundamentals
                st = f[0] if f else None
                return (f"акций TQBR {len(shares)}; {sber.ticker}: кап. {st.market_capitalization / 1e9:,.0f} млрд, "
                        f"дивдоходность {st.dividend_yield_daily_ttm:.1f}%") if st else f"акций TQBR {len(shares)}, фундаментал пуст"
        if self.token:
            check("T-Invest фундаментал (скринер)", fundamentals)

        from news import check_feed
        for url in self.feeds:
            def one(u=url):
                ok, msg = check_feed(u, self.bundle)
                if not ok:
                    raise RuntimeError(msg)
                return msg
            check(f"RSS {url}", one)
        self.done.emit()


class AboutTab(QWidget):
    def __init__(self, app_ctx, parent=None):
        """app_ctx — главное окно: нужны data_dir(), config_path, bundle_path(), token-статус, фиды."""
        super().__init__(parent)
        self.ctx = app_ctx
        self.log_path = setup_logging(self.ctx.data_dir())
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)

        self.lbl_env = QLabel(); self.lbl_env.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_env.setFont(mono_font(11))
        lay.addWidget(QLabel("<b>PortfolioCheck</b> — личный инструмент проверки портфеля против своих правил. "
                             "Не является инвестиционной рекомендацией."))
        lay.addWidget(self.lbl_env)

        row = QHBoxLayout()
        for text, slot in (("Проверить подключения", self.run_checks), ("Скопировать диагностику", self.copy_diag),
                           ("Открыть папку логов", self.open_logs), ("Очистить лог", self.clear_log), ("Обновить", self.refresh)):
            b = QPushButton(text); b.clicked.connect(slot); row.addWidget(b)
        row.addStretch(); lay.addLayout(row)

        lay.addWidget(QLabel("Диагностический лог (последние 200 строк; ошибки помечены ERROR):"))
        self.txt = QPlainTextEdit(); self.txt.setReadOnly(True); self.txt.setFont(mono_font(11))
        self.txt.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.txt, 1)
        self.refresh()

    # ---- данные ------------------------------------------------------------

    def env_text(self) -> str:
        return environment_info(self.ctx.data_dir(), self.ctx.config_path, self.ctx.bundle_path(),
                                bool(self.ctx.token_present()), self.ctx.storage_mode())

    def refresh(self) -> None:
        self.lbl_env.setText(self.env_text())
        self.txt.setPlainText(tail(self.log_path))
        self.txt.verticalScrollBar().setValue(self.txt.verticalScrollBar().maximum())

    def run_checks(self) -> None:
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            return
        log.info("--- проверка подключений ---")
        self.txt.appendPlainText(f"\n--- проверка подключений {datetime.now():%H:%M:%S} ---")
        self.worker = ConnectivityWorker(self.ctx.token_present(), self.ctx.bundle_path(), self.ctx.feeds())
        self.worker.line.connect(self.txt.appendPlainText)
        self.worker.done.connect(lambda: self.txt.appendPlainText("--- готово ---"))
        self.worker.start()

    def copy_diag(self) -> None:
        text = "== Окружение ==\n" + self.env_text() + "\n\n== Лог (хвост) ==\n" + tail(self.log_path, 300)
        QGuiApplication.clipboard().setText(text)
        self.txt.appendPlainText("\n[диагностика скопирована в буфер обмена — токена и сумм в ней нет]")

    def open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path.parent)))

    def clear_log(self) -> None:
        try:
            self.log_path.write_text("", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.error("не удалось очистить лог: %s", e)
        log.info("лог очищен пользователем")
        self.refresh()


class AboutWindow(QDialog):
    """«О программе» — отдельным окном, как Настройки (раньше было вкладкой рядом с рабочими экранами).
    На macOS пункт меню уезжает в меню приложения (AboutRole)."""

    def __init__(self, app_ctx, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе PortfolioCheck")
        self.setModal(False)
        self.tab = AboutTab(app_ctx, self)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.tab)
        self.resize(900, 620)

    def showEvent(self, e) -> None:
        self.tab.refresh()                        # свежий хвост лога при каждом открытии
        super().showEvent(e)

    def closeEvent(self, e) -> None:
        wk = getattr(self.tab, "worker", None)
        if wk is not None and wk.isRunning():     # проверка подключений — не уничтожать живой поток
            wk.wait(15000)
        super().closeEvent(e)
