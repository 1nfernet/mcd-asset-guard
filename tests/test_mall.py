"""商城监控与兑换规划（有界背包 DP）测试。"""
import datetime as dt
from decimal import Decimal

from src.models import MallProduct
from src.services.personal.mall import MallService, _plan_rescue, _plan_value


def _mk(name: str, point: str, price_fen: int, spu_id: int = 1) -> MallProduct:
    return MallProduct(
        spu_id=spu_id,
        name=name,
        point=Decimal(point),
        price_fen=price_fen,
        status=2,
        up_time=None,
        down_time=None,
        selling="",
        cat_name="",
        image_url="",
        shop_id=0,
    )


def test_products_from_fixture(client):
    prods = MallService(client).products("2>8")
    assert len(prods) == 3
    assert {p.name for p in prods} == {
        "罗技键鼠套装",
        "Nike Book 2 麦当劳特别联名款",
        "酸黄瓜吧唧礼盒",
    }


def test_plan_rescue_picks_only_affordable_item(client):
    svc = MallService(client)
    prods = svc.products("2>8")
    plan = svc.plan(prods, Decimal("842.9"), Decimal("43.8"), "rescue")
    assert [p.name for p in plan.items] == ["酸黄瓜吧唧礼盒"]
    assert plan.total_points == Decimal("800.0")
    assert plan.rescued == Decimal("43.8")
    assert plan.leftover == Decimal("0.0")
    assert plan.remaining == Decimal("42.9")
    assert "已全部救回" in plan.note


def test_plan_rescue_without_expiring_is_empty(client):
    svc = MallService(client)
    plan = svc.plan(svc.products("2>8"), Decimal("842.9"), Decimal("0.0"))
    assert plan.items == []


def test_plan_rescue_partial_when_no_combo_reaches_bucket():
    items = [_mk("小件", "10.0", 0)]
    plan = _plan_rescue(items, Decimal("50.0"), Decimal("43.8"))
    # 最大可消耗 10 < 43.8：只能救回 10
    total = sum(p.point for p in plan)
    assert total == Decimal("10.0")


def test_rescue_prefers_closest_to_bucket():
    # 100 / 200 两个可行组合都能覆盖 43.8，应选更接近临期桶的 100
    items = [_mk("A", "100.0", 0), _mk("B", "200.0", 0)]
    chosen = _plan_rescue(items, Decimal("300.0"), Decimal("43.8"))
    assert [p.name for p in chosen] == ["A"]


def test_rescue_caps_at_available_points():
    items = [_mk("A", "100.0", 0), _mk("B", "100.0", 0)]
    chosen = _plan_rescue(items, Decimal("150.0"), Decimal("300.0"))
    assert sum(p.point for p in chosen) == Decimal("100.0")  # 只能取一件


def test_plan_value_maximizes_price():
    items = [_mk("A", "100.0", 5000), _mk("B", "200.0", 3000)]
    assert [p.name for p in _plan_value(items, Decimal("250.0"))] == ["A"]
    assert [p.name for p in _plan_value(items, Decimal("300.0"))] == ["A", "B"]


def test_gaps(client):
    svc = MallService(client)
    gaps = dict((p.name, g) for p, g in svc.gaps(svc.products("2>8"), Decimal("842.9")))
    assert gaps["罗技键鼠套装"] == Decimal("5823.1")
    assert gaps["Nike Book 2 麦当劳特别联名款"] == Decimal("8045.1")


def test_ending_soon_and_opening_soon(client):
    svc = MallService(client)
    prods = svc.products("2>8")
    # 8-20 这天：酸黄瓜（下架 8-14）早已结束，只有罗技（下架 8-21）在 7 天窗口内
    soon = {p.name for p in svc.ending_soon(prods, dt.datetime(2026, 8, 20, 12, 0), days=7)}
    assert soon == {"罗技键鼠套装"}
    # 8-12 这天：只剩酸黄瓜（下架 8-14）在窗口内
    soon2 = {p.name for p in svc.ending_soon(prods, dt.datetime(2026, 8, 12, 12, 0), days=7)}
    assert soon2 == {"酸黄瓜吧唧礼盒"}
    # 开抢提醒：8-21 14:00 开抢，12:00 时距离不足 48 小时
    opening = {p.name for p in svc.opening_soon(prods, dt.datetime(2026, 8, 21, 12, 0), hours=48)}
    assert opening == {"罗技键鼠套装"}
    # 已结束的窗口不算开抢
    assert svc.opening_soon(prods, dt.datetime(2026, 10, 14, 12, 0), hours=48) == []
