from datetime import date, timedelta

import history as H
import news as N
from types import SimpleNamespace as NS


def series(rate: float, years: int, cur: float = 100.0):
    today = date.today()
    return [((today - timedelta(days=d)).strftime("%Y-%m-%d"), cur / rate ** (d / 365)) for d in range(years * 365, -1, -1)]


def test_cagr_matches_constant_growth():
    s = series(1.10, 5)
    for yrs in (1, 3, 5):
        v, cov = H.cagr(s, 100.0, yrs)
        assert abs(v - 10.0) < 0.05 and abs(cov - yrs) < 0.05


def test_cagr_short_history_reports_real_depth():
    v, cov = H.cagr(series(1.10, 5)[-int(1.5 * 365):], 100.0, 5)
    assert abs(v - 10.0) < 0.1 and cov < 2


def test_parse_rss_and_atom():
    rss = b"<rss><channel><item><title>A</title><link>x</link><pubDate>Fri, 19 Sep 2026 05:00:00 +0300</pubDate></item></channel></rss>"
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title><link href="y"/>'
            b"<updated>2026-09-19T05:00:00Z</updated></entry></feed>")
    assert [i.title for i in N.parse_feed(rss, "s")] == ["A"]
    it = N.parse_feed(atom, "s")[0]
    assert it.title == "B" and it.link == "y" and it.published is not None


def test_keywords_skip_generic_words():
    """Регресс: «Банк Санкт-Петербург» ловил все новости про банки; ЗПИФ ищутся по имени УК."""
    kw = N.build_keywords([NS(ticker="BSPB", name="Банк Санкт-Петербург"), NS(ticker="RU000A0DEMO1", name="ЗПИФ Парус-СБЛ")], {})
    assert "Банк" not in kw["BSPB"] and any("Банк Санкт" in w for w in kw["BSPB"])
    assert kw["RU000A0DEMO1"] == ["Парус"]
