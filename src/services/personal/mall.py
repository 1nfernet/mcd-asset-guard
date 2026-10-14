"""商城监控：可兑换周边 -> 兑换规划（有界背包 DP）-> 差多少 -> 下架/开抢标记。

兑换规划是确定性动态规划，绝不让大模型口算：
- rescue 策略（默认，对齐「最大化临期积分消耗」目标）：
    在 总积分 <= 可用积分 的组合里，最大化 min(总消耗, 临期桶)，
    再最小化 |总消耗 - 临期桶|（恰好用掉临期桶，不过度透支非临期积分）。
- value 策略：经典 0/1 背包，最大化商品价值（price_fen），同值取更低消耗。
DP 内部以 0.1 积分为整数单位（x10），Decimal 仅做展示层换算。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from src.mcp_client import McpClient
from src.models import MallProduct, fen_display
from src.report.render import banner, kv, table

DEFAULT_CATS = "2>8"  # 周边产品（离线 fixtures 有该类目；在线可传 2>8,2>9,1>4 …）


@dataclass
class RedeemPlan:
    strategy: str
    items: list[MallProduct]
    total_points: Decimal
    rescued: Decimal
    leftover: Decimal
    remaining: Decimal
    note: str = ""
    window_closed: list[str] = field(default_factory=list)


class MallService:
    def __init__(self, client: McpClient, as_of: str | None = None):
        self.client = client
        self.as_of = as_of

    def now(self) -> dt.datetime:
        if self.as_of:
            return dt.datetime.combine(
                dt.date.fromisoformat(self.as_of), dt.time(12, 0)
            )
        data = self.client.call("now-time-info") or {}
        # 信封 data 里 formatted="2026-10-14 12:44:50"
        f = data.get("formatted") or data.get("datetime") or data.get("date") or ""
        return _parse_server_dt(str(f))

    def products(self, cat_rule_ids: str | None = DEFAULT_CATS) -> list[MallProduct]:
        args = {"catRuleIds": cat_rule_ids} if cat_rule_ids else {}
        data = self.client.call("mall-points-products", args) or []
        return [MallProduct.from_payload(d) for d in data]

    # ------------------------------------------------------------ 规划

    def plan(
        self,
        products: list[MallProduct],
        available: Decimal,
        expiring: Decimal,
        strategy: str = "rescue",
        now: dt.datetime | None = None,
    ) -> RedeemPlan:
        items = [
            p
            for p in products
            if p.is_on_sale and p.point > 0 and p.point <= available
        ]
        if strategy == "value":
            chosen = _plan_value(items, available)
        else:
            chosen = _plan_rescue(items, available, expiring)
        total = sum((p.point for p in chosen), Decimal("0.0"))
        rescued = min(total, expiring)
        leftover = max(expiring - total, Decimal("0.0"))
        remaining = available - total
        if expiring <= 0:
            note = "当前无临期积分，未生成救回组合（可换 value 策略看划算组合）"
        elif leftover == 0:
            note = f"本月临期 {expiring} 积分已全部救回"
        else:
            note = f"仍有 {leftover} 临期积分未救回（商城内无可行组合）"
        closed = [p.name for p in chosen if now and not p.window_open(now)]
        return RedeemPlan(
            strategy, chosen, total, rescued, leftover, remaining, note, closed
        )

    def gaps(
        self, products: list[MallProduct], available: Decimal
    ) -> list[tuple[MallProduct, Decimal]]:
        """积分不够的商品：还差多少。"""
        out = [
            (p, p.point - available)
            for p in products
            if p.is_on_sale and p.point > available
        ]
        return sorted(out, key=lambda t: t[1])

    def ending_soon(
        self, products: list[MallProduct], now: dt.datetime, days: int = 7
    ) -> list[MallProduct]:
        return [p for p in products if p.ending_within(now, days)]

    def opening_soon(
        self, products: list[MallProduct], now: dt.datetime, hours: int = 48
    ) -> list[MallProduct]:
        return [p for p in products if p.is_presale or p.opening_within(now, hours)]


# ---------------------------------------------------------------- DP（纯函数）


def _plan_rescue(items: list[MallProduct], available: Decimal, expiring: Decimal) -> list[MallProduct]:
    cap = int(available * 10)
    exp = int(expiring * 10)
    reach = bytearray(cap + 1)
    reach[0] = 1
    prev = [-1] * (cap + 1)
    pick = [-1] * (cap + 1)
    for i, p in enumerate(items):
        w = int(p.point * 10)
        if w <= 0:
            continue
        for c in range(cap, w - 1, -1):
            if reach[c - w] and not reach[c]:
                reach[c] = 1
                prev[c] = c - w
                pick[c] = i
    best, best_key = 0, (min(0, exp), -abs(0 - exp))
    for c in range(1, cap + 1):
        if not reach[c]:
            continue
        key = (min(c, exp), -abs(c - exp))
        if key > best_key:  # 同分取更小消耗（先遍历到）
            best, best_key = c, key
    return _backtrack(items, prev, pick, best)


def _plan_value(items: list[MallProduct], available: Decimal) -> list[MallProduct]:
    cap = int(available * 10)
    dp = [0] * (cap + 1)
    prev = [-1] * (cap + 1)
    pick = [-1] * (cap + 1)
    for i, p in enumerate(items):
        w = int(p.point * 10)
        v = p.price_fen
        if w <= 0:
            continue
        for c in range(cap, w - 1, -1):
            if dp[c - w] + v > dp[c]:
                dp[c] = dp[c - w] + v
                prev[c] = c - w
                pick[c] = i
    best = max(range(cap + 1), key=lambda c: (dp[c], -c))
    return _backtrack(items, prev, pick, best)


def _backtrack(items, prev, pick, cost: int) -> list[MallProduct]:
    chosen = []
    while cost > 0:
        i = pick[cost]
        if i < 0:
            break
        chosen.append(items[i])
        cost = prev[cost]
    chosen.reverse()
    return chosen


def _parse_server_dt(s: str) -> dt.datetime:
    d = dt.datetime.strptime(s[:10], "%Y-%m-%d")
    return d.replace(hour=12)


# ---------------------------------------------------------------- 渲染


def render_mall(products: list[MallProduct], now: dt.datetime) -> str:
    lines = [banner("商城监控（可兑换商品）"), ""]
    rows = []
    for p in products:
        if p.is_presale or p.opening_within(now):
            mark = "开抢提醒"
        elif p.ending_within(now):
            mark = "即将下架"
        elif not p.window_open(now):
            mark = "窗口已结束"
        else:
            mark = ""
        rows.append(
            [
                p.spu_id,
                p.name,
                str(p.point),
                fen_display(p.price_fen),
                p.status_label,
                _short(p.up_time),
                _short(p.down_time),
                mark,
            ]
        )
    if rows:
        lines += table(
            ["spu", "商品", "积分", "金额", "状态", "上架", "下架", "标记"], rows
        )
    else:
        lines.append("（该类目下没有可展示的商品）")
    return "\n".join(lines)


def render_plan(plan: RedeemPlan, expiring: Decimal) -> str:
    lines = [
        banner(f"兑换规划（策略 {plan.strategy}）"),
        f"目标：救回本月临期 {expiring} 积分",
        "",
    ]
    if plan.items:
        for p in plan.items:
            lines.append(f"  - {p.name} x1（{p.point} 积分，{fen_display(p.price_fen)}）")
        lines += kv(
            [
                ("合计消耗", f"{plan.total_points} 积分"),
                ("救回临期", f"{plan.rescued} / {expiring}"),
                ("兑换后余额", f"{plan.remaining} 积分"),
            ]
        )
    else:
        lines.append("  （无可行兑换组合）")
    lines.append("")
    lines.append(f"结论：{plan.note}")
    if plan.window_closed:
        lines.append(
            f"注意：{'、'.join(plan.window_closed)} 的兑换窗口已结束"
            "（离线 fixtures 为历史样本；在线模式按实时窗口判定）"
        )
    return "\n".join(lines)


def render_gaps(gaps: list[tuple[MallProduct, Decimal]]) -> str:
    if not gaps:
        return ""
    rows = [[p.name, str(p.point), str(gap)] for p, gap in gaps]
    return "\n".join(
        [banner("差多少（积分不够的商品）"), ""]
        + table(["商品", "所需积分", "还差"], rows)
    )


def _short(d: dt.datetime | None) -> str:
    return d.strftime("%m-%d %H:%M") if d else "-"
