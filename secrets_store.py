"""
secrets_store.py — единая точка доступа к секретам (токен T-Invest, ключи ИИ).

Хранилище — macOS Keychain через системную утилиту /usr/bin/security (подписана Apple). В отличие от библиотеки
keyring (старый SecKeychain API, который отказывает при любой смене подписи бандла: «make sure executable is
signed»), утилита работает из любого приложения; Keychain один раз спрашивает разрешение для самой утилиты —
«Всегда разрешать» — и это переживает пересборки. Читается один раз за сессию (кэш в памяти).
Если security недоступна (не macOS) или отказала — запасной файл 0600 в локальной папке.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import paths

log = logging.getLogger("portfolio.secrets")
_cache: dict[tuple[str, str], str | None] = {}
DISABLED = False   # режим --demo: Keychain и файл не трогаем, введённое живёт только в памяти до выхода
SECURITY = shutil.which("security") or "/usr/bin/security"


def _file(service: str, user: str) -> Path:
    return paths.LOCAL_DIR / f"secret_{service}_{user}".replace("/", "_")


def _sec(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([SECURITY, *args], capture_output=True, text=True, timeout=timeout)


def _kc_get(service: str, user: str) -> str | None:
    if not os.path.exists(SECURITY):
        return None
    try:
        r = _sec("find-generic-password", "-s", service, "-a", user, "-w")
    except Exception as e:  # noqa: BLE001
        log.error("security find: %s", e); return None
    if r.returncode == 0:
        return r.stdout.strip() or None
    if "could not be found" not in (r.stderr or ""):
        log.warning("security find %s/%s: rc=%s %s", service, user, r.returncode, (r.stderr or "").strip()[:160])
    return None


def _kc_set(service: str, user: str, value: str) -> bool:
    if not os.path.exists(SECURITY):
        return False
    r = _sec("add-generic-password", "-s", service, "-a", user, "-w", value, "-U", "-l", f"PortfolioCheck {service}",
             "-T", SECURITY, "-T", "")
    if r.returncode != 0:
        log.error("security add %s/%s: rc=%s %s", service, user, r.returncode, (r.stderr or "").strip()[:160])
    return r.returncode == 0


def _kc_delete(service: str, user: str) -> None:
    if os.path.exists(SECURITY):
        _sec("delete-generic-password", "-s", service, "-a", user)


def get(service: str, user: str) -> str | None:
    """Секрет из Keychain (один запрос за сессию), иначе из файла."""
    key = (service, user)
    if DISABLED:
        return _cache.get(key)
    if key in _cache:
        return _cache[key]
    val = _kc_get(service, user)
    if not val:
        f = _file(service, user)
        if f.exists():
            val = f.read_text(encoding="utf-8").strip() or None
    _cache[key] = val
    return val


def set(service: str, user: str, value: str) -> str:
    """Возвращает, куда сохранили: 'keychain' | 'file' | 'session' (демо)."""
    if DISABLED:
        _cache[(service, user)] = value
        return "session"
    if _kc_set(service, user, value):
        f = _file(service, user)
        if f.exists():
            f.unlink()
        where = "keychain"
    else:
        f = _file(service, user); f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(value, encoding="utf-8"); os.chmod(f, 0o600)
        where = "file"
    _cache[(service, user)] = value
    return where


def delete(service: str, user: str) -> None:
    if DISABLED:
        _cache[(service, user)] = None
        return
    _kc_delete(service, user)
    f = _file(service, user)
    if f.exists():
        f.unlink()
    _cache[(service, user)] = None


def where(service: str, user: str) -> str:
    """'keychain' | 'file' | '' — без лишнего запроса к Keychain."""
    if get(service, user) is None:
        return ""
    if DISABLED:
        return "session"
    return "file" if _file(service, user).exists() else "keychain"
