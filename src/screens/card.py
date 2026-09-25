"""
screens/card.py — карточка бумаги (BACKLOG п. 17, аналог DES + FA + DVD + GP у Bloomberg): всё о бумаге на одной
странице — цена и график, изменения по периодам, позиция и её место в правилах, выплаты, фундаментал, новости,
оценка ИИ. Только то, что уже загружено или лежит в кэше: карточка открывается мгновенно и без сети.

Открывается двойным кликом по бумаге в таблицах «Портфель», «Календарь», «Идеи» (и из контекстного меню «Идей»).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QSplitter, QTableWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

import history as H
import news as NEWS
import paths
import activity
import portfolio_check as pc
import style as SY
from charts import LineChart
from ui_common import MASK
from widgets import is_running, stop_workers

PERIOD_LABELS = {"day": "день", "week": "неделя", "month": "месяц", "year": "год"}


def card_data(win, ticker: str) -> dict:
    """Всё, что окно знает о бумаге. Отдельно от виджетов — проверяется тестом без отрисовки."""
    pos = next((p for p in win.positions if p.ticker == ticker), None)
    res = getattr(win, "_last_screen", None)
    idea = next((i for i in (getattr(res, "all_ideas", None) or getattr(res, "ideas", None) or []) if i.ticker == ticker), None)
    cache = paths.cache_dir()
    key = (pos.figi if pos and pos.figi else "") or (getattr(idea, "uid", "") if idea else "")
    series = H.load_cached(cache, key) if key else []
    cur = float(pos.cur_price) if pos else (float(idea.price) if idea else (series[-1][1] if series else 0.0))
    changes = H.period_changes(series, cur) if series and cur else {}
    name = pos.name if pos else (idea.name if idea else ticker)
    pays = sorted((x for x in getattr(win, "payouts", []) if x.ticker == ticker), key=lambda x: x.record_date)
    kw = NEWS.build_keywords([pos or idea or type("P", (), {"ticker": ticker, "name": name})()],
                             {**NEWS.DEFAULT_ALIASES, **((win.cfg.get("news") or {}).get("aliases") or {})}).get(ticker, [])
    news = [it for it in getattr(win, "news_items", [])
            if ticker in it.matched or NEWS.item_refers(it, ticker, kw)]
    s = getattr(win, "ai_summary", None)
    ai = next((i for i in (s.items if s else []) if i.get("ticker") == ticker), None)
    rules = {}
    if pos:
        total = sum((p.value_rub for p in win.positions), Decimal(0))
        cls_total = sum((p.value_rub for p in win.positions if p.cls == pos.cls), Decimal(0))
        tg = (win.cfg.get("targets") or {}).get(pos.cls) or {}
        r = win.cfg.get("rules") or {}
        rules = {"share": float(pos.value_rub / total * 100) if total else 0.0,
                 "class_share": float(cls_total / total * 100) if total else 0.0,
                 "corridor": (tg.get("min"), tg.get("max")) if tg else None,
                 "single_max": float(r.get("single_name_max_pct", 0) or 0),
                 "frozen": (r.get("frozen") or {}).get(ticker),
                 "cap": (r.get("position_caps") or {}).get(ticker)}
    import screener as SCR
    universe = getattr(res, "all_ideas", None) or getattr(res, "ideas", None) or []
    return {"ticker": ticker, "name": name, "pos": pos, "idea": idea, "series": series, "cur": cur,
            "peers": SCR.peer_comparison(ticker, universe),
            "changes": changes, "payouts": pays, "news": news, "ai": ai, "rules": rules}


class SecurityCard(QDialog):
    def __init__(self, win, ticker: str):
        super().__init__(win)
        self.win, self.ticker = win, ticker
        self.setWindowTitle(f"{ticker} — карточка бумаги")
        self.setModal(False)
        self.resize(1100, 760)
        self.lbl_head = QLabel(); self.lbl_head.setTextFormat(Qt.RichText); self.lbl_head.setWordWrap(True)
        self.chart = LineChart("Цена, год", "", zero_based=False)
        self.info = QTextBrowser(); self.info.setOpenExternalLinks(True)
        self.tbl_pay = win._table(["Отсечка", "Посл. день покупки", "Выплата", "На бумагу", "Сумма, ₽", "Ст."])
        self.tbl_news = win._table(["Новость", "Когда", "Источник"])
        for t in (self.tbl_pay, self.tbl_news):
            t.setSortingEnabled(False)               # порядок задан: выплаты — от новых, новости — свежие сверху
        self.tbl_news.cellDoubleClicked.connect(self._open_news)
        left = QSplitter(Qt.Vertical); left.addWidget(self.chart); left.addWidget(self.info)
        left.setStretchFactor(0, 2); left.setStretchFactor(1, 3)
        right = QSplitter(Qt.Vertical)
        for title, t in (("Выплаты", self.tbl_pay), ("Новости", self.tbl_news)):
            w = QWidget(); l_ = QVBoxLayout(w); l_.setContentsMargins(0, 0, 0, 0)
            l_.addWidget(QLabel(f"<b>{title}</b>")); l_.addWidget(t, 1); right.addWidget(w)
        body = QSplitter(Qt.Horizontal); body.addWidget(left); body.addWidget(right)
        body.setStretchFactor(0, 3); body.setStretchFactor(1, 2)
        lay = QVBoxLayout(self); top = QHBoxLayout(); top.addWidget(self.lbl_head, 1)
        lay.addLayout(top); lay.addWidget(body, 1)
        self.refresh()

    def refresh(self) -> None:
        d = card_data(self.win, self.ticker)
        self._news = d["news"]
        priv = self.win.privacy
        money = SY.money_fn(priv)
        pos, idea = d["pos"], d["idea"]
        chg = " · ".join(f"{PERIOD_LABELS[k]} {v:+.1f}%" for k, v in d["changes"].items() if v is not None and k in PERIOD_LABELS)
        where = "в портфеле" if pos else ("кандидат скринера" if idea else "нет в портфеле и среди кандидатов")
        cls = pc.CLASS_LABELS.get(pos.cls, pos.cls) if pos else ""
        sector = pc.sector_label(pos.sector) if pos else (idea.sector if idea else "")
        self.lbl_head.setText(f"<span style='font-size:18px'><b>{d['name']}</b></span> &nbsp; {d['ticker']} · {where}"
                              + (f" · {cls}" if cls else "") + (f" · {sector}" if sector else "")
                              + (f"<br>Цена <b>{d['cur']:,.2f}</b>" if d["cur"] else "") + (f" · {chg}" if chg else ""))

        start = (date.today() - timedelta(days=365)).isoformat()
        pts = [(x, v) for x, v in d["series"] if x >= start]
        self.chart.hide_values = False                 # цена бумаги — не личная сумма
        self.chart.set_data([f"{x[8:10]}.{x[5:7]}" for x, _ in pts], [("цена", [v for _, v in pts], "#534AB7", False)] if pts else [])

        html = []
        if pos:
            r = d["rules"]
            lo, hi = (r["corridor"] or (None, None))
            html += ["<h3>Позиция</h3>",
                     f"Количество {MASK if priv else f'{float(pos.quantity):,.0f}'} · средняя {float(pos.avg_price):,.2f} · "
                     f"стоимость {money(float(pos.value_rub))} ₽ · P/L {float(pos.pnl_pct):+.1f}%",
                     f"<br>Доля в портфеле <b>{r['share']:.1f}%</b>"
                     + (f" (лимит на эмитента {r['single_max']:.0f}%" + (" — " + SY.span("превышен", SY.BAD, bold=True) + ")"
                        if r["single_max"] and r["share"] > r["single_max"] else ")") if r["single_max"] else ""),
                     f"<br>Класс «{cls}»: {r['class_share']:.1f}%"
                     + (f", коридор {lo:.0f}–{hi:.0f}%" if lo is not None else "")]
            if r["frozen"]:
                html.append(f"<br>Заморожена: {r['frozen']}")
            if r["cap"]:
                html.append(f"<br>Лимит позиции: {money(float(r['cap']))} ₽")
            if float(pos.div_12m) > 0:
                html.append(f"<br>Выплаты за 12 мес: {float(pos.div_12m):,.2f} на бумагу после НДФЛ "
                            f"({float(pos.div_12m) / float(pos.cur_price) * 100 if pos.cur_price else 0:.1f}% к цене)")
        if idea:
            f = lambda v, fmt="{:.1f}": "—" if v is None else fmt.format(v)
            html += ["<h3>Фундаментал (T-Invest, из скринера)</h3>",
                     f"Капитализация {idea.cap_bln:,.0f} млрд ₽ · дивдоходность {idea.div_yield:.1f}% "
                     f"(средняя за 5 лет {f(idea.avg5y_div_yield, '{:.1f}%')}) · дивиденды {idea.div_per_share:,.2f} ₽",
                     f"<br>P/E {f(idea.pe)} · долг/EBITDA {f(idea.net_debt_ebitda)} · ROE {f(idea.roe, '{:.1f}%')} · "
                     f"free float {f(idea.free_float, '{:.0f}%')} · прошлая отсечка {idea.ex_div_date or '—'}"]
        pc_ = d["peers"]
        if pc_:
            fmtv = lambda v, f: "—" if v is None else f.format(v)
            mark = {1: SY.span("▲ благоприятнее медианы", SY.GOOD), -1: SY.span("▼ хуже медианы", SY.BAD), 0: ""}
            peers = ", ".join(pc_["peers"][:8]) + (" …" if len(pc_["peers"]) > 8 else "")
            html += [f"<h3>Сравнение с отраслью «{pc_['sector']}»</h3>",
                     f"<span style='color:gray'>Медиана по {len(pc_['peers'])} бумагам отрасли из последнего прохода "
                     f"скринера: {peers}</span>",
                     "<table cellspacing=6><tr><th align=left>метрика</th><th align=right>бумага</th>"
                     "<th align=right>медиана</th><th></th></tr>"]
            for label, v, med, sign, f in pc_["rows"]:
                html.append(f"<tr><td>{label}</td><td align=right>{fmtv(v, f)}</td><td align=right>{fmtv(med, f)}</td>"
                            f"<td>{mark[sign]}</td></tr>")
            html.append("</table>")
        if not idea and pos:
            html.append("<p style='color:gray'>Фундаментала нет: бумаги нет в кэше скринера (не TQBR или скринер ещё "
                        "не запускался — экран «Идеи» → «Найти»).</p>")
        if d["ai"]:
            mark = SY.sentiment_label(d["ai"].get("sentiment"))
            html += ["<h3>ИИ по новостям</h3>", f"{mark}. {d['ai'].get('note', '')}",
                     "<br><span style='color:gray'>Оценка заголовков моделью, не рекомендация.</span>"]
        html += self._consensus_html(d)
        self.info.setHtml("".join(html) or "<p style='color:gray'>Данных о бумаге пока нет.</p>")

        from screens.payouts import STATUS, STATUS_LEGEND
        from widgets import icon_column
        rows = [([x.record_date, x.last_buy_date or "—", x.payment_date or "—", f"{x.per_share:,.2f}",
                  money(x.amount_rub), STATUS[x.status]], None) for x in reversed(d["payouts"])]
        self.win._fill(self.tbl_pay, rows)
        icon_column(self.tbl_pay, 5, STATUS_LEGEND)
        self.win._fill(self.tbl_news, [([it.title, it.published.strftime("%d.%m %H:%M") if it.published else "",
                                         it.source], None) for it in d["news"][:50]])

    # ---- консенсус аналитиков (п. 26): справочно, с кэшем на сутки, загрузка в фоне ----

    def _forecast_uid(self, d) -> str:
        return (getattr(d["idea"], "uid", "") if d["idea"] else "") or (d["pos"].figi if d["pos"] else "")

    def _consensus_html(self, d) -> list[str]:
        import consensus as CS
        import paths
        uid = self._forecast_uid(d)
        if not uid or uid.startswith("demo-") or paths.DEMO_DIR is not None:
            return []
        cache = paths.cache_dir()
        data = getattr(self, "_forecast", None) or CS.load(cache, uid)
        if data is None:
            self._start_forecast(uid, cache)
            return ["<p style='color:gray'>Консенсус аналитиков загружается…</p>"]
        if data.get("error"):
            return [f"<p style='color:gray'>Консенсус аналитиков недоступен: {data['error']}</p>"]
        c = data.get("consensus")
        if not c:
            return ["<p style='color:gray'>Консенсус-прогноза аналитиков по бумаге нет.</p>"]
        f = lambda v: "—" if v is None else f"{v:,.2f}"
        rel = f" ({c['change_rel']:+.1f}% к текущей)" if c.get("change_rel") is not None else ""
        out = ["<h3>Консенсус аналитиков — справочно</h3>",
               f"{c['recommendation']} · целевая цена <b>{f(c['target'])}</b>{rel} · разброс {f(c['min'])}–{f(c['max'])}",
               "<br><span style='color:gray'>Мнения брокерских домов по данным T-Invest, не рекомендация программы. "
               "Решение — за тобой.</span>"]
        ts = [t for t in data.get("targets") or [] if t.get("target") is not None][:6]
        if ts:
            out.append("<br>" + "; ".join(f"{t['company']}: {f(t['target'])} ({t['recommendation']}, {t['date']})"
                                          for t in ts))
        return out

    def _start_forecast(self, uid: str, cache) -> None:
        from ui_common import get_token
        from workers import ForecastWorker
        token = get_token()
        if not token or is_running(getattr(self, "_fw", None)):
            return
        self._fw = activity.get().track(ForecastWorker(token, uid, cache, self), f"consensus:{uid}",
                                        "Консенсус аналитиков (T-Invest)",
                                        done_note=lambda d: (d or {}).get("error", "") if isinstance(d, dict) else "")
        self._fw.done.connect(lambda data: (setattr(self, "_forecast", data), self.refresh()))
        self._fw.start()

    def closeEvent(self, e) -> None:
        stop_workers([getattr(self, "_fw", None)], 15000, interrupt=False)     # не уничтожать живой поток (SIGABRT)
        super().closeEvent(e)

    def _open_news(self, r: int, _c: int) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if 0 <= r < len(self._news) and self._news[r].link:
            QDesktopServices.openUrl(QUrl(self._news[r].link))


class CardMixin:
    def open_card(self, ticker: str) -> None:
        if not ticker:
            return
        cards = getattr(self, "_cards", {})
        c = cards.get(ticker)
        if c is None:
            c = cards[ticker] = SecurityCard(self, ticker)
            self._cards = cards
        else:
            c.refresh()
        c.show(); c.raise_(); c.activateWindow()

    def _connect_cards(self) -> None:
        """Двойной клик по бумаге в таблицах → карточка. Тикер — во 2-й колонке этих таблиц."""
        tables = [getattr(self, n, None) for n in ("tbl_pos", "tbl_ideas", "tbl_payouts", "tbl_cand_div")]
        for t in tables:
            if isinstance(t, QTableWidget):
                t.cellDoubleClicked.connect(lambda r, _c, t=t: self.open_card(t.item(r, 1).text() if t.item(r, 1) else ""))
