"""边框组件 — BoxStyle 枚举 + Box/RoundedBox/DoubleBox 组件。

提供多种风格的边框渲染能力，支持圆角、双线、粗线等样式。
继承 TuiComponent 基类，统一渲染接口。
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from ._base import TuiComponent
from ..render_buffer import RenderBuffer
from ..core.style import Style

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


# ═══════════════════════════════════════════════════════════
# Box 组件
# ═══════════════════════════════════════════════════════════

class Box(TuiComponent):
    """边框渲染器 — 将文本包裹在指定样式的边框中。

    支持多行文本、自定义边框样式、显式宽度、内边距、渐变边框、
    边框四边渐变色、Neon 辉光效果和极光渐变动态边框。

    Args:
        text: 要包裹的文本内容（支持多行，以 \\n 分隔）。
        style: 边框样式，默认为 ROUNDED（圆角）。
        width: 显式指定边框总宽度（含左右边框字符）。
               为 None 时根据文本最大行长度 + 内边距自动计算。
        padding: 文本与左右边框之间的内边距字符数。
        fg_color: 边框字符的前景色 256 色号。为 None 时使用终端默认色。
        border_gradient_start: 水平边框渐变起始色号（可选）。
        border_gradient_end: 水平边框渐变结束色号（可选）。
                              同时设置 start 和 end 时启用渐变边框。
        glow: 启用 Neon 辉光效果。边框字符使用主题 border_glow 色
              或默认亮青色渲染，产生发光视觉效果。
        border_gradient: 统一渐变参数二元组 (start, end)，优先级高于
                         单独的 border_gradient_start/border_gradient_end。
                         同时设置时以此参数为准。
                         启用后四边均应用渐变色（上下水平 + 左右垂直）。
        aurora_gradient: 启用极光渐变边框。边框使用动态极光渐变色号，
                         随 frame 推进产生飘动效果。优先级高于所有其他
                         渐变/glow 参数。
        frame: 当前帧号，极光渐变和呼吸动效推进用。

    Example:
        >>> box = Box("Hello", style=BoxStyle.ROUNDED)
        >>> print(box.render())
        ╭───────╮
        │ Hello │
        ╰───────╯

        >>> # 渐变边框（统一参数）
        >>> box = Box("Gradient", border_gradient=(45, 237))
        >>> print(box.render())

        >>> # Neon 辉光边框
        >>> box = Box("Glow!", glow=True)
        >>> print(box.render())

        >>> # 极光渐变边框
        >>> box = Box("Aurora", aurora_gradient=True, frame=5)
        >>> print(box.render())
    """

    def __init__(
        self,
        text: str,
        style: BoxStyle = BoxStyle.ROUNDED,
        width: int | None = None,
        padding: int = 1,
        fg_color: int | None = None,
        border_gradient_start: int | None = None,
        border_gradient_end: int | None = None,
        glow: bool = False,
        border_gradient: tuple[int, int] | None = None,
        aurora_gradient: bool = False,
        frame: int = 0,
        *,
        props: dict | None = None,
    ) -> None:
        super().__init__(props=props)
        self.text = text
        self.style = style
        self.width = width
        self.padding = padding
        self.fg_color = fg_color
        self.border_gradient_start = border_gradient_start
        self.border_gradient_end = border_gradient_end
        self.glow = glow
        self.border_gradient = border_gradient
        self.aurora_gradient = aurora_gradient
        self.frame = frame

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染带边框的文本。

        窄屏时降级为仅缩进无边框（通过 render_with_narrow_fallback 模板方法）。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时直接写入 buffer。

        Returns:
            str | None: 无 buffer 时返回渲染字符串；有 buffer 时返回 None。
        """
        result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
        if result is not None:
            return result

        result = self._build_box()
        return self._finalize_render(result, buffer)

    def _build_box(self) -> str:
        """构建带边框的文本（核心渲染逻辑）。"""
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

        # ── 极光渐变优先于其他渐变/glow 模式 ─────────────
        # 注：_build_box() 仅在宽屏路径下被调用（由 render() 通过模板方法控制）
        if self.aurora_gradient:
            return self._render_aurora(lines, chars, content_width, pad, text_width)

        # ── 解析渐变参数 ──────────────────────────────────
        # border_gradient 统一参数优先级高于单独的 start/end
        grad_start = self.border_gradient_start
        grad_end = self.border_gradient_end
        if self.border_gradient is not None:
            grad_start, grad_end = self.border_gradient

        # ── 获取辉光 ANSI ──────────────────────────────────
        glow_ansi = ""
        if self.glow:
            from ..core.theme import THEME
            glow_ansi = THEME.get('border_glow', '\033[38;5;81m')

        # ── 构建边框线（支持渐变 + 辉光）──────────────────
        has_gradient = grad_start is not None and grad_end is not None

        if has_gradient:
            from ..core.gradient import gradient_range
            # 水平渐变色号（上 / 下边框）
            h_grad = gradient_range(grad_start, grad_end, content_width)
            # 垂直渐变色号（左 / 右边框）
            num_lines = max(len(lines), 1)
            v_grad = gradient_range(grad_start, grad_end, num_lines)

            # 水平渐变边框字符串
            h_parts = "".join(
                f"\033[38;5;{c}m{chars['h']}" for c in h_grad
            ) + "\033[0m"

            # 四角色号（优先匹配水平渐变：角与水平渐变端点对齐）
            tl_color = h_grad[0]
            tr_color = h_grad[-1]
            bl_color = h_grad[0]    # 与底部水平渐变左端对齐
            br_color = h_grad[-1]   # 与底部水平渐变右端对齐

            # 应用辉光：仅作用于四个角
            if self.glow:
                tl_glow = glow_ansi + f"{chars['tl']}\033[0m"
                tr_glow = glow_ansi + f"{chars['tr']}\033[0m"
                bl_glow = glow_ansi + f"{chars['bl']}\033[0m"
                br_glow = glow_ansi + f"{chars['br']}\033[0m"
                top_border = f"{tl_glow}{h_parts}{tr_glow}"
                bottom_border = f"{bl_glow}{h_parts}{br_glow}"
            else:
                top_border = f"{Style(fg=tl_color).apply(chars['tl'])}{h_parts}{Style(fg=tr_color).apply(chars['tr'])}"
                bottom_border = f"{Style(fg=bl_color).apply(chars['bl'])}{h_parts}{Style(fg=br_color).apply(chars['br'])}"

            # 构建主体行 —— 左右边框使用垂直渐变色
            body: list[str] = []
            for i, line in enumerate(lines):
                vc = v_grad[i] if i < len(v_grad) else v_grad[-1]
                text_part = line[:text_width].ljust(text_width)
                if self.glow:
                    lv = f"{glow_ansi}{chars['v']}\033[0m"
                    rv = f"{glow_ansi}{chars['v']}\033[0m"
                else:
                    lv = f"\033[38;5;{vc}m{chars['v']}\033[0m"
                    rv = f"\033[38;5;{vc}m{chars['v']}\033[0m"
                body.append(f"{lv}{' ' * pad}{text_part}{' ' * pad}{rv}")
        else:
            # 无渐变
            h_line = chars["h"] * content_width
            if self.glow:
                top_border = f"{glow_ansi}{chars['tl']}{h_line}{chars['tr']}\033[0m"
                bottom_border = f"{glow_ansi}{chars['bl']}{h_line}{chars['br']}\033[0m"
            else:
                top_border = f"{chars['tl']}{h_line}{chars['tr']}"
                bottom_border = f"{chars['bl']}{h_line}{chars['br']}"

            # 构建主体行
            body: list[str] = []
            for line in lines:
                text_part = line[:text_width].ljust(text_width)
                if self.glow:
                    lv = f"{glow_ansi}{chars['v']}\033[0m"
                    rv = f"{glow_ansi}{chars['v']}\033[0m"
                else:
                    lv = chars['v']
                    rv = chars['v']
                body.append(f"{lv}{' ' * pad}{text_part}{' ' * pad}{rv}")

        result = f"{top_border}\n" + "\n".join(body) + f"\n{bottom_border}"

        # 应用前景色（最外层包裹，仅在无辉光时生效）
        if self.fg_color is not None and not self.glow:
            result = Style(fg=self.fg_color).apply(result)

        return result

    def _render_aurora(
        self, lines: list[str], chars: dict[str, str],
        content_width: int, pad: int, text_width: int,
    ) -> str:
        """极光渐变边框渲染 — 使用动态极光渐变色号。

        四边全部使用 build_aurora_gradient 生成的动态极光色号，
        frame 推进时产生飘动效果。
        """
        from ..core.effects import build_aurora_gradient
        num_lines = max(len(lines), 1)
        fr = self.frame

        # 生成极光渐变色号
        h_grad = build_aurora_gradient(content_width, fr)
        v_grad = build_aurora_gradient(num_lines, fr)

        # 水平边框
        h_parts = "".join(
            Style(fg=c).apply(chars['h']) for c in h_grad
        )
        top_border = f"{Style(fg=h_grad[0]).apply(chars['tl'])}{h_parts}{Style(fg=h_grad[-1]).apply(chars['tr'])}"
        bottom_border = f"{Style(fg=h_grad[0]).apply(chars['bl'])}{h_parts}{Style(fg=h_grad[-1]).apply(chars['br'])}"

        # 主体行
        body: list[str] = []
        for i, line in enumerate(lines):
            vc = v_grad[i] if i < len(v_grad) else v_grad[-1]
            text_part = line[:text_width].ljust(text_width)
            lv = Style(fg=vc).apply(chars['v'])
            rv = Style(fg=vc).apply(chars['v'])
            body.append(f"{lv}{' ' * pad}{text_part}{' ' * pad}{rv}")

        return f"{top_border}\n" + "\n".join(body) + f"\n{bottom_border}"

    def _render_narrow(self) -> str:
        """窄屏降级：返回缩进的纯文本（无边框）。"""
        lines = self.text.split("\n")
        return "\n".join(f"  {line}" for line in lines)


# ═══════════════════════════════════════════════════════════
# 便捷子类
# ═══════════════════════════════════════════════════════════

class RoundedBox(Box):
    """圆角边框组件 — Box(style=BoxStyle.ROUNDED) 的便捷别名。"""

    def __init__(self, text: str, *, props: dict | None = None, **kwargs) -> None:
        super().__init__(text, style=BoxStyle.ROUNDED, props=props, **kwargs)


class DoubleBox(Box):
    """双线边框组件 — Box(style=BoxStyle.DOUBLE) 的便捷别名。"""

    def __init__(self, text: str, *, props: dict | None = None, **kwargs) -> None:
        super().__init__(text, style=BoxStyle.DOUBLE, props=props, **kwargs)
