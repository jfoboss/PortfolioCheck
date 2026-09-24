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
