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


# ---- кэш свечей: глубина истории ----------------------------------------------------------------

def _fake_sdk(monkeypatch):
    import sys
    schemas = NS(CandleInterval=NS(CANDLE_INTERVAL_DAY=5))
    utils = NS(quotation_to_decimal=lambda q: q)
    monkeypatch.setitem(sys.modules, "tinkoff", NS(invest=NS(schemas=schemas, utils=utils)))
    monkeypatch.setitem(sys.modules, "tinkoff.invest", NS(schemas=schemas, utils=utils))
    monkeypatch.setitem(sys.modules, "tinkoff.invest.schemas", schemas)
    monkeypatch.setitem(sys.modules, "tinkoff.invest.utils", utils)


class FakeMarket:
    """get_candles с сигнатурой SDK (только именованные); свечи есть с даты `listed`."""
    def __init__(self, listed_days_ago: int):
        from datetime import datetime, timezone
        self.listed = datetime.now(timezone.utc) - timedelta(days=listed_days_ago)
        self.calls = []

    def get_candles(self, *, from_=None, to=None, interval=None, figi="", instrument_id=""):
        self.calls.append((from_, to))
        out, d = [], max(from_, self.listed)
        while d < to:
            out.append(NS(time=d, close=100.0)); d += timedelta(days=1)
        return NS(candles=out)


def test_long_history_loaded_once_not_every_refresh(monkeypatch, tmp_path):
    """Регресс: «Загружаю историю за 5 г» при каждом обновлении. При непустом кэше грузился только хвост (история
    назад не догружалась), обычное обновление обрезало кэш до 410 дней, а решение «догрузить» принималось по средней
    глубине — у молодых бумаг она навсегда меньше базы. Переоценка «за 5 лет» фактически считалась примерно за год."""
    _fake_sdk(monkeypatch)
    old, young = FakeMarket(10 * 365), FakeMarket(2 * 365)
    for figi, m in (("OLD", old), ("YOUNG", young)):
        H.fetch_candles(NS(market_data=m), figi, tmp_path)                 # обычное обновление: ~400 дней
    five = 5 * 365 + 30
    assert H.needs_history(tmp_path, "OLD", five) and H.needs_history(tmp_path, "YOUNG", five)
    for figi, m in (("OLD", old), ("YOUNG", young)):
        m.calls.clear()
        s = H.fetch_candles(NS(market_data=m), figi, tmp_path, days=five)
        assert min(c[0] for c in m.calls) < m.calls[-1][1] - timedelta(days=5 * 365)   # грузили назад, не только хвост
    assert H.cagr(H.load_cached(tmp_path, "OLD", None), 100.0, 5)[1] > 4.9        # глубина реально 5 лет
    # молодая бумага: раньше данных нет, и так глубоко уже спрашивали — повторно не нужно
    assert not H.needs_history(tmp_path, "OLD", five) and not H.needs_history(tmp_path, "YOUNG", five)
    # обычное обновление (400 дней) грузит только хвост и не стирает пятилетнюю историю
    old.calls.clear()
    s = H.fetch_candles(NS(market_data=old), "OLD", tmp_path)
    assert len(old.calls) == 1 and old.calls[0][0] > old.calls[0][1] - timedelta(days=10)
    assert s[0][0] >= (date.today() - timedelta(days=411)).strftime("%Y-%m-%d")        # вызывающему — его окно
    assert not H.needs_history(tmp_path, "OLD", five)


def test_load_cached_default_window_is_a_year(tmp_path):
    """В кэше теперь до 6 лет; риск, история стоимости и карточка по-прежнему видят ~400 дней."""
    H.save_series(tmp_path, "X", series(1.10, 5))
    assert len(H.load_cached(tmp_path, "X")) <= 411
    assert len(H.load_cached(tmp_path, "X", None)) == 5 * 365 + 1


def test_news_offtopic_and_ambiguous_aliases():
    """Регресс: к «Золоту» липли спортивные новости («победила в финале ЧЕ по боксу» — золото), к VTBR — «Единая
    лига ВТБ» (баскетбол), к MOEX — всё «московское» (первое слово названия «Московская Биржа»)."""
    pos = [NS(ticker="GLDRUB_TOM", name="Золото"), NS(ticker="VTBR", name="Банк ВТБ"),
           NS(ticker="MOEX", name="Московская Биржа"), NS(ticker="XXXX", name="Энергосбыт")]
    kw = N.build_keywords(pos, N.DEFAULT_ALIASES)
    assert "Московская" not in kw["MOEX"] and "Энергосбыт" in kw["XXXX"]       # без алиасов имя по-прежнему в ходу

    def it(title, summary=""):
        return N.NewsItem(title, "", "s", None, [], summary)
    items = [it("Россиянка победила украинку в финале ЧЕ по боксу", "и завоевала золото"),
             it("В новом сезоне Единой лиги ВТБ на победу претендуют четыре команды"),
             it("Московская филармония представила программу «Рамо»", "на сцене зала"),
             it("«Золотая маска» объявила номинантов", "лучшая сцена сезона"),
             it("Золото подорожало до рекорда", "цена унции выросла"),
             it("ВТБ отчитался о прибыли"),
             it("Мосбиржа запустила новые торги")]
    got = {x.title: x.matched for x in N.match(items, kw)}
    assert got == {"Золото подорожало до рекорда": ["GLDRUB_TOM"], "ВТБ отчитался о прибыли": ["VTBR"],
                   "Мосбиржа запустила новые торги": ["MOEX"]}
    assert not N.refers("сборная завоевала золото", kw["GLDRUB_TOM"])
    assert N.refers("цены на золото растут", kw["GLDRUB_TOM"])


def test_news_source_rules_funds_and_entities():
    """Регресс: smart-lab (блоги) привязывал посты к бумагам и «макро» по тексту поста («Портфель на 23 сентября» →
    AGRO, «Человек и пароход» → макро); служебные сообщения Мосбиржи шли как новости акции MOEX; заметка РБК про кино
    («отработать технологии съемок») привязывалась к фонду TECH по слову из названия; в заголовках — «&quot;»."""
    pos = [NS(ticker="AGRO", name="Русагро", instrument_type="share"), NS(ticker="MOEX", name="Московская Биржа"),
           NS(ticker="TECH", name="Технологии 100", instrument_type="etf"), NS(ticker="SBERP", name="Сбербанк")]
    kw = N.build_keywords(pos, N.DEFAULT_ALIASES)
    assert kw["TECH"] == ["TECH"]                                         # у фонда — только тикер и алиасы

    def it(title, summary="", source="rbc.ru"):
        return N.NewsItem(title, "", source, None, [], summary)
    items = [it("Портфель на 23 сентября 2026 г.", "держу Русагро, жду дивиденд", "smart-lab.ru"),
             it("Человек и пароход", "про ключевая ставка и дивиденд", "smart-lab.ru"),
             it("Русагро: разбор отчётности", "", "smart-lab.ru"),
             it("О начале торгов ценными бумагами", "Московская биржа сообщает", "moex.com"),
             it("Об исключении ценных бумаг", "облигации ПАО Сбербанк исключены", "moex.com"),
             it("Режиссер рассказал, почему время сказок в кино уходит", "Сказки позволили отработать технологии съемок")]
    got = {x.title: x.matched for x in N.match(items, kw)}
    assert got == {"Русагро: разбор отчётности": ["AGRO"]}          # у Мосбиржи — только заголовок, самой MOEX нет
    feed = (b"<rss><channel><item><title>O &amp;quot;25&amp;quot; ok</title><description>&amp;laquo;x&amp;raquo;"
            b"</description></item></channel></rss>")
    one = N.parse_feed(feed, "moex.com")[0]
    assert one.title == 'O "25" ok' and one.summary == "«x»"


def test_feed_namespace_html_check_age_and_moex_technical(monkeypatch):
    """Регресс: retail.ru (RSS с xmlns по умолчанию) выглядел пустой лентой; Forbes/Банки.ру отдают страницу
    «вы не робот» — «Проверить» писал «нет элементов <item>»; лента Мосбиржи — 1000+ техсообщений за два месяца
    (ценовые коридоры МТС, размещения облигаций «Сбербанк КИБ») привязывались к бумагам портфеля."""
    from datetime import datetime, timedelta, timezone
    ns = (b'<rss version="2.0" xmlns="http://backend.userland.com/rss2"><channel><item><title>A</title>'
          b'<link>u</link><pubDate>Fri, 19 Sep 2026 05:00:00 +0300</pubDate></item></channel></rss>')
    got = N.parse_feed(ns, "retail.ru")
    assert [(i.title, i.link) for i in got] == [("A", "u")] and got[0].published is not None
    monkeypatch.setattr(N.net, "fetch", lambda url, timeout=10: b"<html><head><meta name='robots'></head></html>")
    ok, msg = N.check_feed("https://www.forbes.ru/newrss.xml")
    assert not ok and "защита от ботов" in msg

    now = datetime.now(timezone.utc)
    pos = [NS(ticker="MTSS", name="МТС"), NS(ticker="SBERP", name="Сбербанк")]

    def item(title, days, src="moex.com"):
        return N.NewsItem(title, "", src, now - timedelta(days=days), [], "")
    items = [item("12.08, изменены значения верхней границы ценового коридора и диапазона оценки МТС", 0),
             item("Об отмене размещения облигаций серии CIB-001 АО «Сбербанк КИБ»", 0),
             item("Сбербанк присоединился к торгам на новом рынке", 0),
             item("МТС отчиталась о выручке", 30, "interfax.ru"),
             item("МТС отчиталась о прибыли", 1, "interfax.ru")]
    monkeypatch.setattr(N, "parse_feed", lambda data, src: [i for i in items if i.source == src])
    monkeypatch.setattr(N.net, "fetch", lambda url, timeout=12: b"")
    got, failed = N.fetch_news(["https://www.moex.com/x", "https://interfax.ru/rss"], pos, {})
    assert [i.title for i in got] == ["Сбербанк присоединился к торгам на новом рынке", "МТС отчиталась о прибыли"]
