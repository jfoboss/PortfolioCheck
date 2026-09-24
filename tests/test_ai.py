import io
import json
import urllib.error
import urllib.request
from datetime import datetime

import pytest

import ai


def M(id, pin=0, pout=0, ctx=64000, created=1):
    return {"id": id, "name": id, "pricing": {"prompt": str(pin), "completion": str(pout)}, "context_length": ctx,
            "created": created, "architecture": {"output_modalities": ["text"]}}


@pytest.fixture
def catalog(isolated_home, monkeypatch):
    d = isolated_home / "data" / "cache"
    data = {"data": [M("z-ai/glm-5.2:free", created=50), M("qwen/qwen3-235b-a22b:free", created=20),
                     M("some-lab/tiny-7b:free", ctx=8000), M("qwen/qwen3-235b-a22b", 1.8e-7, 5.4e-7, 131000),
                     {"id": "img/flux", "pricing": {}, "architecture": {"output_modalities": ["image"]}}]}
    (d / "openrouter_models.json").write_text(json.dumps({"at": datetime.now().isoformat(), "data": data}))
    monkeypatch.setattr(ai, "CATALOG_DIR", d)
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    return d


def test_catalog_parsing_and_recommendations(catalog):
    ms = ai.parse_openrouter_models(json.loads((catalog / "openrouter_models.json").read_text())["data"])
    ids = [m["id"] for m in ms]
    assert "img/flux" not in ids                                         # картинки отбрасываются
    tiny = next(m for m in ms if m["id"] == "some-lab/tiny-7b:free")
    assert not tiny["recommended"]                                       # мал контекст, неизвестное семейство
    assert ms[0]["recommended"] and ms[0]["free"]                        # рекомендуемые бесплатные — первыми


def test_default_model_comes_from_catalog(catalog):
    """Регресс: зашитая в код бесплатная модель исчезла из OpenRouter."""
    assert ai.default_model("openrouter") == "z-ai/glm-5.2:free"
    assert ai._fallbacks("z-ai/glm-5.2:free") == ["qwen/qwen3-235b-a22b:free"]
    assert ai._fallbacks("qwen/qwen3-235b-a22b") == []                   # у платных фолбэков нет


def _429(body):
    return urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("body,kind", [
    ({"error": {"message": "Rate limit exceeded: free-models-per-day", "metadata": {"headers": {"X-RateLimit-Limit": "50"}}}}, "daily"),
    ({"error": {"message": "Rate limit exceeded: free-models-per-min"}}, "minute"),
    ({"error": {"message": "temporarily rate-limited upstream", "metadata": {"provider_name": "Chutes"}}}, "upstream"),
])
def test_429_is_classified(body, kind):
    assert ai._describe_429(_429(body), {"model": "x:free"}).kind == kind


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(handler):
    return lambda *h: type("O", (), {"open": staticmethod(handler)})()


def test_daily_limit_blocks_further_requests(catalog, monkeypatch):
    calls = []

    def fail(req, timeout=0):
        calls.append(json.loads(req.data))
        raise _429({"error": {"message": "free-models-per-day", "metadata": {"headers": {"X-RateLimit-Limit": "50"}}}})
    monkeypatch.setattr(urllib.request, "build_opener", _opener(fail))
    for _ in range(3):
        with pytest.raises(ai.RateLimited):
            ai.ping("openrouter", "k", "")
    assert len(calls) == 1                                               # после дневного лимита в сеть не ходим
    assert calls[0]["models"][0] == "z-ai/glm-5.2:free"                  # модель по умолчанию + фолбэки


def test_exec_summaries_go_in_one_request(catalog, monkeypatch):
    calls = []

    def ok(req, timeout=0):
        calls.append(1)
        ans = {k: {"headline": k, "good": ["a"], "bad": [], "watch": []} for k in ("overview", "market", "strategy")}
        return _Resp(json.dumps({"choices": [{"message": {"content": json.dumps(ans)}}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", _opener(ok))
    got = ai.exec_summary_batch([("overview", "f"), ("market", "f"), ("strategy", "f")], "openrouter", "k", "")
    assert len(calls) == 1 and [s.kind for s in got] == ["overview", "market", "strategy"]


def test_cache_structural_change_breaks_interval(tmp_path):
    """Регресс: новый состав бумаг/промпт не пробивал шестичасовой интервал кэша."""
    c = ai.ResponseCache(tmp_path)
    c.put("news", "fp1", {"x": 1}, "subjA")
    assert c.get("news", "fp2", 6, False, "subjA")[0] is not None        # тот же состав — держим интервал
    assert c.get("news", "fp2", 6, False, "subjB")[0] is None            # новый состав — запрос
    c.data["news"].pop("structural")                                     # кэш старой версии без поля
    assert c.get("news", "fp2", 6, False, "subjA")[0] is None


def test_daily_block_survives_restart(catalog, monkeypatch):
    """Регресс: блокировка после дневного лимита жила только в памяти — после перезапуска приложение ещё раз
    упиралось в лимит."""
    from datetime import timedelta
    calls = []

    def fail(req, timeout=0):
        calls.append(1)
        raise _429({"error": {"message": "free-models-per-day", "metadata": {"headers": {"X-RateLimit-Limit": "50"}}}})
    monkeypatch.setattr(urllib.request, "build_opener", _opener(fail))
    with pytest.raises(ai.RateLimited):
        ai.ping("openrouter", "k", "")
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)                     # «перезапуск»
    assert ai.load_rate_block() is not None
    with pytest.raises(ai.RateLimited) as e:
        ai.ping("openrouter", "k", "")
    assert e.value.kind == "daily" and len(calls) == 1                   # в сеть второй раз не пошли
    # сброс наступил — старая запись игнорируется
    (catalog / ai.RATE_BLOCK_FILE).write_text(json.dumps({"until": (datetime.now() - timedelta(minutes=1)).isoformat()}))
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    assert ai.load_rate_block() is None and ai.RATE_BLOCK_UNTIL is None
    (catalog / ai.RATE_BLOCK_FILE).write_text("мусор")
    assert ai.load_rate_block() is None


@pytest.mark.parametrize("answer,verdict", [
    ('{"sum": 42, "city": "Москва", "items": ["спад", "падение", "снижение"], "sentiment": "negative"}', "good"),
    ('```json\n{"sum": 42, "city": "Москва", "items": ["спад", "падение", "снижение"], "sentiment": "negative"}\n```', "good"),
    ('{"sum": 41, "city": "Moscow", "items": ["decline"], "sentiment": "neutral"}', "partial"),
    ("Конечно! Вот ответ: сумма 42, столица — Москва.", "bad"),
])
def test_model_check_verdicts(answer, verdict):
    assert ai.evaluate_check(answer)["verdict"] == verdict


def test_check_model_one_request_and_remembered(catalog, monkeypatch):
    """Проверка модели — ровно один запрос; результат запоминается по модели и показывается без нового запроса."""
    calls = []

    def ok(req, timeout=0):
        calls.append(json.loads(req.data))
        ans = '{"sum": 42, "city": "Москва", "items": ["спад", "падение", "снижение"], "sentiment": "negative"}'
        return _Resp(json.dumps({"choices": [{"message": {"content": ans}}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", _opener(ok))
    r = ai.check_model("openrouter", "k", "", cache_dir=catalog)
    assert len(calls) == 1 and r["verdict"] == "good" and r["model"] == "z-ai/glm-5.2:free"
    again = ai.last_check("openrouter", "", catalog)
    assert again["verdict"] == "good" and len(calls) == 1
    assert "подходит" in ai.describe_check(again)
    assert ai.last_check("openrouter", "other/model", catalog) is None
