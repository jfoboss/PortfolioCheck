"""Дымовые тесты окна: offscreen, без токена и сети. Проверяют сценарии, которые уже ломались."""
from datetime import datetime
from decimal import Decimal



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


def test_banner_retry_only_for_icloud(window):
    """Регресс: кнопка «Проверить снова» (доступ к iCloud) висела на любом баннере — например, «нет токена»."""
    window.show_banner("нет токена", "Открыть Настройки → Доступ")
    assert window.banner_retry.isHidden()
    window.show_banner("нет доступа к iCloud", "Открыть", "x", retry=True)
    assert not window.banner_retry.isHidden()


def test_ai_check_button_runs_in_background(window, monkeypatch):
    """Регресс: «Проверить» в Настройки → ИИ ждал ответ модели в UI-потоке — окно замирало на время запроса."""
    import threading
    import ai as AI
    ui_thread = threading.get_ident()
    seen = {}

    def fake_check(*a, **k):
        seen["thread"] = threading.get_ident()
        return {"verdict": "good", "problems": [], "model": "m", "seconds": 0.1, "at": "2026-09-24T10:00:00"}
    monkeypatch.setattr(AI, "check_model", fake_check)
    window.open_settings()
    dlg = window.settings_win
    monkeypatch.setattr(dlg, "_ai_key", lambda: "k")
    dlg._ai_test()
    assert dlg._check_worker.wait(5000)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert seen["thread"] != ui_thread and "подходит" in dlg.lbl_ai_test.text()
    dlg.close()


def test_menu_has_everything_for_macos(window):
    """Меню: всё, что есть на тулбаре и в «О программе», доступно из меню с сочетаниями; роли macOS расставлены
    (Настройки и «О программе» уезжают в меню приложения, «Выход» — туда же)."""
    from PySide6.QtGui import QAction, QKeySequence
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    assert list(menus) == ["Файл", "Вид", "Окно", "Справка"]
    acts = {a.text(): a for m in menus.values() for a in m.actions() if a.text()}
    assert acts["Настройки…"].menuRole() == QAction.PreferencesRole
    assert acts["О программе PortfolioCheck"].menuRole() == QAction.AboutRole
    assert acts["Выход"].menuRole() == QAction.QuitRole
    assert QKeySequence("Ctrl+R") in acts["Обновить"].shortcuts()
    priv = window.act_privacy                  # текст переключается: «Скрыть суммы» / «Показать суммы»
    assert priv in acts.values() and priv.shortcut() == QKeySequence("Ctrl+Shift+H") and priv.isCheckable()
    for k in ("Диагностика и лог…", "Проверить подключения", "Свернуть", "Во весь экран", "ИИ-сводки по экранам",
              "Мастер настройки…", "Открыть снапшот…", "Сохранить снапшот…"):
        assert k in acts, k
    tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert tabs == ["Обзор", "Портфель", "Календарь", "Рынок", "Идеи", "Стратегия"]      # «О программе» — не вкладка
    for i, t in enumerate(tabs):
        assert acts[t].shortcut() == QKeySequence(f"Ctrl+{i + 1}")
    acts["Идеи"].trigger()
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Идеи"


def test_about_is_a_separate_window(window):
    window.act_about.trigger()
    assert window.about_win.isVisible() and window.about_win.tab.lbl_env.text()
    window.about_win.close()


def test_security_card_collects_everything(window, demo_positions):
    """Карточка бумаги: позиция и правила, выплаты, новости — из того, что окно уже знает, без сети."""
    import payouts as PO
    from news import NewsItem
    from screens.card import card_data
    window.on_loaded((demo_positions, None, None, [PO.Payout("SBERP", "Сбербанк-п", "2099-01-10", "", "", 30.0, "RUB",
                                                              1000, 30000.0, "declared")]))
    window.news_items = [NewsItem("Сбербанк отчитался", "https://x", "s", None, ["SBERP"]),
                         NewsItem("Погода", "", "s", None, [])]
    d = card_data(window, "SBERP")
    assert d["pos"] is not None and d["rules"]["share"] > 0 and d["rules"]["single_max"] == 11
    assert [x.record_date for x in d["payouts"]] == ["2099-01-10"]
    assert [n.title for n in d["news"]] == ["Сбербанк отчитался"]
    window.tabs.setCurrentIndex(1)
    row = next(r for r in range(window.tbl_pos.rowCount()) if window.tbl_pos.item(r, 1).text() == "SBERP")
    window.tbl_pos.cellDoubleClicked.emit(row, 0)
    card = window._cards["SBERP"]
    assert card.isVisible() and "Сбербанк" in card.lbl_head.text() and card.tbl_pay.rowCount() == 1
    window.set_privacy(True); card.refresh()
    assert card.tbl_pay.item(0, 4).text() == "•••" or "•" in card.tbl_pay.item(0, 4).text()
    card.close()


def test_guest_mode_and_switch_to_real_profile(qapp, monkeypatch, isolated_home):
    """Первый запуск без токена и данных — демо с одной кнопкой «Настроить…»; по ней — настоящий профиль и мастер,
    кнопки стандартные, секреты снова из Keychain, временная папка демо удалена."""
    import app as A
    import paths
    import secrets_store as SEC
    import wizard as W
    opened = []
    monkeypatch.setattr(W.SetupWizard, "exec", lambda self: opened.append(1) or 0)
    monkeypatch.setattr(A, "get_token", lambda: None)
    assert A.should_start_as_guest()
    demo_dir = isolated_home / "PortfolioCheck-demo-test"
    demo_dir.mkdir()
    monkeypatch.setattr(paths, "DEMO_DIR", demo_dir)          # как после demo.setup(); восстановится после теста
    monkeypatch.setattr(SEC, "DISABLED", True)
    cfg = isolated_home / "data" / "config.yaml"
    A.ensure_config(cfg)
    w = A.MainWindow(str(cfg)); w._demo_dir = demo_dir
    w.enter_guest_mode()
    assert w.act_setup.isVisible() and not w.act_refresh.isVisible() and not w.act_prefs.isVisible()
    assert not w.act_privacy.isVisible() and w.banner_btn.text() == "Настроить…"
    new = A.switch_to_real_profile(w)
    try:
        assert paths.DEMO_DIR is None and SEC.DISABLED is False and not demo_dir.exists()
        assert not new.guest and new.act_refresh.isVisible() and new.act_prefs.isVisible() and not new.act_setup.isVisible()
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop(); QTimer.singleShot(700, loop.quit); loop.exec()
        assert opened, "мастер настройки не открылся для нового пользователя"
    finally:
        new.close()
    (isolated_home / "data" / "snapshots").mkdir(parents=True, exist_ok=True)
    (isolated_home / "data" / "snapshots" / "2026-01-01.json").write_text("[]", encoding="utf-8")
    assert not A.should_start_as_guest()                     # свои данные есть — демо не нужно


def test_command_palette(window, demo_positions):
    """Палитра ⌘K: тикер → карточка первой строкой, «ТИКЕР новости» → лента по бумаге, разделы настроек."""
    from PySide6.QtGui import QKeySequence
    from news import NewsItem
    from screens.palette import entries, rank
    window.on_loaded((demo_positions, None, None, []))
    window.news_items = [NewsItem("Сбербанк отчитался", "", "s", None, ["SBERP"])]
    window._fill_news_filter()
    items = entries(window)
    top = rank(items, "sberp", window)
    assert top[0].ticker == "SBERP" and top[0].kind == "бумага"
    assert rank(items, "сбер", window)[0].ticker == "SBERP"                  # по названию тоже
    assert "LKOH" in [e.ticker for e in rank(items, "нефть", window)]         # и по отрасли
    top[0].run()
    assert window._cards["SBERP"].isVisible(); window._cards["SBERP"].close()
    cmd = rank(items, "SBERP новости", window)
    assert len(cmd) == 1 and "новости" in cmd[0].title
    cmd[0].run()
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Рынок" and window.news_filter.currentData() == "SBERP"
    st = rank(items, "настройки скринер", window)
    assert st[0].title == "Настройки → Скринер"
    st[0].run()
    assert window.settings_win.tabs.tabText(window.settings_win.tabs.currentIndex()) == "Скринер"
    window.settings_win.close()
    assert rank(items, "идеи", window)[0].title == "Экран «Идеи»"
    assert window.act_palette.shortcut() == QKeySequence("Ctrl+K")


def test_numeric_columns_sort_as_numbers(window, demo_positions):
    """Регресс: таблицы сортировали числа как строки — «18,300» оказывался больше «192,000», «-7.3» выше «+10.8»."""
    from PySide6.QtCore import Qt
    window.on_loaded((demo_positions, None, None, []))
    window.set_privacy(False)
    t = window.tbl_pos
    col = next(c for c in range(t.columnCount()) if t.horizontalHeaderItem(c).text().startswith("Стоимость"))
    t.sortByColumn(col, Qt.DescendingOrder)
    vals = [float(t.item(r, col).text().replace(",", "")) for r in range(t.rowCount())]
    assert vals == sorted(vals, reverse=True)
    pl = t.columnCount() - 1
    t.sortByColumn(pl, Qt.AscendingOrder)
    pls = [float(t.item(r, pl).text()) for r in range(t.rowCount())]
    assert pls == sorted(pls)


def test_sort_item_keys(qapp):
    """Числа в любом оформлении — числами; даты, время и текст — текстом (иначе «2026-10-03» стало бы 2026)."""
    from widgets import SortItem
    k = lambda t: SortItem(t)._key()
    assert k("1,234.56") == 1234.56 and k("▲ +2.4%") == 2.4 and k("274.05 RUB") == 274.05 and k("−3.1") == -3.1
    assert k("2026-10-03") == "2026-10-03" and k("24.09 07:36") == "24.09 07:36" and k("SBERP") == "SBERP"
    assert k("—") is None and k("•••") is None
