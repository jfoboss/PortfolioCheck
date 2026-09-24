"""Календарь решений ЦБ по ключевой ставке (macro.py, BACKLOG п. 25): разбор живой вёрстки, кэш, оповещение, экран."""
from datetime import date
from types import SimpleNamespace as NS

import alerts as AL
import macro

# Фрагмент https://www.cbr.ru/dkp/cal_mp/ — как отдавал сайт 24.09.2026 (публичные данные ЦБ, не личные)
PAGE = """<html><body><div class="main-events_day">
<div class="date col-md-5">11&nbsp;сентября 2026 года</div>
<div class="main-events offset-md-1 col-md-17">
<div class="main-event">
<div class="title"><span class="col-md-15">Заседание Совета директоров Банка России по&nbsp;ключевой ставке</span>
<div class="icon_wrapper offset-md-1 col-md-1">
<div></div>
</div>
</div>
<div class="info col-md-15"><a href="/press/pr/?file=11092026_133000key.htm">Пресс-релиз</a> по&nbsp;ключевой ставке <br /><a href="/press/event/?id=32832"><span class="col-md-15">Пресс-конференция Председателя Банка России</span></a></div>
</div>
</div>
</div>
<div class="main-events_day">
<div class="date col-md-5">23&nbsp;сентября 2026 года</div>
<div class="main-events offset-md-1 col-md-17">
<div class="main-event">
<div class="info col-md-15"><a href="/dkp/mp_dec/decision_key_rate/summary_key_rate_23092026/" target="_blank" rel="noopener"><span class="col-md-15">Резюме обсуждения ключевой ставки</span></a>
</div>
</div>
</div>
<div class="main-events_day">
<div class="date col-md-5">23&nbsp;октября 2026 года</div>
<div class="main-events offset-md-1 col-md-17">
<div class="main-event">
<div class="title"><span class="col-md-15">Заседание Совета директоров Банка России по&nbsp;ключевой ставке</span>
<div class="icon_wrapper offset-md-1 col-md-1">
<div class="icon-important"></div>
</div>
</div>
<div class="info col-md-15">Пресс-релиз по&nbsp;ключевой ставке <br />Среднесрочный прогноз <br /><span class="col-md-15">Пресс-конференция Председателя Банка России</span></div>
</div>
</div>
</div>
<div class="main-events_day">
<div class="date col-md-5">5&nbsp;ноября 2026 года</div>
<div class="main-events offset-md-1 col-md-17">
<div class="main-event">
<div class="info col-md-15"><span class="col-md-15">Резюме обсуждения ключевой ставки<br />Комментарий к&nbsp;среднесрочному прогнозу</span>
</div>
</div>
</div>
</body></html>"""


def test_parse_live_layout():
    ev = macro.parse_cbr_calendar(PAGE)
    assert [(e.date, e.kind, e.forecast) for e in ev] == [
        ("2026-09-11", macro.MEETING, False), ("2026-09-23", macro.SUMMARY, False),
        ("2026-10-23", macro.MEETING, True), ("2026-11-05", macro.SUMMARY, False)]
    up = macro.upcoming(ev, date(2026, 9, 24))
    assert [e.date for e in up] == ["2026-10-23", "2026-11-05"]


def test_parse_changed_layout_raises():
    import pytest
    with pytest.raises(ValueError):
        macro.parse_cbr_calendar("<html>другая вёрстка</html>")


def test_fetch_and_cache(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url); return PAGE.encode("utf-8")
    assert macro.fetch_calendar(tmp_path, fetch) == "ok" and calls == [macro.CAL_URL]
    assert len(macro.load_calendar(tmp_path)) == 4 and macro.calendar_fresh(tmp_path)
    assert macro.fetch_calendar(tmp_path, fetch) == "ok" and len(calls) == 1       # кэш на неделю — сеть не трогаем
    bad = tmp_path / "bad"
    assert macro.fetch_calendar(bad, lambda u: b"<html></html>").startswith("ValueError")
    assert macro.load_calendar(bad) == []


def test_alert_nearest_meeting_only():
    ev = macro.parse_cbr_calendar(PAGE)
    rule = [{"kind": "cbr_meeting", "value": 30}]
    hits = AL.evaluate(rule, [NS(ticker="SBERP", cur_price=1.0)], today=date(2026, 9, 24), macro_events=ev)
    assert len(hits) == 1 and "23.10" in hits[0].text and "29 дн." in hits[0].text and "прогноз" in hits[0].text
    assert AL.evaluate(rule, today=date(2026, 9, 12), macro_events=ev) == []        # до 23.10 больше 30 дней
    assert AL.describe(rule[0]) == "До заседания ЦБ по ставке ≤, дней 30"


def test_calendar_screen(window):
    import paths
    from datetime import timedelta
    today = date.today()
    ev = [macro.MacroEvent((today + timedelta(days=d)).isoformat(), k, macro.KIND_LABEL[k], fc)
          for d, k, fc in ((-5, macro.MEETING, False), (5, macro.MEETING, True), (18, macro.SUMMARY, False), (400, macro.MEETING, False))]
    macro.save_calendar(ev, paths.cache_dir())
    window.load_macro_calendar()
    assert window.tbl_macro.rowCount() == 2                                            # прошлое и дальше полугода — нет
    assert "среднесрочный прогноз" in window.tbl_macro.item(0, 0).text()
    assert "заседание ЦБ по ставке</b> — " in window.lbl_payouts.text() and "через 5 дн." in window.lbl_payouts.text()
