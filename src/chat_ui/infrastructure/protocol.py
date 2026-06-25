"""Protocol 定义层 — 跨层接口协议。

从 _components.py 拆分，将 Protocol 类与具体组件类分离，
避免接口定义与实现混杂。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BottomBarProtocol(Protocol):
    def increment_tool(self) -> None: ...
    def decrement_tool(self) -> None: ...
    def increment_tool_fail(self) -> None: ...
    def force_redraw(self) -> None: ...
    def sync_bottom_lines(self) -> None: ...
    @property
    def is_status_active(self) -> bool: ...
    def ensure_cursor_in_upper(self) -> None: ...
    def get_scroll_end(self) -> int: ...
    def get_cursor_info(self) -> tuple[str, int, int, int]: ...
    def compute_cursor_position(self, text: str, cursor_pos: int, h: int, w: int) -> tuple[int, int]: ...
    @property
    def is_completion_visible(self) -> bool: ...
    def hide_completions(self) -> None: ...
    def cycle_completion(self, delta: int) -> None: ...
    def show_completions(self, items: list[str], selected: int = 0, texts: list[str] | None = None, start_pos: int = 0, orig_prefix: str = "") -> None: ...
    def get_selected_completion(self) -> tuple[str, int, str]: ...


class RenderPhase(Protocol):
    """渲染阶段协议 — 可插拔管线的基础接口。

    每个 Phase 实现 execute 方法，接收引擎引用、命令列表和状态快照。
    引擎按 phases 列表顺序调用各 Phase.execute()。
    """

    def execute(
        self,
        engine: "TuiEngine",
        commands: list,
        state: "TuiState | None",
    ) -> bool:
        """执行本阶段的渲染逻辑。

        Args:
            engine: TuiEngine 实例（用于访问 renderer、bottom_bar、_tio 等）
            commands: 本轮 drain 的所有渲染命令列表
            state: 当前 TuiState（VNode 路径启用时非 None）

        Returns:
            True 表示本阶段有实际渲染输出，False 表示无输出。
        """
        ...


class PanelContext(Protocol):
    """parallel/display.py 通过此协议访问 ChatUI，避免直接 import chat_ui。

    ChatUIConsumer 隐式满足此协议（拥有 bottom_bar、output_adapter、push_cmd、
    claude_style_enabled）。
    """

    @property
    def bottom_bar(self) -> Any: ...
    @property
    def output_adapter(self) -> Any: ...
    @property
    def claude_style_enabled(self) -> bool: ...
    def push_cmd(self, cmd: Any) -> None: ...
