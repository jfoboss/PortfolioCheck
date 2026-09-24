"""TLS для сайтов (tls.py): корни ОС, а не пути OpenSSL машины сборки."""
import ssl
import sys
import urllib.error
import urllib.request

import pytest

import tls


def test_fallback_has_roots_without_openssl_paths(monkeypatch):
    """Регресс: dmg из CI искал корни в папке setup-python раннера — на Mac пользователя её нет, и все сайты (RSS,
    ISS, ЦБ, OpenRouter) падали с CERTIFICATE_VERIFY_FAILED. Даже без truststore и без путей OpenSSL корни есть."""
    monkeypatch.setitem(sys.modules, "truststore", None)             # import truststore → ImportError
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/cert.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/certs")
    tls.system_context.cache_clear()
    try:
        ctx = tls.system_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
        assert len(ctx.get_ca_certs()) > 50                          # корни certifi
    finally:
        tls.system_context.cache_clear()


def test_truststore_used_when_available():
    tls.system_context.cache_clear()
    ctx = tls.system_context()
    assert type(ctx).__module__.startswith("truststore") and ctx.verify_mode == ssl.CERT_REQUIRED


def _capture(monkeypatch, errors=()):
    seen, errs = [], list(errors)

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<rss/>"

    def fake(req, timeout=None, context=None):
        seen.append(context)
        if errs:
            raise errs.pop(0)
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_news_fetch_uses_system_roots(monkeypatch):
    """Регресс: news._fetch шёл с контекстом по умолчанию (пути OpenSSL) — через него же факторы и календарь ЦБ."""
    import news
    seen = _capture(monkeypatch)
    assert news._fetch("https://example.org/rss") == b"<rss/>"
    assert seen == [tls.system_context()]


def test_news_legacy_retry_only_for_non_verification_errors(monkeypatch):
    """Регресс: urlopen заворачивает SSLError в URLError — повтор с устаревшими шифрами не срабатывал никогда.
    Непрошедшую проверку сертификата не прощаем: без повтора и без отключения проверки."""
    import news
    seen = _capture(monkeypatch, [urllib.error.URLError(ssl.SSLError(1, "unsupported protocol"))])
    assert news._fetch("https://example.org/rss") == b"<rss/>"
    assert seen[1] is tls.system_context(legacy=True) and seen[1].verify_mode == ssl.CERT_REQUIRED
    seen = _capture(monkeypatch, [urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed"))])
    with pytest.raises(urllib.error.URLError):
        news._fetch("https://example.org/rss")
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
    assert https and https[0]._context is tls.system_context()
