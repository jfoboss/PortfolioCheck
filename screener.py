"""
screener.py — кандидаты по стратегии из T-Invest API (фундаментальные показатели активов).

Раньше — MOEX ISS, но ISS убрал ресурс /securities/{ticker}/dividends. T-Invest отдаёт по каждому активу одним
батч-запросом: капитализацию, дивдоходность TTM, дивиденды на акцию, P/E, чистый долг/EBITDA, ROE, free float.
Фильтры под правила: минимальная дивдоходность, минимальная капитализация, максимальный долг/EBITDA; исключаются
бумаги портфеля и чёрный список. Качественные критерии (менеджмент, миноритарии, «крыша») не автоматизируются —
результат скринера это список для изучения, а не решение.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("portfolio.screener")

# Чёрный список по умолчанию пуст: какие бумаги не предлагать — личное решение инвестора, оно хранится в конфиге
# (Настройки → Скринер или правый клик по кандидату).
DEFAULT_BLACKLIST: dict[str, str] = {}


@dataclass
class Idea:
    ticker: str
    name: str
    sector: str
    price: float
    cap_bln: float            # капитализация, млрд ₽
    div_yield: float          # %, дивдоходность TTM
    div_per_share: float      # ₽ за 12 мес
    pe: float | None
    net_debt_ebitda: float | None
    roe: float | None
    free_float: float | None  # %
    ex_div_date: str
    avg5y_div_yield: float | None = None   # средняя дивдоходность за 5 лет, % — отсекает разовых плательщиков
    div_growth_5y: float | None = None     # рост дивидендов за 5 лет, %/год
    uid: str = ""                          # instrument uid — для догрузки свечей
    month_change: float | None = None      # % за месяц, догружается в фоне
    news_count: int = 0                    # упоминаний в ленте
    news_sentiment: str = ""               # positive | neutral | negative — из ИИ-сводки
    news_note: str = ""                    # пояснение модели (в подсказке)


@dataclass
class ScreenResult:
    ideas: list
    universe: int          # акций Мосбиржи (TQBR) в справочнике
    checked: int           # по скольким получен фундаментал
    with_cap: int
    paying: int            # с дивдоходностью > 0
    from_cache: bool
    at: str
    errors: int = 0
    last_error: str = ""


def _f(v) -> float | None:
    try:
        v = float(v)
        return None if v == 0 else v
    except Exception:  # noqa: BLE001
        return None


def fetch_all(token: str, progress=None) -> tuple[list[Idea], dict]:
    """Все акции TQBR с фундаменталом. Возвращает (ideas без фильтра, статистика)."""
    import portfolio_check as pc
    from tinkoff.invest.schemas import GetAssetFundamentalsRequest, InstrumentStatus
    from tinkoff.invest.utils import quotation_to_decimal
    Client, *_ = pc._sdk(); pc._setup_grpc_roots()
    stats = {"errors": 0, "last_error": ""}
    with Client(token) as client:
        shares = client.instruments.shares(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE).instruments
        universe = [s for s in shares if s.class_code == "TQBR" and s.currency == "rub" and s.asset_uid]
        stats["universe"] = len(universe)
        # цены батчами по 100
        prices: dict[str, float] = {}
        for i in range(0, len(universe), 100):
            chunk = universe[i:i + 100]
            try:
                for lp in client.market_data.get_last_prices(instrument_id=[s.uid for s in chunk]).last_prices:
                    prices[lp.instrument_uid] = float(quotation_to_decimal(lp.price))
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1; stats["last_error"] = f"цены: {type(e).__name__}: {str(e)[:120]}"; log.error("скринер, цены: %s", e)
            time.sleep(0.2)
        # фундаментал батчами по 100 asset_uid
        fund: dict[str, object] = {}
        by_asset = {s.asset_uid: s for s in universe}
        uids = list(by_asset)
        for i in range(0, len(uids), 100):
            chunk = uids[i:i + 100]
            if progress:
                progress(min(i + 100, len(uids)), len(uids), "фундаментал")
            try:
                resp = client.instruments.get_asset_fundamentals(GetAssetFundamentalsRequest(assets=chunk))
                for st in resp.fundamentals:
                    fund[st.asset_uid] = st
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1; stats["last_error"] = f"фундаментал: {type(e).__name__}: {str(e)[:120]}"
                log.error("скринер, фундаментал (батч %d): %s", i // 100, e)
            time.sleep(0.3)
    ideas = []
    for s in universe:
        st = fund.get(s.asset_uid)
        if st is None:
            continue
        cap = _f(getattr(st, "market_capitalization", 0)) or 0.0
        dy = _f(getattr(st, "dividend_yield_daily_ttm", 0)) or 0.0
        dps = _f(getattr(st, "dividend_rate_ttm", 0)) or _f(getattr(st, "dividends_per_share", 0)) or 0.0
        exd = getattr(st, "ex_dividend_date", None)
        ideas.append(Idea(s.ticker, s.name, pc.sector_label(s.sector), prices.get(s.uid, 0.0), cap / 1e9, dy, dps,
                          _f(getattr(st, "pe_ratio_ttm", 0)), _f(getattr(st, "net_debt_to_ebitda", 0)), _f(getattr(st, "roe", 0)),
                          _f(getattr(st, "free_float", 0)), exd.strftime("%Y-%m-%d") if exd and getattr(exd, "year", 1970) > 1971 else "",
                          _f(getattr(st, "five_years_average_dividend_yield", 0)), _f(getattr(st, "five_year_annual_dividend_growth_rate", 0)),
                          s.uid))
    stats["checked"] = len(ideas)
    stats["with_cap"] = sum(1 for i in ideas if i.cap_bln > 0)
    return ideas, stats


@dataclass
class Filters:
    min_yield: float = 8.0
    min_cap_bln: float = 50.0
    max_debt_ebitda: float = 3.0      # 0 — не фильтровать
    min_avg5y_yield: float = 4.0      # 0 — не фильтровать; отсекает разовых плательщиков
    max_pe: float = 0.0               # 0 — не фильтровать
    min_free_float: float = 0.0       # % ; 0 — не фильтровать
    exclude_sectors: tuple = ()       # названия отраслей (как в таблице)


def apply_filters(ideas: list[Idea], f: Filters, exclude: set[str]) -> list[Idea]:
    def ok(i: Idea) -> bool:
        if i.ticker in exclude or i.div_yield < f.min_yield or i.cap_bln < f.min_cap_bln:
            return False
        if f.max_debt_ebitda > 0 and i.net_debt_ebitda is not None and i.net_debt_ebitda > f.max_debt_ebitda:
            return False
        if f.min_avg5y_yield > 0 and (i.avg5y_div_yield or 0) < f.min_avg5y_yield:
            return False
        if f.max_pe > 0 and i.pe is not None and (i.pe > f.max_pe or i.pe < 0):
            return False
        if f.min_free_float > 0 and (i.free_float or 0) < f.min_free_float:
            return False
        if i.sector in f.exclude_sectors:
            return False
        return True
    res = [i for i in ideas if ok(i)]
    res.sort(key=lambda x: -x.div_yield)
    return res


def load_cached(cache_dir: Path, filters: "Filters", exclude: set[str], max_age_h: int = 72) -> "ScreenResult | None":
    """Результат прошлого прохода с диска, без сети. None — кэша нет или он устарел."""
    f = cache_dir / "screener.json"
    if not f.exists():
        return None
    try:
        saved = json.loads(f.read_text(encoding="utf-8"))
        if saved.get("source") != "tinvest2":     # старый кэш без uid — перезапрашиваем
            return None
        at = saved["at"]
        if datetime.fromisoformat(at) < datetime.now() - timedelta(hours=max_age_h):
            return None
        ideas = [Idea(**r) for r in saved["ideas"]]
        st = saved.get("stats", {})
        r = ScreenResult(apply_filters(ideas, filters, exclude), st.get("universe", len(ideas)), len(ideas), st.get("with_cap", 0),
                         sum(1 for i in ideas if i.div_yield > 0), True, at, st.get("errors", 0))
        r.all_ideas = ideas
        return r
    except Exception as e:  # noqa: BLE001
        log.warning("кэш скринера не прочитан: %s", e)
        return None


def screen(token: str, min_yield: float, min_cap_bln: float, exclude: set[str], max_debt_ebitda: float = 0.0,
           progress=None, cache_dir: Path | None = None, cache_hours: int = 24, filters: "Filters | None" = None) -> ScreenResult:
    cache = cache_dir / "screener.json" if cache_dir else None
    ideas, stats, from_cache, at = None, {}, False, datetime.now().isoformat()
    if cache and cache.exists() and cache_hours > 0:
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if datetime.fromisoformat(saved["at"]) > datetime.now() - timedelta(hours=cache_hours) and saved.get("source") == "tinvest2":
                ideas = [Idea(**r) for r in saved["ideas"]]; stats = saved.get("stats", {}); from_cache = True; at = saved["at"]
        except Exception:  # noqa: BLE001
            ideas = None
    if ideas is None:
        ideas, stats = fetch_all(token, progress)
        paying = sum(1 for i in ideas if i.div_yield > 0)
        if cache and paying > 0:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"at": at, "source": "tinvest2", "ideas": [asdict(i) for i in ideas], "stats": stats},
                                        ensure_ascii=False), encoding="utf-8")
    f = filters or Filters(min_yield, min_cap_bln, max_debt_ebitda)
    f.min_yield, f.min_cap_bln, f.max_debt_ebitda = min_yield, min_cap_bln, max_debt_ebitda
    res = apply_filters(ideas, f, exclude)
    paying = sum(1 for i in ideas if i.div_yield > 0)
    log.info("скринер: вселенная %s, фундаментал %d, с кап. %s, платящих %d, ошибок %s, %s", stats.get("universe"), len(ideas),
             stats.get("with_cap"), paying, stats.get("errors", 0), "кэш" if from_cache else "T-Invest")
    r = ScreenResult(res, stats.get("universe", len(ideas)), len(ideas), stats.get("with_cap", 0), paying, from_cache, at,
                     stats.get("errors", 0), stats.get("last_error", ""))
    r.all_ideas = ideas       # для перефильтрации без похода в сеть (чёрный список из контекстного меню)
    return r
