"""Консенсус-прогнозы (consensus.py): защитный разбор ответа, нейтральные слова, кэш."""
import json
import os
import time
from datetime import datetime
from types import SimpleNamespace as NS

import consensus as CS


def Q(x):
    return NS(units=int(x), nano=int(round((x - int(x)) * 1e9)))


def test_parse_forecast_response():
    resp = NS(consensus=NS(recommendation=1, current_price=Q(300.0), consensus=Q(360.5), min_target=Q(320.0),
                           max_target=Q(400.0), price_change_rel=Q(20.1), currency="rub"),
              targets=[NS(company="Дом А", recommendation=2, target_price=Q(350.0), recommendation_date=datetime(2026, 9, 1)),
                       NS(show_name="Дом Б", company="", recommendation="RECOMMENDATION_SELL", target_price=None,
                          recommendation_date=None)])
    d = CS.parse(resp)
    c = d["consensus"]
    assert c["target"] == 360.5 and c["min"] == 320.0 and abs(c["change_rel"] - 20.1) < 1e-6
    assert c["recommendation"] == "позитивный взгляд"                    # не «покупать» — правило 3
    assert d["targets"][0] == {"company": "Дом А", "recommendation": "нейтральный взгляд", "target": 350.0,
                               "date": "2026-09-01"}
    assert d["targets"][1]["company"] == "Дом Б" and d["targets"][1]["recommendation"] == "негативный взгляд"
    words = json.dumps(d, ensure_ascii=False).lower()
    assert "покуп" not in words and "прода" not in words


def test_parse_missing_consensus_and_cache(tmp_path):
    assert CS.parse(NS(consensus=None, targets=[])) is None
    assert CS.parse(NS(consensus=NS(consensus=None))) is None
    CS.cache_path(tmp_path, "u1").write_text(json.dumps({"consensus": None, "targets": []}), encoding="utf-8")
    assert CS.load(tmp_path, "u1") == {"consensus": None, "targets": []}
    old = time.time() - CS.MAX_AGE_S - 10
    os.utime(CS.cache_path(tmp_path, "u1"), (old, old))
    assert CS.load(tmp_path, "u1") is None                                 # устарел — перезапросить


def test_card_shows_cached_consensus(window, demo_positions, isolated_home):
    import paths
    window.on_loaded((demo_positions, None, None, []))
    sber = next(p for p in demo_positions if p.ticker == "SBERP")
    CS.cache_path(paths.cache_dir(), sber.figi).write_text(json.dumps({
        "consensus": {"recommendation": "нейтральный взгляд", "current": 300.0, "target": 330.0, "min": 310.0,
                      "max": 350.0, "change_rel": 10.0, "currency": "rub"}, "targets": []}), encoding="utf-8")
    window.open_card("SBERP")
    html = window._cards["SBERP"].info.toPlainText()
    assert "Консенсус аналитиков — справочно" in html and "330.00" in html and "не рекомендация программы" in html
    window._cards["SBERP"].close()


def test_request_passes_request_object(monkeypatch):
    """Регресс: get_forecast_by(instrument_id=uid) → TypeError «unexpected keyword argument 'instrument_id'»:
    метод SDK принимает только объект GetForecastRequest (сигнатура — как в tinkoff/invest/services.py)."""
    import sys
    from dataclasses import dataclass

    @dataclass
    class GetForecastRequest:
        instrument_id: str = ""

    schemas = NS(GetForecastRequest=GetForecastRequest)
    monkeypatch.setitem(sys.modules, "tinkoff", NS(invest=NS(schemas=schemas)))
    monkeypatch.setitem(sys.modules, "tinkoff.invest", NS(schemas=schemas))
    monkeypatch.setitem(sys.modules, "tinkoff.invest.schemas", schemas)
    seen = []

    class Instruments:
        def get_forecast_by(self, request):                   # как в SDK: без **kwargs
            seen.append(request)
            return NS(consensus=None, targets=[])

    CS.request(NS(instruments=Instruments()), "uid-1")
    assert seen == [GetForecastRequest(instrument_id="uid-1")]


def test_parse_empty_consensus_item_from_sdk():
    """Регресс: без консенсуса SDK отдаёт не None, а ConsensusItem с нулями — карточка показала бы цель 0.00."""
    empty = NS(recommendation=NS(name="RECOMMENDATION_UNSPECIFIED"), current_price=Q(0), consensus=Q(0),
               min_target=Q(0), max_target=Q(0), price_change_rel=Q(0), currency="")
    assert CS.parse(NS(consensus=empty, targets=[])) is None
    assert CS._rec(NS(name="RECOMMENDATION_UNSPECIFIED")) == "" and CS._rec(0) == ""


def test_parse_target_without_date_from_sdk():
    """Регресс: у прогноза дома без даты SDK отдаёт не None, а 1970-01-01 — карточка показала бы эту дату."""
    from datetime import timezone
    resp = NS(consensus=NS(recommendation=1, current_price=Q(100.0), consensus=Q(120.0), min_target=Q(90.0),
                           max_target=Q(150.0), price_change_rel=Q(20.0), currency="rub"),
              targets=[NS(company="Дом В", recommendation=2, target_price=Q(110.0),
                          recommendation_date=datetime(1970, 1, 1, tzinfo=timezone.utc))])
    assert CS.parse(resp)["targets"][0]["date"] == ""
