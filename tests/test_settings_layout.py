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
        wide = {d.tabs.tabText(i): d.tabs.widget(i).widget().minimumSizeHint().width() for i in range(d.tabs.count())}
        assert max(wide.values()) <= 700, wide
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
