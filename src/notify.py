"""共享通知层：终端输出 + 状态去重（同一桶同一档位只提醒一次）；webhook 为 v1.1 预留。"""
from __future__ import annotations

from src.state import JsonStore

ANSI = {
    "critical": "31",  # 红
    "high": "33",      # 黄
    "warn": "35",      # 品红
    "info": "36",      # 青
    "ok": "32",        # 绿
}


class NotifyState:
    """通知去重：key 对应的档位变化时才放行（跨运行持久化）。"""

    def __init__(self, store: JsonStore):
        self._s = store

    def should_emit(self, key: str, tier: str) -> bool:
        if self._s.get(f"dedup:{key}") == tier:
            return False
        self._s.set(f"dedup:{key}", tier)
        return True


class Notify:
    def __init__(self, use_color: bool, state: NotifyState | None = None):
        self.color = use_color
        self.state = state

    def _c(self, text: str, level: str) -> str:
        if not self.color:
            return text
        return f"\033[{ANSI.get(level, '1')}m{text}\033[0m"

    def emit(self, level: str, title: str, lines: list[str] | None = None) -> None:
        block = [self._c(f"[{level.upper()}] {title}", level)]
        block += [f"  {ln}" for ln in (lines or [])]
        print("\n".join(block), flush=True)

    def emit_once(self, key: str, tier: str, title: str, lines: list[str] | None = None) -> bool:
        """同 key 同档位只发一次；返回是否真正发出。"""
        if self.state and not self.state.should_emit(key, tier):
            return False
        self.emit(tier, title, lines)
        return True

    def webhook(self, payload: dict) -> None:  # v1.1 预留
        pass
