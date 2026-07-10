"""测试 src.api.model_async._retry_on_parse_failure_async — 解析重试逻辑。

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- Mock _retry_api_call_async 以控制返回值，验证重试触发/用尽逻辑
- 每个测试函数关注单个场景，遵循"一个断言概念一个测试"
"""

import sys
import asyncio
import pytest
import importlib.util
import importlib.abc
import importlib.machinery
from unittest.mock import MagicMock, AsyncMock

# ── 在导入被测试模块前 mock 所有外部依赖 ────────────────────────────────

class _MockPackageLoader(importlib.abc.Loader):
    """使 MagicMock 表现为合法的 Python 包，支持相对导入。"""
    def create_module(self, spec):
        return MagicMock()
    def exec_module(self, module):
        pass


_MOCK_PACKAGE_NAMES = [
    'src', 'src.api', 'src.api.stream', 'src.ui', 'src.tools',
    'src.core',
]
_ORIGINAL_PACKAGES: dict[str, object] = {}
for _pkg_name in _MOCK_PACKAGE_NAMES:
    _ORIGINAL_PACKAGES[_pkg_name] = sys.modules.get(_pkg_name)
    _spec = importlib.machinery.ModuleSpec(_pkg_name, _MockPackageLoader(), is_package=True)
    sys.modules[_pkg_name] = importlib.util.module_from_spec(_spec)

# ★ _CONNECTION_ERRORS 必须是真实异常类的元组，否则 except 子句会报
#   "catching classes that do not inherit from BaseException is not allowed"
_CONNECTION_ERRORS = (ConnectionError, OSError)

# Mock 叶子模块
_MOCK_MODULES = {
    'src.api.client_async': MagicMock(
        chat_completions_async=AsyncMock(),
        RateLimitError=Exception,
        APIError=Exception,
        _CONNECTION_ERRORS=_CONNECTION_ERRORS,
    ),
    'src.api.tokens': MagicMock(estimate_tokens=MagicMock(return_value=0)),
    'src.api.interrupt_async': MagicMock(
        is_interrupted_async=AsyncMock(return_value=False),
        wait_for_interrupt_async=AsyncMock(return_value=False),
    ),
    'src.api.stats': MagicMock(),
    'src.api.json_repair': MagicMock(),
    'src.api._tool_parse_utils': MagicMock(),
    'src.api.stream_parse': MagicMock(),
    'src.api.stream.pipeline_async': MagicMock(stream_call_async=AsyncMock()),
    'src.api.adapters': MagicMock(
        OpenAICompatAdapter=MagicMock,
        DeepSeekAdapter=MagicMock,
    ),
    'src.api._model_loops': MagicMock(),
    'src.ui.colors': MagicMock(DIM='\x1b[2m', RESET='\x1b[0m', YELLOW='\x1b[33m'),
    'src.ui._lock': MagicMock(locked_print=MagicMock()),
    'src.config': MagicMock(MODEL='test-model', MAX_RETRIES=1, RETRY_BASE_SEC=1),
    'src.core.constants': MagicMock(YELLOW='\x1b[33m', RESET='\x1b[0m'),
    'src.core': MagicMock(),
}

_ORIGINAL_MODULES: dict[str, object] = {}
for mod_name in _MOCK_MODULES:
    _ORIGINAL_MODULES[mod_name] = sys.modules.get(mod_name)

for mod_name, mod in _MOCK_MODULES.items():
    sys.modules[mod_name] = mod

_SCRIPT_DIR = '/home/DeepSeek-cli/src/api'

# ── 前置加载 _retry.py 和 _adapter_manager.py ───────────────────────
# model_async.py 现在从 ._retry 和 ._adapter_manager 导入，
# 需先加载这两个模块到 sys.modules 中供 model_async.py 使用。
for _dep_name, _dep_file in [
    ('src.api._retry', '_retry.py'),
    ('src.api._adapter_manager', '_adapter_manager.py'),
]:
    _dep_spec = importlib.util.spec_from_file_location(
        _dep_name, f'{_SCRIPT_DIR}/{_dep_file}',
    )
    _dep_mod = importlib.util.module_from_spec(_dep_spec)
    sys.modules[_dep_name] = _dep_mod
    _dep_spec.loader.exec_module(_dep_mod)

# ── 直接加载 model_async.py ────────────────────────────────────────────
_model_async_spec = importlib.util.spec_from_file_location(
    'src.api.model_async', f'{_SCRIPT_DIR}/model_async.py',
)
_model_async_module = importlib.util.module_from_spec(_model_async_spec)
sys.modules['src.api.model_async'] = _model_async_module
_model_async_spec.loader.exec_module(_model_async_module)

# ── 清理 mock ──────────────────────────────────────────────────────────
for mod_name in list(_MOCK_MODULES.keys()):
    orig = _ORIGINAL_MODULES.get(mod_name)
    if orig is not None:
        sys.modules[mod_name] = orig
    else:
        sys.modules.pop(mod_name, None)
sys.modules.pop('src.api.model_async', None)
sys.modules.pop('src.api._retry', None)
sys.modules.pop('src.api._adapter_manager', None)
# ★ 确保 src.api.json_repair 从正确路径加载：如果缓存中的模块来自
#   旧路径（如 editable install 的 /home/simple/chat/src），强制清除
#   以让 Python 从当前 CWD 重新导入。
_cached_jr = sys.modules.pop('src.api.json_repair', None)
if _cached_jr is not None:
    _cached_file = getattr(_cached_jr, '__file__', '')
    if '/home/DeepSeek-cli/' not in _cached_file:
        _ORIGINAL_MODULES['src.api.json_repair'] = None  # 不恢复旧路径版本
for _pkg_name in _MOCK_PACKAGE_NAMES:
    orig = _ORIGINAL_PACKAGES.get(_pkg_name)
    if orig is not None:
        sys.modules[_pkg_name] = orig
    else:
        sys.modules.pop(_pkg_name, None)

# ── 提取被测试符号 ─────────────────────────────────────────────────────
_retry_on_parse_failure_async = _model_async_module._retry_on_parse_failure_async


# ═══════════════════════════════════════════════════════════════════════════
# 辅助：构建 mock _retry_api_call_async
# ═══════════════════════════════════════════════════════════════════════════

def _make_mock_retry(return_values, call_counter):
    """创建一个 mock 的 _retry_api_call_async 函数。

    注意：_retry_api_call_async 接收 api_func 作为第一参数，然后调用
    api_func(*api_args)。本 mock 模拟完整链路——当被 _retry_on_parse_failure_async
    调用时，它实际上作为 api_func 传递给真实的 _retry_api_call_async，
    _retry_api_call_async 会用 api_args（默认为 ()）调用本 mock。

    Args:
        return_values: 按调用顺序返回的结果列表
        call_counter: 可变计数器（如 [0]）

    Returns:
        async callable
    """
    async def mock_retry(*args, **kwargs):
        idx = call_counter[0]
        call_counter[0] += 1
        if idx < len(return_values):
            return return_values[idx]
        return return_values[-1]
    return mock_retry


# ═══════════════════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryOnParseFailureAsync:
    """测试 _retry_on_parse_failure_async 解析重试逻辑。"""

    @pytest.mark.asyncio
    async def test_no_parse_failure_no_retry(self):
        """无解析失败：不触发重试，直接返回第一次结果。"""
        expected = ("reasoning", "content", {"input": 10, "output": 20}, [])
        counter = [0]
        mock_retry = _make_mock_retry([expected], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        assert result == expected
        assert counter[0] == 1

    @pytest.mark.asyncio
    async def test_parse_failure_triggers_retry(self):
        """有 _parse_failed_ids：触发重试，返回第二次结果。"""
        first_result = (
            "reasoning", "content",
            {"input": 10, "output": 20, "_parse_failed_ids": ["call1"]},
            [{"id": "call2", "name": "good_tool", "arguments": {"x": 1}}],
        )
        second_result = (
            "reasoning2", "content2",
            {"input": 5, "output": 15},
            [{"id": "call1", "name": "retry_tool", "arguments": {"y": 2}}],
        )
        counter = [0]
        mock_retry = _make_mock_retry([first_result, second_result], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        reasoning, content, usage, tool_calls = result
        assert reasoning == "reasoning2"
        assert content == "content2"
        assert usage == {"input": 5, "output": 15}
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call1"
        assert counter[0] == 2

    @pytest.mark.asyncio
    async def test_parse_failure_exhausted_retry(self):
        """两次都有 _parse_failed_ids：重试用尽，_parse_failed_ids 已清除。"""
        failed_result = (
            "reasoning", "content",
            {"input": 10, "output": 20, "_parse_failed_ids": ["call1", "call2"]},
            [],
        )
        counter = [0]
        mock_retry = _make_mock_retry([failed_result, failed_result], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        reasoning, content, usage, tool_calls = result
        assert "_parse_failed_ids" not in usage
        assert counter[0] == 2  # 1 次初始 + 1 次重试

    @pytest.mark.asyncio
    async def test_retry_success_clears_failed_ids(self):
        """重试成功后，返回结果中不含 _parse_failed_ids。"""
        first_result = (
            "reasoning", "content",
            {"input": 10, "output": 20, "_parse_failed_ids": ["call1"]},
            [{"id": "call2", "name": "good_tool", "arguments": {"x": 1}}],
        )
        second_result = (
            "reasoning2", "content2",
            {"input": 5, "output": 15},
            [{"id": "call1", "name": "retry_tool", "arguments": {"y": 2}}],
        )
        counter = [0]
        mock_retry = _make_mock_retry([first_result, second_result], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        reasoning, content, usage, tool_calls = result
        assert "_parse_failed_ids" not in usage

    @pytest.mark.asyncio
    async def test_empty_failed_ids_list_no_retry(self):
        """_parse_failed_ids 为空列表时不触发重试。"""
        expected = (
            "reasoning", "content",
            {"input": 10, "output": 20, "_parse_failed_ids": []},
            [{"id": "call1", "name": "tool", "arguments": {"x": 1}}],
        )
        counter = [0]
        mock_retry = _make_mock_retry([expected], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        reasoning, content, usage, tool_calls = result
        assert "_parse_failed_ids" not in usage
        assert counter[0] == 1

    @pytest.mark.asyncio
    async def test_usage_without_parse_failed_ids_key_no_retry(self):
        """usage 中无 _parse_failed_ids 键时不触发重试。"""
        expected = (
            "reasoning", "content",
            {"input": 10, "output": 20},
            [{"id": "call1", "name": "tool", "arguments": {"x": 1}}],
        )
        counter = [0]
        mock_retry = _make_mock_retry([expected], counter)

        result = await _retry_on_parse_failure_async(
            mock_retry,
            silent=True,
        )
        assert result == expected
        assert counter[0] == 1


@pytest.fixture(autouse=True)
def cleanup_sys_modules():
    """文件级清理：恢复 sys.modules 中被本文件污染的模块。"""
    saved = sys.modules.copy()
    yield
    for mod_name in list(sys.modules.keys()):
        if mod_name not in saved:
            del sys.modules[mod_name]
