"""
settings_dialog.py — окно «Настройки» (немодальное, автосохранение, без кнопок Сохранить/Отмена).

Любое изменение → сигнал configChanged(dict) с задержкой ~400 мс → главное окно пишет конфиг и пересчитывает.
Невалидные состояния предотвращаются редакторами (спинбоксы, списки), а не диалогами об ошибке.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QColor
import paths
from PySide6.QtWidgets import (
    QDialog, QTabWidget, QVBoxLayout, QHBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
    QComboBox, QPushButton, QToolButton, QLabel, QDoubleSpinBox, QSpinBox, QLineEdit, QFormLayout,
    QCheckBox, QInputDialog, QMessageBox, QHeaderView, QAbstractItemView, QFileDialog, QRadioButton, QButtonGroup,
    QSizePolicy, QApplication,
)

from osutil import SECRET_STORE, SECRET_STORE_NAME, MACHINE, native_keys
from portfolio_check import CLASS_LABELS, guess_reference, DEFAULT_REFERENCES
import secrets_store as SEC

CLASSES = ["gold", "real_estate_wh", "real_estate_other", "equity_core", "equity_spec",
           "ballast", "foreign", "cash"]
TARGET_CLASSES = ["gold", "real_estate", "real_estate_wh", "real_estate_other",
                  "equity_core", "equity_spec", "ballast", "foreign"]
TARGET_LABELS = CLASS_LABELS

from ai import KEY_SERVICE as AI_KEY_SERVICE   # один источник имени — ai.py
import style as SY
from widgets import is_running, note, stop_workers
KEYRING_SERVICE = "portfolio-check.tinkoff"
KEYRING_USER = "invest-api-token"
INVEST_HOST = "invest-public-api.tinkoff.ru"

TOKEN_HOWTO = (
    "<b>Как получить токен</b><br>"
    "1. Открой Т-Инвестиции в браузере: "
    "<a href='https://www.tbank.ru/invest/settings/api/'>tbank.ru/invest/settings/api</a> "
    "(или в приложении: Настройки → Токены API).<br>"
    "2. «Выпустить токен» → доступ <b>«Только для чтения»</b> → ко всем счетам или к нужному.<br>"
    "3. Скопируй токен сразу — он показывается один раз. Начинается с <code>t.</code><br>"
    f"4. Вставь его сюда. Хранится только в {SECRET_STORE} этого {MACHINE}, в файлах не появляется.<br>"
    "<span style='color:gray'>Документация: <a href='https://developer.tbank.ru/invest/intro/intro/token'>developer.tbank.ru/invest/intro/intro/token</a></span>"
)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    hh.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    return t


def _plus_minus(table: QTableWidget, on_add, on_change) -> QHBoxLayout:
    """Стандартные macOS-кнопки + / − под таблицей."""
    lay = QHBoxLayout()
    lay.setSpacing(0)
    b_add, b_del = QToolButton(), QToolButton()
    b_add.setText("+"); b_del.setText("−")
    b_add.setFixedSize(28, 24); b_del.setFixedSize(28, 24)
    b_add.setToolTip("Добавить"); b_del.setToolTip("Удалить выбранное")
    b_add.clicked.connect(on_add)

    def delete():
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        for r in rows:
            table.removeRow(r)
        if rows:
            on_change()
    b_del.clicked.connect(delete)
    lay.addWidget(b_add); lay.addWidget(b_del); lay.addStretch()
    return lay


def _note(text: str, gray: bool = False, rich: bool = False) -> QLabel:
    """Подсказка в форме настроек: переносится и не тянет окно вширь (widgets.note с ignore_width)."""
    return note(text, gray=gray, rich=rich, ignore_width=True)


def _cell(text, editable: bool = True) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


def _pct_spin(value) -> QDoubleSpinBox:
    """Процент с состоянием «—» (не задано) при значении -1."""
    sb = QDoubleSpinBox()
    sb.setDecimals(1); sb.setSuffix(" %"); sb.setFrame(False)
    sb.setRange(-1, 100); sb.setSpecialValueText("—")
    sb.setValue(-1 if value is None else float(value))
    return sb


def _rub_spin(value) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(0, 1_000_000_000); sb.setSingleStep(10_000); sb.setSuffix(" ₽")
    sb.setGroupSeparatorShown(True); sb.setFrame(False)
    sb.setValue(int(value or 0))
    return sb


def build_ca_bundle(out_path: Path) -> str:
    """Корни grpcio + промежуточные/корень из цепочки T-Invest API → out_path."""
    import grpc
    roots = Path(grpc.__file__).parent / "_cython" / "_credentials" / "roots.pem"
    if not roots.exists():
        raise RuntimeError(f"Не найден roots.pem у grpcio: {roots}")
    chain = server_chain(INVEST_HOST)
    if not chain:
        raise RuntimeError("Не удалось получить цепочку сертификатов от API (нет сети?)")
    extra = chain[1:] if len(chain) > 1 else chain          # без листового сертификата сервера
    text = roots.read_text(encoding="utf-8").rstrip("\n") + "\n" + "\n".join(pem for pem, _ in extra) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return "Добавлено из цепочки API:\n" + "\n".join(f"  • {subj}" for _, subj in extra)


def server_chain(host: str, port: int = 443, timeout: int = 20) -> list[tuple[str, str]]:
    """Цепочка сертификатов сервера [(PEM, subject)] средствами Python — без консольного openssl (его нет в Windows).
    Проверку не делаем намеренно: цель — забрать промежуточные и корень (Минцифры), которых нет в roots.pem."""
    import socket
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            if hasattr(tls, "get_unverified_chain"):              # Python 3.13+: список DER
                raw = tls.get_unverified_chain()
            else:                                                 # 3.10–3.12: тот же метод у внутреннего объекта
                raw = tls._sslobj.get_unverified_chain()
    out = []
    for c in raw or []:
        if isinstance(c, (bytes, bytearray)):
            pem, subj = ssl.DER_cert_to_PEM_cert(bytes(c)), ""
        else:
            pem = c.public_bytes(ssl._ssl.ENCODING_PEM)
            info = c.get_info()
            subj = ", ".join(f"{k}={v}" for rdn in info.get("subject", ()) for k, v in rdn)
        out.append((pem.strip(), subj or "сертификат"))
    return out


# ---------------------------------------------------------------------------
# Окно настроек
# ---------------------------------------------------------------------------

def _compact(page: QWidget) -> None:
    """Страница настроек не шире ~700 px на любой ОС: в формах длинная подпись переносится над полем; числовые поля
    и списки не требуют ширины под самое длинное значение (на Windows поле суммы с большим максимумом просило
    ~500 px, и страница «Правила» уходила за 1000 px)."""
    from PySide6.QtWidgets import QAbstractSpinBox
    for form in page.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    for sb in page.findChildren(QAbstractSpinBox):
        sb.setMinimumWidth(min(sb.minimumSizeHint().width(), 160))
    for cb in page.findChildren(QComboBox):
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(min(cb.minimumContentsLength() or 18, 24))


def _set_check_mark(item, ok: bool, msg: str = "") -> None:
    """✓/✗ с цветом в ячейке таблицы источников (полный текст — в подсказке)."""
    mark, color = SY.ok_mark(ok)
    item.setText(mark + (f" {msg[:30] if ok else short_error(msg)}" if msg else ""))
    item.setForeground(QColor(color)); item.setToolTip(msg)


def short_error(msg: str) -> str:
    """Ошибка загрузки ленты → пара слов для узкой колонки; полный текст — в подсказке."""
    m = msg.lower()
    for key, text in (("certificate_verify_failed", "сертификат не прошёл проверку"), ("ssl", "ошибка TLS"),
                      ("timed out", "нет ответа"), ("timeout", "нет ответа"), ("403", "доступ запрещён (403)"),
                      ("404", "адрес не найден (404)"), ("name or service", "сайт не найден (DNS)"),
                      ("nodename", "сайт не найден (DNS)"), ("getaddrinfo", "сайт не найден (DNS)"),
                      ("refused", "соединение отклонено"), ("новостей: 0", "лента пустая")):
        if key in m:
            return text
    return msg[:30] + ("…" if len(msg) > 30 else "")


class FeedCheckWorker(QThread):
    """Параллельная проверка RSS-источников: сигнал по каждому, как только он ответил."""
    one = Signal(str, bool, str)
    done = Signal()

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        self.urls = urls

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import news as NEWS
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(NEWS.check_feed, u): u for u in self.urls}
            for f in as_completed(futures):
                if self.isInterruptionRequested():
                    return
                url = futures[f]
                try:
                    ok, msg = f.result()
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, SY.err_text(e, 80)
                self.one.emit(url, ok, msg)
        self.done.emit()


class ModelsWorker(QThread):
    done = Signal(object, str)
    failed = Signal(str)

    def __init__(self, cache_dir, proxy: str, force: bool):
        super().__init__()
        self.cache_dir, self.proxy, self.force = cache_dir, proxy, force

    def run(self) -> None:
        import ai as AI
        try:
            models, src = AI.fetch_openrouter_models(self.cache_dir, self.proxy, force=self.force)
            self.done.emit(models, src)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 150))


class AutoHeightLabel(QLabel):
    """Многострочная подпись, высота которой всегда соответствует текущей ширине и тексту
    (в форме Qt иначе запоминает высоту, посчитанную при минимальной ширине)."""

    def __init__(self, text: str = "", gray: bool = True):
        super().__init__(text)
        self.setWordWrap(True); self.setTextFormat(Qt.RichText)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if gray:
            self.setStyleSheet("color: gray")

    def _fit(self) -> None:
        if not self.text():
            self.setFixedHeight(0); return
        h = self.heightForWidth(max(self.width(), 200))
        if h > 0 and h != self.height():
            self.setFixedHeight(h)

    def setText(self, t: str) -> None:
        super().setText(t); self._fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e); self._fit()


class TierWorker(QThread):
    done = Signal(object)

    def __init__(self, key: str, proxy: str):
        super().__init__(); self.key, self.proxy = key, proxy

    def run(self) -> None:
        import ai as AI
        try:
            self.done.emit(AI.openrouter_key_info(self.key, self.proxy))
        except Exception:  # noqa: BLE001
            self.done.emit({})


class CheckWorker(QThread):
    """Проверка модели по кнопке — в фоне: запрос к модели может идти десятки секунд, окно не должно замирать."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, conf, cache_dir, parent=None):
        super().__init__(parent)             # родитель держит поток живым, даже если окно настроек отпустили
        self.conf, self.cache_dir = conf, cache_dir

    def run(self) -> None:
        import ai as AI
        c = self.conf
        try:
            self.done.emit(AI.check_model(c.provider, c.key, c.model, c.proxy, c.base_url, cache_dir=self.cache_dir))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 200))


class ModelPicker(QWidget):
    """Поле модели: для OpenRouter — выпадающий список с поиском по подстроке, фильтр бесплатных и ★ рекомендуемые;
    для остальных провайдеров — обычное поле ввода. Интерфейс как у QLineEdit: text / setText / textChanged."""
    textChanged = Signal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QCompleter
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self.combo = QComboBox(); self.combo.setEditable(True); self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setMaxVisibleItems(18); self.combo.setMinimumContentsLength(40)
        comp = QCompleter(self.combo.model(), self.combo)
        comp.setFilterMode(Qt.MatchContains); comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.combo.setCompleter(comp)
        self.combo.lineEdit().setText(text)
        self.combo.lineEdit().textEdited.connect(self._emit)
        self.combo.activated.connect(self._picked)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        self.cb_free = QCheckBox("только бесплатные"); self.cb_free.setChecked(True)
        self.form = None                 # строки каталога живут в форме настроек отдельными строками
        self.cb_free.toggled.connect(self._fill)
        self.b_reload = QPushButton("Обновить список"); self.b_reload.setToolTip("Загрузить свежий каталог моделей OpenRouter")
        # статус каталога — своей строкой под кнопками (форма ставит её в блок каталога): справа от кнопки ему
        # доставалась узкая колонка, текст переносился в 3–4 строки и обрезался по высоте
        self.lbl = AutoHeightLabel()
        row.addWidget(self.cb_free); row.addWidget(self.b_reload); row.addStretch(1)
        self.catalog_row = QWidget(); self.catalog_row.setLayout(row)
        lay.addWidget(self.combo)
        self.why = AutoHeightLabel()
        self.models: list[dict] = []
        self.set_catalog_mode(False)

    # — совместимость с QLineEdit —
    def text(self) -> str:
        """Чистый id модели: если в поле подпись пункта списка — берём его id, иначе то, что вписано."""
        t = self.combo.lineEdit().text().strip()
        i = self.combo.findText(t, Qt.MatchExactly)
        return (self.combo.itemData(i) or t) if i >= 0 else t

    def setText(self, t: str) -> None:
        self.combo.setCurrentIndex(-1); self.combo.lineEdit().setText(t)

    def setPlaceholderText(self, t: str) -> None:
        self.combo.lineEdit().setPlaceholderText(t)

    def _emit(self, *_):
        self.textChanged.emit(self.text()); self._show_why()

    def _picked(self, idx: int) -> None:
        mid = self.combo.itemData(idx)
        if mid:
            # в поле оставляем чистый id — его и сохраним в конфиг
            self.combo.lineEdit().setText(mid)
        self._emit()

    # — каталог —
    def set_catalog_mode(self, on: bool) -> None:
        box = self.catalog_row.parentWidget()
        if self.form is not None and box is not None:
            self.form.setRowVisible(box, on)
        self.catalog_row.setVisible(on); self.why.setVisible(on); self.lbl.setVisible(on)
        if not on:
            cur = self.combo.lineEdit().text()
            self.combo.blockSignals(True); self.combo.clear(); self.combo.blockSignals(False)
            self.combo.lineEdit().setText(cur)

    def set_models(self, models: list[dict], note: str) -> None:
        import ai as AI
        self.models = models; self.lbl.setText(note); self._fill()
        self.setPlaceholderText(f"по умолчанию: {AI.default_model('openrouter')}")

    def _fill(self) -> None:
        import ai as AI
        cur = self.combo.lineEdit().text()
        only_free = self.cb_free.isChecked()
        items = [m for m in self.models if m["free"] or not only_free]
        self.combo.blockSignals(True); self.combo.clear()
        for m in items:
            self.combo.addItem(AI.model_label(m), m["id"])
            self.combo.setItemData(self.combo.count() - 1, f"{m['id']}\n{m['why']}", Qt.ToolTipRole)
        self.combo.setCurrentIndex(-1); self.combo.blockSignals(False)
        self.combo.lineEdit().setText(cur)
        rec = sum(1 for m in items if m["recommended"])
        if self.models:
            self.lbl.setText(f"{len(items)} моделей{' (бесплатные)' if only_free else ''}, рекомендуемых ★ {rec}. "
                             "Начни печатать — список отфильтруется.")
        self._show_why()

    def _show_why(self) -> None:
        import ai as AI
        mid = self.text()
        if not mid and self.models:
            mid = AI.default_model("openrouter")
            prefix = f"Поле пустое — будет использована <code>{mid}</code>. "
        else:
            prefix = ""
        m = next((x for x in self.models if x["id"] == mid), None)
        if m is None:
            if not self.models or not mid:
                self.why.setText(prefix); return
            rec = next((x["id"] for x in self.models if x["free"] and x["recommended"]), "")
            self.why.setText(SY.span("Этой модели сейчас нет в каталоге OpenRouter — бесплатные версии снимают без "
                                     "предупреждения.", SY.WARN) + (f" Например, подойдёт <code>{rec}</code> ★." if rec else ""))
            return
        self.why.setText(prefix + ("★ Рекомендуется: " if m["recommended"] else "") + (m["why"] or ""))


class SettingsDialog(QDialog):
    configChanged = Signal(dict)
    storageChanged = Signal(object)   # Path новой папки данных
    accessChanged = Signal()          # токен задан/удалён или бандл собран

    def __init__(self, cfg: dict, bundle_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setModal(False)
        screen = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        if screen is not None:
            self.setMaximumSize(screen.width() - 40, screen.height() - 60)
            self.resize(min(820, screen.width() - 80), min(560, screen.height() - 120))
        else:
            self.resize(820, 560)
        self.cfg = cfg
        self.bundle_path = bundle_path
        self._loading = True
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._emit)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_general(), "Общие")
        self.tabs.addTab(self._tab_targets(), "Цели")
        self.tabs.addTab(self._tab_instruments(), "Инструменты")
        self.tabs.addTab(self._tab_rules(), "Правила")
        self.tabs.addTab(self._tab_frozen(), "Заморозки")
        self.tabs.addTab(self._tab_caps(), "Лимиты")
        self.tabs.addTab(self._tab_fx(), "Курсы")
        self.tabs.addTab(self._tab_news(), "Новости")
        self.tabs.addTab(self._tab_screener(), "Скринер")
        self.tabs.addTab(self._tab_alerts(), "Оповещения")
        self.tabs.addTab(self._tab_ai(), "ИИ")
        self.tabs.addTab(self._tab_access(), "Доступ")

        # Разделы — списком слева, как в системных настройках macOS. Строка из 12 вкладок на macOS не прокручивается
        # и распирала окно шире экрана MacBook. QTabWidget остаётся (без своей строки): settings_win.tabs.setCurrentIndex
        # и палитра команд работают как раньше. Страницы — в прокрутке: высокая страница не растягивает окно.
        from PySide6.QtWidgets import QFrame, QListWidget, QScrollArea
        pages = [(self.tabs.widget(i), self.tabs.tabText(i)) for i in range(self.tabs.count())]
        for page, _ in pages:
            _compact(page)
        self.tabs.clear()
        for page, name in pages:
            sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.NoFrame); sa.setWidget(page)
            self.tabs.addTab(sa, name)
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        self.nav = QListWidget(); self.nav.setObjectName("settings_nav")
        self.nav.addItems([self.tabs.tabText(i) for i in range(self.tabs.count())])
        self.nav.setFixedWidth(self.nav.sizeHintForColumn(0) + 36)
        self.nav.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda i: self.nav.currentRow() != i and self.nav.setCurrentRow(i))
        self.nav.setCurrentRow(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 0, 0)
        lay.addWidget(self.nav); lay.addWidget(self.tabs, 1)
        self._loading = False

    # ---- автосохранение -----------------------------------------------------

    def _touch(self, *_) -> None:
        if not self._loading:
            self._timer.start()

    def _emit(self) -> None:
        self.cfg = self.collect()
        self.configChanged.emit(self.cfg)

    def _watch_table(self, t: QTableWidget) -> None:
        t.itemChanged.connect(self._touch)

    def names(self) -> dict[str, str]:
        return {str(k): v for k, v in self.cfg.get("instrument_names", {}).items()}

    # ---- Общие --------------------------------------------------------------

    INTERVALS = [("выключено", 0), ("каждые 15 минут", 15), ("каждые 30 минут", 30), ("каждый час", 60),
                 ("каждые 4 часа", 240), ("раз в день", 1440)]

    def _tab_general(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w); form.setContentsMargins(20, 16, 20, 16)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        rf = self.cfg.get("refresh", {})
        self.cb_on_launch = QCheckBox("Обновлять при запуске, если данные старше интервала")
        self.cb_on_launch.setToolTip("Иначе при запуске показываются сохранённые на диске данные, а обновление — по кнопке или по расписанию")
        self.cb_on_launch.setChecked(bool(rf.get("on_launch", True)))
        self.cb_on_launch.toggled.connect(self._touch)
        self.cmb_interval = QComboBox()
        for label, minutes in self.INTERVALS:
            self.cmb_interval.addItem(label, minutes)
        self.cmb_interval.setCurrentIndex(max(0, self.cmb_interval.findData(int(rf.get("interval_min", 60)))))
        self.cmb_interval.currentIndexChanged.connect(self._touch)
        self.cb_privacy = QCheckBox("Запускать со скрытыми суммами")
        self.cb_privacy.setToolTip("Проценты и названия видны, суммы — нет")
        self.cb_privacy.setChecked(bool(rf.get("privacy_on_launch", True)))
        self.cb_privacy.toggled.connect(self._touch)
        self.cb_notify = QCheckBox("Уведомлять о новых сигналах после обновления")
        self.cb_notify.setChecked(bool(rf.get("notify", True)))
        self.cb_notify.toggled.connect(self._touch)
        form.addRow(self.cb_on_launch)
        form.addRow("Обновлять в фоне:", self.cmb_interval)
        form.addRow(self.cb_notify)
        form.addRow(self.cb_privacy)
        form.addRow(_note("Переключить в любой момент: кнопка «Скрыть/Показать суммы» на панели или "
                            f"{native_keys('Ctrl+Shift+H')}.", gray=True))
        form.addRow(_note("Обновление — это один запрос портфеля к API; квота T-Invest на чтение большая, "
                          "но чаще раза в 15 минут смысла нет: цены ЗПИФ меняются редко, а частые взгляды на портфель "
                          "провоцируют лишние решения.", gray=True))

        # ---- стратегия: импорт / экспорт ----
        form.addRow(QLabel("<b>Стратегия</b>"))
        row_s = QHBoxLayout()
        b_exp = QPushButton("Экспорт стратегии…"); b_imp = QPushButton("Импорт стратегии…")
        b_exp.setToolTip("Сохранить правила, коридоры и классификацию в YAML — без токена, id счёта и личных сумм")
        b_imp.setToolTip("Загрузить стратегию из YAML: экспорт другого пользователя или ответ ChatGPT по промпту из docs/")
        b_exp.clicked.connect(self._export_strategy); b_imp.clicked.connect(self._import_strategy)
        b_prm = QPushButton("Промпт для ChatGPT")
        b_prm.setToolTip("Скопировать в буфер промпт: модель расспросит о целях и соберёт файл стратегии")
        b_prm.clicked.connect(lambda: __import__("strategy").copy_prompt_to_clipboard(self))
        row_s.addWidget(b_exp); row_s.addWidget(b_imp); row_s.addWidget(b_prm); row_s.addStretch()
        form.addRow(row_s)
        self.cb_auto_cls = QCheckBox("Классифицировать новые бумаги автоматически")
        self.cb_auto_cls.setToolTip("Акции → ядро, золото → металлы, ЗПИФ → недвижимость; поправить можно в «Инструментах»")
        self.cb_auto_cls.setChecked(bool(self.cfg.get("auto_classify", True))); self.cb_auto_cls.toggled.connect(self._touch)
        form.addRow(self.cb_auto_cls)

        # ---- хранилище настроек ----
        form.addRow(QLabel("<b>Где хранить настройки и снапшоты</b>"))
        cur = paths.data_dir(); mode = paths.mode_of(cur)
        self.rb_icloud = QRadioButton("iCloud Drive — общие для всех твоих Mac" + ("" if paths.icloud_available() else " (не включён)"))
        self.rb_local = QRadioButton("Только на этом компьютере")
        self.rb_custom = QRadioButton("Другая папка (Dropbox, MEGAsync, …)")
        self.rb_icloud.setEnabled(paths.icloud_available())
        if paths.DEMO_DIR is not None:                 # демо: временная папка, переключать некуда
            for rb in (self.rb_icloud, self.rb_local, self.rb_custom):
                rb.setEnabled(False)
        {"icloud": self.rb_icloud, "local": self.rb_local, "custom": self.rb_custom}[mode].setChecked(True)
        self.lbl_storage = _note(str(cur), gray=True)
        grp = QButtonGroup(self)
        for rb in (self.rb_icloud, self.rb_local, self.rb_custom):
            grp.addButton(rb); form.addRow(rb)
        grp.buttonClicked.connect(self._storage_clicked)
        form.addRow(self.lbl_storage)
        form.addRow(_note("При переключении файлы копируются в новую папку. Токен API в общую папку не попадает — "
                          f"он в {SECRET_STORE} каждого {MACHINE}, вводится один раз на машину.", gray=True))
        return w

    def _export_strategy(self) -> None:
        import strategy as ST
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт стратегии", str(Path.home() / "portfolio-strategy.yaml"), "YAML (*.yaml)")
        if not path:
            return
        Path(path).write_text(ST.export_strategy(self.collect()), encoding="utf-8")
        QMessageBox.information(self, "Стратегия сохранена", f"{path}\n\nТокена, id счёта и личных сумм в файле нет.")

    def _import_strategy(self) -> None:
        import strategy as ST
        path, _ = QFileDialog.getOpenFileName(self, "Импорт стратегии", str(Path.home()), "YAML (*.yaml *.yml *.txt);;Все файлы (*)")
        if not path:
            return
        try:
            new, warns, replaced = ST.import_strategy(self.collect(), Path(path).read_text(encoding="utf-8"))
        except ValueError as e:
            QMessageBox.warning(self, "Стратегия не принята", str(e)); return
        if QMessageBox.question(self, "Импорт стратегии", "Будут заменены разделы: " + ", ".join(replaced)
                                + ("\n\nЗамечания:\n" + "\n".join(warns) if warns else "") + "\n\nПродолжить?") != QMessageBox.Yes:
            return
        self.cfg = new
        self.configChanged.emit(self.cfg)
        QMessageBox.information(self, "Готово", "Стратегия импортирована. Окно настроек перезагрузится с новыми значениями.")
        self.close()

    def _storage_clicked(self, btn) -> None:
        cur = paths.data_dir()
        if btn is self.rb_icloud:
            new = paths.ICLOUD_DIR
        elif btn is self.rb_local:
            new = paths.LOCAL_DIR
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Папка для настроек PortfolioCheck", str(Path.home()))
            if not chosen:
                {"icloud": self.rb_icloud, "local": self.rb_local, "custom": self.rb_custom}[paths.mode_of(cur)].setChecked(True)
                return
            new = Path(chosen) / paths.APP_NAME
        if new == cur:
            return
        new = paths.set_data_dir(new)
        self.bundle_path = new / "ca-bundle.pem"
        self.lbl_storage.setText(str(new))
        self._refresh_bundle_label()
        self.storageChanged.emit(new)

    # ---- Цели ---------------------------------------------------------------

    def _tab_targets(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Коридоры долей по классам, % от стоимости портфеля. «—» — класс не проверяется."))
        self.t_targets = _table(["Класс", "Минимум", "Максимум"])
        targets = self.cfg.get("targets", {})
        for cls in TARGET_CLASSES:
            r = self.t_targets.rowCount(); self.t_targets.insertRow(r)
            it = _cell(TARGET_LABELS[cls], editable=False); it.setData(Qt.UserRole, cls)
            self.t_targets.setItem(r, 0, it)
            rng = targets.get(cls)
            lo = _pct_spin(rng["min"] if rng else None); hi = _pct_spin(rng["max"] if rng else None)
            # min не может быть больше max: подтягиваем соседа вместо диалога об ошибке
            lo.valueChanged.connect(lambda v, hi=hi: hi.setValue(v) if hi.value() >= 0 and v > hi.value() else None)
            hi.valueChanged.connect(lambda v, lo=lo: lo.setValue(v) if lo.value() >= 0 and 0 <= v < lo.value() else None)
            lo.valueChanged.connect(self._touch); hi.valueChanged.connect(self._touch)
            self.t_targets.setCellWidget(r, 1, lo); self.t_targets.setCellWidget(r, 2, hi)
        lay.addWidget(self.t_targets)
        return w

    # ---- Инструменты --------------------------------------------------------

    def _tab_instruments(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Класс для каждой бумаги; бумаги из портфеля без класса — внизу. "
                            "«Заблок.» — позиция заблокирована (флаг API, нулевая цена или твоя отметка). "
                            "«Эталон» — торгуемый инструмент, по цене которого считается реальная стоимость заблокированной: "
                            "выбери из списка бумаг портфеля или впиши тикер."))
        self.t_instr = _table(["Тикер / ISIN", "Название", "Класс", "Заблок.", "Эталон"], stretch_col=1)
        mapping = {str(k): v for k, v in self.cfg.get("instruments", {}).items()}
        names = self.names()
        refs = {str(k): str(v) for k, v in (self.cfg.get("reference_tickers") or {}).items()}
        blocked = {str(t) for t in (self.cfg.get("blocked_tickers") or [])} | set(DEFAULT_REFERENCES)
        rows = list(mapping.items()) + [(t, "") for t in names if t not in mapping]
        self._known_tickers = sorted({t for t, _ in rows})
        for ticker, cls in rows:
            ref = refs.get(ticker) or guess_reference(ticker, set(self._known_tickers))
            self._add_instr_row(ticker, names.get(ticker, ""), cls, ref, ticker in blocked)
        self._watch_table(self.t_instr)
        lay.addWidget(self.t_instr)
        lay.addLayout(_plus_minus(self.t_instr, lambda: self._add_instr_row("", "", "", "", False), self._touch))
        return w

    def _add_instr_row(self, ticker: str, name: str, cls: str, ref: str = "", blocked: bool = False) -> None:
        r = self.t_instr.rowCount(); self.t_instr.insertRow(r)
        self.t_instr.setItem(r, 0, _cell(ticker))
        self.t_instr.setItem(r, 1, _cell(name, editable=False))
        cb = QCheckBox(); cb.setChecked(blocked); cb.toggled.connect(self._touch)
        cb.setStyleSheet("margin-left: 10px")
        self.t_instr.setCellWidget(r, 3, cb)
        ref_box = QComboBox(); ref_box.setEditable(True); ref_box.setFrame(False)
        ref_box.addItem("")
        for t in getattr(self, "_known_tickers", []):
            if t != ticker:
                ref_box.addItem(t)
        ref_box.setCurrentText(ref)
        ref_box.currentTextChanged.connect(self._touch)
        self.t_instr.setCellWidget(r, 4, ref_box)
        combo = QComboBox(); combo.setFrame(False)
        combo.addItem("не задан", "")
        for c in CLASSES:
            combo.addItem(CLASS_LABELS[c], c)
        combo.setCurrentIndex(max(0, combo.findData(cls)))
        if ticker in (self.cfg.get("auto_classified") or []):
            combo.setToolTip("Класс определён автоматически — проверь")
            combo.setStyleSheet(f"color: {SY.WARN}")
        combo.currentIndexChanged.connect(self._touch)
        combo.currentIndexChanged.connect(lambda _=0, c=combo: (c.setStyleSheet(""), c.setToolTip("")))
        self.t_instr.setCellWidget(r, 2, combo)

    def refresh_names(self, names: dict[str, str]) -> None:
        """После загрузки портфеля: проставить названия и добавить новые бумаги без класса."""
        self._loading = True
        present = set()
        for r in range(self.t_instr.rowCount()):
            t = self.t_instr.item(r, 0).text().strip()
            present.add(t)
            if t in names and not self.t_instr.item(r, 1).text():
                self.t_instr.item(r, 1).setText(names[t])
        self._known_tickers = sorted(set(self._known_tickers) | set(names))
        for t, n in names.items():
            if t not in present:
                self._add_instr_row(t, n, "", guess_reference(t, set(self._known_tickers)), t in DEFAULT_REFERENCES)
        self._loading = False

    # ---- Правила ------------------------------------------------------------

    def _tab_rules(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w); form.setContentsMargins(20, 16, 20, 16)
        rules = self.cfg.get("rules", {})
        self.sp_dd = QDoubleSpinBox(); self.sp_dd.setRange(1, 90); self.sp_dd.setSuffix(" %")
        self.sp_dd.setValue(float(rules.get("drawdown_alert_pct", 10)))
        self.sp_single = QDoubleSpinBox(); self.sp_single.setRange(1, 100); self.sp_single.setSuffix(" %")
        self.sp_single.setValue(float(rules.get("single_name_max_pct", 11)))
        self.sp_tail = _rub_spin(rules.get("tail_threshold_rub", 30000)); self.sp_tail.setFrame(True)
        for sb in (self.sp_dd, self.sp_single, self.sp_tail):
            sb.valueChanged.connect(self._touch)
        form.addRow("Просадка от средней, чтобы стать кандидатом на добор:", self.sp_dd)
        form.addRow("Максимальная доля одного эмитента (кроме золота и кэша):", self.sp_single)
        form.addRow("Порог «хвоста» для мелких позиций балласта:", self.sp_tail)
        form.addRow(QLabel("Классы, из которых берутся кандидаты на добор:"))
        self.cb_classes: dict[str, QCheckBox] = {}
        allowed = set(rules.get("buy_candidates_classes", []))
        for c in CLASSES:
            cb = QCheckBox(CLASS_LABELS[c]); cb.setChecked(c in allowed)
            cb.toggled.connect(self._touch)
            self.cb_classes[c] = cb
            form.addRow(cb)
        return w

    # ---- Заморозки ----------------------------------------------------------

    def _tab_frozen(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Замороженные бумаги не попадают в кандидаты на добор, даже если просели."))
        self.t_frozen = _table(["Тикер / ISIN", "Причина"], stretch_col=1)
        for t, reason in self.cfg.get("rules", {}).get("frozen", {}).items():
            r = self.t_frozen.rowCount(); self.t_frozen.insertRow(r)
            self.t_frozen.setItem(r, 0, _cell(t)); self.t_frozen.setItem(r, 1, _cell(reason))
        self._watch_table(self.t_frozen)
        lay.addWidget(self.t_frozen)
        lay.addLayout(_plus_minus(self.t_frozen, lambda: self._add_text_row(self.t_frozen, 2), self._touch))
        return w

    # ---- Оповещения ----------------------------------------------------------

    def _tab_alerts(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Оповещения приходят системным уведомлением, <b>пока программа открыта</b> — после каждого "
                            "обновления портфеля, ленты и скринера. Каждое событие сообщается один раз; условие, которое "
                            "перестало выполняться и выполнилось снова, сообщит повторно. Тикер можно оставить пустым "
                            "для «до отсечки» (любая бумага) и «новость» (любая бумага портфеля); для «коридора» и «заседания ЦБ» он "
                            "не нужен.", rich=True))
        self.t_alerts = _table(["Условие", "Тикер", "Значение", "Вкл."], stretch_col=0)
        for rule in self.cfg.get("alerts") or []:
            self._add_alert_row(rule)
        self._watch_table(self.t_alerts)
        lay.addWidget(self.t_alerts)
        lay.addLayout(_plus_minus(self.t_alerts, lambda: (self._add_alert_row({"kind": "price_above", "on": True}),
                                                          self._touch()), self._touch))
        return w

    def _add_alert_row(self, rule: dict) -> None:
        import alerts as AL
        from PySide6.QtWidgets import QCheckBox, QComboBox
        r = self.t_alerts.rowCount(); self.t_alerts.insertRow(r)
        kind = QComboBox()
        for k, label in AL.KINDS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(rule.get("kind", "price_above"))))
        kind.currentIndexChanged.connect(self._touch)
        self.t_alerts.setCellWidget(r, 0, kind)
        self.t_alerts.setItem(r, 1, _cell(str(rule.get("ticker") or "").upper()))
        val = QDoubleSpinBox(); val.setRange(0, 10_000_000); val.setDecimals(2); val.setFrame(False)
        val.setValue(float(rule.get("value") or 0)); val.valueChanged.connect(self._touch)
        self.t_alerts.setCellWidget(r, 2, val)
        on = QCheckBox(); on.setChecked(bool(rule.get("on", True))); on.toggled.connect(self._touch)
        self.t_alerts.setCellWidget(r, 3, on)

    def _collect_alerts(self) -> list[dict]:
        out = []
        for r in range(self.t_alerts.rowCount()):
            out.append({"kind": self.t_alerts.cellWidget(r, 0).currentData(),
                        "ticker": self._text(self.t_alerts, r, 1).upper(),
                        "value": round(self.t_alerts.cellWidget(r, 2).value(), 4),
                        "on": self.t_alerts.cellWidget(r, 3).isChecked()})
        return out

    def _add_text_row(self, table: QTableWidget, cols: int) -> None:
        r = table.rowCount(); table.insertRow(r)
        for c in range(cols):
            table.setItem(r, c, _cell(""))
        table.editItem(table.item(r, 0))

    # ---- Лимиты -------------------------------------------------------------

    def _tab_caps(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Потолок стоимости отдельной позиции."))
        self.t_caps = _table(["Тикер / ISIN", "Лимит"])
        for t, cap in self.cfg.get("rules", {}).get("position_caps", {}).items():
            self._add_cap_row(t, cap)
        self._watch_table(self.t_caps)
        lay.addWidget(self.t_caps)
        lay.addLayout(_plus_minus(self.t_caps, lambda: self._add_cap_row("", 0), self._touch))
        return w

    def _add_cap_row(self, ticker: str, cap) -> None:
        r = self.t_caps.rowCount(); self.t_caps.insertRow(r)
        self.t_caps.setItem(r, 0, _cell(ticker))
        sb = _rub_spin(cap); sb.valueChanged.connect(self._touch)
        self.t_caps.setCellWidget(r, 1, sb)

    # ---- Курсы --------------------------------------------------------------

    def _tab_fx(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("Запасные курсы к рублю — используются, если API курс не отдал."))
        self.t_fx = _table(["Валюта", "Курс, ₽"])
        for cur, rate in self.cfg.get("fx_fallback", {}).items():
            self._add_fx_row(cur, rate)
        self._watch_table(self.t_fx)
        lay.addWidget(self.t_fx)
        lay.addLayout(_plus_minus(self.t_fx, lambda: self._add_fx_row("", 0), self._touch))
        return w

    def _add_fx_row(self, cur: str, rate) -> None:
        r = self.t_fx.rowCount(); self.t_fx.insertRow(r)
        self.t_fx.setItem(r, 0, _cell(cur))
        sb = QDoubleSpinBox(); sb.setRange(0, 100000); sb.setDecimals(2); sb.setFrame(False)
        sb.setValue(float(rate or 0)); sb.valueChanged.connect(self._touch)
        self.t_fx.setCellWidget(r, 1, sb)

    # ---- Новости ------------------------------------------------------------

    def _tab_news(self) -> QWidget:
        import news as NEWS
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(_note("RSS-источники ленты. Новости фильтруются по названиям твоих бумаг и алиасам; "
                            "недоступный источник просто пропускается. «Источники…» — включить/выключить готовые ленты, "
                            "«−» — убрать выбранную строку, «+» — вписать свой адрес."))
        self.t_feeds = _table(["Адрес RSS", "Статус"], stretch_col=0)
        feeds = (self.cfg.get("news") or {}).get("feeds") or NEWS.DEFAULT_FEEDS
        for u in feeds:
            r = self.t_feeds.rowCount(); self.t_feeds.insertRow(r)
            self.t_feeds.setItem(r, 0, _cell(u)); self.t_feeds.setItem(r, 1, _cell("", editable=False))
        self._watch_table(self.t_feeds)
        lay.addWidget(self.t_feeds)
        row = _plus_minus(self.t_feeds, lambda: self._add_text_row(self.t_feeds, 2), self._touch)
        b_add = QPushButton("Источники…"); b_add.setToolTip("Включить или выключить источники из каталога")
        b_add.clicked.connect(self._feeds_catalog)
        b_chk = QPushButton("Проверить"); b_chk.clicked.connect(self._feeds_check)
        row.addWidget(b_add); row.addWidget(b_chk)
        lay.addLayout(row)
        lay.addWidget(_note("Алиасы для поиска: тикер → слова через запятую (например, SBERP → Сбер, Греф). "
                            "Стандартные алиасы для твоих бумаг уже встроены; здесь — дополнения."))
        self.t_aliases = _table(["Тикер / ISIN", "Слова через запятую"], stretch_col=1)
        for t, words in ((self.cfg.get("news") or {}).get("aliases") or {}).items():
            r = self.t_aliases.rowCount(); self.t_aliases.insertRow(r)
            self.t_aliases.setItem(r, 0, _cell(t)); self.t_aliases.setItem(r, 1, _cell(", ".join(words)))
        self._watch_table(self.t_aliases)
        lay.addWidget(self.t_aliases)
        lay.addLayout(_plus_minus(self.t_aliases, lambda: self._add_text_row(self.t_aliases, 2), self._touch))
        return w

    def _feeds_urls(self) -> set:
        return {self._text(self.t_feeds, r, 0) for r in range(self.t_feeds.rowCount())}

    def _feeds_catalog(self) -> None:
        """Диалог управления источниками: отмеченные включены, снятые — убираются из списка.
        Адреса, добавленные вручную (вне каталога), остаются нетронутыми."""
        import news as NEWS
        from PySide6.QtWidgets import QDialog, QScrollArea, QDialogButtonBox
        dlg = QDialog(self); dlg.setWindowTitle("Источники новостей"); dlg.resize(560, 520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(_note("Отмеченные источники включены; снимите галочку, чтобы убрать источник из ленты. "
                            "Адреса, добавленные вручную, здесь не показываются и не затрагиваются. "
                            "После изменений нажмите «Проверить» — увидите, какие ленты отвечают.", gray=True))
        inner = QWidget(); il = QVBoxLayout(inner); have = self._feeds_urls(); boxes = []; status_labels = {}
        for cat, items in NEWS.FEED_CATALOG.items():
            row = QHBoxLayout(); row.addWidget(QLabel(f"<b>{cat}</b>"))
            b_all = QPushButton("все"); b_none = QPushButton("ничего")
            for b in (b_all, b_none):
                b.setFlat(True); b.setMaximumWidth(64)
            row.addStretch(); row.addWidget(b_all); row.addWidget(b_none)
            il.addLayout(row)
            cat_boxes = []
            for url, title in items:
                line = QHBoxLayout()
                cb = QCheckBox(title); cb.setToolTip(url)            # адрес — в подсказке: строка не распирает окно
                cb.setChecked(url in have); cb.setProperty("url", url)
                st = QLabel("проверяю…"); st.setStyleSheet("color: gray"); st.setFixedWidth(230)
                status_labels[url] = st
                line.addWidget(cb, 1); line.addWidget(st)
                boxes.append(cb); cat_boxes.append(cb); il.addLayout(line)
            b_all.clicked.connect(lambda _=False, bs=cat_boxes: [c.setChecked(True) for c in bs])
            b_none.clicked.connect(lambda _=False, bs=cat_boxes: [c.setChecked(False) for c in bs])
        il.addStretch()
        sc = QScrollArea(); sc.setWidget(inner); sc.setWidgetResizable(True); lay.addWidget(sc, 1)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # проверка всех источников каталога — сразу при открытии, параллельно, не блокируя окно
        results: dict[str, bool] = {}

        def on_one(url: str, ok: bool, msg: str) -> None:
            results[url] = ok
            lbl = status_labels.get(url)
            if lbl is not None:
                mark, color = SY.ok_mark(ok)
                lbl.setText(f"{mark} " + (msg[:30] if ok else short_error(msg)))
                lbl.setStyleSheet(f"color: {color}")
                lbl.setToolTip(msg)

        bottom = QHBoxLayout()
        b_recheck = QPushButton("Проверить снова")
        b_drop = QPushButton("Снять нерабочие")
        b_drop.setToolTip("Снять галочки у источников, которые не ответили")
        b_drop.clicked.connect(lambda: [cb.setChecked(False) for cb in boxes if results.get(cb.property("url")) is False])
        bottom.addWidget(b_recheck); bottom.addWidget(b_drop); bottom.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        bottom.addWidget(bb); lay.addLayout(bottom)

        def start_check() -> None:
            if getattr(dlg, "worker", None) is not None and dlg.worker.isRunning():
                return
            for lbl in status_labels.values():
                lbl.setText("проверяю…"); lbl.setStyleSheet("color: gray")
            dlg.worker = FeedCheckWorker(list(status_labels), dlg)
            dlg.worker.one.connect(on_one)
            dlg.worker.done.connect(lambda: b_recheck.setEnabled(True))
            b_recheck.setEnabled(False); dlg.worker.start()

        b_recheck.clicked.connect(start_check)
        QTimer.singleShot(0, start_check)

        def cleanup() -> None:
            stop_workers([getattr(dlg, "worker", None)], 3000)
        dlg.finished.connect(lambda _: cleanup())
        if dlg.exec() != QDialog.Accepted:
            return
        catalog_urls = {url for items in NEWS.FEED_CATALOG.values() for url, _ in items}
        wanted = {cb.property("url") for cb in boxes if cb.isChecked()}
        # что было в таблице вне каталога — сохраняем как есть; каталожные пересобираем по галочкам
        manual = [self._text(self.t_feeds, r, 0) for r in range(self.t_feeds.rowCount())
                  if self._text(self.t_feeds, r, 0) and self._text(self.t_feeds, r, 0) not in catalog_urls]
        ordered = [u for items in NEWS.FEED_CATALOG.values() for u, _ in items if u in wanted]
        self.t_feeds.setRowCount(0)
        for u in manual + ordered:
            r = self.t_feeds.rowCount(); self.t_feeds.insertRow(r)
            self.t_feeds.setItem(r, 0, _cell(u))
            ok = results.get(u)
            it = _cell("", editable=False)
            if ok is not None:
                _set_check_mark(it, ok)
            self.t_feeds.setItem(r, 1, it)
        self._touch()

    def _feeds_check(self) -> None:
        """Проверить каждый источник — в фоне (FeedCheckWorker), ✓/✗ в колонку «Статус» по мере ответов.
        Раньше проверка шла в UI-потоке через processEvents — окно замирало на медленных лентах (правило 4)."""
        if is_running(getattr(self, "_feeds_worker", None)):
            return
        rows = {}
        for r in range(self.t_feeds.rowCount()):
            url = self._text(self.t_feeds, r, 0)
            if url:
                rows[url] = r
                self.t_feeds.item(r, 1).setText("…")

        def one(url: str, ok: bool, msg: str) -> None:
            r = rows.get(url)
            it = self.t_feeds.item(r, 1) if r is not None and r < self.t_feeds.rowCount() else None
            if it is not None and self._text(self.t_feeds, r, 0) == url:
                _set_check_mark(it, ok, msg)
        self._feeds_worker = FeedCheckWorker(list(rows), self)
        self._feeds_worker.one.connect(one)
        self._feeds_worker.start()

    # ---- Скринер ------------------------------------------------------------

    def _tab_screener(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        sc = self.cfg.get("screener") or {}
        lay.addWidget(_note("Дополнительные критерии для «Идей» (0 — не фильтровать). Основные — дивдоходность, капитализация, "
                            "долг/EBITDA — задаются на экране «Рынок»."))
        form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.sp_avg5 = QDoubleSpinBox(); self.sp_avg5.setRange(0, 30); self.sp_avg5.setDecimals(1); self.sp_avg5.setSuffix(" %")
        self.sp_avg5.setValue(float(sc.get("min_avg5y_yield", 4.0))); self.sp_avg5.valueChanged.connect(self._touch)
        self.sp_pe = QDoubleSpinBox(); self.sp_pe.setRange(0, 100); self.sp_pe.setDecimals(1); self.sp_pe.setSpecialValueText("любой")
        self.sp_pe.setValue(float(sc.get("max_pe", 0))); self.sp_pe.valueChanged.connect(self._touch)
        self.sp_ff = QDoubleSpinBox(); self.sp_ff.setRange(0, 100); self.sp_ff.setDecimals(0); self.sp_ff.setSuffix(" %"); self.sp_ff.setSpecialValueText("любой")
        self.sp_ff.setValue(float(sc.get("min_free_float", 0))); self.sp_ff.valueChanged.connect(self._touch)
        self.ed_excl_sectors = QLineEdit(", ".join(sc.get("exclude_sectors") or [])); self.ed_excl_sectors.setPlaceholderText("например: IT, Здравоохранение")
        self.ed_excl_sectors.textChanged.connect(self._touch)
        form.addRow("Средняя дивдоходность за 5 лет ≥:", self.sp_avg5)
        self.sp_avg5.setToolTip("Отсекает разовых плательщиков")
        form.addRow("P/E ≤:", self.sp_pe)
        form.addRow("Free float ≥:", self.sp_ff)
        form.addRow("Исключить отрасли (через запятую):", self.ed_excl_sectors)
        lay.addLayout(form)
        lay.addWidget(_note("<b>Чёрный список</b> — бумаги, которые не предлагать никогда: проданные по решению, с плохим отношением "
                            "к миноритариям и т.п. Пополняется правым кликом по кандидату на экране «Рынок».", rich=True))
        self.t_black = _table(["Тикер", "Причина"], stretch_col=1)
        bl = sc.get("blacklist")
        if isinstance(bl, list):                       # старый формат — список тикеров
            bl = {t: "" for t in bl}
        bl = bl or {}
        for t, reason in bl.items():
            r = self.t_black.rowCount(); self.t_black.insertRow(r)
            self.t_black.setItem(r, 0, _cell(t)); self.t_black.setItem(r, 1, _cell(reason))
        self._watch_table(self.t_black)
        lay.addWidget(self.t_black)
        lay.addLayout(_plus_minus(self.t_black, lambda: self._add_text_row(self.t_black, 2), self._touch))
        return w

    # ---- ИИ -----------------------------------------------------------------


    def _tab_ai(self) -> QWidget:
        import ai as AI
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        cfg = self.cfg.get("ai") or {}
        lay.addWidget(_note("ИИ-сводка по новостям: тональность по каждой бумаге (позитив / нейтрально / негатив) и общий вывод. "
                            "Делается автоматически после загрузки новостей, если задан ключ (для Ollama ключ не нужен)."))
        self.ai_provider = QButtonGroup(self); self.ai_rbs = {}
        for key, meta in AI.PROVIDERS.items():
            rb = QRadioButton(meta["title"]); self.ai_rbs[key] = rb; self.ai_provider.addButton(rb); lay.addWidget(rb)
        self.ai_rbs.get(cfg.get("provider", "openrouter"), self.ai_rbs["openrouter"]).setChecked(True)
        self.ai_provider.buttonClicked.connect(self._ai_provider_changed)
        form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.ai_model = ModelPicker(cfg.get("model", "")); self.ai_model.setPlaceholderText("по умолчанию для провайдера")
        self.ai_model.b_reload.clicked.connect(lambda: self._load_models(force=True))
        self.ai_model.textChanged.connect(self._touch)
        self.ai_model.textChanged.connect(self._ai_show_last_check)
        self.ai_proxy = QLineEdit(cfg.get("proxy", "")); self.ai_proxy.setPlaceholderText("http://host:port или socks5h://host:port — если нужен")
        self.ai_proxy.textChanged.connect(self._touch)
        self.ai_url = QLineEdit(cfg.get("base_url", "")); self.ai_url.setPlaceholderText("оставь пустым для стандартного")
        self.ai_url.textChanged.connect(self._touch)
        self.cb_ai_auto = QCheckBox("Делать сводки автоматически после загрузки новостей")
        self.cb_ai_auto.setChecked(bool(cfg.get("auto", True))); self.cb_ai_auto.toggled.connect(self._touch)
        self.sp_ai_interval = QDoubleSpinBox(); self.sp_ai_interval.setRange(0, 168); self.sp_ai_interval.setDecimals(1)
        self.sp_ai_interval.setSuffix(" ч"); self.sp_ai_interval.setValue(float(cfg.get("min_interval_h", 6)))
        self.sp_ai_interval.valueChanged.connect(self._touch)
        from PySide6.QtWidgets import QSizePolicy
        self.lbl_ai_tier = AutoHeightLabel()
        for wdg in (self.ai_model, self.ai_model.combo, self.ai_model.catalog_row):
            wdg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addRow("Модель:", self.ai_model)
        # каталог, пояснение и лимиты — в одном блоке: так многострочный текст корректно считает высоту
        self.ai_catalog_box = QWidget(); cb_lay = QVBoxLayout(self.ai_catalog_box)
        cb_lay.setContentsMargins(0, 0, 0, 0); cb_lay.setSpacing(4)
        cb_lay.addWidget(self.ai_model.catalog_row); cb_lay.addWidget(self.ai_model.lbl)
        cb_lay.addWidget(self.ai_model.why); cb_lay.addWidget(self.lbl_ai_tier)
        form.addRow("", self.ai_catalog_box)
        self.ai_model.form = form; self._ai_form = form
        form.addRow("Прокси:", self.ai_proxy); form.addRow("Endpoint:", self.ai_url)
        form.addRow(self.cb_ai_auto)
        # длинная подпись — не в колонку подписей формы: она задаёт ширину колонки, и на macOS (подписи прижаты
        # вправо) все поля страницы съезжали вправо, слева оставалось пустое поле в треть окна
        iv = QWidget(); iv_lay = QHBoxLayout(iv); iv_lay.setContentsMargins(0, 0, 0, 0)
        iv_lay.addWidget(QLabel("Не пересчитывать чаще, чем раз в")); iv_lay.addWidget(self.sp_ai_interval); iv_lay.addStretch(1)
        form.addRow(iv)
        form.addRow(_note("Ответы кэшируются: при тех же новостях/данных запрос не уходит вообще, а при изменившихся — не чаще "
                          "заданного интервала. Кнопка «ИИ-сводка» и "
                          f"{native_keys('Ctrl+Shift+I')} пересчитывают принудительно.", gray=True))
        lay.addLayout(form)
        row = QHBoxLayout()
        self.lbl_ai_key = QLabel()
        b_set, b_del, b_test = QPushButton("Задать ключ…"), QPushButton("Удалить"), QPushButton("Проверить")
        b_test.setToolTip("Один запрос: отвечает ли модель строгим JSON по схеме и по-русски — это нужно для сводок. "
                          "Запрос считается в лимите бесплатных моделей; результат запоминается для каждой модели")
        self.b_ai_test = b_test
        b_set.clicked.connect(self._ai_set_key); b_del.clicked.connect(self._ai_del_key); b_test.clicked.connect(self._ai_test)
        row.addWidget(self.lbl_ai_key, 1); row.addWidget(b_set); row.addWidget(b_del); row.addWidget(b_test)
        lay.addLayout(row)
        self.lbl_ai_howto = _note("", rich=True); lay.addWidget(self.lbl_ai_howto)
        self.lbl_ai_test = _note("", gray=True); self.lbl_ai_test.setTextInteractionFlags(Qt.TextSelectableByMouse); lay.addWidget(self.lbl_ai_test)
        lay.addStretch()
        self._ai_provider_changed()
        return w

    def ai_provider_key(self) -> str:
        return next(k for k, rb in self.ai_rbs.items() if rb.isChecked())

    def _ai_provider_changed(self, *_) -> None:
        import ai as AI
        p = self.ai_provider_key()
        self.lbl_ai_howto.setText("<b>Как подключить</b><br>" + AI.HOWTO[p])
        dm = AI.default_model(p)
        self.ai_model.setPlaceholderText(f"по умолчанию: {dm}" if dm else "обязательно: id модели у провайдера")
        self.ai_url.setPlaceholderText(AI.PROVIDERS[p]["url"] or "обязательно: https://host/v1/chat/completions")
        self.ai_model.form = self.ai_model.form or getattr(self, "_ai_form", None)
        self.ai_model.set_catalog_mode(p == "openrouter")
        if hasattr(self, "lbl_ai_tier") and p == "openrouter":
            self._ai_tier_info()
        if p == "openrouter" and not self.ai_model.models:
            self._load_models(force=False)
        self._ai_refresh_key_label(); self._ai_show_last_check(); self._touch()

    def _ai_show_last_check(self, *_) -> None:
        """Результат прошлой проверки выбранной модели — без нового запроса."""
        import ai as AI
        import paths
        if not hasattr(self, "lbl_ai_test"):
            return
        r = AI.last_check(self.ai_provider_key(), self.ai_model.text().strip(), paths.cache_dir())
        self.lbl_ai_test.setText(AI.describe_check(r) if r else "")

    def _ai_tier_info(self) -> None:
        """Сколько бесплатных запросов в сутки доступно этому ключу: зависит от того, покупались ли кредиты."""
        import ai as AI
        key = SEC.get(AI_KEY_SERVICE, "openrouter")
        base = ("Лимит бесплатных (:free) моделей — общий на аккаунт: 20 запросов в минуту и 50 в сутки, а после покупки "
                "кредитов от $10 — 1000 в сутки. Одно обновление приложения тратит 2 запроса (новости + все экраны сразу), "
                "кнопка «Проверить» — ещё 1.")
        block = ""
        if AI.RATE_BLOCK_UNTIL and AI.RATE_BLOCK_UNTIL > __import__("datetime").datetime.now():
            block = "<br>" + SY.span(f"Дневной лимит исчерпан, запросы к :free-моделям не отправляются "
                                     f"до {AI.RATE_BLOCK_UNTIL:%H:%M}.", SY.BAD)
        self.lbl_ai_tier.setText(base + block)
        if not key:
            return

        def show(info: dict) -> None:
            tier = ""
            if info.get("free_tier") is True:
                tier = " <b>Этот ключ — без покупки кредитов: 50 бесплатных запросов в сутки.</b>"
            elif info.get("free_tier") is False:
                tier = " <b>На аккаунте покупались кредиты: до 1000 бесплатных запросов в сутки.</b>"
            self.lbl_ai_tier.setText(base + tier + block)
        if is_running(getattr(self, "_tier_worker", None)):
            return
        self._tier_worker = TierWorker(key, self.ai_proxy.text().strip())
        self._tier_worker.done.connect(show); self._tier_worker.start()

    def _load_models(self, force: bool) -> None:
        """Каталог OpenRouter — в фоне; из кэша (сутки) мгновенно."""
        import paths
        if is_running(getattr(self, "_models_worker", None)):
            return
        self.ai_model.lbl.setText("загружаю каталог моделей…")
        self._models_worker = ModelsWorker(paths.cache_dir(), self.ai_proxy.text().strip(), force)
        self._models_worker.done.connect(lambda ms, src: self.ai_model.set_models(ms, f"каталог: {src}"))
        self._models_worker.failed.connect(lambda m: self.ai_model.lbl.setText(f"каталог не загружен ({m}) — впиши id вручную"))
        self._models_worker.start()

    def _ai_key(self) -> str | None:
        import ai as AI
        return AI.get_key(self.ai_provider_key())

    def _ai_refresh_key_label(self) -> None:
        p = self.ai_provider_key()
        if p == "ollama":
            self.lbl_ai_key.setText("Ключ не нужен — локальная модель"); return
        self.lbl_ai_key.setText("Ключ: сохранён ✓" if SEC.get(AI_KEY_SERVICE, p) else "Ключ: не задан")

    def _ai_set_key(self) -> None:
        p = self.ai_provider_key()
        if p == "ollama":
            return
        key, ok = QInputDialog.getText(self, "API-ключ", f"Ключ для {p}:", QLineEdit.Password)
        if ok and key.strip():
            SEC.set(AI_KEY_SERVICE, p, key.strip())
            self._ai_refresh_key_label(); self.accessChanged.emit()

    def _ai_del_key(self) -> None:
        SEC.delete(AI_KEY_SERVICE, self.ai_provider_key())
        self._ai_refresh_key_label(); self.accessChanged.emit()

    def _ai_test(self) -> None:
        import paths
        p = self.ai_provider_key(); key = self._ai_key()
        if not key:
            self.lbl_ai_test.setText("Сначала задай ключ."); return
        if is_running(getattr(self, "_check_worker", None)):
            return
        self.lbl_ai_test.setText("Проверяю модель (один запрос)…"); self.b_ai_test.setEnabled(False)
        import ai as AI
        conf = AI.AIConfig(p, key, self.ai_model.text().strip(), self.ai_proxy.text().strip(), self.ai_url.text().strip())
        self._check_worker = CheckWorker(conf, paths.cache_dir(), self)

        def done(r):
            import ai as AI
            self.b_ai_test.setEnabled(True); self.lbl_ai_test.setText(AI.describe_check(r))

        def failed(m):
            self.b_ai_test.setEnabled(True); self.lbl_ai_test.setText(f"✗ {m}")
        self._check_worker.done.connect(done); self._check_worker.failed.connect(failed)
        self._check_worker.start()

    # ---- Доступ -------------------------------------------------------------

    def _tab_access(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)

        lay.addWidget(QLabel("<b>Токен T-Invest API</b>"))
        self.lbl_token = QLabel(); self._refresh_token_label()
        row = QHBoxLayout()
        b_set, b_del = QPushButton("Задать токен…"), QPushButton("Удалить")
        b_set.clicked.connect(self._set_token); b_del.clicked.connect(self._del_token)
        row.addWidget(self.lbl_token, 1); row.addWidget(b_set); row.addWidget(b_del)
        lay.addLayout(row)
        lay.addWidget(_note(TOKEN_HOWTO, rich=True))

        lay.addSpacing(18)
        lay.addWidget(QLabel("<b>Сертификаты для подключения к API</b>"))
        lay.addWidget(_note("API подписан корнем Минцифры, которого нет в наборе grpcio. Бандл собирается один раз "
                            "из корней grpcio и цепочки самого API; пересобрать нужно только если сертификаты сменятся."))
        self.lbl_bundle = QLabel(); self._refresh_bundle_label()
        row2 = QHBoxLayout()
        b_bundle = QPushButton("Собрать бандл"); b_bundle.clicked.connect(self._make_bundle)
        row2.addWidget(self.lbl_bundle, 1); row2.addWidget(b_bundle)
        lay.addLayout(row2)

        lay.addSpacing(18)
        form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.ed_account = QLineEdit(self.cfg.get("account", {}).get("id", "") or "")
        self.ed_account.setPlaceholderText("первый открытый брокерский счёт")
        self.ed_account.textChanged.connect(self._touch)
        form.addRow("ID счёта:", self.ed_account)
        lay.addLayout(form)
        lay.addStretch()
        return w

    def _refresh_token_label(self) -> None:
        where = SEC.where(KEYRING_SERVICE, KEYRING_USER)
        self.lbl_token.setText({"session": "Только до выхода (демо-режим) ✓", "keychain": f"Сохранён в {SECRET_STORE} ✓", "file": f"Сохранён в защищённом файле ({SECRET_STORE_NAME} недоступен) ✓"}.get(where, "Не задан"))

    def _set_token(self) -> None:
        tok, ok = QInputDialog.getText(self, "Токен T-Invest API", "Read-only токен (начинается с «t.»):", QLineEdit.Password)
        if ok and tok.strip():
            where = SEC.set(KEYRING_SERVICE, KEYRING_USER, tok.strip())
            if where == "file":
                QMessageBox.information(self, "Токен сохранён в файле",
                                        f"{SECRET_STORE_NAME} не принял запись (подробности: Справка → Диагностика и лог). Токен сохранён в локальной "
                                        "папке с правами только для твоего пользователя — приложение работает.")
            self._refresh_token_label()
            self.accessChanged.emit()

    def _del_token(self) -> None:
        SEC.delete(KEYRING_SERVICE, KEYRING_USER)
        self._refresh_token_label()
        self.accessChanged.emit()

    def _refresh_bundle_label(self) -> None:
        if self.bundle_path.exists():
            n = self.bundle_path.read_text(encoding="utf-8", errors="replace").count("BEGIN CERTIFICATE")
            self.lbl_bundle.setText(f"Собран ✓ ({n} сертификатов)")
        else:
            self.lbl_bundle.setText("Не собран — подключение к API не пройдёт")

    def _make_bundle(self) -> None:
        try:
            msg = build_ca_bundle(self.bundle_path)
            QMessageBox.information(self, "Бандл собран", msg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Не удалось собрать бандл", str(e))
        self._refresh_bundle_label()
        self.accessChanged.emit()

    # ---- Сборка конфига -----------------------------------------------------

    def collect(self) -> dict:
        import config_migrate as CM
        cfg: dict = {CM.KEY: CM.CURRENT_VERSION,          # окно пишет актуальный формат; без версии миграция пошла бы снова
                     "account": {"id": self.ed_account.text().strip()},
                     "auto_classify": self.cb_auto_cls.isChecked(),
                     "setup_done": bool(self.cfg.get("setup_done", True)),
                     "strategy_preset": self.cfg.get("strategy_preset", ""),
                     "auto_classified": self.cfg.get("auto_classified", []),
                     "refresh": {"on_launch": self.cb_on_launch.isChecked(),
                                 "interval_min": int(self.cmb_interval.currentData()),
                                 "notify": self.cb_notify.isChecked(),
                                 "privacy_on_launch": self.cb_privacy.isChecked()}}

        targets = {}
        for r in range(self.t_targets.rowCount()):
            cls = self.t_targets.item(r, 0).data(Qt.UserRole)
            lo, hi = self.t_targets.cellWidget(r, 1).value(), self.t_targets.cellWidget(r, 2).value()
            if lo < 0 and hi < 0:
                continue
            targets[cls] = {"min": max(lo, 0.0), "max": hi if hi >= 0 else 100.0}
        cfg["targets"] = targets

        instruments, names, refs, blocked = {}, {}, {}, []
        for r in range(self.t_instr.rowCount()):
            t = self._text(self.t_instr, r, 0)
            if not t:
                continue
            n = self._text(self.t_instr, r, 1)
            if n:
                names[t] = n
            cls = self.t_instr.cellWidget(r, 2).currentData()
            if cls:
                instruments[t] = cls
            if self.t_instr.cellWidget(r, 3).isChecked():
                blocked.append(t)
            ref = self.t_instr.cellWidget(r, 4).currentText().strip().upper()
            if ref:
                refs[t] = ref
        cfg["instruments"] = instruments
        cfg["instrument_names"] = {**self.names(), **names}
        cfg["reference_tickers"] = refs
        cfg["blocked_tickers"] = blocked

        fx = {}
        for r in range(self.t_fx.rowCount()):
            cur = self._text(self.t_fx, r, 0).upper()
            rate = self.t_fx.cellWidget(r, 1).value()
            if cur and rate > 0:
                fx[cur] = rate
        cfg["fx_fallback"] = fx

        frozen = {}
        for r in range(self.t_frozen.rowCount()):
            t = self._text(self.t_frozen, r, 0)
            if t:
                frozen[t] = self._text(self.t_frozen, r, 1)
        caps = {}
        for r in range(self.t_caps.rowCount()):
            t = self._text(self.t_caps, r, 0)
            cap = self.t_caps.cellWidget(r, 1).value()
            if t and cap > 0:
                caps[t] = cap

        feeds = [self._text(self.t_feeds, r, 0) for r in range(self.t_feeds.rowCount()) if self._text(self.t_feeds, r, 0)]
        aliases = {}
        for r in range(self.t_aliases.rowCount()):
            t = self._text(self.t_aliases, r, 0)
            words = [x.strip() for x in self._text(self.t_aliases, r, 1).split(",") if x.strip()]
            if t and words:
                aliases[t] = words
        cfg["news"] = {"feeds": feeds, "aliases": aliases}
        cfg["alerts"] = self._collect_alerts()
        cfg["ai"] = {"provider": self.ai_provider_key(), "model": self.ai_model.text().strip(),
                     "proxy": self.ai_proxy.text().strip(), "base_url": self.ai_url.text().strip(),
                     "auto": self.cb_ai_auto.isChecked(), "min_interval_h": self.sp_ai_interval.value()}
        black = {}
        for r in range(self.t_black.rowCount()):
            t = self._text(self.t_black, r, 0).upper()
            if t:
                black[t] = self._text(self.t_black, r, 1)
        cfg["screener"] = {"blacklist": black,
                           "min_avg5y_yield": self.sp_avg5.value(), "max_pe": self.sp_pe.value(), "min_free_float": self.sp_ff.value(),
                           "exclude_sectors": [x.strip() for x in self.ed_excl_sectors.text().split(",") if x.strip()],
                           "last_auto_run": (self.cfg.get("screener") or {}).get("last_auto_run", "")}

        cfg["rules"] = {
            "drawdown_alert_pct": self.sp_dd.value(),
            "buy_candidates_classes": [c for c, cb in self.cb_classes.items() if cb.isChecked()],
            "frozen": frozen,
            "position_caps": caps,
            "single_name_max_pct": self.sp_single.value(),
            "tail_threshold_rub": self.sp_tail.value(),
        }
        return cfg

    @staticmethod
    def _text(table: QTableWidget, r: int, c: int) -> str:
        it = table.item(r, c)
        return it.text().strip() if it else ""

    def closeEvent(self, event) -> None:
        for name, ms in (("_models_worker", 3000), ("_tier_worker", 3000), ("_check_worker", 60000), ("_feeds_worker", 15000)):
            stop_workers([getattr(self, name, None)], ms, interrupt=False)   # не уничтожать живой поток (SIGABRT)
        # На закрытие — дописать то, что ещё не ушло по таймеру
        if self._timer.isActive():
            self._timer.stop()
            self._emit()
        super().closeEvent(event)
