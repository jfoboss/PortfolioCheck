"""
osutil.py — различия macOS / Windows / Linux в одном месте: платформа, подписи сочетаний клавиш, моноширинный шрифт.

Правило: код экранов не проверяет sys.platform сам — берёт отсюда. Так порт на новую ОС — правка одного модуля.
"""
from __future__ import annotations

import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# где лежат секреты — для подписей в UI
SECRET_STORE = "диспетчере учётных данных Windows" if IS_WIN else "Keychain"      # «хранится в …»
SECRET_STORE_NAME = "Диспетчер учётных данных Windows" if IS_WIN else "Keychain"   # «… не принял запись»
MACHINE = "компьютера" if not IS_MAC else "Mac"


def native_keys(seq: str) -> str:
    """«Ctrl+Shift+H» → «⇧⌘H» на macOS, «Ctrl+Shift+H» на Windows/Linux — для подсказок и подписей в UI.
    В Qt «Ctrl» на macOS означает ⌘, поэтому сочетания задаются один раз, а показываются по-местному."""
    from PySide6.QtGui import QKeySequence
    return QKeySequence(seq).toString(QKeySequence.NativeText)


def mono_font(point_size: int = 11):
    """Системный моноширинный шрифт (Menlo на macOS, Consolas на Windows) — вместо зашитого «Menlo»."""
    from PySide6.QtGui import QFontDatabase
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(point_size)
    return f
