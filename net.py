"""
net.py — сеть к сайтам (RSS, MOEX ISS, ЦБ, OpenRouter и другие ИИ-провайдеры): TLS с корнями ОС, прокси, заголовки.
T-Invest сюда не относится: gRPC со своим бандлом (корень Минцифры) — portfolio_check._setup_grpc_roots.

Почему не ssl.create_default_context(): он ищет корни по путям OpenSSL, зашитым в Python при сборке. В собранной
программе это пути машины сборки: у dmg из CI — папка setup-python на раннере GitHub, которой на Mac пользователя
нет, и все сайты падали с CERTIFICATE_VERIFY_FAILED (сборка через build_mac.sh на своём Mac работала — пути
совпадали). truststore проверяет сертификаты средствами ОС: Keychain на macOS, хранилище Windows, системные
корни на Linux — как браузер. Запасной вариант (нет truststore) — корни certifi поверх стандартных путей.
Любой новый запрос к сайту — только через fetch / opener / get_json отсюда.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path


def mincifry_roots(bundle) -> bytes:
    """Сертификаты Минцифры (Russian Trusted Root / Sub CA) из бандла T-Invest в DER — b"", если бандла нет.
    Бандл собирает программа (Настройки → Доступ → «Собрать бандл»): корни grpcio + цепочка сервера T-Invest.
    Для сайтов берём только сертификаты Минцифры — остальные корни и так есть в хранилище ОС."""
    if bundle is None or not Path(bundle).exists():
        return b""
    tmp = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        tmp.load_verify_locations(cafile=str(bundle))
    except (OSError, ssl.SSLError):
        return b""
    info, der = tmp.get_ca_certs(), tmp.get_ca_certs(binary_form=True)
    return b"".join(d for i, d in zip(info, der) if "Russian Trusted" in str(i.get("subject", "")))


def _bundle():
    try:
        import portfolio_check as pc
        return pc._bundle_path()
    except Exception:  # noqa: BLE001
        return None


def system_context(legacy: bool = False) -> ssl.SSLContext:
    """Контекст с проверкой сертификата и имени: корни ОС + корень Минцифры из бандла T-Invest (если собран) —
    для сайтов на российских сертификатах; в систему ничего не устанавливается. legacy — для сайтов с
    устаревшими шифрами (проверка остаётся). Кэш — по времени изменения бандла: новый бандл подхватывается сразу."""
    b = _bundle()
    try:
        stamp = (str(b), b.stat().st_mtime) if b is not None else ("", 0.0)
    except OSError:
        stamp = ("", 0.0)
    return _context(legacy, *stamp)


@lru_cache(maxsize=8)
def _context(legacy: bool, bundle: str, _mtime: float) -> ssl.SSLContext:
    try:
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 — нет пакета или ОС не поддерживается
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:  # noqa: BLE001
            pass
    extra = mincifry_roots(bundle or None)
    if extra:
        ctx.load_verify_locations(cadata=extra)       # truststore: дополнительные якоря поверх хранилища ОС
    if legacy:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


def https_handler():
    """urllib-обработчик HTTPS с системными корнями — для build_opener (ИИ, прокси)."""
    return urllib.request.HTTPSHandler(context=system_context())


def opener(proxy: str = ""):
    """urllib-opener: системные корни + прокси из настроек ИИ (если задан)."""
    handlers = [https_handler()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def get_json(url: str, headers: dict | None = None, proxy: str = "", timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "PortfolioCheck", **(headers or {})})
    with opener(proxy).open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


UA_DESKTOP = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/18.0 Safari/605.1.15")
HEADERS = {"User-Agent": UA_DESKTOP,
           "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
           "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}


def fetch(url: str, timeout: int = 12) -> bytes:
    """Сайты (RSS, ISS, ЦБ) — с корнями из системного хранилища ОС, не с нашим бандлом для T-Invest.
    При 403 повторяем с заголовками обычного браузера; при ошибке TLS, кроме непрошедшей проверки сертификата, —
    с устаревшими шифрами (проверка сертификата остаётся: подмену сертификата не прощаем)."""
    def once(headers: dict, ctx) -> bytes:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()

    try:
        return once(HEADERS, system_context())
    except urllib.error.HTTPError as e:
        if e.code not in (403, 406, 429):
            raise
        h = {**HEADERS, "Referer": re.match(r"https?://[^/]+", url).group(0) + "/",
             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        return once(h, system_context())
    except urllib.error.URLError as e:
        # urlopen заворачивает ssl.SSLError в URLError — раньше здесь ловился голый SSLError, и повтор не срабатывал
        if not isinstance(e.reason, ssl.SSLError) or isinstance(e.reason, ssl.SSLCertVerificationError):
            raise
        return once(HEADERS, system_context(legacy=True))
