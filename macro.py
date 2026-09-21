"""
macro.py — макроданные с сайта ЦБ РФ: годовая инфляция и ключевая ставка.

Источник — таблица https://www.cbr.ru/hd_base/infl/ (данные Росстата и Банка России, помесячно).
Публичного JSON у ЦБ для этого ряда нет, поэтому разбираем HTML-таблицу: строки вида
  <td>08.2026</td><td>14,00</td><td>6,33</td><td>4,00</td>
Разбор терпимый к вёрстке: ищем дату ММ.ГГГГ и три числа после неё в той же строке.
"""

from __future__ import annotations

import re
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CBR_URL = "https://www.cbr.ru/hd_base/infl/"


@dataclass
class Macro:
    period: str          # "08.2026"
    key_rate: float      # % годовых
    inflation: float     # % г/г
    target: float        # цель ЦБ, %


def fetch_cbr_macro(ca_bundle: Path | None = None, timeout: int = 15) -> Macro:
    ctx = ssl.create_default_context(cafile=str(ca_bundle)) if ca_bundle and ca_bundle.exists() else None
    req = urllib.request.Request(CBR_URL, headers={"User-Agent": "Mozilla/5.0 PortfolioCheck"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        html = resp.read().decode("utf-8", "replace")
    return parse_cbr_table(html)


def parse_cbr_table(html: str) -> Macro:
    # Убираем теги внутри строк таблицы, чтобы дата и числа шли подряд
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    num = r"(\d+[,.]\d+)"
    for row in rows:
        text = re.sub(r"<[^>]+>", " ", row)
        m = re.search(r"\b(\d{2}\.\d{4})\b\s+" + num + r"\s+" + num + r"\s+" + num, text)
        if m:
            f = lambda s: float(s.replace(",", "."))
            return Macro(m.group(1), f(m.group(2)), f(m.group(3)), f(m.group(4)))
    raise ValueError("Таблица инфляции на странице ЦБ не найдена — вёрстка изменилась")
