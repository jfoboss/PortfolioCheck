"""Журнал сетевой активности (activity.py): состояния задач, привязка к воркерам, повтор ИИ, кнопка и окно."""
import time

from PySide6.QtCore import QThread, Signal

import activity as A
import ai


class Fake(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        self.fn(self)


def _run(qapp, w):
    w.start(); assert w.wait(5000)
    for _ in range(5):
        qapp.processEvents()


def test_worker_lifecycle_done_failed_timeout(qapp):
    """Нужно видеть, что идёт обмен и чем он закончился: раньше были только маленькие ◐ на панелях."""
    act = A.reset()
    seen = []
    act.changed.connect(lambda: seen.append([(t.key, t.state) for t in act.tasks()]))
    ok = act.track(Fake(lambda w: (time.sleep(0.05), w.done.emit(5))), "news", "Новости", done_note=lambda r: f"{r} шт")
    assert act.tasks()[0].state == "pending"
    _run(qapp, ok)
    t = act.tasks()[0]
    assert (t.state, t.note) == ("done", "5 шт") and any(("news", "running") in s for s in seen)
    assert A.when_text(t).startswith("за ")
    bad = act.track(Fake(lambda w: w.failed.emit("HTTP 500")), "ai_exec", "ИИ-сводки")
    slow = act.track(Fake(lambda w: w.failed.emit("TimeoutError: timed out")), "portfolio", "Портфель")
    _run(qapp, bad); _run(qapp, slow)
    st = {t.key: t.state for t in act.tasks()}
    assert st == {"news": "done", "ai_exec": "failed", "portfolio": "timeout"}


def test_ai_retry_shown_with_countdown(qapp, monkeypatch):
    """«Провайдер перегружен — повтор через 10 с» — видно в журнале как ↻ с обратным отсчётом, затем снова идёт."""
    act = A.reset()
    monkeypatch.setattr(ai, "ON_RETRY", act.retry_current); monkeypatch.setattr(ai, "ON_RESUME", act.resume_current)
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    states = []
    calls = []

    def post(url, h, body, proxy="", timeout=120):
        calls.append(1)
        if len(calls) == 1:
            raise ai.RateLimited("перегружен", "upstream")
        return {"model": "m:free", "choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(ai, "_post", post)

    def sleep(s):
        t = act.tasks()[0]; states.append((t.state, round(t.due - time.monotonic())))
    monkeypatch.setattr(ai, "_sleep", sleep)
    w = act.track(Fake(lambda w: ai._chat("openrouter", "k", "m:free", "s", "u", "", "")), "ai_news", "ИИ")
    _run(qapp, w)
    assert states == [("retry", ai.UPSTREAM_RETRY_S)]
    assert act.tasks()[0].state == "done"


def test_pipeline_pending_and_skipped(qapp):
    act = A.reset()
    act.plan("history", "Свечи"); act.plan("news", "Новости", delay_s=30)
    assert A.when_text(act.tasks()[0]).startswith("через ")
    act.start("ai_news", "ИИ"); act.retry("ai_news", 10)
    assert "повтор через 1" in A.when_text(next(t for t in act.tasks() if t.key == "ai_news"))
    act.finish("ai_news")
    act.start("history")
    act.plan("history", "Свечи")                      # запланированный запуск не сбрасывает идущую задачу
    assert {t.key: t.state for t in act.tasks()}["history"] == "running"
    act.skip_pending(["history", "news"])
    assert {t.key: t.state for t in act.tasks()} == {"history": "running", "news": "skipped", "ai_news": "done"}


def test_button_and_window(qapp):
    act = A.reset()
    b = A.ActivityButton(act)
    assert b.text() == "Сеть"
    act.start("news", "Новости")
    assert "Сеть · 1" in b.text()
    act.finish("news", ok=False, note="HTTP 403")
    assert b.text() == "Сеть ✗"
    b.open_window(); w = b.win
    assert w.tbl.rowCount() == 1 and w.tbl.item(0, 1).text() == "✗ ошибка" and w.tbl.item(0, 4).text() == "HTTP 403"
    w.close()


def test_main_window_has_activity_button(window):
    assert window.btn_activity.act is window.act_log
    window.pipeline_begin()
    assert {t.state for t in window.act_log.tasks()} == {"pending"}


def test_durations_remembered_and_progress_estimated(qapp, tmp_path):
    """Нужен примерный прогресс (как в GoCD): время удачных запусков запоминается, по медиане рисуется полоска и
    «осталось ≈ …»; ответ из кэша (быстрее секунды) оценку не портит."""
    f = tmp_path / A.TIMES_FILE
    act = A.reset(); act.set_store(f)
    for dur in (100, 120, 110, 0.2):                      # 0.2 с — из кэша, в историю не идёт
        act.start("news", "Новости"); act._tasks["news"].started -= dur; act.finish("news")
    act.start("consensus:UID1", "Консенсус"); act._tasks["consensus:UID1"].started -= 4; act.finish("consensus:UID1")
    assert act.expected("news") == 110 and act.expected("consensus:UID2") == 4      # копится по виду задачи
    act2 = A.reset(); act2.set_store(f)                    # переживает перезапуск
    assert act2.expected("news") == 110
    act2.start("news", "Новости"); act2._tasks["news"].started -= 55
    frac, left = act2.estimate(act2._tasks["news"])
    assert abs(frac - 0.5) < 0.02 and abs(left - 55) < 2
    assert "из ~1 мин 50 с" in A.when_text(act2._tasks["news"], expected=110)
    act2.plan("history", "Свечи")                          # без истории — в общий процент не входит
    ofrac, oleft, unknown = act2.overall()
    assert abs(ofrac - 0.5) < 0.02 and unknown == 1
    w = A.ActivityWindow(act2); w.refresh()
    assert w.hist.rowCount() == 2 and w.hist.item(0, 2).text() == "1 мин 50 с"
    assert w.tbl.cellWidget(0, 2) is not None or w.tbl.cellWidget(1, 2) is not None     # полоска у идущей задачи
    act2._tasks["news"].started -= 200
    assert "дольше обычного" in A.when_text(act2._tasks["news"], expected=110)
