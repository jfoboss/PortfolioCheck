"""make_icon.py — растеризует icon.svg в набор PNG для .iconset (macOS). Запуск: python make_icon.py
Дальше на Mac: iconutil -c icns icon.iconset -o PortfolioCheck.icns (build_mac.sh делает это сам)."""
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

SIZES = [16, 32, 64, 128, 256, 512, 1024]
here = Path(__file__).parent
out = here / "icon.iconset"
out.mkdir(exist_ok=True)
app = QGuiApplication(sys.argv)
r = QSvgRenderer(str(here / "icon.svg"))
for s in SIZES:
    img = QImage(s, s, QImage.Format_ARGB32); img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); r.render(p); p.end()
    if s <= 512:
        img.save(str(out / f"icon_{s}x{s}.png"))
    if s >= 32:
        img.save(str(out / f"icon_{s // 2}x{s // 2}@2x.png"))
print(f"iconset готов: {out} ({len(list(out.glob('*.png')))} файлов)")
