"""JSON-кэши (jsoncache.py): битый файл не роняет загрузку, запись атомарная, свежесть по времени файла."""
import os
import time

import jsoncache as JC
import news as NEWS


def test_read_write_fresh(tmp_path):
    f = tmp_path / "sub" / "x.json"
    assert JC.read(f, []) == [] and not JC.fresh(f, 60)
    JC.write(f, {"а": 1})
    assert JC.read(f) == {"а": 1} and JC.fresh(f, 60) and not (tmp_path / "sub" / "x.json.tmp").exists()
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert not JC.fresh(f, 60)


def test_corrupt_news_cache_is_empty_feed(tmp_path):
    """Регресс: news.load_cache читал JSON без try — обрезанный news.json ронял загрузку при старте."""
    (tmp_path / "news.json").write_text('[{"title": "обрез', encoding="utf-8")
    assert NEWS.load_cache(tmp_path) == []
    (tmp_path / "news.json").write_text('[{"title": "ок", "link": "", "source": "s", "published": null}, {"bad": 1}]',
                                        encoding="utf-8")
    assert [i.title for i in NEWS.load_cache(tmp_path)] == ["ок"]
