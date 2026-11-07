"""Экспорт для анализа крупной моделью (ai_export.py): разделы, скрытие сумм, глубина новостей, окно."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import ai_export as X
import news as N


def _data(demo_positions, demo_cfg):
    now = datetime.now(timezone.utc)
    sber = next(p for p in demo_positions if p.ticker == "SBERP")
    news = [N.NewsItem("Сбербанк отчитался о прибыли", "", "interfax.ru", now - timedelta(days=1), ["SBERP"], "Прибыль выросла"),
            N.NewsItem("Старая новость про Сбер", "", "rbc.ru", now - timedelta(days=20), ["SBERP"], ""),
            N.NewsItem("ЦБ сохранил ключевую ставку", "", "rbc.ru", now - timedelta(hours=3), [], "")]
    today = date.today()
    snaps = [((today - timedelta(days=30 * k)).isoformat(), 1_000_000 + 10_000 * (12 - k)) for k in range(12, -1, -1)]
    risk = NS(vol=16.8, max_drawdown=-13.6, effective_n=10.7, diversification=2.14, days=250,
              rows=[("SBERP", "Сбербанк", 10.1, 24.1, 10.3)], pairs=[("NVTK", "GAZP", 0.86)], missing=["TECH"])
    summ = NS(model="z-ai/glm:free", at="2026-11-07T10:00", overall="Нейтрально-негативный фон.", macro="Ставка высокая.",
              items=[{"ticker": "SBERP", "sentiment": "positive", "note": "рекордная прибыль"}])
    return X.Data(positions=demo_positions, cfg=demo_cfg, snapshots=snaps, changes={sber.figi: {"day": 0.4, "month": -2.0}},
                  risk=risk, plan_text="Параметры плана: капитал 1,234,567 ₽, пополнение 100,000 ₽/мес.",
                  news=news, ai_summary=summ, today=today)


def test_export_has_all_sections_and_fresh_news_only(demo_positions, demo_cfg):
    """Раньше для анализа крупной моделью копировали окно отчёта (доли и позиции) — без истории, риска и новостей."""
    md = X.build(_data(demo_positions, demo_cfg))
    for head in ("## Задача", "## Мои правила", "## Отчёт", "## История стоимости", "## Изменения бумаг за периоды",
                 "## Риск", "## План", "## Новости за 7 дн.", "## Сводка встроенной ИИ"):
        assert head in md, head
    assert "Сбербанк отчитался о прибыли" in md and "ЦБ сохранил ключевую ставку" in md
    assert "Старая новость" not in md                                   # глубже news_days — не берём
    assert "NVTK–GAZP +0.86" in md and "второе мнение" in md
    low = X.PROMPT.lower()
    assert "не давай указаний купить или продать" in low                 # правило 3: оценка, не рекомендация


def test_export_mask_hides_amounts_keeps_percent(demo_positions, demo_cfg):
    d = _data(demo_positions, demo_cfg)
    md = X.build(d, X.Options(mask=True))
    assert "1,234,567" not in md and "100,000" not in md and "Суммы скрыты" in md
    assert "| Месяц | Индекс | К началу |" in md                         # история — индексом, не рублями
    big = max(demo_positions, key=lambda p: float(p.value_rub))
    assert f"{float(big.value_rub):,.0f}" not in md
    md2 = X.build(d, X.Options(sections={"news"}, news_days=30))
    assert "Старая новость" in md2 and "## Мои правила" not in md2


def test_export_dialog_from_window(window, demo_positions, qapp):
    window.on_loaded((demo_positions, None, None, []))
    window.act_ai_export.trigger()
    d = window.ai_export_win
    assert "## Отчёт" in d.text and d.view.toPlainText() == d.text
    d.cb["report"].setChecked(False)
    assert "## Отчёт" not in d.text
    d.copy()
    assert qapp.clipboard().text() == d.text
    d.close()
