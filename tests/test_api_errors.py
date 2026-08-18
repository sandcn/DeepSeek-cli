"""API 错误处理体系测试 — 分类 / 重试判定 / 退避 / Retry-After / 用户消息。

覆盖 src/api/errors.py（新增）、client_async._check_response（增强）、
_retry.retry_api_call_async（重构）、core.exceptions.is_network_error（增强）。
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import src.api._retry as retry_mod
from src.api.errors import (
    APIError, AuthError, CONNECTION_ERRORS, InvalidRequestError, NotFoundError,
    RateLimitError, RETRY_AFTER_CAP, RETRYABLE_HTTP_STATUS, ServerError,
    classify_http_error, compute_retry_delay, format_user_error, is_retryable,
    parse_retry_after,
)
from src.api.client_async import _check_response
from src.api._retry import retry_api_call_async
from src.core.exceptions import is_network_error


# ── 分类：classify_http_error ───────────────────────────────

@pytest.mark.parametrize("status,expected_type", [
    (429, RateLimitError),
    (401, AuthError),
    (403, AuthError),
    (404, NotFoundError),
    (400, InvalidRequestError),
    (422, InvalidRequestError),
    (408, ServerError),
    (425, ServerError),
    (500, ServerError),
    (502, ServerError),
    (503, ServerError),
    (504, ServerError),
    (418, APIError),
])
def test_classify_http_error_types(status, expected_type):
    exc = classify_http_error(status, "boom")
    assert type(exc) is expected_type
    assert exc.status_code == status


def test_classify_http_error_retryable_flag():
    for status in RETRYABLE_HTTP_STATUS:
        assert classify_http_error(status, "x").retryable, status
    for status in (400, 401, 403, 404, 422, 418):
        assert not classify_http_error(status, "x").retryable, status


def test_classify_http_error_passes_retry_after():
    exc = classify_http_error(429, "slow down", retry_after="30")
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after == 30.0


# ── 向后兼容 ────────────────────────────────────────────────

def test_api_error_backward_compat():
    e = APIError(500, "server exploded")
    assert e.status_code == 500
    assert str(e).startswith("API error 500:")


def test_rate_limit_error_is_api_error_subclass():
    assert issubclass(RateLimitError, APIError)
    exc = RateLimitError("Rate limited: quota")
    assert isinstance(exc, APIError)
    assert exc.status_code == 429


def test_rate_limit_error_single_arg_message():
    exc = RateLimitError("Rate limited: too fast")
    assert "Rate limited" in str(exc)


# ── parse_retry_after ───────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ("5", 5.0),
    ("2.5", 2.5),
    ("-3", None),
    ("abc", None),
    ("soon", None),
])
def test_parse_retry_after_seconds(value, expected):
    assert parse_retry_after(value) == expected


def test_parse_retry_after_numeric_input():
    assert parse_retry_after(7) == 7.0
    assert parse_retry_after(-1) is None


def test_parse_retry_after_http_date_future():
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT")
    assert 25.0 <= parse_retry_after(future) <= 30.0


def test_parse_retry_after_http_date_past():
    assert parse_retry_after("Mon, 01 Jan 2001 00:00:00 GMT") == 0.0


# ── compute_retry_delay ─────────────────────────────────────

def test_retry_after_takes_priority_over_backoff():
    exc = RateLimitError("x", retry_after=90)
    assert compute_retry_delay(1, 1.0, exc) == 90.0


def test_retry_after_capped():
    exc = RateLimitError("x", retry_after=99999)
    assert compute_retry_delay(1, 1.0, exc) == RETRY_AFTER_CAP


def test_exponential_backoff_growth():
    assert 1.0 <= compute_retry_delay(1, 1.0) <= 1.1
    assert 2.0 <= compute_retry_delay(2, 1.0) <= 2.2
    assert 4.0 <= compute_retry_delay(3, 1.0) <= 4.4
    assert 16.0 <= compute_retry_delay(5, 1.0) <= 17.6


def test_backoff_capped_at_max_delay():
    delay = compute_retry_delay(10, 10.0)
    assert 60.0 <= delay <= 66.0


def test_zero_base_delay_yields_zero():
    assert compute_retry_delay(3, 0.0) == 0.0


def test_negative_base_delay_clamped():
    assert compute_retry_delay(1, -5.0) == 0.0


def test_no_exception_uses_backoff():
    assert 1.0 <= compute_retry_delay(1, 1.0, None) <= 1.1


# ── is_retryable ────────────────────────────────────────────

@pytest.mark.parametrize("status", sorted(RETRYABLE_HTTP_STATUS))
def test_is_retryable_transient_api_errors(status):
    assert is_retryable(classify_http_error(status, "x"))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 418])
def test_is_retryable_permanent_api_errors(status):
    assert not is_retryable(classify_http_error(status, "x"))


def test_is_retryable_connection_errors():
    assert is_retryable(httpx.ConnectError("refused"))
    assert is_retryable(ConnectionError("down"))
    assert is_retryable(httpx.ReadTimeout("slow"))


def test_is_retryable_parse_and_timeout_errors():
    assert is_retryable(json.JSONDecodeError("bad", "doc", 0))
    assert is_retryable(TimeoutError("t"))
    assert is_retryable(asyncio.TimeoutError())


def test_is_retryable_unknown_exception_false():
    assert not is_retryable(ValueError("nope"))
    assert not is_retryable(KeyError("k"))


# ── _check_response ─────────────────────────────────────────

def test_check_response_200_ok():
    _check_response(httpx.Response(200))


def test_check_response_429_with_retry_after_header():
    resp = httpx.Response(429, headers={"Retry-After": "12"}, content=b"rate limited")
    with pytest.raises(RateLimitError) as ei:
        _check_response(resp)
    assert ei.value.status_code == 429
    assert ei.value.retry_after == 12.0


def test_check_response_429_without_retry_after_header():
    resp = httpx.Response(429, content=b"rate limited")
    with pytest.raises(RateLimitError) as ei:
        _check_response(resp)
    assert ei.value.retry_after is None


def test_check_response_401_maps_to_auth_error():
    with pytest.raises(AuthError):
        _check_response(httpx.Response(401, content=b"bad key"))


def test_check_response_404_maps_to_not_found_error():
    with pytest.raises(NotFoundError):
        _check_response(httpx.Response(404, content=b"no model"))


def test_check_response_500_maps_to_server_error():
    with pytest.raises(ServerError):
        _check_response(httpx.Response(500, content=b"boom"))


def test_check_response_truncates_body():
    resp = httpx.Response(500, content=b"x" * 2000)
    with pytest.raises(ServerError) as ei:
        _check_response(resp)
    assert len(ei.value.message) == 500


# ── format_user_error ───────────────────────────────────────

def test_format_user_error_connection_keeps_keyword():
    msg = format_user_error(httpx.ConnectError("connection refused"))
    assert msg.startswith("连接错误:")


def test_format_user_error_api_keeps_keyword():
    msg = format_user_error(classify_http_error(500, "boom"))
    assert "API 调用出错" in msg


def test_format_user_error_auth_hint_actionable():
    msg = format_user_error(classify_http_error(401, "bad key"))
    assert "CHAT_API_KEY" in msg


def test_format_user_error_rate_limit_includes_retry_after():
    msg = format_user_error(RateLimitError("slow down", retry_after=30))
    assert "30 秒后重试" in msg


def test_format_user_error_timeout():
    msg = format_user_error(TimeoutError("read timed out"))
    assert "请求超时" in msg


def test_format_user_error_lowercased_keyword_compat():
    for exc in (classify_http_error(503, "x"), TimeoutError("t"), httpx.ConnectError("c")):
        assert is_network_error(format_user_error(exc), None), type(exc).__name__


# ── is_network_error（策略 0：结构化识别） ───────────────────

def test_is_network_error_structured_transient_true():
    assert is_network_error("", RateLimitError("x"))
    assert is_network_error("", classify_http_error(503, "x"))


def test_is_network_error_structured_permanent_false():
    assert not is_network_error("", classify_http_error(401, "x"))
    assert not is_network_error("", classify_http_error(400, "x"))


def test_is_network_error_structured_connection_true():
    assert is_network_error("", httpx.ConnectError("refused"))


def test_is_network_error_keyword_content_still_works():
    assert is_network_error("抱歉，API 调用出错: API error 500: boom", None)
    assert is_network_error("连接错误: refused", None)
    assert not is_network_error("正常回复内容", None)


# ── retry_api_call_async ────────────────────────────────────

@pytest.fixture
def no_interrupt(monkeypatch):
    async def _false():
        return False

    async def _wait(sec):
        _wait.values.append(sec)
        return False

    _wait.values = []
    monkeypatch.setattr(retry_mod, "is_interrupted_async", _false)
    monkeypatch.setattr(retry_mod, "wait_for_interrupt_async", _wait)
    return _wait


async def test_retry_success_first_attempt(no_interrupt):
    calls = []

    async def api():
        calls.append(1)
        return ("r", "c", {"input": 1, "output": 1}, [])

    result = await retry_api_call_async(api, silent=True)
    assert result[1] == "c"
    assert len(calls) == 1
    assert _wait_values(no_interrupt) == []


async def test_retry_transient_error_then_success(no_interrupt):
    calls = []

    async def api():
        calls.append(1)
        if len(calls) < 2:
            raise RateLimitError("slow down", retry_after=0.05)
        return ("r", "ok", {"input": 0, "output": 0}, [])

    result = await retry_api_call_async(api, silent=True)
    assert result[1] == "ok"
    assert len(calls) == 2
    # Retry-After（0.05s）优先于指数退避
    assert _wait_values(no_interrupt) == [pytest.approx(0.05)]


async def test_retry_permanent_error_raises_immediately(no_interrupt):
    calls = []

    async def api():
        calls.append(1)
        raise classify_http_error(401, "bad key")

    with pytest.raises(AuthError):
        await retry_api_call_async(api, silent=True)
    assert len(calls) == 1
    assert _wait_values(no_interrupt) == []


async def test_retry_exhausted_returns_error_tuple(no_interrupt):
    calls = []

    async def api():
        calls.append(1)
        raise classify_http_error(500, "always down")

    result = await retry_api_call_async(
        api, silent=True, override_max_retries=2, fixed_delay_sec=0)
    reasoning, content, usage, tool_calls = result
    assert reasoning == ""
    assert "API 调用出错" in content
    assert usage == {"input": 0, "output": 0}
    assert tool_calls == []
    assert len(calls) == 2


async def test_retry_fixed_delay_overrides_backoff(no_interrupt, monkeypatch):
    monkeypatch.setattr(retry_mod, "RETRY_BASE_SEC", 999.0)
    calls = []

    async def api():
        calls.append(1)
        if len(calls) < 2:
            # 即使异常带 retry_after，显式 fixed_delay_sec 也优先
            raise RateLimitError("x", retry_after=88)
        return ("", "done", {"input": 0, "output": 0}, [])

    result = await retry_api_call_async(api, silent=True, fixed_delay_sec=0)
    assert result[1] == "done"
    assert _wait_values(no_interrupt) == [0.0]


async def test_retry_exponential_backoff_delays(no_interrupt, monkeypatch):
    monkeypatch.setattr(retry_mod, "RETRY_BASE_SEC", 1.0)
    calls = []

    async def api():
        calls.append(1)
        if len(calls) < 3:
            raise classify_http_error(503, "overloaded")
        return ("", "done", {"input": 0, "output": 0}, [])

    await retry_api_call_async(api, silent=True)
    w1, w2 = _wait_values(no_interrupt)
    assert 1.0 <= w1 <= 1.1
    assert 2.0 <= w2 <= 2.2


async def test_retry_connection_error_exhausted_message(no_interrupt):
    async def api():
        raise httpx.ConnectError("connection refused")

    result = await retry_api_call_async(
        api, silent=True, override_max_retries=1)
    assert result[1].startswith("连接错误:")


async def test_retry_non_retryable_generic_exception_propagates(no_interrupt):
    async def api():
        raise ValueError("bug")

    with pytest.raises(ValueError):
        await retry_api_call_async(api, silent=True)


async def test_retry_keyboard_interrupt_returns_empty(no_interrupt):
    async def api():
        raise KeyboardInterrupt()

    result = await retry_api_call_async(api, silent=True)
    assert result[1] == "(已中断，无内容)"


def _wait_values(wait_stub):
    return wait_stub.values
