import strategy as ST
from types import SimpleNamespace as NS


def P(t, name, typ, cur="RUB"):
    return NS(ticker=t, name=name, instrument_type=typ, currency=cur, isin="")


def test_auto_class_rules():
    assert ST.auto_class(P("GLDRUB_TOM", "Золото", "currency")) == "gold"
    assert ST.auto_class(P("USD000UTSTOM", "Доллар США", "currency")) == "cash"
    assert ST.auto_class(P("SBERP", "Сбербанк-п", "share")) == "equity_core"
    assert ST.auto_class(P("RU000A0DEMO1", "ЗПИФ Демо-Склад", "etf")) == "real_estate_other"
    assert ST.auto_class(P("AAPL", "Apple", "share", "USD")) == "foreign"
    assert ST.auto_class(P("SU26238", "ОФЗ 26238", "bond")) is None      # облигации — без класса


def test_auto_classify_marks_auto_and_keeps_existing():
    cfg = {"instruments": {"SBERP": "equity_spec"}}
    done = ST.auto_classify([P("SBERP", "Сбербанк-п", "share"), P("LKOH", "ЛУКОЙЛ", "share")], cfg)
    assert done == ["LKOH"]
    assert cfg["instruments"]["SBERP"] == "equity_spec"                # ручной класс не перетирается
    assert cfg["auto_classified"] == ["LKOH"]


def test_presets_are_consistent():
    for pid, p in ST.PRESETS.items():
        mins = sum(lo for cls, (lo, hi) in p["targets"].items() if cls not in ("real_estate_wh", "real_estate_other"))
        assert mins <= 100, pid
        assert not ST.validate_strategy(ST.apply_preset({}, pid)) or pid == "blank"


def test_export_has_no_secrets_or_personal_numbers():
    cfg = ST.apply_preset({"account": {"id": "SECRET-ID"}, "forecast": {"start_capital": 12345678, "monthly_contribution": 1}},
                          "conservative")
    y = ST.export_strategy(cfg)
    assert "SECRET-ID" not in y and "12345678" not in y and "account" not in y


def test_import_strips_chatgpt_wrapper_and_keeps_account():
    text = "Вот:\n```yaml\ntargets:\n  gold: {min: 20, max: 25}\nrules:\n  drawdown_alert_pct: 10\n```\nУдачи"
    new, warns, replaced = ST.import_strategy({"account": {"id": "x"}}, text)
    assert replaced == ["targets", "rules"] and new["account"] == {"id": "x"} and new["setup_done"] is True


def test_import_rejects_incompatible_corridors():
    import pytest
    with pytest.raises(ValueError, match="сумма минимумов"):
        ST.import_strategy({}, "targets:\n  gold: {min: 60, max: 70}\n  equity_core: {min: 50, max: 60}\n")


def test_prompt_example_imports_cleanly():
    import re
    md = (ST.Path(ST.__file__).parent / "docs" / "CHATGPT_PROMPT.md").read_text(encoding="utf-8")
    block = re.findall(r"```yaml\n(.*?)```", md, re.S)[-1]
    new, warns, replaced = ST.import_strategy({}, block)
    assert not warns and "targets" in replaced
