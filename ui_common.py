"""
ui_common.py — общее для окна и экранов: имя приложения, цвета подсветки, токен, первичный конфиг.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor

import portfolio_check as pc
from settings_dialog import KEYRING_SERVICE, KEYRING_USER
import paths
import secrets_store as SEC
import config_migrate as CM


APP_NAME = "PortfolioCheck"
# Полупрозрачные системно-нейтральные оттенки: читаются и на светлом, и на тёмном фоне
TINT_GOOD = QColor(52, 199, 89, 60)     # системный зелёный
TINT_WARN = QColor(255, 159, 10, 70)    # системный оранжевый
TINT_BAD = QColor(255, 69, 58, 60)      # системный красный
MASK, mask_text = pc.MASK, pc.mask_text

SUPPORT_DIR = paths.LOCAL_DIR          # локальное: storage.json (указатель на папку данных)


# ---------------------------------------------------------------------------
# Токен в Keychain
# ---------------------------------------------------------------------------

def get_token() -> str | None:
    """Токен T-Invest: задаётся только через Настройки → Доступ; Keychain читается один раз за сессию."""
    return SEC.get(KEYRING_SERVICE, KEYRING_USER)


def ensure_config(path: Path) -> None:
    """Первый запуск: если конфига в Application Support нет — копируем дефолтный из бандла/исходников."""
    if path.exists():
        return
    default = Path(__file__).with_name("config.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    if default.exists():
        path.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        pc.save_config({CM.KEY: CM.CURRENT_VERSION, "account": {"id": ""}, "targets": {}, "instruments": {}, "fx_fallback": {},
                        "rules": {"drawdown_alert_pct": 10, "buy_candidates_classes": [], "frozen": {},
                                  "position_caps": {}, "single_name_max_pct": 11, "tail_threshold_rub": 30000}},
                       str(path))
