"""派对五级链路与 watch 快照差异测试。"""
from src.services.personal.party import PartyService
from src.state import JsonStore, TtlCache


def _svc(client, tmp_path) -> PartyService:
    return PartyService(
        client,
        TtlCache(JsonStore(tmp_path / "party-cache.json")),
        JsonStore(tmp_path / "party-snapshot.json"),
    )


def test_party_chain_full(client, tmp_path):
    svc = _svc(client, tmp_path)

    prods = svc.party_products()
    assert any(p.spu_id == 6971 for p in prods)

    cities = svc.cities(6971)
    assert [c.name for c in cities] == ["福州市", "厦门市", "泉州市", "龙岩市"]

    xiamen = next(c for c in cities if c.name == "厦门市")
    stores = svc.stores(6971, xiamen)
    assert stores[0].code == "1410056"
    assert stores[0].online is True

    dates = svc.dates(6971, "1410056")
    assert len(dates) == 48
    assert dates[0] == "2026-10-14"

    sessions = svc.sessions(6971, "1410056", "2026-10-17")
    assert len(sessions) == 3
    assert {s.time_start for s in sessions} == {"12:15", "17:00", "19:30"}
    assert all(s.left_num == 12 and s.price_fen == 19800 and s.party_min == 5 for s in sessions)


def test_watch_scan_diff(client, tmp_path):
    svc = _svc(client, tmp_path)
    svc.dates(6971, "1410056")  # 预热缓存

    first = svc.scan_once(6971, "1410056", ["2026-10-17"])
    assert len(first) == 3
    assert all(e.kind == "new" for e in first)
    assert all(e.session.left_num == 12 for e in first)

    # 快照无变化 -> 不重复告警
    assert svc.scan_once(6971, "1410056", ["2026-10-17"]) == []

    # 模拟某场次被抢空后重新开放（0 -> 12）
    svc.snapshot.set("6971|1410056|2026-10-17|35557157", 0)
    events = svc.scan_once(6971, "1410056", ["2026-10-17"])
    assert [e.kind for e in events] == ["opened"]
    assert events[0].session.id == 35557157


def test_cache_avoids_second_fetch(client, tmp_path):
    svc = _svc(client, tmp_path)
    svc.cities(6971)
    assert svc.cache.get("cities|6971", 3600) is not None
