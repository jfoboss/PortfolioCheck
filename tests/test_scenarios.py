"""Сценарии (scenarios.py) и загрузка факторов (factors.py): регрессия, применение, валюта, разбор ISS и ЦБ."""
import json
import math
from datetime import date, timedelta
from types import SimpleNamespace as NS

import factors as FA
import scenarios as SC

TODAY = date(2026, 9, 24)


def ser(fn, n=300):
    d0 = TODAY - timedelta(days=n)
    return [((d0 + timedelta(days=k)).isoformat(), fn(k)) for k in range(n + 1)]


def test_ols_recovers_known_betas():
    import random
    rnd = random.Random(1)
    x1 = [rnd.gauss(0, 0.01) for _ in range(300)]
    x2 = [rnd.gauss(0, 0.005) for _ in range(300)]
    y = [1.5 * a - 0.8 * b + rnd.gauss(0, 0.0005) for a, b in zip(x1, x2)]
    b1, b2, r2 = SC.ols2(y, x1, x2)
    assert abs(b1 - 1.5) < 0.05 and abs(b2 + 0.8) < 0.1 and r2 > 0.95


def test_betas_and_scenario():
    import random
    rnd = random.Random(2)
    idx_lvl, usd_lvl, a_lvl = [1000.0], [90.0], [100.0]
    for _ in range(300):
        ri, rf = rnd.gauss(0, 0.01), rnd.gauss(0, 0.005)
        idx_lvl.append(idx_lvl[-1] * math.exp(ri)); usd_lvl.append(usd_lvl[-1] * math.exp(rf))
        a_lvl.append(a_lvl[-1] * math.exp(2.0 * ri + rnd.gauss(0, 0.0005)))
    idx = ser(lambda k: idx_lvl[k]); usd = ser(lambda k: usd_lvl[k]); a = ser(lambda k: a_lvl[k])
    pos = [NS(ticker="A", name="A", figi="A", value_rub=1000, cls="equity_core"),
           NS(ticker="USD000UTSTOM", name="Доллар", figi="U", value_rub=500, cls="cash"),
           NS(ticker="GOLD", name="Золото", figi="G", value_rub=300, cls="gold")]
    bs, missing = SC.betas(pos, {"A": a}, idx, usd, today=TODAY)
    assert missing == ["GOLD"]                                         # без истории — не ноль, а в списке
    beta = {b.ticker: b for b in bs}
    assert abs(beta["A"].bm - 2.0) < 0.1 and beta["USD000UTSTOM"].bfx == 1.0
    r = SC.apply(bs, -10, 20, missing)
    assert abs(r.rows[0][3] - ((0.9 ** beta["A"].bm * 1.2 ** beta["A"].bfx) - 1) * 100) < 1e-6
    usd_row = next(x for x in r.rows if x[0] == "USD000UTSTOM")
    assert abs(usd_row[3] - 20) < 1e-9                                  # валюта — ровно по курсу
    assert r.total == 1500 and abs(sum(v for *_x, v, _p in r.rows) - r.change) < 1e-6


def test_parsers_and_fetch_all_with_stub(tmp_path):
    iss_page = lambda start: json.dumps({"history": {"columns": ["TRADEDATE", "CLOSE"], "data": (
        [[f"2026-01-{i % 28 + 1:02d}", 3000 + i] for i in range(100)] if start == 0 else [["2026-09-01", 2900.0]])}}).encode()
    cbr = ('<ValCurs><Record Date="02.09.2026" Id="R01235"><Nominal>1</Nominal><Value>84,0700</Value></Record>'
           '<Record Date="03.09.2026" Id="R01235"><Nominal>1</Nominal><Value>84,5</Value></Record></ValCurs>').encode("cp1251")
    urls = []

    def fetch(url):
        urls.append(url)
        if "iss.moex.com" in url:
            return iss_page(int(url.split("start=")[1].split("&")[0]))
        return cbr
    st = FA.fetch_all(tmp_path, fetch=fetch, today=TODAY)
    assert st == {"IMOEX": "ok", "USDRUB": "ok"}
    assert sum("iss.moex.com" in u for u in urls) == 2                  # постраничная загрузка ISS
    assert FA.load(tmp_path, FA.USDRUB) == [("2026-09-02", 84.07), ("2026-09-03", 84.5)]
    assert ("2026-09-01", 2900.0) in FA.load(tmp_path, FA.IMOEX)
    urls.clear()
    FA.fetch_all(tmp_path, fetch=fetch, today=TODAY)
    assert urls == []                                                   # свежий кэш — в сеть не ходим

    def broken(url):
        raise OSError("403")
    st = FA.fetch_all(tmp_path / "x", fetch=broken, today=TODAY)
    assert st["IMOEX"].startswith("OSError") and st["USDRUB"].startswith("OSError")
