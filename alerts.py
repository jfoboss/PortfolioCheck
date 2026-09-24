"""
alerts.py — оповещения-условия (BACKLOG п. 21, аналог ALRT у Bloomberg). Проверяются, пока программа открыта:
после каждого обновления портфеля, ленты и скринера. Решение пользователя: без фоновой службы.

Правило в конфиге (раздел `alerts`): {"kind", "ticker", "value", "on"}. Виды:
  price_above / price_below — цена бумаги (портфель или кандидат скринера) ≥ / ≤ значения;
  yield_above   — дивдоходность кандидата скринера ≥ значения, %;
  days_to_cut   — до последнего дня покупки под выплату (календарь) осталось ≤ N дней; без тикера — любая бумага;
  corridor      — доля класса вышла из целевого коридора (тикер не нужен);
  news          — свежая новость по бумаге; без тикера — по любой бумаге портфеля;
  cbr_meeting   — до заседания ЦБ по ключевой ставке осталось ≤ N дней (тикер не нужен; календарь — macro.py).
Оповещение приходит при переходе «не выполнено → выполнено» (состояние — cache/alerts_state.json), поэтому не
повторяется на каждой перерисовке; новость и отсечка — по одному разу на событие. Формулировки — «условие
выполнено», без советов (правило 3 проекта).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import jsoncache as JC

KINDS = {
    "price_above": "Цена ≥",
    "price_below": "Цена ≤",
    "yield_above": "Дивдоходность кандидата ≥, %",
    "days_to_cut": "До последнего дня покупки ≤, дней",
    "corridor": "Класс вышел из коридора",
    "news": "Новость по бумаге",
    "cbr_meeting": "До заседания ЦБ по ставке ≤, дней",
}
STATE_FILE = "alerts_state.json"
NEWS_FRESH_H = 24                      # новости старше — не повод будить


@dataclass
class Hit:
    key: str                           # идентичность события: одно событие — одно уведомление
    text: str


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def evaluate(rules: list[dict], positions=(), ideas=(), payouts=(), class_status=None, news=(),
             today: date | None = None, now: datetime | None = None, macro_events=()) -> list[Hit]:
    """Все выполненные сейчас условия. class_status — {класс: (доля %, min, max)}; payouts — payouts.Payout;
    macro_events — macro.MacroEvent."""
    today = today or date.today()
    now = now or datetime.now()
    price = {p.ticker: float(p.cur_price) for p in positions if float(p.cur_price or 0) > 0}
    for i in ideas:
        price.setdefault(i.ticker, float(i.price))
    yld = {i.ticker: float(i.div_yield) for i in ideas}
    mine = {p.ticker for p in positions}
    hits: list[Hit] = []
    for n, r in enumerate(rules):
        if not r.get("on", True):
            continue
        kind, t, v = r.get("kind"), str(r.get("ticker") or "").strip().upper(), _num(r.get("value"))
        rid = f"{n}:{kind}:{t}:{v:g}"
        if kind in ("price_above", "price_below") and t in price:
            cur = price[t]
            if (cur >= v) if kind == "price_above" else (cur <= v):
                sign = "≥" if kind == "price_above" else "≤"
                hits.append(Hit(rid, f"{t}: цена {cur:,.2f} {sign} {v:,.2f} — условие выполнено"))
        elif kind == "yield_above" and t in yld and yld[t] >= v:
            hits.append(Hit(rid, f"{t}: дивдоходность {yld[t]:.1f}% ≥ {v:.1f}% — условие выполнено"))
        elif kind == "days_to_cut":
            for x in payouts:
                if x.status == "paid" or (t and x.ticker != t):
                    continue
                cut = date.fromisoformat(x.last_buy_date or x.record_date)
                left = (cut - today).days
                if 0 <= left <= v:
                    hits.append(Hit(f"{rid}:{x.ticker}:{x.record_date}",
                                    f"{x.ticker}: до последнего дня покупки под выплату {left} дн. ({cut:%d.%m}), "
                                    f"{x.per_share:,.2f} на бумагу" + (" — ожидается, не объявлена" if x.status == "expected" else "")))
        elif kind == "corridor":
            for cls, (share, lo, hi) in (class_status or {}).items():
                if share < lo or share > hi:
                    side = "ниже" if share < lo else "выше"
                    hits.append(Hit(f"{rid}:{cls}:{side}", f"Класс «{cls}»: {share:.1f}% — {side} коридора {lo:g}–{hi:g}%"))
        elif kind == "cbr_meeting":
            for e in macro_events:
                left = (date.fromisoformat(e.date) - today).days
                if e.kind == "meeting" and 0 <= left <= v:
                    hits.append(Hit(f"{rid}:{e.date}", f"Заседание ЦБ по ключевой ставке {date.fromisoformat(e.date):%d.%m} — "
                                                       f"через {left} дн." + (" (с новым среднесрочным прогнозом)" if e.forecast else "")))
                    break                                    # только ближайшее
        elif kind == "news":
            for it in news:
                tick = [x for x in it.matched if (x == t if t else x in mine)]
                pub = it.published
                if pub is not None and pub.tzinfo is not None:
                    pub = pub.astimezone().replace(tzinfo=None)      # в местное время, как now
                if not tick or pub is None or now - pub > timedelta(hours=NEWS_FRESH_H):
                    continue
                h = hashlib.sha1(it.title.encode("utf-8")).hexdigest()[:10]
                hits.append(Hit(f"{rid}:{h}", f"{', '.join(tick)}: {it.title}"))
    return hits


def new_hits(hits: list[Hit], cache_dir: Path) -> list[Hit]:
    """Только те, что появились с прошлой проверки. Состояние — множество выполненных сейчас ключей: условие, которое
    перестало выполняться, снова «взводится» и сообщит при следующем выполнении."""
    f = cache_dir / STATE_FILE
    prev = set((JC.read(f) or {}).get("active", []))
    cur = {h.key for h in hits}
    try:
        JC.write(f, {"active": sorted(cur), "at": datetime.now().isoformat(timespec="seconds")})
    except OSError:
        pass
    return [h for h in hits if h.key not in prev]


def describe(rule: dict) -> str:
    t = rule.get("ticker") or "любая бумага"
    kind = rule.get("kind")
    if kind == "corridor":
        return KINDS[kind]
    if kind == "cbr_meeting":
        return f"{KINDS[kind]} {rule.get('value', '')}".strip()
    return f"{KINDS.get(kind, kind)} {rule.get('value', '')} — {t}".strip()
