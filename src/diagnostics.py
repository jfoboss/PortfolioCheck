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
from PySide6.QtGui import QDesktopServices, QGuiApplication
from osutil import SECRET_STORE, mono_font
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QDialog
from widgets import is_running, stop_workers
import style as SY

APP_VERSION = "1.5"
REPO_URL = "https://github.com/jfoboss/PortfolioCheck"
ABOUT_TEXT = (
    "Настольное приложение для macOS, Windows и Linux: сверяет портфель в Т-Инвестициях с твоими правилами — "
    "целевыми долями классов активов, лимитами на эмитентов, правилами добора — и подсвечивает отклонения. "
    "Плюс тепловые карты, новости по бумагам, скринер дивидендных идей, сценарии и калькулятор «иду ли я по плану».")
DISCLAIMER = ("Не является инвестиционной рекомендацией: программа только считает и показывает, решения принимаешь ты. "
              "Токен T-Invest — только на чтение, сделок программа не совершает. Настройки и данные хранятся у тебя, "
              "токен и ключи ИИ — в системном хранилище.")
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
                msg = SY.err_text(e, 200)
                self.line.emit(f"✗ {name}: {msg}")
                log.error("check %s: %s", name, msg)

        check(f"Токен в {SECRET_STORE}", lambda: (_ for _ in ()).throw(RuntimeError("не задан")) if not self.token else "задан")
        check("CA-бандл", lambda: f"{self.bundle.read_text(errors='replace').count('BEGIN CERTIFICATE')} серт." if self.bundle.exists()
              else (_ for _ in ()).throw(RuntimeError("не собран")))

        def api():
            import portfolio_check as pc
            with pc.connect(self.token) as c:
                accs = c.users.get_accounts().accounts
                return f"счетов: {len(accs)}"
        if self.token:
            check("T-Invest API (GetAccounts)", api)

        def cbr():
            from macro import fetch_cbr_macro
            m = fetch_cbr_macro(); return f"{m.period}: инфляция {m.inflation}%, ставка {m.key_rate}%"
        check("ЦБ РФ (инфляция)", cbr)

        def factor(url_tmpl, parse, what):
            def run():
                from datetime import date, timedelta
                from net import fetch as _fetch
                t = date.today(); f = t - timedelta(days=10)
                url = url_tmpl.format(frm=f"{f:%d/%m/%Y}" if "cbr" in url_tmpl else f.isoformat(),
                                      till=f"{t:%d/%m/%Y}" if "cbr" in url_tmpl else t.isoformat(), start=0)
                rows = parse(_fetch(url))
                return f"{what}: {len(rows)} значений, последнее {rows[-1][0]} = {rows[-1][1]:,.2f}" if rows else "пусто"
            return run
        import factors as FA
        check("MOEX ISS (индекс для сценариев)", factor(FA.ISS_URL, FA.parse_iss, "IMOEX"))
        check("ЦБ РФ (курс доллара для сценариев)", factor(FA.CBR_URL, FA.parse_cbr, "USD/RUB"))

        def calendar():
            from datetime import date
            import macro
            from net import fetch as _fetch
            ev = macro.upcoming(macro.parse_cbr_calendar(_fetch(macro.CAL_URL).decode("utf-8", "replace")), date.today())
            meet = next((e for e in ev if e.kind == macro.MEETING), None)
            return f"событий {len(ev)}, ближайшее заседание {meet.date}" if meet else f"событий {len(ev)}, заседаний впереди нет"
        check("ЦБ РФ (календарь решений по ставке)", calendar)

        def fundamentals():
            import portfolio_check as pc
            from tinkoff.invest.schemas import GetAssetFundamentalsRequest, InstrumentStatus
            with pc.connect(self.token) as c:
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
        if is_running(getattr(self, "worker", None)):
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
        stop_workers([getattr(self.tab, "worker", None)], 15000, interrupt=False)   # не уничтожать живой поток
        super().closeEvent(e)


class AppAboutDialog(QDialog):
    """«О программе»: что это, версия, ссылка на GitHub. Диагностика и лог — отдельным окном (кнопка внизу)."""
    TEXT_W = 480

    def __init__(self, open_diag=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе PortfolioCheck")
        self.setModal(False)
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 16); lay.setSpacing(10)
        head = QHBoxLayout()
        icon = QLabel()
        svg = Path(__file__).with_name("icon.svg")
        if svg.exists():
            from PySide6.QtGui import QIcon
            icon.setPixmap(QIcon(str(svg)).pixmap(64, 64))
        self.lbl_title = QLabel(f"<b style='font-size:18px'>PortfolioCheck</b><br>версия {APP_VERSION}")
        head.addWidget(icon); head.addWidget(self.lbl_title, 1)
        lay.addLayout(head)
        self.lbl_text = QLabel(ABOUT_TEXT); self.lbl_text.setWordWrap(True)
        self.lbl_note = QLabel(DISCLAIMER); self.lbl_note.setWordWrap(True); self.lbl_note.setStyleSheet("color: gray")
        self.lbl_link = QLabel(f'Исходный код, релизы и описание: <a href="{REPO_URL}">{REPO_URL.split("//")[1]}</a>'
                               "<br>Лицензия MIT.")
        self.lbl_link.setOpenExternalLinks(True); self.lbl_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        for w in (self.lbl_text, self.lbl_note, self.lbl_link):
            # ширина задана заранее: многострочная метка иначе считает высоту от минимальной ширины и обрезается
            w.setFixedWidth(self.TEXT_W); w.setMinimumHeight(w.heightForWidth(self.TEXT_W))
            lay.addWidget(w)
        row = QHBoxLayout(); row.addStretch(1)
        if open_diag is not None:
            b = QPushButton("Диагностика и лог…"); b.clicked.connect(open_diag); row.addWidget(b)
        close = QPushButton("Закрыть"); close.setDefault(True); close.clicked.connect(self.close); row.addWidget(close)
        lay.addLayout(row)
        self.adjustSize()
