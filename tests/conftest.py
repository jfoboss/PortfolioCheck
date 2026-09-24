"""Общие фикстуры. Все тесты идут без сети и без доступа к настоящим данным пользователя:
HOME подменяется на временный каталог, Qt работает в offscreen-режиме."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Каждый тест — с чистым HOME: конфиг, кэш и снапшоты не пересекаются с реальными."""
    # профиль пользователя подменяется на всех ОС: HOME (macOS/Linux), USERPROFILE и APPDATA (Windows)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    import paths
    local = paths._local_dir()                  # модуль мог импортироваться в прошлом тесте — пересчитываем
    monkeypatch.setattr(paths, "LOCAL_DIR", local)
    monkeypatch.setattr(paths, "POINTER", local / "storage.json")
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data", raising=False)
    # настоящие хранилища секретов (Keychain, Credential Manager, Secret Service) тесты не трогают — только файл
    import secrets_store as SEC
    monkeypatch.setattr(SEC, "_wincred", lambda: None)
    monkeypatch.setattr(SEC, "SECURITY", str(tmp_path / "no-security-binary"))
    monkeypatch.setattr(SEC, "_cache", {})
    (tmp_path / "data" / "cache").mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Любая попытка сходить в сеть из теста — ошибка: тесты не должны зависеть от внешних сервисов."""
    import urllib.request

    def blocked(*a, **k):
        raise RuntimeError("сеть в тестах запрещена — подмени вызов")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    yield


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test"])


@pytest.fixture
def demo_positions():
    import portfolio_check as pc
    pos = pc.load_snapshot(str(ROOT / "demo_snapshot.json"))
    for i, p in enumerate(pos):
        p.figi = p.figi or f"F{i}"
    return pos


@pytest.fixture
def demo_cfg():
    import strategy as ST
    return ST.apply_preset({"instruments": {}}, "dividend_defensive")
