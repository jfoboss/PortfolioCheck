"""
tls.py — TLS для сайтов (RSS, MOEX ISS, ЦБ, OpenRouter): корни доверия из системного хранилища ОС.

Почему не ssl.create_default_context(): он ищет корни по путям OpenSSL, зашитым в Python при сборке. В собранной
программе это пути машины сборки: у dmg из CI — папка setup-python на раннере GitHub, которой на Mac пользователя
нет, и все сайты падали с CERTIFICATE_VERIFY_FAILED (сборка через build_mac.sh на своём Mac работала — пути
совпадали). truststore проверяет сертификаты средствами ОС: Keychain на macOS, хранилище Windows, системные
корни на Linux — как браузер. Запасной вариант (нет truststore) — корни certifi поверх стандартных путей.
T-Invest сюда не относится: у него свой бандл с корнем Минцифры (portfolio_check._setup_grpc_roots).
"""
from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=None)
def system_context(legacy: bool = False) -> ssl.SSLContext:
    """Контекст с проверкой сертификата и имени. legacy — для сайтов с устаревшими шифрами (проверка остаётся)."""
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
    if legacy:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


def https_handler():
    """urllib-обработчик HTTPS с системными корнями — для build_opener (ИИ, прокси)."""
    import urllib.request
    return urllib.request.HTTPSHandler(context=system_context())
