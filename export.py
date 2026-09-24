"""
export.py — выгрузка таблиц и истории стоимости в CSV для Excel/Numbers/LibreOffice (BACKLOG п. 24).

Формат — под русский Excel: разделитель «;», десятичная запятая, UTF-8 с BOM (иначе Excel путает кодировку).
Числа из ячеек приводятся к виду 1234,56 (без разделителей тысяч, знаки % и ₽ — в заголовок не переносятся, а
остаются только у текстовых значений). Скрытые «глазиком» суммы выгружаются как есть — «•••»: что видно, то и
выгружено; для выгрузки сумм режим нужно выключить (об этом предупреждает окно).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

NUM = re.compile(r"^[+\-−]?[\d,  ]*\d(\.\d+)?\s*(%|₽)?$")


def normalize(cell: str) -> str:
    """«1,234.56» → «1234,56»; «+12.5%» → «12,5»; текст — как есть."""
    s = (cell or "").strip()
    if not s or not NUM.match(s):
        return s
    s = s.replace("−", "-").replace("%", "").replace("₽", "").replace(",", "").replace(" ", "").replace(" ", "")
    s = s.lstrip("+")
    return s.replace(".", ",")


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    path = Path(path)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        for r in rows:
            w.writerow([normalize(c) for c in r])
    return path


def table_rows(table) -> tuple[list[str], list[list[str]]]:
    """Заголовки и строки QTableWidget в текущем порядке сортировки — как их видит пользователь."""
    headers = [table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else "" for c in range(table.columnCount())]
    rows = []
    for r in range(table.rowCount()):
        rows.append([table.item(r, c).text() if table.item(r, c) else "" for c in range(table.columnCount())])
    return headers, rows


def history_rows(snapshots: list[tuple[str, float]], backfilled: list[tuple[str, float]]) -> tuple[list[str], list[list[str]]]:
    """История стоимости: дата, факт по снапшотам, оценка «сегодняшний состав по ценам прошлых дней»."""
    s, b = dict(snapshots), dict(backfilled)
    rows = [[d, f"{s[d]:.2f}" if d in s else "", f"{b[d]:.2f}" if d in b else ""] for d in sorted(set(s) | set(b))]
    return ["Дата", "Стоимость по снапшотам, ₽", "Сегодняшний состав по ценам дня, ₽"], rows
