"""
paths.py — где лежат данные приложения (config.yaml, ca-bundle.pem, snapshots/).

По умолчанию — iCloud Drive/PortfolioCheck, если iCloud Drive на этом Mac включён: тогда настройки,
классификация, эталоны и снапшоты общие для всех твоих Mac без дополнительного софта.
Иначе — ~/Library/Application Support/PortfolioCheck. Выбор можно поменять в Настройки → Общие
(iCloud / локально / любая папка — Dropbox, MEGAsync и т.п.). Указатель на выбранную папку хранится
локально в storage.json. Токен API в общую папку не попадает — только Keychain этого Mac.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

APP_NAME = "PortfolioCheck"
LOCAL_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
ICLOUD_ROOT = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
ICLOUD_DIR = ICLOUD_ROOT / APP_NAME
POINTER = LOCAL_DIR / "storage.json"
DATA_FILES = ("config.yaml", "ca-bundle.pem")


def icloud_available() -> bool:
    return ICLOUD_ROOT.is_dir()


fallback_reason: str = ""    # заполняется, если выбранная папка недоступна и пришлось уйти на локальную


def _accessible(d: Path) -> bool:
    """Проверка, что папку можно создать, прочитать и записать (macOS может запретить iCloud Drive по TCC)."""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.read_text(encoding="utf-8")
        probe.unlink()
        for name in DATA_FILES:
            f = d / name
            if f.exists():
                f.open("rb").close()
        return True
    except Exception:  # noqa: BLE001
        return False


def data_dir() -> Path:
    """Текущая папка данных. Создаёт её и при первом запуске переносит локальные файлы, если они есть.
    Если выбранная папка недоступна (нет разрешения macOS на iCloud Drive) — тихо работает локально."""
    global fallback_reason
    d = None
    if POINTER.exists():
        try:
            d = Path(json.loads(POINTER.read_text(encoding="utf-8"))["dir"]).expanduser()
        except Exception:  # noqa: BLE001
            d = None
    if d is None:
        d = ICLOUD_DIR if (icloud_available() and _accessible(ICLOUD_DIR)) else LOCAL_DIR
        _write_pointer(d)
    if d != LOCAL_DIR and not _accessible(d):
        fallback_reason = (f"Нет доступа к папке данных {d} — работаю локально. Разрешение: Системные настройки → "
                           "Конфиденциальность и безопасность → Файлы и папки → PortfolioCheck → iCloud Drive.")
        d = LOCAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(exist_ok=True)
    # первый запуск с iCloud: если раньше всё лежало локально — переносим, чтобы ничего не потерять
    if d != LOCAL_DIR:
        _migrate(LOCAL_DIR, d, move=False)
    return d


def set_data_dir(new_dir: Path, move_files: bool = True) -> Path:
    """Сменить папку данных; при move_files перенести туда текущие файлы (если в новой их ещё нет)."""
    old = data_dir()
    new_dir = new_dir.expanduser()
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "snapshots").mkdir(exist_ok=True)
    if move_files and old != new_dir:
        _migrate(old, new_dir, move=False)
    _write_pointer(new_dir)
    return new_dir


def mode_of(d: Path) -> str:
    if d == ICLOUD_DIR:
        return "icloud"
    if d == LOCAL_DIR:
        return "local"
    return "custom"


def _write_pointer(d: Path) -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(json.dumps({"dir": str(d)}, ensure_ascii=False), encoding="utf-8")


def _migrate(src: Path, dst: Path, move: bool) -> None:
    if not src.is_dir() or src == dst:
        return
    for name in DATA_FILES:
        s, t = src / name, dst / name
        if s.exists() and not t.exists():
            shutil.copy2(s, t)
    ss, ds = src / "snapshots", dst / "snapshots"
    if ss.is_dir():
        ds.mkdir(exist_ok=True)
        for f in ss.glob("*.json"):
            if not (ds / f.name).exists():
                shutil.copy2(f, ds / f.name)
