"""积分看护：月度临期桶 -> 月末推算 -> 7/3/1 天分级提醒。

真实接口只提供月度粒度（currentMouthExpirePoint / nextMouthExpirePoint），
没有具体到期日，因此 days_left 按本月最后一天推算；
--as-of 可模拟日期，用于演示「3 天后过期」的分级提醒效果。
所有算术（月末、天数、分级）全部本地完成，MCP 只取数。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from src.mcp_client import McpClient
from src.models import (
    AccountSnapshot,
    ExpiryBucket,
    TIER_LABEL,
    month_end,
    next_month_end,
)
from src.notify import Notify
from src.report.render import banner, kv


@dataclass
class BucketView:
    label: str
    points: object
    expires_on: dt.date
    days_left: int
    tier: str | None


@dataclass
class Alert:
    level: str
    title: str
    lines: list[str] = field(default_factory=list)
    is_new: bool = True  # False = 同档位此前已提醒过（去重命中）


@dataclass
class PointsReport:
    snapshot: AccountSnapshot
    today: dt.date
    buckets: list[BucketView] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


class PointsService:
    def __init__(self, client: McpClient, notify: Notify, as_of: str | None = None):
        self.client = client
        self.notify = notify
        self.as_of = as_of

    def server_today(self) -> dt.date:
        """基准日期：--as-of 优先，否则用服务端时间（避免本地时钟偏差）。"""
        if self.as_of:
            return dt.date.fromisoformat(self.as_of)
        data = self.client.call("now-time-info") or {}
        d = data.get("date") or str(data.get("formatted", ""))[:10]
        if not d:
            raise ValueError("now-time-info 未返回日期字段")
        return dt.date.fromisoformat(str(d)[:10])

    def snapshot(self) -> AccountSnapshot:
        data = self.client.call("query-my-account") or {}
        return AccountSnapshot.from_payload(data)

    def buckets(self, snap: AccountSnapshot, today: dt.date) -> list[ExpiryBucket]:
        out = [ExpiryBucket("本月", snap.current_month_expire, month_end(today))]
        if snap.next_month_expire > 0:
            out.append(
                ExpiryBucket("下月", snap.next_month_expire, next_month_end(today))
            )
        return out

    def evaluate(self) -> PointsReport:
        """汇总积分状态与临期分级。

        去重规则：同一桶同一档位只提醒一次（状态落在 state/notify-state.json）。
        这里只判定 is_new 并标记，不直接打印——由 CLI 在报表之后统一输出，
        保证终端里「先看数据、再看提醒」的阅读顺序。
        """
        snap = self.snapshot()
        today = self.server_today()
        rep = PointsReport(snapshot=snap, today=today)
        for b in self.buckets(snap, today):
            view = BucketView(
                b.label, b.points, b.expires_on, b.days_left(today), b.tier(today)
            )
            rep.buckets.append(view)
            if view.tier and b.points > 0:
                title = (
                    f"{b.label}将过期 {b.points} 积分："
                    f"{view.days_left} 天后（{b.expires_on.isoformat()}）清零"
                )
                lines = [
                    "建议：python -m src.main personal mall --plan  查看可救回的兑换组合"
                ]
                is_new = True
                if self.notify.state and not self.notify.state.should_emit(
                    f"points:{b.label}", view.tier
                ):
                    is_new = False
                rep.alerts.append(Alert(view.tier, title, lines, is_new))
        return rep


def render_points(rep: PointsReport) -> str:
    s = rep.snapshot
    lines = [banner("积分看护")]
    lines += kv(
        [
            ("账户", s.account_id or "-"),
            ("币种", s.currency or "-"),
            ("可用积分", str(s.available)),
            ("本月将过期", str(s.current_month_expire)),
            ("下月将过期", str(s.next_month_expire)),
            ("上月已过期", str(s.last_month_expire)),
            ("累计已过期", str(s.expired)),
            ("累计获得 / 已使用", f"{s.accumulative} / {s.used}"),
        ]
    )
    lines.append("")
    lines.append(f"基准日期 {rep.today}（服务端时间或 --as-of 推算）")
    for v in rep.buckets:
        tier_txt = f" · {TIER_LABEL[v.tier]}档" if v.tier else ""
        lines.append(
            f"  {v.label}: {v.points} 积分 -> {v.expires_on} 到期，剩 {v.days_left} 天{tier_txt}"
        )
    lines.append("")
    if rep.alerts:
        for a in rep.alerts:
            tag = "" if a.is_new else "（此前已提醒过，本次不重复）"
            lines.append(f"!! [{a.level}] {a.title}{tag}")
            lines += [f"   {ln}" for ln in a.lines]
    else:
        lines.append("（当前没有 7/3/1 天内的临期提醒）")
    return "\n".join(lines)
