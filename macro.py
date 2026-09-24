"""
macro.py — макроданные с сайта ЦБ РФ: годовая инфляция и ключевая ставка; календарь решений по ставке (BACKLOG п. 25).

Источник — таблица https://www.cbr.ru/hd_base/infl/ (данные Росстата и Банка России, помесячно).
Публичного JSON у ЦБ для этого ряда нет, поэтому разбираем HTML-таблицу: строки вида
  <td>08.2026</td><td>14,00</td><td>6,33</td><td>4,00</td>
Разбор терпимый к вёрстке: ищем дату ММ.ГГГГ и три числа после неё в той же строке.

Календарь — https://www.cbr.ru/dkp/cal_mp/ (API нет, только HTML; вёрстка проверена вживую 24.09.2026):
  <div class="main-events_day"><div class="date …">23&nbsp;октября 2026 года</div> … «Заседание Совета директоров
  Банка России по ключевой ставке» / «Резюме обсуждения ключевой ставки» …
Кэш — cache/macro_calendar.json на неделю (график публикуется на год вперёд и меняется редко).
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from datetime import date
from dataclasses import asdict, dataclass
from pathlib import Path

CBR_URL = "https://www.cbr.ru/hd_base/infl/"
CAL_URL = "https://www.cbr.ru/dkp/cal_mp/"
CAL_FILE = "macro_calendar.json"
CAL_MAX_AGE_S = 7 * 24 * 3600
MONTHS = {m: i for i, m in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                                      "сентября", "октября", "ноября", "декабря"), 1)}
MEETING, SUMMARY = "meeting", "summary"
KIND_LABEL = {MEETING: "Заседание ЦБ по ключевой ставке", SUMMARY: "Резюме обсуждения ключевой ставки"}


@dataclass
class Macro:
    period: str          # "08.2026"
    key_rate: float      # % годовых
    inflation: float     # % г/г
    target: float        # цель ЦБ, %


def fetch_cbr_macro(timeout: int = 15) -> Macro:
    """Сайт ЦБ — с корнями ОС (net.fetch), как остальные сайты; бандл T-Invest здесь не нужен."""
    import net
    return parse_cbr_table(net.fetch(CBR_URL, timeout=timeout).decode("utf-8", "replace"))


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


@dataclass
class MacroEvent:
    date: str            # ISO
    kind: str            # MEETING | SUMMARY
    title: str           # как на сайте ЦБ, без лишних пробелов
    forecast: bool = False   # к заседанию публикуется среднесрочный прогноз ЦБ


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_cbr_calendar(page: str) -> list[MacroEvent]:
    """HTML «Календаря решений по ключевой ставке» → события по дате (все годы на странице)."""
    out = []
    for block in re.split(r'<div class="main-events_day"', page)[1:]:
        m = re.search(r'class="date[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not m:
            continue
        d = re.match(r"(\d{1,2}) (\S+) (\d{4})", _text(m.group(1)))
        if not d or d.group(2) not in MONTHS:
            continue
        day = date(int(d.group(3)), MONTHS[d.group(2)], int(d.group(1))).isoformat()
        body = _text(block[m.end():])
        if "Заседание Совета директоров" in body:
            out.append(MacroEvent(day, MEETING, "Заседание Совета директоров Банка России по ключевой ставке",
                                  "Среднесрочный прогноз" in body))
        elif "Резюме обсуждения" in body:
            out.append(MacroEvent(day, SUMMARY, "Резюме обсуждения ключевой ставки"))
    if not out:
        raise ValueError("Календарь на странице ЦБ не найден — вёрстка изменилась")
    return sorted(out, key=lambda e: e.date)


def load_calendar(cache_dir: Path) -> list[MacroEvent]:
    try:
        return [MacroEvent(**x) for x in json.loads((cache_dir / CAL_FILE).read_text(encoding="utf-8"))["events"]]
    except Exception:  # noqa: BLE001
        return []


def calendar_fresh(cache_dir: Path) -> bool:
    f = cache_dir / CAL_FILE
    return f.exists() and time.time() - f.stat().st_mtime < CAL_MAX_AGE_S


def save_calendar(events: list[MacroEvent], cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / CAL_FILE).write_text(json.dumps({"events": [asdict(e) for e in events]}, ensure_ascii=False),
                                      encoding="utf-8")


def fetch_calendar(cache_dir: Path, fetch=None) -> str:
    """Догрузить календарь, если кэш старше недели (только из воркера). → "ok" | текст ошибки."""
    if calendar_fresh(cache_dir):
        return "ok"
    if fetch is None:
        import net
        fetch = net.fetch                      # корни ОС
    try:
        save_calendar(parse_cbr_calendar(fetch(CAL_URL).decode("utf-8", "replace")), cache_dir)
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"


def upcoming(events: list[MacroEvent], today: date, days: int = 190) -> list[MacroEvent]:
    return [e for e in events if 0 <= (date.fromisoformat(e.date) - today).days <= days]
