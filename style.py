"""
style.py — цвета и значки интерфейса в одном месте (без Qt: используется и в HTML сводок ИИ, и в таблицах).

Цвета — системная палитра macOS (читаются и в светлой, и в тёмной теме); значки — обычные символы Unicode,
не эмодзи: одинаково видны на macOS, Windows и Linux. Полупрозрачные заливки строк — ui_common.TINT_*.
"""
from __future__ import annotations

GOOD = "#34C759"          # зелёный: в коридоре, рост, выполнено
BAD = "#FF453A"           # красный: перевес, падение, ошибка
WARN = "#FF9F0A"          # оранжевый: недовес, внимание
MUTED = "#8E8E93"         # серый: нейтрально, «ожидается»

KPI_CSS = "font-size: 18px; font-weight: 600"

SENTIMENT = {             # тональность новостей по бумаге (ИИ): значок, цвет, подпись
    "positive": ("▲", GOOD, "Позитив для бумаги"),
    "neutral": ("■", MUTED, "Нейтрально"),
    "negative": ("▼", BAD, "Негатив для бумаги"),
}


def sign_color(v: float, zero: str = "gray") -> str:
    """Цвет числа по знаку: рост — зелёный, падение — красный, ноль — zero."""
    return GOOD if v > 0 else (BAD if v < 0 else zero)


def arrow(v: float) -> str:
    """▲ / ▼ / ■ по знаку."""
    return "▲" if v > 0 else ("▼" if v < 0 else "■")


def ok_mark(ok: bool) -> tuple[str, str]:
    """(значок, цвет) результата проверки: ✓ зелёный или ✗ красный."""
    return ("✓", GOOD) if ok else ("✗", BAD)


def sentiment(s: str | None) -> tuple[str, str, str]:
    """(значок, цвет, подпись) тональности; неизвестная — как нейтральная."""
    return SENTIMENT.get(s or "", SENTIMENT["neutral"])


def sentiment_label(s: str | None) -> str:
    """«▲ позитив» / «■ нейтрально» / «▼ негатив» — для текста."""
    return {"positive": "▲ позитив", "negative": "▼ негатив"}.get(s or "", "■ нейтрально")


def span(text: str, color: str, bold: bool = False) -> str:
    """Цветной фрагмент для HTML-подписей (QLabel с RichText, QTextBrowser)."""
    b = ";font-weight:600" if bold else ""
    return f"<span style='color:{color}{b}'>{text}</span>"


MASK = "•••"              # режим «глазика»: сумма скрыта (проценты и цены видны)


def money_fn(mask: bool, signed: bool = False):
    """Формат суммы для таблиц: «1,234» / «+1,234» или маска — одна функция вместо копий lambda на каждом экране."""
    if mask:
        return lambda v: MASK
    return (lambda v: f"{v:+,.0f}") if signed else (lambda v: f"{v:,.0f}")
