"""
attribution.py — откуда изменение стоимости портфеля за период (BACKLOG п. 18, аналог PORT → attribution).

Между двумя снапшотами изменение раскладывается точно, без остатка:
    ΔV = Σ q0 · (p1 − p0)        переоценка — то, что сделал рынок с бумагами, которые уже были
       + Σ (q1 − q0) · p1        покупки и продажи (нетто) по цене конца периода — пополнения и реинвест
где q — количество, p — цена в рублях (стоимость / количество). Новые бумаги — только во втором слагаемом,
проданные целиком — вклад −q0·p0 во второе и переоценка до продажи неизвестна (снапшоты дискретны).

Отдельно, справочно — выплаты за период из календаря (payouts.py): они либо лежат деньгами, либо уже вошли в
покупки как реинвест; разделить пополнения и реинвест без истории операций брокера нельзя — это честно пишется
в подписи. Модуль без Qt — проверяется тестами.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass
class Attribution:
    start: str
    end: str
    v0: float
    v1: float
    revaluation: float
    flows: float                                        # покупки − продажи (нетто)
    payouts: float = 0.0                                # выплаты с отсечкой в периоде (справочно)
    by_class: dict = field(default_factory=dict)        # класс → (переоценка, покупки нетто)
    top: list = field(default_factory=list)             # [(тикер, имя, переоценка)] по модулю, крупнейшие

    @property
    def change(self) -> float:
        return self.v1 - self.v0


def _rows(path: Path) -> dict[str, dict]:
    return {r["ticker"]: r for r in json.loads(path.read_text(encoding="utf-8"))}


def _qp(r: dict | None) -> tuple[float, float]:
    """(количество, цена в ₽ за штуку). Цена — из стоимости: учитывает валюту и курс на дату снапшота."""
    if not r:
        return 0.0, 0.0
    q = float(r.get("quantity") or 0)
    v = float(r.get("value_rub") or 0)
    return q, (v / q if q else 0.0)


def attribute(rows0: dict[str, dict], rows1: dict[str, dict], classes: dict[str, str] | None = None,
              start: str = "", end: str = "", top_n: int = 8) -> Attribution:
    """Разложение между двумя наборами позиций (тикер → строка снапшота). classes — текущие классы по тикеру
    (класс мог смениться между снапшотами; считаем по текущему, как в остальном интерфейсе)."""
    classes = classes or {}
    reval = flows = 0.0
    by_class: dict[str, list[float]] = {}
    per = []
    for t in set(rows0) | set(rows1):
        q0, p0 = _qp(rows0.get(t)); q1, p1 = _qp(rows1.get(t))
        if not rows1.get(t):                  # продана целиком: цены конца нет, весь уход — во «продажи»
            r_, f_ = 0.0, -q0 * p0
        else:
            r_, f_ = q0 * (p1 - p0), (q1 - q0) * p1
        reval += r_; flows += f_
        cls = classes.get(t) or (rows1.get(t) or rows0.get(t) or {}).get("cls") or "unclassified"
        acc = by_class.setdefault(cls, [0.0, 0.0]); acc[0] += r_; acc[1] += f_
        name = (rows1.get(t) or rows0.get(t) or {}).get("name", t)
        per.append((t, name, r_))
    per.sort(key=lambda x: -abs(x[2]))
    v0 = sum(float(r.get("value_rub") or 0) for r in rows0.values())
    v1 = sum(float(r.get("value_rub") or 0) for r in rows1.values())
    return Attribution(start, end, v0, v1, reval, flows, 0.0,
                       {c: (round(a, 2), round(b, 2)) for c, (a, b) in by_class.items()},
                       [x for x in per if abs(x[2]) > 0.5][:top_n])


def pick_base(snap_dir: Path, days: int | None, today: date | None = None) -> Path | None:
    """Снапшот начала периода: последний не позже (сегодня − days); days=None — самый первый.
    Если раньше границы снапшотов нет — самый ранний из имеющихся (период короче запрошенного — видно по дате)."""
    files = sorted(snap_dir.glob("*.json"))
    if len(files) < 2:
        return None
    if days is None:
        return files[0]
    bound = ((today or date.today()) - timedelta(days=days)).isoformat()
    before = [f for f in files[:-1] if f.stem[:10] <= bound]
    return before[-1] if before else files[0]


def for_period(snap_dir: Path, days: int | None, classes: dict[str, str] | None = None,
               payouts=None, today: date | None = None) -> Attribution | None:
    """Атрибуция от снапшота начала периода до последнего. None — снапшотов меньше двух."""
    base = pick_base(snap_dir, days, today)
    if base is None:
        return None
    last = sorted(snap_dir.glob("*.json"))[-1]
    if base == last:
        return None
    a = attribute(_rows(base), _rows(last), classes, base.stem[:10], last.stem[:10])
    a.payouts = round(sum(x.amount_rub for x in (payouts or [])
                          if x.status == "paid" and a.start < x.record_date <= a.end), 2)
    return a


PERIODS = [("Месяц", 31), ("Квартал", 92), ("Год", 365), ("Всё время", None)]
