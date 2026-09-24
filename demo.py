"""
demo.py — режим `--demo`: приложение на вымышленном портфеле (`demo_snapshot.json`) без токена и сети.

Для скриншотов README и осмотра интерфейса. Все данные — во временной папке, которая удаляется при выходе;
реальный конфиг, снапшоты и Keychain пользователя не читаются и не меняются, размеры окна не сохраняются.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import paths
import secrets_store as SEC

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))   # в собранном .app — ресурсы бандла
SNAPSHOT = ROOT / "demo_snapshot.json"
PRESET = "dividend_defensive"


def setup(base: Path | None = None) -> Path:
    """Готовит временную папку данных и переключает на неё приложение. Возвращает путь к демо-конфигу.

    Вызывать до первого обращения к paths.data_dir() и до чтения секретов.
    """
    import config_migrate as CM
    import portfolio_check as pc
    import strategy as ST
    import yaml

    d = Path(base) if base else Path(tempfile.mkdtemp(prefix="PortfolioCheck-demo-"))
    paths.DEMO_DIR = d
    SEC.DISABLED = True                           # Keychain не читаем и не пишем: секреты — только на сессию
    (d / "cache").mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SNAPSHOT, d / "snapshots" / f"{datetime.now():%Y-%m-%d}.json")

    tpl = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    cfg = ST.apply_preset(tpl, PRESET)
    positions = pc.load_snapshot(str(SNAPSHOT))
    cfg["instruments"] = {p.ticker: p.cls for p in positions if p.cls and p.cls != "unclassified"}
    cfg["setup_done"] = True
    cfg["refresh"] = {**(cfg.get("refresh") or {}), "on_launch": False, "privacy_on_launch": False}
    cfg[CM.KEY] = CM.CURRENT_VERSION
    path = d / "config.yaml"
    pc.save_config(cfg, str(path))
    return path


def cleanup() -> None:
    d = paths.DEMO_DIR
    if d is not None and d.name.startswith("PortfolioCheck-demo-"):
        shutil.rmtree(d, ignore_errors=True)
