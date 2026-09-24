"""
risk.py — риск портфеля по истории цен (BACKLOG п. 22, аналог CORR / PORT → risk у Bloomberg).

Из кэша дневных свечей за год, без сети и без numpy (для 10–40 бумаг чистого Python хватает):
  • волатильность портфеля (годовая, по текущим весам и ковариации дневных доходностей);
  • максимальная просадка «сегодняшнего состава» за период;
  • эффективное число бумаг 1/Σw² — сколько «равных» позиций даёт такую же концентрацию;
  • коэффициент диверсификации Σwᵢσᵢ / σₚ — во сколько раз корреляции гасят риск (1 — не гасят вовсе);
  • вклад каждой бумаги в риск (доля дисперсии) и самые связанные пары.
Прошлое — не прогноз: это описание того, как портфель вёл себя за период, так и подписывается в интерфейсе.
Бумаги без свечей в расчёт не входят и перечисляются явно (класс без данных — не ноль).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

TRADING_DAYS = 252


@dataclass
class RiskReport:
    days: int                                    # общих торговых дней в расчёте
    vol: float                                   # годовая волатильность портфеля, %
    max_drawdown: float                          # %, отрицательное
    effective_n: float
    diversification: float
    rows: list = field(default_factory=list)     # [(тикер, имя, вес %, волатильность %, вклад в риск %)]
    pairs: list = field(default_factory=list)    # [(тикер1, тикер2, корреляция)] — самые связанные
    missing: list = field(default_factory=list)  # бумаги без истории


def _returns(series: list[tuple[str, float]], start: str) -> dict[str, float]:
    """Дневные лог-доходности {дата: r} начиная со start."""
    s = [(d, c) for d, c in series if d >= start and c > 0]
    return {s[i][0]: math.log(s[i][1] / s[i - 1][1]) for i in range(1, len(s))}


def _mean(x):
    return sum(x) / len(x)


def analyze(positions, candles: dict[str, list[tuple[str, float]]], days: int = 365, today: date | None = None,
            top_pairs: int = 5) -> RiskReport | None:
    """positions — с figi, value_rub; candles — figi → [(дата, close)]. None — меньше двух бумаг с историей."""
    start = ((today or date.today()) - timedelta(days=days)).isoformat()
    live = [p for p in positions if float(p.value_rub) > 0]
    total = sum(float(p.value_rub) for p in live)
    rets, missing, used = {}, [], []
    for p in live:
        r = _returns(candles.get(p.figi) or [], start)
        if len(r) >= 20:
            rets[p.ticker] = r; used.append(p)
        else:
            missing.append(p.ticker)
    if len(used) < 2 or total <= 0:
        return None
    dates = sorted(set.intersection(*(set(r) for r in rets.values())))
    if len(dates) < 20:
        return None
    tick = [p.ticker for p in used]
    X = {t: [rets[t][d] for d in dates] for t in tick}
    mu = {t: _mean(X[t]) for t in tick}
    n = len(dates)
    cov = {(a, b): sum((X[a][k] - mu[a]) * (X[b][k] - mu[b]) for k in range(n)) / (n - 1) for a in tick for b in tick}
    w_used = sum(float(p.value_rub) for p in used)
    w = {p.ticker: float(p.value_rub) / w_used for p in used}          # веса среди бумаг с историей
    var_p = sum(w[a] * w[b] * cov[(a, b)] for a in tick for b in tick)
    sd = {t: math.sqrt(cov[(t, t)]) for t in tick}
    vol_p = math.sqrt(max(var_p, 0.0))
    ann = math.sqrt(TRADING_DAYS)
    contrib = {a: (w[a] * sum(w[b] * cov[(a, b)] for b in tick) / var_p * 100) if var_p else 0.0 for a in tick}
    names = {p.ticker: p.name for p in used}
    rows = sorted(((t, names[t], w[t] * 100, sd[t] * ann * 100, contrib[t]) for t in tick), key=lambda r: -r[4])
    pairs = []
    for i, a in enumerate(tick):
        for b in tick[i + 1:]:
            if sd[a] and sd[b]:
                pairs.append((a, b, cov[(a, b)] / (sd[a] * sd[b])))
    pairs.sort(key=lambda x: -x[2])
    # просадка «сегодняшнего состава» по дням: стоимость = Σ wᵢ·exp(накопленная доходность)
    level, peak, mdd = 1.0, 1.0, 0.0
    cum = {t: 0.0 for t in tick}
    for k in range(n):
        for t in tick:
            cum[t] += X[t][k]
        level = sum(w[t] * math.exp(cum[t]) for t in tick)
        peak = max(peak, level)
        mdd = min(mdd, level / peak - 1)
    all_w = [float(p.value_rub) / total for p in live]
    eff_n = 1 / sum(x * x for x in all_w)
    div = sum(w[t] * sd[t] for t in tick) / vol_p if vol_p else 1.0
    return RiskReport(n, vol_p * ann * 100, mdd * 100, eff_n, div, rows, pairs[:top_pairs], missing)
