"""通用空端口实现 — UI 无关的 Null Object 模式

用于 ChatSession 创建 Agent 时避免 UI 依赖。
为已知端口接口提供显式空实现，避免 __getattr__ 的 hasattr 陷阱。
"""

from __future__ import annotations

from contextlib import nullcontext

from .display import DisplayPort


class _NullPort(DisplayPort):
    """通用空端口 — 为已知方法提供显式空实现。

    不覆盖 __getattr__，避免 hasattr 误报。
    """

    is_web: bool = False

    def write(self, text: str = "", level: str = "info", source: str = "core") -> None:
        pass

    def write_with_lock(self, text: str = "", level: str = "info", source: str = "core") -> None:
        pass

    def locked(self):
        return nullcontext()

    def publish(self, event_type: str, data: dict | None = None, source: str = "core") -> None:
        pass

    def subscribe(self, event_type: str, handler) -> None:
        pass

    def unsubscribe(self, event_type: str, handler) -> None:
        pass

    # ── DisplayPort 接口 ──────────────────────────────
    def start(self) -> None:
        pass

    def stop(self, final: bool = False) -> None:
        pass

    async def await_stop(self, timeout: float = 2.0) -> None:
        pass

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        pass

    def tool_start(self, label: str, tool_name: str, detail: str, metadata: dict | None = None) -> None:
        pass

    def tool_done(self, label: str, tool_name: str = "", success: bool = True, metadata: dict | None = None) -> None:
        pass

    def update_spinner(self, label: str) -> None:
        pass

    def capture_and_print(self, display_func) -> str:
        return ""

    def capture_and_print_async(self, display_func) -> str:
        return ""

    def tool_batch_start(self, label: str, names: list[str]) -> None:
        pass

    def update_model_phase(self, label: str, phase: str, message: str = "") -> None:
        pass

    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        pass

    def update_speed(self, label: str, speed: float) -> None:
        pass

    def update_live_input(self, label: str, tokens: int) -> None:
        pass

    def update_live_output(self, label: str, tokens: int) -> None:
        pass

    def update_parse_info(self, label: str, tool_name: str, tokens: int, elapsed: float) -> None:
        pass

    def parse_info_done(self, label: str) -> None:
        pass

    def update_agent_status(self, agent_id: str, status: str, detail: str) -> None:
        pass

    def add_agent(self, agent_id: str, agent_type: str, description: str) -> None:
        pass

    def update_status(self, label: str, status: str) -> None:
        pass

    def set_panel_context(self, context) -> None:
        pass

    def create_sub_display(self, max_history: int):
        """创建子 DisplayPort — 空实现返回自身（Postel's Law）"""
        return self

    def set_result(self, agent_id: str, result: str | None = None, error: str | None = None) -> None:
        pass

    def remove_agent(self, agent_id: str) -> None:
        pass


class _NullOutputPort:
    """空输出端口 — 提供 locked() 上下文管理器。"""

    def write(self, text: str = "", level: str = "info", source: str = "core") -> None:
        pass

    def write_with_lock(self, text: str = "", level: str = "info", source: str = "core") -> None:
        pass

    def locked(self):
        return nullcontext()
