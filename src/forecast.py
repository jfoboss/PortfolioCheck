"""
forecast.py — вкладка «Прогноз»: сложный процент, реинвест дивидендов, пополнения, инфляция, цель.

Модель (шаг — год):
  доход_{t+1}   = доход_t · (1 + рост)  +  (реинвест · доход_t + пополнения_за_год) · доходность_новых_денег
  капитал_{t+1} = капитал_t · (1 + рост_цены) + реинвест · доход_t + пополнения_за_год
  реальный доход_t = доход_t / (1 + инфляция)^t
Цель достигнута, когда реальный доход в месяц ≥ целевого (в сегодняшних деньгах).

Три сценария из прежнего разбора: A — всё забираешь (реинвест 0%), B — 50%, C — 100%.
Графики рисуются QPainter'ом — QtCharts нет в PySide6-Essentials, а тащить Addons ради двух линий незачем.
"""

from __future__ import annotations

from datetime import datetime, date
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QDoubleSpinBox, QSpinBox, QLabel, QPushButton, QProgressBar, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox, QSplitter, QTabWidget,
)

DEFAULTS = {
    "start_capital": 1_000_000,    # ₽, подставляется из портфеля автоматически
    "base_income": 80_000,         # ₽/год после налога — подставляется из выплат по API
    "monthly_contribution": 30_000,
    "reinvest_pct": 100,
    "income_growth_pct": 7.0,      # органический рост выплат
    "yield_new_money_pct": 10.0,   # доходность реинвестируемых/новых денег
    "price_growth_pct": 3.0,       # переоценка капитала сверх дохода
    "inflation_pct": 6.5,
    "target_monthly_real": 100_000,
    "years": 20,
    "start_capital_auto": True,    # брать стартовый капитал из портфеля при каждом обновлении
    "base_income_auto": True,      # брать доход за год из API (выплаты за 12 мес)
    "inflation_pct_auto": True,        # брать инфляцию с сайта ЦБ РФ
    "yield_new_money_pct_auto": True,  # брать доходность новых денег из портфеля (по целевым долям)
    "price_growth_pct_auto": False,    # брать переоценку капитала из портфеля
    "growth_base_years": 5,            # база расчёта переоценки: 1 / 3 / 5 лет (среднегодовой рост)
    "plan_start": "",              # дата фиксации плана (ISO) — точка отсчёта «план vs факт»
    "plan_capital": 0,             # капитал на дату фиксации
    "plan_income": 0,              # доход за 12 мес на дату фиксации
    "target_year": 0,              # год, к которому хочется достичь цели (0 — расчётный)
}

SCENARIOS = [("A — всё забираешь", 0, "#888780"), ("B — реинвест 50%", 50, "#0F6E56"), ("C — реинвест 100%", 100, "#534AB7")]


@dataclass
class YearRow:
    year: int
    capital: float
    income: float
    income_real: float
    contributions: float


def simulate(p: dict, reinvest_pct: float | None = None) -> list[YearRow]:
    """Годовой ряд. reinvest_pct переопределяет долю реинвеста (для сценариев)."""
    r = (p["reinvest_pct"] if reinvest_pct is None else reinvest_pct) / 100
    g, y, pg, infl = (p["income_growth_pct"] / 100, p["yield_new_money_pct"] / 100,
                      p["price_growth_pct"] / 100, p["inflation_pct"] / 100)
    contrib = p["monthly_contribution"] * 12
    cap, inc = float(p["start_capital"]), float(p["base_income"])
    rows = [YearRow(0, cap, inc, inc, 0.0)]
    for t in range(1, int(p["years"]) + 1):
        new_money = r * inc + contrib
        inc = inc * (1 + g) + new_money * y
        cap = cap * (1 + pg) + new_money
        rows.append(YearRow(t, cap, inc, inc / (1 + infl) ** t, contrib))
    return rows


def simulate_monthly(p: dict, months: int, reinvest_pct: float | None = None,
                     start_capital: float | None = None, start_income: float | None = None) -> list[tuple[float, float]]:
    """Помесячный ряд [(капитал, реальный доход в месяц)] — для сравнения плана с фактом на любую дату."""
    r = (p["reinvest_pct"] if reinvest_pct is None else reinvest_pct) / 100
    g, y, pg, infl = (p["income_growth_pct"] / 100, p["yield_new_money_pct"] / 100,
                      p["price_growth_pct"] / 100, p["inflation_pct"] / 100)
    contrib = float(p["monthly_contribution"])
    cap = float(start_capital if start_capital is not None else p["start_capital"])
    inc = float(start_income if start_income is not None else p["base_income"])
    out = [(cap, inc / 12)]
    for m in range(1, months + 1):
        new_money = r * inc / 12 + contrib
        inc = inc * (1 + g) ** (1 / 12) + new_money * y
        cap = cap * (1 + pg) ** (1 / 12) + new_money
        out.append((cap, inc / 12 / (1 + infl) ** (m / 12)))
    return out


def months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + (b.month - a.month))


@dataclass
class PlanStatus:
    months_passed: int
    plan_capital: float          # где должен быть капитал сегодня по плану
    fact_capital: float
    plan_income_m: float         # реальный доход в месяц по плану сегодня
    fact_income_m: float
    ahead_rub: float             # + опережение, − отставание (по капиталу)
    ahead_months: float          # на сколько месяцев впереди/позади графика
    progress_pct: float          # путь к цели по реальному доходу
    required_contribution: float | None   # какой взнос нужен, чтобы успеть к целевому году
    goal_year: int | None


def required_contribution(p: dict, target_year: int, lo: float = 0.0, hi: float = 3_000_000.0) -> float | None:
    """Обратная задача: какой ежемесячный взнос нужен, чтобы цель достигалась к target_year (бинарный поиск)."""
    years = max(1, target_year - datetime.now().year)
    target = p["target_monthly_real"]

    def reached(contrib: float) -> bool:
        q = {**p, "monthly_contribution": contrib}
        return simulate_monthly(q, years * 12)[-1][1] >= target

    if reached(lo):
        return 0.0
    if not reached(hi):
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if reached(mid):
            hi = mid
        else:
            lo = mid
    return hi


def plan_status(p: dict, fact_capital: float, fact_income_year: float, plan_start: date,
                plan_capital0: float, plan_income0: float, target_year: int) -> PlanStatus:
    """Где мы относительно плана, зафиксированного на дату plan_start."""
    today = date.today()
    m = months_between(plan_start, today)
    horizon = max(m + 1, (target_year - plan_start.year) * 12 + 1)
    plan = simulate_monthly(p, horizon, start_capital=plan_capital0, start_income=plan_income0)
    plan_cap, plan_inc_m = plan[min(m, len(plan) - 1)]
    fact_inc_m = fact_income_year / 12 / (1 + p["inflation_pct"] / 100) ** (m / 12)
    ahead_rub = fact_capital - plan_cap
    # на сколько месяцев впереди/позади: когда план достигает текущего факта
    ahead_months = 0.0
    caps = [c for c, _ in plan]
    for i, c in enumerate(caps):
        if c >= fact_capital:
            ahead_months = i - m
            break
    else:
        ahead_months = len(caps) - m
    target = p["target_monthly_real"]
    return PlanStatus(m, plan_cap, fact_capital, plan_inc_m, fact_inc_m, ahead_rub, ahead_months,
                      min(100.0, fact_inc_m / target * 100) if target else 0.0,
                      required_contribution(p, target_year),
                      goal_year_absolute(p)[0])


def goal_year_absolute(p: dict, max_years: int = 50) -> tuple[int | None, bool]:
    """Год достижения цели без оглядки на горизонт: (год, за пределами ли выбранного горизонта)."""
    series = simulate_monthly(p, max_years * 12)
    target = p["target_monthly_real"]
    for i, (_, inc_m) in enumerate(series):
        if inc_m >= target:
            years = i / 12
            return datetime.now().year + int(years + 0.999), years > float(p["years"])
    return None, True


def goal_year(rows: list[YearRow], target_monthly_real: float) -> int | None:
    for r in rows:
        if r.income_real / 12 >= target_monthly_real:
            return r.year
    return None


from charts import LineChart
from widgets import scroll_wrap
import style as SY


# ---------------------------------------------------------------------------
# Вкладка
# ---------------------------------------------------------------------------


def _need_line(req: float | None, monthly: float, ty: int) -> str:
    """«Чтобы успеть к ГГГГ, нужно/достаточно N ₽/мес — …» (одна фраза для плана и без плана)."""
    if req is None:
        return f"Цель к {ty} году недостижима при этих параметрах — сдвинь год или измени допущения."
    if req <= monthly:
        return (f"Чтобы успеть к {ty}, достаточно <b>{req:,.0f} ₽/мес</b> — "
                + SY.span(f"можно вносить на {monthly - req:,.0f} ₽ меньше", SY.GOOD) + ".")
    return (f"Чтобы успеть к {ty}, нужно <b>{req:,.0f} ₽/мес</b> — "
            + SY.span(f"на {req - monthly:,.0f} ₽ больше, чем сейчас", SY.BAD) + ".")

class ForecastTab(QWidget):
    paramsChanged = Signal(dict)

    def __init__(self, params: dict | None, parent=None):
        super().__init__(parent)
        self.p = {**DEFAULTS, **(params or {})}
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(400)
        self._timer.timeout.connect(self._emit)
        self._loading = True

        # ---- левая панель: параметры
        form_w = QWidget(); form = QFormLayout(form_w)
        form.setContentsMargins(16, 12, 16, 12)
        self.w: dict[str, QSpinBox | QDoubleSpinBox] = {}

        def rub(key, label, step=10_000, mx=2_000_000_000):
            sb = QSpinBox(); sb.setRange(0, mx); sb.setSingleStep(step); sb.setSuffix(" ₽")
            sb.setGroupSeparatorShown(True); sb.setValue(int(self.p[key])); sb.valueChanged.connect(self._touch)
            self.w[key] = sb; form.addRow(label, sb)

        def pct(key, label, mn=0.0, mx=100.0, step=0.5):
            sb = QDoubleSpinBox(); sb.setRange(mn, mx); sb.setSingleStep(step); sb.setDecimals(1); sb.setSuffix(" %")
            sb.setValue(float(self.p[key])); sb.valueChanged.connect(self._touch)
            self.w[key] = sb; form.addRow(label, sb)

        def auto_row(key, label, step=None, pct_args=None, cb_label="из портфеля"):
            if pct_args is not None:
                pct(key, label, *pct_args)
            else:
                rub(key, label, step)
            sb = self.w[key]
            cb = QCheckBox(cb_label); cb.setChecked(bool(self.p.get(key + "_auto", DEFAULTS.get(key + "_auto", True))))
            cb.setToolTip("Пока включено — значение подставляется из портфеля при каждом обновлении")
            sb.setEnabled(not cb.isChecked())
            cb.toggled.connect(lambda on, sb=sb: (sb.setEnabled(not on), self._touch()))
            cb.toggled.connect(lambda on, k=key: on and self._request_and_apply(k))
            self.auto[key] = cb
            row = QHBoxLayout(); row.addWidget(sb, 1); row.addWidget(cb)
            form.removeRow(form.rowCount() - 1); form.addRow(label, row)

        self.auto: dict[str, QCheckBox] = {}
        self._pf_total: float | None = None
        self._pf_income: float | None = None
        self._cbr_inflation: float | None = None
        self._cbr_note = ""
        self._buy_yield: float | None = None
        self._buy_note = ""
        self._growth: float | None = None
        self._growth_note = ""
        self.request_refresh = None        # владелец (главное окно) пересчитывает значения по запросу
        self.request_long_history = None   # владелец грузит свечи за N лет
        self.growth_covered = 1.0          # фактическая глубина истории, лет
        auto_row("start_capital", "Стартовый капитал:", 100_000)
        auto_row("base_income", "Доход за год сейчас (после налога):", 10_000)
        rub("monthly_contribution", "Пополнение в месяц:", 10_000)
        pct("reinvest_pct", "Доля реинвеста дохода:", 0, 100, 5)
        pct("income_growth_pct", "Органический рост выплат в год:", -20, 50)
        auto_row("yield_new_money_pct", "Доходность новых денег:", pct_args=(0, 50), cb_label="из портфеля")
        auto_row("price_growth_pct", "Переоценка капитала в год:", pct_args=(-20, 50), cb_label="из портфеля")
        self.cmb_growth_base = QComboBox()
        for label, yrs in (("за 12 месяцев", 1), ("среднегодовой за 3 года", 3), ("среднегодовой за 5 лет", 5)):
            self.cmb_growth_base.addItem(label, yrs)
        self.cmb_growth_base.setCurrentIndex(max(0, self.cmb_growth_base.findData(int(self.p.get("growth_base_years", 5)))))
        self.cmb_growth_base.currentIndexChanged.connect(self._growth_base_changed)
        form.addRow("База расчёта переоценки:", self.cmb_growth_base)
        self.w["price_growth_pct"].setToolTip("Рост цены активов сверх выплат — сюда попадает эффект золота и переоценка ЗПИФ.\n"
                                              "«Из портфеля» — фактический рост цен за последние 12 месяцев, взвешенный по целевым долям.")
        auto_row("inflation_pct", "Инфляция:", pct_args=(0, 50), cb_label="из ЦБ РФ")
        rub("target_monthly_real", "Цель: доход в месяц (в сегодняшних ₽):", 10_000)
        yrs = QSpinBox(); yrs.setRange(1, 50); yrs.setSuffix(" лет"); yrs.setValue(int(self.p["years"]))
        yrs.valueChanged.connect(self._touch); self.w["years"] = yrs; form.addRow("Горизонт:", yrs)

        # план: точка отсчёта и целевой год
        self.ed_plan = QLabel(); self.ed_plan.setStyleSheet("color: gray")
        self.btn_fix_plan = QPushButton("Зафиксировать план с сегодня")
        self.btn_fix_plan.setToolTip("Запомнить текущие капитал и доход как точку отсчёта — дальше видно опережение/отставание")
        self.btn_fix_plan.clicked.connect(self._fix_plan)
        self.sp_target_year = QSpinBox(); self.sp_target_year.setRange(datetime.now().year, datetime.now().year + 60)
        self.sp_target_year.setValue(int(self.p.get("target_year") or 0) or datetime.now().year + 15)
        self.sp_target_year.valueChanged.connect(self._touch)
        form.addRow("Цель к году:", self.sp_target_year)
        form.addRow(self.btn_fix_plan)
        form.addRow(self.ed_plan)

        self.lbl_hint = QLabel(); self.lbl_hint.setWordWrap(True); self.lbl_hint.setStyleSheet(f"color: {SY.WARN}")
        form.addRow(self.lbl_hint)

        self.lbl_summary = QLabel(); self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextFormat(Qt.RichText)
        form.addRow(self.lbl_summary)

        # ---- правая панель: графики + таблица
        # панель «Где я сейчас» — план против факта
        self.lbl_status = QLabel(); self.lbl_status.setTextFormat(Qt.RichText); self.lbl_status.setWordWrap(True)
        self.bar_goal = QProgressBar(); self.bar_goal.setRange(0, 100); self.bar_goal.setTextVisible(True)
        self.bar_goal.setFormat("путь к цели: %p%")
        status_box = QWidget(); sb = QVBoxLayout(status_box); sb.setContentsMargins(8, 6, 8, 6)
        sb.addWidget(QLabel("<b>Где я сейчас</b>")); sb.addWidget(self.lbl_status); sb.addWidget(self.bar_goal)
        self.status_box = status_box

        self.chart_income = LineChart("Пассивный доход в месяц, в сегодняшних деньгах", "₽ / мес")
        self.chart_capital = LineChart("Капитал (номинал) при выбранной доле реинвеста", "₽")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Год", "Капитал", "Доход / год", "В месяц (реально)", "% цели"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        right = QTabWidget()
        charts = QWidget(); charts.setAutoFillBackground(True); charts.setBackgroundRole(QPalette.Base)
        cl = QVBoxLayout(charts); cl.setContentsMargins(8, 8, 8, 8)
        cl.addWidget(self.chart_income, 1); cl.addWidget(self.chart_capital, 1)
        right.addTab(scroll_wrap(charts), "Графики"); right.addTab(self.table, "По годам")
        right_w = QWidget(); rl = QVBoxLayout(right_w); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(self.status_box); rl.addWidget(right, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll_wrap(form_w, base_background=False)); split.addWidget(right_w)   # форма длинная — прокрутка
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([380, 700])
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(split)

        self._loading = False
        self.recalc()

    # ---- параметры ----------------------------------------------------------

    def params(self) -> dict:
        p = {k: w.value() for k, w in self.w.items()}
        p.update({k + "_auto": cb.isChecked() for k, cb in self.auto.items()})
        p["target_year"] = self.sp_target_year.value()
        p["growth_base_years"] = self.growth_base()
        for k in ("plan_start", "plan_capital", "plan_income"):
            p[k] = self.p.get(k, DEFAULTS[k])
        return p

    def _fix_plan(self) -> None:
        """Точка отсчёта: сегодняшние капитал и доход становятся началом плана."""
        self.p["plan_start"] = date.today().isoformat()
        self.p["plan_capital"] = float(self.w["start_capital"].value())
        self.p["plan_income"] = float(self.w["base_income"].value())
        self._emit(); self.recalc()

    def _plan_anchor(self):
        """(дата, капитал, доход) начала плана. Если план не зафиксирован — сегодняшние значения."""
        s = self.p.get("plan_start") or ""
        try:
            d0 = date.fromisoformat(s)
        except Exception:  # noqa: BLE001
            return None
        return d0, float(self.p.get("plan_capital") or 0), float(self.p.get("plan_income") or 0)

    def _touch(self, *_) -> None:
        if self._loading:
            return
        self.recalc()
        self._timer.start()

    def _emit(self) -> None:
        self.paramsChanged.emit(self.params())

    def set_portfolio(self, total: float, income: float | None = None, macro=None, buy_yield=None, price_growth=None) -> None:
        """Главное окно передаёт стоимость портфеля, оценку дохода и макроданные ЦБ после каждого обновления.
        Поля с включённой галочкой подхватывают их сами."""
        self._pf_total = total
        if income and income > 0:
            self._pf_income = income
        if buy_yield is not None:
            y, detail = buy_yield
            if y:
                self._buy_yield = y
                parts = " + ".join(f"{name} {share:.0f}%×{cy:.1f}%" for name, share, cy in detail if share >= 1)
                self._buy_note = (f"Доходность новых денег {y:.1f}% — взвешенно по твоим целевым долям "
                                  f"(новые деньги идут во все классы, включая те, что не платят): {parts}")
        if price_growth is not None:
            g, detail, missing = price_growth
            if g is not None and detail:
                self._growth = g
                parts = " + ".join(f"{name} {share:.0f}%×{gr:+.0f}%" for name, share, gr in detail if share >= 1)
                skip = ("; <b>не учтено (нет свечей): " + ", ".join(missing) + "</b>") if missing else ""
                base = self.growth_base()
                period_txt = ("рост цен за 12 мес" if base == 1 else
                              f"среднегодовой рост цен за {self.growth_covered:.1f} г (запрошено {base})")
                self._growth_note = (f"Переоценка капитала {g:+.1f}% — {period_txt}, взвешенный по целевым долям: "
                                     + parts + skip
                                     + (". Это факт прошлого года, а не прогноз — для долгого горизонта обычно берут скромнее."
                                        if base == 1 else ". Историческая средняя — ориентир, а не гарантия."))
        if macro is not None:
            self._cbr_inflation = macro.inflation
            self._cbr_note = f"ЦБ РФ, {macro.period}: инфляция {macro.inflation:.2f}% г/г, ключевая ставка {macro.key_rate:.2f}%, цель {macro.target:.0f}%"
        self._apply_portfolio()

    def growth_base(self) -> int:
        return int(self.cmb_growth_base.currentData() or 1)

    def _growth_base_changed(self) -> None:
        """Сменили базу: для 3/5 лет просим владельца догрузить длинную историю."""
        self._touch()
        years = self.growth_base()
        if years > 1 and callable(self.request_long_history):
            self.request_long_history(float(years))     # сначала из кэша, при нехватке — фоновая догрузка
        elif callable(self.request_refresh):
            self.request_refresh()

    def _request_and_apply(self, key: str) -> None:
        """Галочку включили: просим владельца пересчитать значения из портфеля и подставляем."""
        if callable(self.request_refresh):
            try:
                self.request_refresh()          # вызовет set_portfolio с актуальными данными
            except Exception as e:  # noqa: BLE001
                import logging; logging.getLogger("portfolio").error("пересчёт из портфеля: %s", e)
        self._apply_portfolio()
        have = {"start_capital": self._pf_total, "base_income": self._pf_income,
                "yield_new_money_pct": self._buy_yield, "price_growth_pct": self._growth,
                "inflation_pct": self._cbr_inflation}.get(key)
        if have is None:
            self.lbl_hint.setText("Нечего подставить: данные ещё не загружены. Нажми «Обновить» — для переоценки "
                                  "нужны свечи, они грузятся сразу после портфеля.")
            QTimer.singleShot(8000, lambda: self.lbl_hint.setText(""))
        else:
            self.lbl_hint.setText("")

    def _apply_portfolio(self) -> None:
        self._loading = True
        if self.auto["start_capital"].isChecked() and self._pf_total is not None:
            self.w["start_capital"].setValue(int(self._pf_total))
        if self.auto["base_income"].isChecked() and self._pf_income is not None:
            self.w["base_income"].setValue(int(self._pf_income))
        if self.auto["inflation_pct"].isChecked() and self._cbr_inflation is not None:
            self.w["inflation_pct"].setValue(float(self._cbr_inflation))
        if self.auto["yield_new_money_pct"].isChecked() and self._buy_yield is not None:
            self.w["yield_new_money_pct"].setValue(float(self._buy_yield))
        if self.auto["price_growth_pct"].isChecked() and self._growth is not None:
            self.w["price_growth_pct"].setValue(float(self._growth))
        self._loading = False
        self.recalc()
        self._timer.start()

    # ---- расчёт -------------------------------------------------------------

    def _render_status(self, p: dict, rows, target: float) -> None:
        """Панель «Где я сейчас»: план против факта, опережение/отставание, нужный взнос."""
        anchor = self._plan_anchor()
        fact_cap = float(p["start_capital"]); fact_inc = float(p["base_income"])
        ty = int(p.get("target_year") or 0) or (datetime.now().year + 15)
        if anchor is None:
            req = required_contribution(p, ty)
            self.bar_goal.setValue(int(min(100, fact_inc / 12 / target * 100)) if target else 0)
            need = _need_line(req, p["monthly_contribution"], ty)
            self.lbl_status.setText("План не зафиксирован — нажми «Зафиксировать план с сегодня», чтобы видеть опережение "
                                    "или отставание от графика.<br>" + need)
            self.ed_plan.setText("План не зафиксирован")
            return
        d0, cap0, inc0 = anchor
        st = plan_status(p, fact_cap, fact_inc, d0, cap0, inc0, ty)
        self.ed_plan.setText(f"План от {d0.strftime('%d.%m.%Y')}: капитал {cap0:,.0f} ₽, доход {inc0:,.0f} ₽/год")
        self.bar_goal.setValue(int(st.progress_pct))
        ahead = st.ahead_rub >= 0
        color = SY.GOOD if ahead else SY.BAD
        word = "Опережение" if ahead else "Отставание"
        req = st.required_contribution
        need = _need_line(req, p["monthly_contribution"], ty)
        now_line = (f"Сейчас: <b>{st.fact_income_m:,.0f} ₽/мес</b> из {target:,.0f} — это "
                    f"<b>{st.progress_pct:.0f}%</b> цели; капитал <b>{st.fact_capital:,.0f} ₽</b>.")
        if st.months_passed < 1:
            dev = ("План зафиксирован сегодня — отклонение появится, когда пройдёт хотя бы месяц. "
                   "Пока сравнивать не с чем.")
        else:
            dev = (f"Прошло {st.months_passed} мес. По плану сегодня: <b>{st.plan_capital:,.0f} ₽</b> и "
                   f"<b>{st.plan_income_m:,.0f} ₽/мес</b> → "
                   f"<span style='color:{color}'><b>{word}: {abs(st.ahead_rub):,.0f} ₽ ≈ {abs(st.ahead_months):.0f} мес графика</b></span>.")
        abs_year, beyond = goal_year_absolute(p)
        year_line = ("Цель не достигается и за 50 лет." if abs_year is None else
                     f"Расчётный год достижения цели: <b>{abs_year}</b>"
                     + (f" (дальше выбранного горизонта в {int(p['years'])} лет)" if beyond else "") + ".")
        self.lbl_status.setText(now_line + "<br>" + dev + "<br>" + need + "<br>" + year_line)

    def recalc(self) -> None:
        p = self.params()
        target = p["target_monthly_real"]
        labels = [str(2026 + i) for i in range(int(p["years"]) + 1)]

        series_income, goal_txt = [], []
        for name, r, color in SCENARIOS:
            rows = simulate(p, r)
            series_income.append((name, [x.income_real / 12 for x in rows], color, r == 0))
            gy = goal_year(rows, target)
            goal_txt.append(f"{name}: {'цель в ' + labels[gy] if gy is not None else 'цель не достигается за горизонт'}")
        self.chart_income.set_data(labels, series_income, ("цель", target))

        rows = simulate(p)
        self.chart_capital.set_data(labels, [("капитал", [x.capital for x in rows], "#534AB7", False)])

        self.table.setRowCount(len(rows))
        for i, x in enumerate(rows):
            cells = [labels[i], f"{x.capital:,.0f}", f"{x.income:,.0f}", f"{x.income_real / 12:,.0f}",
                     f"{x.income_real / 12 / target * 100:.0f}%" if target else "—"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if c else Qt.AlignLeft | Qt.AlignVCenter)
                if c == 4 and target and x.income_real / 12 >= target:
                    it.setBackground(QColor(215, 245, 215))
                self.table.setItem(i, c, it)

        self._render_status(p, rows, target)
        gy = goal_year(rows, target)
        last = rows[-1]
        abs_year, beyond = goal_year_absolute(p)
        if gy is not None:
            head = f"<b>При текущих параметрах цель достигается в {labels[gy]}</b>"
        elif abs_year is not None:
            head = (f"<b>Цель достигается в {abs_year}</b> — это дальше выбранного горизонта в {int(p['years'])} лет; "
                    f"к концу горизонта {last.income_real / 12:,.0f} ₽/мес ({last.income_real / 12 / target * 100:.0f}% цели)")
        else:
            head = (f"<b>Цель не достигается и за 50 лет</b> — к концу горизонта {last.income_real / 12:,.0f} ₽/мес "
                    f"({last.income_real / 12 / target * 100:.0f}% цели)")
        self.lbl_summary.setText(head + "<br><span style='color:gray'>" + "<br>".join(goal_txt) + "</span>"
                                 + f"<br><br>Всего пополнений за горизонт: {sum(x.contributions for x in rows):,.0f} ₽. "
                                   f"Капитал к концу: {last.capital:,.0f} ₽ (номинал)."
                                 + (f"<br><span style='color:gray'>{self._buy_note}</span>" if self._buy_note else "")
                                 + (f"<br><span style='color:gray'>{self._growth_note}</span>" if self._growth_note else "")
                                 + (f"<br><span style='color:gray'>{self._cbr_note}</span>" if self._cbr_note else ""))
