"""MCP 协议层（JSON-RPC 报文构造/解析）单元测试。"""

from __future__ import annotations

import json

from src.mcp import protocol as P


def test_make_request_shape():
    msg = P.make_request(7, "tools/list", {"cursor": "c"})
    assert msg == {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {"cursor": "c"}}
    assert P.is_request(msg)
    assert not P.is_notification(msg)
    assert not P.is_response(msg)


def test_make_request_without_params_omits_key():
    msg = P.make_request(1, "initialize")
    assert "params" not in msg


def test_make_notification_shape():
    msg = P.make_notification("notifications/initialized", {})
    assert "id" not in msg
    assert P.is_notification(msg)
    assert not P.is_request(msg)


def test_make_error_response():
    msg = P.make_error_response(3, P.METHOD_NOT_FOUND, "nope")
    assert msg["error"]["code"] == P.METHOD_NOT_FOUND
    assert P.is_response(msg)


def test_parse_message_accepts_dict_str_bytes():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert P.parse_message(payload) == payload
    assert P.parse_message(json.dumps(payload)) == payload
    assert P.parse_message(json.dumps(payload).encode("utf-8")) == payload


def test_parse_message_rejects_invalid():
    assert P.parse_message("") is None
    assert P.parse_message("not json") is None
    assert P.parse_message("[1, 2]") is None
    assert P.parse_message(b"\xff\xfe\x00") is None
    assert P.parse_message(None) is None
    assert P.parse_message(123) is None


def test_serialize_message_is_single_line():
    text = P.serialize_message({"jsonrpc": "2.0", "id": 1, "result": {"a": "多行\n内容"}})
    assert "\n" not in text
    assert json.loads(text)["result"]["a"] == "多行\n内容"


def test_build_initialize_params():
    params = P.build_initialize_params()
    assert params["protocolVersion"] == P.PROTOCOL_VERSION
    assert params["clientInfo"]["name"] == P.CLIENT_NAME
    assert params["capabilities"]["tools"] == {}


def test_negotiate_protocol_version():
    assert P.negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert P.negotiate_protocol_version("1999-01-01") == P.PROTOCOL_VERSION
    assert P.negotiate_protocol_version(None) == P.PROTOCOL_VERSION
    assert P.negotiate_protocol_version(5) == P.PROTOCOL_VERSION


def test_message_classification():
    response = {"jsonrpc": "2.0", "id": 1, "result": None}
    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}
    request = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    notification = {"jsonrpc": "2.0", "method": "ping"}
    assert P.is_response(response) and P.is_response(error)
    assert P.is_request(request) and not P.is_request(notification)
    assert P.is_notification(notification)
