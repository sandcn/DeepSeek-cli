"""边框组件 — BoxStyle 枚举 + Box/RoundedBox/DoubleBox 组件。

提供多种风格的边框渲染能力，支持圆角、双线、粗线等样式。
继承 TuiComponent 基类，统一渲染接口。
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from ._base import TuiComponent
from ..terminal.narrow import is_narrow

__all__ = [
    "BoxStyle",
    "Box",
    "RoundedBox",
    "DoubleBox",
]


class BoxStyle(str, Enum):
    """边框样式枚举。

    使用 str + Enum 混合继承，在 Python 3.9 下获得字符串枚举行为。
    值即样式名，可直接参与字符串比较和格式化。
    """
    ROUNDED = "rounded"
    DOUBLE = "double"
    HEAVY = "heavy"
    DOTTED = "dotted"
    DASHED = "dashed"
    ASCII = "ascii"


# ═══════════════════════════════════════════════════════════
# 边框字符集
# 每种样式定义 6 个键：tl(左上) tr(右上) bl(左下) br(右下) h(水平) v(垂直)
# ═══════════════════════════════════════════════════════════

_BOX_CHARS: dict[BoxStyle, dict[str, str]] = {
    BoxStyle.ROUNDED: {
        "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
        "h": "─", "v": "│",
    },
    BoxStyle.DOUBLE: {
        "tl": "╔", "tr": "╗", "bl": "╚", "br": "╝",
        "h": "═", "v": "║",
    },
    BoxStyle.HEAVY: {
        "tl": "┏", "tr": "┓", "bl": "┗", "br": "┛",
        "h": "━", "v": "┃",
    },
    BoxStyle.DOTTED: {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "┄", "v": "┆",
    },
    BoxStyle.DASHED: {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "╌", "v": "╎",
    },
    BoxStyle.ASCII: {
        "tl": "+", "tr": "+", "bl": "+", "br": "+",
        "h": "-", "v": "|",
    },
}


def _wrap_ansi(color: int, text: str) -> str:
    """用指定 256 色号包裹文本为 ANSI 转义序列。"""
    return f"\033[38;5;{color}m{text}\033[0m"


# ═══════════════════════════════════════════════════════════
# Box 组件
# ═══════════════════════════════════════════════════════════

class Box(TuiComponent):
    """边框渲染器 — 将文本包裹在指定样式的边框中。

    支持多行文本、自定义边框样式、显式宽度和内边距。

    Args:
        text: 要包裹的文本内容（支持多行，以 \\n 分隔）。
        style: 边框样式，默认为 ROUNDED（圆角）。
        width: 显式指定边框总宽度（含左右边框字符）。
               为 None 时根据文本最大行长度 + 内边距自动计算。
        padding: 文本与左右边框之间的内边距字符数。
        fg_color: 边框字符的前景色 256 色号。为 None 时使用终端默认色。

    Example:
        >>> box = Box("Hello", style=BoxStyle.ROUNDED)
        >>> print(box.render())
        ╭───────╮
        │ Hello │
        ╰───────╯
    """

    def __init__(
        self,
        text: str,
        style: BoxStyle = BoxStyle.ROUNDED,
        width: int | None = None,
        padding: int = 1,
        fg_color: int | None = None,
    ) -> None:
        self.text = text
        self.style = style
        self.width = width
        self.padding = padding
        self.fg_color = fg_color

    def render(self) -> str:
        """渲染带边框的文本。

        窄屏时降级为仅缩进无边框（调用 is_narrow() 检测）。
        """
        if is_narrow():
            return self._render_narrow()

        lines = self.text.split("\n")
        max_line_len = max((len(line) for line in lines), default=0)
        chars = _BOX_CHARS[self.style]
        pad = self.padding

        # 计算内容区宽度（不含左右边框字符）
        if self.width is not None:
            content_width = max(self.width - 2, 1)
        else:
            content_width = max(max_line_len + 2 * pad, 1)

        # 每行文本可用的实际显示宽度（扣除左右内边距后）
        text_width = max(content_width - 2 * pad, 0)

        # 构建边框线
        h_line = chars["h"] * content_width
        top_border = f"{chars['tl']}{h_line}{chars['tr']}"
        bottom_border = f"{chars['bl']}{h_line}{chars['br']}"

        # 构建主体行
        body: list[str] = []
        for line in lines:
            text_part = line[:text_width].ljust(text_width)
            body.append(f"{chars['v']}{' ' * pad}{text_part}{' ' * pad}{chars['v']}")

        result = f"{top_border}\n" + "\n".join(body) + f"\n{bottom_border}"

        # 应用前景色
        if self.fg_color is not None:
            result = _wrap_ansi(self.fg_color, result)

        return result

    def _render_narrow(self) -> str:
        """窄屏降级：返回缩进的纯文本（无边框）。"""
        lines = self.text.split("\n")
        return "\n".join(f"  {line}" for line in lines)


# ═══════════════════════════════════════════════════════════
# 便捷子类
# ═══════════════════════════════════════════════════════════

class RoundedBox(Box):
    """圆角边框组件 — Box(style=BoxStyle.ROUNDED) 的便捷别名。"""

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(text, style=BoxStyle.ROUNDED, **kwargs)


class DoubleBox(Box):
    """双线边框组件 — Box(style=BoxStyle.DOUBLE) 的便捷别名。"""

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(text, style=BoxStyle.DOUBLE, **kwargs)
