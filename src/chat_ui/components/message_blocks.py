"""消息块 Box 包装组件 — React Ink Box 边框的消息流组件包装器。

为 8 种消息块类型提供声明式 Box 边框渲染：
  - ThinkingBlockBox: 推理块（blue round border + "Thinking..." title，可折叠）
  - AnswerBlockBox: 回答块（dim round border）
  - UserMsgBlockBox: 用户消息块（cyan round border）
  - ToolOutputBlockBox: 工具输出块（yellow dim round border）
  - ToolCallBlockBox: 工具调用块（round cyan border + 状态标记）
  - ToolResultBlockBox: 工具结果块（round border + 成功/失败标记）
  - ErrorBlockBox: 错误块（red single border）
  - NotificationBlockBox: 通知块（green borderless）

窄屏（< 40 列）自动降级为无边框模式。

样式集中管理在 _MSG_BLOCK_STYLES 字典中，未来新增块类型或调整样式
只需修改此字典。工厂函数 create_message_box() 根据 VNode type 字符串
自动创建对应的 Box + TextContent 实例。
"""

from __future__ import annotations

import shutil
import time
from typing import Any

from .box import Box
from .base import TuiComponent


# ── 窄屏降级 ────────────────────────────────────────

_NARROW_TERM_THRESHOLD = 40


def _is_narrow_screen(threshold: int = _NARROW_TERM_THRESHOLD) -> bool:
    """检查当前终端是否为窄屏。

    Args:
        threshold: 列数阈值，默认 40。

    Returns:
        True 如果终端宽度 < threshold。
    """
    try:
        return shutil.get_terminal_size().columns < threshold
    except (OSError, ValueError):
        return False


# ── 消息块样式预设 ──────────────────────────────────

_MSG_BLOCK_STYLES: dict[str, dict[str, Any]] = {
    "thinking": {
        "border_style": "round",
        "border_color": "blue",
        "border_dim_color": False,
        "title": "Thinking...",
        "title_color": "blue",
        "padding_x": 1,
        "padding_y": 0,
        "margin_y": 0,
    },
    "answer": {
        "border_style": "round",
        "border_color": "bright_black",
        "border_dim_color": True,
        "padding_x": 1,
        "padding_y": 0,
        "margin_y": 0,
    },
    "user_msg": {
        "border_style": "round",
        "border_color": "cyan",
        "padding_x": 1,
        "margin_y": 0,
    },
    "tool_output": {
        "border_style": "round",
        "border_color": "yellow",
        "border_dim_color": True,
        "title": "",
        "padding_x": 1,
        "margin_y": 0,
    },
    "tool_call": {
        "border_style": "round",
        "border_color": "cyan",
        "title": "⚙ Tool",
        "title_color": "cyan",
        "padding_x": 1,
        "margin_y": 0,
    },
    "tool_result": {
        "border_style": "round",
        "border_color": "green",
        "title": "",
        "title_color": "",
        "padding_x": 1,
        "margin_y": 0,
    },
    "error": {
        "border_style": "single",
        "border_color": "red",
        "padding_x": 1,
        "margin_y": 0,
    },
    "notification": {
        "border_style": "none",
        "background_color": "green",
        "padding_x": 1,
        "margin_y": 0,
    },
}


# ── 文本内容辅助组件 ──────────────────────────────

class TextContent(TuiComponent):
    """纯文本内容组件 — 作为 Box 子组件，render() 返回原文本。

    与 Box 边框组件兼容：Box 的 render_children() 遍历子组件并调用
    各子组件的 render()，TextContent 直接返回原始文本字符串。

    兼容性契约：render() 始终返回 str，与 Box._build_content_line()
    的 str() 转换兼容。
    """

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


def _make_text_component(text: str) -> TextContent:
    """创建文本内容组件（工厂函数）。

    Args:
        text: 纯文本内容。

    Returns:
        TextContent 实例。
    """
    return TextContent(text)


# ── 消息块 Box 基类 ──────────────────────────────

class _MessageBlockBox(Box):
    """消息块 Box 基类 — 窄屏 / border_style="none" 自动降级。

    所有消息块 Box 子类的共同行为：
    - render() 时检测终端宽度，窄屏自动隐藏所有边框
    - border_style 为 "none" 时同样隐藏边框（如 NotificationBlockBox）
    """

    def render(self) -> str:
        is_no_border = self.border_style == "none" or _is_narrow_screen()
        if not is_no_border:
            return super().render()
        saved = (self.show_top, self.show_bottom, self.show_left, self.show_right)
        self.show_top = self.show_bottom = self.show_left = self.show_right = False
        try:
            return super().render()
        finally:
            (self.show_top, self.show_bottom, self.show_left, self.show_right) = saved


# ── 消息块 Box 组件 ──────────────────────────────

class ThinkingBlockBox(_MessageBlockBox):
    """推理块 — blue round border + "Thinking..." title（可折叠）。

    构造参数:
        text: 推理内容文本。
        is_active: 是否正在活跃推理中（默认 True）。活跃时标题前显示动画指示符。
    """

    def __init__(self, text: str = "", is_active: bool = True, **kwargs: Any) -> None:
        self._thinking_active = is_active
        style = dict(_MSG_BLOCK_STYLES["thinking"])
        style["collapsible"] = True
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))

    def render(self) -> str:
        """渲染推理块。

        活跃状态下标题前显示动画 spinner；折叠时显示 ▶，展开非活跃时显示 ▼。
        CHAT_UI_CLAUDE_STYLE 启用时使用 Claude Code 风格：⏺ Thinking… + braille spinner（dim italic）。
        """
        # ── Claude Code 风格门控（惰性导入）─────────────
        from ..infrastructure.claude_style import (
            _is_claude_style_enabled, CLAUDE_THINKING_ICON,
            CLAUDE_SPINNER_FRAMES, CLAUDE_COLORS,
        )
        if _is_claude_style_enabled():
            from ..infrastructure.ansi import ANSI_RESET
            # 边框颜色改为 dim (bright_black)
            self.border_color = "bright_black"
            self.border_dim_color = False
            # 清除 title_color，避免 Box 的 _styled() 覆盖 dim+italic 预样式
            self.title_color = ""
            dim_italic_prefix = CLAUDE_COLORS["thinking"]
            icon = CLAUDE_THINKING_ICON

            if self._thinking_active and not self.collapsed:
                from ..components.animation import use_animation
                anim = use_animation({"interval": 100})
                idx = anim["frame"] % len(CLAUDE_SPINNER_FRAMES)
                spinner_char = CLAUDE_SPINNER_FRAMES[idx]
                self.title = f"{spinner_char} {icon} Thinking…"
            elif self.collapsed:
                self.title = f"▶ {icon} Thinking…"
            else:
                self.title = f"▼ {icon} Thinking…"

            # 预应用 dim + italic ANSI 样式到标题
            self.title = f"{dim_italic_prefix}{self.title}{ANSI_RESET}"
        else:
            if self._thinking_active and not self.collapsed:
                from ..components.animation import use_spinner
                spinner = use_spinner({"type": "dots", "interval": 100})
                self.title = f"{spinner['char']} Thinking..."
            elif self.collapsed:
                self.title = "▶ Thinking..."
            else:
                self.title = "▼ Thinking..."
        return super().render()


class AnswerBlockBox(_MessageBlockBox):
    """回答块 — dim round border。

    Claude Code 风格（CHAT_UI_CLAUDE_STYLE=1）下：
    - 将原始 Markdown 文本渲染为 ANSI 样式字符串后显示
    - 支持标题、粗体、斜体、行内代码、列表、引用、代码块等
    - 非 Claude 路径行为不变（直接显示原始文本）
    """

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["answer"])
        style.update(kwargs)

        # ── Claude Code 风格：渲染 Markdown ──
        self._claude_rendered: str = ""
        if text:
            try:
                from ..infrastructure.claude_style import _is_claude_style_enabled
                if _is_claude_style_enabled():
                    from ..infrastructure.markdown_renderer import render_markdown
                    self._claude_rendered = render_markdown(text)
            except ImportError:
                pass

        super().__init__(**style)
        if self._claude_rendered:
            self.add_child(_make_text_component(self._claude_rendered))
        elif text:
            self.add_child(_make_text_component(text))


class UserMsgBlockBox(_MessageBlockBox):
    """用户消息块 — cyan round border（Claude Code 风格下标题前添加 ❯ 前缀）。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        # Claude Code 风格：标题前添加 ❯ 前缀
        try:
            from ..infrastructure.claude_style import (
                _is_claude_style_enabled, CLAUDE_PROMPT_ICON,
            )
            if _is_claude_style_enabled():
                if "title" not in kwargs:
                    kwargs["title"] = CLAUDE_PROMPT_ICON + " User"
        except ImportError:
            pass
        style = dict(_MSG_BLOCK_STYLES["user_msg"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class ToolOutputBlockBox(_MessageBlockBox):
    """工具输出块 — yellow dim round border。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["tool_output"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class ErrorBlockBox(_MessageBlockBox):
    """错误块 — red single border。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["error"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class NotificationBlockBox(_MessageBlockBox):
    """通知块 — green borderless。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["notification"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class ToolCallBlockBox(_MessageBlockBox):
    """工具调用块 — round cyan border + spinner + tool name。

    Claude Code 风格（CHAT_UI_CLAUDE_STYLE_TOOLS=1）下渲染为可展开卡片：
    图标 + 名称 + 参数摘要 + braille spinner + ✓/✗ + 耗时 + 展开/折叠。
    非 Claude 风格下保持原有行为不变。

    构造参数:
        tool_name: 工具名称。
        status: 状态，可选 "running" / "completed" / "failed"，默认 "running"。
        text: 附加文本内容。
        params_summary: 工具参数摘要（如 "src/main.py"），Claude 风格使用。
        elapsed_ms: 工具调用耗时（毫秒），Claude 风格使用。
    """

    def __init__(self, tool_name: str = "", status: str = "running",
                 text: str = "", params_summary: str = "",
                 elapsed_ms: float = 0.0, **kwargs: Any) -> None:
        self.tool_name = tool_name
        self._status = status
        self._elapsed_start = kwargs.pop('elapsed_start', None)
        self._claude_params_summary = params_summary
        self._claude_elapsed_ms = elapsed_ms

        # ── Claude Code 风格门控 ──────────────────────
        self._claude_mode = False
        self._claude_tool_icon = "\u2699"  # ⚙ 默认图标
        try:
            from ..infrastructure.claude_style import (
                _is_feature_enabled, CLAUDE_TOOL_ICONS
            )
            self._claude_mode = _is_feature_enabled("TOOLS")
            if self._claude_mode:
                self._claude_tool_icon = CLAUDE_TOOL_ICONS.get(
                    tool_name, "\u2699")
        except ImportError:
            pass

        # 工具名称截断：超过 40 字符自动截断 + ...
        display_name = tool_name
        if len(tool_name) > 40:
            display_name = tool_name[:37] + "..."
        self._display_name = display_name

        style = dict(_MSG_BLOCK_STYLES["tool_call"])

        if self._claude_mode:
            # Claude 风格：可折叠卡片
            style["collapsible"] = True
            style["collapsed"] = (status != "running")

            # 卡片缩进
            try:
                from ..infrastructure.claude_style import CLAUDE_TOOL_CARD_STYLE
                indent = CLAUDE_TOOL_CARD_STYLE.get("indent", 2)
                style["margin_x"] = indent
            except ImportError:
                pass

            # 状态颜色
            if status == "completed":
                style["border_color"] = "green"
                style["title_color"] = "green"
            elif status == "failed":
                style["border_color"] = "red"
                style["title_color"] = "red"
            else:  # running
                style["border_color"] = "cyan"
                style["title_color"] = "cyan"
            # title 在 render() 中动态设置（含 spinner 和耗时）
        else:
            # 非 Claude：原有行为完全不变
            if status == "completed":
                style["title"] = f"\u2713 {display_name}"
                style["title_color"] = "green"
                style["border_color"] = "green"
            elif status == "failed":
                style["title"] = f"\u2717 {display_name}"
                style["title_color"] = "red"
                style["border_color"] = "red"
            else:  # running
                if tool_name:
                    style["title"] = f"\u2699 {display_name}"
                style["title_color"] = "cyan"
                style["border_color"] = "cyan"

        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """格式化耗时为 Claude Code 风格字符串。

        < 1s → "0.8s"，< 60s → "12.3s"，≥ 60s → "1:23"。
        """
        if seconds >= 60:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"({mins}:{secs:02d})"
        else:
            return f"({seconds:.1f}s)"

    def render(self) -> str:
        """渲染工具调用块。

        Claude 风格：braille spinner + 工具图标 + 参数摘要 + 耗时。
        非 Claude 风格：dots spinner + ⚙ 图标 + 耗时（保持原有行为）。
        窄屏降级为单行摘要（仅显示工具名 + 状态）。
        """
        if self._claude_mode:
            # ── Claude Code 风格渲染 ────────────────────
            if _is_narrow_screen():
                # 窄屏降级：仅显示工具名 + 状态
                if self._status == "completed":
                    self.title = f"\u2713 {self._display_name}"
                elif self._status == "failed":
                    self.title = f"\u2717 {self._display_name}"
                else:
                    self.title = self._display_name
                return super().render()

            if self._status == "running":
                from ..components.animation import use_spinner
                spinner = use_spinner({"type": "braille", "interval": 80})

                title_parts = [
                    f"{spinner['char']} {self._claude_tool_icon}"
                    f" {self._display_name}"
                ]

                if self._claude_params_summary:
                    title_parts.append(self._claude_params_summary)

                # 计算耗时
                if self._elapsed_start is not None:
                    elapsed = time.monotonic() - self._elapsed_start
                    title_parts.append(self._format_elapsed(elapsed))
                elif self._claude_elapsed_ms > 0:
                    title_parts.append(
                        self._format_elapsed(self._claude_elapsed_ms / 1000.0))

                self.title = " ".join(title_parts)
            else:
                # completed / failed
                try:
                    from ..infrastructure.claude_style import (
                        CLAUDE_SUCCESS_ICON, CLAUDE_FAIL_ICON
                    )
                    status_icon = (CLAUDE_SUCCESS_ICON
                                   if self._status == "completed"
                                   else CLAUDE_FAIL_ICON)
                except ImportError:
                    status_icon = ("\u2713" if self._status == "completed"
                                   else "\u2717")

                title_parts = [
                    f"{status_icon} {self._claude_tool_icon}"
                    f" {self._display_name}"
                ]

                # 耗时
                elapsed = None
                if self._claude_elapsed_ms > 0:
                    elapsed = self._claude_elapsed_ms / 1000.0
                elif self._elapsed_start is not None:
                    elapsed = time.monotonic() - self._elapsed_start

                if elapsed is not None:
                    title_parts.append(self._format_elapsed(elapsed))

                self.title = " ".join(title_parts)
        else:
            # ── 非 Claude：保持原有行为 ────────────────
            if self._status == "running":
                from ..components.animation import use_spinner
                spinner = use_spinner({"type": "dots", "interval": 80})

                # 构建标题
                title_parts = [f"{spinner['char']} {self._display_name}"]

                # 计算耗时
                if self._elapsed_start is not None:
                    elapsed = time.monotonic() - self._elapsed_start
                    if elapsed >= 60:
                        mins = int(elapsed // 60)
                        secs = int(elapsed % 60)
                        title_parts.append(f"({mins}:{secs:02d})")
                    else:
                        title_parts.append(f"({elapsed:.1f}s)")

                self.title = " ".join(title_parts)
        return super().render()


class ToolResultBlockBox(_MessageBlockBox):
    """工具结果块 — round border + 成功/失败标记。

    构造参数:
        tool_name: 工具名称。
        text: 结果文本内容。
        success: 是否成功，默认 True。
    """

    def __init__(self, tool_name: str = "", text: str = "",
                 success: bool = True, **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["tool_result"])
        if success:
            style["border_color"] = "green"
            style["title"] = f"✓ {tool_name}"
            style["title_color"] = "green"
        else:
            style["border_color"] = "red"
            style["title"] = f"✗ {tool_name}"
            style["title_color"] = "red"
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


# VNode type → Box 子类映射
_VNODE_TYPE_TO_CLASS: dict[str, type[_MessageBlockBox]] = {
    "thinking_block": ThinkingBlockBox,
    "answer_block": AnswerBlockBox,
    "user_messages": UserMsgBlockBox,
    "tool_outputs": ToolOutputBlockBox,
    "tool_calls": ToolCallBlockBox,
    "tool_results": ToolResultBlockBox,
    "notifications": NotificationBlockBox,
    "errors": ErrorBlockBox,
}


def create_message_box(vnode_type: str, text: str = "",
                       **kwargs: Any) -> _MessageBlockBox:
    """工厂函数 — 根据 VNode type 创建对应的消息块 Box 实例。

    Args:
        vnode_type: VNode 类型字符串（如 "thinking_block"、"answer_block" 等）。
        text: 消息内容文本。
        **kwargs: 额外样式覆盖参数。

    Returns:
        对应的 _MessageBlockBox 子类实例。

    Raises:
        KeyError: 如果 vnode_type 不在已知映射中。
    """
    box_cls = _VNODE_TYPE_TO_CLASS[vnode_type]
    return box_cls(text=text, **kwargs)


# ── 导出 ──────────────────────────────────────────

__all__ = [
    "ThinkingBlockBox",
    "AnswerBlockBox",
    "UserMsgBlockBox",
    "ToolOutputBlockBox",
    "ErrorBlockBox",
    "NotificationBlockBox",
    "ToolCallBlockBox",
    "ToolResultBlockBox",
    "TextContent",
    "_MSG_BLOCK_STYLES",
    "_is_narrow_screen",
    "_make_text_component",
    "create_message_box",
]
