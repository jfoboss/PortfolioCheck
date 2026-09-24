"""Графики (charts.py): минимальная высота — по содержимому, чтобы подписи не налезали на узком окне."""
import charts as CH


def test_need_height_follows_content(qapp):
    """Регресс: на MacBook 14″ бублики «По компаниям» и бары сжимались до 14 px на строку — подписи налезали."""
    pie = CH.PieChart("П", max_slices=12)
    pie.set_data([(f"x{i}", 10 - i * 0.5) for i in range(12)])
    assert pie.minimumHeight() == pie.need_height() >= 12 * 16
    div = CH.DivergingBarChart("Д"); div.set_data([(f"к{i}", i - 3) for i in range(7)])
    assert div.minimumHeight() >= 7 * CH.ROW_MIN
    bars = CH.BarChart("Б"); bars.set_data([f"м{i}" for i in range(12)], [("а", [1] * 12, "#000"), ("б", [2] * 12, "#111")])
    stacked = CH.BarChart("Б", stacked=True); stacked.set_data(bars.categories, bars.series)
    assert stacked.need_height() < bars.need_height() and stacked.minimumHeight() >= 12 * CH.ROW_MIN


def test_stacked_and_line_paint_without_errors(qapp):
    """Отрисовка в узкое окно: легенда строкой, подписи оси X прореживаются — без исключений."""
    line = CH.LineChart("Стоимость", "₽"); line.resize(260, 180)
    line.set_data([f"{d:02d}.09" for d in range(1, 31)] * 12, [("факт", list(range(360)), "#534AB7", False)], ("цель", 300))
    line.grab()
    st = CH.BarChart("Поток", stacked=True); st.resize(300, 400)
    st.set_data(["сен", "окт"], [("объявлено", [1000, 0], "#0a0"), ("ожидается", [500, 2000], "#888")])
    st.grab()
