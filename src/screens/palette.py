"""
screens/palette.py — палитра команд ⌘K (BACKLOG п. 19, аналог командной строки Bloomberg с <GO>).

Одна строка ввода вместо поиска по меню:
  • тикер или название  → карточка бумаги (портфель, кандидаты скринера, календарь);
  • «SBERP новости» (или CN) → «Рынок» с лентой по этой бумаге; «SBERP выплаты» (DVD) → карточка;
  • экран, действие или раздел настроек: «идеи», «обновить», «скрыть суммы», «настройки скринер», «лог»…
Enter — выполнить первую строку списка, ↑/↓ — выбрать другую, Esc — закрыть.
Подбор и ранжирование — чистые функции (entries, rank): проверяются тестом без окна.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

SETTINGS_TABS = ["Общие", "Цели", "Инструменты", "Правила", "Заморозки", "Лимиты", "Курсы", "Новости", "Скринер",
                 "Оповещения", "ИИ", "Доступ"]
NEWS_WORDS = {"cn", "новости", "news"}
CARD_WORDS = {"des", "dvd", "карточка", "выплаты", "дивиденды", "fa"}


@dataclass
class Entry:
    title: str                      # что видно в списке
    keys: str                       # по чему ищем (нижний регистр)
    run: Callable[[], None]
    kind: str = ""                  # бумага | экран | действие | настройки
    ticker: str = ""


def entries(win) -> list[Entry]:
    """Всё, куда можно перейти: бумаги, экраны, действия, разделы настроек."""
    out: list[Entry] = []
    seen = set()

    def sec(t, name, where, sector=""):
        if t and t not in seen:
            seen.add(t)
            out.append(Entry(f"{t} — {name} · {where}", f"{t} {name} {sector}".lower(), lambda t=t: win.open_card(t),
                             "бумага", t))

    import portfolio_check as pc
    for p in win.positions:
        sec(p.ticker, p.name, "в портфеле", pc.sector_label(p.sector) if p.sector else "")
    res = getattr(win, "_last_screen", None)
    for i in (getattr(res, "all_ideas", None) or getattr(res, "ideas", None) or []):
        sec(i.ticker, i.name, "кандидат", i.sector)
    for x in getattr(win, "payouts", []):
        sec(x.ticker, x.name, "календарь")
    for i in range(win.tabs.count()):
        name = win.tabs.tabText(i)
        out.append(Entry(f"Экран «{name}»", f"{name} экран".lower(), lambda i=i: win.tabs.setCurrentIndex(i), "экран"))
    acts = [(win.act_refresh, "обновить загрузить refresh"), (win.act_privacy, "скрыть показать суммы глазик privacy"),
            (win.act_prefs, "настройки preferences"), (win.act_about, "о программе версия about github")]
    for a, k in acts:
        if a.isVisible() and a.isEnabled():
            out.append(Entry(a.text().replace("&", ""), k, a.trigger, "действие"))
    for text, k, fn in (("Диагностика и лог", "лог диагностика log подключения", win.open_about),
                        ("ИИ-сводки по экранам", "ии сводки ai", lambda: win.run_exec(manual=True)),
                        ("Мастер настройки", "мастер wizard стратегия шаблон", lambda: win.run_wizard(first=False))):
        out.append(Entry(text, k, fn, "действие"))
    if not getattr(win, "guest", False):
        for i, tab in enumerate(SETTINGS_TABS):
            out.append(Entry(f"Настройки → {tab}", f"настройки {tab}".lower(),
                             lambda i=i: (win.open_settings(), win.settings_win.tabs.setCurrentIndex(i)), "настройки"))
    return out


def rank(items: list[Entry], query: str, win=None) -> list[Entry]:
    """Отбор по всем словам запроса; тикер в начале — выше всего. «ТИКЕР новости» — отдельная команда."""
    q = query.strip().lower()
    if not q:
        return items[:40]
    words = q.split()
    # «SBERP новости» / «SBERP DVD»: первое слово — тикер, остальное — что открыть
    if len(words) >= 2 and (words[-1] in NEWS_WORDS or words[-1] in CARD_WORDS):
        base = [e for e in items if e.ticker and e.ticker.lower() == words[0]]
        if base and win is not None:
            t = base[0].ticker
            if words[-1] in NEWS_WORDS:
                return [Entry(f"{t} — новости на экране «Рынок»", q, lambda: win.show_news_for(t), "бумага", t)]
            return [Entry(f"{t} — карточка (выплаты, фундаментал)", q, lambda: win.open_card(t), "бумага", t)]

    def score(e: Entry) -> int | None:
        if not all(w in e.keys for w in words):
            return None
        s = 0
        if e.ticker and e.ticker.lower() == q:
            s -= 100
        elif e.ticker and e.ticker.lower().startswith(q):
            s -= 50
        elif e.keys.startswith(q):
            s -= 20
        return s + {"бумага": 0, "экран": 5, "действие": 6, "настройки": 8}.get(e.kind, 9)
    scored = [(s, i, e) for i, e in enumerate(items) if (s := score(e)) is not None]
    return [e for _s, _i, e in sorted(scored, key=lambda x: (x[0], x[1]))][:40]


class CommandPalette(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Команды и поиск")
        self.setWindowFlag(Qt.FramelessWindowHint, False)
        self.resize(640, 420)
        self.edit = QLineEdit(); self.edit.setPlaceholderText("Тикер, «SBERP новости», экран, действие, «настройки скринер»…")
        self.list = QListWidget()
        lay = QVBoxLayout(self); lay.addWidget(self.edit); lay.addWidget(self.list, 1)
        self.items = entries(win)
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda _it: self._run_current())
        self.edit.installEventFilter(self)
        self._filter("")

    def _filter(self, text: str) -> None:
        self.shown = rank(self.items, text, self.win)
        self.list.clear()
        for e in self.shown:
            QListWidgetItem(f"{e.title}" + (f"   · {e.kind}" if e.kind and e.kind != "бумага" else ""), self.list)
        if self.shown:
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.edit and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Down, Qt.Key_Up):
            r = self.list.currentRow() + (1 if ev.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
            return True
        return super().eventFilter(obj, ev)

    def _run_current(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.shown):
            e = self.shown[r]
            self.accept()
            e.run()


class PaletteMixin:
    def open_palette(self) -> None:
        CommandPalette(self).exec()

    def show_news_for(self, ticker: str) -> None:
        """«Рынок» с лентой, отфильтрованной по бумаге (если по ней есть новости)."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Рынок":
                self.tabs.setCurrentIndex(i)
        idx = self.news_filter.findData(ticker)
        if idx >= 0:
            self.news_filter.setCurrentIndex(idx)
        else:
            self.set_status(f"По {ticker} новостей в ленте нет — показана вся лента")

