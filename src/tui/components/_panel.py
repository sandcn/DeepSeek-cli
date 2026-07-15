"""面板组件 — Panel。

带标题的边框容器，支持 content 嵌套（str | TuiComponent）和窄屏降级。
内部使用 _box.py 的 BoxStyle 枚举和边框字符集渲染边框。

设计模式: 装饰器 (Decorator) — Panel 在 content 外部装饰边框和标题，
不修改 content 本身的渲染逻辑。

窄屏降级：is_narrow() 时简化为无边框的标题行 + 内容。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from ..terminal.narrow import is_narrow
from ..core.text_utils import truncate
from ..core.ansi_utils import visual_width
from ._base import TuiComponent, _estimate_content_lines
from ._box import BoxStyle, _BOX_CHARS

_logger = logging.getLogger(__name__)

__all__ = [
    "Panel",
]


class Panel(TuiComponent):
    """带标题的边框容器组件。

    在 content 外部装饰边框和标题，不修改 content 本身的渲染逻辑。
    支持窄屏降级：is_narrow() 时简化为无边框的标题行 + 内容。

    Attributes:
        title: 标题文本。
        content: 内容（str 或 TuiComponent 实例）。
        style: 边框样式，默认 ROUNDED。
        width: 显式宽度（含边框），None 时自动计算。
        title_align: 标题对齐方式，"left" / "center" / "right"。
        border_color: 边框字符色号，None 时使用终端默认色。
        title_color: 标题色号，None 时使用终端默认色。
        padding: 内容与边框之间的填充空格数。
    """

    def __init__(
        self,
        title: str,
        content: str | TuiComponent,
        style: BoxStyle = BoxStyle.ROUNDED,
        width: int | None = None,
        title_align: str = "left",
        border_color: int | None = None,
        title_color: int | None = None,
        padding: int = 1,
    ) -> None:
        self.title = title
        self.content = content
        self.style = style
        self.width = width
        self.title_align = title_align
        self.border_color = border_color
        self.title_color = title_color
        self.padding = padding

    # ── 核心渲染接口 ────────────────────────────────────

    def render(self) -> str:
        """渲染面板组件。

        窄屏时调用 _render_narrow() 降级输出；
        宽屏时调用 _render_normal() 输出带标题的边框容器。
        """
        if is_narrow():
            return self._render_narrow()
        return self._render_normal()

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染面板。

        Args:
            adapter: OutputAdapter 实例。

        Returns:
            int: 渲染内容的估计行数。
        """
        output = self.render()
        adapter.write(output)
        return _estimate_content_lines(output)

    # ── 窄屏降级 ────────────────────────────────────────

    def _render_narrow(self) -> str:
        """窄屏降级渲染：无边框的标题行 + 内容。"""
        content_str = self._resolve_content()
        if self.title:
            # 窄屏下 title 也做截断避免超宽
            title_part = truncate(self.title, 40)
            return f"  {title_part}\n{content_str}"
        return content_str

    # ── 标准渲染（宽屏） ────────────────────────────────

    def _render_normal(self) -> str:
        """标准渲染：带标题的边框容器。

        构建流程：
          1. 从 _BOX_CHARS 获取当前样式的边框字符集
          2. 计算内容最大视觉宽度，确定边框宽度
          3. 构建顶部边框（含标题）、内容行、底部边框
          4. 对超长行进行截断处理
        """
        chars = _BOX_CHARS[self.style]
        content_str = self._resolve_content()
        content_lines = content_str.split('\n')

        # ── 计算边框宽度 ──
        max_content_width = max(
            (visual_width(line) for line in content_lines),
            default=0,
        )
        inner_width = max_content_width + 2 * self.padding
        box_width = inner_width + 2  # 左右边框字符各占 1 列

        if self.width is not None:
            box_width = self.width
            inner_width = max(1, box_width - 2)

        # ── ANSI 颜色前缀/后缀 ──
        border_pre, border_suf = self._ansi_wrap(self.border_color)
        title_pre, title_suf = self._ansi_wrap(self.title_color)

        # ── 展开边框字符 ──
        tl, tr = chars['tl'], chars['tr']
        bl, br = chars['bl'], chars['br']
        h, v = chars['h'], chars['v']

        # ── 顶部边框（含标题） ──
        top_line = self._build_top_border(
            tl, tr, h, box_width,
            title_pre, title_suf, border_pre, border_suf,
        )

        # ── 内容行 ──
        content_lines_rendered = self._build_content_lines(
            content_lines, v, inner_width,
            border_pre, border_suf,
        )

        # ── 底部边框 ──
        bottom_line = (
            f"{border_pre}{bl}{h * (box_width - 2)}{br}{border_suf}"
        )

        return '\n'.join([top_line] + content_lines_rendered + [bottom_line])

    # ── 内部辅助 ────────────────────────────────────────

    def _resolve_content(self) -> str:
        """解析 content 为字符串。

        若 content 是 TuiComponent 实例，调用其 render() 获取输出；
        否则直接返回 str(content)。
        """
        if isinstance(self.content, TuiComponent):
            return str(self.content.render())
        return str(self.content)

    @staticmethod
    def _ansi_wrap(color: int | None) -> tuple[str, str]:
        """生成 ANSI 颜色包裹前缀/后缀。

        Args:
            color: 256 色号，None 时返回空字符串对。

        Returns:
            (prefix, suffix) 元组，prefix 为 ANSI 前景色序列，
            suffix 为 RESET 序列。color 为 None 时均为空字符串。
        """
        if color is not None:
            return (f"\033[38;5;{color}m", "\033[0m")
        return ("", "")

    def _build_top_border(
        self,
        tl: str,
        tr: str,
        h: str,
        box_width: int,
        title_pre: str,
        title_suf: str,
        border_pre: str,
        border_suf: str,
    ) -> str:
        """构建顶部边框行（含标题）。

        格式：{tl}{h*left}[ {title} ]{h*right}{tr}

        Args:
            tl: 左上角字符。
            tr: 右上角字符。
            h: 水平边框字符。
            box_width: 边框总宽度。
            title_pre: 标题 ANSI 颜色前缀。
            title_suf: 标题 ANSI 颜色后缀。
            border_pre: 边框 ANSI 颜色前缀。
            border_suf: 边框 ANSI 颜色后缀。

        Returns:
            带 ANSI 颜色的顶部边框字符串。
        """
        has_title = bool(self.title)
        if not has_title:
            return f"{border_pre}{tl}{h * (box_width - 2)}{tr}{border_suf}"

        # 标题装饰部分: "[ {title} ]"
        title_deco = f"[ {self.title} ]"
        title_vw = visual_width(title_deco)

        fill_width = box_width - 2  # 减去 tl + tr

        if title_vw > fill_width:
            # 标题超宽：截断，保留括号和空格空间
            # 括号 + 空格 = 4 个视觉宽度: "[ ", " ]"
            max_title_vw = fill_width - 4
            if max_title_vw < 1:
                # 极窄情况，仅显示截断标题
                self.title = truncate(self.title, max(1, fill_width - 2))
                title_deco = self.title
                title_vw = visual_width(title_deco)
            else:
                self.title = truncate(self.title, max_title_vw)
                title_deco = f"[ {self.title} ]"
                title_vw = visual_width(title_deco)

        remaining = fill_width - title_vw

        if self.title_align == "left":
            left_h = 0
            right_h = remaining
        elif self.title_align == "right":
            left_h = remaining
            right_h = 0
        else:  # center
            left_h = remaining // 2
            right_h = remaining - left_h

        return (
            f"{border_pre}{tl}{h * left_h}"
            f"{title_pre}{title_deco}{title_suf}"
            f"{border_pre}{h * right_h}{tr}{border_suf}"
        )

    def _build_content_lines(
        self,
        lines: list[str],
        v_char: str,
        inner_width: int,
        border_pre: str,
        border_suf: str,
    ) -> list[str]:
        """构建内容行（带左右边框字符）。

        每行格式：{v}{padding}{line}{padding}{v}

        Args:
            lines: 分割后的内容行列表。
            v_char: 垂直边框字符。
            inner_width: 内部可用宽度（不含边框）。
            border_pre: 边框 ANSI 颜色前缀。
            border_suf: 边框 ANSI 颜色后缀。

        Returns:
            渲染后的边框内容行列表。
        """
        result: list[str] = []
        pad = self.padding
        for line in lines:
            line_vw = visual_width(line)
            if line_vw > inner_width:
                line = truncate(line, inner_width)
                line_vw = visual_width(line)

            pad_right = inner_width - line_vw
            padded_line = ' ' * pad + line + ' ' * pad_right

            result.append(
                f"{border_pre}{v_char}{border_suf}"
                f"{padded_line}"
                f"{border_pre}{v_char}{border_suf}"
            )
        return result
