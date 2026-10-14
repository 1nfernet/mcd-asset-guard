#!/usr/bin/env python
"""提交前自检：扫描仓库是否混入真实 Token / 长凭据。

判定逻辑刻意保守但有效——避免把分隔线 `----` 和 snake_case 标识符
（如 test_plan_rescue_picks_only_affordable_item）误报成凭据：
  1) 长度 >= 32 且字符种类 >= 8（排除重复字符组成的分隔线）
  2) 至少包含「大写 / 小写 / 数字」中的两类（排除纯小写标识符）
  3) 排除纯 snake_case 标识符

默认跳过 tests/fixtures（实测响应夹具，含公开 CDN 链接与脱敏 ID，非凭据）。
命中即退出码 1。用法：python scripts/check_secrets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "state", "logs", "node_modules", ".venv"}
SKIP_FILES = {".env.example"}
SCAN_SUFFIX = {".py", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".cfg", ".env", ""}

CANDIDATE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}"), "疑似 Bearer Token 明文"),
    (
        re.compile(
            r"(?im)^\s*MCD_MCP_TOKEN\s*=\s*(?!your-token-here\b)[\"']?[A-Za-z0-9._-]{8,}"
        ),
        "疑似 MCD_MCP_TOKEN 明文",
    ),
]


def looks_like_secret(token: str) -> bool:
    if len(set(token)) < 8:
        return False  # 分隔线、重复字符填充
    if re.fullmatch(r"[A-Za-z]+(/[A-Za-z]+)+", token):
        return False  # 字段枚举 / 路径片段，如 spuId/point/price/upTime
    classes = sum(
        bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]")
    )
    if classes < 2:
        return False  # 纯小写标识符 / 纯数字
    if re.fullmatch(r"[a-z0-9_]+", token) and "_" in token:
        return False  # snake_case 标识符
    return True


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.parts[0] == "tests" and "fixtures" in rel.parts:
            continue
        if p.name in SKIP_FILES or p.suffix not in SCAN_SUFFIX:
            continue
        yield p


def main() -> int:
    hits: list[str] = []
    for p in iter_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat, desc in PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append(f"{p.relative_to(ROOT)}:{i}  {desc}: {m.group(0)[:28]}...")
            for m in CANDIDATE.finditer(line):
                if looks_like_secret(m.group(0)):
                    hits.append(
                        f"{p.relative_to(ROOT)}:{i}  疑似长凭据串（请人工确认）: "
                        f"{m.group(0)[:28]}..."
                    )
    if hits:
        print("check_secrets: 发现疑似凭据——")
        for h in hits:
            print(f"  {h}")
        return 1
    print("check_secrets: 通过（未发现明文凭据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
