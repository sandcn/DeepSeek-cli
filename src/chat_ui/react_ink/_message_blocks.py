"""消息块 Box 包装组件 — React Ink Box 边框的消息流组件包装器。

为 6 种消息块类型提供声明式 Box 边框渲染：
  - ThinkingBlockBox: 推理块（dim single border）
  - AnswerBlockBox: 回答块（dim single border）
  - UserMsgBlockBox: 用户消息块（cyan single border）
  - ToolOutputBlockBox: 工具输出块（yellow dim single border）
  - ErrorBlockBox: 错误块（red single border）
  - NotificationBlockBox: 通知块（green borderless）

窄屏（< 40 列）自动降级为无边框模式。

样式集中管理在 _MSG_BLOCK_STYLES 字典中，未来新增块类型或调整样式
只需修改此字典。工厂函数 create_message_box() 根据 VNode type 字符串
自动创建对应的 Box + TextContent 实例。
"""

from __future__ import annotations

import shutil
from typing import Any

from ._box import Box
from .._components import TuiComponent


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
        "border_style": "single",
        "border_color": "bright_black",  # dim gray
        "border_dim_color": True,
        "padding_x": 1,
        "padding_y": 0,
        "margin_y": 0,
    },
    "answer": {
        "border_style": "single",
        "border_color": "bright_black",
        "border_dim_color": True,
        "padding_x": 1,
        "padding_y": 0,
        "margin_y": 0,
    },
    "user_msg": {
        "border_style": "single",
        "border_color": "cyan",
        "padding_x": 1,
        "margin_y": 0,
    },
    "tool_output": {
        "border_style": "single",
        "border_color": "yellow",
        "border_dim_color": True,
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
    """推理块 — dim single border。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["thinking"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class AnswerBlockBox(_MessageBlockBox):
    """回答块 — dim single border。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["answer"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class UserMsgBlockBox(_MessageBlockBox):
    """用户消息块 — cyan single border。"""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        style = dict(_MSG_BLOCK_STYLES["user_msg"])
        style.update(kwargs)
        super().__init__(**style)
        if text:
            self.add_child(_make_text_component(text))


class ToolOutputBlockBox(_MessageBlockBox):
    """工具输出块 — yellow dim single border。"""

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


# VNode type → Box 子类映射
_VNODE_TYPE_TO_CLASS: dict[str, type[_MessageBlockBox]] = {
    "thinking_block": ThinkingBlockBox,
    "answer_block": AnswerBlockBox,
    "user_messages": UserMsgBlockBox,
    "tool_outputs": ToolOutputBlockBox,
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
    "TextContent",
    "_MSG_BLOCK_STYLES",
    "_is_narrow_screen",
    "_make_text_component",
    "create_message_box",
]
