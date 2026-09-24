import math
from decimal import Decimal

import portfolio_check as pc


def test_new_money_yield_counts_gold_as_zero(demo_positions, demo_cfg):
    """Честная доходность новых денег: часть пополнений уходит в золото, которое не платит."""
    y, detail = pc.new_money_yield(demo_positions, demo_cfg, {"RUB": Decimal(1)})
    core_only = max(cy for name, share, cy in detail)
    gold = next(d for d in detail if d[0].startswith("Золото"))
    assert gold[2] == 0 and y < core_only


def test_price_growth_skips_classes_without_candles(demo_positions, demo_cfg):
    """Регресс: класс без свечей считался как 0% и занижал среднюю."""
    ch = {p.figi: {"year": 2.0} for p in demo_positions if p.cls != "gold"}
    g, detail, missing = pc.price_growth_target_weighted(demo_positions, demo_cfg, ch)
    assert abs(g - 2.0) < 1e-6 and any("Золото" in m for m in missing)
    gold = next(d for d in detail if d[0].startswith("Золото"))
    assert gold[1] == 0 and math.isnan(gold[2])


def test_snapshot_roundtrip(tmp_path, demo_positions):
    f = tmp_path / "s.json"
    pc.save_snapshot(demo_positions, str(f))
    again = pc.load_snapshot(str(f))
    assert [p.ticker for p in again] == [p.ticker for p in demo_positions]
