"""
build_windows.py — сборка PortfolioCheck для Windows: dist/PortfolioCheck/PortfolioCheck.exe и zip-архив.

Запуск (Windows, Python 3.12, из корня репозитория):
    py -3.12 -m venv .venv && .venv\\Scripts\\activate
    pip install --no-deps "git+https://github.com/RussianInvestments/invest-python.git"
    pip install -r requirements.txt pillow
    python build_windows.py

То же делает CI (.github/workflows/ci.yml) — артефакт PortfolioCheck-windows.zip. Подписи кода пока нет:
при первом запуске Windows SmartScreen покажет «Неизвестный издатель» → «Подробнее» → «Выполнить в любом случае».
Список модулей — такой же, как в build_mac.sh (сверяет tests/test_platform.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HIDDEN = ["settings_dialog", "forecast", "charts", "macro", "paths", "history", "news", "screener", "diagnostics", "ai",
          "secrets_store", "strategy", "config_migrate", "demo", "ui_common", "widgets", "workers", "osutil", "wizard",
          "portfolio_check", "keyring.backends.Windows"]
DATA = ["icon.svg", "config.yaml", "demo_snapshot.json", "docs"]


def make_ico(out: Path) -> Path | None:
    """icon.svg → .ico (16–256 px): Qt рисует PNG, Pillow собирает ICO. Без Pillow — сборка без иконки."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow не установлен — сборка без иконки (pip install pillow)")
        return None
    from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication(["icon"])  # noqa: F841 — нужен для QPainter
    png = out.with_suffix(".png")
    img = QImage(256, 256, QImage.Format_ARGB32); img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); QSvgRenderer(str(ROOT / "icon.svg")).render(p); p.end()
    img.save(str(png))
    Image.open(png).save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return out


def main() -> None:
    if not sys.platform.startswith("win") and "--force" not in sys.argv:
        sys.exit("Сборка для Windows запускается на Windows (для проверки аргументов: --force)")
    os.chdir(ROOT)
    (ROOT / "build").mkdir(exist_ok=True)
    ico = make_ico(ROOT / "build" / "PortfolioCheck.ico")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "PortfolioCheck",
            "--collect-all", "tinkoff", "--collect-submodules", "screens"]
    if ico:
        args += ["--icon", str(ico)]
    for m in HIDDEN:
        args += ["--hidden-import", m]
    for d in DATA:
        args += ["--add-data", f"{d}{os.pathsep}{d if Path(d).is_dir() else '.'}"]
    args.append("app.py")
    print(" ".join(args))
    if "--dry-run" in sys.argv:
        return
    subprocess.run(args, check=True)
    archive = shutil.make_archive(str(ROOT / "dist" / "PortfolioCheck-windows"), "zip", ROOT / "dist", "PortfolioCheck")
    print(f"Готово: {archive}")


if __name__ == "__main__":
    main()
