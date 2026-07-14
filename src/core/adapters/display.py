"""显示适配器 — 核心层与 UI 显示的默认适配器

职责：桥接核心层与基础设施层显示实现。
适配器层允许导入 ui/ 模块（桥接职责）。
"""
from __future__ import annotations

from typing import Optional

from ..ports import DisplayPort


class DefaultDisplayAdapter(DisplayPort):
    """默认显示适配器 — 包装 EventBusDisplayProxy

    作为全局默认显示端口，供核心模块在没有依赖注入时使用。
    通过 EventBusDisplayProxy 发布显示事件到 DisplayEventBus，
    由 ChatUIConsumer 等订阅者消费渲染。
    """

    def __init__(self, source: str = "agent"):
        self._source = source
        self._proxy = None

    def _get_proxy(self):
        if self._proxy is None:
            from ...tui.events.adapters import EventBusDisplayProxy
            self._proxy = EventBusDisplayProxy(source=self._source)
        return self._proxy

    # ── 工具调用 ────────────────────────────────────────

    def tool_start(self, tool_label: str, tool_name: str, detail: str,
                   metadata: Optional[dict] = None) -> None:
        self._get_proxy().tool_start(tool_label, tool_name, detail, metadata)

    def tool_done(self, tool_label: str, tool_name: str = "",
                  success: bool = True, metadata: Optional[dict] = None) -> None:
        self._get_proxy().tool_done(tool_label, tool_name, success, metadata)

    def capture_and_print(self, display_func) -> str:
        return self._get_proxy().capture_and_print(display_func)

    async def capture_and_print_async(self, display_func) -> str:
        return await self._get_proxy().capture_and_print_async(display_func)

    def update_status(self, label: str, status: str) -> None:
        self._get_proxy().update_status(label, status)

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        self._get_proxy().tool_parsing(label, tool_name, arguments)

    def tool_batch_start(self, label: str, names: list[str]) -> None:
        self._get_proxy().tool_batch_start(label, names)

    def update_parse_info(self, label: str, tool_name: str, tokens: int,
                          elapsed: float) -> None:
        self._get_proxy().update_parse_info(label, tool_name, tokens, elapsed)

    def parse_info_done(self, label: str) -> None:
        self._get_proxy().parse_info_done(label)

    # ── 代理状态与实时指标 ──────────────────────────────

    def update_model_phase(self, label: str, phase: str, message: str = "") -> None:
        self._get_proxy().update_model_phase(label, phase, message)

    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        self._get_proxy().update_usage(label, usage, replace)

    def update_speed(self, label: str, speed: float) -> None:
        self._get_proxy().update_speed(label, speed)

    def update_live_input(self, label: str, tokens: int) -> None:
        self._get_proxy().update_live_input(label, tokens)

    def update_live_output(self, label: str, tokens: int) -> None:
        self._get_proxy().update_live_output(label, tokens)

    def update_agent_status(self, label: str, status: str) -> None:
        self._get_proxy().update_agent_status(label, status)

    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        self._get_proxy().add_agent(label, description, status)

    # ── 生命周期 ────────────────────────────────────────

    def start(self) -> None:
        self._get_proxy().start()

    def stop(self) -> None:
        self._get_proxy().stop()
