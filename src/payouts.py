"""
payouts.py — календарь выплат и денежного потока (BACKLOG п. 16, аналог DVD/CACS у Bloomberg).

Источник — T-Invest `GetDividends`: прошлые выплаты и уже объявленные будущие (с датой отсечки и последним днём
покупки). Где будущая выплата ещё не объявлена, показываем «ожидается» — прошлогоднюю выплату, сдвинутую на год.
Это честная эвристика, а не прогноз: в интерфейсе такие строки помечены отдельно.

Модуль без Qt: расчёты и кэш (`cache/payouts.json`) проверяются тестами.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import jsoncache as JC

NDFL = 0.87                 # выплата после НДФЛ 13% — как в доходе за 12 месяцев
CACHE_FILE = "payouts.json"


@dataclass
class Payout:
    ticker: str
    name: str
    record_date: str             # дата отсечки (ISO)
    last_buy_date: str           # последний день покупки под выплату (ISO, может быть пусто)
    payment_date: str            # ожидаемая дата выплаты (ISO, может быть пусто)
    per_share: float             # на одну бумагу, в валюте инструмента, после НДФЛ
    currency: str
    quantity: float
    amount_rub: float            # per_share × количество × курс
    status: str                  # declared — объявлена | expected — по прошлому году | paid — уже была

    @property
    def when(self) -> date:
        """Дата для календаря: выплата, если известна, иначе отсечка."""
        return date.fromisoformat(self.payment_date or self.record_date)


def _d(ts) -> str:
    """Timestamp из SDK (datetime или None/нулевая дата) → ISO-дата или ''."""
    if not ts:
        return ""
    try:
        d = ts.date() if isinstance(ts, datetime) else ts
        return "" if d.year < 1971 else d.isoformat()
    except Exception:  # noqa: BLE001
        return ""


def from_dividends(p, divs, fx_rate: float, money, today: date | None = None) -> list[Payout]:
    """Ответ GetDividends по одной позиции → выплаты (прошлые — paid, будущие — declared).
    money — функция MoneyValue → Decimal из SDK."""
    today = today or date.today()
    out = []
    for dv in divs:
        rec = _d(getattr(dv, "record_date", None))
        if not rec:
            continue
        per = float(money(dv.dividend_net)) * NDFL
        if per <= 0:
            continue
        qty = float(p.quantity)
        out.append(Payout(p.ticker, p.name, rec, _d(getattr(dv, "last_buy_date", None)),
                          _d(getattr(dv, "payment_date", None)), round(per, 6), p.currency, qty,
                          round(per * qty * fx_rate, 2), "paid" if date.fromisoformat(rec) <= today else "declared"))
    return out


def with_expected(payouts: list[Payout], today: date | None = None, horizon_days: int = 365) -> list[Payout]:
    """Добавляет «ожидается»: прошлогодние выплаты +1 год, если в окне ±60 дней от этой даты у бумаги нет
    объявленной. Только в горизонте [сегодня, сегодня + horizon_days]."""
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    declared = [x for x in payouts if x.status == "declared"]
    out = list(payouts)
    for x in payouts:
        if x.status != "paid":
            continue
        try:
            nxt = date.fromisoformat(x.record_date).replace(year=date.fromisoformat(x.record_date).year + 1)
        except ValueError:                           # 29 февраля
            nxt = date.fromisoformat(x.record_date) + timedelta(days=365)
        if not (today < nxt <= end):
            continue
        if any(d.ticker == x.ticker and abs((date.fromisoformat(d.record_date) - nxt).days) <= 60 for d in declared):
            continue
        pay = ""
        if x.payment_date:
            pay = (nxt + (date.fromisoformat(x.payment_date) - date.fromisoformat(x.record_date))).isoformat()
        out.append(Payout(x.ticker, x.name, nxt.isoformat(), "", pay, x.per_share, x.currency, x.quantity,
                          x.amount_rub, "expected"))
    return out


def upcoming(payouts: list[Payout], today: date | None = None, horizon_days: int = 365) -> list[Payout]:
    """Будущие события по дате отсечки, ближайшие сверху."""
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    fut = [x for x in payouts if x.status != "paid" and today <= date.fromisoformat(x.record_date) <= end]
    return sorted(fut, key=lambda x: (x.record_date, x.ticker))


def monthly_cashflow(payouts: list[Payout], today: date | None = None, months: int = 12) -> list[tuple[str, float, float]]:
    """Денежный поток по месяцам выплаты: [(«2026-10», объявлено ₽, ожидается ₽)] на months вперёд, с текущего."""
    today = today or date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    acc = {k: [0.0, 0.0] for k in keys}
    for x in payouts:
        if x.status == "paid" or x.when < today:
            continue
        k = x.when.strftime("%Y-%m")
        if k in acc:
            acc[k][0 if x.status == "declared" else 1] += x.amount_rub
    return [(k, round(a, 2), round(b, 2)) for k, (a, b) in acc.items()]


def save(payouts: list[Payout], cache_dir: Path) -> None:
    JC.write(cache_dir / CACHE_FILE, {"at": datetime.now().isoformat(timespec="seconds"),
                                      "items": [asdict(x) for x in payouts]})


def load(cache_dir: Path) -> tuple[list[Payout], str]:
    """Кэш с диска — календарь показывается при старте сразу, без сети. ([], '') если кэша нет или он битый."""
    try:
        raw = JC.read(cache_dir / CACHE_FILE) or {}
        return [Payout(**r) for r in raw.get("items", [])], raw.get("at", "")
    except Exception:  # noqa: BLE001
        return [], ""


CAND_FILE = "candidate_payouts.json"


def next_payout(items: list[Payout], today: date | None = None) -> Payout | None:
    """Ближайшая будущая выплата по одной бумаге: объявленная, а если её нет — ожидаемая по прошлому году."""
    today = today or date.today()
    ahead = [x for x in with_expected(items, today) if x.status != "paid" and date.fromisoformat(x.record_date) >= today]
    return min(ahead, key=lambda x: x.record_date) if ahead else None


def save_candidates(by_ticker: dict[str, Payout], cache_dir: Path) -> None:
    """Ближайшие выплаты кандидатов скринера (GetDividends) — чтобы «Календарь» показывал их при старте сразу."""
    JC.write(cache_dir / CAND_FILE, {"at": datetime.now().isoformat(timespec="seconds"),
                                     "items": {t: asdict(x) for t, x in by_ticker.items()}})


def load_candidates(cache_dir: Path) -> dict[str, Payout]:
    try:
        return {t: Payout(**r) for t, r in ((JC.read(cache_dir / CAND_FILE) or {}).get("items") or {}).items()}
    except TypeError:
        return {}


MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"
