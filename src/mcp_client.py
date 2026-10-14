"""共享 MCP 连接层——整个项目唯一触网的模块。

职责（对应架构铁律「取数交给 MCP」）：
- Streamable HTTP + Bearer Token（Token 只从环境变量读取，本文件不落任何凭据）
- 响应双格式解析：application/json 与 text/event-stream（SSE）
- Mcp-Session-Id 持久化到 state/，跨进程复用，避免重复握手；404 失效自动重握手
- 本地令牌桶限速（默认 480 req/min，官方上限 600，留 20% 余量）+ 429 指数退避
- 写操作独立入口 call_write()：函数级白名单 + ENABLE_WRITE_ACTIONS 闸门
- 离线回放：FixtureTransport 与 HttpTransport 同构，--offline 时零改动切换

业务信封（真实接口 2026-10-14 实测）：
    {"success":true,"code":200,"message":"...","datetime":"...","traceId":"...","data":...}
成功判定以 code == 200 为准（success 字段可能缺失）；traceId 记入日志。
"""
from __future__ import annotations

import itertools
import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.config import Config
from src.errors import (
    McpBusinessError,
    McpError,
    McpProtocolError,
    McpTransportError,
    RateLimited,
    SessionExpired,
    WriteActionLocked,
)

log = logging.getLogger("mcd.mcp")

PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = "mcd-asset-guard/0.1.0"

# 写操作白名单：只允许这些工具走 call_write()
WRITE_TOOLS = frozenset(
    {
        "create-order",
        "party-order-create",
        "mall-create-order",
        "draw-lottery",
        "auto-bind-coupons",
        "delivery-create-address",
    }
)


class RawText:
    """非 JSON 载荷（如 available-coupons 返回的预渲染 Markdown）。"""

    def __init__(self, text: str):
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover
        return f"RawText({len(self.text)} chars)"


# ---------------------------------------------------------------- 解析


def parse_sse(text: str) -> dict:
    """SSE 流 -> JSON-RPC 响应 dict。忽略注释行（: ping）与 event/id 行。"""
    data: list[str] = []
    for line in text.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if not data:
        raise McpProtocolError("SSE 流中没有 data: 行")
    try:
        return json.loads("\n".join(data))
    except json.JSONDecodeError as e:
        raise McpProtocolError(f"SSE data 不是合法 JSON: {e}") from e


def unwrap_tool_result(result: Any) -> Any:
    """JSON-RPC result -> 业务载荷。

    structuredContent 优先；否则拼接 content[].text 并尝试 JSON 解析，
    解析失败则原样返回 RawText（Markdown 工具）。
    """
    if not isinstance(result, dict):
        raise McpProtocolError(f"工具 result 不是对象: {type(result)}")
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        return sc
    text = "".join(
        c.get("text", "")
        for c in result.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    )
    if not text:
        raise McpProtocolError("工具结果中没有 text / structuredContent")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return RawText(text)


# ---------------------------------------------------------------- 限速


class RateLimiter:
    """滑动窗口令牌桶（线程安全）。"""

    def __init__(self, max_events: int, window: float = 60.0):
        self._max = max_events
        self._window = window
        self._events: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._events = [t for t in self._events if now - t < self._window]
                if len(self._events) < self._max:
                    self._events.append(now)
                    return
                wait = self._window - (now - self._events[0]) + 0.05
            log.debug("限速等待 %.1fs", wait)
            time.sleep(wait)


# ---------------------------------------------------------------- 传输层


class Transport(Protocol):
    def request(self, method: str, params: dict | None) -> dict: ...


class HttpTransport:
    """Streamable HTTP 传输：握手、会话复用、双格式解码、429/404 信号。"""

    def __init__(self, cfg: Config, session_file: Path):
        self._endpoint = cfg.endpoint
        self._base_headers = {
            "Authorization": f"Bearer {cfg.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        self._client = httpx.Client(timeout=cfg.request_timeout)
        self._ids = itertools.count(1)
        self._session_file = Path(session_file)
        self._session_id = self._load_session()
        self._lock = threading.Lock()

    # ---- 会话持久化 ----
    def _load_session(self) -> str | None:
        try:
            return json.loads(self._session_file.read_text(encoding="utf-8")).get(
                "sessionId"
            )
        except (OSError, ValueError):
            return None

    def _save_session(self) -> None:
        try:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            self._session_file.write_text(
                json.dumps({"sessionId": self._session_id, "savedAt": time.time()}),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover
            log.warning("session-id 持久化失败（不影响本次运行）")

    def _reset_session(self) -> None:
        self._session_id = None
        try:
            self._session_file.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    # ---- HTTP ----
    def _post(self, payload: dict, extra: dict | None = None) -> httpx.Response:
        headers = dict(self._base_headers)
        if extra:
            headers.update(extra)
        try:
            return self._client.post(self._endpoint, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise McpTransportError(f"网络错误: {e}") from e

    def _decode(self, resp: httpx.Response) -> dict:
        if resp.status_code == 429:
            try:
                ra = float(resp.headers.get("Retry-After", "0") or 0)
            except ValueError:
                ra = 0.0
            raise RateLimited(ra)
        if resp.status_code == 404:
            raise SessionExpired()
        if resp.status_code >= 400:
            raise McpTransportError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content:  # 202 Accepted（notification 常见）
            return {}
        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            return parse_sse(resp.text)
        try:
            return resp.json()
        except ValueError as e:
            raise McpProtocolError(f"非 JSON 响应: {e}") from e

    # ---- 握手 ----
    def initialize(self) -> None:
        with self._lock:
            if self._session_id:
                return
            payload = {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mcd-asset-guard", "version": "0.1.0"},
                },
            }
            resp = self._post(payload)
            sid = resp.headers.get("mcp-session-id")
            body = self._decode(resp)
            if "error" in body:
                raise McpProtocolError(f"initialize 失败: {body['error']}")
            if sid:
                self._session_id = sid
                self._save_session()
                log.info("握手完成 session=%s...", sid[:12])
            else:
                log.warning("initialize 未返回 Mcp-Session-Id，按无会话模式继续")
            # 握手完成后必须发送 initialized 通知
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                self._session_headers(),
            )

    def _session_headers(self) -> dict:
        h = {}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return h

    # ---- 统一入口 ----
    def request(self, method: str, params: dict | None = None) -> dict:
        for attempt in (1, 2):
            self.initialize()
            payload: dict = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
            if params is not None:
                payload["params"] = params
            resp = self._post(payload, self._session_headers())
            try:
                body = self._decode(resp)
            except SessionExpired:
                if attempt == 1 and self._session_id:
                    log.info("会话失效，重新握手")
                    self._reset_session()
                    continue
                raise
            if "error" in body:
                raise McpProtocolError(f"JSON-RPC error: {body['error']}")
            return body
        raise McpProtocolError("重握手后仍失败")  # pragma: no cover


class FixtureTransport:
    """离线回放：按 工具名__参数slug.json 在 fixtures 目录找真实响应回放。

    fixture 文件格式（二选一）：
      {"content_type": "application/json", "json": {…原始信封…}}
      {"content_type": "text/markdown",    "text": "…预渲染文本…"}
    """

    def __init__(self, fixtures_dir: Path):
        self._dir = Path(fixtures_dir)

    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": 0,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "serverInfo": {"name": "mcd-fixtures"},
                },
            }
        if method.startswith("notifications/"):
            return {"jsonrpc": "2.0", "id": None, "result": {}}
        if method != "tools/call":
            raise McpError(f"fixtures 不支持 method={method}")
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        path = self._resolve(str(name), args)
        if path is None:
            raise McpError(
                f"缺少 fixture: {name} args={args}（在线跑一次后把响应存入 {self._dir}）"
            )
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        if "json" in wrapper:
            text = json.dumps(wrapper["json"], ensure_ascii=False)
        else:
            text = wrapper["text"]
        return {
            "jsonrpc": "2.0",
            "id": 0,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    def _resolve(self, name: str, args: dict) -> Path | None:
        candidates = []
        if args:
            candidates.append(self._dir / f"{name}__{_slug(args)}.json")
        candidates.append(self._dir / f"{name}.json")
        for p in candidates:
            if p.exists():
                return p
        return None


def _slug(args: dict) -> str:
    def sanitize(v) -> str:
        s = str(v)
        for ch in ">,: ":
            s = s.replace(ch, "_")
        return s

    return "__".join(f"{k}_{sanitize(v)}" for k, v in sorted(args.items()))


# ---------------------------------------------------------------- 客户端


class McpClient:
    """工具调用门面：限速 -> 传输 -> 解包 -> 业务信封校验。"""

    def __init__(self, cfg: Config, transport: Transport, limiter: RateLimiter | None = None):
        self.cfg = cfg
        self._t = transport
        self._limiter = limiter or RateLimiter(cfg.rate_limit_per_min)
        self.last_trace_id: str | None = None

    @classmethod
    def create(cls, cfg: Config) -> "McpClient":
        if cfg.offline:
            log.info("[offline] 使用 fixtures: %s", cfg.fixtures_dir)
            transport: Transport = FixtureTransport(cfg.fixtures_dir)
        else:
            transport = HttpTransport(cfg, cfg.state_dir / "mcp-session.json")
        return cls(cfg, transport)

    def call(self, tool: str, args: dict | None = None, retries: int = 3) -> Any:
        """调用只读工具，返回业务信封里的 data（或 Markdown RawText）。"""
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._limiter.acquire()
            try:
                resp = self._t.request("tools/call", {"name": tool, "arguments": args or {}})
                result = resp.get("result")
                if result is None:
                    raise McpProtocolError(f"工具 {tool} 未返回 result: {resp}")
                payload = unwrap_tool_result(result)
                return self._check_envelope(tool, payload)
            except (RateLimited, McpTransportError) as e:
                last_err = e
                if attempt >= retries:
                    break
                wait = max(getattr(e, "retry_after", 0.0), delay) + random.uniform(0, 0.5)
                delay *= 2
                log.warning(
                    "%s，%.1fs 后重试 (%d/%d)", type(e).__name__, wait, attempt + 1, retries
                )
                time.sleep(wait)
        raise McpError(f"工具 {tool} 重试 {retries} 次后仍失败: {last_err}")

    def _check_envelope(self, tool: str, payload: Any) -> Any:
        if isinstance(payload, dict) and "code" in payload and ("data" in payload or "message" in payload):
            if payload.get("code") != 200:
                raise McpBusinessError(
                    payload.get("code"), payload.get("message"), payload.get("traceId")
                )
            self.last_trace_id = payload.get("traceId")
            log.info("tool=%s ok traceId=%s", tool, self.last_trace_id)
            return payload.get("data")
        return payload

    def call_write(self, tool: str, args: dict | None = None) -> Any:
        """写操作专用入口：白名单 + 闸门。闸门关闭时抛 WriteActionLocked，
        调用方捕获后应把 payload 打印为 dry-run。"""
        if tool not in WRITE_TOOLS:
            raise ValueError(f"{tool} 不在写操作白名单: {sorted(WRITE_TOOLS)}")
        if not self.cfg.enable_write_actions:
            raise WriteActionLocked(tool, args or {})
        log.warning("执行写操作 %s args=%s", tool, args)
        return self.call(tool, args)
