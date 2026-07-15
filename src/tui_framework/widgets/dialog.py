"""Dialog 对话框控件 — 模态/非模态弹窗，支持标题+内容+按钮行。

设计模式: 组合 — Dialog 包含 Button 子控件作为按钮行。

动效：首次渲染时触发 ``fade_in`` 渐显入场动画。
"""

from __future__ import annotations

import logging

from tui_framework.animation.declarative import effect
from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.animated import AnimatedWidget
from tui_framework.widgets.base import TuiComponent, Widget
from tui_framework.widgets.button import Button

_logger = logging.getLogger(__name__)


# ── 辅助: 计算文本视觉宽度 ──────────────────────────────


def _visual_width(text: str) -> int:
    """估算文本的终端视觉宽度（跳过 ANSI 序列）。"""
    from tui_framework.core.ansi_utils import visual_width
    return visual_width(text)


def _repeat_char(ch: str, count: int) -> str:
    """重复字符 count 次，count <= 0 时返回空字符串。"""
    if count <= 0:
        return ""
    return ch * count


# ── Dialog ───────────────────────────────────────────────


@effect("appear", type="fade_in", duration=8, easing="smooth")
class Dialog(AnimatedWidget):
    """对话框控件。

    ## 渲染结构

    ::

        ┌─ Title ────────────┐
        │                     │
        │  Content            │
        │                     │
        ├─────────────────────┤
        │  [ OK ] [ Cancel ]  │
        └─────────────────────┘

    ## 交互行为

    **模态模式**（``modal=True``）:
    - 键盘事件仅对话框内部处理（不冒泡给父容器）
    - ``escape`` → 关闭对话框（触发 ``on_close``）

    **非模态模式**（``modal=False``）:
    - 键盘事件正常冒泡
    - ``escape`` → 关闭对话框

    ## 按钮行

    通过 ``buttons`` 属性设置按钮列表，每个按钮可绑定独立的 ``on_click`` 回调。
    默认按索引顺序渲染，焦点在对话框内时可以导航按钮。

    Args:
        title: 对话框标题。
        content: 对话框内容（字符串或 Widget 实例）。
        buttons: 按钮列表（Button 实例）。
        modal: 是否模态（默认 True）。
        width: 对话框宽度（0 表示自适应内容宽度，默认 50）。
    """

    # 边框字符
    BOX_TL: str = "┌"
    BOX_TR: str = "┐"
    BOX_BL: str = "└"
    BOX_BR: str = "┘"
    BOX_H: str = "─"
    BOX_V: str = "│"
    BOX_MERGE_L: str = "├"
    BOX_MERGE_R: str = "┤"
    SEP: str = "─"

    # 内边距
    PADDING_H: int = 2
    PADDING_V: int = 1

    def __init__(
        self,
        title: str = "",
        content: str | Widget | TuiComponent = "",
        buttons: list[Button] | None = None,
        modal: bool = True,
        width: int = 50,
    ) -> None:
        super().__init__()
        self.title: str = title
        self._content: str | Widget | TuiComponent = content
        self._buttons: list[Button] = list(buttons) if buttons else []
        self.modal: bool = modal
        self._width: int = max(10, width)
        self._appear_triggered: bool = False  # 只触发一次入场动效

        # 回调
        self.on_close: object | None = None  # Callable[[], None]

    # ── 属性 ────────────────────────────────────────────

    @property
    def content(self) -> str | Widget | TuiComponent:
        """对话框内容。"""
        return self._content

    @content.setter
    def content(self, value: str | Widget | TuiComponent) -> None:
        self._content = value

    @property
    def buttons(self) -> list[Button]:
        """按钮列表。"""
        return self._buttons

    @buttons.setter
    def buttons(self, value: list[Button]) -> None:
        self._buttons = list(value)

    @property
    def width(self) -> int:
        """对话框宽度（终端列数）。"""
        return self._width

    @width.setter
    def width(self, value: int) -> None:
        self._width = max(10, value)

    def add_button(self, button: Button) -> None:
        """添加按钮。"""
        self._buttons.append(button)

    def remove_button(self, button: Button) -> None:
        """移除按钮。"""
        if button in self._buttons:
            self._buttons.remove(button)

    # ── 事件处理 ────────────────────────────────────────

    def handle_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件（覆写模板方法）。"""
        try:
            return self._do_handle_key(event)
        except Exception:
            _logger.exception("Dialog.handle_key() 异常 [id=%s, key=%s]",
                              self._id, getattr(event, 'key', '?'))
            return False

    def _do_handle_key(self, event: KeyPressEvent) -> bool:
        """实际键盘事件处理逻辑。"""
        if self._disabled or not self._visible:
            return False

        # ESC 关闭对话框
        if event.key == "escape":
            self._close()
            return True

        # Enter → 触发第一个按钮
        if event.key == "enter" and self._buttons and not self._disabled:
            btn = self._buttons[0]
            if btn.on_click is not None:
                try:
                    btn.on_click()
                except Exception:
                    _logger.exception("Dialog button click 异常")
            return True

        return False

    def _close(self) -> None:
        """关闭对话框。"""
        if self.on_close is not None:
            try:
                self.on_close()  # type: ignore[misc]
            except Exception:
                _logger.exception("Dialog.on_close() 异常")

    # ── 渲染 ────────────────────────────────────────────

    def _resolve_border_color(self) -> str:
        """解析边框颜色。"""
        return self.resolve_theme_color("border", "\033[38;5;239m")

    def _resolve_title_color(self) -> str:
        """解析标题颜色。"""
        return self.resolve_theme_color("title", "\033[38;5;45m")

    def _resolve_content_color(self) -> str:
        """解析内容颜色。"""
        return self.resolve_theme_color("info", "\033[38;5;242m")

    def _resolve_button_row_color(self) -> str:
        """解析按钮行分隔符颜色。"""
        return self.resolve_theme_color("divider", "\033[38;5;239m")

    def render(self) -> str:
        """渲染对话框（首次渲染时触发渐显入场动效）。"""
        if not self._visible:
            return ""

        # 首次渲染触发入场动效
        if not self._appear_triggered:
            self._appear_triggered = True
            self.trigger_effect("appear")

        reset = "\033[0m"
        border_c = self._resolve_border_color()
        title_c = self._resolve_title_color()

        w = self._width
        inner_w = w - 2  # 减去左右边框

        # ── 内容文本 ──
        if isinstance(self._content, (Widget, TuiComponent)):
            content_text = self._content.render()
        else:
            content_text = str(self._content)

        content_lines = content_text.split("\n") if content_text else [""]

        # 包装内容到 inner_w 宽度
        wrapped_lines: list[str] = []
        for line in content_lines:
            # 简单按字符截断（非 ANSI 安全，但对话框内容通常较短）
            while len(line) > inner_w:
                wrapped_lines.append(line[:inner_w])
                line = line[inner_w:]
            wrapped_lines.append(line)

        # ── 按钮行 ──
        button_row = self._render_button_row(reset)
        button_row_w = _visual_width(self._strip_ansi(button_row))

        # ── 标题行 ──
        title_display = self.title[:inner_w - 2] if len(self.title) > inner_w - 2 else self.title
        title_line = f"{border_c}{self.BOX_TL}{title_c} {title_display} {reset}"
        # 填充剩余宽度
        title_visual = _visual_width(self._strip_ansi(title_line))
        pad = inner_w + 1 - title_visual
        title_line += f"{border_c}{_repeat_char(self.BOX_H, max(0, pad))}{self.BOX_TR}{reset}"

        lines: list[str] = []
        lines.append(title_line)

        # ── 上内边距 ──
        for _ in range(self.PADDING_V):
            lines.append(f"{border_c}{self.BOX_V}{' ' * inner_w}{self.BOX_V}{reset}")

        # ── 内容行 ──
        content_c = self._resolve_content_color()
        for line in wrapped_lines:
            pad_right = inner_w - len(line)
            lines.append(
                f"{border_c}{self.BOX_V}{content_c}{line}{reset}"
                f"{' ' * max(0, pad_right)}{border_c}{self.BOX_V}{reset}"
            )

        # ── 下内边距 ──
        for _ in range(self.PADDING_V):
            lines.append(f"{border_c}{self.BOX_V}{' ' * inner_w}{self.BOX_V}{reset}")

        # ── 按钮行分隔 ──
        lines.append(
            f"{border_c}{self.BOX_MERGE_L}{_repeat_char(self.SEP, inner_w)}{self.BOX_MERGE_R}{reset}"
        )

        # ── 按钮行 ──
        button_pad_left = (inner_w - button_row_w) // 2
        button_pad_right = inner_w - button_row_w - button_pad_left
        lines.append(
            f"{border_c}{self.BOX_V}{' ' * button_pad_left}"
            f"{button_row}"
            f"{' ' * button_pad_right}{border_c}{self.BOX_V}{reset}"
        )

        # ── 底部边框 ──
        lines.append(f"{border_c}{self.BOX_BL}{_repeat_char(self.BOX_H, inner_w)}{self.BOX_BR}{reset}")

        content = "\n".join(lines)
        return self._apply_effects(content)

    def _render_button_row(self, reset: str) -> str:
        """渲染按钮行。"""
        if not self._buttons:
            return ""
        sep_color = self._resolve_button_row_color()
        parts: list[str] = []
        for btn in self._buttons:
            parts.append(btn.render())
        return f" {sep_color}│{reset} ".join(parts)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """去除 ANSI 序列的辅助函数。"""
        from tui_framework.core.ansi_utils import strip_ansi
        return strip_ansi(text)
