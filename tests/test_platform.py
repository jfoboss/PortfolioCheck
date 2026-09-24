"""Различия ОС: папки данных, хранилище секретов на Windows, бандл сертификатов без openssl, сборка."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_dir_per_os(monkeypatch, tmp_path):
    import paths
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths._local_dir() == Path.home() / "Library" / "Application Support" / "PortfolioCheck"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths._local_dir() == tmp_path / "Roaming" / "PortfolioCheck"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths._local_dir() == tmp_path / "xdg" / "PortfolioCheck"
    assert not paths.icloud_available()                    # iCloud — только macOS


def test_windows_secrets_go_to_credential_manager(monkeypatch):
    """На Windows токен — в диспетчере учётных данных (keyring), не в файле."""
    import secrets_store as SEC
    store = {}
    fake = type("KR", (), {
        "get_password": staticmethod(lambda s, u: store.get((s, u))),
        "set_password": staticmethod(lambda s, u, v: store.__setitem__((s, u), v)),
        "delete_password": staticmethod(lambda s, u: store.pop((s, u))),
    })
    monkeypatch.setattr(SEC, "USE_KEYRING", True)
    monkeypatch.setattr(SEC, "_wincred", lambda: fake)
    monkeypatch.setattr(SEC, "_cache", {})
    assert SEC.set("svc", "u", "t.секрет") == "keychain"
    assert store[("svc", "u")] == "t.секрет" and not SEC._file("svc", "u").exists()
    SEC._cache.clear()
    assert SEC.get("svc", "u") == "t.секрет"
    SEC.delete("svc", "u")
    SEC._cache.clear()
    assert SEC.get("svc", "u") is None


def test_ca_bundle_built_without_openssl(monkeypatch, tmp_path):
    """Бандл собирается из цепочки, полученной средствами Python (в Windows нет консольного openssl)."""
    import settings_dialog as SD
    leaf = ("-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----", "CN=*.tinkoff.ru")
    root = ("-----BEGIN CERTIFICATE-----\nROOT\n-----END CERTIFICATE-----", "CN=Russian Trusted Root CA")
    monkeypatch.setattr(SD, "server_chain", lambda host: [leaf, root])
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("вызван subprocess")))
    out = tmp_path / "ca-bundle.pem"
    msg = SD.build_ca_bundle(out)
    text = out.read_text(encoding="utf-8")
    assert "ROOT" in text and "LEAF" not in text and "Russian Trusted Root CA" in msg
    assert text.count("BEGIN CERTIFICATE") > 10                # плюс корни grpcio


def _app_modules():
    mods = {p.stem for p in (ROOT / "src").glob("*.py")}
    return mods


def test_build_scripts_include_all_modules():
    """Новый модуль, забытый в сборке, всплывает только в собранном .app/.exe — ловим заранее."""
    mac = open(ROOT / "build_mac.sh", encoding="utf-8").read()
    win = open(ROOT / "build.py", encoding="utf-8").read()
    for m in _app_modules() - {"app"}:
        assert re.search(rf"--hidden-import {m}\b", mac), f"build_mac.sh: нет {m}"
        assert f'"{m}"' in win, f"build.py: нет {m}"
    for data in ("config.yaml", "demo_snapshot.json", "icon.svg", "docs"):
        assert data in mac and data in win
    assert "screens" in mac and "screens" in win
