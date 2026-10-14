"""分账计算（Phase 2 / v1.1）。

设计已定：最大余数法（largest remainder），Decimal 精确到分，总额严格守恒，
处理服务费/打包费按比例分摊与"谁点了什么"的逐行归集。
"""
from src.errors import PhaseNotImplemented


def split(*args, **kwargs):
    raise PhaseNotImplemented("squad.splitter 将在 Phase 2（v1.1）实现")
