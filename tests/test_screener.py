"""screener.apply_filters: каждый фильтр по отдельности, «0 — не фильтровать» и пропуски в данных."""
import screener as S


def idea(t, dy=10.0, cap=100.0, debt=1.0, avg5=8.0, pe=5.0, ff=30.0, sector="Нефть и газ"):
    return S.Idea(t, t, sector, 100.0, cap, dy, dy, pe, debt, 20.0, ff, "", avg5)


def tickers(ideas, **kw):
    f = S.Filters(**{"min_yield": 8.0, "min_cap_bln": 50.0, "max_debt_ebitda": 3.0, "min_avg5y_yield": 4.0, **kw})
    return [i.ticker for i in S.apply_filters(ideas, f, set())]


def test_basic_thresholds_and_sort():
    ideas = [idea("LOWY", dy=7.9), idea("SMALL", cap=49), idea("OK1", dy=9), idea("OK2", dy=12)]
    assert tickers(ideas) == ["OK2", "OK1"]                          # по убыванию дивдоходности


def test_exclude_set():
    got = S.apply_filters([idea("A"), idea("B")], S.Filters(), {"A"})
    assert [i.ticker for i in got] == ["B"]


def test_debt_filter_zero_disables_and_none_passes():
    ideas = [idea("HIGH", debt=3.5), idea("NODATA", debt=None), idea("OK", debt=2.9)]
    assert set(tickers(ideas)) == {"NODATA", "OK"}                    # нет данных о долге — не отсекаем
    assert set(tickers(ideas, max_debt_ebitda=0)) == {"HIGH", "NODATA", "OK"}


def test_avg5y_filter_cuts_one_off_payers():
    ideas = [idea("ONCE", avg5=None), idea("LOW", avg5=3.9), idea("STEADY", avg5=4.0)]
    assert tickers(ideas) == ["STEADY"]                               # пропуск средней = разовый плательщик
    assert set(tickers(ideas, min_avg5y_yield=0)) == {"ONCE", "LOW", "STEADY"}


def test_pe_filter_rejects_losses():
    ideas = [idea("CHEAP", pe=4), idea("PRICEY", pe=12), idea("LOSS", pe=-3), idea("NOPE", pe=None)]
    assert set(tickers(ideas, max_pe=10)) == {"CHEAP", "NOPE"}        # отрицательный P/E — убыток, не «дёшево»
    assert set(tickers(ideas)) == {"CHEAP", "PRICEY", "LOSS", "NOPE"}  # 0 — не фильтровать


def test_free_float_and_sectors():
    ideas = [idea("THIN", ff=5), idea("NOFF", ff=None), idea("IT1", sector="IT"), idea("OK")]
    assert set(tickers(ideas, min_free_float=10)) == {"IT1", "OK"}
    assert set(tickers(ideas, exclude_sectors=("IT",))) == {"THIN", "NOFF", "OK"}
