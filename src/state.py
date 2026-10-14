"""本地状态持久化（state/，已 gitignore）。

存放：Mcp-Session-Id、通知去重、派对空位快照、慢变数据缓存。
全部为 JSON 文件，写入走临时文件 + 原子替换，避免半截文件。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class JsonStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # 状态文件损坏时从零开始，不影响主流程
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.flush()

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self._path)


class TtlCache:
    """基于 JsonStore 的简单 TTL 缓存（慢变数据：城市/门店/可预约日期）。"""

    def __init__(self, store: JsonStore):
        self._s = store

    def get(self, key: str, ttl: float):
        ent = self._s.get(key)
        if not isinstance(ent, dict):
            return None
        if time.time() - ent.get("t", 0) > ttl:
            return None
        return ent.get("v")

    def set(self, key: str, value) -> None:
        self._s.set(key, {"t": time.time(), "v": value})
