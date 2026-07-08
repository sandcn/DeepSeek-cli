"""Tests for src/core/pipeline.py — Pipeline 中间件管道

覆盖内容：
  1. PipelineContext — 创建、默认值、__slots__
  2. AsyncMiddleware — 基类默认行为、name 属性
  3. Pipeline — use_async / async_middlewares / __repr__
  4. Pipeline.run_round_async — 核心对话循环编排
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch, ANY

import pytest

from src.core.pipeline import (
    Pipeline,
    AsyncMiddleware,
    PipelineContext,
)


# ═══════════════════════════════════════════════════════════════
# 辅助类型
# ═══════════════════════════════════════════════════════════════

class _MockAgent:
    """mock Agent，仅包含 Pipeline 需要的属性和方法"""
    def __init__(self):
        self.messages = []
        self.model = "test-model"
        self.tools = []
        self.display = None
        self._call_model_async = AsyncMock(return_value=("", "mock response", {}, []))
        self._handle_tool_calls = AsyncMock()
        self._capture_mgr = MagicMock()
        self._capture_mgr.cleanup = MagicMock()
        self._append_assistant_msg = MagicMock()


class _SimpleAsyncMiddleware(AsyncMiddleware):
    """最小 AsyncMiddleware 实现，用于测试"""
    def __init__(self, name: str | None = None):
        self._name = name

    @property
    def name(self) -> str:
        return self._name or super().name


class _HooksRecorderMiddleware(AsyncMiddleware):
    """记录所有钩子调用顺序的中间件，用于验证执行流"""
    def __init__(self):
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "Recorder"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        self.calls.append("before_model_call")

    async def after_model_call(self, ctx: PipelineContext) -> None:
        self.calls.append("after_model_call")

    async def before_tool_execution(self, ctx: PipelineContext) -> None:
        self.calls.append("before_tool_execution")

    async def after_tool_execution(self, ctx: PipelineContext) -> None:
        self.calls.append("after_tool_execution")

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        self.calls.append("on_round_complete")

    async def on_exception(self, ctx: PipelineContext, exc: Exception) -> None:
        self.calls.append(f"on_exception:{type(exc).__name__}")


# ═══════════════════════════════════════════════════════════════
# PipelineContext
# ═══════════════════════════════════════════════════════════════

class TestPipelineContext:
    """PipelineContext 创建与默认值"""

    def test_create_with_agent(self):
        """传入 agent 后 agent 属性正确"""
        agent = _MockAgent()
        ctx = PipelineContext(agent)
        assert ctx.agent is agent

    def test_default_values(self):
        """默认值验证"""
        ctx = PipelineContext(_MockAgent())
        assert ctx.interrupted is False
        assert ctx.round_complete is False
        assert ctx.reasoning == ""
        assert ctx.content == ""
        assert ctx.usage == {}
        assert ctx.tool_calls == []
        assert ctx.model_calls == 0
        assert ctx.error is None

    def test_has_slots(self):
        """PipelineContext 定义了 __slots__"""
        assert hasattr(PipelineContext, "__slots__")
        assert "agent" in PipelineContext.__slots__
        assert "interrupted" in PipelineContext.__slots__
        assert "error" in PipelineContext.__slots__

    def test_slots_prevent_dict(self):
        """__slots__ 生效，实例没有 __dict__"""
        ctx = PipelineContext(_MockAgent())
        assert not hasattr(ctx, "__dict__")


# ═══════════════════════════════════════════════════════════════
# AsyncMiddleware
# ═══════════════════════════════════════════════════════════════

class TestAsyncMiddleware:
    """AsyncMiddleware 基类行为"""

    async def test_name_defaults_to_class_name(self):
        """name 属性默认返回类名"""
        mw = AsyncMiddleware()
        assert mw.name == "AsyncMiddleware"

    async def test_name_custom_class(self):
        """自定义子类的 name 返回子类名"""
        mw = _SimpleAsyncMiddleware()
        assert mw.name == "_SimpleAsyncMiddleware"

    async def test_name_custom_name(self):
        """显式传入 name 时返回该名称"""
        mw = _SimpleAsyncMiddleware(name="CustomMW")
        assert mw.name == "CustomMW"

    async def test_all_hooks_are_coroutines(self):
        """所有钩子都是 async def，调用返回 coroutine"""
        mw = AsyncMiddleware()
        ctx = PipelineContext(_MockAgent())
        for hook_name in ["before_model_call", "after_model_call",
                          "before_tool_execution", "after_tool_execution",
                          "on_round_complete"]:
            result = getattr(mw, hook_name)(ctx)
            assert asyncio.iscoroutine(result), f"{hook_name} 不是 coroutine"
            result.close()  # 清理未完成的 coroutine

    async def test_on_exception_is_coroutine(self):
        """on_exception 也是 async def"""
        mw = AsyncMiddleware()
        ctx = PipelineContext(_MockAgent())
        result = mw.on_exception(ctx, ValueError("test"))
        assert asyncio.iscoroutine(result)
        result.close()

    async def test_default_hooks_do_nothing(self):
        """默认钩子执行后不改变上下文"""
        mw = AsyncMiddleware()
        ctx = PipelineContext(_MockAgent())
        await mw.before_model_call(ctx)
        await mw.after_model_call(ctx)
        await mw.before_tool_execution(ctx)
        await mw.after_tool_execution(ctx)
        await mw.on_round_complete(ctx)
        await mw.on_exception(ctx, ValueError("test"))
        # 默认钩子不应修改任何属性
        assert ctx.interrupted is False
        assert ctx.round_complete is False


# ═══════════════════════════════════════════════════════════════
# Pipeline 中间件管理
# ═══════════════════════════════════════════════════════════════

class TestPipelineManagement:
    """Pipeline 中间件注册与管理"""

    def test_init_empty(self):
        """初始中间件列表为空"""
        p = Pipeline()
        assert p.async_middlewares == []

    def test_use_async_appends(self):
        """use_async 添加中间件到列表末尾"""
        p = Pipeline()
        mw1 = _SimpleAsyncMiddleware(name="MW1")
        mw2 = _SimpleAsyncMiddleware(name="MW2")
        p.use_async(mw1).use_async(mw2)
        names = [m.name for m in p.async_middlewares]
        assert names == ["MW1", "MW2"]

    def test_use_async_returns_self(self):
        """use_async 返回 self 支持链式调用"""
        p = Pipeline()
        result = p.use_async(_SimpleAsyncMiddleware())
        assert result is p

    def test_async_middlewares_returns_copy(self):
        """async_middlewares 返回副本，外部修改不影响内部"""
        p = Pipeline()
        mw = _SimpleAsyncMiddleware()
        p.use_async(mw)
        view = p.async_middlewares
        view.clear()
        assert len(p.async_middlewares) == 1

    def test_custom_middleware_name(self):
        """中间件的 name 属性可通过子类自定义"""
        p = Pipeline()
        mw = _SimpleAsyncMiddleware(name="CustomName")
        p.use_async(mw)
        assert p.async_middlewares[0].name == "CustomName"

    def test_repr_empty(self):
        """空 pipeline 的 repr"""
        p = Pipeline()
        assert repr(p) == "<Pipeline: >"

    def test_repr_with_middlewares(self):
        """有中间件时 repr 显示名称链"""
        p = Pipeline()
        p.use_async(_SimpleAsyncMiddleware(name="A"))
        p.use_async(_SimpleAsyncMiddleware(name="B"))
        assert repr(p) == "<Pipeline: A → B>"


# ═══════════════════════════════════════════════════════════════
# Pipeline.run_round_async — 无工具调用路径
# ═══════════════════════════════════════════════════════════════

class TestRunRoundAsyncNoToolCalls:
    """run_round_async — 模型无工具调用的路径"""

    @pytest.mark.asyncio
    async def test_no_tool_calls_round_complete(self):
        """无工具调用时 round_complete=True，返回 interrupted=False"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(
            return_value=("", "Hello!", {"input": 10, "output": 20}, [])
        )
        ctx = PipelineContext(agent)
        p = Pipeline()

        result = await p.run_round_async(ctx)

        assert result is False  # not interrupted
        assert ctx.round_complete is True
        assert ctx.content == "Hello!"
        assert ctx.model_calls == 1
        # 无工具调用时应追加 assistant 消息
        agent._append_assistant_msg.assert_called_once_with("Hello!", "")

    @pytest.mark.asyncio
    async def test_middleware_hooks_fired_no_tool(self):
        """无工具调用时钩子触发顺序正确"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(
            return_value=("", "Hello!", {}, [])
        )
        ctx = PipelineContext(agent)
        p = Pipeline()
        recorder = _HooksRecorderMiddleware()
        p.use_async(recorder)

        await p.run_round_async(ctx)

        # before_model_call → after_model_call → on_round_complete
        assert recorder.calls == [
            "before_model_call",
            "after_model_call",
            "on_round_complete",
        ]

    @pytest.mark.asyncio
    async def test_empty_content_does_not_append_msg_regression(self):
        """Bug 5 回归测试：空 content 且无 tool_calls 时不追加 assistant 消息"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(
            return_value=("", "", {"input": 10, "output": 20}, [])
        )
        ctx = PipelineContext(agent)
        p = Pipeline()

        result = await p.run_round_async(ctx)

        assert result is False  # not interrupted
        assert ctx.round_complete is True
        assert ctx.content == ""
        assert ctx.model_calls == 1
        # 空 content 不应追加 assistant 消息
        agent._append_assistant_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_can_modify_context(self):
        """中间件可以修改 PipelineContext"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(
            return_value=("", "Hello!", {}, [])
        )

        class _ModifierMiddleware(AsyncMiddleware):
            async def before_model_call(self, ctx):
                ctx.interrupted = True  # 模拟中间件阻止模型调用

        ctx = PipelineContext(agent)
        p = Pipeline()
        p.use_async(_ModifierMiddleware())

        result = await p.run_round_async(ctx)

        assert result is True  # interrupted
        # 模型调用不应执行
        agent._call_model_async.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# Pipeline.run_round_async — 有工具调用路径
# ═══════════════════════════════════════════════════════════════

class TestRunRoundAsyncWithToolCalls:
    """run_round_async — 模型返回工具调用的路径"""

    @pytest.mark.asyncio
    async def test_tool_calls_path(self):
        """有工具调用时触发工具执行，循环继续"""
        agent = _MockAgent()
        tool_calls = [{"function": {"name": "read_file", "arguments": "{}"}}]
        # 第一次返回 tool_calls，第二次返回空（让循环结束）
        agent._call_model_async = AsyncMock(side_effect=[
            ("", "Let me check", {"input": 10}, tool_calls),
            ("", "Done!", {"input": 5}, []),
        ])
        agent._handle_tool_calls = AsyncMock()

        ctx = PipelineContext(agent)
        p = Pipeline()

        await p.run_round_async(ctx)

        # 有工具调用时应触发 _handle_tool_calls
        agent._handle_tool_calls.assert_awaited_once()
        # 清理方法应被调用
        agent._capture_mgr.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_hooks_fired(self):
        """有工具调用时钩子触发顺序包含 before/after_tool_execution"""
        agent = _MockAgent()
        tool_calls = [{"function": {"name": "read_file", "arguments": "{}"}}]
        agent._call_model_async = AsyncMock(side_effect=[
            ("", "Let me check", {}, tool_calls),
            ("", "Done!", {}, []),
        ])
        agent._handle_tool_calls = AsyncMock()

        ctx = PipelineContext(agent)
        p = Pipeline()
        recorder = _HooksRecorderMiddleware()
        p.use_async(recorder)

        await p.run_round_async(ctx)

        assert "before_tool_execution" in recorder.calls
        assert "after_tool_execution" in recorder.calls

    @pytest.mark.asyncio
    async def test_tool_cancelled_handling(self):
        """工具执行被取消时 interrupted=True"""
        agent = _MockAgent()
        tool_calls = [{"function": {"name": "read_file", "arguments": "{}"}}]
        agent._call_model_async = AsyncMock(
            return_value=("", "Let me check", {}, tool_calls)
        )
        agent._handle_tool_calls = AsyncMock(side_effect=asyncio.CancelledError())

        ctx = PipelineContext(agent)
        p = Pipeline()

        result = await p.run_round_async(ctx)

        assert result is True  # interrupted
        # 清理方法必须在 finally 中执行
        agent._capture_mgr.cleanup.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# Pipeline.run_round_async — 异常路径
# ═══════════════════════════════════════════════════════════════

class TestRunRoundAsyncExceptions:
    """run_round_async — 异常处理"""

    @pytest.mark.asyncio
    async def test_model_call_exception_sets_interrupted(self):
        """模型调用抛异常时 interrupted=True，触发 on_exception"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(side_effect=ValueError("model error"))

        ctx = PipelineContext(agent)
        p = Pipeline()
        recorder = _HooksRecorderMiddleware()
        p.use_async(recorder)

        result = await p.run_round_async(ctx)

        assert result is True
        assert ctx.error is not None
        assert isinstance(ctx.error, ValueError)
        # on_exception 应被调用
        assert any("on_exception" in c for c in recorder.calls)

    @pytest.mark.asyncio
    async def test_middleware_before_model_exception(self):
        """中间件 before_model_call 异常时 interrupted=True"""
        agent = _MockAgent()

        class _BadMiddleware(AsyncMiddleware):
            async def before_model_call(self, ctx):
                raise RuntimeError("mw error")

        ctx = PipelineContext(agent)
        p = Pipeline()
        p.use_async(_BadMiddleware())

        result = await p.run_round_async(ctx)

        assert result is True
        assert ctx.error is not None
        # 模型调用不应执行
        agent._call_model_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_exception_all_middlewares_called(self):
        """on_exception 异常不阻止其他中间件执行"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(side_effect=ValueError("error"))

        class _SafeMiddleware(AsyncMiddleware):
            def __init__(self):
                self.exceptions_seen = []

            async def on_exception(self, ctx, exc):
                self.exceptions_seen.append(exc)

        ctx = PipelineContext(agent)
        p = Pipeline()
        safe = _SafeMiddleware()
        p.use_async(safe)

        await p.run_round_async(ctx)

        assert len(safe.exceptions_seen) == 1


# ═══════════════════════════════════════════════════════════════
# Pipeline.run_round_async — 中断信号检查
# ═══════════════════════════════════════════════════════════════

class TestRunRoundAsyncInterrupt:
    """run_round_async — 中断信号检查"""

    @pytest.mark.asyncio
    async def test_interrupt_checked_after_tool_execution(self):
        """工具执行后额外检查中断信号"""
        agent = _MockAgent()
        tool_calls = [{"function": {"name": "read_file", "arguments": "{}"}}]
        agent._call_model_async = AsyncMock(
            return_value=("", "check", {}, tool_calls)
        )
        agent._handle_tool_calls = AsyncMock()

        with patch("src.api.interrupt_async.is_interrupted_async", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True  # 模拟中断

            ctx = PipelineContext(agent)
            p = Pipeline()

            result = await p.run_round_async(ctx)

            assert result is True  # interrupted
            # 中断信号检查被调用（工具执行后检查）
            assert mock_check.await_count >= 1


# ═══════════════════════════════════════════════════════════════
# Bug 4 回归测试 — TestCancelledErrorCheckpoint
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestCancelledErrorCheckpoint:
    """★ Bug 4 回归: Pipeline CancelledError 时设置 checkpoint_requested。

    验证 CancelledError 时 ctx.checkpoint_requested 为 True，
    且 pipeline 保存 ctx 引用供 session 检查。
    """

    async def test_checkpoint_requested_on_cancelled_error(self):
        """CancelledError 时 checkpoint_requested 被设为 True"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(side_effect=asyncio.CancelledError())

        ctx = PipelineContext(agent)
        p = Pipeline()

        result = await p.run_round_async(ctx)

        assert result is True  # interrupted
        assert ctx.checkpoint_requested is True, (
            "CancelledError 后 checkpoint_requested 应为 True"
        )

    async def test_last_ctx_saved_for_session_access(self):
        """pipeline 保存 _last_ctx 引用供 session._emit_round_events 检查"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(side_effect=asyncio.CancelledError())

        ctx = PipelineContext(agent)
        p = Pipeline()

        await p.run_round_async(ctx)

        assert p._last_ctx is ctx, "_last_ctx 应指向最近一次的 PipelineContext"
        assert p._last_ctx.checkpoint_requested is True

    async def test_checkpoint_not_requested_on_normal_completion(self):
        """正常完成时 checkpoint_requested 保持 False"""
        agent = _MockAgent()
        ctx = PipelineContext(agent)
        p = Pipeline()

        await p.run_round_async(ctx)

        assert ctx.checkpoint_requested is False, "正常完成时不应请求 checkpoint"

    async def test_checkpoint_requested_preserves_round_complete(self):
        """CancelledError 时 round_complete 也为 True"""
        agent = _MockAgent()
        agent._call_model_async = AsyncMock(side_effect=asyncio.CancelledError())

        ctx = PipelineContext(agent)
        p = Pipeline()

        await p.run_round_async(ctx)

        assert ctx.round_complete is True, "CancelledError 后 round_complete 应为 True"
        assert ctx.checkpoint_requested is True


# ═══════════════════════════════════════════════════════════════
# Pipeline.__repr__
# ═══════════════════════════════════════════════════════════════

class TestPipelineRepr:
    """Pipeline 字符串表示"""

    def test_repr_empty_pipeline(self):
        p = Pipeline()
        assert repr(p) == "<Pipeline: >"

    def test_repr_single_middleware(self):
        p = Pipeline()
        p.use_async(_SimpleAsyncMiddleware(name="OnlyOne"))
        assert repr(p) == "<Pipeline: OnlyOne>"

    def test_repr_multiple_middlewares(self):
        p = Pipeline()
        p.use_async(_SimpleAsyncMiddleware(name="A"))
        p.use_async(_SimpleAsyncMiddleware(name="B"))
        p.use_async(_SimpleAsyncMiddleware(name="C"))
        assert repr(p) == "<Pipeline: A → B → C>"
