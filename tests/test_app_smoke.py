"""Дымовые тесты окна: offscreen, без токена и сети. Проверяют сценарии, которые уже ломались."""
from datetime import datetime
from decimal import Decimal

import pytest


@pytest.fixture
def window(qapp, monkeypatch):
    import app as A
    import wizard as W
    monkeypatch.setattr(W.SetupWizard, "exec", lambda self: 0)          # мастер модальный — в тестах не ждём
    A.ensure_config(A.paths.data_dir() / "config.yaml")
    w = A.MainWindow(str(A.paths.data_dir() / "config.yaml"))
    yield w
    w.close()


def test_new_user_gets_wizard_existing_does_not(qapp, monkeypatch):
    import app as A
    import wizard as W
    opened = []
    monkeypatch.setattr(W.SetupWizard, "exec", lambda self: opened.append(1) or 0)
    cfg_path = A.paths.data_dir() / "config.yaml"
    A.ensure_config(cfg_path)
    w = A.MainWindow(str(cfg_path))
    assert w.needs_setup()
    w.close()
    cfg_path.write_text("instruments:\n  SBERP: equity_core\n", encoding="utf-8")
    w = A.MainWindow(str(cfg_path))
    assert not w.needs_setup()
    w.close()


def test_load_classifies_and_renders(window, demo_positions):
    for p in demo_positions:
        p.cls = "unclassified"
    window.on_loaded((demo_positions, Decimal("300000"), None))
    assert len(window.cfg["instruments"]) == len(demo_positions)


def test_ideas_enrichment_runs_once_and_survives_reload(window):
    """Регресс: бесконечный цикл догрузки свечей и потеря «Мес.» при повторном показе из кэша."""
    import screener as S
    calls = []
    window.enrich_ideas = lambda ideas: calls.append([i.ticker for i in ideas])
    mk = lambda: [S.Idea("LKOH", "ЛУКОЙЛ", "Нефть и газ", 5248, 3636, 12.9, 676, 5, -0.3, 18, 55, "", 11.0, None, "u1")]
    r1 = S.ScreenResult(mk(), 255, 253, 253, 122, False, datetime.now().isoformat()); r1.all_ideas = r1.ideas
    window._last_screen = r1; window._on_ideas(r1)
    window._on_ideas_enriched({"LKOH": 25.4})
    r2 = S.ScreenResult(mk(), 255, 253, 253, 122, True, datetime.now().isoformat()); r2.all_ideas = r2.ideas
    window._last_screen = r2; window._on_ideas(r2)
    assert calls == [["LKOH"]]
    assert window.tbl_ideas.item(0, 3).text().endswith("+25.4%")


def test_settings_model_picker_returns_clean_id(window, isolated_home):
    """Регресс: после ручного ввода поле возвращало ранее выбранную модель."""
    window.open_settings()
    mp = window.settings_win.ai_model
    mp.set_models([{"id": "a/b:free", "name": "AB", "free": True, "context": 64000, "in_per_m": 0, "out_per_m": 0,
                    "recommended": True, "why": "", "created": 1}], "тест")
    mp.combo.setCurrentIndex(0); mp._picked(0)
    assert mp.text() == "a/b:free"
    mp.setText("vendor/other"); mp._emit()
    assert mp.text() == "vendor/other"
    window.settings_win.close()
