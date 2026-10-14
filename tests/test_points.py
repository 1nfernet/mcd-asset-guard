"""积分看护测试：月末推算、7/3/1 分级、跨运行去重。"""
import datetime as dt
import json
from decimal import Decimal

from src.mcp_client import McpClient
from src.services.personal.points import PointsService
from tests.conftest import make_config


def test_as_of_triggers_high_tier(client, notify):
    svc = PointsService(client, notify, as_of="2026-10-28")
    rep = svc.evaluate()
    assert rep.snapshot.available == Decimal("842.9")
    assert rep.snapshot.current_month_expire == Decimal("43.8")
    b = rep.buckets[0]
    assert b.label == "本月"
    assert b.expires_on == dt.date(2026, 10, 31)  # 月末推算
    assert b.days_left == 3
    assert b.tier == "high"
    assert len(rep.alerts) == 1
    assert rep.alerts[0].is_new is True
    # 同一档位再次运行不再重复提醒（state/notify-state.json 去重）
    second = svc.evaluate()
    assert [a.is_new for a in second.alerts] == [False]


def test_real_today_has_no_alert(client, notify):
    svc = PointsService(client, notify)  # 用 fixtures 里的服务端日期 2026-10-14
    rep = svc.evaluate()
    assert rep.today == dt.date(2026, 10, 14)
    assert rep.buckets[0].days_left == 17
    assert rep.buckets[0].tier is None
    assert rep.alerts == []


def test_month_end_tier_progression(client, notify):
    for as_of, expected in [
        ("2026-10-24", "warn"),      # 剩 7 天
        ("2026-10-28", "high"),      # 剩 3 天
        ("2026-10-30", "critical"),  # 剩 1 天
    ]:
        svc = PointsService(client, notify, as_of=as_of)
        assert svc.evaluate().buckets[0].tier == expected


def test_next_month_bucket_included_when_positive(tmp_path, notify):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "now-time-info.json").write_text(
        json.dumps({"json": {"code": 200, "data": {"date": "2026-10-14"}}}),
        encoding="utf-8",
    )
    (fx / "query-my-account.json").write_text(
        json.dumps(
            {
                "json": {
                    "code": 200,
                    "data": {
                        "availablePoint": "100.0",
                        "currentMouthExpirePoint": "10.0",
                        "nextMouthExpirePoint": "12.5",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = make_config(tmp_path)
    cfg.fixtures_dir = fx
    svc = PointsService(McpClient.create(cfg), notify, as_of="2026-10-14")
    rep = svc.evaluate()
    assert [b.label for b in rep.buckets] == ["本月", "下月"]
    assert rep.buckets[1].expires_on == dt.date(2026, 11, 30)
