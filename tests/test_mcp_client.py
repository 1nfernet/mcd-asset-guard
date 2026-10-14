"""MCP 连接层测试：SSE 解析、结果解包、业务信封、fixture 路由、限速。"""
import json
import time
from pathlib import Path

import pytest

from src.errors import McpBusinessError, McpProtocolError
from src.mcp_client import (
    FixtureTransport,
    RateLimiter,
    RawText,
    _slug,
    parse_sse,
    unwrap_tool_result,
)


def test_parse_sse_with_ping_and_event():
    text = (
        ": ping\n"
        "event: message\n"
        'id: 1\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        "\n"
    )
    assert parse_sse(text)["result"]["ok"] is True


def test_parse_sse_multiline_data():
    text = 'data: {"a":1,\ndata: "b":2}\n\n'
    assert parse_sse(text) == {"a": 1, "b": 2}


def test_parse_sse_without_data_raises():
    with pytest.raises(McpProtocolError):
        parse_sse(": keep-alive\n\n")


def test_unwrap_structured_content_first():
    result = {
        "structuredContent": {"code": 200, "data": [1]},
        "content": [{"type": "text", "text": '{"ignored":true}'}],
    }
    assert unwrap_tool_result(result) == {"code": 200, "data": [1]}


def test_unwrap_json_text():
    result = {"content": [{"type": "text", "text": '{"code":200,"data":{"x":1}}'}]}
    assert unwrap_tool_result(result)["data"] == {"x": 1}


def test_unwrap_markdown_returns_rawtext():
    md = "### 麦麦省优惠券列表：\n- 优惠券标题：中薯条买一送一"
    result = {"content": [{"type": "text", "text": md}]}
    out = unwrap_tool_result(result)
    assert isinstance(out, RawText)
    assert out.text.startswith("### 麦麦省优惠券列表")


def test_slug_sanitizes_cat_rule_ids():
    assert _slug({"catRuleIds": "2>8"}) == "catRuleIds_2_8"
    assert _slug({"spuId": 6971, "storeCode": "1410056"}) == "spuId_6971__storeCode_1410056"


def test_fixture_transport_resolves_by_args(tmp_path: Path):
    fixtures = Path(__file__).resolve().parent / "fixtures"
    t = FixtureTransport(fixtures)
    resp = t.request("tools/call", {"name": "mall-points-products",
                                   "arguments": {"catRuleIds": "2>8"}})
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["code"] == 200
    assert len(payload["data"]) == 3


def test_fixture_transport_missing_raises():
    t = FixtureTransport(Path(__file__).resolve().parent / "fixtures")
    with pytest.raises(Exception):
        t.request("tools/call", {"name": "no-such-tool", "arguments": {}})


def test_client_returns_data_of_envelope(client):
    data = client.call("query-my-account")
    assert data["availablePoint"] == "842.9"
    assert client.last_trace_id  # traceId 被记录


def test_client_raises_on_business_error(tmp_path: Path):
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    (fixtures / "boom.json").write_text(
        json.dumps({"json": {"code": 500, "message": "服务异常", "traceId": "t-1"}}),
        encoding="utf-8",
    )
    from tests.conftest import make_config
    from src.mcp_client import McpClient

    cfg = make_config(tmp_path)
    cfg.fixtures_dir = fixtures
    client = McpClient.create(cfg)
    with pytest.raises(McpBusinessError) as ei:
        client.call("boom")
    assert ei.value.code == 500


def test_rate_limiter_blocks_when_window_full():
    limiter = RateLimiter(2, window=0.4)
    t0 = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # 第三次必须等待窗口滚动
    assert time.monotonic() - t0 >= 0.3


def test_write_actions_locked_by_default(client):
    from src.errors import WriteActionLocked

    with pytest.raises(WriteActionLocked):
        client.call_write("mall-create-order", {"spuId": 17643})
