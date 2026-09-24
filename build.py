"""
build.py — сборка PortfolioCheck для Windows и Linux (macOS — ./build_mac.sh: там .app, подпись и .dmg).

    Windows:  dist/PortfolioCheck/PortfolioCheck.exe  → dist/PortfolioCheck-windows.zip
    Linux:    dist/PortfolioCheck/PortfolioCheck      → dist/PortfolioCheck-linux-x86_64.tar.gz
              (внутри — ярлык PortfolioCheck.desktop, иконка и install.sh для меню приложений)

Запуск из корня репозитория в окружении с зависимостями (см. README → «Сборка из исходников»):
    python build.py            # сборка для текущей ОС
    python build.py --dry-run  # только показать аргументы PyInstaller

То же делают CI (.github/workflows/ci.yml, артефакты) и релиз (release.yml). Подписи кода нет.
Список модулей — такой же, как в build_mac.sh (сверяет tests/test_platform.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HIDDEN = ["settings_dialog", "forecast", "charts", "macro", "paths", "history", "news", "screener", "diagnostics", "ai",
          "secrets_store", "strategy", "config_migrate", "demo", "ui_common", "widgets", "workers", "osutil", "payouts", "attribution", "export", "risk", "alerts",
          "wizard", "portfolio_check"]
KEYRING_BACKENDS = {"win32": ["keyring.backends.Windows"], "linux": ["keyring.backends.SecretService"]}
DATA = ["icon.svg", "config.yaml", "demo_snapshot.json", "docs"]
IS_WIN = sys.platform.startswith("win")

DESKTOP = """[Desktop Entry]
Type=Application
Name=PortfolioCheck
Comment=Проверка портфеля в Т-Инвестициях против своих правил
Exec={exec}
Icon={icon}
Terminal=false
Categories=Office;Finance;
"""

INSTALL_SH = """#!/usr/bin/env bash
# Добавляет PortfolioCheck в меню приложений текущего пользователя (без sudo).
# Убрать из меню: удалить applications/portfoliocheck.desktop и icons/…/portfoliocheck.png (пути ниже).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$APPS" "$ICONS"
cp "$HERE/portfoliocheck.png" "$ICONS/portfoliocheck.png"
sed "s|@DIR@|$HERE|g" "$HERE/PortfolioCheck.desktop.in" > "$APPS/portfoliocheck.desktop"
chmod +x "$APPS/portfoliocheck.desktop"
echo "Готово: PortfolioCheck в меню приложений ($APPS/portfoliocheck.desktop)"
"""


def render_icon(png: Path, size: int = 256) -> Path:
    """icon.svg → PNG средствами Qt (уже в зависимостях). Без дисплея (CI, сервер) — offscreen."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication(["icon"])  # noqa: F841 — нужен для QPainter
    img = QImage(size, size, QImage.Format_ARGB32); img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); QSvgRenderer(str(ROOT / "icon.svg")).render(p); p.end()
    png.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(png))
    return png


def make_ico(out: Path) -> Path | None:
    """Windows: PNG → .ico (16–256 px) через Pillow. Без Pillow — сборка без иконки."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow не установлен — сборка без иконки (pip install pillow)")
        return None
    png = render_icon(out.with_suffix(".png"))
    Image.open(png).save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return out


def pyinstaller_args(target: str, icon: Path | None) -> list[str]:
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "PortfolioCheck",
            "--collect-all", "tinkoff", "--collect-submodules", "screens"]
    if icon:
        args += ["--icon", str(icon)]
    for m in HIDDEN + KEYRING_BACKENDS.get(target, []):
        args += ["--hidden-import", m]
    for d in DATA:
        args += ["--add-data", f"{d}{os.pathsep}{d if (ROOT / d).is_dir() else '.'}"]
    return args + ["app.py"]


def package_linux() -> Path:
    """Ярлык, иконка и install.sh — рядом с программой; архив tar.gz (права на исполнение сохраняются)."""
    app_dir = ROOT / "dist" / "PortfolioCheck"
    render_icon(app_dir / "portfoliocheck.png")
    (app_dir / "PortfolioCheck.desktop.in").write_text(
        DESKTOP.format(exec='"@DIR@/PortfolioCheck"', icon="portfoliocheck"), encoding="utf-8")
    inst = app_dir / "install.sh"
    inst.write_text(INSTALL_SH, encoding="utf-8"); inst.chmod(0o755)
    out = ROOT / "dist" / "PortfolioCheck-linux-x86_64.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(app_dir, arcname="PortfolioCheck")
    return out


def main() -> None:
    target = "win32" if IS_WIN else ("linux" if sys.platform.startswith("linux") else "")
    if not target:
        sys.exit("build.py — для Windows и Linux; на macOS используй ./build_mac.sh")
    os.chdir(ROOT)
    (ROOT / "build").mkdir(exist_ok=True)
    icon = make_ico(ROOT / "build" / "PortfolioCheck.ico") if target == "win32" else None
    args = pyinstaller_args(target, icon)
    print(" ".join(args))
    if "--dry-run" in sys.argv:
        return
    subprocess.run(args, check=True)
    if target == "win32":
        out = shutil.make_archive(str(ROOT / "dist" / "PortfolioCheck-windows"), "zip", ROOT / "dist", "PortfolioCheck")
    else:
        out = package_linux()
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
