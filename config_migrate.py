"""
config_migrate.py — версия конфига пользователя и явные миграции между версиями.

Зачем: раньше часть данных жила «неявно» — если ключа в конфиге нет, код подставлял свой дефолт. Стоило убрать
дефолт из кода, и у существующего пользователя данные пропадали (так исчез чёрный список скринера).
Теперь правило такое:

  * в конфиге есть `config_version`; код читает конфиг как есть и не домешивает к данным пользователя дефолты;
  * меняешь или удаляешь дефолт, от которого могли зависеть данные, — поднимаешь CURRENT_VERSION и пишешь
    миграцию, которая записывает в конфиг старое значение явно. Значения дефолтов в миграции — замороженная
    копия на момент её написания, а не ссылка на константу в коде (константу потом удалят);
  * перед записью мигрированного конфига рядом сохраняется копия исходного файла `config.yaml.v<N>.bak`.

Модуль без Qt и без сети: проверяется тестами (`tests/test_config_migrate.py`).
"""
from __future__ import annotations

import copy
import logging
import shutil
from pathlib import Path
from typing import Callable

log = logging.getLogger("portfolio.config")

KEY = "config_version"
CURRENT_VERSION = 2

# Чёрный список скринера, который код домешивал к конфигу, пока в нём не было blacklist_customized.
# Замороженная копия screener.DEFAULT_BLACKLIST на момент введения версий (он уже был пуст — личные тикеры автора
# убраны при обезличивании, вернуть их отсюда нельзя). Не заменять ссылкой на константу в screener.py.
V1_BLACKLIST_DEFAULTS: dict[str, str] = {}


def version_of(cfg: dict) -> int:
    """Версия конфига; конфиги до введения версий — 0."""
    try:
        return int((cfg or {}).get(KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def normalize_screener(sc: dict | None, defaults: dict[str, str] | None = None) -> dict:
    """Раздел screener в формате v1: blacklist — словарь тикер → причина, без неявных дефолтов.

    Старые форматы: список тикеров вместо словаря; флаг blacklist_customized=false означал «к списку добавить
    дефолты из кода» — их записываем явно. Используется и миграцией, и импортом стратегии (файл мог быть
    выгружен старой версией программы).
    """
    sc = dict(sc) if isinstance(sc, dict) else {}
    bl = sc.get("blacklist") or {}
    if isinstance(bl, list):
        bl = {str(t): "" for t in bl}
    elif not isinstance(bl, dict):
        bl = {}
    bl = {str(t).upper(): ("" if r is None else str(r)) for t, r in bl.items()}
    if not sc.get("blacklist_customized", False):
        base = V1_BLACKLIST_DEFAULTS if defaults is None else defaults
        bl = {**base, **bl}
    sc["blacklist"] = bl
    sc.pop("blacklist_customized", None)
    return sc


def _to_v1(cfg: dict) -> str:
    had = "screener" in cfg
    before = (cfg.get("screener") or {}).get("blacklist")
    cfg["screener"] = normalize_screener(cfg.get("screener"))
    n = len(cfg["screener"]["blacklist"])
    if not had and not n:
        del cfg["screener"]                        # раздела не было и данных нет — не плодим пустой
        return "v1: неявных дефолтов скринера нет"
    return f"v1: чёрный список скринера записан явно ({n} бумаг, было {len(before or {})})"


# Оповещения по умолчанию на момент их появления (v2) — замороженная копия, не ссылка на код.
V2_DEFAULT_ALERTS = [
    {"kind": "corridor", "ticker": "", "value": 0, "on": True},
    {"kind": "days_to_cut", "ticker": "", "value": 7, "on": True},
    {"kind": "news", "ticker": "", "value": 0, "on": False},
]


def _to_v2(cfg: dict) -> str:
    if "alerts" in cfg:
        return "v2: раздел оповещений уже есть"
    cfg["alerts"] = copy.deepcopy(V2_DEFAULT_ALERTS)
    return "v2: добавлены оповещения по умолчанию (выход из коридора, 7 дней до отсечки; новости — выключены)"


# версия, в которую переводит миграция → функция(cfg) -> описание для лога. Меняет cfg на месте.
MIGRATIONS: dict[int, Callable[[dict], str]] = {
    1: _to_v1,
    2: _to_v2,
}


def migrate(cfg: dict | None) -> tuple[dict, list[str]]:
    """(новый конфиг, описания применённых шагов). Исходный словарь не меняется.

    Конфиг новее программы (после отката версии) не трогаем: шаги вниз не пишутся, а перезапись могла бы
    стереть незнакомые этой версии данные — возвращаем как есть с предупреждением.
    """
    cfg = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    v = version_of(cfg)
    if v > CURRENT_VERSION:
        return cfg, [f"конфиг версии {v} новее программы (знает до {CURRENT_VERSION}) — миграции не применяются"]
    notes = []
    while v < CURRENT_VERSION:
        v += 1
        notes.append(MIGRATIONS[v](cfg))
        cfg[KEY] = v
    if notes:                                      # версия — первой строкой файла, её видно сразу
        cfg = {KEY: cfg.pop(KEY), **cfg}
    return cfg, notes


def backup_path(path: Path, version: int) -> Path:
    return path.with_name(f"{path.name}.v{version}.bak")


def load_and_migrate(path: str | Path, save: Callable[[dict, str], None] | None = None) -> dict:
    """Читает конфиг и, если он старее программы, мигрирует и сохраняет, оставив рядом копию исходного файла.

    Если копия этой версии уже есть (миграция прерывалась), её не перезаписываем — в ней оригинал.
    """
    import portfolio_check as pc
    path = Path(path)
    raw = pc.load_config(str(path))
    old = version_of(raw)
    cfg, notes = migrate(raw)
    if old > CURRENT_VERSION:
        log.warning("%s: %s", path.name, notes[0])
        return cfg
    if notes:
        for n in notes:
            log.info("миграция конфига %s: %s", path.name, n)
        bak = backup_path(path, old)
        try:
            if not bak.exists():
                shutil.copy2(path, bak)
            (save or pc.save_config)(cfg, str(path))
        except OSError as e:
            # не смогли записать (например, iCloud без доступа) — работаем с мигрированным конфигом в памяти;
            # падать нельзя: окно тогда откатилось бы на пустой дефолтный конфиг
            log.warning("мигрированный конфиг не сохранён (%s): %s", path, e)
            return cfg
        log.info("конфиг обновлён до версии %d, копия прежнего — %s", CURRENT_VERSION, bak.name)
    return cfg
