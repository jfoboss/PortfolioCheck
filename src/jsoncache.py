"""
jsoncache.py — JSON-кэши на диске: чтение с запасным значением, атомарная запись, свежесть по времени файла.

Раньше каждый модуль (свечи, факторы, календарь ЦБ, консенсус, выплаты, новости, оповещения, ответы ИИ) писал
и читал сам: где-то без try (битый news.json ронял загрузку), где-то без mkdir; запись была не атомарной —
программа, упавшая посреди записи, оставляла обрезанный файл. Здесь — одна реализация.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def read(path: Path, default=None):
    """Содержимое JSON или default, если файла нет или он битый."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла, битый JSON, нет доступа
        return default


def write(path: Path, obj, indent: int | None = None) -> None:
    """Атомарно: во временный файл рядом, затем замена — читатель видит либо старый, либо новый файл целиком."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def fresh(path: Path, max_age_s: float) -> bool:
    """Файл есть и моложе max_age_s секунд."""
    try:
        return time.time() - Path(path).stat().st_mtime < max_age_s
    except OSError:
        return False
