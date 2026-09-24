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


def _rss(*items):
    body = "".join(f"<item><title>{t}</title><link>https://n/{i}</link><pubDate>{d}</pubDate></item>"
                   for i, (t, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode()


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_news_matches_dedups_sorts_and_reports_failed(monkeypatch):
    """fetch_news с подменой сети: привязка к бумагам, дубли из разных лент, порядок, недоступные источники,
    повтор с браузерными заголовками на 403."""
    import urllib.error
    import urllib.request
    feeds = {
        "https://a.example/rss": _rss(("Сбербанк отчитался за квартал", "Fri, 19 Sep 2026 10:00:00 +0300"),
                                      ("Погода в Москве", "Fri, 19 Sep 2026 11:00:00 +0300")),
        "https://b.example/rss": _rss(("Сбербанк отчитался за квартал", "Fri, 19 Sep 2026 10:05:00 +0300"),
                                      ("МТС запускает новый тариф", "Fri, 19 Sep 2026 12:00:00 +0300")),
        "https://blocked.example/rss": _rss(("Фосагро увеличила экспорт", "Fri, 19 Sep 2026 09:00:00 +0300")),
    }
    seen = []

    def fake_urlopen(req, timeout=0, context=None):
        seen.append((req.full_url, req.get_header("Referer")))
        if req.full_url == "https://down.example/rss":
            raise urllib.error.URLError("нет соединения")
        if req.full_url == "https://blocked.example/rss" and not req.get_header("Referer"):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        return _Resp(feeds[req.full_url])
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    positions = [NS(ticker="SBERP", name="Сбербанк-п"), NS(ticker="MTSS", name="МТС"), NS(ticker="PHOR", name="ФосАгро")]
    items, failed = N.fetch_news(list(feeds) + ["https://down.example/rss"], positions, {})
    assert failed == ["https://down.example/rss"]
    assert [i.title for i in items] == ["МТС запускает новый тариф", "Сбербанк отчитался за квартал",
                                        "Фосагро увеличила экспорт"]              # новые сверху, дубль один раз
    assert items[0].matched == ["MTSS"] and "SBERP" in items[1].matched
    assert ("https://blocked.example/rss", "https://blocked.example/") in seen   # повтор с Referer после 403


def test_short_aliases_match_whole_words_only():
    """Регресс: алиасы короче 4 символов («МТС», «ВТБ», «VK») отбрасывались — новости этих компаний не
    привязывались к бумагам. Теперь работают, но только целым словом."""
    kw = N.build_keywords([NS(ticker="MTSS", name="МТС"), NS(ticker="VTBR", name="Банк ВТБ")], N.DEFAULT_ALIASES)
    assert "МТС" in kw["MTSS"] and "ВТБ" in kw["VTBR"]
    items = [N.NewsItem("МТС запускает тариф", "", "s", None, []), N.NewsItem("ВТБ снизил ставки", "", "s", None, []),
             N.NewsItem("Акции ГМТС и прочее", "", "s", None, [])]
    got = {i.title: i.matched for i in N.match(items, kw)}
    assert got["МТС запускает тариф"] == ["MTSS"] and got["ВТБ снизил ставки"] == ["VTBR"]
    assert "Акции ГМТС и прочее" not in got
