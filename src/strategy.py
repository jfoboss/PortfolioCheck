"""
strategy.py — шаблоны стратегий, автоклассификация бумаг, импорт/экспорт стратегии (YAML).

Принципы, на которых построено приложение (одинаковы для всех шаблонов, меняются только цифры):
  1. Портфель — это набор КЛАССОВ с целевыми коридорами, а не список любимых бумаг. Решения — по отклонению от коридора.
  2. Защитное ядро: доля золота/металлов держится всегда, независимо от «настроения рынка».
  3. Денежный поток: основная часть — дивидендное ядро и доходная недвижимость (ЗПИФ), а не ставка на рост.
  4. Спекуляции — в отдельной маленькой ячейке с жёстким потолком, чтобы ошибка не била по плану.
  5. Концентрация ограничена: ни один эмитент не превышает заданной доли.
  6. Добор — по правилу (просадка от средней), а не по эмоции; проблемные бумаги можно «заморозить», чтобы правило их не предлагало.
  7. Бумаги, от которых отказался осознанно, — в чёрный список скринера с причиной, чтобы не возвращаться к ним по инерции.
  8. Программа считает и подсвечивает; решения принимает человек.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PRINCIPLES = [
    "Портфель — это классы с целевыми коридорами; решения принимаются по отклонению от коридора.",
    "Защитное ядро (золото/металлы) держится всегда, независимо от настроения рынка.",
    "Основа денежного потока — дивидендное ядро и доходная недвижимость, а не ставка на рост.",
    "Спекуляции — в отдельной маленькой ячейке с жёстким потолком.",
    "Концентрация ограничена: ни один эмитент не больше заданной доли портфеля.",
    "Добор — по правилу (просадка от средней), а не по эмоции; проблемные бумаги можно «заморозить».",
    "От чего отказался осознанно — в чёрный список скринера с причиной.",
    "Программа считает и подсвечивает; решения принимает человек.",
]

CLASSES = ["gold", "real_estate_wh", "real_estate_other", "equity_core", "equity_spec", "ballast", "foreign", "cash"]
TARGET_KEYS = ["gold", "real_estate", "real_estate_wh", "real_estate_other", "equity_core", "equity_spec", "ballast", "foreign"]

# Шаблоны: одинаковые принципы, разная агрессивность коридоров и правил.
PRESETS = {
    "dividend_defensive": {
        "title": "Дивидендная с защитным ядром",
        "description": "Денежный поток из дивидендного ядра и ЗПИФ, крупная доля золота как страховка, "
                       "маленькая спекулятивная ячейка. Для тех, кто строит пассивный доход и не хочет резких просадок.",
        "targets": {"gold": (20, 27), "real_estate": (10, 15), "equity_core": (40, 55), "equity_spec": (0, 3)},
        "rules": {"drawdown_alert_pct": 10, "single_name_max_pct": 11, "tail_threshold_rub": 30000},
    },
    "conservative": {
        "title": "Консервативная",
        "description": "Больше защиты и недвижимости, меньше акций, строже лимит на эмитента. "
                       "Для короткого горизонта или низкой терпимости к просадкам.",
        "targets": {"gold": (25, 30), "real_estate": (15, 20), "equity_core": (30, 45), "equity_spec": (0, 2)},
        "rules": {"drawdown_alert_pct": 8, "single_name_max_pct": 8, "tail_threshold_rub": 20000},
    },
    "growth": {
        "title": "Активная (больше акций)",
        "description": "Основной вес — дивидендное ядро, защита меньше, спекулятивная ячейка шире. "
                       "Для длинного горизонта и готовности пережидать просадки.",
        "targets": {"gold": (10, 15), "real_estate": (0, 10), "equity_core": (50, 70), "equity_spec": (0, 8)},
        "rules": {"drawdown_alert_pct": 12, "single_name_max_pct": 15, "tail_threshold_rub": 30000},
    },
    "blank": {
        "title": "Задать всё самому",
        "description": "Пустые коридоры — заполнишь в настройках или импортируешь готовую стратегию из файла "
                       "(например, собранную с помощью ChatGPT по промпту из docs/CHATGPT_PROMPT.md).",
        "targets": {},
        "rules": {"drawdown_alert_pct": 10, "single_name_max_pct": 11, "tail_threshold_rub": 30000},
    },
}


def apply_preset(cfg: dict, preset_id: str) -> dict:
    p = PRESETS[preset_id]
    cfg = dict(cfg)
    cfg["targets"] = {k: {"min": float(lo), "max": float(hi)} for k, (lo, hi) in p["targets"].items()}
    rules = dict(cfg.get("rules") or {})
    rules.update(p["rules"])
    rules.setdefault("buy_candidates_classes", ["equity_core", "real_estate_wh", "real_estate_other"])
    rules.setdefault("frozen", {}); rules.setdefault("position_caps", {})
    cfg["rules"] = rules
    cfg["strategy_preset"] = preset_id
    return cfg


# ---------------------------------------------------------------------------
# Автоклассификация: чтобы новый пользователь не начинал с сорока «без класса»
# ---------------------------------------------------------------------------

METAL_WORDS = ("золот", "серебр", "платин", "паллад", "gold", "silver")


def auto_class(p) -> str | None:
    """Разумный класс по типу инструмента и названию. None — оставить «без класса» (облигации, неясные фонды)."""
    name = (p.name or "").lower()
    t = (p.ticker or "").upper()
    typ = (p.instrument_type or "").lower()
    cur = (p.currency or "").upper()
    if typ == "currency":
        if any(w in name for w in METAL_WORDS) or t.startswith(("GLD", "SLV", "PLT", "PLD")):
            return "gold"
        return "cash"
    if any(w in name for w in METAL_WORDS) and typ in ("etf", "share"):
        return "gold"
    if "зпиф" in name or "недвиж" in name or "rental" in name:
        return "real_estate_other"
    if cur and cur not in ("RUB", "RUR"):
        return "foreign"
    if typ == "share":
        return "equity_core"
    return None


def auto_classify(positions, cfg: dict) -> list[str]:
    """Проставить класс бумагам без класса. Возвращает тикеры, классифицированные автоматически."""
    if not cfg.get("auto_classify", True):
        return []
    instruments = cfg.setdefault("instruments", {})
    done = []
    for p in positions:
        if p.ticker in instruments or (p.isin and p.isin in instruments):
            continue
        c = auto_class(p)
        if c:
            instruments[p.ticker] = c
            done.append(p.ticker)
    if done:
        cfg.setdefault("auto_classified", [])
        cfg["auto_classified"] = sorted(set(cfg["auto_classified"]) | set(done))
    return done


# ---------------------------------------------------------------------------
# Экспорт / импорт стратегии
# ---------------------------------------------------------------------------

# Что входит в «стратегию»: правила и классификация. НЕ входит: id счёта, пути, кэш, снапшоты, секреты.
STRATEGY_KEYS = ("targets", "rules", "instruments", "reference_tickers", "blocked_tickers", "screener", "news",
                 "forecast", "fx_fallback", "strategy_preset", "auto_classify")
PERSONAL_FORECAST_KEYS = ("start_capital", "base_income", "plan_start", "plan_capital", "plan_income")


def export_strategy(cfg: dict, include_personal_numbers: bool = False) -> str:
    import config_migrate as CM
    out = {CM.KEY: CM.CURRENT_VERSION, **{k: cfg[k] for k in STRATEGY_KEYS if k in cfg}}
    if not include_personal_numbers and "forecast" in out:
        out["forecast"] = {k: v for k, v in out["forecast"].items() if k not in PERSONAL_FORECAST_KEYS}
    if "screener" in out:
        out["screener"] = {k: v for k, v in out["screener"].items() if k != "last_auto_run"}
    header = ("# Стратегия PortfolioCheck. Импорт: Настройки → Общие → «Импорт стратегии…».\n"
              "# Секретов и id счёта здесь нет.\n")
    return header + yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


def validate_strategy(data) -> list[str]:
    """Список проблем (пусто — всё в порядке). Проверяем то, что может сломать расчёт."""
    errs = []
    if not isinstance(data, dict):
        return ["файл не похож на стратегию: ожидается набор разделов (targets, rules, instruments…)"]
    unknown = [k for k in data if k not in STRATEGY_KEYS and k not in ("account", "config_version")]
    if unknown:
        errs.append("незнакомые разделы (будут пропущены): " + ", ".join(unknown))
    t = data.get("targets") or {}
    if t and not isinstance(t, dict):
        errs.append("targets: ожидается словарь класс → {min, max}")
    else:
        for cls, rng in t.items():
            if cls not in TARGET_KEYS:
                errs.append(f"targets: неизвестный класс «{cls}» (допустимо: {', '.join(TARGET_KEYS)})")
                continue
            try:
                lo, hi = float(rng["min"]), float(rng["max"])
                if not (0 <= lo <= hi <= 100):
                    errs.append(f"targets.{cls}: нужно 0 ≤ min ≤ max ≤ 100, а тут {lo}–{hi}")
            except Exception:  # noqa: BLE001
                errs.append(f"targets.{cls}: нужны числа min и max")
        mins = sum(float(r.get("min", 0)) for c, r in t.items() if c in TARGET_KEYS and c not in ("real_estate_wh", "real_estate_other")
                   and isinstance(r, dict))
        if mins > 100:
            errs.append(f"targets: сумма минимумов {mins:.0f}% больше 100% — коридоры несовместимы")
    ins = data.get("instruments") or {}
    for tick, cls in (ins.items() if isinstance(ins, dict) else []):
        if cls not in CLASSES:
            errs.append(f"instruments.{tick}: неизвестный класс «{cls}»")
    r = data.get("rules") or {}
    for key in ("drawdown_alert_pct", "single_name_max_pct"):
        if key in r:
            try:
                v = float(r[key])
                if not (0 < v <= 100):
                    errs.append(f"rules.{key}: ожидается 0–100, а тут {v}")
            except Exception:  # noqa: BLE001
                errs.append(f"rules.{key}: ожидается число")
    return errs


def import_strategy(cfg: dict, text: str) -> tuple[dict, list[str], list[str]]:
    """(новый конфиг, предупреждения, заменённые разделы). Бросает ValueError при фатальных ошибках."""
    # ChatGPT часто оборачивает ответ в ```yaml … ``` и добавляет текст вокруг — берём только содержимое блока
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"не удалось прочитать YAML: {e}") from e
    errs = validate_strategy(data)
    fatal = [e for e in errs if not e.startswith("незнакомые разделы")]
    if fatal:
        raise ValueError("\n".join(fatal))
    new = dict(cfg)
    replaced = []
    for k in STRATEGY_KEYS:
        if k in data:
            new[k] = data[k]; replaced.append(k)
    if "targets" in data:
        new["targets"] = {c: {"min": float(r["min"]), "max": float(r["max"])} for c, r in data["targets"].items()}
    if "screener" in data:
        # файл мог выгрузить старый экспорт или написать ChatGPT по старому примеру — приводим раздел к текущему
        # формату; неявные дефолты прежних версий добавляются только для файлов без версии
        import config_migrate as CM
        old = CM.version_of(data) < 1
        new["screener"] = CM.normalize_screener(data["screener"], None if old else {})
    new["setup_done"] = True
    return new, errs, replaced


def chatgpt_prompt() -> str:
    """Текст промпта для ChatGPT (часть после линии «---» в docs/CHATGPT_PROMPT.md). Работает и в собранном .app."""
    import sys
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for cand in (base / "docs" / "CHATGPT_PROMPT.md", Path(__file__).resolve().parents[1] / "docs" / "CHATGPT_PROMPT.md"):
        if cand.exists():
            text = cand.read_text(encoding="utf-8")
            return text.split("\n---\n", 1)[-1].strip()
    return ""


def copy_prompt_to_clipboard(parent=None) -> bool:
    from PySide6.QtWidgets import QApplication, QMessageBox
    text = chatgpt_prompt()
    if not text:
        QMessageBox.warning(parent, "Промпт не найден", "Файл docs/CHATGPT_PROMPT.md не найден рядом с программой.")
        return False
    QApplication.clipboard().setText(text)
    QMessageBox.information(parent, "Промпт скопирован",
                            "Промпт для ChatGPT в буфере обмена. Вставь его в новый чат — модель задаст вопросы и в конце "
                            "выдаст стратегию в YAML. Сохрани ответ в файл и загрузи: Настройки → Общие → «Импорт стратегии…».")
    return True
