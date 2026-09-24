"""
screens/alerts_ui.py — проверка оповещений в окне (BACKLOG п. 21): после обновлений портфеля, ленты и скринера.
Только пока программа открыта (решение пользователя). Миксин MainWindow. Модель — alerts.py.
"""
from __future__ import annotations

from PySide6.QtWidgets import QSystemTrayIcon

import alerts as AL
import paths
import portfolio_check as pc
from diagnostics import log
from ui_common import APP_NAME


class AlertsMixin:
    def _class_status(self) -> dict:
        total, by_cls = pc.class_shares(self.positions)
        out = {}
        for cls, rng in (self.cfg.get("targets") or {}).items():
            share = float(by_cls.get(cls, 0) / total * 100) if total else 0.0
            out[pc.class_label(cls)] = (share, float(rng["min"]), float(rng["max"]))
        return out

    def check_alerts(self) -> list:
        """Выполненные условия → новые из них → системное уведомление и строка в журнале «Оповещения»."""
        rules = self.cfg.get("alerts") or []
        if not rules or not self.positions or paths.DEMO_DIR is not None:    # демо — вымышленные данные, не будим
            return []
        res = getattr(self, "_last_screen", None)
        hits = AL.evaluate(rules, self.positions, getattr(res, "all_ideas", None) or getattr(res, "ideas", None) or [],
                           getattr(self, "payouts", []), self._class_status(), getattr(self, "news_items", []))
        fresh = AL.new_hits(hits, paths.data_dir() / "cache")
        self._alert_log = (getattr(self, "_alert_log", []) + [h.text for h in fresh])[-50:]
        if fresh:
            log.info("оповещения: %s", "; ".join(h.text for h in fresh))
            body = "\n".join(h.text for h in fresh[:4]) + (f"\n…и ещё {len(fresh) - 4}" if len(fresh) > 4 else "")
            if getattr(self, "tray", None) is not None and self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(f"{APP_NAME}: оповещения ({len(fresh)})", body, QSystemTrayIcon.Information, 10000)
            self.set_status("Оповещение: " + fresh[0].text + (f" (+{len(fresh) - 1})" if len(fresh) > 1 else ""))
            if hasattr(self, "txt_signals") and self.positions:
                self.render()                          # показать в «Сигналах» (render проверку не запускает — цикла нет)
        return fresh
