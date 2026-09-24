"""wizard.py — мастер первого запуска: принципы → шаблон стратегии → коридоры → правила → цель → что дальше."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup, QFormLayout,
    QDoubleSpinBox, QSpinBox, QPushButton, QFileDialog, QMessageBox, QGridLayout,
)

import strategy as ST
import style as SY
from portfolio_check import CLASS_LABELS


def _note(text: str, gray: bool = False) -> QLabel:
    l = QLabel(text); l.setWordWrap(True); l.setTextFormat(Qt.RichText); l.setOpenExternalLinks(True)
    l.setTextInteractionFlags(Qt.TextBrowserInteraction)
    if gray:
        l.setStyleSheet("color: gray")
    return l


class SetupWizard(QWizard):
    """Результат — self.cfg (обновлённый конфиг). Ничего не пишет на диск сам."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка стратегии")
        self.setWizardStyle(QWizard.MacStyle)
        self.resize(760, 560)
        self.cfg = dict(cfg)
        self.imported = False
        self.addPage(self._page_intro())
        self.addPage(self._page_preset())
        self.addPage(self._page_targets())
        self.addPage(self._page_rules())
        self.addPage(self._page_goal())
        self.addPage(self._page_done())
        self.setButtonText(QWizard.FinishButton, "Готово")
        self.setButtonText(QWizard.NextButton, "Далее")
        self.setButtonText(QWizard.BackButton, "Назад")
        self.setButtonText(QWizard.CancelButton, "Отмена")

    # ---- 1. Принципы ----
    def _page_intro(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Как устроено приложение")
        lay = QVBoxLayout(p)
        lay.addWidget(_note("PortfolioCheck сверяет портфель в Т-Инвестициях с <b>твоими</b> правилами и подсвечивает "
                            "отклонения. Советов «купить/продать» он не даёт — считает и показывает. Правила строятся на "
                            "нескольких принципах:"))
        lay.addWidget(_note("<ol>" + "".join(f"<li>{x}</li>" for x in ST.PRINCIPLES) + "</ol>"))
        lay.addWidget(_note("Дальше выберешь шаблон и подправишь цифры. Всё можно поменять потом в Настройках.", gray=True))
        lay.addStretch()
        return p

    # ---- 2. Шаблон ----
    def _page_preset(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Шаблон стратегии")
        p.setSubTitle("Принципы одинаковые, разница — в коридорах и строгости правил.")
        lay = QVBoxLayout(p)
        self.preset_group = QButtonGroup(self); self.preset_rbs = {}
        current = self.cfg.get("strategy_preset", "dividend_defensive")
        for pid, meta in ST.PRESETS.items():
            rb = QRadioButton(meta["title"]); rb.setProperty("pid", pid)
            self.preset_group.addButton(rb); self.preset_rbs[pid] = rb
            lay.addWidget(rb); lay.addWidget(_note(meta["description"], gray=True))
            if pid == current:
                rb.setChecked(True)
        self.preset_group.buttonClicked.connect(lambda b: self._apply_preset(b.property("pid")))
        row = QHBoxLayout()
        b_imp = QPushButton("Импортировать готовую стратегию из файла…")
        b_imp.setToolTip("YAML, экспортированный из PortfolioCheck или собранный с помощью ChatGPT (docs/CHATGPT_PROMPT.md)")
        b_imp.clicked.connect(self._import)
        b_prompt = QPushButton("Скопировать промпт для ChatGPT")
        b_prompt.setToolTip("Модель расспросит о целях и соберёт файл стратегии для импорта")
        b_prompt.clicked.connect(lambda: ST.copy_prompt_to_clipboard(self))
        row.addWidget(b_imp); row.addWidget(b_prompt); row.addStretch(); lay.addLayout(row)
        lay.addStretch()
        return p

    def _apply_preset(self, pid: str) -> None:
        self.cfg = ST.apply_preset(self.cfg, pid)
        self._load_targets_into_page(); self._load_rules_into_page()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Стратегия (YAML)", str(Path.home()), "YAML (*.yaml *.yml *.txt);;Все файлы (*)")
        if not path:
            return
        try:
            new, warns, replaced = ST.import_strategy(self.cfg, Path(path).read_text(encoding="utf-8"))
        except ValueError as e:
            QMessageBox.warning(self, "Стратегия не принята", str(e)); return
        self.cfg = new; self.imported = True
        self._load_targets_into_page(); self._load_rules_into_page()
        QMessageBox.information(self, "Стратегия импортирована",
                                "Загружены разделы: " + ", ".join(replaced) + ("\n\nЗамечания:\n" + "\n".join(warns) if warns else ""))

    # ---- 3. Коридоры ----
    def _page_targets(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Целевые коридоры по классам")
        p.setSubTitle("Доля класса от стоимости портфеля, %. Вне коридора — сигнал «докупить» или «перевес».")
        lay = QVBoxLayout(p); grid = QGridLayout(); self.tgt_spins = {}
        grid.addWidget(QLabel("<b>Класс</b>"), 0, 0); grid.addWidget(QLabel("<b>мин.</b>"), 0, 1); grid.addWidget(QLabel("<b>макс.</b>"), 0, 2)
        for i, cls in enumerate(["gold", "real_estate", "equity_core", "equity_spec"], start=1):
            lo = QDoubleSpinBox(); hi = QDoubleSpinBox()
            for sb in (lo, hi):
                sb.setRange(0, 100); sb.setDecimals(0); sb.setSuffix(" %")
            grid.addWidget(QLabel(CLASS_LABELS[cls]), i, 0); grid.addWidget(lo, i, 1); grid.addWidget(hi, i, 2)
            lo.valueChanged.connect(lambda v, hi=hi: hi.setValue(v) if v > hi.value() else None)
            self.tgt_spins[cls] = (lo, hi)
        lay.addLayout(grid)
        self.lbl_sum = _note("", gray=True); lay.addWidget(self.lbl_sum)
        for lo, hi in self.tgt_spins.values():
            lo.valueChanged.connect(self._update_sum); hi.valueChanged.connect(self._update_sum)
        lay.addWidget(_note("Классы «балласт», «иностранное/заблокированное» и «кэш» обычно без коридоров — это то, что "
                            "досталось из прошлого или временно. Подклассы ЗПИФ (склады/офисы) можно задать потом в Настройках → Цели.", gray=True))
        lay.addStretch()
        self._load_targets_into_page()
        return p

    def _load_targets_into_page(self) -> None:
        if not hasattr(self, "tgt_spins"):
            return
        t = self.cfg.get("targets") or {}
        for cls, (lo, hi) in self.tgt_spins.items():
            r = t.get(cls) or {"min": 0, "max": 0}
            lo.setValue(float(r["min"])); hi.setValue(float(r["max"]))
        self._update_sum()

    def _update_sum(self) -> None:
        mins = sum(lo.value() for lo, _ in self.tgt_spins.values())
        maxs = sum(hi.value() for _, hi in self.tgt_spins.values())
        ok = mins <= 100 <= maxs or maxs == 0
        self.lbl_sum.setText(f"Сумма минимумов {mins:.0f}%, сумма максимумов {maxs:.0f}% — "
                             + ("коридоры совместимы." if ok else SY.span("коридоры не сходятся: 100% должно быть "
                                                                         "между суммой минимумов и максимумов.", SY.BAD)))

    # ---- 4. Правила ----
    def _page_rules(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Правила")
        lay = QVBoxLayout(p); form = QFormLayout()
        self.sp_dd = QDoubleSpinBox(); self.sp_dd.setRange(1, 90); self.sp_dd.setSuffix(" %")
        self.sp_name = QDoubleSpinBox(); self.sp_name.setRange(1, 100); self.sp_name.setSuffix(" %")
        self.sp_tail = QSpinBox(); self.sp_tail.setRange(0, 10_000_000); self.sp_tail.setSingleStep(5000); self.sp_tail.setSuffix(" ₽")
        form.addRow("Кандидат на добор — если просадка от средней цены больше:", self.sp_dd)
        form.addRow("Максимальная доля одного эмитента:", self.sp_name)
        form.addRow("Мелкие «хвосты» балласта — меньше:", self.sp_tail)
        lay.addLayout(form)
        lay.addWidget(_note("Заморозки (бумаги, которые правило добора не должно предлагать), лимиты по отдельным бумагам и "
                            "чёрный список скринера — в Настройках, когда появятся реальные позиции.", gray=True))
        lay.addStretch()
        self._load_rules_into_page()
        return p

    def _load_rules_into_page(self) -> None:
        if not hasattr(self, "sp_dd"):
            return
        r = self.cfg.get("rules") or {}
        self.sp_dd.setValue(float(r.get("drawdown_alert_pct", 10)))
        self.sp_name.setValue(float(r.get("single_name_max_pct", 11)))
        self.sp_tail.setValue(int(r.get("tail_threshold_rub", 30000)))

    # ---- 5. Цель ----
    def _page_goal(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Цель")
        p.setSubTitle("Для экрана «Стратегия»: сколько хочется получать и сколько готов вносить.")
        lay = QVBoxLayout(p); form = QFormLayout()
        fc = self.cfg.get("forecast") or {}
        self.sp_goal = QSpinBox(); self.sp_goal.setRange(0, 100_000_000); self.sp_goal.setSingleStep(10_000); self.sp_goal.setSuffix(" ₽/мес")
        self.sp_goal.setValue(int(fc.get("target_monthly_real", 100_000)))
        self.sp_contrib = QSpinBox(); self.sp_contrib.setRange(0, 100_000_000); self.sp_contrib.setSingleStep(5_000); self.sp_contrib.setSuffix(" ₽/мес")
        self.sp_contrib.setValue(int(fc.get("monthly_contribution", 30_000)))
        from datetime import datetime
        self.sp_year = QSpinBox(); self.sp_year.setRange(datetime.now().year + 1, datetime.now().year + 60)
        self.sp_year.setValue(int(fc.get("target_year") or datetime.now().year + 15))
        form.addRow("Пассивный доход в сегодняшних деньгах:", self.sp_goal)
        form.addRow("Пополнение в месяц:", self.sp_contrib)
        form.addRow("Хочу достичь к году:", self.sp_year)
        lay.addLayout(form)
        lay.addWidget(_note("Капитал, текущий доход, инфляция и доходность подставятся из портфеля и с сайта ЦБ сами.", gray=True))
        lay.addStretch()
        return p

    # ---- 6. Что дальше ----
    def _page_done(self) -> QWizardPage:
        p = QWizardPage(); p.setTitle("Что дальше")
        lay = QVBoxLayout(p)
        lay.addWidget(_note(
            "<ol>"
            "<li><b>Токен T-Invest API</b> (только чтение): Настройки → Доступ, там же инструкция и кнопка «Собрать бандл» "
            "сертификатов.</li>"
            "<li>После первой загрузки портфеля бумаги <b>классифицируются автоматически</b> (акции — в дивидендное ядро, "
            "золото — в металлы, ЗПИФ — в недвижимость, иностранные — отдельно). Проверь и поправь в Настройки → Инструменты: "
            "спекулятивные бумаги и балласт программа сама не отличит.</li>"
            "<li>Нужна помощь со стратегией? Кнопка «Скопировать промпт для ChatGPT» (на шаге «Шаблон» и в Настройки → Общие): "
            "модель задаст вопросы и соберёт файл, который загружается через «Импорт стратегии…».</li>"
            "<li>Мастер можно запустить снова: меню «Файл → Мастер настройки…».</li>"
            "</ol>"))
        lay.addStretch()
        return p

    # ---- результат ----
    def accept(self) -> None:
        t = dict(self.cfg.get("targets") or {})
        for cls, (lo, hi) in self.tgt_spins.items():
            if hi.value() > 0:
                t[cls] = {"min": float(lo.value()), "max": float(hi.value())}
            else:
                t.pop(cls, None)
        self.cfg["targets"] = t
        rules = dict(self.cfg.get("rules") or {})
        rules.update({"drawdown_alert_pct": self.sp_dd.value(), "single_name_max_pct": self.sp_name.value(),
                      "tail_threshold_rub": self.sp_tail.value()})
        rules.setdefault("buy_candidates_classes", ["equity_core", "real_estate_wh", "real_estate_other"])
        rules.setdefault("frozen", {}); rules.setdefault("position_caps", {})
        self.cfg["rules"] = rules
        fc = dict(self.cfg.get("forecast") or {})
        fc.update({"target_monthly_real": self.sp_goal.value(), "monthly_contribution": self.sp_contrib.value(),
                   "target_year": self.sp_year.value()})
        self.cfg["forecast"] = fc
        self.cfg["setup_done"] = True
        self.cfg.setdefault("auto_classify", True)
        super().accept()
