"""Атрибуция изменения стоимости (attribution.py): точное разложение, выбор базы, выплаты периода."""
import json
from datetime import date

import attribution as AT
import payouts as PO


def R(t, q, price, cls="equity_core"):
    return {"ticker": t, "name": t, "quantity": str(q), "value_rub": str(q * price), "cls": cls}


def test_decomposition_is_exact():
    r0 = {x["ticker"]: x for x in [R("A", 100, 10), R("B", 50, 20, "gold"), R("SOLD", 10, 5)]}
    r1 = {x["ticker"]: x for x in [R("A", 120, 12), R("B", 50, 18, "gold"), R("NEW", 5, 100)]}
    a = AT.attribute(r0, r1)
    assert abs(a.revaluation + a.flows - a.change) < 1e-6          # без остатка
    assert a.revaluation == 100 * 2 + 50 * (-2)                    # A +200, B −100, SOLD — 0
    assert a.flows == 20 * 12 + 5 * 100 - 10 * 5                   # докупка A, новая NEW, продажа SOLD
    assert a.by_class["gold"] == (-100, 0) and a.top[0][0] == "A"


def _snap(d, name, rows):
    (d / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")


def test_period_base_and_payouts(tmp_path):
    _snap(tmp_path, "2026-01-01", [R("A", 100, 10)])
    _snap(tmp_path, "2026-06-01", [R("A", 110, 11)])
    _snap(tmp_path, "2026-09-24", [R("A", 120, 12)])
    today = date(2026, 9, 24)
    assert AT.pick_base(tmp_path, 31, today).stem == "2026-06-01"   # последний не позже границы
    assert AT.pick_base(tmp_path, None, today).stem == "2026-01-01"
    assert AT.pick_base(tmp_path, 10, today).stem == "2026-06-01"   # раньше границы нет новее — берём ближайший
    pays = [PO.Payout("A", "A", "2026-07-10", "", "", 1.0, "RUB", 110, 110.0, "paid"),
            PO.Payout("A", "A", "2026-03-10", "", "", 1.0, "RUB", 100, 100.0, "paid"),
            PO.Payout("A", "A", "2026-10-10", "", "", 1.0, "RUB", 120, 120.0, "declared")]
    a = AT.for_period(tmp_path, 31, payouts=pays, today=today)
    assert (a.start, a.end) == ("2026-06-01", "2026-09-24") and a.payouts == 110.0
    assert a.change == 120 * 12 - 110 * 11


def test_needs_two_snapshots(tmp_path):
    _snap(tmp_path, "2026-09-24", [R("A", 1, 1)])
    assert AT.for_period(tmp_path, 365) is None
