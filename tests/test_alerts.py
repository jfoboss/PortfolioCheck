"""Оповещения (alerts.py): виды условий, однократность, «взвод» после сброса, время новостей, окно."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import alerts as AL
import payouts as PO
from news import NewsItem

TODAY = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 12, 0)
POS = [NS(ticker="SBERP", cur_price=310.0), NS(ticker="LKOH", cur_price=5600.0)]
IDEAS = [NS(ticker="TATNP", price=610.0, div_yield=11.9)]


def keys(hits):
    return [h.key for h in hits]


def test_price_yield_corridor():
    rules = [{"kind": "price_above", "ticker": "SBERP", "value": 300}, {"kind": "price_below", "ticker": "LKOH", "value": 5000},
             {"kind": "yield_above", "ticker": "tatnp", "value": 11.5}, {"kind": "corridor"},
             {"kind": "price_above", "ticker": "SBERP", "value": 400, "on": False}]
    hits = AL.evaluate(rules, POS, IDEAS, class_status={"Золото": (30.0, 20, 27), "Ядро": (50.0, 40, 55)}, today=TODAY, now=NOW)
    texts = " | ".join(h.text for h in hits)
    assert "SBERP: цена 310.00 ≥ 300.00" in texts and "LKOH" not in texts          # LKOH 5600 > 5000 — не выполнено
    assert "TATNP: дивдоходность 11.9%" in texts and "«Золото»: 30.0% — выше коридора" in texts
    assert "Ядро" not in texts and "400" not in texts                                 # в коридоре; выключенное правило
    assert all("купи" not in h.text.lower() and "прода" not in h.text.lower() for h in hits)


def test_days_to_cut_and_news():
    pays = [PO.Payout("SBERP", "Сбер", "2026-09-30", "2026-09-29", "", 30.0, "RUB", 1, 30.0, "declared"),
            PO.Payout("LKOH", "Лук", "2026-12-30", "", "", 1.0, "RUB", 1, 1.0, "expected"),
            PO.Payout("SBERP", "Сбер", "2026-01-10", "", "", 1.0, "RUB", 1, 1.0, "paid")]
    utc_fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    news = [NewsItem("Сбер отчитался", "", "s", utc_fresh, ["SBERP"]),
            NewsItem("Старая новость", "", "s", datetime.now() - timedelta(days=3), ["SBERP"]),
            NewsItem("Про чужую бумагу", "", "s", datetime.now(), ["GAZP"])]
    hits = AL.evaluate([{"kind": "days_to_cut", "value": 7}, {"kind": "news"}], POS, [], pays, news=news, today=TODAY)
    texts = [h.text for h in hits]
    assert any("SBERP: до последнего дня покупки под выплату 5 дн." in t for t in texts)
    assert not any("LKOH" in t for t in texts)                                        # далеко
    assert texts.count("SBERP: Сбер отчитался") == 1 and len(texts) == 2              # старая и чужая — нет


def test_new_hits_once_and_rearm(tmp_path):
    h = [AL.Hit("a", "A"), AL.Hit("b", "B")]
    assert keys(AL.new_hits(h, tmp_path)) == ["a", "b"]
    assert AL.new_hits(h, tmp_path) == []                                             # повторно не будим
    assert AL.new_hits([AL.Hit("b", "B")], tmp_path) == []                            # «a» перестало выполняться
    assert keys(AL.new_hits(h, tmp_path)) == ["a"]                                    # и снова выполнилось


def test_window_checks_alerts_and_settings_keep_rules(window, demo_positions, qapp):
    """Окно проверяет правила после загрузки; окно настроек сохраняет раздел alerts; разделы палитры совпадают
    с вкладками настроек."""
    from screens.palette import SETTINGS_TABS
    window.cfg["alerts"] = [{"kind": "price_above", "ticker": "SBERP", "value": 1, "on": True}]
    window.on_loaded((demo_positions, None, None, []))
    assert any("SBERP" in t for t in window._alert_log)
    assert window.check_alerts() == []                                                # уже сообщено
    window.open_settings()
    dlg = window.settings_win
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == SETTINGS_TABS
    assert dlg.collect()["alerts"] == [{"kind": "price_above", "ticker": "SBERP", "value": 1.0, "on": True}]
    dlg.close()
