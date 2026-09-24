"""SettingsDialog.collect() туда-обратно: окно настроек не должно терять то, что пользователь не трогал."""
import copy

import pytest


@pytest.fixture
def full_cfg(demo_cfg, demo_positions):
    import config_migrate as CM
    cfg = copy.deepcopy(demo_cfg)
    cfg[CM.KEY] = CM.CURRENT_VERSION
    cfg["account"] = {"id": "ACC-TEST"}
    cfg["instruments"] = {p.ticker: p.cls for p in demo_positions}
    cfg["instrument_names"] = {p.ticker: p.name for p in demo_positions}
    cfg["reference_tickers"] = {"RU000A0DEMO1": "SBERP"}
    cfg["blocked_tickers"] = ["YDEX"]
    cfg["fx_fallback"] = {"USD": 90.0, "CNY": 12.5}
    cfg["rules"].update({"frozen": {"MTSS": "жду отчёт"}, "position_caps": {"SBERP": 300000},
                         "buy_candidates_classes": ["equity_core", "real_estate_wh"]})
    cfg["screener"] = {"blacklist": {"AFLT": "не мой сектор"}, "min_avg5y_yield": 5.0, "max_pe": 9.0,
                       "min_free_float": 10.0, "exclude_sectors": ["IT"], "last_auto_run": "2026-09-01"}
    cfg["news"] = {"feeds": ["https://example.com/rss"], "aliases": {"SBERP": ["Сбер", "Греф"]}}
    cfg["ai"] = {"provider": "openrouter", "model": "a/b:free", "proxy": "", "base_url": "", "auto": False,
                 "min_interval_h": 12}
    cfg["refresh"] = {"on_launch": False, "interval_min": 30, "privacy_on_launch": False}
    cfg["alerts"] = [{"kind": "price_below", "ticker": "LKOH", "value": 5000.0, "on": True},
                     {"kind": "news", "ticker": "", "value": 0.0, "on": False}]
    cfg["setup_done"] = True
    cfg["auto_classify"] = False
    cfg["auto_classified"] = ["PHOR"]
    return cfg


def _collect(qapp, cfg, isolated_home):
    from settings_dialog import SettingsDialog
    dlg = SettingsDialog(copy.deepcopy(cfg), isolated_home / "data" / "ca-bundle.pem")
    try:
        return dlg.collect()
    finally:
        dlg.close()


# разделы, которыми владеет окно настроек; прогноз (forecast) окно не трогает — его сохраняет главное окно
SECTIONS = ("config_version", "account", "targets", "instruments", "reference_tickers", "blocked_tickers",
            "fx_fallback", "screener", "news", "ai", "setup_done", "strategy_preset", "auto_classify", "auto_classified", "alerts")


def test_collect_roundtrip_keeps_everything(qapp, full_cfg, isolated_home):
    out = _collect(qapp, full_cfg, isolated_home)
    for k in SECTIONS:
        assert out.get(k) == full_cfg.get(k), k
    for k, v in full_cfg["rules"].items():
        got = out["rules"].get(k)
        if k == "buy_candidates_classes":            # множество: окно пишет классы в своём порядке
            got, v = sorted(got), sorted(v)
        assert got == v, f"rules.{k}"
    for k, v in full_cfg["refresh"].items():
        assert out["refresh"].get(k) == v, f"refresh.{k}"
    assert set(full_cfg["instrument_names"]) <= set(out["instrument_names"])


def test_collect_is_idempotent(qapp, full_cfg, isolated_home):
    once = _collect(qapp, full_cfg, isolated_home)
    twice = _collect(qapp, once, isolated_home)
    assert once == twice
