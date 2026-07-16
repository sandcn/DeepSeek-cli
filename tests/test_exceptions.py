"""Tests for src/core/exceptions.py – exception classification."""

from __future__ import annotations

import asyncio

import pytest

from src.core.exceptions import (
    FatalError,
    NonFatalError,
    TransientError,
    is_fatal_exception,
    is_network_error,
)


# ---------------------------------------------------------------------------
# Fatal exceptions
# ---------------------------------------------------------------------------

class TestFatalDetection:
    """Verify that clearly fatal conditions are detected."""

    def test_memory_error_is_fatal(self) -> None:
        assert is_fatal_exception(MemoryError()) is True

    def test_system_exit_is_fatal(self) -> None:
        assert is_fatal_exception(SystemExit(1)) is True

    def test_fatal_error_subclass_is_fatal(self) -> None:
        assert is_fatal_exception(FatalError("disk corrupted")) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "[Errno 28] No space left on device",
            "write failed: [Errno 28]",
            "[Errno 12] Cannot allocate memory",
            "mmap failed [Errno 12]",
        ],
    )
    def test_os_error_with_fatal_errno(self, msg: str) -> None:
        assert is_fatal_exception(OSError(msg)) is True


# ---------------------------------------------------------------------------
# Non-fatal exceptions
# ---------------------------------------------------------------------------

class TestNonFatalDetection:
    """Verify that recoverable errors are NOT treated as fatal."""

    def test_connection_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(ConnectionError("reset")) is False

    def test_timeout_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(TimeoutError("timed out")) is False

    def test_asyncio_timeout_is_non_fatal(self) -> None:
        assert is_fatal_exception(asyncio.TimeoutError()) is False

    def test_value_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(ValueError("bad input")) is False

    def test_key_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(KeyError("missing")) is False

    def test_non_fatal_error_subclass_is_non_fatal(self) -> None:
        assert is_fatal_exception(NonFatalError("minor issue")) is False

    def test_transient_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(TransientError("rate limited")) is False

    def test_generic_exception_is_non_fatal(self) -> None:
        """Unknown exception types default to non-fatal (conservative)."""
        assert is_fatal_exception(RuntimeError("something")) is False

    def test_generic_exception_subclass_is_non_fatal(self) -> None:
        """Custom exception without explicit classification defaults to non-fatal."""

        class MyPluginError(Exception):
            pass

        assert is_fatal_exception(MyPluginError("oops")) is False


# ---------------------------------------------------------------------------
# CancelledError special case
# ---------------------------------------------------------------------------

class TestCancelledError:
    """CancelledError must NEVER be classified as fatal."""

    def test_cancelled_error_is_not_fatal(self) -> None:
        assert is_fatal_exception(asyncio.CancelledError()) is False


# ---------------------------------------------------------------------------
# OSError edge cases
# ---------------------------------------------------------------------------

class TestOSErrorEdgeCases:
    """OSError without fatal errno patterns should be non-fatal."""

    def test_permission_denied_is_non_fatal(self) -> None:
        assert is_fatal_exception(PermissionError("[Errno 13] Permission denied")) is False

    def test_file_not_found_is_non_fatal(self) -> None:
        assert is_fatal_exception(FileNotFoundError("[Errno 2] No such file")) is False

    def test_generic_os_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(OSError("unknown error")) is False


# ---------------------------------------------------------------------------
# Hierarchy sanity checks
# ---------------------------------------------------------------------------

class TestHierarchy:
    """Ensure exception class relationships are correct."""

    def test_transient_is_non_fatal(self) -> None:
        assert issubclass(TransientError, NonFatalError)

    def test_non_fatal_is_exception(self) -> None:
        assert issubclass(NonFatalError, Exception)

    def test_fatal_is_exception(self) -> None:
        assert issubclass(FatalError, Exception)

    def test_fatal_is_not_non_fatal(self) -> None:
        """FatalError and NonFatalError are separate branches."""
        assert not issubclass(FatalError, NonFatalError)
        assert not issubclass(NonFatalError, FatalError)


# ---------------------------------------------------------------------------
# Network error detection
# ---------------------------------------------------------------------------

class TestIsNetworkError:
    """Verify is_network_error() detection strategies."""

    # ── 内容关键词匹配 ──────────────────────────────────

    def test_content_connection_error(self) -> None:
        """中文'连接错误'关键词应被检测"""
        assert is_network_error("连接错误: 网络不可达", None) is True

    def test_content_api_call_error(self) -> None:
        """中文'API 调用出错'关键词应被检测"""
        assert is_network_error("抱歉，API 调用出错: 超时", None) is True

    def test_content_stream_idle_timeout(self) -> None:
        """中文'流空闲超时'关键词应被检测"""
        assert is_network_error("流空闲超时: 连接已断开", None) is True

    def test_content_normal_text(self) -> None:
        """正常回复内容不应被检测为网络错误"""
        assert is_network_error("这是正常的回复内容", None) is False

    def test_content_empty_string(self) -> None:
        """空字符串不应被检测为网络错误"""
        assert is_network_error("", None) is False

    def test_content_none(self) -> None:
        """None 内容不应被检测为网络错误"""
        assert is_network_error(None, None) is False

    def test_content_keyword_in_long_string(self) -> None:
        """关键词在长字符串中间也能匹配"""
        assert is_network_error(
            "前面有内容连接错误中间还有内容", None
        ) is True

    def test_content_partial_match(self) -> None:
        """部分匹配不应误判——'超时'关键词应匹配各种超时场景"""
        assert is_network_error("请求超时，请稍后重试", None) is True

    # ── 异常类型匹配 ────────────────────────────────────

    def test_exception_connection_error(self) -> None:
        """ConnectionError 应被检测"""
        assert is_network_error("", ConnectionError("连接被拒绝")) is True

    def test_exception_timeout_error(self) -> None:
        """TimeoutError 应被检测"""
        assert is_network_error("", TimeoutError("timed out")) is True

    def test_exception_asyncio_timeout(self) -> None:
        """asyncio.TimeoutError 应被检测"""
        assert is_network_error("", asyncio.TimeoutError()) is True

    def test_exception_value_error(self) -> None:
        """ValueError 不应被检测为网络错误"""
        assert is_network_error("", ValueError("参数错误")) is False

    def test_exception_key_error(self) -> None:
        """KeyError 不应被检测为网络错误"""
        assert is_network_error("", KeyError("missing")) is False

    def test_exception_runtime_error(self) -> None:
        """RuntimeError 不应被检测为网络错误"""
        assert is_network_error("", RuntimeError("未知错误")) is False

    # ── 组合场景 ────────────────────────────────────────

    def test_both_content_and_exception_match(self) -> None:
        """内容和异常同时匹配"""
        assert is_network_error("连接错误", ConnectionError()) is True

    def test_content_match_exception_not(self) -> None:
        """内容匹配但异常不匹配也返回 True（OR 逻辑）"""
        assert is_network_error("连接错误", ValueError("参数错误")) is True

    def test_neither_matches(self) -> None:
        """内容和异常都不匹配"""
        assert is_network_error("正常回复", ValueError("参数错误")) is False

    def test_empty_both(self) -> None:
        """空内容和无异常"""
        assert is_network_error("", None) is False
