"""纯 Python 样式化文本模块 — Span + StyledText。

替代 rich.text.Text，无第三方依赖。
提供样式化文本的组装、拼接和 ANSI 解析功能。

使用示例:
    from ..infrastructure.styled import StyledText
    t = StyledText("hello", fg="red", bold=True)
    print(str(t))  # 输出 ANSI 包裹的红色加粗文本
    print(t.plain)  # 输出纯文本 "hello"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..infrastructure.ansi import (style, _RESET, ANSI_RESET, _fg_code, _bg_code,
                    ANSI_BOLD, ANSI_DIM, ANSI_ITALIC, ANSI_UNDERLINE, ANSI_REVERSE)

_logger = logging.getLogger(__name__)


@dataclass
class Span:
    """样式化文本片段。

    Attributes:
        text: 文本内容
        fg: 前景色名（如 'red', '#FF0000'），与 color_number 互斥
        bg: 背景色名，与 bg_color_number 互斥
        color_number: 256 色前景色号（0-255），优先于 fg
        bg_color_number: 256 色背景色号（0-255），优先于 bg
        bold/dim/italic/underline/reverse/strikethrough: 样式标志
    """
    text: str
    fg: str | None = None
    bg: str | None = None
    color_number: int | None = None
    bg_color_number: int | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False
    strikethrough: bool = False

    def to_ansi(self) -> str:
        """将 Span 转为 ANSI 包裹的字符串（含重置码）。

        当 color_number/bg_color_number 存在时，生成合并的 SGR 序列
        （如 \\033[1;38;5;45mtext\\033[0m），与原始硬编码 ANSI 逐字节兼容。
        """
        # 使用 to_ansi_raw 的合并 SGR 逻辑生成前缀
        prefix = _span_to_ansi_prefix(self)
        if not prefix:
            return self.text
        return f"{prefix}{self.text}{ANSI_RESET}"


def _span_to_ansi_prefix(span: Span) -> str:
    """从 Span 生成合并的 ANSI 前缀字符串（不含文本和重置码）。

    当存在 color_number/bg_color_number 时，尽量合并为单条 SGR 序列；
    否则回退到 _fg_code/_bg_code 拼接模式。
    """
    params: list[int] = []
    if span.bold:            params.append(1)
    if span.dim:             params.append(2)
    if span.italic:          params.append(3)
    if span.underline:       params.append(4)
    if span.reverse:         params.append(7)
    if span.strikethrough:   params.append(9)

    # 前景色
    if span.color_number is not None:
        params.extend([38, 5, span.color_number])
    elif span.fg:
        codes = [f"\033[{p}m" for p in params] if params else []
        codes.append(_fg_code(span.fg))
        return "".join(codes)

    # 背景色
    if span.bg_color_number is not None:
        params.extend([48, 5, span.bg_color_number])
    elif span.bg:
        codes = [f"\033[{p}m" for p in params] if params else []
        codes.append(_bg_code(span.bg))
        return "".join(codes)

    if not params:
        return ""
    return f"\033[{';'.join(map(str, params))}m"


class StyledText:
    """样式化文本 — 纯 Python 替代 rich.text.Text。

    支持:
    - 单段样式文本: StyledText("hello", fg="red", bold=True)
    - 多段组装: StyledText.assemble(("hello", style_str), (" world", style_str))
    - ANSI 解析: StyledText.from_ansi("\\033[31mred\\033[0m")
    - 纯文本提取: .plain 属性
    - ANSI 渲染: str() / .render_str()
    """

    def __init__(self, text: str = "", *,
                 fg: str | None = None, bg: str | None = None,
                 color_number: int | None = None,
                 bg_color_number: int | None = None,
                 bold: bool = False, dim: bool = False,
                 italic: bool = False, underline: bool = False,
                 reverse: bool = False, strikethrough: bool = False):
        """创建单段样式文本。

        Args:
            text: 文本内容
            fg/bg: 前景/背景色名（与 color_number/bg_color_number 互斥）
            color_number: 256 色前景色号（0-255），优先于 fg
            bg_color_number: 256 色背景色号（0-255），优先于 bg
            bold/dim/italic/underline/reverse/strikethrough: 样式标志
        """
        self._spans: list[Span] = []
        if text:
            self._spans.append(Span(
                text=text, fg=fg, bg=bg,
                color_number=color_number, bg_color_number=bg_color_number,
                bold=bold, dim=dim, italic=italic,
                underline=underline, reverse=reverse,
                strikethrough=strikethrough,
            ))

    # ── 属性 ──────────────────────────────────────────────

    @property
    def spans(self) -> list[Span]:
        """样式化文本片段列表（浅拷贝）。"""
        return list(self._spans)

    @property
    def plain(self) -> str:
        """返回无样式纯文本。"""
        return "".join(s.text for s in self._spans)

    def __str__(self) -> str:
        """返回 ANSI 包裹的字符串（用于 print / adapter.write）。"""
        return self.render_str()

    def __repr__(self) -> str:
        return f"StyledText({self.plain!r})"

    def render_str(self) -> str:
        """渲染为 ANSI 转义字符串。"""
        return "".join(s.to_ansi() for s in self._spans)

    def __rich_console__(self, console, options):
        """Rich Console 协议 — 将 StyledText 转换为 Rich Text 渲染。

        当 StyledText 传递给 Console.print() 时（如 OutputAdapter.write()），
        Rich 会调用此方法获取原生 Rich Text 对象，避免将 ANSI 字符串
        当作 Rich markup 解析导致颜色丢失。

        此方法依赖 rich（仅在 Rich 渲染路径触发时惰性导入），
        StyledText 的其他路径保持纯 Python。
        """
        from rich.style import Style as RichStyle
        from rich.text import Text as RichText

        result = RichText()
        for span in self._spans:
            style_kwargs: dict = {}
            if span.color_number is not None:
                style_kwargs["color"] = f"#{_256_to_hex(span.color_number)}"
            elif span.fg:
                style_kwargs["color"] = span.fg
            if span.bg_color_number is not None:
                style_kwargs["bgcolor"] = f"#{_256_to_hex(span.bg_color_number)}"
            elif span.bg:
                style_kwargs["bgcolor"] = span.bg
            if span.bold:
                style_kwargs["bold"] = True
            if span.dim:
                style_kwargs["dim"] = True
            if span.italic:
                style_kwargs["italic"] = True
            if span.underline:
                style_kwargs["underline"] = True
            if span.reverse:
                style_kwargs["reverse"] = True
            if span.strikethrough:
                style_kwargs["strike"] = True
            rich_style = RichStyle(**style_kwargs) if style_kwargs else None
            result.append(span.text, style=rich_style)
        yield result

    # ── 工厂方法 ──────────────────────────────────────────

    @staticmethod
    def assemble(*segments: str | tuple) -> StyledText:
        """组装多段样式文本。

        支持两种参数格式:
        - 纯字符串: StyledText.assemble("hello ", "world")
        - (text, style_str) 元组: StyledText.assemble(("hello", "red"), (" world", "bold"))
          其中 style_str 格式: "red", "bold red", "bold dim red" 等空格分隔的修饰符

        Args:
            *segments: 字符串或 (text, style_str) 元组序列

        Returns:
            组装后的 StyledText 实例
        """
        result = StyledText.__new__(StyledText)
        result._spans = []

        for seg in segments:
            if isinstance(seg, str):
                result._spans.append(Span(text=seg))
            elif isinstance(seg, tuple) and len(seg) >= 1:
                text = seg[0]
                style_str = seg[1] if len(seg) > 1 and seg[1] else ""

                # 解析 style_str 为样式属性
                kwargs: dict = {}
                if style_str:
                    # 如果 style_str 直接是一个 ANSI 字符串常量（如 _ANSI_RED）
                    # 我们需要判断这是颜色常量还是样式描述
                    parts = style_str.split() if isinstance(style_str, str) else []
                    for p in parts:
                        p_lower = p.lower()
                        if p_lower in ("bold", "dim", "italic", "underline", "reverse", "strikethrough"):
                            kwargs[p_lower] = True
                        elif p_lower in ("red", "green", "yellow", "blue", "magenta", "cyan",
                                         "white", "black", "bright_red", "bright_green",
                                         "bright_yellow", "bright_blue", "bright_magenta",
                                         "bright_cyan", "bright_white"):
                            kwargs["fg"] = p_lower
                        else:
                            # 不可识别的样式，忽略
                            _logger.debug("assemble: 不可识别的样式标记: %r", p)
                            pass

                result._spans.append(Span(text=str(text), **kwargs))
            elif isinstance(seg, StyledText):
                # 支持拼接已有的 StyledText
                result._spans.extend(seg.spans)

        return result

    @staticmethod
    def from_ansi(text: str) -> StyledText:
        """从 ANSI 转义文本解析为 StyledText。

        解析 `\\033[...m` 序列，将 SGR 参数映射为 Span 样式。
        256 色（38;5;N / 48;5;N）保留原始色号，
        标准 16 色和 bright 变体映射为色名。

        Args:
            text: 含 ANSI 转义序列的文本

        Returns:
            解析后的 StyledText 实例
        """
        result = StyledText.__new__(StyledText)
        result._spans = []

        # 解析 ANSI SGR 序列
        ansi_re = re.compile(r'\033\[([\d;]*)m')

        current_fg: str | None = None
        current_bg: str | None = None
        current_color_number: int | None = None
        current_bg_color_number: int | None = None
        current_bold = False
        current_dim = False
        current_italic = False
        current_underline = False
        current_reverse = False
        current_strikethrough = False

        def _make_span(t: str) -> Span:
            return Span(
                text=t,
                fg=current_fg, bg=current_bg,
                color_number=current_color_number,
                bg_color_number=current_bg_color_number,
                bold=current_bold, dim=current_dim,
                italic=current_italic, underline=current_underline,
                reverse=current_reverse, strikethrough=current_strikethrough,
            )

        pos = 0
        for m in ansi_re.finditer(text):
            # 输出前面的纯文本
            if m.start() > pos:
                plain = text[pos:m.start()]
                if plain:
                    result._spans.append(_make_span(plain))

            # 解析 SGR 参数
            params_str = m.group(1)
            if params_str == "" or params_str == "0":
                # 重置
                current_fg = None; current_bg = None
                current_color_number = None; current_bg_color_number = None
                current_bold = False; current_dim = False
                current_italic = False; current_underline = False
                current_reverse = False; current_strikethrough = False
            else:
                params = [int(p) for p in params_str.split(";") if p]
                i = 0
                while i < len(params):
                    p = params[i]
                    if p == 0:
                        current_fg = None; current_bg = None
                        current_color_number = None; current_bg_color_number = None
                        current_bold = False; current_dim = False
                        current_italic = False; current_underline = False
                        current_reverse = False; current_strikethrough = False
                    elif p == 1: current_bold = True
                    elif p == 2: current_dim = True
                    elif p == 3: current_italic = True
                    elif p == 4: current_underline = True
                    elif p == 7: current_reverse = True
                    elif p == 9: current_strikethrough = True
                    elif p == 22: current_bold = False; current_dim = False
                    elif p == 23: current_italic = False
                    elif p == 24: current_underline = False
                    elif p == 27: current_reverse = False
                    elif p == 29: current_strikethrough = False
                    elif p == 39: current_fg = None; current_color_number = None
                    elif p == 49: current_bg = None; current_bg_color_number = None
                    elif 30 <= p <= 37:
                        fg_map = {30:"black",31:"red",32:"green",33:"yellow",
                                  34:"blue",35:"magenta",36:"cyan",37:"white"}
                        current_fg = fg_map.get(p)
                        current_color_number = None
                    elif 40 <= p <= 47:
                        bg_map = {40:"black",41:"red",42:"green",43:"yellow",
                                  44:"blue",45:"magenta",46:"cyan",47:"white"}
                        current_bg = bg_map.get(p)
                        current_bg_color_number = None
                    elif 90 <= p <= 97:
                        fg_map = {90:"bright_black",91:"bright_red",92:"bright_green",
                                  93:"bright_yellow",94:"bright_blue",95:"bright_magenta",
                                  96:"bright_cyan",97:"bright_white"}
                        current_fg = fg_map.get(p)
                        current_color_number = None
                    elif 100 <= p <= 107:
                        bg_map = {100:"bright_black",101:"bright_red",102:"bright_green",
                                  103:"bright_yellow",104:"bright_blue",105:"bright_magenta",
                                  106:"bright_cyan",107:"bright_white"}
                        current_bg = bg_map.get(p)
                        current_bg_color_number = None
                    elif p == 38 and i + 2 < len(params) and params[i+1] == 5:
                        # 38;5;N — 256 色前景（保留原始色号）
                        current_color_number = params[i+2]
                        current_fg = None
                        i += 2
                    elif p == 48 and i + 2 < len(params) and params[i+1] == 5:
                        # 48;5;N — 256 色背景（保留原始色号）
                        current_bg_color_number = params[i+2]
                        current_bg = None
                        i += 2
                    i += 1

            pos = m.end()

        # 剩余文本
        if pos < len(text):
            plain = text[pos:]
            if plain:
                result._spans.append(_make_span(plain))

        return result

    @classmethod
    def gradient(cls, text: str, start_color: str, end_color: str, steps: int | None = None) -> StyledText:
        """创建渐变文本 — 将 text 按字符均分，在 start_color → end_color 的 256 色调色板上均匀采样。

        Args:
            text: 要渲染渐变的文本。
            start_color: 起始颜色名（如 'cyan'、'blue'、'magenta'）。
            end_color: 结束颜色名。
            steps: 渐变步数，默认等于 len(text)。

        Returns:
            每个字符独立 Span + color_number 的 StyledText。
        """
        if not text:
            return cls()

        steps = steps if steps is not None else len(text)
        start_idx = _named_color_to_256(start_color)
        end_idx = _named_color_to_256(end_color)

        result = cls.__new__(cls)
        result._spans = []

        max_t = max(steps - 1, 1)
        for i, char in enumerate(text):
            t = i / max_t
            color_num = _interpolate_256(start_idx, end_idx, t)
            result._spans.append(Span(text=char, color_number=color_num))

        return result

    @staticmethod
    def to_ansi_raw(fg=None, bg=None, bold=False, dim=False, italic=False,
                    underline=False, reverse=False, strikethrough=False,
                    color_number=None, bg_color_number=None):
        """返回纯 ANSI 前缀字符串（不含文本和重置码），用于底部栏颜色常量。

        生成尽可能合并的 SGR 序列（如 \\033[1;38;5;45m），
        与原始硬编码 ANSI 逐字节兼容。

        Args:
            fg: 前景色名（如 'red', '#FF0000'），与 color_number 互斥
            bg: 背景色名，与 bg_color_number 互斥
            bold/dim/italic/underline/reverse/strikethrough: 样式标志
            color_number: 256 色前景色号（0-255），优先于 fg
            bg_color_number: 256 色背景色号（0-255），优先于 bg

        Returns:
            ANSI 前缀字符串（如 "\\033[38;5;39m"），无属性时返回空字符串。
        """
        params: list[int] = []
        if bold:            params.append(1)
        if dim:             params.append(2)
        if italic:          params.append(3)
        if underline:       params.append(4)
        if reverse:         params.append(7)
        if strikethrough:   params.append(9)

        # 前景色
        if color_number is not None:
            params.extend([38, 5, color_number])
        elif fg:
            # 命名颜色与样式参数无法合并，回退到拼接模式
            codes = _params_to_codes(params) if params else []
            codes.append(_fg_code(fg))
            return "".join(codes)

        # 背景色
        if bg_color_number is not None:
            params.extend([48, 5, bg_color_number])
        elif bg:
            codes = _params_to_codes(params) if params else []
            codes.append(_bg_code(bg))
            return "".join(codes)

        if not params:
            return ""
        return f"\033[{';'.join(map(str, params))}m"


def _params_to_codes(params: list[int]) -> list[str]:
    """将 SGR 参数列表转为 ANSI 序列列表（每个样式单独一条）。"""
    return [f"\033[{p}m" for p in params]


def _256_to_hex(idx: int) -> str:
    """将 256 色调色板索引转为 #RRGGBB 字符串。"""
    if idx < 16:
        # 标准 16 色
        hex_map = [
            "000000", "800000", "008000", "808000",
            "000080", "800080", "008080", "c0c0c0",
            "808080", "ff0000", "00ff00", "ffff00",
            "0000ff", "ff00ff", "00ffff", "ffffff",
        ]
        return hex_map[idx]
    elif idx < 232:
        # 216 色立方
        idx -= 16
        r = (idx // 36) * 51
        g = ((idx % 36) // 6) * 51
        b = (idx % 6) * 51
        return f"{r:02x}{g:02x}{b:02x}"
    else:
        # 灰度
        gray = (idx - 232) * 10 + 8
        return f"{gray:02x}{gray:02x}{gray:02x}"


# 命名颜色 → 256 色调色板索引映射
_NAMED_COLOR_MAP: dict[str, int] = {
    "red": 196, "green": 46, "yellow": 226, "blue": 21,
    "magenta": 201, "cyan": 51, "white": 15, "black": 0,
    "bright_red": 196, "bright_green": 46, "bright_yellow": 226,
    "bright_blue": 21, "bright_magenta": 201, "bright_cyan": 51,
    "bright_white": 15, "bright_black": 8,
}


def _named_color_to_256(name: str) -> int:
    """将颜色名映射到 256 色调色板索引。

    支持标准 16 色名称及其 bright 变体。

    Args:
        name: 颜色名（如 'red'、'cyan'、'bright_blue'）。

    Returns:
        256 色调色板索引（0-255）。

    Raises:
        ValueError: 颜色名不被支持时。
    """
    idx = _NAMED_COLOR_MAP.get(name)
    if idx is None:
        raise ValueError(f"不支持的颜色名: {name!r}")
    return idx


def _hex_to_256(hex_str: str) -> int:
    """将 #RRGGBB 字符串映射到最接近的 256 色调色板索引。

    使用欧几里得距离在 RGB 空间中搜索最近色。

    Args:
        hex_str: 6 位十六进制 RGB 字符串（如 "ff0000"）。

    Returns:
        最接近的 256 色调色板索引（0-255）。
    """
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)

    best_idx = 0
    best_dist = float('inf')
    for idx in range(256):
        idx_hex = _256_to_hex(idx)
        idx_r = int(idx_hex[0:2], 16)
        idx_g = int(idx_hex[2:4], 16)
        idx_b = int(idx_hex[4:6], 16)
        dist = (r - idx_r) ** 2 + (g - idx_g) ** 2 + (b - idx_b) ** 2
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _interpolate_256(start_idx: int, end_idx: int, t: float) -> int:
    """在 256 色调色板上执行 RGB 空间线性插值。

    将 256 色索引映射到 RGB，执行线性插值后映射回最接近的 256 色索引。

    Args:
        start_idx: 起始 256 色索引。
        end_idx: 结束 256 色索引。
        t: 插值参数，范围 [0.0, 1.0]。

    Returns:
        插值后的 256 色调色板索引（0-255）。
    """
    start_hex = _256_to_hex(start_idx)
    end_hex = _256_to_hex(end_idx)
    sr = int(start_hex[0:2], 16)
    sg = int(start_hex[2:4], 16)
    sb = int(start_hex[4:6], 16)
    er = int(end_hex[0:2], 16)
    eg = int(end_hex[2:4], 16)
    eb = int(end_hex[4:6], 16)
    r = int(sr + (er - sr) * t)
    g = int(sg + (eg - sg) * t)
    b = int(sb + (eb - sb) * t)
    # 裁剪到 [0, 255]
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return _hex_to_256(f"{r:02x}{g:02x}{b:02x}")
