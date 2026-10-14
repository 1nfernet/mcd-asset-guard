"""统一异常体系。"""


class McdError(Exception):
    """项目内异常基类。"""


class McpError(McdError):
    """MCP 连接层错误。"""


class McpTransportError(McpError):
    """网络/HTTP 层错误（可重试）。"""


class McpProtocolError(McpError):
    """JSON-RPC / MCP 协议层错误（不可重试）。"""


class RateLimited(McpError):
    """HTTP 429 限速。"""

    def __init__(self, retry_after: float = 0.0):
        self.retry_after = retry_after
        super().__init__(f"429 限速（Retry-After={retry_after}s）")


class SessionExpired(McpError):
    """Mcp-Session-Id 失效（HTTP 404），需要重新握手。"""

    def __init__(self):
        super().__init__("Mcp-Session-Id 失效（HTTP 404）")


class McpBusinessError(McpError):
    """业务信封 code != 200。"""

    def __init__(self, code, message, trace_id=None):
        self.code = code
        self.trace_id = trace_id
        super().__init__(f"业务错误 code={code} message={message} traceId={trace_id}")


class WriteActionLocked(McpError):
    """写操作被锁定（ENABLE_WRITE_ACTIONS 未开启）。

    捕获后应把将要发送的 payload 打印出来作为 dry-run 演示。
    """

    def __init__(self, tool: str, payload: dict):
        self.tool = tool
        self.payload = payload
        super().__init__(f"写操作 {tool} 已锁定（ENABLE_WRITE_ACTIONS!=true）")


class PhaseNotImplemented(McdError):
    """v1.1 模块（squad）未实现。"""
