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
    CS.cache_path(paths.data_dir() / "cache", sber.figi).write_text(json.dumps({
        "consensus": {"recommendation": "нейтральный взгляд", "current": 300.0, "target": 330.0, "min": 310.0,
                      "max": 350.0, "change_rel": 10.0, "currency": "rub"}, "targets": []}), encoding="utf-8")
    window.open_card("SBERP")
    html = window._cards["SBERP"].info.toPlainText()
    assert "Консенсус аналитиков — справочно" in html and "330.00" in html and "не рекомендация программы" in html
    window._cards["SBERP"].close()
