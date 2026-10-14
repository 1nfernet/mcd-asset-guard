"""终端渲染：ASCII 表格（正确处理中日韩字符宽度）、KV 块、横幅。零第三方依赖。"""
from __future__ import annotations

import os
import sys
import unicodedata


def supports_color(stream=None) -> bool:
    s = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(s, "isatty", lambda: False)())


def dw(s: str) -> int:
    """显示宽度：全角字符按 2 计。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dw(s))


def table(headers: list[str], rows: list[list]) -> list[str]:
    cols = len(headers)
    widths = [dw(h) for h in headers]
    for r in rows:
        for i in range(cols):
            widths[i] = max(widths[i], dw(str(r[i])))
    out = ["  ".join(pad(h, widths[i]) for i, h in enumerate(headers))]
    out.append("  ".join("-" * w for w in widths))
    for r in rows:
        out.append("  ".join(pad(str(r[i]), widths[i]) for i in range(cols)))
    return out


def kv(pairs: list[tuple[str, object]], key_w: int = 0) -> list[str]:
    """键值块；key_w=0 时按最长键自动对齐（中文按 2 宽计算）。"""
    if pairs and key_w <= 0:
        key_w = max(dw(str(k)) for k, _ in pairs) + 2
    return [f"{pad(str(k) + ':', key_w)}{v}" for k, v in pairs]


def banner(title: str) -> str:
    return f"== {title} =="
