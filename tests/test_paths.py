"""Где лежат данные и кэш (paths.py): state/ в папке данных (iCloud), локальный кэш ОС, разовый переезд старого cache/."""
import json
import os
import sys

import ai
import paths


def _w(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_old_cache_split_state_in_icloud_rest_local(tmp_path):
    """Регресс: весь cache/ лежал в iCloud — каждое обновление гоняло в облако мегабайты свечей, на втором Mac —
    конфликтные копии. Переезд: ИИ, оповещения и история времени — в state/ (остаются в iCloud, ни одна сводка не
    теряется и не запрашивается заново), остальное — в локальный кэш (переносится, а не качается заново)."""
    data, cache = tmp_path / "icloud" / "PortfolioCheck", tmp_path / "local-cache"
    old = data / "cache"
    _w(old / "ai_responses.json", {"news": {"at": "2026-09-25T06:45", "fp": "a", "data": {"x": 1}},
                                   "exec_overview": {"at": "2026-09-22T22:01", "fp": "b", "data": {"y": 2}}})
    for name in ("ai_summary.json", "exec_overview.json", "ai_model_checks.json", "ai_rate_block.json",
                 "alerts_state.json", "activity_times.json", "candles_FIGI1.json", "news.json", "screener.json"):
        _w(old / name, {"name": name})
    (old / "news.json.tmp").write_text("обрывок")
    moved = paths.migrate_old_cache(data, cache, ai.split_responses)
    st, ai_dir = data / "state", data / "state" / "ai"
    assert sorted(p.name for p in ai_dir.iterdir()) == [
        "ai_model_checks.json", "ai_rate_block.json", "ai_summary.json", "exec_overview.json",
        "resp_exec_overview.json", "resp_news.json"]
    assert sorted(p.name for p in st.iterdir() if p.is_file()) == ["activity_times.json", "alerts_state.json"]
    assert sorted(p.name for p in cache.iterdir()) == ["candles_FIGI1.json", "news.json", "screener.json"]
    assert moved == {"state": 7, "cache": 3}
    c = ai.ResponseCache(ai_dir)                          # ответы ИИ после переезда — из кэша, без запроса
    assert c.get("news", "a", 6)[0] == {"x": 1} and c.get("exec_overview", "b", 6)[0] == {"y": 2}
    assert not (old / "ai_responses.json").exists() and not (old / "candles_FIGI1.json").exists()


def test_migration_keeps_newer_file(tmp_path):
    """Второй Mac со старой версией мог снова написать в старый cache/ — побеждает более свежий файл."""
    data, cache = tmp_path / "d", tmp_path / "c"
    _w(data / "state" / "ai" / "ai_summary.json", {"v": "новее"})
    _w(data / "cache" / "ai_summary.json", {"v": "старее"})
    os.utime(data / "cache" / "ai_summary.json", (1, 1))
    _w(cache / "news.json", {"v": "старее"}); os.utime(cache / "news.json", (1, 1))
    _w(data / "cache" / "news.json", {"v": "новее"})
    paths.migrate_old_cache(data, cache, ai.split_responses)
    assert json.loads((data / "state" / "ai" / "ai_summary.json").read_text())["v"] == "новее"
    assert json.loads((cache / "news.json").read_text())["v"] == "новее"
    assert not (data / "cache").exists()
    assert paths.migrate_old_cache(data, cache) == {"state": 0, "cache": 0}          # повторно — ничего


def test_ai_responses_do_not_overwrite_each_other(tmp_path):
    """Регресс: один общий ai_responses.json — сводка новостей и сводка экранов, закончившиеся одновременно, каждая
    перезаписывала файл своей копией, и одна из них пропадала. Теперь — по файлу на ключ."""
    a, b = ai.ResponseCache(tmp_path), ai.ResponseCache(tmp_path)    # оба открыты до записи — как два воркера
    a.put("news", "fp1", {"n": 1})
    b.put("exec_overview", "fp2", {"e": 2})
    c = ai.ResponseCache(tmp_path)
    assert c.get("news", "fp1", 6)[0] == {"n": 1} and c.get("exec_overview", "fp2", 6)[0] == {"e": 2}


def test_state_moves_with_data_dir(tmp_path, monkeypatch):
    """Регресс-ловушка: при смене папки данных переносились только конфиг и снапшоты — ИИ-сводки из state/ потерялись бы."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _w(src / "config.yaml", {}); _w(src / "state" / "ai" / "resp_news.json", {"fp": "a"})
    _w(src / "state" / "alerts_state.json", {"active": []}); dst.mkdir()
    paths._migrate(src, dst, move=False)
    assert (dst / "state" / "ai" / "resp_news.json").exists() and (dst / "state" / "alerts_state.json").exists()


def test_os_cache_root_per_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths._os_cache_root().parts[-3:] == ("Library", "Caches", "PortfolioCheck")
    monkeypatch.setattr(sys, "platform", "win32"); monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "L"))
    assert paths._os_cache_root() == tmp_path / "L" / "PortfolioCheck" / "Cache"
    monkeypatch.setattr(sys, "platform", "linux"); monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "X"))
    assert paths._os_cache_root() == tmp_path / "X" / "PortfolioCheck"
