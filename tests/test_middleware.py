"""中间件模块测试

覆盖模块:
  - _AsyncObservabilityMiddleware  — 异步可观测性中间件
  - _AuditLogMiddleware            — 审计日志中间件
  - _InterruptCheckMiddleware      — 中断检查中间件
  - _ToolRegistryAdapter           — 工具注册表适配器
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_agent():
    """创建一个最小化的 mock Agent，仅包含中间件访问的属性"""
    agent = MagicMock()
    agent.model = "test-model"
    agent.messages = [{"role": "user", "content": "hello world"}]
    agent.tools = [{"name": "test_tool"}]
    return agent


@pytest.fixture
def mock_ctx(mock_agent):
    """创建 PipelineContext，用 mock agent 初始化"""
    from src.core.pipeline import PipelineContext

    ctx = PipelineContext(mock_agent)
    return ctx


@pytest.fixture
def ctx_with_usage(mock_agent):
    """创建带有 usage 数据的 PipelineContext"""
    from src.core.pipeline import PipelineContext

    ctx = PipelineContext(mock_agent)
    ctx.usage = {"input": 50, "output": 100, "latency_ms": 350.0}
    ctx.tool_calls = [{"name": "search_tool", "arguments": {"q": "test"}}]
    return ctx


# ═══════════════════════════════════════════════════════════════
# _AsyncObservabilityMiddleware
# ═══════════════════════════════════════════════════════════════


class TestAsyncObservabilityMiddleware:
    """_AsyncObservabilityMiddleware 测试（端口模式）"""

    def test_name(self):
        """name 属性应返回 'AsyncObservability'"""
        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        assert mw.name == "AsyncObservability"

    async def test_before_model_call_increments_counter(self, mock_ctx):
        """before_model_call: 通过端口增加模型调用计数器"""
        mock_port = MagicMock()
        mock_ctx.agent.get_observability_port.return_value = mock_port

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.before_model_call(mock_ctx)

        mock_port.counter.assert_called_once_with("model.calls", 1)

    async def test_after_model_call_with_usage(self, ctx_with_usage):
        """after_model_call: 通过端口记录 token 指标 + gauge"""
        mock_port = MagicMock()
        ctx_with_usage.agent.get_observability_port.return_value = mock_port

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.after_model_call(ctx_with_usage)

        # token 计数器
        mock_port.counter.assert_any_call("tokens.input", 50)
        mock_port.counter.assert_any_call("tokens.output", 100)
        # latency 直方图
        mock_port.histogram.assert_called_once_with(
            "model.latency_ms", 350.0
        )
        # context.chars gauge
        mock_port.gauge.assert_called_once_with("context.chars", 11)

    async def test_after_model_call_no_port(self, mock_ctx):
        """after_model_call: port 返回 None 时静默跳过"""
        mock_ctx.agent.get_observability_port.return_value = None

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        # 不应抛出异常
        await mw.after_model_call(mock_ctx)

    async def test_after_model_call_empty_usage(self, mock_ctx):
        """after_model_call: usage 为空字典时不记录 token 指标（gauge 仍记录）"""
        mock_port = MagicMock()
        mock_ctx.agent.get_observability_port.return_value = mock_port

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.after_model_call(mock_ctx)

        # usage 是空字典（falsy），不记录 token 指标
        mock_port.counter.assert_not_called()
        mock_port.histogram.assert_not_called()
        # gauge 仍被调用
        mock_port.gauge.assert_called_once()

    async def test_after_model_call_zero_tokens(self, ctx_with_usage):
        """after_model_call: input=0 且 output=0 时不记录 histogram"""
        mock_port = MagicMock()
        ctx_with_usage.agent.get_observability_port.return_value = mock_port
        ctx_with_usage.usage = {"input": 0, "output": 0, "latency_ms": 100.0}

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.after_model_call(ctx_with_usage)

        # input=0 且 output=0 → histogram 不应记录
        mock_port.histogram.assert_not_called()
        # counter 仍然记录 0 值
        mock_port.counter.assert_any_call("tokens.input", 0)
        mock_port.counter.assert_any_call("tokens.output", 0)

    async def test_on_round_complete(self, mock_ctx):
        """on_round_complete: 通过端口递增 rounds"""
        mock_port = MagicMock()
        mock_ctx.agent.get_observability_port.return_value = mock_port

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.on_round_complete(mock_ctx)

        mock_port.counter.assert_called_once_with("rounds", 1)

    async def test_on_round_complete_with_interrupt(self, mock_ctx):
        """on_round_complete: interrupted=True 时增加 interrupts 计数器"""
        mock_port = MagicMock()
        mock_ctx.agent.get_observability_port.return_value = mock_port
        mock_ctx.interrupted = True

        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.on_round_complete(mock_ctx)

        mock_port.counter.assert_any_call("rounds", 1)
        mock_port.counter.assert_any_call("interrupts", 1)

    async def test_on_exception_passthrough(self, mock_ctx):
        """on_exception: 基类默认实现，不抛出异常"""
        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        exc = RuntimeError("test error")
        # 基类默认 on_exception 为空实现，不应抛出异常
        await mw.on_exception(mock_ctx, exc)

    async def test_before_tool_execution_passthrough(self, mock_ctx):
        """before_tool_execution: 基类默认实现，不抛出异常"""
        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.before_tool_execution(mock_ctx)

    async def test_after_tool_execution_passthrough(self, mock_ctx):
        """after_tool_execution: 基类默认实现，不抛出异常"""
        from src.core.middleware.observability import (
            _AsyncObservabilityMiddleware,
        )

        mw = _AsyncObservabilityMiddleware()
        await mw.after_tool_execution(mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _AuditLogMiddleware
# ═══════════════════════════════════════════════════════════════


class TestAuditLogMiddleware:
    """_AuditLogMiddleware 测试"""

    def test_name(self):
        """name 属性应返回 'AuditLog'"""
        from src.core.middleware.audit import _AuditLogMiddleware

        mw = _AuditLogMiddleware()
        assert mw.name == "AuditLog"

    async def test_before_model_call_logs_audit(self, mock_ctx):
        """before_model_call: 记录模型调用审计日志"""
        with patch("src.core.middleware.audit.audit_logger") as mock_logger:
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.before_model_call(mock_ctx)

            mock_logger.info.assert_called_once_with(
                "model_call | model=%s | messages=%d | tools=%d",
                "test-model",
                1,
                1,
            )

    async def test_before_model_call_logs_with_multiple_messages(self, mock_agent):
        """before_model_call: 多条消息时正确记录数量"""
        mock_agent.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]

        with patch("src.core.middleware.audit.audit_logger") as mock_logger:
            from src.core.pipeline import PipelineContext
            from src.core.middleware.audit import _AuditLogMiddleware

            ctx = PipelineContext(mock_agent)
            mw = _AuditLogMiddleware()
            await mw.before_model_call(ctx)

            mock_logger.info.assert_called_once_with(
                "model_call | model=%s | messages=%d | tools=%d",
                "test-model",
                2,
                1,
            )

    async def test_after_tool_execution_logs_tool_names(self, mock_ctx):
        """after_tool_execution: 有 tool_calls 时记录工具名"""
        mock_ctx.tool_calls = [
            {"name": "search", "arguments": {"q": "test"}},
            {"name": "calculator", "arguments": {"expr": "1+1"}},
        ]

        with patch("src.core.middleware.audit.audit_logger") as mock_logger:
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.after_tool_execution(mock_ctx)

            mock_logger.info.assert_called_once_with(
                "tool_executed | %s", "search, calculator"
            )

    async def test_after_tool_execution_skips_when_no_tool_calls(self, mock_ctx):
        """after_tool_execution: 无 tool_calls 时不记录审计日志"""
        mock_ctx.tool_calls = []

        with patch("src.core.middleware.audit.audit_logger") as mock_logger:
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.after_tool_execution(mock_ctx)

            mock_logger.info.assert_not_called()

    async def test_after_tool_execution_tool_name_fallback(self, mock_ctx):
        """after_tool_execution: tool_calls 缺少 name key 时回退为 '?'"""
        mock_ctx.tool_calls = [{"name": "valid_tool"}, {}]

        with patch("src.core.middleware.audit.audit_logger") as mock_logger:
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.after_tool_execution(mock_ctx)

            mock_logger.info.assert_called_once_with(
                "tool_executed | %s", "valid_tool, ?"
            )

    async def test_on_exception_passthrough(self, mock_ctx):
        """on_exception: 基类默认实现，不抛出异常"""
        with patch("src.core.middleware.audit.audit_logger"):
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.on_exception(mock_ctx, RuntimeError("err"))

    async def test_before_tool_execution_passthrough(self, mock_ctx):
        """before_tool_execution: 基类默认实现"""
        with patch("src.core.middleware.audit.audit_logger"):
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.before_tool_execution(mock_ctx)

    async def test_after_model_call_passthrough(self, mock_ctx):
        """after_model_call: 基类默认实现"""
        with patch("src.core.middleware.audit.audit_logger"):
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.after_model_call(mock_ctx)

    async def test_on_round_complete_passthrough(self, mock_ctx):
        """on_round_complete: 基类默认实现"""
        with patch("src.core.middleware.audit.audit_logger"):
            from src.core.middleware.audit import _AuditLogMiddleware

            mw = _AuditLogMiddleware()
            await mw.on_round_complete(mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _InterruptCheckMiddleware
# ═══════════════════════════════════════════════════════════════


class TestInterruptCheckMiddleware:
    """_InterruptCheckMiddleware 测试"""

    def test_name(self):
        """name 属性应返回 'InterruptCheck'"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        assert mw.name == "InterruptCheck"

    async def test_before_model_call_interrupts_when_check_returns_true(
        self, mock_ctx
    ):
        """before_model_call: port.is_interrupted 返回 True 时设置 ctx.interrupted"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware
        from src.core.adapters.interrupt import MockInterruptAdapter

        port = MockInterruptAdapter()
        port.set_interrupted(True)
        mw = _InterruptCheckMiddleware(interrupt_port=port)

        assert mock_ctx.interrupted is False
        await mw.before_model_call(mock_ctx)
        assert mock_ctx.interrupted is True

    async def test_before_model_call_no_interrupt_when_check_returns_false(
        self, mock_ctx
    ):
        """before_model_call: port.is_interrupted 返回 False 时不中断"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware
        from src.core.adapters.interrupt import MockInterruptAdapter

        port = MockInterruptAdapter()
        port.set_interrupted(False)
        mw = _InterruptCheckMiddleware(interrupt_port=port)

        await mw.before_model_call(mock_ctx)
        assert mock_ctx.interrupted is False

    async def test_before_model_call_with_default_port(self, mock_ctx):
        """before_model_call: 不传参数时使用 DefaultInterruptAdapter，不抛出异常"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.before_model_call(mock_ctx)
        # DefaultInterruptAdapter 默认不中断
        assert mock_ctx.interrupted is False

    async def test_on_round_complete_passthrough(self, mock_ctx):
        """on_round_complete: 基类默认实现"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.on_round_complete(mock_ctx)

    async def test_on_exception_passthrough(self, mock_ctx):
        """on_exception: 基类默认实现"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.on_exception(mock_ctx, RuntimeError("err"))

    async def test_before_tool_execution_passthrough(self, mock_ctx):
        """before_tool_execution: 基类默认实现"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.before_tool_execution(mock_ctx)

    async def test_after_tool_execution_passthrough(self, mock_ctx):
        """after_tool_execution: 基类默认实现"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.after_tool_execution(mock_ctx)

    async def test_after_model_call_passthrough(self, mock_ctx):
        """after_model_call: 基类默认实现"""
        from src.core.middleware.interrupt import _InterruptCheckMiddleware

        mw = _InterruptCheckMiddleware()
        await mw.after_model_call(mock_ctx)


# ═══════════════════════════════════════════════════════════════
# _ToolRegistryAdapter
# ═══════════════════════════════════════════════════════════════


class TestToolRegistryAdapter:
    """_ToolRegistryAdapter 测试"""

    @pytest.fixture
    def mock_registry(self):
        """创建一个 mock ToolRegistry"""
        return MagicMock()

    def test_get_schemas_delegates(self, mock_registry):
        """get_schemas 委托给 _registry.get_schemas"""
        from src.core.middleware.adapters import _ToolRegistryAdapter

        expected = [{"name": "tool1", "parameters": {}}]
        mock_registry.get_schemas.return_value = expected

        adapter = _ToolRegistryAdapter(mock_registry)
        result = adapter.get_schemas()

        assert result == expected
        mock_registry.get_schemas.assert_called_once_with()

    def test_dispatch_delegates(self, mock_registry):
        """dispatch 委托给 _registry.dispatch"""
        from src.core.middleware.adapters import _ToolRegistryAdapter

        expected = "result_value"
        mock_registry.dispatch.return_value = expected

        adapter = _ToolRegistryAdapter(mock_registry)
        result = adapter.dispatch("test_tool", {"arg1": "val1"}, agent="test_agent")

        assert result == expected
        mock_registry.dispatch.assert_called_once_with(
            "test_tool", {"arg1": "val1"}, "test_agent"
        )

    def test_dispatch_without_agent(self, mock_registry):
        """dispatch: agent 参数默认为 None"""
        from src.core.middleware.adapters import _ToolRegistryAdapter

        adapter = _ToolRegistryAdapter(mock_registry)
        adapter.dispatch("tool", {"a": 1})

        mock_registry.dispatch.assert_called_once_with("tool", {"a": 1}, None)

    def test_build_system_prompt_delegates(self, mock_registry):
        """build_system_prompt 委托给 _registry.build_system_prompt"""
        from src.core.middleware.adapters import _ToolRegistryAdapter

        expected = ["prompt_line_1", "prompt_line_2"]
        mock_registry.build_system_prompt.return_value = expected

        adapter = _ToolRegistryAdapter(mock_registry)
        result = adapter.build_system_prompt()

        assert result == expected
        mock_registry.build_system_prompt.assert_called_once_with()

    def test_get_tools_delegates(self, mock_registry):
        """get_tools 委托给 _registry.get_tools"""
        from src.core.middleware.adapters import _ToolRegistryAdapter

        expected = {"tool1": MagicMock()}
        mock_registry.get_tools.return_value = expected

        adapter = _ToolRegistryAdapter(mock_registry)
        result = adapter.get_tools()

        assert result == expected
        mock_registry.get_tools.assert_called_once_with()

    def test_is_tool_registry_port_instance(self, mock_registry):
        """_ToolRegistryAdapter 是 ToolRegistryPort 的子类"""
        from src.core.ports.tool_registry import ToolRegistryPort
        from src.core.middleware.adapters import _ToolRegistryAdapter

        adapter = _ToolRegistryAdapter(mock_registry)
        assert isinstance(adapter, ToolRegistryPort)
