"""Риск портфеля (risk.py): волатильность, просадка, концентрация, корреляции, бумаги без истории."""
import math
from datetime import date, timedelta
from types import SimpleNamespace as NS

import risk as RK

TODAY = date(2026, 9, 24)


def series(fn, n=260):
    d0 = TODAY - timedelta(days=n)
    return [((d0 + timedelta(days=k)).isoformat(), fn(k)) for k in range(n + 1)]


def P(t, value, figi=None):
    return NS(ticker=t, name=t, figi=figi or t, value_rub=value)


def test_identical_assets_have_no_diversification():
    wave = lambda k: 100 * math.exp(0.01 * math.sin(k))
    r = RK.analyze([P("A", 500), P("B", 500)], {"A": series(wave), "B": series(wave)}, today=TODAY)
    assert abs(r.diversification - 1.0) < 1e-6 and abs(r.pairs[0][2] - 1.0) < 1e-6
    assert abs(r.effective_n - 2.0) < 1e-9
    assert abs(sum(c for *_x, c in r.rows) - 100) < 1e-6          # вклады в риск складываются в 100%


def test_opposite_assets_cancel_and_drawdown_is_negative():
    up = lambda k: 100 * math.exp(0.01 * math.sin(k))
    down = lambda k: 100 * math.exp(-0.01 * math.sin(k))
    r = RK.analyze([P("A", 500), P("B", 500)], {"A": series(up), "B": series(down)}, today=TODAY)
    assert r.pairs[0][2] < -0.99 and r.vol < 0.5 and r.diversification > 10
    fall = lambda k: 100 * (1 - 0.001 * k)
    r2 = RK.analyze([P("A", 500), P("B", 500)], {"A": series(fall), "B": series(fall)}, today=TODAY)
    assert r2.max_drawdown < -20


def test_missing_history_is_listed_not_zero():
    wave = lambda k: 100 * math.exp(0.01 * math.sin(k))
    r = RK.analyze([P("A", 400), P("B", 400), P("GOLD", 200)], {"A": series(wave), "B": series(lambda k: 50 + k % 3)},
                   today=TODAY)
    assert r.missing == ["GOLD"] and abs(sum(w for _t, _n, w, _v, _c in r.rows) - 100) < 1e-6
    assert r.effective_n < 3                                      # концентрация — по всем бумагам, включая без истории
    assert RK.analyze([P("A", 1)], {"A": series(wave)}, today=TODAY) is None
