"""
demo.py — режим `--demo`: приложение на вымышленном портфеле (`demo_snapshot.json`) без токена и сети.

Для скриншотов README и осмотра интерфейса. Все данные — во временной папке, которая удаляется при выходе;
реальный конфиг, снапшоты и Keychain пользователя не читаются и не меняются, размеры окна не сохраняются.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import paths
import secrets_store as SEC

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))   # в собранном .app — ресурсы бандла
SNAPSHOT = ROOT / "demo_snapshot.json"
PRESET = "dividend_defensive"


def setup(base: Path | None = None) -> Path:
    """Готовит временную папку данных и переключает на неё приложение. Возвращает путь к демо-конфигу.

    Вызывать до первого обращения к paths.data_dir() и до чтения секретов.
    """
    import config_migrate as CM
    import portfolio_check as pc
    import strategy as ST
    import yaml

    d = Path(base) if base else Path(tempfile.mkdtemp(prefix="PortfolioCheck-demo-"))
    paths.DEMO_DIR = d
    SEC.DISABLED = True                           # Keychain не читаем и не пишем: секреты — только на сессию
    (d / "cache").mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SNAPSHOT, d / "snapshots" / f"{datetime.now():%Y-%m-%d}.json")

    tpl = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    cfg = ST.apply_preset(tpl, PRESET)
    positions = pc.load_snapshot(str(SNAPSHOT))
    cfg["instruments"] = {p.ticker: p.cls for p in positions if p.cls and p.cls != "unclassified"}
    cfg["setup_done"] = True
    cfg["refresh"] = {**(cfg.get("refresh") or {}), "on_launch": False, "privacy_on_launch": False}
    cfg[CM.KEY] = CM.CURRENT_VERSION
    path = d / "config.yaml"
    pc.save_config(cfg, str(path))
    fill_caches(d, positions)
    return path


# ---------------------------------------------------------------------------
# Демо-кэши: всё вымышленное и детерминированное (одинаковые скриншоты при каждом запуске)
# ---------------------------------------------------------------------------

# годовой дрейф цены по классам, % — чтобы переоценка и CAGR выглядели правдоподобно, а не случайно
DRIFT = {"gold": 14, "equity_core": 9, "real_estate_wh": 6, "real_estate_other": 3, "equity_spec": 18,
         "ballast": -6, "cash": 4}
VOL = {"gold": 0.009, "cash": 0.006, "real_estate_wh": 0.004, "real_estate_other": 0.005}

NEWS = [  # (часов назад, тикеры, заголовок) — выдуманные формулировки, не пересказ реальных событий
    (1, ["SBERP"], "набсовет банка рекомендовал дивиденды за год, отсечка — через шесть недель"),
    (3, [], "ЦБ сохранил ключевую ставку и смягчил риторику по инфляции"),
    (5, ["LKOH"], "нефтяная компания отчиталась о росте свободного денежного потока"),
    (8, ["MTSS"], "оператор связи увеличил долговую нагрузку ради инвестпрограммы"),
    (11, ["PHOR"], "производитель удобрений нарастил экспорт на новые рынки"),
    (14, ["TRNFP"], "тарифы на прокачку нефти проиндексируют с нового года"),
    (20, ["MOEX"], "объём торгов на бирже вырос на фоне притока частных инвесторов"),
    (26, [], "инфляционные ожидания населения снизились второй месяц подряд"),
    (31, ["RU000A0DEMO1"], "складской фонд подписал договор аренды нового комплекса"),
    (39, ["YDEX"], "технологическая компания представила обновлённого ИИ-ассистента"),
    (47, ["GLDRUB_TOM"], "цена золота обновила максимум на фоне спроса центробанков"),
    (55, ["AFLT"], "авиаперевозчик снизил прогноз пассажиропотока"),
    (62, ["LKOH", "TRNFP"], "экспорт нефти через порты вернулся к прошлогодним уровням"),
    (70, [], "курс доллара стабилизировался после налогового периода"),
]

# Кандидаты скринера: реальные тикеры и названия Мосбиржи — чтобы к ним привязывались настоящие новости (кнопка
# «Обновить» у ленты работает и без токена) и ИИ оценивал существующие компании. Числа — условные, для вида экрана,
# не данные T-Invest: без токена настоящего фундаментала в демо нет.
# тикер, название, отрасль, цена, кап. млрд, дивдох. %, P/E, долг/EBITDA, ROE, FF, средняя 5 лет, мес. %
IDEAS = [
    ("MRKP", "Россети Центр и Приволжье", "Электроэнергетика", 0.5, 57, 13.5, 3.2, 0.9, 19, 25, 10.2, 1.1),
    ("SNGSP", "Сургутнефтегаз - привилегированные акции", "Нефть и газ", 52, 400, 12.6, 3.0, None, 9, 60, 11.8, -1.5),
    ("TATNP", "Татнефть - привилегированные акции", "Нефть и газ", 610, 1400, 11.9, 5.8, 0.2, 22, 30, 10.4, -0.8),
    ("MSNG", "Мосэнерго", "Электроэнергетика", 2.9, 115, 10.8, 4.5, 0.1, 11, 20, 8.6, 0.6),
    ("SIBN", "Газпром нефть", "Нефть и газ", 590, 2800, 12.2, 4.9, 1.0, 20, 5, 10.9, -2.2),
    ("BELU", "НоваБев Групп", "Потребительский сектор", 470, 75, 9.4, 8.1, 1.6, 21, 25, 7.9, 3.4),
    ("NMTP", "НМТП", "Промышленность", 7.1, 135, 9.8, 6.4, 0.5, 23, 20, 8.4, -1.9),
    ("IRAO", "Интер РАО", "Электроэнергетика", 3.4, 355, 8.6, 3.1, None, 12, 30, 6.8, 1.4),
    ("CHMF", "Северсталь", "Сырьё и материалы", 1150, 960, 9.1, 7.7, 0.3, 28, 22, 11.6, -3.7),
    ("MGNT", "Магнит", "Потребительский сектор", 4200, 430, 8.2, 8.8, 2.4, 25, 35, 8.3, 2.0),
]


def _candles(ticker: str, cls: str, last: float, years: int = 5) -> list[tuple[str, float]]:
    """Дневные закрытия по рабочим дням: случайное блуждание с дрейфом класса, последнее значение = цена в снапшоте."""
    import math
    import random
    from datetime import date, timedelta
    rnd = random.Random(ticker)                    # детерминированно: один и тот же ряд при каждом запуске
    drift = DRIFT.get(cls, 8) / 100 / 252
    vol = VOL.get(cls, 0.016)
    days, cur = [], date.today() - timedelta(days=365 * years)
    while cur <= date.today():
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    x, xs = 0.0, []
    for _ in days:
        x += drift + rnd.gauss(0, vol)
        xs.append(x)
    k = math.log(last) - xs[-1]
    return [(d.isoformat(), round(math.exp(v + k), 4)) for d, v in zip(days, xs)]


def fill_caches(d: Path, positions) -> None:
    """Свечи позиций, новости, кэш скринера и помесячные снапшоты — экраны демо не пустые и без сети."""
    import json
    from dataclasses import asdict
    from datetime import date, timedelta
    import news as NEWS_MOD
    import screener as SCR

    cache = d / "cache"
    series = {}
    for p in positions:
        if p.figi:
            series[p.figi] = _candles(p.ticker, p.cls, float(p.cur_price))
            (cache / f"candles_{p.figi}.json").write_text(json.dumps(series[p.figi]), encoding="utf-8")

    now = datetime.now()
    items = [NEWS_MOD.NewsItem(title, f"https://example.com/demo/{i}", "демо", now - timedelta(hours=h), tickers)
             for i, (h, tickers, title) in enumerate(NEWS)]
    NEWS_MOD.save_cache(items, cache)

    ideas = []
    for n, (t, name, sector, price, cap, dy, pe, debt, roe, ff, avg5, mon) in enumerate(IDEAS):
        ex = (date.today() + timedelta(days=9 + 11 * n)).isoformat()
        ideas.append(SCR.Idea(t, name, sector, price, cap, dy, round(price * dy / 100, 4), pe, debt, roe, ff, ex,
                              avg5, None, f"demo-{t}", mon))
    for i in ideas:                                   # свечи кандидатов — для «Мес.» и графика в карточке
        (cache / f"candles_{i.uid}.json").write_text(json.dumps(_candles(i.ticker, "equity_core", i.price, years=1)),
                                                     encoding="utf-8")
    (cache / "screener.json").write_text(json.dumps({
        "at": now.isoformat(), "source": "tinvest2", "ideas": [asdict(i) for i in ideas],
        "stats": {"universe": 250, "checked": len(ideas), "with_cap": len(ideas), "errors": 0}}, ensure_ascii=False),
        encoding="utf-8")

    # календарь выплат: прошлые выплаты (одна-две в год по бумаге) и несколько объявленных на ближайшие недели;
    # «ожидается» достроит payouts.with_expected — как для настоящих данных
    import payouts as PO
    import random
    today = date.today()
    raw = []
    declared = {"SBERP": 24, "PHOR": 9, "TRNFP": 47}          # тикер → через сколько дней отсечка
    for p in positions:
        per_year = float(p.div_12m)
        if per_year <= 0:
            continue
        rnd = random.Random("div" + p.ticker)
        n = 2 if p.ticker in ("LKOH", "PHOR", "MTSS") else 1
        for k in range(n):
            rec = today - timedelta(days=rnd.randint(30, 330) if n == 1 else 60 + 170 * k + rnd.randint(0, 40))
            raw.append(PO.Payout(p.ticker, p.name, rec.isoformat(), (rec - timedelta(days=1)).isoformat(),
                                 (rec + timedelta(days=14)).isoformat(), round(per_year / n, 4), p.currency,
                                 float(p.quantity), round(per_year / n * float(p.quantity), 2), "paid"))
        if p.ticker in declared:
            rec = today + timedelta(days=declared[p.ticker])
            raw.append(PO.Payout(p.ticker, p.name, rec.isoformat(), (rec - timedelta(days=1)).isoformat(),
                                 (rec + timedelta(days=12)).isoformat(), round(per_year / n * 1.05, 4), p.currency,
                                 float(p.quantity), round(per_year / n * 1.05 * float(p.quantity), 2), "declared"))
    PO.save(PO.with_expected(raw, today), cache)

    # история стоимости: снапшот на первое число последних 12 месяцев — текущий состав по ценам тех дней,
    # масштабированный под пополнения (раньше позиций было меньше)
    rows = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for m in range(12, 0, -1):
        day = (date.today().replace(day=1) - timedelta(days=30 * (m - 1))).replace(day=1)
        scale = 1 - 0.035 * m                        # ≈ пополнения 3,5% капитала в месяц
        snap = []
        for r in rows:
            s = series.get(r.get("figi", ""))
            price = next((c for dd, c in reversed(s) if dd <= day.isoformat()), float(r["cur_price"])) if s else float(r["cur_price"])
            snap.append({**r, "value_rub": str(round(float(r["quantity"]) * scale * price, 2))})
        f = d / "snapshots" / f"{day:%Y-%m-%d}.json"
        if f.exists():                               # 1-е число сегодня — текущий снапшот не перезаписываем
            continue
        f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")


def cleanup() -> None:
    d = paths.DEMO_DIR
    if d is not None and d.name.startswith("PortfolioCheck-demo-"):
        shutil.rmtree(d, ignore_errors=True)
