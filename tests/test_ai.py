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


def test_upstream_error_names_requested_model_and_fallbacks():
    """Регресс: «провайдер модели Decart перегружен» при выбранной z-ai/glm-…:free — казалось, что настройка модели
    не применилась. Теперь в тексте модель из запроса и запасные, которые тоже пробовали."""
    body = {"error": {"message": "temporarily rate-limited upstream", "metadata": {"provider_name": "Decart"}}}
    rl = ai._describe_429(_429(body), {"model": "z-ai/glm-5.2:free", "models": ["z-ai/glm-5.2:free", "qwen/q:free"]})
    assert rl.kind == "upstream" and "z-ai/glm-5.2:free" in str(rl) and "qwen/q:free" in str(rl)


def test_default_model_never_router(monkeypatch):
    """Регресс: моделью по умолчанию становился маршрутизатор openrouter/free (случайная модель на каждый запрос)."""
    cat = [{"id": "openrouter/free", "free": True, "recommended": False},
           {"id": "some/model:free", "free": True, "recommended": False}]
    monkeypatch.setattr(ai, "_cached_catalog", lambda: cat)
    assert ai.default_model("openrouter") == "some/model:free"


def test_upstream_429_retried_once_and_real_model_recorded(monkeypatch):
    """Регресс: «провайдер перегружен» сразу превращался в ошибку сводки, хотя через секунды запрос проходит
    (руками — проходил); в шапке сводки стояла выбранная модель, даже если ответила запасная."""
    calls, slept = [], []

    def post(url, headers, body, proxy="", timeout=120):
        calls.append(body)
        if len(calls) == 1:
            raise ai.RateLimited("перегружен", "upstream")
        return {"model": "qwen/q:free", "choices": [{"message": {"content": '{"overall": "ok", "items": []}'}}]}
    monkeypatch.setattr(ai, "_post", post)
    monkeypatch.setattr(ai, "_sleep", slept.append)
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    s = ai.summarize("openrouter", "k", "z-ai/glm:free", [], [])
    assert len(calls) == 2 and slept == [ai.UPSTREAM_RETRY_S] and s.model == "qwen/q:free"

    calls.clear()
    monkeypatch.setattr(ai, "_post", lambda *a, **k: (_ for _ in ()).throw(ai.RateLimited("день", "daily")))
    with pytest.raises(ai.RateLimited):                          # дневной лимит — без повтора
        ai.summarize("openrouter", "k", "z-ai/glm:free", [], [])


def test_fallbacks_filled_beyond_recommended_and_check_without_them(monkeypatch):
    """Регресс: рекомендуемых бесплатных — одна-две, и при перегрузке «запасные (qwen…) тоже не ответили»; кнопка
    «Проверить» слала запасные — за выбранную модель мог ответить другой, и результат записывался ей."""
    cat = [{"id": "z-ai/glm:free", "free": True, "recommended": True, "context": 128000},
           {"id": "qwen/q:free", "free": True, "recommended": True, "context": 128000},
           {"id": "openrouter/free", "free": True, "recommended": False, "context": 200000},
           {"id": "tiny/m:free", "free": True, "recommended": False, "context": 8000},
           {"id": "big/m:free", "free": True, "recommended": False, "context": 64000}]
    monkeypatch.setattr(ai, "_cached_catalog", lambda: cat)
    assert ai._fallbacks("z-ai/glm:free") == ["qwen/q:free", "big/m:free"]
    seen = []
    monkeypatch.setattr(ai, "_post", lambda url, h, body, proxy="", timeout=120:
                        seen.append(body) or {"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    ai.check_model("openrouter", "k", "z-ai/glm:free")
    assert "models" not in seen[-1]


def test_startup_ai_is_cache_only(tmp_path, monkeypatch):
    """Регресс: при старте сводка по ленте с диска уходила в сеть, а через ~10 с — вторая по свежей ленте: два
    бесплатных запроса подряд, оба в перегруженного провайдера. При старте — только кэш."""
    import workers
    monkeypatch.setattr(ai, "summarize", lambda *a, **k: pytest.fail("при старте запроса быть не должно"))
    conf = ai.AIConfig("openrouter", "k", "z-ai/glm:free")
    w = workers.AIWorker(conf, [], [], tmp_path, False, cache_only=True)
    got = []
    w.skipped.connect(lambda: got.append("skipped")); w.done.connect(got.append)
    w.run()
    assert got == ["skipped"]


def test_exec_summaries_run_after_failed_news_summary(window, monkeypatch):
    """Регресс: сводка экранов запускалась только после УДАЧНОЙ сводки новостей — при днях «провайдер перегружен»
    экраны неделями показывали сводки от 19–22 сентября, а в логе не было ни одной попытки их обновить."""
    import screens.news as SN
    fired = []
    monkeypatch.setattr(SN.QTimer, "singleShot", lambda ms, fn: fired.append(ms))
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", None)
    window._on_ai_failed("перегружен", ai.AIConfig("openrouter", "k", "z-ai/glm:free"))
    assert SN.EXEC_AFTER_FAIL_MS in fired
    fired.clear()
    monkeypatch.setattr(ai, "RATE_BLOCK_UNTIL", datetime(2999, 1, 1))
    window._on_ai_failed("дневной лимит", ai.AIConfig("openrouter", "k", "z-ai/glm:free"))
    assert SN.EXEC_AFTER_FAIL_MS not in fired                       # дневной лимит — экраны не дёргаем
