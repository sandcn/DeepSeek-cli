from __future__ import annotations

import logging
from ._capture_manager import CaptureManager
from .base_agent import BaseAgent
from ._tool_callbacks import ToolCallbackChain
from .pipeline import Pipeline, PipelineContext
from .tool_executor_async import AsyncToolExecutor
from ..tools.registry import ToolRegistry
from ..core.ports import ConfigPort
from ..core.ports.chat_ui import ChatUIPort, get_default_chat_ui_port
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
                 prompt_builder_port=None, chat_ui_port: ChatUIPort | None = None):
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
            chat_ui_port: ChatUIPort 依赖倒置端口，默认从全局获取
        """
        super().__init__()
        # ToolCallbackChain — 工具回调链独立封装
        self._tool_callbacks = ToolCallbackChain(self)
        # ChatUIPort — 依赖倒置端口，ToolCallbackChain 可通过 self._agent._chat_ui_port 访问
        self._chat_ui_port = chat_ui_port if chat_ui_port is not None else get_default_chat_ui_port()

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

        # Port 注入：允许外部传入 null 实现（ChatSession），
        # 不传时使用默认 UI 实现（保持向后兼容）。
        if display_port is not None and event_port is not None and output_port is not None:
            self._display_port = display_port
            self._event_port = event_port
            self._output_port = output_port
        else:
            # ★ 默认实现封装在 _default_adapters 中，agent 不再直接引用 ui. 命名空间。
            #   工厂函数内部通过惰性导入访问 ui.adapters / ui.events.adapters。
            from ..core.ports._default_adapters import (
                create_default_display_port,
                create_default_event_port,
                create_default_output_port,
            )
            self._display_port = create_default_display_port()
            self._event_port = create_default_event_port()
            self._output_port = create_default_output_port()
        self.display = self._display_port

        # CaptureManager — 统一的 stdout 捕获管理器，注入 EventPort
        self._capture_mgr = CaptureManager(event_port=self._event_port)

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
        from ..api.interrupt_async import reset_interrupt_async
        reset_interrupt_async()

        ctx = PipelineContext(self)
        # 将会话状态机引用从 agent 临时属性转移到 PipelineContext
        sm = getattr(self, '_session_state_machine', None)
        if sm is not None:
            ctx.session_state_machine = sm
        interrupted = await self._pipeline.run_round_async(ctx)
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
