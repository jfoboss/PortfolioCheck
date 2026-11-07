"""
paths.py — где лежат данные приложения (config.yaml, ca-bundle.pem, snapshots/, state/) и локальный кэш.

По умолчанию — iCloud Drive/PortfolioCheck, если iCloud Drive на этом Mac включён: тогда настройки,
классификация, эталоны и снапшоты общие для всех твоих Mac без дополнительного софта.
Иначе — ~/Library/Application Support/PortfolioCheck. Выбор можно поменять в Настройки → Общие
(iCloud / локально / любая папка — Dropbox, MEGAsync и т.п.). Указатель на выбранную папку хранится
локально в storage.json. Токен API в общую папку не попадает — только Keychain этого Mac.

Кэш делится по цене потери:
  • state/ — в папке данных (iCloud): то, что дорого или нельзя восстановить — ответы ИИ (лимит 50 запросов в сутки
    у бесплатных моделей), блокировка дневного лимита, проверки моделей, состояние оповещений, история времени
    загрузок. Маленькие файлы, пишутся несколько раз в день — синхронизация почти ничего не стоит; ИИ-сводки
    переживают переустановку и второй Mac.
  • cache_dir() — локальная папка кэша ОС: свечи, скринер, выплаты, новости, календари — мегабайты, перезаписываются
    при каждом обновлении, восстанавливаются за одно обновление (T-Invest и сайты без дневного лимита). Раньше лежали
    в iCloud: каждое обновление гоняло их в облако, на втором Mac — конфликтные копии, выгруженный файл — ожидание.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

APP_NAME = "PortfolioCheck"


def _local_dir() -> Path:
    """Стандартная папка данных приложения для ОС: macOS — Application Support, Windows — %APPDATA%,
    Linux — $XDG_DATA_HOME (~/.local/share). Путь на macOS не менялся — существующие данные на месте."""
    import os
    import sys
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME


LOCAL_DIR = _local_dir()
ICLOUD_ROOT = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
ICLOUD_DIR = ICLOUD_ROOT / APP_NAME
POINTER = LOCAL_DIR / "storage.json"
DATA_FILES = ("config.yaml", "ca-bundle.pem")
# Из старого cache/ (в папке данных) — в state/: что дорого восстановить. ИИ — в state/ai/, остальное — в state/.
STATE_AI_FILES = ("ai_responses.json", "ai_summary.json", "ai_model_checks.json", "ai_rate_block.json")
STATE_FILES = ("alerts_state.json", "activity_times.json")


def _os_cache_root() -> Path:
    """Папка кэша ОС: macOS — ~/Library/Caches, Windows — %LOCALAPPDATA%, Linux — $XDG_CACHE_HOME (~/.cache)."""
    import os
    import sys
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_NAME


def icloud_available() -> bool:
    """iCloud Drive — только macOS. На Windows общая папка выбирается как «Другая папка» (OneDrive, iCloud для Windows…)."""
    import sys
    return sys.platform == "darwin" and ICLOUD_ROOT.is_dir()


DEMO_DIR: Path | None = None   # режим --demo (demo.py): временная папка вместо настоящей, указатель не трогаем
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
    if DEMO_DIR is not None:
        (DEMO_DIR / "snapshots").mkdir(parents=True, exist_ok=True)
        return DEMO_DIR
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
        import sys
        fallback_reason = (f"Нет доступа к папке данных {d} — работаю локально. " + (
            "Разрешение: Системные настройки → Конфиденциальность и безопасность → Файлы и папки → PortfolioCheck → "
            "iCloud Drive." if sys.platform == "darwin" else
            "Проверь, что папка существует и доступна (облачный диск подключён), и выбери её заново в Настройки → Общие."))
        d = LOCAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(exist_ok=True)
    # первый запуск с iCloud: если раньше всё лежало локально — переносим, чтобы ничего не потерять
    if d != LOCAL_DIR:
        _migrate(LOCAL_DIR, d, move=False)
    return d


def cache_dir() -> Path:
    """Локальный кэш (свечи, новости, скринер, выплаты, календари) — не в iCloud: пересчитывается за одно обновление.
    В демо — во временной папке демо."""
    d = (DEMO_DIR / "cache") if DEMO_DIR is not None else _os_cache_root()
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_dir() -> Path:
    """Дорогое в восстановлении (ИИ, оповещения, история времени) — в папке данных, синхронизируется с ней."""
    d = data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ai_dir() -> Path:
    """Ответы и сводки ИИ — по файлу на ключ: меньше конфликтов iCloud, когда программа открыта на двух Mac."""
    d = state_dir() / "ai"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _newer_wins(src: Path, dst: Path) -> None:
    """Перенести src → dst; если dst уже есть — оставить более свежий по времени изменения."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        src.unlink()
    else:
        shutil.move(str(src), str(dst))


def migrate_old_cache(data: Path, cache: Path, split_responses=None) -> dict:
    """Разовый переезд старого data/cache/: дорогое — в data/state/ (ИИ — в state/ai/), остальное — в локальный кэш.
    Файлы переносятся, а не удаляются: ни одна ИИ-сводка не теряется, свечи не качаются заново.
    split_responses(path, ai_dir) — разложить старый общий ai_responses.json по ключам (делает ai.py).
    Возвращает {"state": n, "cache": n} — сколько файлов куда ушло."""
    old = data / "cache"
    moved = {"state": 0, "cache": 0}
    if not old.is_dir() or old == cache:
        return moved
    ai, st = data / "state" / "ai", data / "state"
    for f in sorted(old.iterdir()):
        if not f.is_file() or f.name.endswith(".tmp"):
            continue
        if f.name == "ai_responses.json" and split_responses is not None:
            split_responses(f, ai); f.unlink(missing_ok=True); moved["state"] += 1
        elif f.name in STATE_AI_FILES or (f.name.startswith("exec_") and f.suffix == ".json"):
            _newer_wins(f, ai / f.name); moved["state"] += 1
        elif f.name in STATE_FILES:
            _newer_wins(f, st / f.name); moved["state"] += 1
        else:
            _newer_wins(f, cache / f.name); moved["cache"] += 1
    try:
        old.rmdir()                                      # пустой — убираем; остались чужие файлы — пусть лежат
    except OSError:
        pass
    return moved


def set_data_dir(new_dir: Path, move_files: bool = True) -> Path:
    """Сменить папку данных; при move_files перенести туда текущие файлы (если в новой их ещё нет)."""
    if DEMO_DIR is not None:
        raise RuntimeError("демо-режим: папка данных временная и не меняется")
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
    # state/ (ИИ, оповещения, история времени) — переезжает вместе с папкой данных, иначе ИИ-сводки потеряются
    sst = src / "state"
    if sst.is_dir():
        for f in sst.rglob("*.json"):
            t = dst / "state" / f.relative_to(sst)
            if not t.exists():
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
    ss, ds = src / "snapshots", dst / "snapshots"
    if ss.is_dir():
        ds.mkdir(exist_ok=True)
        for f in ss.glob("*.json"):
            if not (ds / f.name).exists():
                shutil.copy2(f, ds / f.name)
