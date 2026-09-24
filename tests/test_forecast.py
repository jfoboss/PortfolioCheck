from datetime import date

import forecast as F

BASE = {**F.DEFAULTS, "start_capital": 13_000_000, "base_income": 660_000, "monthly_contribution": 100_000,
        "yield_new_money_pct": 10.0, "price_growth_pct": 3.0, "inflation_pct": 6.0, "income_growth_pct": 7.0,
        "reinvest_pct": 100, "target_monthly_real": 500_000, "years": 20}


def test_more_contribution_reaches_goal_earlier():
    y1, _ = F.goal_year_absolute(BASE)
    y2, _ = F.goal_year_absolute({**BASE, "monthly_contribution": 300_000})
    assert y1 and y2 and y2 < y1


def test_goal_year_beyond_horizon_is_reported_not_none():
    """Регресс: год цели за пределами горизонта показывался как «—»."""
    y, beyond = F.goal_year_absolute({**BASE, "years": 5})
    assert y is not None and beyond is True


def test_required_contribution_monotonic():
    near = F.required_contribution(BASE, date.today().year + 12)
    far = F.required_contribution(BASE, date.today().year + 25)
    assert near is not None and far is not None and near > far


def test_plan_status_ahead_and_behind():
    start = date(date.today().year, 1, 1)
    ahead = F.plan_status(BASE, 14_000_000, 700_000, start, 12_000_000, 620_000, date.today().year + 15)
    behind = F.plan_status(BASE, 10_000_000, 500_000, start, 12_000_000, 620_000, date.today().year + 15)
    assert ahead.ahead_rub > 0 > behind.ahead_rub


ZERO = {**BASE, "start_capital": 0, "base_income": 0, "monthly_contribution": 0, "yield_new_money_pct": 0,
        "price_growth_pct": 0, "income_growth_pct": 0, "inflation_pct": 0}


def test_all_zero_parameters_do_not_crash():
    rows = F.simulate(ZERO)
    assert len(rows) == ZERO["years"] + 1 and all(r.capital == 0 and r.income == 0 for r in rows)
    assert F.goal_year_absolute(ZERO) == (None, True)                 # цель недостижима — честно None
    assert F.required_contribution({**ZERO, "yield_new_money_pct": 0}, date.today().year + 10) is None
    st = F.plan_status(ZERO, 0, 0, date.today(), 0, 0, date.today().year + 10)
    assert st.ahead_rub == 0 and st.progress_pct == 0


def test_zero_target_is_reached_immediately():
    p = {**BASE, "target_monthly_real": 0}
    assert F.goal_year_absolute(p)[0] == date.today().year
    assert F.required_contribution(p, date.today().year + 5) == 0.0
    assert F.plan_status(p, 1, 1, date.today(), 1, 1, date.today().year + 5).progress_pct == 0.0


def test_negative_growth_shrinks_capital_but_stays_finite():
    p = {**BASE, "monthly_contribution": 0, "price_growth_pct": -20, "income_growth_pct": -20}
    rows = F.simulate(p)
    assert rows[-1].capital < rows[0].capital and all(r.capital >= 0 and r.income >= 0 for r in rows)
    assert all(c >= 0 for c, _ in F.simulate_monthly(p, 240))


def test_target_year_in_past_and_future_plan_start():
    """Целевой год в прошлом и дата плана в будущем (часы сбились, ручной ввод) не должны ронять расчёт."""
    past = F.required_contribution(BASE, date.today().year - 3)     # считается как «за год»
    assert past is None or past >= F.required_contribution(BASE, date.today().year + 1)
    assert F.required_contribution({**BASE, "target_monthly_real": 60_000}, date.today().year - 3) == 0.0
    future = date(date.today().year + 1, 1, 1)
    st = F.plan_status(BASE, 13_000_000, 660_000, future, 13_000_000, 660_000, date.today().year + 10)
    assert st.months_passed == 0
