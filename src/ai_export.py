"""
ai_export.py — выгрузка портфеля одним Markdown-документом для анализа крупной моделью (ChatGPT, Claude и т.п.).

Раньше для этого копировали окно с отчётом: там только доли и позиции. Здесь — всё, что программа уже знает, без
сетевых запросов: правила и коридоры, текущий отчёт, история стоимости по снапшотам, изменения бумаг за периоды и
среднегодовой рост, риск (волатильность, просадка, корреляции), выплаты, план, макро и календарь ЦБ, кандидаты
скринера, новости по бумагам за последние дни и сводка встроенной ИИ — как «второе мнение».

Вверху — инструкция для модели: что это за документ и что от неё ждут. Формулировки — «оценка и что проверить»,
без указаний покупать или продавать (правило 3 проекта): решение за человеком.

Режим скрытых сумм: рублёвые суммы и количества заменяются на «•••», история стоимости — индексом (100 в начале),
проценты, доли и цены остаются — так документ можно отдать модели, не раскрывая размер портфеля.

build(data, opts) — чистая функция от собранных данных (проверяется тестами без окна); collect(win) — сбор из окна.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import portfolio_check as pc
from style import money_fn

SECTIONS = [  # ключ, подпись в окне экспорта
    ("prompt", "Инструкция для модели"),
    ("strategy", "Правила и коридоры"),
    ("report", "Отчёт: доли, отклонения, позиции"),
    ("history", "История стоимости"),
    ("changes", "Изменения бумаг за периоды"),
    ("risk", "Риск"),
    ("payouts", "Выплаты"),
    ("plan", "План (прогноз)"),
    ("macro", "Макро и календарь ЦБ"),
    ("ideas", "Кандидаты скринера"),
    ("news", "Новости по бумагам"),
    ("ai", "Сводка встроенной ИИ"),
]

PROMPT = (
    "Ниже — выгрузка моего инвестиционного портфеля из программы PortfolioCheck (Т-Инвестиции) с моими правилами, "
    "историей, выплатами, макрофоном и новостями по бумагам. Проанализируй её как независимый аналитик:\n"
    "1. Насколько портфель соответствует моим правилам и коридорам; где отклонения и насколько они существенны.\n"
    "2. Концентрации и риски: по эмитентам, отраслям, валютам, связанным бумагам; что изменилось за период.\n"
    "3. Что в новостях и макрофоне реально касается моих бумаг, а что — шум.\n"
    "4. Реалистичность плана (доходность, рост выплат, инфляция) и на каких допущениях он держится.\n"
    "5. Что стоит проверить или уточнить, каких данных не хватает для выводов.\n"
    "Это оценка, а не рекомендация: не давай указаний купить или продать конкретные бумаги — решения принимаю я. "
    "Где делаешь вывод — опирайся на цифры из документа и отмечай, где их недостаточно."
)


@dataclass
class Data:
    """Всё, что нужно для документа (собирается из окна функцией collect)."""
    positions: list
    cfg: dict
    snapshots: list = field(default_factory=list)        # [(дата, сумма ₽)] — факт по снапшотам
    backfilled: list = field(default_factory=list)       # [(дата, сумма ₽)] — сегодняшний состав по ценам прошлых дней
    changes: dict = field(default_factory=dict)          # figi → {day, week, month, year: %}
    long_growth: dict = field(default_factory=dict)      # {"years": N, "values": {figi: %/год}}
    risk: object = None                                   # risk.RiskReport | None
    payouts: list = field(default_factory=list)          # [payouts.Payout]
    plan_text: str = ""                                  # «Параметры плана…» — как уходит встроенной ИИ
    macro: object = None                                  # macro.Macro | None
    events: list = field(default_factory=list)           # [macro.MacroEvent]
    ideas: list = field(default_factory=list)            # [screener.Idea]
    news: list = field(default_factory=list)             # [news.NewsItem]
    ai_summary: object = None                             # ai.Summary | None
    today: date | None = None


@dataclass
class Options:
    sections: set = field(default_factory=lambda: {k for k, _ in SECTIONS})
    mask: bool = False
    news_days: int = 7
    news_max: int = 80
    history_months: int = 24


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _monthly(points: list, months: int, today: date) -> list:
    """Последняя точка каждого месяца за months месяцев."""
    lo = (today.replace(day=1) - timedelta(days=31 * (months - 1))).strftime("%Y-%m")
    by_m = {}
    for d, v in points:
        if d[:7] >= lo:
            by_m[d[:7]] = (d, v)
    return [by_m[k] for k in sorted(by_m)]


def build(data: Data, opts: Options | None = None) -> str:
    opts = opts or Options()
    on = opts.sections.__contains__
    M = money_fn(opts.mask)
    today = data.today or date.today()
    names = {p.ticker: p.name for p in data.positions}
    out = [f"# Портфель для анализа — {datetime.now():%d.%m.%Y %H:%M}", ""]
    if opts.mask:
        out += ["_Суммы скрыты: рублёвые значения заменены на «•••», история стоимости — индексом (100 в начале). "
                "Доли, проценты и цены — настоящие._", ""]
    if on("prompt"):
        out += ["## Задача", "", PROMPT, ""]

    if on("strategy"):
        out += ["## Мои правила", ""]
        tg = data.cfg.get("targets") or {}
        if tg:
            out += ["Целевые коридоры классов:", "", "| Класс | Мин, % | Макс, % |", "|---|---:|---:|"]
            out += [f"| {pc.class_label(c)} | {r.get('min', 0)} | {r.get('max', 0)} |" for c, r in tg.items()]
            out.append("")
        r = data.cfg.get("rules") or {}
        frozen = r.get("frozen") or {}
        caps = r.get("position_caps") or {}
        out += [f"- Кандидат на добор: просадка от средней больше {r.get('drawdown_alert_pct', '—')}% "
                f"(классы: {', '.join(pc.class_label(c) for c in r.get('buy_candidates_classes') or []) or '—'}).",
                f"- Максимум на одного эмитента: {r.get('single_name_max_pct', '—')}%.",
                f"- «Хвосты» балласта: позиции меньше {M(r.get('tail_threshold_rub', 0))} ₽."]
        if frozen:
            out.append("- Заморожены (не доливаю): " + "; ".join(f"{t} — {why}" for t, why in frozen.items()) + ".")
        if caps:
            out.append("- Лимиты на позицию: " + "; ".join(f"{t} ≤ {M(v)} ₽" for t, v in caps.items()) + ".")
        out.append("")

    if on("report"):
        rep = pc.render(data.positions, data.cfg, mask=opts.mask)
        out += ["## Отчёт", ""] + ["#" + ln if ln.startswith("#") else ln for ln in rep.splitlines()[2:]] + [""]

    if on("history"):
        snaps = _monthly(data.snapshots, opts.history_months, today)
        back = _monthly(data.backfilled, 12, today)
        out += ["## История стоимости", ""]
        if snaps:
            base = snaps[0][1] or 1
            out += [f"Факт по снапшотам программы (последняя точка месяца, {len(snaps)} мес.; пополнения и выводы "
                    "в стоимость входят — это не доходность):", "",
                    "| Месяц | " + ("Индекс" if opts.mask else "Стоимость, ₽") + " | К началу |", "|---|---:|---:|"]
            for d, v in snaps:
                val = f"{v / base * 100:.1f}" if opts.mask else M(v)
                out.append(f"| {d[:7]} | {val} | {(v / base - 1) * 100:+.1f}% |")
            out.append("")
        if back:
            base = back[0][1] or 1
            out += ["Сегодняшний состав по ценам прошлых дней (без пополнений — как вёл бы себя нынешний портфель):", "",
                    "| Месяц | Индекс |", "|---|---:|"]
            out += [f"| {d[:7]} | {v / base * 100:.1f} |" for d, v in back]
            out.append("")
        if not snaps and not back:
            out += ["Истории пока нет.", ""]

    if on("changes") and data.changes:
        lg = data.long_growth or {}
        yrs = lg.get("years")
        head = "| Бумага | Тикер | Доля | День | Неделя | Месяц | Год |" + (f" Ср.год. за {yrs:.0f} г |" if yrs else "")
        total = sum(float(p.value_rub) for p in data.positions) or 1
        out += ["## Изменения бумаг за периоды", "", head, "|---|---|---:|---:|---:|---:|---:|" + ("---:|" if yrs else "")]
        for p in sorted(data.positions, key=lambda x: -float(x.value_rub)):
            ch = data.changes.get(p.figi) or {}
            row = (f"| {p.name} | {p.ticker} | {float(p.value_rub) / total * 100:.1f}% | {_pct(ch.get('day'))} | "
                   f"{_pct(ch.get('week'))} | {_pct(ch.get('month'))} | {_pct(ch.get('year'))} |")
            if yrs:
                row += f" {_pct((lg.get('values') or {}).get(p.figi))} |"
            out.append(row)
        out.append("")

    if on("risk") and data.risk is not None:
        r = data.risk
        out += ["## Риск (за год, по дневным ценам)", "",
                f"Волатильность портфеля {r.vol:.1f}% в год; максимальная просадка сегодняшнего состава {r.max_drawdown:.1f}%; "
                f"эффективное число бумаг {r.effective_n:.1f}; диверсификация снижает риск в {r.diversification:.2f}×. "
                f"Торговых дней в расчёте: {r.days}.", "",
                "| Бумага | Тикер | Вес, % | Волатильность, % | Вклад в риск, % |", "|---|---|---:|---:|---:|"]
        out += [f"| {n} | {t} | {w:.1f} | {v:.1f} | {c:.1f} |" for t, n, w, v, c in r.rows]
        if r.pairs:
            out += ["", "Сильнее всего движутся вместе: " + ", ".join(f"{a}–{b} {c:+.2f}" for a, b, c in r.pairs) + "."]
        if r.missing:
            out += [f"Без истории цен, в расчёт не вошли: {', '.join(r.missing)}."]
        out.append("")

    if on("payouts") and data.payouts:
        up = sorted((x for x in data.payouts if x.status != "paid" and x.when >= today), key=lambda x: x.when)[:30]
        past = [x for x in data.payouts if x.status == "paid" and x.when >= today - timedelta(days=365)]
        status = {"declared": "объявлена", "expected": "ожидается (по прошлому году)"}
        out += ["## Выплаты", "", f"За последние 12 месяцев пришло: {M(sum(x.amount_rub for x in past))} ₽ "
                f"({len(past)} выплат).", ""]
        if up:
            out += ["| Бумага | Посл. день покупки | Выплата | На бумагу | Сумма, ₽ | Статус |", "|---|---|---|---:|---:|---|"]
            out += [f"| {x.name} ({x.ticker}) | {x.last_buy_date or '—'} | {x.payment_date or x.record_date} | "
                    f"{x.per_share:,.2f} {x.currency} | {M(x.amount_rub)} | {status.get(x.status, x.status)} |" for x in up]
            out.append("")

    if on("plan") and data.plan_text:
        txt = pc.mask_text(data.plan_text) if opts.mask else data.plan_text
        out += ["## План", ""] + [f"- {ln}" for ln in txt.splitlines() if ln.strip()] + [""]

    if on("macro") and (data.macro is not None or data.events):
        out += ["## Макро", ""]
        m = data.macro
        if m is not None:
            out.append(f"- ЦБ РФ ({m.period}): ключевая ставка {m.key_rate}%, инфляция {m.inflation}% г/г, цель {m.target}%.")
        evs = [e for e in data.events if e.date >= today.isoformat()][:6]
        if evs:
            out.append("- Ближайшие события ЦБ: " + "; ".join(f"{e.date} — {e.title}" for e in evs) + ".")
        out.append("")

    if on("ideas") and data.ideas:
        out += ["## Кандидаты скринера (дивидендные акции по моим фильтрам)", "",
                "| Компания | Тикер | Отрасль | Дивдох., % | Ср. 5 лет, % | P/E | Долг/EBITDA | ROE, % | Месяц | Новости |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
        f = lambda v, fmt="{:.1f}": "—" if v is None else fmt.format(v)
        for i in data.ideas[:20]:
            tone = {"positive": "позитив", "negative": "негатив", "neutral": "нейтр."}.get(i.news_sentiment, "")
            out.append(f"| {i.name} | {i.ticker} | {i.sector} | {f(i.div_yield)} | {f(i.avg5y_div_yield)} | {f(i.pe)} | "
                       f"{f(i.net_debt_ebitda)} | {f(i.roe)} | {_pct(i.month_change)} | "
                       f"{(str(i.news_count) + ' ' + tone).strip() if i.news_count else '—'} |")
        out.append("")

    if on("news"):
        cut = today - timedelta(days=opts.news_days)
        items = [it for it in data.news if it.published is None or it.published.date() >= cut][:opts.news_max]
        out += [f"## Новости за {opts.news_days} дн. — по моим бумагам и макро", ""]
        if not items:
            out += ["Новостей нет.", ""]
        by_t: dict[str, list] = {}
        for it in items:
            for t in (it.matched or ["макро"]):
                by_t.setdefault(t, []).append(it)
        for t in sorted(by_t, key=lambda k: (k == "макро", names.get(k, k))):
            out += [f"### {names.get(t, t)}" + (f" ({t})" if t in names else ""), ""]
            for it in by_t[t]:
                when = it.published.astimezone().strftime("%d.%m %H:%M") if it.published else ""
                summ = f" — {it.summary[:240]}" if it.summary and it.summary.strip() != it.title.strip() else ""
                out.append(f"- {when} [{it.source}] {it.title}{summ}")
            out.append("")

    if on("ai") and data.ai_summary is not None:
        s = data.ai_summary
        out += [f"## Сводка встроенной ИИ ({s.model}, {str(s.at).replace('T', ' ')}) — второе мнение, не истина", ""]
        if s.overall:
            out.append(s.overall)
        if s.macro:
            out += ["", f"Макро: {s.macro}"]
        tone = {"positive": "позитив", "negative": "негатив", "neutral": "нейтрально"}
        if s.items:
            out.append("")
            out += [f"- {names.get(i.get('ticker'), i.get('ticker'))}: {tone.get(i.get('sentiment'), '')} — {i.get('note', '')}"
                    for i in s.items]
        out.append("")

    out += ["---", "_Выгружено из PortfolioCheck: программа считает и показывает, не является инвестиционной "
            "рекомендацией._"]
    return "\n".join(out)


def collect(win) -> Data:
    """Всё, что окно уже знает, — без сети. Снапшоты читаются с диска (десятки мс)."""
    import history as H
    import macro
    import paths
    risk = None
    try:
        risk = win._risk_report() if win.positions else None
    except Exception:  # noqa: BLE001 — нет свечей и т.п.: без раздела риска
        risk = None
    plan = ""
    try:
        plan = win._facts_strategy()
    except Exception:  # noqa: BLE001
        plan = ""
    res = getattr(win, "_last_screen", None)
    return Data(
        positions=list(win.positions), cfg=win.cfg,
        snapshots=H.snapshot_history(win.snap_dir()), backfilled=list(getattr(win, "backfilled", []) or []),
        changes=dict(getattr(win, "period_changes", {}) or {}), long_growth=getattr(win, "_long_values", None) or {},
        risk=risk, payouts=list(getattr(win, "payouts", []) or []), plan_text=plan,
        macro=getattr(win, "_macro", None),
        events=list(getattr(win, "macro_events", None) or macro.load_calendar(paths.cache_dir())),
        ideas=list(getattr(res, "ideas", None) or []), news=list(getattr(win, "news_items", []) or []),
        ai_summary=getattr(win, "ai_summary", None))


# ---------------------------------------------------------------------------
# Окно экспорта: разделы, глубина новостей и истории, скрытие сумм, предпросмотр, «Копировать» / «Сохранить .md»
# ---------------------------------------------------------------------------

def _dialog_class():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                                   QPushButton, QSpinBox, QVBoxLayout)
    from osutil import mono_font

    class AIExportDialog(QDialog):
        """Документ собирается из того, что окно уже знает, — без сети; данные берутся один раз при открытии."""

        def __init__(self, win):
            super().__init__(win)
            self.setWindowTitle("Экспорт для ИИ-анализа")
            self.data = collect(win)
            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Один Markdown-документ для крупной модели (ChatGPT, Claude…): правила, отчёт, история, "
                                 "риск, выплаты, план, макро, кандидаты, новости. Вставь его в чат целиком."))
            grid = QGridLayout()
            self.cb = {}
            for i, (k, label) in enumerate(SECTIONS):
                c = QCheckBox(label); c.setChecked(True); c.toggled.connect(self.refresh)
                self.cb[k] = c; grid.addWidget(c, i // 3, i % 3)
            lay.addLayout(grid)
            row = QHBoxLayout()
            self.sp_news = QSpinBox(); self.sp_news.setRange(1, 30); self.sp_news.setValue(7); self.sp_news.setSuffix(" дн.")
            self.sp_hist = QSpinBox(); self.sp_hist.setRange(3, 120); self.sp_hist.setValue(24); self.sp_hist.setSuffix(" мес.")
            self.cb_mask = QCheckBox("Скрыть суммы (только доли и проценты)")
            self.cb_mask.setChecked(bool(getattr(win, "privacy", False)))
            for w in (self.sp_news, self.sp_hist):
                w.valueChanged.connect(self.refresh)
            self.cb_mask.toggled.connect(self.refresh)
            row.addWidget(QLabel("Новости за")); row.addWidget(self.sp_news); row.addSpacing(16)
            row.addWidget(QLabel("История")); row.addWidget(self.sp_hist); row.addSpacing(16)
            row.addWidget(self.cb_mask); row.addStretch(1)
            lay.addLayout(row)
            self.view = QPlainTextEdit(); self.view.setReadOnly(True); self.view.setFont(mono_font(11))
            lay.addWidget(self.view, 1)
            bottom = QHBoxLayout()
            self.lbl_size = QLabel(); self.lbl_size.setStyleSheet("color: gray")
            b_copy, b_save, b_close = QPushButton("Копировать"), QPushButton("Сохранить .md…"), QPushButton("Закрыть")
            b_copy.setDefault(True)
            b_copy.clicked.connect(self.copy); b_save.clicked.connect(self.save); b_close.clicked.connect(self.close)
            bottom.addWidget(self.lbl_size, 1); bottom.addWidget(b_copy); bottom.addWidget(b_save); bottom.addWidget(b_close)
            lay.addLayout(bottom)
            self.resize(980, 720)
            self.refresh()

        def options(self) -> Options:
            return Options(sections={k for k, c in self.cb.items() if c.isChecked()}, mask=self.cb_mask.isChecked(),
                           news_days=self.sp_news.value(), history_months=self.sp_hist.value())

        def refresh(self, *_) -> None:
            self.text = build(self.data, self.options())
            self.view.setPlainText(self.text)
            # грубая оценка: ~3,5 символа кириллицы на токен у современных токенизаторов
            self.lbl_size.setText(f"{len(self.text):,} символов, ≈ {len(self.text) / 3500:.1f} тыс. токенов".replace(",", " "))

        def copy(self) -> None:
            QGuiApplication.clipboard().setText(self.text)
            self.lbl_size.setText("Скопировано в буфер обмена — вставь в чат с моделью.")

        def save(self) -> None:
            name = f"portfolio-{datetime.now():%Y-%m-%d}.md"
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить для ИИ-анализа", name, "Markdown (*.md)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.text)
                self.lbl_size.setText(f"Сохранено: {path}")

    return AIExportDialog


def open_dialog(win):
    d = _dialog_class()(win)
    d.show(); d.raise_(); d.activateWindow()
    return d
