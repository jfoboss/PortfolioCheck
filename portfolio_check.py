#!/usr/bin/env python3
"""
portfolio_check.py — проверка портфеля Т-Инвестиций против собственных правил.

Что делает:
  1. Забирает портфель через Tinkoff Invest API (позиции, средние, текущие цены).
  2. Классифицирует инструменты по config.yaml (золото / ЗПИФ склады / ЗПИФ прочее / ядро / спекуляции / балласт).
  3. Считает доли классов против целевых коридоров.
  4. Прогоняет правила: просадки-кандидаты на добор, заморозки, лимиты позиций, концентрация, хвост.
  5. Печатает markdown-отчёт (stdout) — удобно кидать в чат или в заметки.

Запуск:
  python portfolio_check.py [--config ...] [--json snapshot.json]
  Токен берётся из Keychain (задан в приложении) или из TINKOFF_TOKEN.
  Конфиг по умолчанию — ~/Library/Application Support/PortfolioCheck/config.yaml (его пишет приложение).

Зависимости: ./setup_env.sh (SDK ставится из GitHub — с PyPI он удалён)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml
import logging
_log = logging.getLogger("portfolio.core")


def _bundle_path() -> Path | None:
    candidates = []
    try:
        import paths
        candidates.append(paths.data_dir() / "ca-bundle.pem")
    except Exception:  # noqa: BLE001
        pass
    try:
        import paths
        candidates.append(paths.LOCAL_DIR / "ca-bundle.pem")
    except Exception:  # noqa: BLE001
        pass
    candidates.append(Path(__file__).with_name("ca-bundle.pem"))
    for p in candidates:
        if p.exists():
            return p
    return None


def _setup_grpc_roots() -> None:
    """Эндпоинт T-Invest API подписан корнем Минцифры (Russian Trusted Root CA), которого нет
    в roots.pem у grpcio. SDK создаёт канал через grpc.ssl_channel_credentials() без аргументов,
    а переменную GRPC_DEFAULT_SSL_ROOTS_FILE_PATH grpc на macOS читает ненадёжно — поэтому
    подменяем дефолт: если root_certificates не переданы, подставляем наш ca-bundle.pem."""
    p = _bundle_path()
    if p is None:
        return
    import grpc
    if getattr(grpc, "_portfolio_check_patched", False):
        return
    roots = p.read_bytes()
    original = grpc.ssl_channel_credentials

    def patched(root_certificates=None, private_key=None, certificate_chain=None):
        return original(root_certificates or roots, private_key, certificate_chain)

    grpc.ssl_channel_credentials = patched
    grpc._portfolio_check_patched = True
    os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", str(p))


def connect(token: str):
    """Клиент T-Invest с нашим бандлом (корень Минцифры): `with pc.connect(token) as client: …`. Только из воркеров."""
    _setup_grpc_roots()
    Client, *_ = _sdk()
    return Client(token)


def _sdk():
    """Ленивый импорт SDK — чтобы модуль (модель, правила, рендер) импортировался и без него."""
    try:
        from tinkoff.invest import Client, InstrumentIdType
        from tinkoff.invest.utils import quotation_to_decimal, money_to_decimal
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Нужен SDK: запусти ./setup_env.sh (ставит invest-python из GitHub)") from e
    return Client, InstrumentIdType, quotation_to_decimal, money_to_decimal


# ---------------------------------------------------------------------------
# Модель данных
# ---------------------------------------------------------------------------

@dataclass
class Position:
    ticker: str
    name: str
    instrument_type: str      # share / etf / bond / currency / ...
    currency: str             # rub / usd / hkd / ...
    quantity: Decimal
    avg_price: Decimal        # в валюте инструмента
    cur_price: Decimal        # в валюте инструмента
    value_rub: Decimal        # текущая стоимость в рублях
    pnl_pct: Decimal          # (cur/avg - 1) * 100, в валюте инструмента
    cls: str                  # класс из конфига или "unclassified"
    isin: str = ""
    figi: str = ""
    sector: str = ""
    blocked: bool = False       # заблокирована (API) или цена у брокера нулевая
    ref_ticker: str = ""        # эталон: чья цена показывает «сколько это стоит на самом деле»
    fair_price: Decimal = Decimal(0)   # цена эталона в ₽ за штуку (0 — не найдена)
    otc_price: Decimal = Decimal(0)    # внебиржевая цена продажи у брокера, ₽ за штуку (бид стакана или дилерская)
    otc_source: str = ""               # "бид" | "дилер" | ""
    div_12m: Decimal = Decimal(0)      # выплаты за 12 мес на одну бумагу, в валюте инструмента, после НДФЛ


# ---------------------------------------------------------------------------
# Загрузка данных
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg: dict, path: str) -> None:
    """Пишет конфиг целиком (источник правды — UI приложения)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Файл генерируется приложением PortfolioCheck (Настройки). Правки руками будут перезаписаны.\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def pick_account(client, cfg_id: str) -> str:
    accounts = client.users.get_accounts().accounts
    if cfg_id:
        return cfg_id
    # Берём первый открытый брокерский счёт (не ИИС), если найдём; иначе первый.
    for a in accounts:
        if a.type == 1 and a.status == 2:   # ACCOUNT_TYPE_TINKOFF, ACCOUNT_STATUS_OPEN
            return a.id
    return accounts[0].id


def fetch_fx(client, fallback: dict[str, float]) -> dict[str, Decimal]:
    """Курсы валют к рублю. Пытаемся взять из API по известным FIGI, иначе fallback из конфига."""
    _, _, quotation_to_decimal, _ = _sdk()
    figis = {"USD": "BBG0013HGFT4", "HKD": "BBG0013HSW87", "EUR": "BBG0013HJJ31"}
    rates: dict[str, Decimal] = {k: Decimal(str(v)) for k, v in fallback.items()}
    try:
        resp = client.market_data.get_last_prices(figi=list(figis.values()))
        by_figi = {p.figi: quotation_to_decimal(p.price) for p in resp.last_prices}
        for cur, figi in figis.items():
            if figi in by_figi and by_figi[figi] > 0:
                rates[cur] = by_figi[figi]
    except Exception as e:  # noqa: BLE001
        _log.warning("курсы из API не получены (%s), использую fallback", e)
    rates["RUB"] = Decimal(1)
    return rates


def fetch_positions(client, account_id: str, cfg: dict) -> list[Position]:
    _, InstrumentIdType, quotation_to_decimal, money_to_decimal = _sdk()
    fx = fetch_fx(client, cfg.get("fx_fallback", {}))
    mapping: dict[str, str] = {str(k): v for k, v in cfg["instruments"].items()}
    pf = client.operations.get_portfolio(account_id=account_id)

    out: list[Position] = []
    for p in pf.positions:
        qty = quotation_to_decimal(p.quantity)
        if qty == 0:
            continue
        # Тикер и имя — отдельным запросом по FIGI (кэш не нужен: позиций ~40).
        try:
            ins = client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=p.figi
            ).instrument
            ticker, name, isin = ins.ticker, ins.name, ins.isin
        except Exception:  # noqa: BLE001
            ticker, name, isin = p.figi, p.figi, ""
        sector = ""
        try:  # отрасль есть только у акций и фондов
            if p.instrument_type == "share":
                sector = client.instruments.share_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=p.figi).instrument.sector
            elif p.instrument_type == "etf":
                sector = client.instruments.etf_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=p.figi).instrument.sector
        except Exception:  # noqa: BLE001
            pass

        cur = (p.current_price.currency or "rub").upper()
        avg = money_to_decimal(p.average_position_price)
        price = money_to_decimal(p.current_price)
        rate = fx.get(cur, Decimal(1))
        value_rub = qty * price * rate
        pnl = (price / avg - 1) * 100 if avg > 0 else Decimal(0)

        # Для ЗПИФ в API тикер часто = ISIN (RU000A...), для акций — обычный тикер.
        cls = mapping.get(ticker) or mapping.get(isin) or "unclassified"

        out.append(Position(
            ticker=ticker, name=name, instrument_type=p.instrument_type,
            currency=cur, quantity=qty, avg_price=avg, cur_price=price,
            value_rub=value_rub, pnl_pct=pnl, cls=cls, isin=isin, figi=p.figi, sector=sector,
            blocked=bool(getattr(p, "blocked", False)) or price == 0,
        ))
    apply_blocked_flags(out, cfg)
    _fill_fair_prices(client, out, cfg, fx)
    return out


def apply_blocked_flags(positions: list[Position], cfg: dict) -> None:
    """Заблокирована = флаг API, или нулевая цена, или отмечена пользователем в настройках,
    или известный заблокированный дубликат (AMZN-RM, TECH2, TEUR2, AGRO)."""
    user = {str(t) for t in (cfg.get("blocked_tickers") or [])}
    for p in positions:
        p.blocked = p.blocked or p.ticker in user or p.ticker in DEFAULT_REFERENCES


def _fill_otc_prices(client, positions: list[Position], fx: dict[str, Decimal]) -> None:
    """Внебиржевая цена продажи у брокера для заблокированных: лучший бид из стакана, иначе дилерская котировка."""
    from tinkoff.invest.schemas import LastPriceType
    _, _, quotation_to_decimal, _ = _sdk()
    for p in positions:
        if not p.blocked or not p.figi:
            continue
        rate = fx.get(p.currency, Decimal(1))
        p.otc_price, p.otc_source = Decimal(0), ""
        try:
            ob = client.market_data.get_order_book(figi=p.figi, depth=1)
            if ob.bids:
                p.otc_price, p.otc_source = quotation_to_decimal(ob.bids[0].price) * rate, "бид"
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            lp = client.market_data.get_last_prices(figi=[p.figi], last_price_type=LastPriceType.LAST_PRICE_DEALER).last_prices
            if lp and quotation_to_decimal(lp[0].price) > 0:
                p.otc_price, p.otc_source = quotation_to_decimal(lp[0].price) * rate, "дилер"
        except Exception:  # noqa: BLE001
            pass


def _fill_fair_prices(client, positions: list[Position], cfg: dict, fx: dict[str, Decimal]) -> None:
    """Для заблокированных позиций: цена эталона (реальная) и внебиржевая цена продажи."""
    _fill_otc_prices(client, positions, fx)
    _, InstrumentIdType, quotation_to_decimal, _ = _sdk()
    refs_cfg = {str(k): str(v) for k, v in (cfg.get("reference_tickers") or {}).items()}
    known = {p.ticker for p in positions}
    price_of_known = {p.ticker: (p.cur_price * fx.get(p.currency, Decimal(1))) for p in positions if p.cur_price > 0}
    for p in positions:
        if not p.blocked:
            continue
        ref = refs_cfg.get(p.ticker) or guess_reference(p.ticker, known)
        if not ref:
            continue
        p.ref_ticker = ref
        if ref in price_of_known:            # эталон уже в портфеле — цена известна без запросов
            p.fair_price = price_of_known[ref]
            continue
        try:
            found = client.instruments.find_instrument(query=ref).instruments
            hit = next((i for i in found if i.ticker.upper() == ref.upper()), None) or (found[0] if found else None)
            if hit is None:
                continue
            ins = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_UID, id=hit.uid).instrument
            lp = client.market_data.get_last_prices(instrument_id=[hit.uid]).last_prices
            if lp:
                p.fair_price = quotation_to_decimal(lp[0].price) * fx.get((ins.currency or "rub").upper(), Decimal(1))
        except Exception:  # noqa: BLE001
            continue


def fill_fair_prices_local(positions: list[Position], cfg: dict, fx: dict[str, Decimal] | None = None) -> None:
    """Без API: эталоны, которые сами есть в портфеле (AMZN-RM → AMZN, TECH2 → TECH), считаем сразу."""
    fx = fx or {k.upper(): Decimal(str(v)) for k, v in (cfg.get("fx_fallback") or {}).items()}
    fx.setdefault("RUB", Decimal(1))
    refs_cfg = {str(k): str(v) for k, v in (cfg.get("reference_tickers") or {}).items()}
    known = {p.ticker for p in positions}
    price_of_known = {p.ticker: (p.cur_price * fx.get(p.currency, Decimal(1))) for p in positions if p.cur_price > 0}
    for p in positions:
        if not p.blocked:
            p.ref_ticker, p.fair_price = "", Decimal(0)
            continue
        ref = refs_cfg.get(p.ticker) or guess_reference(p.ticker, known)
        p.ref_ticker = ref
        if ref in price_of_known:
            p.fair_price = price_of_known[ref]
        elif ref != getattr(p, "_fair_ref", ref):
            p.fair_price = Decimal(0)


@dataclass
class BlockedRow:
    p: Position
    broker: Decimal                 # как оценивает брокер
    fair: Decimal | None            # реальная стоимость по эталону (None — эталона нет)
    otc: Decimal | None             # за сколько можно продать внебиржей сейчас (None — котировки нет)

    @property
    def loss_if_sold(self) -> Decimal | None:
        """Сколько теряешь, продавая внебиржей сейчас, относительно реальной цены."""
        return (self.fair - self.otc) if (self.fair is not None and self.otc is not None) else None


def blocked_report(positions: list[Position]) -> list[BlockedRow]:
    rows = []
    for p in positions:
        if p.blocked:
            fair = p.fair_price * p.quantity if p.fair_price > 0 else None
            otc = p.otc_price * p.quantity if p.otc_price > 0 else None
            rows.append(BlockedRow(p, p.value_rub, fair, otc))
    return rows


def blocked_totals(rows: list[BlockedRow]) -> dict:
    """Итоги по строкам, у которых есть и реальная, и внебиржевая оценка (чтобы сравнение было честным)."""
    full = [r for r in rows if r.fair is not None and r.otc is not None]
    fair = sum((r.fair for r in full), Decimal(0)); otc = sum((r.otc for r in full), Decimal(0))
    return {
        "n": len(rows), "n_full": len(full),
        "broker_all": sum((r.broker for r in rows), Decimal(0)),
        "fair_all": sum((r.fair for r in rows if r.fair is not None), Decimal(0)),
        "otc_all": sum((r.otc for r in rows if r.otc is not None), Decimal(0)),
        "fair": fair, "otc": otc, "loss": fair - otc,
        "loss_pct": (float((fair - otc) / fair * 100) if fair else 0.0),
    }


def refresh_fair_prices(token: str, cfg: dict, positions: list[Position]) -> list[Position]:
    """Лёгкий запрос: только курсы и цены эталонов для заблокированных — без перезагрузки портфеля."""
    apply_blocked_flags(positions, cfg)
    for p in positions:
        p.ref_ticker, p.fair_price, p.otc_price, p.otc_source = "", Decimal(0), Decimal(0), ""
    with connect(token) as client:
        fx = fetch_fx(client, cfg.get("fx_fallback", {}))
        _fill_fair_prices(client, positions, cfg, fx)
    return positions


CLASS_LABELS = {
    "gold": "Золото / металлы",
    "real_estate": "Недвижимость (весь блок ЗПИФ)",
    "real_estate_wh": "ЗПИФ — склады",
    "real_estate_other": "ЗПИФ — офисы, торговля",
    "equity_core": "Дивидендное ядро",
    "equity_spec": "Спекулятивная ячейка",
    "ballast": "Балласт",
    "foreign": "Иностранное / заблокированное",
    "cash": "Валюта / кэш",
    "unclassified": "Без класса",
}


# Эталоны по умолчанию для заблокированных бумаг: тикер-дубликат → торгуемый оригинал.
DEFAULT_REFERENCES = {
    "AMZN-RM": "AMZN",   # заблокированная линия Amazon → SPB-линия с живой ценой
    "TECH2": "TECH",     # заблокированные паи «Тинькофф Технологии» → торгуемые
    "TEUR2": "TEUR",     # «Вечный портфель EUR» заблокированный → торгуемый
    "AGRO": "RAGR",      # расписки РусАгро → акции РусАгро после редомициляции
}


def guess_reference(ticker: str, known: set[str]) -> str:
    """Эвристика: X-RM → X, X2 → X, если X есть среди известных тикеров."""
    if ticker in DEFAULT_REFERENCES:
        return DEFAULT_REFERENCES[ticker]
    if ticker.endswith("-RM") and ticker[:-3] in known:
        return ticker[:-3]
    if ticker.endswith("2") and ticker[:-1] in known:
        return ticker[:-1]
    return ""


def class_label(code: str) -> str:
    return CLASS_LABELS.get(code, code)


SECTOR_LABELS = {
    "energy": "Нефть и газ", "financial": "Финансы", "materials": "Сырьё и материалы",
    "consumer": "Потребительский", "health_care": "Здравоохранение", "it": "IT", "telecom": "Телеком",
    "industrials": "Промышленность", "real_estate": "Недвижимость", "utilities": "Электроэнергетика",
    "government": "Госбумаги", "municipal": "Муниципальные", "ecomaterials": "Экоматериалы",
    "green_energy": "Зелёная энергетика", "green_buildings": "Зелёное строительство", "electrocars": "Электромобили",
    "other": "Прочее", "": "Не указана",
}


def sector_label(code: str) -> str:
    return SECTOR_LABELS.get((code or "").lower(), code or "Не указана")


def class_yields(positions: list[Position], fx: dict[str, Decimal] | None = None) -> dict[str, tuple[float, Decimal]]:
    """Текущая дивдоходность по классам: класс → (%, стоимость класса). Классы без выплат дают 0%."""
    fx = fx or {}
    val: dict[str, Decimal] = {}
    inc: dict[str, Decimal] = {}
    for p in positions:
        if p.value_rub <= 0:
            continue
        val[p.cls] = val.get(p.cls, Decimal(0)) + p.value_rub
        if p.div_12m > 0:
            inc[p.cls] = inc.get(p.cls, Decimal(0)) + p.div_12m * p.quantity * fx.get(p.currency, Decimal(1))
    return {c: (float(inc.get(c, Decimal(0)) / v * 100) if v > 0 else 0.0, v) for c, v in val.items()}


def new_money_yield(positions: list[Position], cfg: dict, fx: dict[str, Decimal] | None = None) -> tuple[float | None, list[tuple[str, float, float]]]:
    """Честная доходность новых денег: новые деньги распределяются по ЦЕЛЕВЫМ долям, значит доходность —
    средневзвешенная по этим долям, включая классы без выплат (золото, кэш) с нулевым денежным доходом.
    Возвращает (%, разбивку [(класс, доля %, дивдоходность класса %)])."""
    targets = cfg.get("targets") or {}
    # середина коридора; «real_estate» — общий блок, берём его вместо подклассов, если он задан
    weights: dict[str, float] = {}
    for cls, rng in targets.items():
        if cls in ("real_estate_wh", "real_estate_other") and "real_estate" in targets:
            continue
        weights[cls] = (float(rng["min"]) + float(rng["max"])) / 2
    if not weights:
        return None, []
    total_w = sum(weights.values())
    if total_w <= 0:
        return None, []
    ys = class_yields(positions, fx)
    def yield_of(cls: str) -> float:
        if cls == "real_estate":                        # общий блок — взвешенно по факту подклассов
            parts = [(ys.get(c, (0.0, Decimal(0)))) for c in ("real_estate_wh", "real_estate_other")]
            v = sum(float(p[1]) for p in parts)
            return sum(p[0] * float(p[1]) for p in parts) / v if v else 0.0
        return ys.get(cls, (0.0, Decimal(0)))[0]
    detail = []
    blended = 0.0
    for cls, w in weights.items():
        share = w / total_w * 100
        y = yield_of(cls)
        detail.append((class_label(cls), share, y))
        blended += share / 100 * y
    detail.sort(key=lambda x: -x[1])
    return blended, detail


def class_price_growth_values(positions: list[Position], values: dict[str, float | None]) -> dict[str, tuple[float, Decimal]]:
    """Рост цены по классам из готовых значений {figi: %} (например, CAGR за несколько лет)."""
    acc: dict[str, list] = {}
    for p in positions:
        v = values.get(p.figi)
        if v is None or p.value_rub <= 0:
            continue
        acc.setdefault(p.cls, []).append((float(p.value_rub), float(v)))
    out = {}
    for cls, xs in acc.items():
        tot = sum(x[0] for x in xs)
        out[cls] = (sum(x[0] * x[1] for x in xs) / tot if tot else 0.0, Decimal(str(tot)))
    return out


def class_price_growth(positions: list[Position], changes: dict, period: str = "year") -> dict[str, tuple[float, Decimal]]:
    """Рост ЦЕНЫ по классам за период (взвешенно по стоимости позиций): класс → (%, стоимость с данными).
    Дивиденды сюда не входят — в ценах акций уже учтены дивидендные гэпы."""
    acc: dict[str, list] = {}
    for p in positions:
        c = (changes.get(p.figi) or {}).get(period)
        if c is None or p.value_rub <= 0:
            continue
        acc.setdefault(p.cls, []).append((float(p.value_rub), float(c)))
    out = {}
    for cls, xs in acc.items():
        v = sum(x[0] for x in xs)
        out[cls] = (sum(x[0] * x[1] for x in xs) / v if v else 0.0, Decimal(str(v)))
    return out


def price_growth_target_weighted(positions: list[Position], cfg: dict, changes: dict,
                                 period: str = "year", values: dict | None = None):
    """Переоценка капитала «как есть»: рост цен по классам за период, взвешенный по ЦЕЛЕВЫМ долям.
    Возвращает (%, разбивка [(класс, доля %, рост %)], число классов без данных)."""
    targets = cfg.get("targets") or {}
    weights: dict[str, float] = {}
    for cls, rng in targets.items():
        if cls in ("real_estate_wh", "real_estate_other") and "real_estate" in targets:
            continue
        weights[cls] = (float(rng["min"]) + float(rng["max"])) / 2
    if not weights:
        return None, [], 0
    g = class_price_growth_values(positions, values) if values is not None else class_price_growth(positions, changes, period)

    def growth_of(cls: str) -> tuple[float, bool]:
        if cls == "real_estate":
            parts = [g.get(c) for c in ("real_estate_wh", "real_estate_other") if g.get(c)]
            v = sum(float(p[1]) for p in parts)
            return (sum(p[0] * float(p[1]) for p in parts) / v, True) if v else (0.0, False)
        hit = g.get(cls)
        return (hit[0], True) if hit else (0.0, False)

    # классы без данных НЕ считаем нулём — исключаем и перенормируем веса, иначе среднее занижается
    known = {cls: growth_of(cls)[0] for cls, _ in weights.items() if growth_of(cls)[1]}
    missing_names = [class_label(cls) for cls in weights if cls not in known]
    w_known = sum(w for cls, w in weights.items() if cls in known)
    if w_known <= 0:
        return None, [], len(missing_names)
    detail, blended = [], 0.0
    for cls, w in weights.items():
        share = w / w_known * 100 if cls in known else 0.0
        val = known.get(cls)
        detail.append((class_label(cls), share, val if val is not None else float("nan")))
        if cls in known:
            blended += share / 100 * val
    detail.sort(key=lambda x: (-x[1], x[0]))
    return blended, detail, missing_names


def dividend_yield(p: Position, fx: dict[str, Decimal] | None = None) -> float | None:
    """Дивдоходность позиции за 12 мес к текущей стоимости, %. None — выплат нет / не известно."""
    if p.div_12m <= 0 or p.value_rub <= 0:
        return None
    rate = (fx or {}).get(p.currency, Decimal(1))
    return float(p.div_12m * p.quantity * rate / p.value_rub * 100)


def breakdown_currency(positions: list[Position]) -> list[tuple[str, float]]:
    """Доля по валюте/металлу: металлы — по названию, остальное — по валюте инструмента."""
    cur_names = {"RUB": "Рубль", "USD": "Доллар США", "HKD": "Гонконгский доллар", "EUR": "Евро", "CNY": "Юань"}
    acc: dict[str, float] = {}
    for p in positions:
        if p.cls == "gold" or (p.instrument_type == "currency" and p.ticker.endswith("_TOM") and not p.ticker.startswith(("USD", "EUR", "CNY"))):
            key = p.name
        else:
            key = cur_names.get(p.currency, p.currency)
        acc[key] = acc.get(key, 0.0) + float(p.value_rub)
    return list(acc.items())


def breakdown_companies(positions: list[Position]) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for p in positions:
        if p.cls in ("cash",):
            continue
        acc[p.name] = acc.get(p.name, 0.0) + float(p.value_rub)
    return list(acc.items())


def sector_group(p: Position) -> str:
    """Группа для тепловой карты: как в разбивке по отраслям."""
    if p.cls == "gold" or p.instrument_type == "currency":
        return "Валюта и металлы"
    if p.cls in ("real_estate_wh", "real_estate_other"):
        return "Недвижимость (ЗПИФ)"
    return sector_label(p.sector)


def breakdown_sectors(positions: list[Position]) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for p in positions:
        if p.cls == "gold" or p.instrument_type == "currency":
            key = "Валюта и металлы"
        elif p.cls in ("real_estate_wh", "real_estate_other"):
            key = "Недвижимость (ЗПИФ)"
        else:
            key = sector_label(p.sector)
        acc[key] = acc.get(key, 0.0) + float(p.value_rub)
    return list(acc.items())


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------

def class_shares(positions: list[Position]) -> tuple[Decimal, dict[str, Decimal]]:
    total = sum(p.value_rub for p in positions)
    by_cls: dict[str, Decimal] = defaultdict(Decimal)
    for p in positions:
        by_cls[p.cls] += p.value_rub
    # real_estate = склады + прочее
    by_cls["real_estate"] = by_cls.get("real_estate_wh", Decimal(0)) + by_cls.get("real_estate_other", Decimal(0))
    return total, by_cls


def check_targets(total: Decimal, by_cls: dict, targets: dict) -> list[str]:
    lines = []
    for cls, rng in targets.items():
        share = (by_cls.get(cls, Decimal(0)) / total * 100) if total else Decimal(0)
        lo, hi = Decimal(str(rng["min"])), Decimal(str(rng["max"]))
        if share < lo:
            gap = (lo - share) / 100 * total
            lines.append(f"- **{class_label(cls)}**: {share:.1f}% < цели {lo}% — недовес ~{gap:,.0f} ₽")
        elif share > hi:
            over = (share - hi) / 100 * total
            lines.append(f"- **{class_label(cls)}**: {share:.1f}% > потолка {hi}% — перевес ~{over:,.0f} ₽")
    return lines


def check_rules(positions: list[Position], total: Decimal, rules: dict) -> dict[str, list[str]]:
    res: dict[str, list[str]] = defaultdict(list)
    frozen: dict[str, str] = rules.get("frozen", {})
    dd = Decimal(str(rules.get("drawdown_alert_pct", 10)))
    buy_cls = set(rules.get("buy_candidates_classes", []))
    caps = rules.get("position_caps", {})
    single_max = Decimal(str(rules.get("single_name_max_pct", 100)))
    tail = Decimal(str(rules.get("tail_threshold_rub", 0)))

    for p in sorted(positions, key=lambda x: x.pnl_pct):
        # Кандидаты на добор: просадка >= порога, класс разрешён, не заморожен
        if p.cls in buy_cls and p.pnl_pct <= -dd:
            if p.ticker in frozen:
                res["frozen_but_dipped"].append(
                    f"- {p.name} ({p.ticker}): {p.pnl_pct:.1f}% — заморожен: {frozen[p.ticker]}")
            else:
                res["buy_candidates"].append(
                    f"- {p.name} ({p.ticker}): {p.pnl_pct:.1f}% от средней, позиция {p.value_rub:,.0f} ₽")
        # Лимиты на позицию
        if p.ticker in caps and p.value_rub > Decimal(str(caps[p.ticker])):
            res["caps"].append(f"- {p.name}: {p.value_rub:,.0f} ₽ > лимита {caps[p.ticker]:,} ₽")
        # Концентрация
        share = p.value_rub / total * 100 if total else Decimal(0)
        if p.cls not in ("gold", "cash") and share > single_max:
            res["concentration"].append(f"- {p.name}: {share:.1f}% портфеля > {single_max}%")
        # Хвост
        if p.cls in ("ballast", "unclassified") and 0 < p.value_rub < tail:
            res["tail"].append(f"- {p.name} ({p.ticker}): {p.value_rub:,.0f} ₽, {p.pnl_pct:.1f}%")
        if p.cls == "unclassified":
            res["unclassified"].append(f"- {p.name} ({p.ticker}): {p.value_rub:,.0f} ₽")
    return res


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

import re as _re
from style import MASK, money_fn   # noqa: E402 — маска «глазика» и формат сумм живут в style.py
_MONEY_RE = _re.compile(r"(?<![\w%])[-+]?~?\d[\d\s,.]*\s*(?:тыс\.?|млн\.?|млрд\.?|к)?\s?(?:₽|руб\.?|рублей)", _re.I)


def mask_text(text: str) -> str:
    """Скрывает рублёвые суммы в свободном тексте (строки сигналов/отклонений); проценты остаются."""
    return _MONEY_RE.sub(MASK + " ₽", text)


def render(positions: list[Position], cfg: dict, mask: bool = False) -> str:
    """Markdown-отчёт. mask=True — суммы и количества скрыты (режим «глазика»), проценты и цены видны."""
    total, by_cls = class_shares(positions)
    tgt_lines = check_targets(total, by_cls, cfg["targets"])
    rules = check_rules(positions, total, cfg.get("rules", {}))
    if mask:
        tgt_lines = [mask_text(l) for l in tgt_lines]
        rules = {k: [mask_text(l) for l in v] for k, v in rules.items()}
    M = money_fn(mask)

    out = [f"# Портфель — {datetime.now():%d.%m.%Y %H:%M}", "",
           f"Стоимость: **{M(total)} ₽**", "", "## Доли по классам", "",
           "| Класс | Сумма, ₽ | Доля |", "|---|---:|---:|"]
    order = ["gold", "real_estate", "real_estate_wh", "real_estate_other",
             "equity_core", "equity_spec", "ballast", "foreign", "cash", "unclassified"]
    for cls in order:
        v = by_cls.get(cls, Decimal(0))
        if v:
            out.append(f"| {class_label(cls)} | {M(v)} | {v / total * 100:.1f}% |")

    out += ["", "## Отклонения от целей", ""]
    out += tgt_lines or ["- всё в коридорах"]

    sections = [
        ("buy_candidates", "## Кандидаты на добор (просадка ≥ порога, не заморожены)"),
        ("frozen_but_dipped", "## Просели, но заморожены — не трогать"),
        ("caps", "## Превышение лимитов на позицию"),
        ("concentration", "## Концентрация в одном эмитенте"),
        ("tail", "## Хвост (мелкие позиции балласта — кандидаты на чистку)"),
        ("unclassified", "## Без класса — назначь в Настройки → Инструменты"),
    ]
    for key, title in sections:
        if rules.get(key):
            out += ["", title, ""] + rules[key]

    br = blocked_report(positions)
    if br:
        t = blocked_totals(br)
        out += ["", f"## Заблокировано — {t['n']} поз.", "",
                f"Реальная стоимость (по эталонам): **{M(t['fair_all'])} ₽**; можно выручить внебиржей сейчас: **{M(t['otc_all'])} ₽**; "
                f"у брокера в портфеле: {M(t['broker_all'])} ₽.",
                f"По {t['n_full']} поз. с обеими оценками потеря при продаже сейчас: **{M(t['loss'])} ₽ ({t['loss_pct']:.0f}% реальной цены)**.", "",
                "| Инструмент | Эталон | У брокера, ₽ | Внебиржа, ₽ | По рынку, ₽ | Потеря при продаже, ₽ |",
                "|---|---|---:|---:|---:|---:|"]
        for r in br:
            f = lambda v: "—" if v is None else M(v)
            out.append(f"| {r.p.name} | {r.p.ref_ticker or '—'} | {M(r.broker)} | {f(r.otc)} | {f(r.fair)} | {f(r.loss_if_sold)} |")

    out += ["", "## Позиции", "", "| Инструмент | Класс | Кол-во | Средняя | Текущая | Стоимость, ₽ | P/L |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for p in sorted(positions, key=lambda x: -x.value_rub):
        out.append(f"| {p.name} | {class_label(p.cls)} | {M(p.quantity)} | {p.avg_price:,.2f} | {p.cur_price:,.2f} "
                   f"| {M(p.value_rub)} | {p.pnl_pct:+.1f}% |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    try:
        import paths
        default_cfg = paths.data_dir() / "config.yaml"
    except Exception:  # noqa: BLE001
        default_cfg = Path("config.yaml")
    ap.add_argument("--config", default=str(default_cfg if default_cfg.exists() else "config.yaml"))
    ap.add_argument("--json", help="сохранить сырой снапшот позиций в JSON (для истории/диффа)")
    args = ap.parse_args()

    token = os.environ.get("TINKOFF_TOKEN")
    if not token:
        try:  # тот же токен, что задан в приложении (Настройки → Доступ)
            import keyring
            token = keyring.get_password("portfolio-check.tinkoff", "invest-api-token")
        except Exception:  # noqa: BLE001
            token = None
    if not token:
        sys.exit("Нет токена: задай его в приложении (Настройки → Доступ) или через TINKOFF_TOKEN")

    import config_migrate as CM
    cfg, _ = CM.migrate(load_config(args.config))   # CLI файл не переписывает — только читает в актуальном формате
    positions = load_portfolio(token, cfg)

    if args.json:
        save_snapshot(positions, args.json)

    print(render(positions, cfg))


def load_portfolio(token: str, cfg: dict) -> list[Position]:
    """Единая точка входа для CLI и GUI."""
    with connect(token) as client:
        account_id = pick_account(client, cfg.get("account", {}).get("id", ""))
        return fetch_positions(client, account_id, cfg)


def fetch_income_estimate(client, positions: list[Position], fx: dict[str, Decimal] | None = None,
                          payouts_out: list | None = None) -> Decimal:
    """Сумма выплат за последние 12 месяцев по всем позициям × количество, после НДФЛ 13%.
    Тем же запросом (окно на год вперёд) собираются объявленные будущие выплаты для календаря — в payouts_out.
    Best-effort: бумаги, по которым API выплат не отдаёт (часть ЗПИФ, иностранные), просто не учитываются."""
    from datetime import timedelta, timezone
    import payouts as PO
    _, _, _, money_to_decimal = _sdk()
    fx = fx or {}
    now = datetime.now(timezone.utc)
    total = Decimal(0)
    for p in positions:
        if not p.figi or p.instrument_type not in ("share", "etf"):
            continue
        try:
            divs = client.instruments.get_dividends(figi=p.figi, from_=now - timedelta(days=365),
                                                    to=now + timedelta(days=365)).dividends
        except Exception:  # noqa: BLE001
            continue
        # доход за 12 мес — только прошедшие отсечки (будущие объявленные — в календарь)
        past = [d for d in divs if not getattr(d, "record_date", None) or d.record_date <= now]
        per_share = sum((money_to_decimal(d.dividend_net) for d in past), Decimal(0)) * Decimal("0.87")
        p.div_12m = per_share
        rate = fx.get(p.currency, Decimal(1))
        total += per_share * p.quantity * rate
        if payouts_out is not None:
            payouts_out += PO.from_dividends(p, divs, float(rate), money_to_decimal, now.date())
    return total


def load_portfolio_full(token: str, cfg: dict) -> tuple[list[Position], Decimal, object, list]:
    """Позиции + оценка годового дохода + макро ЦБ (инфляция, ставка) + выплаты для календаря. Для приложения."""
    with connect(token) as client:
        account_id = pick_account(client, cfg.get("account", {}).get("id", ""))
        positions = fetch_positions(client, account_id, cfg)
        fx = fetch_fx(client, cfg.get("fx_fallback", {}))
        payouts: list = []
        income = fetch_income_estimate(client, positions, fx, payouts)
    macro = None
    try:
        from macro import fetch_cbr_macro
        macro = fetch_cbr_macro()
    except Exception as e:  # noqa: BLE001
        _log.warning("макро ЦБ не получено: %s", e)
    return positions, income, macro, payouts


def save_snapshot(positions: list[Position], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(p).items()}
                   for p in positions], f, ensure_ascii=False, indent=2)


def load_snapshot(path: str) -> list[Position]:
    """Обратная операция: снапшот → позиции (офлайн-просмотр, демо, дифф)."""
    dec = {"quantity", "avg_price", "cur_price", "value_rub", "pnl_pct", "fair_price", "otc_price", "div_12m"}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [Position(**{k: (Decimal(v) if k in dec else v) for k, v in r.items()}) for r in raw]


if __name__ == "__main__":
    main()
