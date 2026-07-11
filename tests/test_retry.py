"""测试 src.api._retry.retry_api_call_async — 重试策略。

测试覆盖：
- 默认行为（指数退避 + MAX_RETRIES=3）不变
- override_max_retries 参数
- fixed_delay_sec 参数（固定间隔、无抖动、跳过 RateLimit 额外等待）
- 组合参数
- 连接错误场景
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── 在导入被测试模块前 mock 所有外部依赖 ────────────────────────────────

_CONNECTION_ERRORS = (ConnectionError, OSError)

_MOCK_MODULES = {
    'src.api.client_async': MagicMock(
        RateLimitError=type('RateLimitError', (Exception,), {}),
        APIError=type('APIError', (Exception,), {}),
        _CONNECTION_ERRORS=_CONNECTION_ERRORS,
    ),
    'src.api.interrupt_async': MagicMock(
        is_interrupted_async=AsyncMock(return_value=False),
        wait_for_interrupt_async=AsyncMock(return_value=False),
    ),
    'src.ui._lock': MagicMock(locked_print=MagicMock()),
    'src.config': MagicMock(MAX_RETRIES=3, RETRY_BASE_SEC=1),
    'src.core.constants': MagicMock(YELLOW='\x1b[33m', RESET='\x1b[0m'),
}

_ORIGINAL_MODULES: dict[str, object] = {}
for mod_name, mod in _MOCK_MODULES.items():
    _ORIGINAL_MODULES[mod_name] = sys.modules.get(mod_name)
    sys.modules[mod_name] = mod

# ── 加载被测试模块 ───────────────────────────────────────────────────
import importlib.util
import importlib.abc
import importlib.machinery

_SCRIPT_DIR = '/home/DeepSeek-cli/src/api'

# 加载 _retry.py
_retry_spec = importlib.util.spec_from_file_location(
    'src.api._retry', f'{_SCRIPT_DIR}/_retry.py',
)
# 不设 loader 的 _MockPackageLoader，因为 _retry.py 不需要 package
_retry_module = importlib.util.module_from_spec(_retry_spec)
sys.modules['src.api._retry'] = _retry_module  # 保留在 sys.modules 中供 patch 使用
_retry_spec.loader.exec_module(_retry_module)

# 提取被测试符号
retry_api_call_async = _retry_module.retry_api_call_async

# ── 清理 mock — 恢复 sys.modules ────────────────────────────────────
for mod_name in list(_MOCK_MODULES.keys()):
    orig = _ORIGINAL_MODULES.get(mod_name)
    if orig is not None:
        sys.modules[mod_name] = orig
    else:
        sys.modules.pop(mod_name, None)
# 不 pop src.api._retry，因为测试需要 patch.object 引用它


# ═══════════════════════════════════════════════════════════════════════════
# 测试辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_failing_api_func(fail_count: int, exc=httpx.HTTPStatusError):
    """创建一个 async callable，前 fail_count 次抛出 exc，之后返回正常结果。

    Args:
        fail_count: 失败次数（抛出异常的次数）
        exc: 要抛出的异常类

    Returns:
        (async_callable, call_counter)
    """
    counter = [0]

    async def api_func(*args, **kwargs):
        idx = counter[0]
        counter[0] += 1
        if idx < fail_count:
            if exc is httpx.HTTPStatusError:
                response = MagicMock(status_code=500, spec=httpx.Response)
                raise exc(
                    "Internal Server Error",
                    request=MagicMock(spec=httpx.Request),
                    response=response,
                )
            elif exc is httpx.RequestError:
                raise exc("Connection refused", request=MagicMock(spec=httpx.Request))
            elif exc is json.JSONDecodeError:
                raise exc("Expecting value", "", 0)
            else:
                raise exc(str(exc))
        return ("reasoning", "content", {"input": 10, "output": 20}, [])

    return api_func, counter


def _make_failing_api_func_connection(fail_count: int, exc_class=ConnectionError):
    """创建一个抛出连接错误的 async callable。"""
    counter = [0]

    async def api_func(*args, **kwargs):
        idx = counter[0]
        counter[0] += 1
        if idx < fail_count:
            raise exc_class("Connection refused")
        return ("reasoning", "content", {"input": 10, "output": 20}, [])

    return api_func, counter


# ============================================================
# Mock RateLimitError — 用于测试固定延迟模式下跳过 RateLimit 额外等待
# ============================================================
class MockRateLimitError(Exception):
    pass


@pytest.fixture(autouse=True)
def cleanup_sys_modules():
    """文件级清理：恢复 sys.modules 中被本文件污染的模块。"""
    saved = sys.modules.copy()
    yield
    for mod_name in list(sys.modules.keys()):
        if mod_name not in saved:
            del sys.modules[mod_name]


# ═══════════════════════════════════════════════════════════════════════════
# 测试 — 默认行为
# ═══════════════════════════════════════════════════════════════════════════

class TestDefaultBehavior:
    """默认参数行为不变（指数退避 + MAX_RETRIES=3）。"""

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """首次调用成功，不触发重试。"""
        api_func, counter = _make_failing_api_func(0)
        result = await retry_api_call_async(api_func, silent=True)
        assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
        assert counter[0] == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """首次失败，第2次成功（重试1次）。"""
        api_func, counter = _make_failing_api_func(1)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False):
            result = await retry_api_call_async(api_func, silent=True)
            assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
            assert counter[0] == 2

    @pytest.mark.asyncio
    async def test_default_max_retries_exhausted(self):
        """默认 MAX_RETRIES=3，连续失败3次后返回错误结果。"""
        api_func, counter = _make_failing_api_func(3)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False):
            result = await retry_api_call_async(api_func, silent=True)
            reasoning, content, usage, tool_calls = result
            assert "出错" in content or "抱歉" in content
            assert counter[0] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 测试 — override_max_retries
# ═══════════════════════════════════════════════════════════════════════════

class TestOverrideMaxRetries:
    """override_max_retries 参数覆盖 MAX_RETRIES。"""

    @pytest.mark.asyncio
    async def test_override_to_5(self):
        """override_max_retries=5，最多尝试5次后返回错误。"""
        api_func, counter = _make_failing_api_func(5)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False):
            result = await retry_api_call_async(api_func, silent=True, override_max_retries=5)
            reasoning, content, usage, tool_calls = result
            assert "出错" in content or "抱歉" in content
            assert counter[0] == 5

    @pytest.mark.asyncio
    async def test_override_to_1_no_retry(self):
        """override_max_retries=1，首次失败即返回错误（不重试）。"""
        api_func, counter = _make_failing_api_func(1)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False):
            result = await retry_api_call_async(api_func, silent=True, override_max_retries=1)
            reasoning, content, usage, tool_calls = result
            assert "出错" in content or "抱歉" in content
            assert counter[0] == 1

    @pytest.mark.asyncio
    async def test_override_to_2_then_success(self):
        """override_max_retries=2，第1次失败，第2次成功。"""
        api_func, counter = _make_failing_api_func(1)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False):
            result = await retry_api_call_async(api_func, silent=True, override_max_retries=2)
            assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
            assert counter[0] == 2

    @pytest.mark.asyncio
    async def test_override_to_0_clamped(self):
        """override_max_retries=0 时 clamp 到至少 1 次尝试。"""
        api_func, counter = _make_failing_api_func(0)
        result = await retry_api_call_async(api_func, silent=True, override_max_retries=0)
        assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
        assert counter[0] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 测试 — fixed_delay_sec
# ═══════════════════════════════════════════════════════════════════════════

class TestFixedDelay:
    """fixed_delay_sec 固定延迟行为。"""

    @pytest.mark.asyncio
    async def test_fixed_delay_success(self):
        """fixed_delay_sec=10，首次失败，第2次成功，等待参数为10。"""
        api_func, counter = _make_failing_api_func(1)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False) as mock_wait:
            result = await retry_api_call_async(api_func, silent=True, fixed_delay_sec=10.0)
            assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
            assert counter[0] == 2
            # 验证 wait_for_interrupt_async 被调用且参数为 10.0
            mock_wait.assert_awaited_once()
            args, _ = mock_wait.call_args
            assert args[0] == 10.0

    @pytest.mark.asyncio
    async def test_fixed_delay_no_jitter(self):
        """固定延迟模式下 random.uniform 不应被调用（无抖动）。"""
        api_func, counter = _make_failing_api_func(1)
        with (
            patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False),
            patch('random.uniform') as mock_uniform,
        ):
            await retry_api_call_async(api_func, silent=True, fixed_delay_sec=10.0)
            mock_uniform.assert_not_called()

    @pytest.mark.asyncio
    async def test_fixed_delay_with_rate_limit(self):
        """RateLimit 异常时，固定延迟仍使用 fixed_delay_sec（跳过 RateLimit 额外等待）。"""
        api_func, counter = _make_failing_api_func(1, exc=MockRateLimitError)
        with (
            patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False) as mock_wait,
            patch.object(_retry_module, 'RateLimitError', MockRateLimitError),
        ):
            await retry_api_call_async(api_func, silent=True, fixed_delay_sec=10.0)
            mock_wait.assert_awaited_once()
            args, _ = mock_wait.call_args
            assert args[0] == 10.0  # 固定 10s，不触发 max(wait, 10*attempt)

    @pytest.mark.asyncio
    async def test_fixed_delay_exhausted(self):
        """fixed_delay_sec=10 + override_max_retries=5，连续失败5次后耗尽。"""
        api_func, counter = _make_failing_api_func(5)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False) as mock_wait:
            result = await retry_api_call_async(
                api_func, silent=True,
                fixed_delay_sec=10.0, override_max_retries=5,
            )
            reasoning, content, usage, tool_calls = result
            assert "出错" in content or "抱歉" in content
            assert counter[0] == 5
            # 前4次失败触发等待（第5次耗尽，不等待）
            assert mock_wait.await_count == 4
            for call_args in mock_wait.call_args_list:
                args, _ = call_args
                assert args[0] == 10.0


# ═══════════════════════════════════════════════════════════════════════════
# 测试 — 连接错误
# ═══════════════════════════════════════════════════════════════════════════

class TestConnectionErrors:
    """连接错误场景下的重试行为。"""

    @pytest.mark.asyncio
    async def test_connection_error_fixed_delay(self):
        """连接错误 + fixed_delay_sec，等待参数为固定值。"""
        api_func, counter = _make_failing_api_func_connection(1, ConnectionError)
        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False) as mock_wait:
            result = await retry_api_call_async(api_func, silent=True, fixed_delay_sec=10.0)
            assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
            mock_wait.assert_awaited_once()
            args, _ = mock_wait.call_args
            assert args[0] == 10.0

    @pytest.mark.asyncio
    async def test_connection_error_default(self):
        """连接错误 + 默认行为（指数退避+抖动），等待参数遵循退避算法。"""
        api_func, counter = _make_failing_api_func_connection(1, ConnectionError)
        with (
            patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=False) as mock_wait,
            patch('random.uniform', return_value=1.0) as mock_uniform,
        ):
            result = await retry_api_call_async(api_func, silent=True)
            assert result == ("reasoning", "content", {"input": 10, "output": 20}, [])
            mock_wait.assert_awaited_once()
            args, _ = mock_wait.call_args
            # RETRY_BASE_SEC(1) * 2^(1-1) * 1.0 = 1.0
            assert args[0] == 1.0
            mock_uniform.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# 测试 — 中断处理
# ═══════════════════════════════════════════════════════════════════════════

class TestInterruptHandling:
    """中断场景下的行为。"""

    @pytest.mark.asyncio
    async def test_interrupted_during_wait(self):
        """重试等待期间被中断，返回已中断结果。"""
        api_func, counter = _make_failing_api_func(1)

        with patch.object(_retry_module, 'wait_for_interrupt_async', new_callable=AsyncMock, return_value=True):
            result = await retry_api_call_async(api_func, silent=True, fixed_delay_sec=10.0)
            reasoning, content, usage, tool_calls = result
            assert "已中断" in content
            assert counter[0] == 1  # 只调用了1次（未重试）
