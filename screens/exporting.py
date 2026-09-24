"""
screens/exporting.py — экспорт в CSV из окна (BACKLOG п. 24). Миксин MainWindow. Модель — export.py.
"""
from __future__ import annotations

from PySide6.QtCore import Qt


class ExportMixin:
    """Экспорт в CSV (BACKLOG п. 24): контекстное меню таблиц и «Файл → Экспорт истории стоимости». Модель — export.py."""

    EXPORT_TABLES = {"tbl_classes": "доли классов", "tbl_pos": "позиции", "tbl_blocked": "заблокированные",
                     "tbl_payouts": "календарь выплат", "tbl_cand_div": "отсечки кандидатов", "tbl_ideas": "идеи"}

    def _install_export_menus(self) -> None:
        from PySide6.QtWidgets import QMenu
        for attr, title in self.EXPORT_TABLES.items():
            t = getattr(self, attr, None)
            if t is None or attr == "tbl_ideas":            # у «Идей» своё меню — пункт добавлен туда
                continue
            t.setContextMenuPolicy(Qt.CustomContextMenu)

            def menu(pos, t=t, title=title):
                m = QMenu(self)
                m.addAction("Экспорт таблицы в CSV…", lambda: self.export_table(t, title))
                m.exec(t.viewport().mapToGlobal(pos))
            t.customContextMenuRequested.connect(menu)

    def _ask_csv_path(self, title: str):
        from datetime import date
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        default = str(Path.home() / f"PortfolioCheck — {title} {date.today():%Y-%m-%d}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт в CSV", default, "CSV (*.csv)")
        return path

    def export_table(self, table, title: str, path: str | None = None) -> str:
        import export as EX
        path = path or self._ask_csv_path(title)
        if not path:
            return ""
        headers, rows = EX.table_rows(table)
        EX.write_csv(path, headers, rows)
        self._export_done(path)
        return path

    def export_history(self, path: str | None = None) -> str:
        import export as EX
        import history as H
        path = path or self._ask_csv_path("история стоимости")
        if not path:
            return ""
        headers, rows = EX.history_rows(H.snapshot_history(self.snap_dir()), getattr(self, "backfilled", []) or [])
        if self.privacy:
            rows = [[r[0], "•••" if r[1] else "", "•••" if r[2] else ""] for r in rows]
        EX.write_csv(path, headers, rows)
        self._export_done(path)
        return path

    def _export_done(self, path: str) -> None:
        note = " Суммы скрыты «глазиком» и выгружены как «•••» — для выгрузки сумм выключи режим." if self.privacy else ""
        self.set_status(f"Экспорт: {path}.{note}")
