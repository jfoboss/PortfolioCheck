"""Режим --demo: вымышленный портфель без токена, сети и записи в настоящий профиль."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Отдельный процесс: conftest подменяет paths.data_dir, а здесь проверяется настоящая логика выбора папки.
SCRIPT = r"""
import sys
from pathlib import Path
import demo, paths, secrets_store as SEC
cfg_path = demo.setup()
d = paths.DEMO_DIR
assert paths.data_dir() == d and cfg_path.parent == d, (paths.data_dir(), d)
from PySide6.QtWidgets import QApplication
app = QApplication(["demo"])
import app as A
w = A.MainWindow(str(cfg_path))
assert "ДЕМО" in w.windowTitle()
assert len(w.positions) > 0 and not w.needs_setup(), "демо-портфель не загрузился с диска"
assert all(p.cls != "unclassified" for p in w.positions)
assert w.news_items and w.tbl_ideas.rowCount() > 0, "экран «Рынок» в демо пустой"
assert w.tbl_payouts.rowCount() >= 3 and w.tbl_cand_div.rowCount() > 0, "календарь в демо пустой"
st = w.tbl_payouts.item(0, 8)
assert st.text() in ("✔", "≈", "✓") and st.toolTip(), "статус выплаты — значком с подсказкой"
assert {w.tbl_classes.item(r, 4).text()[:1] for r in range(w.tbl_classes.rowCount())} & {"✓", "▼", "▲"}
assert w.chart_attr.rows and "переоценка" in w.lbl_attr.text(), "атрибуция в демо пустая"
assert w.tbl_sc.rowCount() > 0 and "Оценка" in w.lbl_sc.text(), "сценарии в демо пустые"
assert w.tbl_macro.rowCount() >= 3 and "заседание ЦБ" in w.lbl_payouts.text(), "календарь ЦБ в демо пустой"
assert len(list((d / "snapshots").glob("*.json"))) > 6, "нет истории стоимости"
assert A.get_token() is None
assert SEC.set("svc", "u", "секрет") == "session" and SEC.get("svc", "u") == "секрет"
try:
    paths.set_data_dir(Path.home() / "x"); raise SystemExit("set_data_dir в демо не должен работать")
except RuntimeError:
    pass
# Регресс: на окне MacBook 14″ графики сжимались ниже нужного и подписи налезали (бублики, бары, линии Стратегии).
# Теперь минимум высоты — по содержимому, стопки графиков прокручиваются: ни один видимый график не ниже минимума.
import charts as CH
from PySide6.QtWidgets import QWidget as _QW
f = app.font(); f.setPixelSize(13); app.setFont(f)                # шрифт как на macOS
w.resize(1300, 720); w.show()
kinds = (CH.LineChart, CH.PieChart, CH.BarChart, CH.RangeBarChart, CH.DivergingBarChart)
squeezed = []
for i in range(w.tabs.count()):
    w.tabs.setCurrentIndex(i)
    for j in range(w.pos_bottom.count() if w.tabs.tabText(i) == "Портфель" else 1):
        if w.tabs.tabText(i) == "Портфель":
            w.pos_bottom.setCurrentIndex(j)
        app.processEvents()
        for c in w.findChildren(_QW):
            if isinstance(c, kinds) and c.isVisible():
                need = c.need_height() if hasattr(c, "need_height") else 180
                if c.height() < need:
                    squeezed.append((w.tabs.tabText(i), c.title, c.height(), need))
assert not squeezed, f"графики сжаты ниже минимума: {squeezed}"
if len(sys.argv) > 1:
    w.resize(1400, 900); w.show(); app.processEvents(); w.grab().save(sys.argv[1])
w.close()
demo.cleanup()
assert not d.exists(), "временная папка не удалена"
print("ok")
"""


def test_demo_runs_isolated(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "APPDATA": str(tmp_path / "AppData"),
           "XDG_DATA_HOME": str(tmp_path / "xdg"), "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT)}
    shot = os.environ.get("DEMO_SCREENSHOT", "")
    r = subprocess.run([sys.executable, "-c", SCRIPT] + ([shot] if shot else []), cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    touched = [p for p in tmp_path.rglob("*") if "PortfolioCheck" in p.name]
    assert not touched, f"демо тронуло настоящий профиль: {touched[:5]}"
