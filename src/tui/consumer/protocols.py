"""Protocol 定义层 — 跨层接口协议。

从 _components.py 拆分，将 Protocol 类与具体组件类分离，
避免接口定义与实现混杂。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter


@runtime_checkable
class RenderEngine(Protocol):
    """渲染引擎协议 — TuiEngine 实现此协议，Consumer 仅依赖协议而非具体类。

    定义渲染引擎与消费者之间的接口契约，支持解耦测试和替代实现。
    """

    def push_cmd(self, cmd: tuple) -> None:
        """入队渲染命令到命令队列。"""

    def flush(self, timeout: float | None = 5.0) -> None:
        """排空命令队列，等待所有命令处理完成。"""

    def start(self) -> None:
        """启动渲染引擎（render 线程）。"""

    def stop(self) -> None:
        """停止渲染引擎。"""

    def request_bottom_redraw(self) -> None:
        """请求底部栏重绘。"""

    def ensure_cursor_upper(self) -> None:
        """确保光标位于上部区域。"""

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        """注册面板刷新回调。"""

    @property
    def output_adapter(self) -> OutputAdapter:
        """获取当前 OutputAdapter 实例。"""


@runtime_checkable
class BottomBarProtocol(Protocol):
    def prepare_for_content(self) -> None: ...
    def increment_tool(self) -> None: ...
    def decrement_tool(self) -> None: ...
    def increment_tool_fail(self) -> None: ...
    def set_main_phase(self, phase: str) -> None: ...
    def force_redraw(self) -> None: ...
    def sync_bottom_lines(self) -> None: ...
    def set_subagent_frame(self, lines: list[str]) -> None: ...
    @property
    def is_status_active(self) -> bool: ...
    @property
    def is_active(self) -> bool: ...
    def ensure_cursor_in_upper(self) -> None: ...
    def get_scroll_end(self) -> int: ...
    def get_cursor_info(self) -> tuple[str, int, int, int]: ...
    def compute_cursor_position(self, text: str, cursor_pos: int, h: int, w: int) -> tuple[int, int]: ...
    @property
    def is_completion_visible(self) -> bool: ...
    def hide_completions(self) -> None: ...
    def cycle_completion(self, delta: int) -> None: ...
    def show_completions(self, items: list[str], selected: int = 0, texts: list[str] | None = None, start_pos: int = 0, orig_prefix: str = "", types: list[str] | None = None, match_prefix: str = "") -> None: ...
    def get_selected_completion(self) -> tuple[str, int, str]: ...
