"""Версия конфига и миграции (config_migrate.py)."""
import yaml

import config_migrate as CM


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_v0_blacklist_defaults_become_explicit(monkeypatch):
    """Регресс: чёрный список скринера пропал у существующего пользователя, когда дефолт удалили из кода.

    Миграция записывает дефолты той версии в конфиг явно; после этого код их больше не домешивает.
    """
    monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {"AAA": "из дефолта", "BBB": "из дефолта"})
    old = {"screener": {"blacklist": {"BBB": "своя причина", "ccc": None}}}
    cfg, notes = CM.migrate(old)
    assert cfg["config_version"] == CM.CURRENT_VERSION and notes
    assert cfg["screener"]["blacklist"] == {"AAA": "из дефолта", "BBB": "своя причина", "CCC": ""}
    assert "blacklist_customized" not in cfg["screener"]
    assert old == {"screener": {"blacklist": {"BBB": "своя причина", "ccc": None}}}    # исходник не меняется
    monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {})     # дефолт «удалили из кода» — данные на месте
    assert CM.migrate(cfg) == (cfg, [])


def test_v0_customized_list_is_kept_as_is(monkeypatch):
    monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {"AAA": "из дефолта"})
    cfg, _ = CM.migrate({"screener": {"blacklist": ["XXX", "YYY"], "blacklist_customized": True, "max_pe": 12}})
    assert cfg["screener"] == {"blacklist": {"XXX": "", "YYY": ""}, "max_pe": 12}
    assert list(cfg)[0] == "config_version"


def test_no_screener_section_stays_absent():
    cfg, notes = CM.migrate({"instruments": {"SBERP": "equity_core"}})
    assert cfg == {"config_version": CM.CURRENT_VERSION, "instruments": {"SBERP": "equity_core"},
                   "alerts": CM.V2_DEFAULT_ALERTS} and notes


def test_v2_adds_default_alerts_once():
    """v2: оповещения по умолчанию записываются в конфиг явно; свои правила пользователя не трогаются."""
    cfg, _ = CM.migrate({"config_version": 1})
    assert cfg["alerts"] == CM.V2_DEFAULT_ALERTS and cfg["alerts"] is not CM.V2_DEFAULT_ALERTS
    mine = [{"kind": "price_above", "ticker": "SBERP", "value": 400, "on": True}]
    cfg, _ = CM.migrate({"config_version": 1, "alerts": mine})
    assert cfg["alerts"] == mine


def test_newer_config_is_not_touched():
    raw = {"config_version": 99, "screener": {"blacklist": ["X"]}, "future": {"a": 1}}
    cfg, notes = CM.migrate(raw)
    assert cfg == raw and "новее" in notes[0]


def test_load_and_migrate_writes_backup_once(isolated_home):
    p = _write(isolated_home / "data" / "config.yaml",
               "screener:\n  blacklist: [XXX]\n  blacklist_customized: false\n")
    original = p.read_text(encoding="utf-8")
    cfg = CM.load_and_migrate(p)
    assert cfg["screener"]["blacklist"] == {"XXX": ""}
    bak = p.with_name("config.yaml.v0.bak")
    assert bak.read_text(encoding="utf-8") == original
    on_disk = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert on_disk == cfg and on_disk["config_version"] == CM.CURRENT_VERSION
    mtime = p.stat().st_mtime_ns
    assert CM.load_and_migrate(p) == cfg                  # актуальный конфиг не переписывается
    assert p.stat().st_mtime_ns == mtime


def test_newer_config_file_is_not_rewritten(isolated_home):
    p = _write(isolated_home / "data" / "config.yaml", "config_version: 99\nscreener:\n  blacklist: [X]\n")
    before = p.read_text(encoding="utf-8")
    CM.load_and_migrate(p)
    assert p.read_text(encoding="utf-8") == before
    assert not list(p.parent.glob("*.bak"))


def test_template_config_is_current():
    """Шаблон для нового пользователя не должен требовать миграции."""
    from pathlib import Path
    tpl = yaml.safe_load((Path(CM.__file__).with_name("config.yaml")).read_text(encoding="utf-8"))
    assert CM.version_of(tpl) == CM.CURRENT_VERSION
    assert CM.migrate(tpl) == (tpl, [])


def test_strategy_import_normalizes_old_screener(monkeypatch):
    import strategy as ST
    monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {"AAA": "из дефолта"})
    new, _, _ = ST.import_strategy({"config_version": 1}, "screener:\n  blacklist: [xxx]\n")
    assert new["screener"] == {"blacklist": {"AAA": "из дефолта", "XXX": ""}}
    # файл текущего формата (с версией) дефолтов прежних версий не получает
    new, _, _ = ST.import_strategy({"config_version": 1}, "config_version: 1\nscreener:\n  blacklist: {XXX: ''}\n")
    assert new["screener"] == {"blacklist": {"XXX": ""}}
    exported = yaml.safe_load(ST.export_strategy(new))
    assert exported["config_version"] == CM.CURRENT_VERSION


def test_window_reads_blacklist_only_from_config(qapp, monkeypatch, isolated_home):
    """Регресс: окно домешивало дефолты скринера к конфигу — теперь чёрный список только из конфига (после миграции)."""
    import app as A
    import wizard as W
    monkeypatch.setattr(W.SetupWizard, "exec", lambda self: 0)
    monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {"AAA": "из дефолта"})
    p = _write(isolated_home / "data" / "config.yaml",
               "instruments:\n  SBERP: equity_core\nscreener:\n  blacklist: {BBB: причина}\n")
    w = A.MainWindow(str(p))
    try:
        assert w._blacklist() == {"AAA": "из дефолта", "BBB": "причина"}
        monkeypatch.setattr(CM, "V1_BLACKLIST_DEFAULTS", {})
        w2 = A.MainWindow(str(p))
        assert w2._blacklist() == {"AAA": "из дефолта", "BBB": "причина"}
        w2.close()
    finally:
        w.close()


def test_settings_save_keeps_version(qapp, monkeypatch, isolated_home):
    """Регресс: collect() собирает конфиг заново и терял config_version — миграция повторялась после
    каждого сохранения настроек и возвращала удалённые пользователем бумаги в чёрный список."""
    import app as A
    import wizard as W
    monkeypatch.setattr(W.SetupWizard, "exec", lambda self: 0)
    p = _write(isolated_home / "data" / "config.yaml", "instruments:\n  SBERP: equity_core\n")
    w = A.MainWindow(str(p))
    try:
        w.open_settings()
        w.on_config_changed(w.settings_win.collect())
        w.settings_win.close()
        assert yaml.safe_load(p.read_text(encoding="utf-8"))["config_version"] == CM.CURRENT_VERSION
    finally:
        w.close()


def test_write_failure_keeps_migrated_config(isolated_home):
    """Ошибка записи при миграции не должна ронять загрузку (иначе окно откатилось бы на пустой конфиг)."""
    p = _write(isolated_home / "data" / "config.yaml", "screener:\n  blacklist: [XXX]\n")

    def failing_save(cfg, path):
        raise PermissionError("нет доступа")
    cfg = CM.load_and_migrate(p, save=failing_save)
    assert cfg["screener"]["blacklist"] == {"XXX": ""} and cfg["config_version"] == CM.CURRENT_VERSION
