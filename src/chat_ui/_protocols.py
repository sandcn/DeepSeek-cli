"""chat_ui 接口协议模块 — BottomBarProtocol 抽象接口。

Layer 0 — 仅依赖 typing，不依赖任何 chat_ui 内部模块。

定义 _BottomBar 的抽象接口协议，使 ContentRenderer / RenderEngine /
_CmplHandler 只依赖协议而非具体类型，消除对 ui._bottom_bar 的直接
import 依赖（TYPE_CHECKING 时仍可引用具体类型用于运行时检查）。

所有方法签名与 _BottomBar 实际方法保持一致，使用结构子类型（PEP 544），
_BottomBar 无需显式声明 implements BottomBarProtocol。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ._controls import Control, MarkdownControl


class ControlFactory(Protocol):
    """控件工厂协议 — 根据 style 创建 MarkdownControl 实例。

    由 ContentRenderer 注册到 _RenderState.control_factory，
    使 _RenderState.get_reasoning()/get_content() 可通过回调创建控件，
    消除 _RenderState 对 ContentRenderer 的直接依赖。
    """
    def __call__(self, style: str) -> "MarkdownControl": ...


class ControlLifecycleHook(Protocol):
    """控件生命周期钩子协议 — 创建/移除 Control 时的回调。

    由 ContentRenderer 注册到 _RenderState.on_control_created/
    on_control_removed，使 _RenderState 在创建/关闭控件时自动通知
    ControlList 更新。
    """
    def __call__(self, control: "Control") -> None: ...


@runtime_checkable
class BottomBarProtocol(Protocol):
    """底部栏接口协议 — _BottomBar 的抽象接口。

    ContentRenderer / RenderEngine / _CmplHandler 通过此协议
    访问底部栏，不依赖具体实现类型。
    """

    # ── 工具计数 ──────────────────────────────────────
    def increment_tool(self) -> None: ...
    def decrement_tool(self) -> None: ...
    def increment_tool_fail(self) -> None: ...

    # ── 重绘与同步 ────────────────────────────────────
    def force_redraw(self) -> None: ...
    def sync_bottom_lines(self) -> None: ...

    @property
    def is_status_active(self) -> bool: ...
    @property
    def is_resize_pending(self) -> bool: ...
    def check_resize(self) -> bool: ...

    # ── 光标定位 ──────────────────────────────────────
    def ensure_cursor_in_upper(self) -> None: ...
    def get_cursor_info(self) -> tuple[str, int, int, int]: ...
    def compute_cursor_position(
        self, text: str, cursor_pos: int, h: int, w: int,
    ) -> tuple[int, int]: ...

    # ── 补全弹窗 ──────────────────────────────────────
    @property
    def is_completion_visible(self) -> bool: ...
    def hide_completions(self) -> None: ...
    def cycle_completion(self, delta: int) -> None: ...
    def show_completions(
        self, items: list[str], selected: int = 0,
        texts: list[str] | None = None,
        start_pos: int = 0, orig_prefix: str = "",
    ) -> None: ...
    def get_selected_completion(self) -> tuple[str, int, str]: ...
