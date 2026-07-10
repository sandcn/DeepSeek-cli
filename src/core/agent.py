from __future__ import annotations

import logging
from ._capture_manager import CaptureManager
from .base_agent import BaseAgent
from ._tool_callbacks import ToolCallbackChain
from .pipeline import Pipeline, PipelineContext
from .tool_executor_async import AsyncToolExecutor
from ..tools.registry import ToolRegistry
from ..core.ports import ConfigPort
from ..core.ports.interrupt import DefaultInterruptAdapter
from ..core.ports.tool_registry import ToolRegistryPort
from ..core.ports.prompt_builder import PromptBuilderPort, DefaultPromptBuilderAdapter
from .middleware.observability import _AsyncObservabilityMiddleware  # noqa: F401 — re-exported
from .middleware.audit import _AuditLogMiddleware  # noqa: F401 — re-exported
from .middleware.interrupt import _InterruptCheckMiddleware  # noqa: F401 — re-exported
from .middleware.adapters import _ToolRegistryAdapter  # noqa: F401 — re-exported
_logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """对话代理，封装模型调用和工具执行逻辑

    Agent 的核心对话循环使用 Pipeline 中间件管道驱动，
    可通过 pipeline.use() 注册自定义中间件扩展行为。
    """

    def __init__(self, model=None, registry=None, sandbox=None,
                 display_port=None, event_port=None, output_port=None,
                 config_port=None, async_model_port=None,
                 prompt_builder_port=None,
                 interrupt_port=None):
        """初始化 Agent。

        Args:
            model: 模型名称
            registry: 工具注册表
            sandbox: 沙盒管理器
            display_port: 显示端口
            event_port: 事件端口
            output_port: 输出端口
            config_port: 配置端口
            async_model_port: 异步 AsyncModelPort
                              默认 DefaultAsyncModelAdapter()（异步优先）
                              传入 None 禁用异步路径
            prompt_builder_port: PromptBuilderPort 实例
            interrupt_port: InterruptPort 实例
        """
        super().__init__()
        # CaptureManager — 统一的 stdout 捕获管理器
        self._capture_mgr = CaptureManager()
        # ToolCallbackChain — 工具回调链独立封装
        self._tool_callbacks = ToolCallbackChain(self)

        self._registry = registry or ToolRegistry.default()

        # ── PromptBuilderPort ────────────────────────────
        if prompt_builder_port is not None:
            self._prompt_builder_port = prompt_builder_port
        else:
            self._prompt_builder_port = DefaultPromptBuilderAdapter()

        # ── 异步 ModelPort（默认启用） ────────────────────
        if async_model_port is not None:
            self._async_model_port = async_model_port
        else:
            from ..core.ports.model import DefaultAsyncModelAdapter
            self._async_model_port = DefaultAsyncModelAdapter()
        self._call_model_async = self._wrap_async_model_port(self._async_model_port)

        self.messages = [{"role": "system", "content": part} for part in self._registry.build_system_prompt()]

        # ── ConfigPort 注入 ───────────────────────────────
        if config_port is not None:
            self._config_port = config_port
        else:
            from ..core.ports import DefaultConfigAdapter
            self._config_port = DefaultConfigAdapter()
        self.model = model or self._config_port.get_model()
        self.tools = self._registry.get_schemas()

        # ── ToolRegistryPort 包装 ─────────────────────────
        self._tool_registry_port: ToolRegistryPort = _ToolRegistryAdapter(self._registry)

        self._async_tool_executor = AsyncToolExecutor(self._registry)

        # ── InterruptPort 中断端口 ─────────────────────────
        if interrupt_port is not None:
            self._interrupt_port = interrupt_port
        else:
            from ..core.ports.interrupt import DefaultInterruptAdapter
            self._interrupt_port = DefaultInterruptAdapter()

        # Port 注入：每个端口独立判断，传入的端口不会被静默忽略
        if display_port is not None:
            self._display_port = display_port
        else:
            from ..ui.adapters import UIDisplayAdapter
            from ..ui.events.adapters import EventBusDisplayProxy
            self._display_port = UIDisplayAdapter(EventBusDisplayProxy(source="agent"))

        if event_port is not None:
            self._event_port = event_port
        else:
            from ..ui.adapters import UIEventAdapter
            self._event_port = UIEventAdapter()

        if output_port is not None:
            self._output_port = output_port
        else:
            from ..ui.adapters import UIOutputAdapter
            self._output_port = UIOutputAdapter()
        self.display = self._display_port
        self.context_manager = None
        self._shared_executor = None
        # ── 工具完成回调列表（TUI 刷新等外部监听） ────────
        self._on_tool_completed_callbacks: list = []

        if sandbox is not None:
            self.sandbox = sandbox
        else:
            from .sandbox_manager import get_sandbox_manager
            self.sandbox = get_sandbox_manager()

        # ── Pipeline（中间件管道） ─────────────────────────
        self._pipeline = Pipeline()
        # 异步中间件（默认启用，由 run() 驱动）
        # ★ P1-4 修复：中断检查中间件放在第一位，确保模型调用前优先检查中断
        self._pipeline.use_async(_InterruptCheckMiddleware())
        self._pipeline.use_async(_AsyncObservabilityMiddleware())
        self._pipeline.use_async(_AuditLogMiddleware())
        # ★ Bug1 修复：跨轮次 checkpoint_requested 防残留
        self._last_checkpoint_requested = False

    # ── stdout 捕获（CaptureManager） ─────────────────
    # 调用方通过 agent._capture_mgr.xxx() 直接访问：
    #   _tool_callbacks.py: agent._capture_mgr.start_capture(label) / stop_capture(label)
    #   pipeline.py:       agent._capture_mgr.cleanup()

    @property
    def _capture_state(self) -> dict | None:
        """保持对 diagnose_stdout_leak.py 的向后兼容。"""
        return getattr(self._capture_mgr, '_state', None)

    # ── 工具完成回调 ──────────────────────────────────────

    def add_on_tool_completed(self, callback) -> None:
        """注册工具完成回调，在每次工具执行完成后调用。

        callback 签名: callback(tc: dict, output: str, success: bool) -> None
        在 display.tool_done() 之后调用，可用于 TUI 刷新等场景。
        """
        self._on_tool_completed_callbacks.append(callback)

    # ── Pipeline 访问 ─────────────────────────────────────

    @property
    def pipeline(self) -> Pipeline:
        """获取 Agent 的 Pipeline 实例，用于注册自定义中间件"""
        return self._pipeline

    def build_system_prompt(self) -> list[str]:
        """构建系统提示词。"""
        from ..prompt_builder.builder import build_system_prompt as _build
        return _build()

    def _get_active_tools(self) -> list[dict]:
        """返回当前工具 schemas。"""
        return self.tools

    def get_tool_registry(self):
        """返回工具注册表实例"""
        return self._registry

    @staticmethod
    def _wrap_async_model_port(async_model_port):
        """将 AsyncModelPort 的 ModelResult 返回值包装为旧版 (reasoning, content, usage, tool_calls) 元组。

        返回 async 函数，可直接用于 Pipeline 的异步路径。
        """
        from ..core.ports.model import ModelResult

        async def wrapped(messages, model=None, tools=None, display=None, label=None, silent=False):
            result = await async_model_port.call(messages, model, tools, display, label, silent)
            return result.reasoning, result.content, result.usage, result.tool_calls

        return wrapped

    def get_async_model_port(self):
        """返回当前 AsyncModelPort 实例（供 AsyncSubAgent 等使用）"""
        return self._async_model_port

    def get_tool_registry_port(self) -> ToolRegistryPort:
        """返回 ToolRegistryPort 实例"""
        return self._tool_registry_port

    def get_prompt_builder_port(self) -> PromptBuilderPort:
        """返回 PromptBuilderPort 实例"""
        return self._prompt_builder_port

    def get_config_port(self) -> ConfigPort:
        """返回 ConfigPort 实例"""
        return self._config_port

    def _append_assistant_msg(self, content, reasoning=None):
        """追加普通 assistant 消息（不含 tool_calls），委托给 _append_assistant_message。"""
        self._append_assistant_message(content, reasoning_content=reasoning)

    # =================== 对话循环（默认异步） ===================

    # =================== 对话循环（纯异步） ===================

    async def run(self):
        """纯异步执行对话（用于已有事件循环的上下文）"""
        self._interrupt_port.reset_interrupt()
        # ★ 步骤 1 修复：跨轮次不残留上一轮的 checkpoint_requested 状态
        self._last_checkpoint_requested = False

        ctx = PipelineContext(self)
        # 将会话状态机引用从 agent 临时属性转移到 PipelineContext
        sm = getattr(self, '_session_state_machine', None)
        if sm is not None:
            ctx.session_state_machine = sm
        # ★ P1 修复：try/except/else 精确区分 CancelledError 路径和其他路径
        #   - CancelledError 路径：pipeline 已将 ctx.checkpoint_requested 设为 True，
        #     必须在透传前提取保存，否则标记丢失。
        #   - 其他异常路径：重置 checkpoint 状态防跨轮次残留。
        #   - 正常路径：保存 checkpoint_requested 标记。
        try:
            interrupted, checkpoint_requested = await self._pipeline.run_round_async(ctx)
        except asyncio.CancelledError:
            # ★ P1 修复：CancelledError 透传前从 ctx 提取 checkpoint_requested 标记
            self._last_checkpoint_requested = ctx.checkpoint_requested
            raise
        except Exception:
            # 其他异常路径：重置 checkpoint 标记防跨轮次残留
            self._last_checkpoint_requested = False
            raise
        else:
            # 正常路径：保存 checkpoint_requested 标记
            self._last_checkpoint_requested = checkpoint_requested
            return interrupted

    # ── 兼容旧版直接调用（某些测试可能直接使用） ──────────

    async def run_async(self):
        """兼容旧版调用，委托给 run()

        某些测试/使用方直接调用 run_async，保留此别名确保兼容。
        """
        return await self.run()

    # =================== 工具调用处理 ===================

    async def _handle_tool_calls(self, content, tool_calls, reasoning_content=None, usage=None):
        """处理工具调用（委托给 ToolCallbackChain）。"""
        return await self._tool_callbacks.handle_tool_calls(content, tool_calls, reasoning_content, usage)

    # ── 工具回调已移入 ToolCallbackChain ──────────────
    # _sanitize_args_for_log / _detect_webdiff / _on_before_tool /
    # _on_after_tool / _run_tool_method / _show_tool_execution_summary
    # 均通过 self._tool_callbacks 直接访问，Agent 不再提供薄委托。


# 以下类已提取到 middleware/ 包，在此 re-export：
# _AsyncObservabilityMiddleware → middleware/observability.py
# _AuditLogMiddleware          → middleware/audit.py
# _InterruptCheckMiddleware    → middleware/interrupt.py
# _ToolRegistryAdapter         → middleware/adapters.py
