"""
activity.py — журнал сетевой активности: что сейчас загружается и чем закончилось.

Панели показывают свой маленький ◐, но общего «идёт обмен» не было: не видно, что грузятся новости или ждётся
ответ ИИ (бесплатная модель думает минутами), и нельзя отличить «запрос идёт» от «ничего не запускалось».
Здесь — один журнал задач с состояниями:
  pending  — в очереди (шаг конвейера обновления или запуск по таймеру «через N с»);
  running  — выполняется (с временем от старта и последним сообщением воркера);
  retry    — ждём повтора («провайдер перегружен — повтор через 10 с», обратный отсчёт);
  done / failed / timeout — чем закончилось (последние задачи хранятся для окна).

Воркер подключается одной строкой `ACT.track(worker, key, title)`: состояние берётся из сигналов потока
`started` / `finished` и, если есть, `failed` / `progress` / `skipped`. Код без Qt (ai.py) сообщает о повторе через
`retry_current()` — задача находится по текущему потоку. Сигнал `changed` можно испускать из рабочего потока:
получатели живут в UI-потоке, Qt доставит его очередью.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

KEEP_FINISHED = 30          # сколько завершённых задач помнить для окна
ACTIVE = ("pending", "running", "retry")
STATES = {  # состояние → (значок, подпись)
    "pending": ("⏳", "ожидает"), "running": ("◐", "выполняется"), "retry": ("↻", "повтор"),
    "done": ("✓", "готово"), "failed": ("✗", "ошибка"), "timeout": ("⏱", "таймаут"), "skipped": ("–", "пропущено"),
}
_TIMEOUT_WORDS = ("timed out", "timeout", "таймаут", "не пришёл целиком", "нет ответа")


@dataclass
class Task:
    key: str
    title: str
    state: str = "pending"
    note: str = ""
    started: float | None = None        # time.monotonic()
    finished: float | None = None
    due: float | None = None            # pending «через N с» / retry — когда
    seq: int = 0                        # порядок появления — для сортировки
    thread: int | None = field(default=None, repr=False)    # threading.get_ident() рабочего потока


def classify(error: str) -> str:
    """Текст ошибки → failed / timeout."""
    low = (error or "").lower()
    return "timeout" if any(w in low for w in _TIMEOUT_WORDS) else "failed"


class Activity(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._seq = 0

    # ---- изменения состояния (любой поток) ----------------------------------

    def _put(self, key: str, title: str | None, **kw) -> None:
        with self._lock:
            t = self._tasks.get(key)
            if t is None or (t.state not in ACTIVE and kw.get("state") in ACTIVE):
                self._seq += 1                                  # новый запуск той же задачи — наверх списка
                t = Task(key, title or key, seq=self._seq)
                self._tasks[key] = t
            if title:
                t.title = title
            for k, v in kw.items():
                setattr(t, k, v)
            self._trim()
        self.changed.emit()

    def plan(self, key: str, title: str, delay_s: float = 0, note: str = "") -> None:
        """Задача в очереди; delay_s — запустится по таймеру через столько секунд. Уже идущую не трогает."""
        with self._lock:
            cur = self._tasks.get(key)
            if cur is not None and cur.state in ("running", "retry"):
                return
        self._put(key, title, state="pending", note=note, due=time.monotonic() + delay_s if delay_s else None,
                  started=None, finished=None)

    def start(self, key: str, title: str | None = None, note: str = "", thread=None) -> None:
        self._put(key, title, state="running", note=note, started=time.monotonic(), finished=None, due=None,
                  thread=thread)

    def note(self, key: str, text: str) -> None:
        with self._lock:
            if key not in self._tasks:
                return
        self._put(key, None, note=text)

    def retry(self, key: str, wait_s: float, note: str = "") -> None:
        self._put(key, None, state="retry", due=time.monotonic() + wait_s, note=note)

    def resume(self, key: str) -> None:
        """После паузы повтора — снова «выполняется» (время от первого старта сохраняется)."""
        self._put(key, None, state="running", due=None)

    def finish(self, key: str, ok: bool = True, note: str = "", state: str | None = None) -> None:
        with self._lock:
            if key not in self._tasks:
                return
        self._put(key, None, state=state or ("done" if ok else classify(note)), note=note,
                  finished=time.monotonic(), due=None, thread=None)

    def skip_pending(self, keys) -> None:
        """Шаги конвейера, которые так и не запустились (например, без токена), — не висят «ожидает» вечно."""
        with self._lock:
            hit = [k for k in keys if k in self._tasks and self._tasks[k].state == "pending"]
        for k in hit:
            self.finish(k, state="skipped", note="не понадобилось")

    # ---- для кода без Qt: сообщить о повторе из рабочего потока --------------

    def _current_key(self) -> str | None:
        th = threading.get_ident()
        with self._lock:
            return next((t.key for t in self._tasks.values() if t.thread == th and t.state in ACTIVE), None)

    def retry_current(self, wait_s: float, note: str = "") -> None:
        key = self._current_key()
        if key:
            self.retry(key, wait_s, note)

    def resume_current(self) -> None:
        key = self._current_key()
        if key:
            self.resume(key)

    # ---- подключение воркера ---------------------------------------------------

    def track(self, worker: QThread, key: str, title: str, done_note=None) -> QThread:
        """Состояние задачи — из сигналов воркера. done_note(result) → подпись к «готово» (необязательно)."""
        box = {"err": "", "note": "", "skipped": False}
        # started с прямым соединением выполняется уже в рабочем потоке — запоминаем его id для retry_current
        worker.started.connect(lambda: self.start(key, title, thread=threading.get_ident()), Qt.DirectConnection)
        if hasattr(worker, "failed"):
            worker.failed.connect(lambda m: box.__setitem__("err", str(m)))
        if hasattr(worker, "progress"):
            worker.progress.connect(lambda m: self.note(key, str(m)))
        if hasattr(worker, "skipped"):
            worker.skipped.connect(lambda: box.__setitem__("skipped", True))
        if done_note is not None and hasattr(worker, "done"):
            worker.done.connect(lambda r: box.__setitem__("note", done_note(r) or ""))

        def fin():
            if box["err"]:
                self.finish(key, ok=False, note=box["err"])
            elif box["skipped"]:
                self.finish(key, state="skipped", note="в кэше нет — запрос уйдёт после загрузки ленты")
            else:
                self.finish(key, ok=True, note=box["note"])
        worker.finished.connect(fin)
        self.plan(key, title, note="запускается")
        return worker

    # ---- чтение ----------------------------------------------------------------

    def tasks(self) -> list[Task]:
        """Активные — сверху (свежие первыми), затем завершённые — от последних."""
        with self._lock:
            ts = list(self._tasks.values())
        act = sorted((t for t in ts if t.state in ACTIVE), key=lambda t: -t.seq)
        rest = sorted((t for t in ts if t.state not in ACTIVE), key=lambda t: -(t.finished or 0))
        return act + rest

    def active(self) -> list[Task]:
        return [t for t in self.tasks() if t.state in ("running", "retry")]

    def _trim(self) -> None:
        done = sorted((t for t in self._tasks.values() if t.state not in ACTIVE), key=lambda t: t.finished or 0)
        for t in done[:max(len(done) - KEEP_FINISHED, 0)]:
            self._tasks.pop(t.key, None)


def when_text(t: Task, now: float | None = None) -> str:
    """Колонка «Время»: сколько идёт / через сколько / сколько шло."""
    now = time.monotonic() if now is None else now
    if t.state == "retry" and t.due:
        spent = f"{now - t.started:.0f} с · " if t.started else ""
        return f"{spent}повтор через {max(t.due - now, 0):.0f} с"
    if t.state == "pending" and t.due:
        return f"через {max(t.due - now, 0):.0f} с"
    if t.state == "running" and t.started:
        return f"{now - t.started:.0f} с"
    if t.started and t.finished:
        return f"за {t.finished - t.started:.0f} с"
    return ""


def state_text(t: Task) -> str:
    icon, label = STATES.get(t.state, ("", t.state))
    return f"{icon} {label}"


_ACT: Activity | None = None


def get() -> Activity:
    global _ACT
    if _ACT is None:
        _ACT = Activity()
    return _ACT


def reset() -> Activity:
    """Для тестов: чистый журнал."""
    global _ACT
    _ACT = Activity()
    return _ACT


# ---------------------------------------------------------------------------
# Кнопка в панели инструментов и окно со списком
# ---------------------------------------------------------------------------

class ActivityButton(QToolButton):
    """«Сеть»: крутится, пока идёт обмен, с числом задач; ✗ — если последняя задача упала. Клик — окно со списком."""
    FRAMES = "◐◓◑◒"

    def __init__(self, act: Activity, parent=None):
        super().__init__(parent)
        self.act = act; self._i = 0; self.win: ActivityWindow | None = None
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setToolTip("Сетевая активность: что загружается сейчас и чем закончилось — нажми, чтобы открыть список")
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self.clicked.connect(self.open_window)
        act.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        busy = self.act.active()
        if busy:
            if not self._timer.isActive():
                self._timer.start(150)
            retry = any(t.state == "retry" for t in busy)
            self.setText(f"{self.FRAMES[self._i]} Сеть · {len(busy)}" + (" ↻" if retry else ""))
        else:
            self._timer.stop()
            last = next((t for t in self.act.tasks() if t.state not in ACTIVE), None)
            mark = {"failed": " ✗", "timeout": " ⏱"}.get(last.state, "") if last else ""
            self.setText(f"Сеть{mark}")

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self.FRAMES); self.refresh()

    def open_window(self) -> None:
        if self.win is None:
            self.win = ActivityWindow(self.act, self.window())
        self.win.show(); self.win.raise_(); self.win.activateWindow()


class ActivityWindow(QWidget):
    """Отдельное маленькое окно: задачи, состояние, время, подробности (текст ошибки, «повтор через…»)."""
    COLS = ["Задача", "Состояние", "Время", "Подробности"]

    def __init__(self, act: Activity, parent=None):
        super().__init__(parent, Qt.Tool)
        self.act = act
        self.setWindowTitle("Сетевая активность")
        self.resize(860, 340)
        lay = QVBoxLayout(self)
        self.lbl = QLabel(); self.lbl.setStyleSheet("color: gray")
        self.tbl = QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        h = self.tbl.horizontalHeader()
        for c in range(3):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.lbl); lay.addWidget(self.tbl)
        self._timer = QTimer(self); self._timer.timeout.connect(self.refresh)   # секунды и обратный отсчёт
        act.changed.connect(self.refresh)
        self.refresh()

    def showEvent(self, e) -> None:
        self._timer.start(1000); self.refresh(); super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop(); super().hideEvent(e)

    def refresh(self) -> None:
        ts = self.act.tasks(); now = time.monotonic()
        busy = sum(1 for t in ts if t.state in ("running", "retry"))
        wait = sum(1 for t in ts if t.state == "pending")
        self.lbl.setText(f"Выполняется: {busy} · в очереди: {wait} · ниже — последние завершённые"
                         if ts else "Пока ничего не запускалось.")
        self.tbl.setRowCount(len(ts))
        for r, t in enumerate(ts):
            for c, val in enumerate((t.title, state_text(t), when_text(t, now), t.note)):
                it = QTableWidgetItem(val); it.setToolTip(val)
                self.tbl.setItem(r, c, it)
