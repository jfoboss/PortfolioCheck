"""
history.py — свечи из T-Invest API с кэшем, доходности по периодам (heatmap), история стоимости портфеля.

История стоимости портфеля в API отсутствует, поэтому два ряда:
  • «факт» — из снапшотов, которые приложение пишет при каждом обновлении (копится с первого запуска);
  • «текущий состав по историческим ценам» — сумма qty × close по дням за год. Это приближение: покупки и
    продажи внутри года не учитываются, зато видно, как вёл бы себя сегодняшний портфель.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

PERIODS = [("day", "День", 1), ("week", "Неделя", 7), ("month", "Месяц", 30), ("year", "Год", 365)]


# ---------------------------------------------------------------------------
# Свечи с кэшем
# ---------------------------------------------------------------------------

def _cache_file(cache_dir: Path, figi: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"candles_{figi}.json"


def load_cached(cache_dir: Path, figi: str) -> list[tuple[str, float]]:
    f = _cache_file(cache_dir, figi)
    if not f.exists():
        return []
    try:
        return [(d, float(c)) for d, c in json.loads(f.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001
        return []


def fetch_candles(client, figi: str, cache_dir: Path, days: int = 400, by_uid: bool = False) -> list[tuple[str, float]]:
    """Дневные закрытия [(YYYY-MM-DD, close)], из кэша + догрузка хвоста. Ошибки API → то, что есть в кэше."""
    from tinkoff.invest.schemas import CandleInterval
    from tinkoff.invest.utils import quotation_to_decimal
    cached = load_cached(cache_dir, figi)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    if cached:
        last = datetime.strptime(cached[-1][0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start = max(start, last - timedelta(days=3))     # перекрытие, чтобы обновить незакрытую свечу
    out = {d: c for d, c in cached}
    try:
        # API отдаёт не больше года дневных свечей за запрос — режем по годам
        cur = start
        while cur < now:
            chunk_to = min(cur + timedelta(days=365), now)
            kw = {"instrument_id": figi} if by_uid else {"figi": figi}
            resp = client.market_data.get_candles(from_=cur, to=chunk_to, interval=CandleInterval.CANDLE_INTERVAL_DAY, **kw)
            for c in resp.candles:
                out[c.time.strftime("%Y-%m-%d")] = float(quotation_to_decimal(c.close))
            cur = chunk_to
    except Exception:  # noqa: BLE001
        pass
    series = sorted(out.items())
    cutoff = (now - timedelta(days=days + 10)).strftime("%Y-%m-%d")
    series = [(d, c) for d, c in series if d >= cutoff]
    if series:
        _cache_file(cache_dir, figi).write_text(json.dumps(series), encoding="utf-8")
    return series


# ---------------------------------------------------------------------------
# Доходности по периодам
# ---------------------------------------------------------------------------

def close_before(series: list[tuple[str, float]], days_ago: int) -> float | None:
    """Последнее закрытие не позже (сегодня − days_ago)."""
    if not series:
        return None
    target = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    best = None
    for d, c in series:
        if d <= target:
            best = c
        else:
            break
    return best


def cagr(series: list[tuple[str, float]], cur_price: float, years: float) -> tuple[float | None, float]:
    """Среднегодовой рост цены за последние `years` лет: (%, фактически покрытые годы).
    Если истории меньше, считаем по тому, что есть, и возвращаем реальную длину — её видно в подписи."""
    if not series or cur_price <= 0:
        return None, 0.0
    target = (date.today() - timedelta(days=int(years * 365))).strftime("%Y-%m-%d")
    base = None
    for d, c in series:
        if d <= target:
            base = (d, c)
        else:
            break
    if base is None:
        base = series[0]                      # истории меньше запрошенного — берём самую раннюю точку
    d0 = datetime.strptime(base[0], "%Y-%m-%d").date()
    covered = max((date.today() - d0).days / 365, 0.0)
    if covered < 0.5 or base[1] <= 0:
        return None, covered
    return ((cur_price / base[1]) ** (1 / covered) - 1) * 100, covered


def period_changes(series: list[tuple[str, float]], cur_price: float) -> dict[str, float | None]:
    """% изменения текущей цены к закрытию N дней назад для каждого периода."""
    res: dict[str, float | None] = {}
    for key, _, days in PERIODS:
        base = close_before(series, days)
        res[key] = ((cur_price / base - 1) * 100) if (base and cur_price) else None
    return res


# ---------------------------------------------------------------------------
# История стоимости портфеля
# ---------------------------------------------------------------------------

def backfilled_history(positions, candles: dict[str, list[tuple[str, float]]], fx: dict[str, float], days: int = 365):
    """Текущий состав × исторические цены. Позиции без свечей (заблокированные, металлы без истории) — по текущей цене."""
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    dates = sorted({d for s in candles.values() for d, _ in s if d >= start})
    if not dates:
        return []
    last_close: dict[str, float | None] = {}
    idx: dict[str, int] = {}
    out = []
    for d in dates:
        total = 0.0
        for p in positions:
            qty = float(p.quantity); rate = fx.get(p.currency, 1.0)
            s = candles.get(p.figi) or []
            if s:
                i = idx.get(p.figi, 0)
                while i < len(s) and s[i][0] <= d:
                    last_close[p.figi] = s[i][1]; i += 1
                idx[p.figi] = i
                price = last_close.get(p.figi)
                total += qty * (price if price is not None else float(p.cur_price)) * rate
            else:
                total += float(p.value_rub)
        out.append((d, total))
    return out


def snapshot_history(snap_dir: Path) -> list[tuple[str, float]]:
    """Факт: суммарная стоимость из сохранённых снапшотов, по датам файлов."""
    out = []
    for f in sorted(snap_dir.glob("*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
            total = sum(float(r.get("value_rub", 0)) for r in rows)
            out.append((f.stem[:10], total))
        except Exception:  # noqa: BLE001
            continue
    return out
