"""
factors.py — рыночные факторы для сценариев (BACKLOG п. 23): индекс Мосбиржи и курс доллара.

  IMOEX  — MOEX ISS, история индекса: /iss/history/engines/stock/markets/index/securities/IMOEX.json (без токена;
           эндпоинт истории индекса работает, в отличие от убранного /securities/{t}/dividends);
  USDRUB — официальный курс ЦБ: /scripts/XML_dynamic.asp (без токена).
Ряды кэшируются как свечи — cache/candles_IMOEX.json, cache/candles_USDRUB.json — и догружаются не чаще раза в сутки.
Сеть — только из воркера (FactorsWorker). Сайты — через net.fetch (корни ОС).
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
import style as SY
import jsoncache as JC

IMOEX, USDRUB = "IMOEX", "USDRUB"
ISS_URL = ("https://iss.moex.com/iss/history/engines/stock/markets/index/securities/IMOEX.json"
           "?from={frm}&till={till}&start={start}&iss.meta=off&history.columns=TRADEDATE,CLOSE")
CBR_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={frm}&date_req2={till}&VAL_NM_RQ=R01235"
MAX_AGE_S = 20 * 3600


def parse_iss(payload: bytes) -> list[tuple[str, float]]:
    """Ответ ISS (JSON, iss.meta=off): {"history": {"columns": [...], "data": [[дата, close], ...]}}."""
    h = json.loads(payload.decode("utf-8"))["history"]
    cols = [c.upper() for c in h["columns"]]
    i_d, i_c = cols.index("TRADEDATE"), cols.index("CLOSE")
    return [(row[i_d], float(row[i_c])) for row in h["data"] if row[i_c] is not None]


RECORD = re.compile(r'<Record Date="(\d{2})\.(\d{2})\.(\d{4})"[^>]*>.*?<Nominal>(\d+)</Nominal>.*?<Value>([\d,.]+)</Value>',
                    re.S)


def parse_cbr(payload: bytes) -> list[tuple[str, float]]:
    """XML ЦБ (windows-1251): <Record Date="02.09.2026"><Nominal>1</Nominal><Value>84,0700</Value>…
    Курс — за одну единицу валюты (Value / Nominal)."""
    text = payload.decode("windows-1251", "replace")
    out = [(f"{y}-{m}-{d}", float(value.replace(",", ".")) / int(nominal))
           for d, m, y, nominal, value in RECORD.findall(text)]
    return sorted(out)


def load(cache_dir: Path, key: str) -> list[tuple[str, float]]:
    """Ряд фактора — в том же формате, что свечи бумаг (history.load_cached)."""
    import history as H
    return H.load_cached(cache_dir, key)


def fresh(cache_dir: Path, key: str) -> bool:
    return JC.fresh(cache_dir / f"candles_{key}.json", MAX_AGE_S)


def fetch_all(cache_dir: Path, days: int = 400, fetch=None, today: date | None = None) -> dict[str, str]:
    """Догрузить оба ряда (если кэш старше суток). fetch(url) → bytes; по умолчанию net.fetch (корни ОС).
    Возвращает {ключ: "ok" | текст ошибки} — ошибка одного ряда не мешает другому."""
    if fetch is None:
        import net
        fetch = net.fetch
    import history as H
    today = today or date.today()
    frm = today - timedelta(days=days)
    status = {}
    if not fresh(cache_dir, IMOEX):
        try:
            rows, start = [], 0
            while True:                                  # ISS отдаёт историю страницами по 100 строк
                page = parse_iss(fetch(ISS_URL.format(frm=frm.isoformat(), till=today.isoformat(), start=start)))
                rows += page
                if len(page) < 100:
                    break
                start += len(page)
            if rows:
                H.save_series(cache_dir, IMOEX, sorted(dict(rows).items()))
            status[IMOEX] = "ok" if rows else "пустой ответ"
        except Exception as e:  # noqa: BLE001
            status[IMOEX] = SY.err_text(e, 120)
    if not fresh(cache_dir, USDRUB):
        try:
            rows = parse_cbr(fetch(CBR_URL.format(frm=f"{frm:%d/%m/%Y}", till=f"{today:%d/%m/%Y}")))
            if rows:
                H.save_series(cache_dir, USDRUB, rows)
            status[USDRUB] = "ok" if rows else "пустой ответ"
        except Exception as e:  # noqa: BLE001
            status[USDRUB] = SY.err_text(e, 120)
    return status
