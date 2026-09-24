"""Окно настроек помещается на экран ноутбука: разделы списком слева, страницы не шире ~700 px."""
import settings_dialog as SD


def test_settings_fit_laptop_screen(window, qapp):
    """Регресс: 12 вкладок в строке (на macOS строка не прокручивается) и длинные подписи флажков распирали окно
    шире экрана MacBook 14″."""
    f = qapp.font(); old = qapp.font(); f.setPixelSize(13); qapp.setFont(f)          # шрифт как на macOS
    try:
        window.settings_win = None
        window.open_settings(); d = window.settings_win
        assert d.tabs.tabBar().isHidden() and d.nav.count() == d.tabs.count() == 12
        assert d.minimumSizeHint().width() < 600
        from PySide6.QtWidgets import QWidget
        wide = {d.tabs.tabText(i): d.tabs.widget(i).widget().minimumSizeHint().width() for i in range(d.tabs.count())}
        culprits = sorted(((c.minimumSizeHint().width(), type(c).__name__, (getattr(c, "text", None) or (lambda: ""))()[:40])
                           for i in range(d.tabs.count()) for c in d.tabs.widget(i).widget().findChildren(QWidget)
                           if type(c).__name__ != "QWidget"), reverse=True)[:6]
        assert max(wide.values()) <= 700, (wide, culprits)
        d.nav.setCurrentRow(9); assert d.tabs.tabText(d.tabs.currentIndex()) == "Оповещения"
        d.tabs.setCurrentIndex(11); assert d.nav.currentRow() == 11                # палитра и «Доступ» открывают раздел
        d.close()
    finally:
        qapp.setFont(old)


def test_short_feed_errors():
    assert SD.short_error("URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed") \
        == "сертификат не прошёл проверку"
    assert SD.short_error("HTTPError: HTTP Error 403: Forbidden") == "доступ запрещён (403)"
    assert len(SD.short_error("x" * 200)) <= 31


def test_feeds_check_runs_in_background(window, qapp, monkeypatch):
    """Регресс: «Проверить» в Настройки → Новости проверял ленты в UI-потоке через processEvents — окно замирало
    на медленных лентах (правило 4). Теперь — FeedCheckWorker, отметки по мере ответов."""
    import news as NEWS
    monkeypatch.setattr(NEWS, "check_feed", lambda url, *a: (url.endswith("ok"), "новостей: 5" if url.endswith("ok") else "HTTPError: 403"))
    window.settings_win = None
    window.open_settings(); d = window.settings_win
    d.t_feeds.setRowCount(0)
    for u in ("https://a.example/ok", "https://b.example/bad"):
        r = d.t_feeds.rowCount(); d.t_feeds.insertRow(r)
        d.t_feeds.setItem(r, 0, SD._cell(u)); d.t_feeds.setItem(r, 1, SD._cell("", editable=False))
    d._feeds_check()
    assert d._feeds_worker.wait(5000)
    qapp.processEvents()
    assert d.t_feeds.item(0, 1).text().startswith("✓") and d.t_feeds.item(1, 1).text() == "✗ доступ запрещён (403)"
    d.close()
