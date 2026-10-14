"""券组合优化（Phase 2 / v1.1）。

设计已定，待 Phase 1 数据层跑通后实现：
- 券池取数：available-coupons / query-my-coupons（预渲染 Markdown，需确定性正则抽取）
  + query-store-coupons / query-promotions（满减规则，仅 beType=6 企业团餐）
- 本地规则引擎按 promotionType(31 满额减 / 33 订单折扣) + products(包含/排除/全部)
  枚举候选组合，硬上限 12 品 / 10 券，2s 超时降级贪心
- Top-N 候选回传 calculate-price 权威复核（券按 item 传 couponId/couponCode，
  返回价格单位为分）
- 遗留未知数：Markdown 券响应看不到 couponId，需真实 MCP 原始 JSON-RPC 响应确认
"""
from src.errors import PhaseNotImplemented


def optimize(*args, **kwargs):
    raise PhaseNotImplemented("squad.optimizer 将在 Phase 2（v1.1）实现")
