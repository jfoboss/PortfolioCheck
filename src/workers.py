"""
workers.py — фоновые загрузки (QThread). Сеть и тяжёлые расчёты — только здесь, не в UI-потоке.
Новый воркер-атрибут окна добавь в MainWindow.WORKERS (иначе SIGABRT при выходе).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

import portfolio_check as pc
import history as H
import ai as AI
import news as NEWS
import screener as SCR
import style as SY

import logging
log = logging.getLogger("portfolio")


class FairPriceWorker(QThread):
    """Пересчёт цен эталонов для заблокированных после правки настроек — без перезагрузки портфеля."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list):
        super().__init__()
        self.token, self.cfg, self.positions = token, cfg, positions

    def run(self) -> None:
        try:
            self.done.emit(pc.refresh_fair_prices(self.token, self.cfg, self.positions))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 0))


class HistoryWorker(QThread):
    """Свечи по всем позициям (с кэшем) → доходности по периодам и восстановленная история портфеля."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list, cache_dir: Path):
        super().__init__()
        self.token, self.cfg, self.positions, self.cache_dir = token, cfg, positions, cache_dir

    def run(self) -> None:
        try:
            candles: dict[str, list] = {}
            with pc.connect(self.token) as client:
                fx = {k: float(v) for k, v in pc.fetch_fx(client, self.cfg.get("fx_fallback", {})).items()}
                for p in self.positions:
                    if self.isInterruptionRequested():
                        return
                    if p.figi and p.instrument_type in ("share", "etf", "bond", "currency"):
                        candles[p.figi] = H.fetch_candles(client, p.figi, self.cache_dir)
            changes = {p.figi: H.period_changes(candles.get(p.figi, []), float(p.cur_price)) for p in self.positions}
            empty = [p.ticker for p in self.positions if not candles.get(p.figi)]
            hist = H.backfilled_history(self.positions, candles, fx)
            self.done.emit((changes, hist, empty))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 0))


class NewsWorker(QThread):
    done = Signal(object)

    def __init__(self, feeds: list, positions: list, aliases: dict, bundle: Path):
        super().__init__()
        self.feeds, self.positions, self.aliases, self.bundle = feeds, positions, aliases, bundle

    def run(self) -> None:
        self.done.emit(NEWS.fetch_news(self.feeds, self.positions, self.aliases, self.bundle))


class LongHistoryWorker(QThread):
    """Свечи за несколько лет по позициям портфеля → среднегодовой рост цены (CAGR) по каждой бумаге."""
    done = Signal(object)          # ({figi: cagr %}, покрытые годы, сколько бумаг без истории)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, token: str, cfg: dict, positions: list, cache_dir: Path, years: float):
        super().__init__()
        self.token, self.cfg, self.positions, self.cache_dir, self.years = token, cfg, positions, cache_dir, years

    def run(self) -> None:
        try:
            out, covered, missing = {}, [], 0
            days = int(self.years * 365) + 30
            with pc.connect(self.token) as client:
                for i, p in enumerate(self.positions):
                    if self.isInterruptionRequested():
                        return
                    if not p.figi or p.instrument_type not in ("share", "etf", "bond", "currency"):
                        continue
                    self.progress.emit(f"История {self.years:.0f} г: {i + 1}/{len(self.positions)} ({p.ticker})")
                    series = H.fetch_candles(client, p.figi, self.cache_dir, days=days)
                    val, cov = H.cagr(series, float(p.cur_price), self.years)
                    if val is None:
                        missing += 1
                    else:
                        out[p.figi] = val; covered.append(cov)
            self.done.emit((out, (sum(covered) / len(covered)) if covered else 0.0, missing))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 200))


class IdeasEnrichWorker(QThread):
    """Догрузка по кандидатам скринера: свечи (изменение за месяц) и ближайшая выплата (GetDividends).
    Дата из фундаментала (ex_dividend_date) — прошлая отсечка, для календаря не годится."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, token: str, ideas: list, cache_dir: Path, limit: int = 40):
        super().__init__()
        self.token, self.ideas, self.cache_dir, self.limit = token, ideas, cache_dir, limit

    def run(self) -> None:
        try:
            out, pays = {}, {}
            with pc.connect(self.token) as client:
                for idea in self.ideas[:self.limit]:
                    if self.isInterruptionRequested():
                        return
                    if not idea.uid:
                        continue
                    series = H.fetch_candles(client, idea.uid, self.cache_dir, days=120, by_uid=True)
                    out[idea.ticker] = H.period_changes(series, idea.price).get("month")
                    nxt = self._next_payout(client, idea)
                    if nxt is not None:
                        pays[idea.ticker] = nxt
            self.done.emit({"month": out, "payouts": pays})
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 200))


    @staticmethod
    def _next_payout(client, idea):
        """Ближайшая выплата кандидата на одну бумагу: объявленная или ожидаемая по прошлому году."""
        import payouts as PO
        from datetime import datetime, timedelta
        from types import SimpleNamespace
        now = datetime.now()
        try:
            divs = client.instruments.get_dividends(instrument_id=idea.uid, from_=now - timedelta(days=400),
                                                    to=now + timedelta(days=365)).dividends
        except Exception as e:  # noqa: BLE001 — нет выплат или старая версия SDK: кандидат просто без даты
            log.info("дивиденды кандидата %s: %s", idea.ticker, SY.err_text(e, 120))
            return None
        money = lambda v: float(getattr(v, "units", 0) or 0) + float(getattr(v, "nano", 0) or 0) / 1e9   # MoneyValue
        pos = SimpleNamespace(ticker=idea.ticker, name=idea.name, quantity=1, currency="rub")
        return PO.next_payout(PO.from_dividends(pos, divs, 1.0, money, now.date()), now.date())


class ScreenerWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, token: str, min_yield: float, min_cap: float, max_debt: float, exclude: set, cache_dir: Path, force: bool = False):
        super().__init__()
        self.token, self.min_yield, self.min_cap, self.max_debt, self.exclude, self.cache_dir = token, min_yield, min_cap, max_debt, exclude, cache_dir
        self.force = force
        self.filters = None

    def run(self) -> None:
        try:
            def prog(i, n, t):
                if self.isInterruptionRequested():
                    raise InterruptedError("остановлено при выходе")
                self.progress.emit(f"Скринер: {i}/{n} ({t})")
            res = SCR.screen(self.token, self.min_yield, self.min_cap, self.exclude, self.max_debt, progress=prog,
                             cache_dir=self.cache_dir, cache_hours=0 if self.force else 24, filters=self.filters)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 0))


class AIWorker(QThread):
    done = Signal(object)      # (Summary, note)
    failed = Signal(str)

    def __init__(self, conf: "AI.AIConfig", news_items, positions, cache_dir, force, candidates=None):
        super().__init__()
        self.conf, self.news_items, self.positions = conf, news_items, positions
        self.cache_dir, self.force, self.candidates = cache_dir, force, candidates or []

    def run(self) -> None:
        c = self.conf
        cache = AI.ResponseCache(self.cache_dir)
        fp = AI.news_fingerprint(self.news_items, c.provider, c.model, self.candidates)
        subj = AI.subjects_fingerprint(self.positions, self.candidates)
        hit, why = cache.get("news", fp, c.min_interval_h, self.force, subj)
        if hit:
            self.done.emit((AI.Summary(**hit), f"из кэша: {why}")); return
        try:
            s = AI.summarize(c.provider, c.key, c.model, self.news_items, self.positions, c.proxy, c.base_url,
                             candidates=self.candidates)
            cache.put("news", fp, __import__("dataclasses").asdict(s), subj)
            self.done.emit((s, ""))
        except AI.RateLimited as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 300))


class ExecWorker(QThread):
    """Executive summary по всем экранам по очереди: (kind, facts) → ExecSummary."""
    one = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, conf: "AI.AIConfig", jobs: list, cache_dir, force):
        super().__init__()
        self.conf, self.jobs, self.cache_dir, self.force = conf, jobs, cache_dir, force

    def run(self) -> None:
        """Сначала всё, что есть в кэше; устаревшие экраны — ОДНИМ запросом (лимиты считаются в запросах)."""
        cache = AI.ResponseCache(self.cache_dir)
        todo, fps = [], {}
        for kind, facts in self.jobs:
            fp = AI.exec_fingerprint(kind, facts, self.conf.provider, self.conf.model); fps[kind] = fp
            hit, why = cache.get(f"exec_{kind}", fp, self.conf.min_interval_h, self.force)
            if hit:
                self.one.emit((AI.ExecSummary(**hit), f"из кэша: {why}"))
            else:
                todo.append((kind, facts))
        if todo and not self.isInterruptionRequested():
            try:
                c = self.conf
                got = AI.exec_summary_batch(todo, c.provider, c.key, c.model, c.proxy, c.base_url)
                for s in got:
                    cache.put(f"exec_{s.kind}", fps[s.kind], __import__("dataclasses").asdict(s))
                    self.one.emit((s, ""))
                missing = [k for k, _ in todo if k not in {s.kind for s in got}]
                if missing:
                    self.failed.emit("модель не вернула сводку для: " + ", ".join(missing))
            except AI.RateLimited as e:
                self.failed.emit(str(e))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(SY.err_text(e, 200))
        self.done.emit()


class LoadWorker(QThread):
    done = Signal(object)      # (positions, income_estimate)
    failed = Signal(str)

    def __init__(self, token: str, cfg: dict):
        super().__init__()
        self.token, self.cfg = token, cfg

    def run(self) -> None:
        try:
            self.done.emit(pc.load_portfolio_full(self.token, self.cfg))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(SY.err_text(e, 0))


class FactorsWorker(QThread):
    """Индекс Мосбиржи (MOEX ISS) и курс доллара (T-Invest, запасной — ЦБ) для сценариев, не чаще раза в сутки;
    заодно календарь решений ЦБ по ставке (не чаще раза в неделю)."""
    done = Signal(object)

    def __init__(self, cache_dir: Path, token: str | None = None):
        super().__init__()
        self.cache_dir, self.token = cache_dir, token

    def run(self) -> None:
        import factors as FA
        import macro

        def usd():
            with pc.connect(self.token) as client:
                return H.fetch_candles(client, FA.USD_FIGI, self.cache_dir, days=400)
        try:
            status = FA.fetch_all(self.cache_dir, usd_series=usd if self.token else None)
            status["calendar"] = macro.fetch_calendar(self.cache_dir)
            self.done.emit(status)
        except Exception as e:  # noqa: BLE001
            self.done.emit({"ошибка": SY.err_text(e, 0)})


class ForecastWorker(QThread):
    """Консенсус-прогноз по одной бумаге (T-Invest GetForecastBy) для карточки — с кэшем на сутки."""
    done = Signal(object)

    def __init__(self, token: str, uid: str, cache_dir: Path, parent=None):
        super().__init__(parent)                    # родитель — карточка: поток живёт, пока жива она
        self.token, self.uid, self.cache_dir = token, uid, cache_dir

    def run(self) -> None:
        import consensus as CS
        try:
            self.done.emit(CS.fetch(self.token, self.uid, self.cache_dir))
        except Exception as e:  # noqa: BLE001
            self.done.emit({"error": SY.err_text(e, 160)})
