"""
consensus.py — консенсус-прогнозы аналитиков по бумаге (BACKLOG п. 26, аналог ANR у Bloomberg), справочно.

Источник — T-Invest InstrumentsService.GetForecastBy (по instrument uid): консенсус (рекомендация, целевая цена,
разброс целей) и прогнозы отдельных домов. Это чужие мнения, не рекомендация программы (правило 3 проекта):
в карточке — отдельным блоком с подписью «справочно». Поля читаются защитно (getattr): формат ответа проверен
по документации, а не на живом API; при любом расхождении блок просто не показывается.
Кэш — cache/forecast_<uid>.json на сутки: карточка не ходит в сеть при каждом открытии.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_AGE_S = 24 * 3600
# Рейтинги домов — нейтральными словами: программа не произносит «купи/продай» даже чужими устами (правило 3)
RECOMMENDATION = {1: "позитивный взгляд", 2: "нейтральный взгляд", 3: "негативный взгляд",
                  "RECOMMENDATION_BUY": "позитивный взгляд", "RECOMMENDATION_HOLD": "нейтральный взгляд",
                  "RECOMMENDATION_SELL": "негативный взгляд"}


def _money(v) -> float | None:
    """Quotation/MoneyValue (units + nano) или число → float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    units, nano = getattr(v, "units", None), getattr(v, "nano", 0)
    return None if units is None else float(units) + float(nano or 0) / 1e9


def _rec(v) -> str:
    key = getattr(v, "name", v)
    try:
        return RECOMMENDATION.get(key) or RECOMMENDATION.get(int(v), "") or str(key)
    except (TypeError, ValueError):
        return str(key)


def parse(resp) -> dict | None:
    """Ответ GetForecastBy → {consensus: {...}, targets: [...]}; None, если консенсуса нет."""
    c = getattr(resp, "consensus", None)
    if c is None:
        return None
    cons = {"recommendation": _rec(getattr(c, "recommendation", "")), "current": _money(getattr(c, "current_price", None)),
            "target": _money(getattr(c, "consensus", None)), "min": _money(getattr(c, "min_target", None)),
            "max": _money(getattr(c, "max_target", None)), "change_rel": _money(getattr(c, "price_change_rel", None)),
            "currency": getattr(c, "currency", "")}
    if cons["target"] is None:
        return None
    targets = []
    for t in getattr(resp, "targets", None) or []:
        d = getattr(t, "recommendation_date", None)
        targets.append({"company": getattr(t, "company", "") or getattr(t, "show_name", ""),
                        "recommendation": _rec(getattr(t, "recommendation", "")),
                        "target": _money(getattr(t, "target_price", None)),
                        "date": d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else ""})
    return {"consensus": cons, "targets": targets}


def cache_path(cache_dir: Path, uid: str) -> Path:
    return cache_dir / f"forecast_{uid}.json"


def load(cache_dir: Path, uid: str, max_age_s: int = MAX_AGE_S) -> dict | None:
    f = cache_path(cache_dir, uid)
    try:
        if time.time() - f.stat().st_mtime > max_age_s:
            return None
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def fetch(token: str, uid: str, cache_dir: Path) -> dict | None:
    """Запрос к T-Invest (только из воркера). Пустой консенсус тоже кэшируется — чтобы не спрашивать каждый раз."""
    import portfolio_check as pc
    Client, *_ = pc._sdk(); pc._setup_grpc_roots()
    with Client(token) as client:
        resp = client.instruments.get_forecast_by(instrument_id=uid)
    data = parse(resp) or {"consensus": None, "targets": []}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, uid).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
