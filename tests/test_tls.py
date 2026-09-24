"""TLS для сайтов (net.py): корни ОС, а не пути OpenSSL машины сборки."""
import ssl
import sys
import urllib.error
import urllib.request

import pytest

import net


def test_fallback_has_roots_without_openssl_paths(monkeypatch):
    """Регресс: dmg из CI искал корни в папке setup-python раннера — на Mac пользователя её нет, и все сайты (RSS,
    ISS, ЦБ, OpenRouter) падали с CERTIFICATE_VERIFY_FAILED. Даже без truststore и без путей OpenSSL корни есть."""
    monkeypatch.setitem(sys.modules, "truststore", None)             # import truststore → ImportError
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/cert.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/certs")
    net._context.cache_clear()
    try:
        ctx = net.system_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
        assert len(ctx.get_ca_certs()) > 50                          # корни certifi
    finally:
        net._context.cache_clear()


def test_truststore_used_when_available():
    net._context.cache_clear()
    ctx = net.system_context()
    assert type(ctx).__module__.startswith("truststore") and ctx.verify_mode == ssl.CERT_REQUIRED


def _capture(monkeypatch, errors=()):
    seen, errs = [], list(errors)

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __init__(self): self.left = b"<rss/>"
        def read(self, n=-1): out, self.left = self.left, b""; return out

    def fake(req, timeout=None, context=None):
        seen.append(context)
        if errs:
            raise errs.pop(0)
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_news_fetch_uses_system_roots(monkeypatch):
    """Регресс: news._fetch шёл с контекстом по умолчанию (пути OpenSSL) — через него же факторы и календарь ЦБ."""
    seen = _capture(monkeypatch)
    assert net.fetch("https://example.org/rss") == b"<rss/>"
    assert seen == [net.system_context()]


def test_news_legacy_retry_only_for_non_verification_errors(monkeypatch):
    """Регресс: urlopen заворачивает SSLError в URLError — повтор с устаревшими шифрами не срабатывал никогда.
    Непрошедшую проверку сертификата не прощаем: без повтора и без отключения проверки."""
    seen = _capture(monkeypatch, [urllib.error.URLError(ssl.SSLError(1, "unsupported protocol"))])
    assert net.fetch("https://example.org/rss") == b"<rss/>"
    assert seen[1] is net.system_context(legacy=True) and seen[1].verify_mode == ssl.CERT_REQUIRED
    seen = _capture(monkeypatch, [urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed"))])
    with pytest.raises(urllib.error.URLError):
        net.fetch("https://example.org/rss")
    assert len(seen) == 1


def test_ai_opener_uses_system_roots(monkeypatch):
    import ai
    got = {}

    def fake_build(*handlers):
        got["h"] = handlers
        raise RuntimeError("стоп")
    monkeypatch.setattr(urllib.request, "build_opener", fake_build)
    with pytest.raises(RuntimeError):
        ai._post("https://openrouter.ai/api/v1/chat/completions", {}, {"model": "x"})
    https = [h for h in got["h"] if isinstance(h, urllib.request.HTTPSHandler)]
    assert https and https[0]._context is net.system_context()


def test_openrouter_catalog_and_key_use_system_roots(monkeypatch):
    """Регресс: список моделей и проверка ключа OpenRouter строили свой opener без корней ОС — в сборке из CI
    «Обновить список» и определение бесплатного тарифа падали с CERTIFICATE_VERIFY_FAILED."""
    import ai
    seen = []

    def fake_build(*handlers):
        seen.append(handlers)
        raise RuntimeError("стоп")
    monkeypatch.setattr(urllib.request, "build_opener", fake_build)
    for call in (lambda: ai.fetch_openrouter_models(None, force=True), lambda: ai.openrouter_key_info("k")):
        with pytest.raises(RuntimeError):
            call()
    assert len(seen) == 2
    for handlers in seen:
        https = [h for h in handlers if isinstance(h, urllib.request.HTTPSHandler)]
        assert https and https[0]._context is net.system_context()


def test_mincifry_root_from_bundle_trusted_for_sites(tmp_path, monkeypatch):
    """Сайты на сертификатах Минцифры: корень берётся из бандла T-Invest (Настройки → Доступ → «Собрать бандл»),
    в систему ничего не ставится. В тестовых данных — публичная цепочка Russian Trusted Sub CA → Root CA."""
    import grpc
    import portfolio_check as pc
    from pathlib import Path
    roots = (Path(grpc.__file__).parent / "_cython" / "_credentials" / "roots.pem").read_text(encoding="utf-8")
    chain = (Path(__file__).parent / "data" / "mincifry_chain.pem").read_text(encoding="utf-8")
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text(roots.rstrip("\n") + "\n" + chain, encoding="utf-8")
    der = net.mincifry_roots(bundle)
    assert der and der.count(b"Russian Trusted") >= 2                      # только Минцифры, не весь бандл
    assert len(der) < 10_000
    monkeypatch.setattr(pc, "_bundle_path", lambda: bundle)
    import truststore
    loaded = []
    orig = truststore.SSLContext.load_verify_locations
    monkeypatch.setattr(truststore.SSLContext, "load_verify_locations",
                        lambda self, cafile=None, capath=None, cadata=None: (loaded.append(cadata),
                                                                            orig(self, cafile, capath, cadata))[1])
    net._context.cache_clear()
    net.system_context()
    assert loaded == [der]                                                  # truststore: якоря поверх хранилища ОС
    monkeypatch.setitem(sys.modules, "truststore", None)                  # запасной путь: видно по списку корней
    net._context.cache_clear()
    subjects = [str(c.get("subject")) for c in net.system_context().get_ca_certs()]
    assert any("Russian Trusted Root CA" in x for x in subjects)
    monkeypatch.setattr(pc, "_bundle_path", lambda: None)                 # бандла нет — только хранилище ОС / certifi
    assert not any("Russian Trusted" in str(c.get("subject")) for c in net.system_context().get_ca_certs())
    net._context.cache_clear()


def test_fetch_gives_up_on_trickling_response(monkeypatch):
    """Регресс: таймаут urlopen — на каждое чтение сокета, а не на весь ответ. Сайт, отдающий ленту по капле,
    держал обновление новостей минутами. Теперь у ответа общий срок, и такая лента считается недоступной."""
    import news
    clock = iter(range(0, 10_000, 10))                  # каждый вызов часов — +10 с
    monkeypatch.setattr(net.time, "monotonic", lambda: next(clock))

    class Trickle:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"<"               # по байту, бесконечно
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None, context=None: Trickle())
    with pytest.raises(TimeoutError):
        net.fetch("https://slow.example/rss")
    items, failed = news.fetch_news(["https://slow.example/rss"], [], {})
    assert items == [] and failed == ["https://slow.example/rss"]
