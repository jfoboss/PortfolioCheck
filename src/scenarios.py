"""
scenarios.py — сценарии «что если» (BACKLOG п. 23, аналог PORT → scenario / MARS у Bloomberg).

Для каждой бумаги с историей цен — двухфакторная регрессия дневных доходностей за год на индекс Мосбиржи и курс
доллара: r = α + βm·r_индекса + βfx·r_доллара. Сценарий («индекс −30%», «доллар +25%») даёт оценку изменения:
Δ = стоимость · (βm·Δиндекса + βfx·Δдоллара). Это линейное приближение по прошлому году — связи в кризис меняются,
поэтому в интерфейсе так и подписано. Бумаги без истории в оценку не входят и перечисляются явно; валюта
(позиции в USD и т.п.) пересчитывается по курсу напрямую.
Модуль без Qt и numpy — проверяется тестами.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

PRESETS = [("Индекс −30%", -30.0, 0.0), ("Рубль −20% (доллар +25%)", 0.0, 25.0),
           ("Индекс −20% и доллар +15%", -20.0, 15.0), ("Индекс +15%", 15.0, 0.0), ("Доллар −10%", 0.0, -10.0)]
FX_TICKERS = {"USD000UTSTOM": "USD", "USDRUB_TOM": "USD"}


@dataclass
class Beta:
    ticker: str
    name: str
    value: float
    bm: float                      # чувствительность к индексу
    bfx: float                     # чувствительность к доллару
    r2: float                      # доля объяснённой дисперсии, 0…1
    cls: str = ""


@dataclass
class ScenarioResult:
    d_index: float
    d_usd: float
    total: float
    change: float
    by_class: dict = field(default_factory=dict)    # класс → Δ ₽
    rows: list = field(default_factory=list)        # [(тикер, имя, Δ ₽, Δ %)] по модулю
    missing: list = field(default_factory=list)


def _rets(series: list[tuple[str, float]], start: str) -> dict[str, float]:
    s = [(d, c) for d, c in series if d >= start and c > 0]
    return {s[i][0]: math.log(s[i][1] / s[i - 1][1]) for i in range(1, len(s))}


def ols2(y: list[float], x1: list[float], x2: list[float]) -> tuple[float, float, float]:
    """y = a + b1·x1 + b2·x2 по МНК (центрированные данные, решение 2×2). → (b1, b2, R²)."""
    n = len(y)
    my, m1, m2 = sum(y) / n, sum(x1) / n, sum(x2) / n
    s11 = sum((a - m1) ** 2 for a in x1); s22 = sum((b - m2) ** 2 for b in x2)
    s12 = sum((a - m1) * (b - m2) for a, b in zip(x1, x2))
    s1y = sum((a - m1) * (c - my) for a, c in zip(x1, y)); s2y = sum((b - m2) * (c - my) for b, c in zip(x2, y))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-18:                             # факторы вырождены (или один ряд пуст) — одна переменная
        if s11:
            b1, b2 = s1y / s11, 0.0
        else:
            b1, b2 = 0.0, (s2y / s22 if s22 else 0.0)
    else:
        b1 = (s1y * s22 - s2y * s12) / det
        b2 = (s2y * s11 - s1y * s12) / det
    syy = sum((c - my) ** 2 for c in y)
    ss_res = sum((c - my - b1 * (a - m1) - b2 * (b - m2)) ** 2 for a, b, c in zip(x1, x2, y))
    return b1, b2, (1 - ss_res / syy) if syy else 0.0


def betas(positions, candles: dict[str, list], index: list, usd: list, days: int = 365,
          today: date | None = None) -> tuple[list[Beta], list[str]]:
    """(беты бумаг, тикеры без истории). candles — figi → свечи; index, usd — ряды факторов.
    Если один ряд не загрузился (у части пользователей cbr.ru не отвечает) — однофакторная регрессия по другому,
    бета к отсутствующему фактору = 0 (в интерфейсе это подписано)."""
    start = ((today or date.today()) - timedelta(days=days)).isoformat()
    ri, rf = _rets(index, start), _rets(usd, start)
    common = (set(ri) & set(rf)) if (ri and rf) else set(ri or rf)
    out, missing = [], []
    for p in positions:
        v = float(p.value_rub)
        if v <= 0:
            continue
        if p.ticker in FX_TICKERS:                   # валюта: стоимость в рублях = количество × курс
            out.append(Beta(p.ticker, p.name, v, 0.0, 1.0, 1.0, p.cls)); continue
        r = _rets(candles.get(p.figi) or [], start)
        dates = sorted(set(r) & common)
        if len(dates) < 30:
            missing.append(p.ticker); continue
        b1, b2, r2 = ols2([r[d] for d in dates], [ri.get(d, 0.0) for d in dates], [rf.get(d, 0.0) for d in dates])
        out.append(Beta(p.ticker, p.name, v, b1, b2, r2, p.cls))
    return out, missing


def apply(bs: list[Beta], d_index: float, d_usd: float, missing=(), class_label=lambda c: c) -> ScenarioResult:
    """Сценарий в процентах (−30 = −30%). Лог-приближение: Δлог = β·log(1+Δ), результат — exp−1."""
    li, lf = math.log1p(d_index / 100), math.log1p(d_usd / 100)
    rows, by_cls, total, change = [], {}, 0.0, 0.0
    for b in bs:
        d = b.value * (math.exp(b.bm * li + b.bfx * lf) - 1)
        total += b.value; change += d
        by_cls[class_label(b.cls)] = by_cls.get(class_label(b.cls), 0.0) + d
        rows.append((b.ticker, b.name, d, d / b.value * 100 if b.value else 0.0))
    rows.sort(key=lambda r: -abs(r[2]))
    return ScenarioResult(d_index, d_usd, total, change, {k: round(v, 2) for k, v in by_cls.items()}, rows, list(missing))
