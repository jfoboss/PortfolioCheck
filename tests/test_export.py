"""Экспорт в CSV (export.py): формат под русский Excel, числа, таблица окна, история, «глазик»."""
import csv

import export as EX


def test_normalize_numbers_for_russian_excel():
    assert EX.normalize("1,234.56") == "1234,56"
    assert EX.normalize("+12.5%") == "12,5"
    assert EX.normalize("−3.1") == "-3,1"
    assert EX.normalize("31.79 ₽") == "31,79"
    assert EX.normalize("2026-10-03") == "2026-10-03"          # даты — как есть
    assert EX.normalize("Сбербанк-п") == "Сбербанк-п" and EX.normalize("—") == "—"


def test_write_csv_bom_and_semicolon(tmp_path):
    p = EX.write_csv(tmp_path / "x.csv", ["Бумага", "Сумма, ₽"], [["Сбербанк", "300,000"]])
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                     # BOM — Excel правильно читает кириллицу
    rows = list(csv.reader(p.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    assert rows == [["Бумага", "Сумма, ₽"], ["Сбербанк", "300000"]]


def test_window_table_and_history_export(window, demo_positions, tmp_path):
    window.on_loaded((demo_positions, None, None, []))
    window.set_privacy(False)
    p = window.export_table(window.tbl_pos, "позиции", str(tmp_path / "pos.csv"))
    rows = list(csv.reader(open(p, encoding="utf-8-sig"), delimiter=";"))
    assert rows[0][1] == "Тикер" and len(rows) == len(demo_positions) + 1
    assert any(r[1] == "SBERP" for r in rows[1:])
    window.set_privacy(True)
    h = window.export_history(str(tmp_path / "hist.csv"))
    hist = list(csv.reader(open(h, encoding="utf-8-sig"), delimiter=";"))
    assert hist[0][0] == "Дата" and all("•••" in r[1] or not r[1] for r in hist[1:])
