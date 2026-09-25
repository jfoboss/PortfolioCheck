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
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QProgressBar, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

import jsoncache as JC

KEEP_FINISHED = 30          # сколько завершённых задач помнить для окна
ACTIVE = ("pending", "running", "retry")
STATES = {  # состояние → (значок, подпись)
    "pending": ("⏳", "ожидает"), "running": ("◐", "выполняется"), "retry": ("↻", "повтор"),
    "done": ("✓", "готово"), "failed": ("✗", "ошибка"), "timeout": ("⏱", "таймаут"), "skipped": ("–", "пропущено"),
}
_TIMEOUT_WORDS = ("timed out", "timeout", "таймаут", "не пришёл целиком", "нет ответа")
# Время прошлых удачных запусков — для примерного прогресса (как в GoCD: длительность запоминается, усредняется и
# рисуется полоской). Медиана последних HISTORY_N; быстрее MIN_RECORD_S — ответ из кэша, в оценку не идёт.
HISTORY_N = 10
MIN_RECORD_S = 1.0
TIMES_FILE = "activity_times.json"


def stat_key(key: str) -> str:
    """«consensus:<uid>» → «consensus»: время копится по виду задачи, а не по бумаге."""
    return key.split(":", 1)[0]


def fmt_s(sec: float) -> str:
    sec = max(int(round(sec)), 0)
    return f"{sec // 60} мин {sec % 60:02d} с" if sec >= 60 else f"{sec} с"


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
        self._times: dict[str, dict] = {}        # stat_key → {"title", "runs": [секунды]}
        self._store: Path | None = None

    # ---- время прошлых запусков ------------------------------------------------

    def set_store(self, path: Path | None) -> None:
        """Файл истории времени (в кэше профиля); читается сразу."""
        self._store = path
        data = JC.read(path, {}) if path else {}
        with self._lock:
            self._times = {k: v for k, v in (data or {}).items()
                           if isinstance(v, dict) and isinstance(v.get("runs"), list)}

    def _record(self, t: "Task") -> None:
        dur = (t.finished or 0) - (t.started or 0)
        if not t.started or dur < MIN_RECORD_S:
            return
        with self._lock:
            e = self._times.setdefault(stat_key(t.key), {"title": t.title, "runs": []})
            e["title"] = t.title
            e["runs"] = (e["runs"] + [round(dur, 1)])[-HISTORY_N:]
            snap = {k: dict(v) for k, v in self._times.items()}
        if self._store:
            try:
                JC.write(self._store, snap)
            except OSError:
                pass

    def expected(self, key: str) -> float | None:
        """Сколько обычно длится (медиана прошлых удачных запусков); None — истории нет."""
        with self._lock:
            runs = (self._times.get(stat_key(key)) or {}).get("runs") or []
        return statistics.median(runs) if runs else None

    def history(self) -> list[dict]:
        with self._lock:
            items = [(k, dict(v)) for k, v in self._times.items()]
        out = []
        for k, v in items:
            runs = v.get("runs") or []
            if runs:
                out.append({"key": k, "title": v.get("title") or k, "count": len(runs), "median": statistics.median(runs),
                            "last": runs[-1], "min": min(runs), "max": max(runs)})
        return sorted(out, key=lambda x: -x["median"])

    def estimate(self, t: "Task", now: float | None = None) -> tuple[float | None, float | None]:
        """(доля 0…1, осталось секунд) по прошлым запусках; None — оценки нет (нет истории / уже дольше обычного)."""
        now = time.monotonic() if now is None else now
        exp = self.expected(t.key)
        if exp is None or t.state not in ACTIVE:
            return None, None
        if t.state == "pending":
            return 0.0, exp + max((t.due or now) - now, 0)
        spent = now - (t.started or now)
        wait = max((t.due or now) - now, 0) if t.state == "retry" else 0
        if spent >= exp:
            return 0.99, None
        return min(spent / exp, 0.99), exp - spent + wait

    def overall(self, now: float | None = None) -> tuple[float | None, float | None, int]:
        """По всем активным задачам: (доля, осталось секунд, сколько задач без истории)."""
        now = time.monotonic() if now is None else now
        done_s = total_s = left = 0.0
        unknown = 0
        for t in self.tasks():
            if t.state not in ACTIVE:
                continue
            exp = self.expected(t.key)
            if exp is None:
                unknown += 1; continue
            frac, rem = self.estimate(t, now)
            total_s += exp; done_s += exp * (frac or 0); left += rem or 0
        if not total_s:
            return None, None, unknown
        return done_s / total_s, left, unknown

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
        final = state or ("done" if ok else classify(note))
        self._put(key, None, state=final, note=note, finished=time.monotonic(), due=None, thread=None)
        if final == "done":
            with self._lock:
                t = self._tasks.get(key)
            if t is not None:
                self._record(t)

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


def when_text(t: Task, now: float | None = None, expected: float | None = None) -> str:
    """Колонка «Время»: сколько идёт / через сколько / сколько шло; expected — обычное время по прошлым запускам."""
    now = time.monotonic() if now is None else now
    usual = f"обычно ~{fmt_s(expected)}" if expected else ""
    if t.state == "retry" and t.due:
        spent = f"{fmt_s(now - t.started)} · " if t.started else ""
        return f"{spent}повтор через {max(t.due - now, 0):.0f} с"
    if t.state == "pending":
        wait = f"через {max(t.due - now, 0):.0f} с" if t.due else ""
        return " · ".join(x for x in (wait, usual) if x)
    if t.state == "running" and t.started:
        spent = now - t.started
        if expected and spent > expected * 1.2:
            return f"{fmt_s(spent)} — дольше обычного (~{fmt_s(expected)})"
        return f"{fmt_s(spent)} из ~{fmt_s(expected)}" if expected else fmt_s(spent)
    if t.started and t.finished:
        took = f"за {fmt_s(t.finished - t.started)}"
        return f"{took} ({usual})" if usual and t.state == "done" else took
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
    """«Сеть»: крутится, пока идёт обмен, с числом задач и примерным процентом по прошлым запускам; ✗ — последняя
    задача упала. Клик — окно со списком."""
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
            frac, left, _ = self.act.overall()
            pct = f" · {int(frac * 100)}%" if frac is not None else ""
            self.setText(f"{self.FRAMES[self._i]} Сеть · {len(busy)}{pct}" + (" ↻" if retry else ""))
            if left:
                self.setToolTip(f"Осталось ≈ {fmt_s(left)} (по прошлым запускам). Нажми — подробности.")
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


def _table(cols: list[str], stretch: int) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    h = t.horizontalHeader()
    for c in range(len(cols)):
        h.setSectionResizeMode(c, QHeaderView.Stretch if c == stretch else QHeaderView.ResizeToContents)
    return t


class ActivityWindow(QWidget):
    """Отдельное окно: «Сейчас» — задачи, состояние, примерный прогресс, время, подробности; «История» — сколько
    обычно длится каждая задача (по последним удачным запускам)."""
    COLS = ["Задача", "Состояние", "Прогресс", "Время", "Подробности"]
    HCOLS = ["Задача", "Запусков", "Обычно (медиана)", "Последний", "Разброс"]

    def __init__(self, act: Activity, parent=None):
        super().__init__(parent, Qt.Tool)
        self.act = act
        self.setWindowTitle("Сетевая активность")
        self.resize(960, 380)
        lay = QVBoxLayout(self)
        self.lbl = QLabel(); self.lbl.setStyleSheet("color: gray")
        self.tabs = QTabWidget()
        self.tbl = _table(self.COLS, 4)
        self.hist = _table(self.HCOLS, 0)
        self.tabs.addTab(self.tbl, "Сейчас"); self.tabs.addTab(self.hist, "История (время)")
        lay.addWidget(self.lbl); lay.addWidget(self.tabs)
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
        frac, left, unknown = self.act.overall(now)
        eta = (f" · осталось ≈ {fmt_s(left)} по прошлым запускам" if left else "") + \
              (f" (без оценки: {unknown} — ещё нет истории)" if unknown and (busy or wait) else "")
        self.lbl.setText(f"Выполняется: {busy} · в очереди: {wait}{eta}" if ts else "Пока ничего не запускалось.")
        self.tbl.setRowCount(len(ts))
        for r, t in enumerate(ts):
            exp = self.act.expected(t.key)
            for c, val in enumerate((t.title, state_text(t), "", when_text(t, now, exp), t.note)):
                it = QTableWidgetItem(val); it.setToolTip(val)
                self.tbl.setItem(r, c, it)
            f, _ = self.act.estimate(t, now)
            if f is not None:
                bar = self.tbl.cellWidget(r, 2)
                if not isinstance(bar, QProgressBar):
                    bar = QProgressBar(); bar.setRange(0, 100); bar.setMaximumHeight(16)
                    self.tbl.setCellWidget(r, 2, bar)
                bar.setValue(int(f * 100)); bar.setFormat("%p%")
            else:
                self.tbl.removeCellWidget(r, 2)
                self.tbl.item(r, 2).setText({"done": "100%"}.get(t.state, "—" if t.state in ACTIVE else ""))
        hs = self.act.history()
        self.hist.setRowCount(len(hs))
        for r, h in enumerate(hs):
            for c, val in enumerate((h["title"], str(h["count"]), fmt_s(h["median"]), fmt_s(h["last"]),
                                     f"{fmt_s(h['min'])} – {fmt_s(h['max'])}")):
                self.hist.setItem(r, c, QTableWidgetItem(val))
