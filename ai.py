"""
ai.py — ИИ-сводка по новостям: тональность по бумагам (позитив / нейтрально / негатив) и общий вывод.

Провайдеры:
  claude  — Anthropic Messages API (https://api.anthropic.com/v1/messages), ключ с console.anthropic.com
  qwen    — Alibaba Model Studio / DashScope, OpenAI-совместимый endpoint, ключ с modelstudio.console.alibabacloud.com
  ollama  — локальный Ollama (http://localhost:11434), модель qwen2.5 и любая другая; ключ не нужен

Ответ модели просим строго в JSON, разбираем терпимо. Все запросы — stdlib urllib, прокси — из настроек
или из переменных окружения HTTPS_PROXY/HTTP_PROXY.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("portfolio.ai")

PROVIDERS = {
    # по умолчанию — бесплатная модель: ИИ-сводки работают сразу после создания ключа, баланс не тратится
    "openrouter": {"title": "OpenRouter (DeepSeek, Qwen, Claude и др., один ключ)", "model": "deepseek/deepseek-v4-flash-0731:free",
                   "url": "https://openrouter.ai/api/v1/chat/completions", "kind": "openai"},
    "claude": {"title": "Claude (Anthropic API)", "model": "claude-haiku-4-5-20251001",
               "url": "https://api.anthropic.com/v1/messages", "kind": "anthropic"},
    "qwen": {"title": "Qwen в облаке (Alibaba Model Studio)", "model": "qwen-plus",
             "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions", "kind": "openai"},
    "openai_compat": {"title": "Другой OpenAI-совместимый шлюз (свой endpoint)", "model": "",
                      "url": "", "kind": "openai"},
    "ollama": {"title": "Qwen локально (Ollama)", "model": "qwen2.5:7b",
               "url": "http://localhost:11434/api/chat", "kind": "ollama"},
}

# Инструкции — HTML: ссылки кликабельные, команды в <code>, текст выделяется и копируется
HOWTO = {
    "openrouter": ("1. <a href='https://openrouter.ai/keys'>openrouter.ai/keys</a> → Create Key — ключ за минуту, без верификации; "
                   "пополнение картой или крипто-кошельком, есть бесплатные модели с суффиксом <code>:free</code>.<br>"
                   "2. Модель выбирается из списка выше (★ — рекомендуемые). Если поле пустое — берётся первая рекомендуемая "
                   "бесплатная из текущего каталога. У бесплатных моделей общий лимит на аккаунт: 50 запросов в сутки, а после "
                   "покупки кредитов от $10 — 1000; платные модели этого лимита не имеют, сводка стоит доли цента.<br>"
                   "3. Endpoint менять не нужно. Прокси — только если openrouter.ai не открывается напрямую."),
    "openai_compat": ("Любой шлюз с OpenAI-совместимым API (Fireworks, Together, DeepInfra, Groq, ModelScope, свой vLLM/LM Studio):<br>"
                      "1. Впиши полный адрес в поле Endpoint, вида <code>https://host/v1/chat/completions</code> "
                      "(для LM Studio — <code>http://localhost:1234/v1/chat/completions</code>).<br>"
                      "2. Задай ключ (для локальных серверов без авторизации — любое слово) и точный id модели у провайдера.<br>"
                      "3. Нажми «Проверить» — ответ или текст ошибки покажет, что не так."),
    "claude": ("1. Открой <a href='https://platform.claude.com/'>platform.claude.com</a> → <b>API Keys</b> → Create Key; "
               "скопируй ключ сразу — показывается один раз. Подписка Pro/Max API не покрывает: это отдельный баланс "
               "(<a href='https://platform.claude.com/settings/billing'>Billing</a>), новый аккаунт получает ~$5 кредитов.<br>"
               "2. Модель по умолчанию — Haiku: цикл сводок (5 запросов) стоит около полцента.<br>"
               "3. Из России api.anthropic.com напрямую недоступен — укажи прокси ниже: <code>http://host:port</code> или "
               "<code>socks5h://host:port</code>."),
    "qwen": ("Официальный путь Alibaba: требует аккаунт Alibaba Cloud с верификацией (в ряде стран недоступен). Если не хочется — "
             "используй OpenRouter выше: там те же модели Qwen.<br>"
             "1. Открой <a href='https://modelstudio.console.alibabacloud.com/'>modelstudio.console.alibabacloud.com</a> "
             "(Model Studio / DashScope, международная версия) → <b>API-KEY</b> → создать.<br>"
             "2. Модель <code>qwen-plus</code> или дешевле <code>qwen-turbo</code>; на старте есть бесплатная квота.<br>"
             "3. Endpoint по умолчанию — международный (dashscope-intl). Если аккаунт в китайском регионе, впиши в поле Endpoint: "
             "<code>https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions</code>."),
    "ollama": ("1. Установить: <code>brew install ollama</code> и <code>brew services start ollama</code> "
               "(или приложение с <a href='https://ollama.com/download/mac'>ollama.com</a>).<br>"
               "2. Скачать модель: <code>ollama pull qwen2.5:7b</code> (≈4,7 ГБ; на 8 ГБ памяти — <code>qwen2.5:3b</code>).<br>"
               "3. Ключ не нужен, данные не покидают Mac. Цикл сводок на M-серии — одна-две минуты в фоне."),
}

# ---------------------------------------------------------------------------
# Executive summary по экранам: единый формат «хорошо / плохо / следить»
# ---------------------------------------------------------------------------

EXEC_FORMAT = ("Отвечай строго JSON без текста вокруг: {\"headline\": \"одна фраза — главное сегодня\", "
               "\"good\": [\"2-4 пункта\"], \"bad\": [\"2-4 пункта\"], \"watch\": [\"2-3 пункта: на что смотреть дальше\"]}. "
               "Пиши по-русски, коротко, с цифрами из данных. Никаких советов покупать или продавать — только оценка. "
               "Оценивай относительно ПРАВИЛ инвестора, если они даны, а не абстрактно.")

EXEC_SYSTEMS = {
    "overview": ("Ты личный аналитик инвестора. Тебе дают срез портфеля на сегодня: стоимость, движение за день и месяц, "
                 "доли классов и целевые коридоры инвестора, сработавшие сигналы, доход за 12 месяцев. "
                 "Задача — сказать, что сегодня хорошо, что плохо и на что обратить внимание. " + EXEC_FORMAT),
    "portfolio": ("Ты риск-менеджер. Тебе дают структуру портфеля: позиции с долями и результатом за всё время, разбивку по "
                  "отраслям и валютам, заблокированные активы с оценкой потерь. Оцени здоровье структуры: концентрации, "
                  "перекосы, слабые места, что в порядке. " + EXEC_FORMAT),
    "market": ("Ты рыночный аналитик. Тебе дают движение отраслей портфеля за день и месяц, заголовки новостей с "
               "тональностью и список кандидатов из скринера. Объясни, что двигает портфель и связано ли это с новостями; "
               "отдели шум от значимого. " + EXEC_FORMAT),
    "strategy": ("Ты финансовый планировщик. Тебе дают параметры долгосрочного плана (капитал, доход, пополнения, реинвест, "
                 "рост выплат, инфляция, цель), расчётный год достижения цели и динамику. Оцени, идёт ли инвестор по плану, "
                 "где чувствительность модели и какие допущения рискованные. " + EXEC_FORMAT),
}


@dataclass
class ExecSummary:
    kind: str
    at: str
    provider: str
    model: str
    headline: str = ""
    good: list = field(default_factory=list)
    bad: list = field(default_factory=list)
    watch: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


def _openai_headers(provider: str, key: str) -> dict:
    h = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        h.update({"HTTP-Referer": "https://github.com/portfolio-check", "X-Title": "PortfolioCheck"})
    return h


CATALOG_DIR: Path | None = None     # задаёт приложение: где лежит кэш каталога OpenRouter


def _cached_catalog() -> list[dict]:
    if CATALOG_DIR is None:
        return []
    f = CATALOG_DIR / "openrouter_models.json"
    try:
        return parse_openrouter_models(json.loads(f.read_text(encoding="utf-8"))["data"]) if f.exists() else []
    except Exception:  # noqa: BLE001
        return []


def default_model(provider: str) -> str:
    """Модель по умолчанию. Для OpenRouter — первая рекомендуемая бесплатная из каталога: бесплатные версии
    снимают без предупреждения, и зашитый в код id со временем перестаёт существовать."""
    if provider == "openrouter":
        cat = _cached_catalog()
        rec = [m["id"] for m in cat if m["free"] and m["recommended"]]
        if rec:
            return rec[0]
        if cat and PROVIDERS["openrouter"]["model"] not in {m["id"] for m in cat}:
            free = [m["id"] for m in cat if m["free"]]
            if free:
                return free[0]
    return PROVIDERS[provider]["model"]


def _fallbacks(model: str, n: int = 2) -> list[str]:
    """Запасные бесплатные модели: если провайдер основной перегружен, OpenRouter сам уйдёт на следующую."""
    if not model.endswith(":free"):
        return []
    return [m["id"] for m in _cached_catalog() if m["free"] and m["recommended"] and m["id"] != model][:n]


def _chat(provider: str, key: str, model: str, system: str, user: str, proxy: str, base_url: str, max_tokens: int = 1200) -> str:
    cfg = PROVIDERS[provider]; model = model or default_model(provider); url = base_url or cfg["url"]
    if provider == "openrouter" and model.endswith(":free") and RATE_BLOCK_UNTIL and datetime.now() < RATE_BLOCK_UNTIL:
        raise RateLimited(f"дневной лимит бесплатных моделей OpenRouter исчерпан, запросы не отправляются до "
                          f"{RATE_BLOCK_UNTIL:%H:%M %d.%m}. Показаны ответы из кэша.", "daily", RATE_BLOCK_UNTIL)
    if not url:
        raise ValueError("не задан Endpoint (Настройки → ИИ)")
    if not model:
        raise ValueError("не задана модель (Настройки → ИИ)")
    kind = cfg["kind"]
    if kind == "anthropic":
        resp = _post(url, {"x-api-key": key, "anthropic-version": "2023-06-01"},
                     {"model": model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]}, proxy)
        return "".join(b.get("text", "") for b in resp.get("content", []))
    if kind == "openai":
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2}
        if provider == "openrouter":
            fb = _fallbacks(model)
            if fb:
                body["models"] = [model] + fb        # маршрутизация OpenRouter: при сбое провайдера — следующая модель
        resp = _post(url, _openai_headers(provider, key), body, proxy)
        return resp["choices"][0]["message"]["content"]
    resp = _post(url, {}, {"model": model, "stream": False, "format": "json",
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, "")
    return resp["message"]["content"]


def exec_summary(kind: str, facts: str, provider: str, key: str, model: str, proxy: str = "", base_url: str = "") -> ExecSummary:
    text = _chat(provider, key, model, EXEC_SYSTEMS[kind], facts, proxy, base_url)
    d = _extract_json(text)
    def lst(x):
        return [str(i) for i in (x or [])][:5]
    return ExecSummary(kind, datetime.now().isoformat(timespec="minutes"), provider, model or default_model(provider),
                       str(d.get("headline", "")), lst(d.get("good")), lst(d.get("bad")), lst(d.get("watch")))


EXEC_BATCH_SYSTEM = ("Ты личный аналитик инвестора. Ниже — данные по нескольким экранам его приложения, каждый в своём "
                     "разделе ===ЭКРАН: ключ===. Для КАЖДОГО экрана дай краткий вывод в своей роли: overview — аналитик "
                     "портфеля на сегодня; portfolio — риск-менеджер по структуре; market — рыночный аналитик (что двигает "
                     "портфель, связь с новостями, шум против значимого); strategy — финансовый планировщик (идёт ли по плану, "
                     "рискованные допущения). Отвечай строго одним JSON без пояснений вокруг: {\"ключ экрана\": "
                     "{\"headline\": \"одна фраза\", \"good\": [\"до 5 пунктов\"], \"bad\": [...], \"watch\": [...]}, ...}. "
                     "Оценивай относительно правил инвестора. Пиши по-русски, сухо, без советов купить/продать.")


def exec_summary_batch(jobs: list[tuple[str, str]], provider: str, key: str, model: str, proxy: str = "",
                       base_url: str = "") -> list[ExecSummary]:
    """Сводки по нескольким экранам ОДНИМ запросом — у бесплатных моделей OpenRouter лимит считается в запросах."""
    if len(jobs) == 1:
        return [exec_summary(jobs[0][0], jobs[0][1], provider, key, model, proxy, base_url)]
    user = "\n\n".join(f"===ЭКРАН: {kind}===\n{facts}" for kind, facts in jobs)
    text = _chat(provider, key, model, EXEC_BATCH_SYSTEM, user, proxy, base_url, max_tokens=600 * len(jobs))
    d = _extract_json(text)
    def lst(x):
        return [str(i) for i in (x or [])][:5]
    at = datetime.now().isoformat(timespec="minutes"); m = model or default_model(provider)
    out = []
    for kind, _ in jobs:
        part = d.get(kind) or {}
        if part:
            out.append(ExecSummary(kind, at, provider, m, str(part.get("headline", "")), lst(part.get("good")),
                                   lst(part.get("bad")), lst(part.get("watch"))))
    return out


def save_exec(s: ExecSummary, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"exec_{s.kind}.json").write_text(s.to_json(), encoding="utf-8")


def load_exec(kind: str, cache_dir: Path) -> ExecSummary | None:
    f = cache_dir / f"exec_{kind}.json"
    if not f.exists():
        return None
    try:
        return ExecSummary(**json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def render_exec_html(s: ExecSummary, mask=None, note: str = "") -> str:
    """Три колонки: хорошо / плохо / следить. mask — функция маскирования сумм (режим «глазика»)."""
    m = mask or (lambda t: t)
    def col(mark, title, color, items):
        lis = "".join(f"<li>{m(i)}</li>" for i in items) or "<li style='color:gray'>—</li>"
        return (f"<td style='vertical-align:top;padding:0 10px 0 0;width:33%'>"
                f"<div title='{title}' style='color:{color};font-weight:700;font-size:15px'>{mark}</div>"
                f"<ul style='margin:2px 0 0 14px;padding:0'>{lis}</ul></td>")
    return (f"<div style='font-weight:600;font-size:13px'>{m(s.headline)}</div>"
            f"<table style='width:100%'><tr>{col('✓', 'Хорошо', '#34C759', s.good)}{col('✕', 'Плохо', '#FF453A', s.bad)}"
            f"{col('⚑', 'Следить', '#FF9F0A', s.watch)}</tr></table>"
            f"<div style='color:gray;font-size:11px'>ИИ · {s.provider}/{s.model} · {s.at.replace('T', ' ')}"
            + (f" · {note}" if note else "") + " · оценка, не рекомендация</div>")


SYSTEM = ("Ты аналитик российского фондового рынка. Тебе дают список заголовков новостей и список бумаг инвестора. "
          "Оцени влияние новостей на КАЖДУЮ бумагу, к которой они относятся, и на портфель в целом. Отвечай строго JSON без "
          "пояснений вокруг: {\"overall\": \"2-3 предложения общего вывода\", \"macro\": \"1-2 предложения про ставку/рубль/инфляцию, "
          "если есть новости\", \"items\": [{\"ticker\": \"...\", \"sentiment\": \"positive|neutral|negative\", "
          "\"note\": \"одна фраза почему\"}]}. По бумагам инвестора включай в items только те, по которым были новости. "
          "Бумаги-кандидаты включай в items ВСЕ до единой: если прямых новостей по ним нет, оценивай по отраслевому и "
          "макро-контексту из этих же заголовков (для них в note начинай с «по отрасли:»), а если и отраслевых сигналов нет — "
          "ставь neutral и note «нет новостей». Пиши по-русски, сухо, без советов покупать/продавать.")


@dataclass
class Summary:
    at: str
    provider: str
    model: str
    overall: str = ""
    macro: str = ""
    items: list[dict] = field(default_factory=list)
    news_count: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @staticmethod
    def from_json(s: str) -> "Summary":
        return Summary(**json.loads(s))


class RateLimited(RuntimeError):
    """429 с понятной причиной. kind: daily (дневной лимит аккаунта) | minute | upstream (перегружен провайдер) | other."""
    def __init__(self, text: str, kind: str, reset: datetime | None = None):
        super().__init__(text); self.kind = kind; self.reset = reset


# После дневного лимита бесплатных моделей не отправляем запросы до сброса — иначе каждый вызов впустую жжёт время.
# Хранится и на диске (cache/ai_rate_block.json рядом с каталогом): иначе после перезапуска приложение ещё раз
# упиралось в лимит, прежде чем узнать о нём.
RATE_BLOCK_UNTIL: datetime | None = None
RATE_BLOCK_FILE = "ai_rate_block.json"


def set_rate_block(until: datetime) -> None:
    global RATE_BLOCK_UNTIL
    RATE_BLOCK_UNTIL = until
    if CATALOG_DIR is None:
        return
    try:
        (CATALOG_DIR / RATE_BLOCK_FILE).write_text(json.dumps({"until": until.isoformat()}), encoding="utf-8")
    except OSError as e:
        log.warning("не удалось сохранить блокировку лимита: %s", e)


def load_rate_block() -> datetime | None:
    """При старте: восстановить блокировку из файла, если сброс ещё не наступил. Возвращает её или None."""
    global RATE_BLOCK_UNTIL
    if CATALOG_DIR is None:
        return None
    try:
        until = datetime.fromisoformat(json.loads((CATALOG_DIR / RATE_BLOCK_FILE).read_text(encoding="utf-8"))["until"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if until > datetime.now() and (RATE_BLOCK_UNTIL is None or until > RATE_BLOCK_UNTIL):
        RATE_BLOCK_UNTIL = until
    return RATE_BLOCK_UNTIL if RATE_BLOCK_UNTIL and RATE_BLOCK_UNTIL > datetime.now() else None


def _describe_429(e, body: dict) -> RateLimited:
    raw = ""
    try:
        raw = e.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    msg, meta, hdrs = "", {}, {}
    try:
        err = (json.loads(raw) or {}).get("error") or {}
        msg = str(err.get("message", "")); meta = err.get("metadata") or {}
        hdrs = meta.get("headers") or {}
    except Exception:  # noqa: BLE001
        msg = raw[:200]
    for k in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
        if k not in hdrs and e.headers is not None and e.headers.get(k):
            hdrs[k] = e.headers.get(k)
    reset = None
    try:
        rv = float(hdrs.get("X-RateLimit-Reset", 0))
        if rv > 0:
            reset = datetime.fromtimestamp(rv / 1000 if rv > 1e11 else rv)
    except Exception:  # noqa: BLE001
        pass
    model = str(body.get("model", ""))
    low = msg.lower()
    limit = str(hdrs.get("X-RateLimit-Limit", ""))
    when = f" Сброс примерно в {reset:%H:%M %d.%m}." if reset else " Сброс — в начале следующих суток (UTC)."
    if "per-day" in low or "rpd" in low or "per day" in low or limit in ("50", "1000"):
        return RateLimited("исчерпан дневной лимит бесплатных моделей OpenRouter на аккаунт: 50 запросов в сутки "
                           "(после покупки кредитов от $10 — 1000). Лимит общий для всех :free-моделей, смена модели "
                           "не поможет." + when + " Ответы в кэше продолжают показываться.", "daily", reset)
    if "per-min" in low or "per minute" in low or "rpm" in low or limit == "20":
        return RateLimited("лимит 20 запросов в минуту для бесплатных моделей — подожди минуту.", "minute", reset)
    prov = meta.get("provider_name") or meta.get("provider_code") or ""
    if prov or "upstream" in low or "temporarily" in low:
        return RateLimited(f"провайдер модели{(' ' + str(prov)) if prov else ''} перегружен (это не твой лимит) — "
                           "повтори позже или выбери другую модель ★.", "upstream", reset)
    return RateLimited(f"слишком много запросов{': ' + msg[:150] if msg else ''}"
                       + (" — для бесплатной модели это обычно дневной лимит аккаунта" if model.endswith(":free") else ""),
                       "other", reset)


def _post(url: str, headers: dict, body: dict, proxy: str = "", timeout: int = 120) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        model = str(body.get("model", ""))
        if e.code == 429:
            rl = _describe_429(e, body)
            if rl.kind == "daily":
                set_rate_block(rl.reset or (datetime.now() + __import__("datetime").timedelta(hours=6)))
            raise rl from e
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 404:
            hint = f"модель «{model}» не найдена — возможно, её убрали; выбери другую в Настройки → ИИ"
        elif e.code == 402:
            hint = "на балансе провайдера нет денег — пополни или выбери бесплатную модель (с суффиксом :free)"
        elif e.code in (401, 403):
            hint = "ключ не принят — проверь его в Настройки → ИИ"
        else:
            hint = detail or e.reason
        raise RuntimeError(f"HTTP {e.code}: {hint}") from e


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("в ответе модели нет JSON")
    return json.loads(m.group(0))


def build_prompt(news_items, positions, max_news: int = 60, candidates=None) -> str:
    names = {p.ticker: p.name for p in positions}
    lines = [f"Бумаги инвестора: " + ", ".join(f"{t} ({n})" for t, n in list(names.items())[:60])]
    if candidates:
        lines.append("Бумаги-кандидаты (в портфеле их нет, инвестор их рассматривает — оцени КАЖДУЮ, при отсутствии прямых "
                     "новостей по отраслевому контексту): "
                     + "; ".join(f"{c.ticker} — {c.name}, отрасль: {getattr(c, 'sector', '') or 'не указана'}"
                                 for c in candidates[:20]))
    lines += ["", "Новости:"]
    for it in news_items[:max_news]:
        tag = ",".join(it.matched) if it.matched else "макро"
        lines.append(f"- [{tag}] {it.title}")
    return "\n".join(lines)


def summarize(provider: str, key: str, model: str, news_items, positions, proxy: str = "", base_url: str = "",
              candidates=None) -> Summary:
    prompt = build_prompt(news_items, positions, candidates=candidates)
    text = _chat(provider, key, model, SYSTEM, prompt, proxy, base_url, max_tokens=1500)
    data = _extract_json(text)
    items = []
    for it in data.get("items", []) or []:
        s = str(it.get("sentiment", "neutral")).lower()
        s = "positive" if s.startswith("pos") else ("negative" if s.startswith("neg") else "neutral")
        items.append({"ticker": str(it.get("ticker", "")), "sentiment": s, "note": str(it.get("note", ""))})
    return Summary(datetime.now().isoformat(timespec="minutes"), provider, model or default_model(provider),
                   str(data.get("overall", "")), str(data.get("macro", "")), items, len(news_items))


def ping(provider: str, key: str, model: str, proxy: str = "", base_url: str = "") -> str:
    """Короткий запрос для кнопки «Проверить». Возвращает текст ответа модели. Тратит один запрос из лимита."""
    return _chat(provider, key, model, "Отвечай одним словом.", "Ответь одним словом: ОК", proxy, base_url, max_tokens=20)


# ---------------------------------------------------------------------------
# Кэш ответов: по содержимому запроса (hash) + по времени (min_interval_h)
# ---------------------------------------------------------------------------

import hashlib


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class ResponseCache:
    """cache/ai_responses.json: {key: {"at": iso, "fp": hash, "data": {...}, "structural": hash}}. Один файл на все виды сводок."""

    def __init__(self, cache_dir: Path):
        self.path = cache_dir / "ai_responses.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:  # noqa: BLE001
            self.data = {}

    def get(self, key: str, fp: str, min_interval_h: float, force: bool = False, structural: str = "") -> tuple[dict | None, str]:
        """(данные, причина). Кэш отдаётся, если отпечаток совпал или ещё не прошёл интервал.
        force — игнорировать интервал. structural — отпечаток состава запроса (набор бумаг): если он изменился,
        интервал не держит: в прошлой сводке этих бумаг просто не было."""
        e = self.data.get(key)
        if not e:
            return None, "нет в кэше"
        if e.get("fp") == fp:
            return e["data"], "данные не изменились"
        if structural and e.get("structural", "") != structural:
            # состав бумаг в запросе другой (или кэш записан старой версией без этого поля) — интервал не держит
            return None, "изменился состав бумаг"
        if not force:
            try:
                age_h = (datetime.now() - datetime.fromisoformat(e["at"])).total_seconds() / 3600
            except Exception:  # noqa: BLE001
                age_h = 1e9
            if age_h < min_interval_h:
                return e["data"], f"интервал {min_interval_h:g} ч не прошёл ({age_h:.1f} ч)"
        return None, "устарело"

    def put(self, key: str, fp: str, data: dict, structural: str = "") -> None:
        self.data[key] = {"at": datetime.now().isoformat(timespec="minutes"), "fp": fp, "data": data, "structural": structural}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


def subjects_fingerprint(positions, candidates=None) -> str:
    """Отпечаток «структуры» запроса: состав бумаг + версия промпта. Меняется, когда появляются новые кандидаты
    или когда меняется сам промпт — в обоих случаях прошлый ответ не подходит, и интервал его не держит."""
    return _fingerprint(",".join(sorted(p.ticker for p in positions)),
                        ",".join(sorted(c.ticker for c in (candidates or [])[:20])),
                        _fingerprint(SYSTEM))


def news_fingerprint(news_items, provider: str, model: str, candidates=None) -> str:
    titles = sorted(it.title.strip().lower() for it in news_items[:60])
    cand = ",".join(sorted(c.ticker for c in (candidates or [])[:20]))
    return _fingerprint(provider, model, SYSTEM, cand, "\n".join(titles))


def exec_fingerprint(kind: str, facts: str, provider: str, model: str) -> str:
    # числа огрубляем (2 значащие цифры; проценты < 10 — до 0.1), чтобы копеечные движения не ломали кэш
    norm = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", facts)          # 13,274,633 → 13274633 (разделители тысяч)
    norm = re.sub(r"\d+(?:\.\d+)?", lambda m: _coarse(m.group(0)), norm)
    return _fingerprint(kind, provider, model, EXEC_SYSTEMS[kind], norm)


def _coarse(num: str) -> str:
    try:
        v = float(num)
    except ValueError:
        return num
    if v == 0:
        return "0"
    if abs(v) < 10:
        return str(round(v, 1))
    from math import log10, floor
    digits = 2 - int(floor(log10(abs(v)))) - 1
    return str(round(v, digits))


def save_summary(s: Summary, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "ai_summary.json").write_text(s.to_json(), encoding="utf-8")


def load_summary(cache_dir: Path) -> Summary | None:
    f = cache_dir / "ai_summary.json"
    if not f.exists():
        return None
    try:
        return Summary.from_json(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# Знаки тональности — как на терминалах (Bloomberg/Reuters/биржевые табло): ▲ вверх, ▼ вниз, ■ без изменений.
SENT_MARK = {"positive": "▲", "neutral": "■", "negative": "▼"}
SENT_COLOR = {"positive": "#34C759", "neutral": "#8E8E93", "negative": "#FF453A"}
SENT_TITLE = {"positive": "Позитив для бумаги", "neutral": "Нейтрально", "negative": "Негатив для бумаги"}


def render_html(s: Summary, names: dict[str, str], privacy: bool = False, note: str = "") -> str:
    """HTML для панели: общий вывод, макро, по бумагам со знаками тональности."""
    colors, labels = SENT_COLOR, SENT_TITLE
    parts = [f"<div style='color:gray;font-size:11px'>ИИ-сводка · {s.provider} / {s.model} · {s.at.replace('T', ' ')} · {s.news_count} новостей"
             + (f" · {note}" if note else "") + "</div>"]
    if s.overall:
        parts.append(f"<p>{s.overall}</p>")
    if s.macro:
        parts.append(f"<p><b>Макро:</b> {s.macro}</p>")
    if s.items:
        rows = []
        for it in s.items:
            col = colors.get(it["sentiment"], "#8E8E93")
            mark = SENT_MARK.get(it["sentiment"], "■")
            title = labels.get(it["sentiment"], "")
            rows.append(f"<tr><td title='{title}' style='padding:2px 6px 2px 0;color:{col};font-weight:700'>{mark}</td>"
                        f"<td style='padding:2px 8px 2px 0'><span style='background:{col};color:white;border-radius:4px;"
                        f"padding:1px 6px;font-weight:600'>{it['ticker']}</span></td>"
                        f"<td style='padding:2px 0'>{names.get(it['ticker'], '')}: {it['note']}</td></tr>")
        parts.append("<table>" + "".join(rows) + "</table>")
    parts.append("<div style='color:gray;font-size:11px'>Модель оценивает заголовки, не отчётность; это не рекомендация.</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Каталог моделей OpenRouter (публичный, ключ не нужен)
# ---------------------------------------------------------------------------

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Что важно для наших задач: ответ строго в JSON, приличный русский, контекст на ~60 новостей (≥ 32k токенов).
# Семейства, которые с этим стабильно справляются, — основа рекомендаций; остальные показываются без звезды.
GOOD_FAMILIES = ("deepseek/", "qwen/", "google/gemini", "anthropic/claude", "openai/gpt", "meta-llama/llama-3.3-70b",
                 "meta-llama/llama-4", "mistralai/mistral-large", "mistralai/mistral-medium", "x-ai/grok", "moonshotai/kimi",
                 "z-ai/glm")
MIN_CONTEXT = 32_000


def _price(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def parse_openrouter_models(data: dict) -> list[dict]:
    """[{id, name, free, in_per_m, out_per_m, context, recommended, why}] — цены в $ за 1 млн токенов."""
    out = []
    for m in (data or {}).get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pr = m.get("pricing") or {}
        pin, pout = _price(pr.get("prompt")), _price(pr.get("completion"))
        free = mid.endswith(":free") or (pin == 0 and pout == 0)
        ctx = int(m.get("context_length") or 0)
        modality = ((m.get("architecture") or {}).get("output_modalities") or ["text"])
        if "text" not in modality:
            continue
        good_family = mid.startswith(GOOD_FAMILIES)
        reasons = []
        if good_family:
            reasons.append("семейство надёжно отдаёт JSON и пишет по-русски")
        if ctx >= MIN_CONTEXT:
            reasons.append(f"контекст {ctx // 1000}k — хватает на всю ленту")
        cheap = free or (0 <= pin * 1e6 <= 0.5 and 0 <= pout * 1e6 <= 2.0)
        if free:
            reasons.append("бесплатная")
        elif cheap:
            reasons.append("дешёвая: сводка стоит доли цента")
        recommended = good_family and ctx >= MIN_CONTEXT and cheap
        out.append({"id": mid, "name": m.get("name") or mid, "free": free, "context": ctx,
                    "in_per_m": pin * 1e6 if pin >= 0 else None, "out_per_m": pout * 1e6 if pout >= 0 else None,
                    "recommended": recommended, "why": "; ".join(reasons), "created": int(m.get("created") or 0)})
    # рекомендуемые наверх, внутри — бесплатные, затем по свежести
    out.sort(key=lambda x: (not x["recommended"], not x["free"], -x["created"], x["id"]))
    return out


def fetch_openrouter_models(cache_dir: Path | None = None, proxy: str = "", max_age_h: float = 24,
                            force: bool = False) -> tuple[list[dict], str]:
    """(модели, откуда: «кэш» / «сеть»). Кэш на сутки: список меняется нечасто."""
    f = cache_dir / "openrouter_models.json" if cache_dir else None
    if f and f.exists() and not force:
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            if datetime.fromisoformat(saved["at"]) > datetime.now() - __import__("datetime").timedelta(hours=max_age_h):
                return parse_openrouter_models(saved["data"]), "кэш"
        except Exception:  # noqa: BLE001
            pass
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    req = urllib.request.Request(OPENROUTER_MODELS_URL, headers={"User-Agent": "PortfolioCheck"})
    with urllib.request.build_opener(*handlers).open(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    if f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"at": datetime.now().isoformat(), "data": data}, ensure_ascii=False), encoding="utf-8")
    return parse_openrouter_models(data), "сеть"


def model_label(m: dict) -> str:
    star = "★ " if m["recommended"] else ""
    if m["free"]:
        price = "бесплатно"
    elif m["in_per_m"] is not None:
        price = f"${m['in_per_m']:.2f} / ${m['out_per_m']:.2f} за 1М"
    else:
        price = "цена ?"
    ctx = f"{m['context'] // 1000}k" if m["context"] else "?"
    return f"{star}{m['name']} — {price}, {ctx}"


def openrouter_key_info(key: str, proxy: str = "") -> dict:
    """{'free_tier': bool|None, 'label': str} по GET /api/v1/auth/key. Ключ без покупки кредитов — 50 бесплатных запросов в сутки."""
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    req = urllib.request.Request("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.build_opener(*handlers).open(req, timeout=15) as r:
        d = (json.loads(r.read().decode("utf-8")) or {}).get("data") or {}
    return {"free_tier": d.get("is_free_tier"), "label": d.get("label", ""), "usage": d.get("usage")}
