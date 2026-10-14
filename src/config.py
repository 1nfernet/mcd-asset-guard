"""运行配置。

安全约定：
- Token 只从环境变量 MCD_MCP_TOKEN 读取，任何提交文件禁止出现真实 Token。
- 写操作默认锁定，只有 ENABLE_WRITE_ACTIONS=true 才放行。
- 未检测到 Token 时自动降级为 offline（fixtures 演示模式），不报错不裸连。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
_PLACEHOLDER_TOKENS = {"", "your-token-here", "changeme"}


def load_env_file(path: Path) -> None:
    """极简 .env 加载（避免引入 python-dotenv 依赖）。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    token: str | None
    endpoint: str
    offline: bool
    auto_offline: bool  # True = 因缺少 Token 自动降级到 offline
    enable_write_actions: bool
    fixtures_dir: Path
    state_dir: Path
    log_dir: Path
    as_of: str | None
    rate_limit_per_min: int
    squad_enabled: bool
    request_timeout: float

    @classmethod
    def load(
        cls,
        offline: bool = False,
        as_of: str | None = None,
        state_dir: Path | None = None,
        log_dir: Path | None = None,
        fixtures_dir: Path | None = None,
    ) -> "Config":
        load_env_file(PROJECT_ROOT / ".env")
        token = (os.environ.get("MCD_MCP_TOKEN") or "").strip()
        if token.lower() in _PLACEHOLDER_TOKENS:
            token = ""
        token = token or None
        auto = (not offline) and (token is None)
        cfg = cls(
            token=token,
            endpoint=os.environ.get("MCD_MCP_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/"),
            offline=offline or auto,
            auto_offline=auto,
            enable_write_actions=_bool("ENABLE_WRITE_ACTIONS", False),
            fixtures_dir=fixtures_dir or PROJECT_ROOT / "tests" / "fixtures",
            state_dir=state_dir or PROJECT_ROOT / "state",
            log_dir=log_dir or PROJECT_ROOT / "logs",
            as_of=as_of or os.environ.get("MCD_AS_OF"),
            rate_limit_per_min=int(os.environ.get("MCD_RATE_LIMIT_PER_MIN", "480")),
            squad_enabled=_bool("SQUAD_ENABLED", False),
            request_timeout=float(os.environ.get("MCD_REQUEST_TIMEOUT", "15")),
        )
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        return cfg
