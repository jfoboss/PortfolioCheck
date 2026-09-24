"""Календарь выплат (payouts.py): разбор GetDividends, «ожидается» по прошлому году, поток по месяцам, кэш."""
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import payouts as PO

TODAY = date(2026, 9, 24)
POS = NS(ticker="SBERP", name="Сбербанк-п", quantity=Decimal(100), currency="RUB")


def dv(rec, net, last=None, pay=None):
    ts = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if s else None
    return NS(record_date=ts(rec), last_buy_date=ts(last), payment_date=ts(pay), dividend_net=net)


def test_from_dividends_splits_paid_and_declared_after_tax():
    got = PO.from_dividends(POS, [dv("2026-07-18", 30.0, "2026-07-17", "2026-07-30"), dv("2026-11-10", 20.0),
                                  dv("1970-01-01", 5.0), dv("2026-12-01", 0)],
                            1.0, lambda m: Decimal(str(m)), TODAY)
    assert [(p.record_date, p.status) for p in got] == [("2026-07-18", "paid"), ("2026-11-10", "declared")]
    assert got[0].per_share == 30 * 0.87 and got[0].amount_rub == round(30 * 0.87 * 100, 2)
    assert got[0].last_buy_date == "2026-07-17" and got[1].payment_date == ""


def _p(rec, status, t="SBERP", amount=1000.0, pay=""):
    return PO.Payout(t, t, rec, "", pay, 10.0, "RUB", 100, amount, status)


def test_expected_from_last_year_unless_declared_nearby():
    paid = [_p("2025-10-20", "paid", pay="2025-11-03"), _p("2025-12-15", "paid", t="LKOH"), _p("2025-05-01", "paid", t="MTSS")]
    declared = [_p("2026-10-25", "declared")]                    # у SBERP уже объявлена рядом — ожидаемую не добавляем
    out = PO.with_expected(paid + declared, TODAY)
    exp = [(x.ticker, x.record_date, x.payment_date) for x in out if x.status == "expected"]
    assert ("LKOH", "2026-12-15", "") in exp
    assert not any(t == "SBERP" for t, *_ in exp)
    assert not any(t == "MTSS" for t, *_ in exp)                 # 2026-05-01 уже прошла — не в горизонте
    out2 = PO.with_expected([_p("2025-10-20", "paid", pay="2025-11-03")], TODAY)
    assert [(x.record_date, x.payment_date) for x in out2 if x.status == "expected"] == [("2026-10-20", "2026-11-03")]


def test_upcoming_and_monthly_cashflow():
    items = [_p("2026-10-01", "declared", amount=500, pay="2026-10-15"), _p("2026-11-20", "expected", t="LKOH", amount=300),
             _p("2026-06-01", "paid", t="OLD"), _p("2028-01-01", "declared", t="FAR")]
    assert [x.ticker for x in PO.upcoming(items, TODAY)] == ["SBERP", "LKOH"]
    flow = dict((k, (a, b)) for k, a, b in PO.monthly_cashflow(items, TODAY))
    assert len(flow) == 12 and list(flow)[0] == "2026-09"
    assert flow["2026-10"] == (500, 0) and flow["2026-11"] == (0, 300)
    assert PO.month_label("2026-11") == "ноя 2026"


def test_cache_roundtrip_and_broken_file(tmp_path):
    items = [_p("2026-10-01", "declared")]
    PO.save(items, tmp_path)
    got, at = PO.load(tmp_path)
    assert got == items and at
    (tmp_path / PO.CACHE_FILE).write_text("{битый", encoding="utf-8")
    assert PO.load(tmp_path) == ([], "")
