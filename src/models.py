"""领域模型（纯数据 + 纯函数，禁止网络/IO）。

单位约定（对齐 2026-10-14 真实接口探测结论）：
- 积分：Decimal，粒度 0.1（接口返回字符串小数，如 availablePoint="842.9"）。
- 金额：整数「分」。真实接口单位混乱——mall 的 price 是元字符串（"198"）、
  party session 的 price 是分整数（19800）、calculate-price 返回分——models 层
  统一为整数分，杜绝浮点误差。
- 字段名照抄真实接口：currentMouthExpirePoint 的 "Mouth" 是官方拼写，禁止"纠正"。
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------- 单位换算


def parse_points(v) -> Decimal:
    """积分字符串/数字 -> Decimal（0.1 粒度）。"""
    if v is None or v == "":
        return Decimal("0.0")
    try:
        d = Decimal(str(v))
    except InvalidOperation as e:
        raise ValueError(f"积分字段不是数字: {v!r}") from e
    return d.quantize(Decimal("0.1"))


def yuan_to_fen(v) -> int:
    """元（字符串/数字）-> 整数分。"""
    if v is None or v == "":
        return 0
    return int((Decimal(str(v)) * 100).quantize(Decimal("1")))


def fen(v) -> int:
    """已是分单位的字段 -> int。"""
    return int(v) if v is not None else 0


def fen_display(f: int) -> str:
    return f"¥{f / 100:.2f}"


def parse_dt(s):
    """接口时间字符串 -> datetime；解析失败返回 None（不抛异常）。"""
    if not s:
        return None
    try:
        value = str(s)
    except (TypeError, ValueError):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def month_end(d: dt.date) -> dt.date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def next_month_end(d: dt.date) -> dt.date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return dt.date(y, m, calendar.monthrange(y, m)[1])


# ---------------------------------------------------------------- 临期分级

TIER_LABEL = {"critical": "紧急", "high": "高", "warn": "提醒"}


def tier_for(days_left: int) -> str | None:
    """7/3/1 天分级：<=1 紧急 / <=3 高 / <=7 提醒。"""
    if days_left <= 1:
        return "critical"
    if days_left <= 3:
        return "high"
    if days_left <= 7:
        return "warn"
    return None


# ---------------------------------------------------------------- 积分账户


@dataclass
class AccountSnapshot:
    account_id: str
    currency: str
    available: Decimal
    current_month_expire: Decimal
    next_month_expire: Decimal
    last_month_expire: Decimal
    expired: Decimal
    frozen: Decimal
    used: Decimal
    accumulative: Decimal
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, d: dict) -> "AccountSnapshot":
        # ⚠️ 字段名照抄真实接口（currentMouthExpirePoint 官方拼写就是 Mouth）
        return cls(
            account_id=str(d.get("accountId", "")),
            currency=str(d.get("currency", "")),
            available=parse_points(d.get("availablePoint", "0")),
            current_month_expire=parse_points(d.get("currentMouthExpirePoint", "0")),
            next_month_expire=parse_points(d.get("nextMouthExpirePoint", "0")),
            last_month_expire=parse_points(d.get("lastMouthExpirePoint", "0")),
            expired=parse_points(d.get("expiredPoint", "0")),
            frozen=parse_points(d.get("frozenPoint", "0")),
            used=parse_points(d.get("usedPoint", "0")),
            accumulative=parse_points(d.get("accumulativePoint", "0")),
            raw=d,
        )


@dataclass
class ExpiryBucket:
    """月度临期桶。真实接口只给月度粒度，到期日按月末推算。"""

    label: str
    points: Decimal
    expires_on: dt.date

    def days_left(self, today: dt.date) -> int:
        return (self.expires_on - today).days

    def tier(self, today: dt.date) -> str | None:
        return tier_for(self.days_left(today))


# ---------------------------------------------------------------- 商城


@dataclass
class MallProduct:
    spu_id: int
    name: str
    point: Decimal
    price_fen: int
    status: int
    up_time: dt.datetime | None
    down_time: dt.datetime | None
    selling: str
    cat_name: str
    image_url: str
    shop_id: int

    STATUS_LABEL = {1: "仓库", 2: "上架", 3: "售罄", 4: "下架", 5: "预热"}

    @classmethod
    def from_payload(cls, d: dict) -> "MallProduct":
        return cls(
            spu_id=int(d.get("spuId", 0)),
            name=str(d.get("spuName", "")),
            point=parse_points(d.get("point", "0")),
            price_fen=yuan_to_fen(d.get("price", "0")),
            status=int(d.get("status", 0)),
            up_time=parse_dt(d.get("upTime")),
            down_time=parse_dt(d.get("downTime")),
            selling=str(d.get("selling", "")),
            cat_name=str(d.get("catName", "")),
            image_url=str(d.get("spuImage", "")),
            shop_id=int(d.get("shopId", 0)),
        )

    @property
    def status_label(self) -> str:
        return self.STATUS_LABEL.get(self.status, str(self.status))

    @property
    def is_on_sale(self) -> bool:
        return self.status == 2

    @property
    def is_presale(self) -> bool:
        return self.status == 5

    def window_open(self, now: dt.datetime) -> bool:
        if self.up_time and now < self.up_time:
            return False
        if self.down_time and now > self.down_time:
            return False
        return True

    def ending_within(self, now: dt.datetime, days: int = 7) -> bool:
        return bool(
            self.down_time
            and now <= self.down_time
            and self.down_time <= now + dt.timedelta(days=days)
        )

    def opening_within(self, now: dt.datetime, hours: int = 48) -> bool:
        """开抢提醒：预热状态或上架时间在未来 hours 小时内。"""
        if not self.up_time:
            return False
        return now <= self.up_time <= now + dt.timedelta(hours=hours)


# ---------------------------------------------------------------- 派对


@dataclass
class PartyCity:
    code: int
    name: str
    latitude: float
    longitude: float

    @classmethod
    def from_payload(cls, d: dict) -> "PartyCity":
        return cls(
            code=int(d.get("code", 0)),
            name=str(d.get("name", "")),
            latitude=float(d.get("latitude", 0.0)),
            longitude=float(d.get("longitude", 0.0)),
        )


@dataclass
class PartyStore:
    code: str
    name: str
    short_name: str
    address: str
    business_status: int
    online: bool

    @classmethod
    def from_payload(cls, d: dict) -> "PartyStore":
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            short_name=str(d.get("shortName", "")),
            address=str(d.get("address", "")),
            business_status=int(d.get("businessStatus", 0)),
            online=bool(d.get("onlineBusinessStatus", False)),
        )


@dataclass
class PartySession:
    id: int
    date: str
    time_start: str
    time_end: str
    party_min: int
    party_max: int
    left_num: int
    price_fen: int
    store_code: str

    @classmethod
    def from_payload(cls, d: dict, date_str: str, store_code: str) -> "PartySession":
        # ⚠️ 派对场次 price 接口直接返回分（19800 = ¥198），不做 ×100
        return cls(
            id=int(d.get("id", 0)),
            date=date_str,
            time_start=str(d.get("timeStart", "")),
            time_end=str(d.get("timeEnd", "")),
            party_min=int(d.get("partyMin", 0)),
            party_max=int(d.get("partyMax", 0)),
            left_num=int(d.get("leftNum", 0)),
            price_fen=fen(d.get("price", 0)),
            store_code=store_code,
        )

    @property
    def available(self) -> bool:
        return self.left_num > 0
