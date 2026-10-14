"""领域模型与单位换算测试。"""
import datetime as dt
from decimal import Decimal

from src.models import (
    AccountSnapshot,
    MallProduct,
    PartySession,
    fen_display,
    month_end,
    next_month_end,
    parse_dt,
    parse_points,
    tier_for,
    yuan_to_fen,
)


def test_parse_dt_invalid_unicode_empty_and_unstringable_values():
    class Unstringable:
        def __str__(self):
            raise TypeError("invalid API value")

    assert parse_dt("") is None
    assert parse_dt("２０２６-１０-１４") is None
    assert parse_dt(Unstringable()) is None


def test_parse_points_keeps_decimal():
    # 真实接口返回字符串小数："842.9"
    assert parse_points("842.9") == Decimal("842.9")
    assert parse_points("43.8") == Decimal("43.8")
    assert parse_points("0") == Decimal("0.0")
    assert parse_points(None) == Decimal("0.0")


def test_yuan_to_fen():
    assert yuan_to_fen("198") == 19800
    assert yuan_to_fen("0") == 0
    assert yuan_to_fen("9.9") == 990


def test_fen_display():
    assert fen_display(19800) == "¥198.00"


def test_month_end():
    assert month_end(dt.date(2026, 10, 14)) == dt.date(2026, 10, 31)
    assert next_month_end(dt.date(2026, 10, 14)) == dt.date(2026, 11, 30)
    assert next_month_end(dt.date(2026, 12, 5)) == dt.date(2027, 1, 31)


def test_tier_boundaries():
    assert tier_for(0) == "critical"
    assert tier_for(1) == "critical"
    assert tier_for(2) == "high"
    assert tier_for(3) == "high"
    assert tier_for(4) == "warn"
    assert tier_for(7) == "warn"
    assert tier_for(8) is None


def test_account_snapshot_real_field_names():
    payload = {
        "availablePoint": "842.9",
        "accountId": "ACC-1",
        "currency": "麦享会积分",
        "currentMouthExpirePoint": "43.8",
        "nextMouthExpirePoint": "12.5",
        "lastMouthExpirePoint": "0",
        "expiredPoint": "2409.6",
        "frozenPoint": "0",
        "usedPoint": "4305",
        "accumulativePoint": "7557.5",
    }
    s = AccountSnapshot.from_payload(payload)
    assert s.available == Decimal("842.9")
    # currentMouthExpirePoint 的 "Mouth" 是官方拼写，必须能解析
    assert s.current_month_expire == Decimal("43.8")
    assert s.next_month_expire == Decimal("12.5")
    assert s.expired == Decimal("2409.6")


def test_mall_product_units():
    p = MallProduct.from_payload(
        {
            "spuName": "酸黄瓜吧唧礼盒",
            "spuId": 17643,
            "point": "800",
            "price": "0",
            "status": 2,
            "upTime": "2026-08-14 14:00:00",
            "downTime": "2026-08-14 23:59:59",
            "catName": "玩具",
        }
    )
    assert p.point == Decimal("800.0")
    assert p.price_fen == 0
    assert p.is_on_sale and not p.is_presale
    assert p.status_label == "上架"
    now = dt.datetime(2026, 10, 14, 12, 0)
    assert p.window_open(now) is False  # 兑换窗口已过


def test_party_session_price_is_yuan_to_fen_agnostic():
    # 派对场次 price 接口直接返回分（19800 = ¥198），不能再乘 100
    s = PartySession.from_payload(
        {
            "id": 35557157,
            "timeStart": "17:00",
            "timeEnd": "19:00",
            "partyMin": 5,
            "partyMax": 12,
            "price": 19800,
            "leftNum": 12,
        },
        "2026-10-17",
        "1410056",
    )
    assert s.price_fen == 19800
    assert fen_display(s.price_fen) == "¥198.00"
    assert s.available is True
