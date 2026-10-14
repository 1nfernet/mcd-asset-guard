"""派对空位：五级链路下钻 + watch 常驻轮询 + 快照差异告警。

真实链路（2026-10-14 实测全程走通）：
  mall-points-products(catRuleIds=1>6>21)  -> spuId 锚点
  -> query-party-city                       -> {code, latitude, longitude, name}
  -> query-party-store                      -> {code(=storeCode), businessStatus, ...}
  -> query-party-store-date                 -> {date, ...}
  -> query-party-store-session              -> {id, timeStart, timeEnd, partyMin,
                                                partyMax, leftNum, price(分)}

leftNum 就是剩余空位。watch 模式只高频轮询最底层的场次，城市/门店/日期逐级缓存，
轮询间隔可配；空位告警基于快照 diff：新场次 / 0->开放 / 空位增加 才通知，避免刷屏。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.mcp_client import McpClient
from src.models import MallProduct, PartyCity, PartySession, PartyStore, fen_display
from src.report.render import banner, kv, table
from src.state import JsonStore, TtlCache

log = logging.getLogger("mcd.party")

PARTY_CATS = "1>6>21"
TTL_SLOW = 12 * 3600  # 城市/门店慢变，缓存 12h
TTL_DATES = 3600      # 可预约日期缓存 1h


@dataclass
class WatchEvent:
    kind: str      # new / opened / increased
    spu_id: int
    store_code: str
    date: str
    session: PartySession
    detail: str


class PartyService:
    def __init__(self, client: McpClient, cache: TtlCache, snapshot: JsonStore):
        self.client = client
        self.cache = cache
        self.snapshot = snapshot

    def party_products(self) -> list[MallProduct]:
        data = self.client.call("mall-points-products", {"catRuleIds": PARTY_CATS}) or []
        return [MallProduct.from_payload(d) for d in data]

    def cities(self, spu_id: int) -> list[PartyCity]:
        key = f"cities|{spu_id}"
        cached = self.cache.get(key, TTL_SLOW)
        if cached is None:
            cached = self.client.call("query-party-city", {"spuId": spu_id}) or []
            self.cache.set(key, cached)
        return [PartyCity.from_payload(d) for d in cached]

    def stores(self, spu_id: int, city: PartyCity) -> list[PartyStore]:
        key = f"stores|{spu_id}|{city.code}"
        cached = self.cache.get(key, TTL_SLOW)
        if cached is None:
            cached = (
                self.client.call(
                    "query-party-store",
                    {
                        "code": str(city.code),
                        "latitude": city.latitude,
                        "longitude": city.longitude,
                        "spuId": spu_id,
                    },
                )
                or []
            )
            self.cache.set(key, cached)
        return [PartyStore.from_payload(d) for d in cached]

    def dates(self, spu_id: int, store_code: str) -> list[str]:
        key = f"dates|{spu_id}|{store_code}"
        cached = self.cache.get(key, TTL_DATES)
        if cached is None:
            data = self.client.call(
                "query-party-store-date",
                {"spuId": spu_id, "storeCode": store_code},
            ) or []
            cached = [str(d.get("date", "")) for d in data if d.get("date")]
            self.cache.set(key, cached)
        return cached

    def sessions(self, spu_id: int, store_code: str, date_str: str) -> list[PartySession]:
        data = (
            self.client.call(
                "query-party-store-session",
                {"spuId": spu_id, "storeCode": store_code, "dateStr": date_str},
            )
            or []
        )
        return [PartySession.from_payload(d, date_str, store_code) for d in data]

    # ------------------------------------------------------------ watch

    def scan_once(
        self, spu_id: int, store_code: str, dates: list[str]
    ) -> list[WatchEvent]:
        events: list[WatchEvent] = []
        for d in dates:
            try:
                sess = self.sessions(spu_id, store_code, d)
            except Exception as e:  # 单日失败不影响整轮
                log.warning("查询场次失败 date=%s: %s", d, e)
                continue
            for s in sess:
                key = f"{spu_id}|{store_code}|{d}|{s.id}"
                old = self.snapshot.get(key)
                if s.left_num > 0:
                    if old is None:
                        events.append(
                            WatchEvent("new", spu_id, store_code, d, s, "新增可预约场次")
                        )
                    elif old == 0:
                        events.append(
                            WatchEvent("opened", spu_id, store_code, d, s, "空位重新开放")
                        )
                    elif s.left_num > old:
                        events.append(
                            WatchEvent(
                                "increased",
                                spu_id,
                                store_code,
                                d,
                                s,
                                f"空位 {old} -> {s.left_num}",
                            )
                        )
                self.snapshot.set(key, s.left_num)
        return events

    def watch(self, spu_id, store_code, dates, interval=60.0, emit=None, max_rounds=None):
        """常驻轮询；emit(WatchEvent) 负责通知。Ctrl-C 退出。"""
        emit = emit or (lambda e: print(f"[watch] {e.kind} {e.date} {e.detail}"))
        rnd = 0
        while True:
            rnd += 1
            log.info("watch 第 %d 轮 spu=%s store=%s dates=%s", rnd, spu_id, store_code, dates)
            for e in self.scan_once(spu_id, store_code, dates):
                emit(e)
            if max_rounds and rnd >= max_rounds:
                return
            time.sleep(interval)


def render_party(
    product_name: str, spu_id: int, city_name: str, store: PartyStore,
    date_str: str, sessions: list[PartySession],
) -> str:
    lines = [banner("派对空位")]
    lines += kv(
        [
            ("商品", f"{product_name}（spu {spu_id}）"),
            ("城市", city_name),
            ("门店", f"{store.name}（{store.code}）"),
            ("日期", date_str),
        ]
    )
    lines.append("")
    rows = [
        [
            f"{s.time_start}-{s.time_end}",
            f"{s.party_min}-{s.party_max} 人",
            fen_display(s.price_fen),
            s.left_num,
            "可预约" if s.available else "已满",
        ]
        for s in sorted(sessions, key=lambda x: x.time_start)
    ]
    if rows:
        lines += table(["时段", "成团人数", "单价/场", "剩余空位", "状态"], rows)
    else:
        lines.append("（该日无可预约场次）")
    return "\n".join(lines)
