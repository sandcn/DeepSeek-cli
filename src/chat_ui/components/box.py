"""Box 边框组件 — 声明式终端边框渲染。

提供 <Box> 组件，支持：
  - 8 种内置边框样式（single / double / round / bold / singleDouble / doubleSingle / classic / dashed）
  - 自定义边框样式（BoxBorderStyle dict，含 tl/tr/bl/br/h/v 键）
  - 每条边独立颜色控制（borderTopColor / borderRightColor / ...）
  - 每条边独立背景色控制（borderTopBackgroundColor / ...）
  - borderDimColor 暗色边框
  - backgroundColor 内容区背景填充
  - 每条边可见性控制（showTop / showBottom / showLeft / showRight）
  - padding / margin / width / height 尺寸控制

继承自 TuiComponent，复用现有 StyledText ANSI 渲染能力。
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from typing import Any

from ..components.base import TuiComponent
from ..infrastructure.ansi import _fg_code, _bg_code, ANSI_DIM, ANSI_BOLD, ANSI_RESET


# ── 边框样式常量 ────────────────────────────────────────

# 8 种预设边框样式的字符映射。
# 键名: tl=top-left, tr=top-right, bl=bottom-left, br=bottom-right,
#       h=horizontal, v=vertical
_BORDER_STYLES: dict[str, dict[str, str]] = {
    "single": {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "─", "v": "│",
    },
    "double": {
        "tl": "╔", "tr": "╗", "bl": "╚", "br": "╝",
        "h": "═", "v": "║",
    },
    "round": {
        "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
        "h": "─", "v": "│",
    },
    "bold": {
        "tl": "┏", "tr": "┓", "bl": "┗", "br": "┛",
        "h": "━", "v": "┃",
    },
    "singleDouble": {
        "tl": "┌", "tr": "╖", "bl": "└", "br": "╜",
        "h": "─", "v": "║",
    },
    "doubleSingle": {
        "tl": "╒", "tr": "╕", "bl": "╘", "br": "╛",
        "h": "═", "v": "│",
    },
    "classic": {
        "tl": "+", "tr": "+", "bl": "+", "br": "+",
        "h": "-", "v": "|",
    },
    "dashed": {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "╌", "v": "╎",
    },
}

# 边框样式类型别名（供外部引用）
BoxBorderStyle = dict[str, str]

# 公开的边框样式常量（8 种预设 + 可扩展）
BORDER_STYLES: dict[str, dict[str, str]] = dict(_BORDER_STYLES)


# ── ANSI 与宽度辅助 ─────────────────────────────────────

_ANSI_SGR_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除文本中所有 ANSI SGR 转义序列，返回纯文本。"""
    return _ANSI_SGR_RE.sub('', text)


def _visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（剥离 ANSI 序列，CJK 字符计为 2 列）。"""
    clean = _strip_ansi(text)
    w = 0
    for ch in clean:
        w += 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
    return w


def _make_ansi_prefix(*, fg: str | None = None, bg: str | None = None,
                      dim: bool = False, bold: bool = False) -> str:
    """构造 ANSI SGR 前缀字符串（不含文本、不含重置码）。

    Args:
        fg: 前景色名（如 'red'、'blue'），传给 _fg_code。
        bg: 背景色名，传给 _bg_code。
        dim: 是否暗色。
        bold: 是否加粗。

    Returns:
        ANSI 前缀字符串，无样式时返回空字符串。
    """
    codes: list[str] = []
    if dim:
        codes.append(ANSI_DIM)
    if bold:
        codes.append(ANSI_BOLD)
    if fg:
        codes.append(_fg_code(fg))
    if bg:
        codes.append(_bg_code(bg))
    return "".join(codes)


def _styled(text: str, *, fg: str | None = None, bg: str | None = None,
            dim: bool = False, bold: bool = False) -> str:
    """用 ANSI 转义序列包裹文本，末尾自动追加重置码。

    当 fg/bg/dim/bold 全为 None/False 时，直接返回原文本（无 ANSI 开销）。
    """
    prefix = _make_ansi_prefix(fg=fg, bg=bg, dim=dim, bold=bold)
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI_RESET}"


# ── Box 组件 ────────────────────────────────────────────

class Box(TuiComponent):
    """Box 边框组件 — 在终端中渲染带边框的内容容器。

    继承自 TuiComponent，子组件通过 children 参数传入或链式 add_child() 添加。
    render() 输出带 ANSI 转义序列的多行字符串。

    属性（构造参数）:
        border_style: 边框样式 — 预设名称（'single'/'double'/'round'/'bold'/
                     'singleDouble'/'doubleSingle'/'classic'/'dashed'）
                     或自定义 BoxBorderStyle dict（含 tl/tr/bl/br/h/v 键）。
        border_color: 统一边框前景色名（如 'red'/'blue'）。
        border_top_color / border_right_color / border_bottom_color / border_left_color:
            各边独立前景色，覆盖 border_color。
        border_dim_color: bool，边框暗色模式。
        border_background_color: 统一边框背景色名。
        border_top_bg_color / border_right_bg_color / border_bottom_bg_color / border_left_bg_color:
            各边独立背景色，覆盖 border_background_color。
        background_color: 内容区背景色名（填充 padding + 内容区域）。
        show_top / show_bottom / show_left / show_right: bool，各边可见性。
        padding_x / padding_y: int，内边距（水平/垂直）。
        margin_x / margin_y: int，外边距（水平/垂直）。
        width: int | str | None — 内容区宽度。
               int: 固定列数（含 padding）。
               str: 百分比（如 '50%'），相对于终端宽度。
               None: 自适应子组件内容宽度。
        height: int | None — 内容区最小高度（行数）。None 表示自适应。
    """

    # ── 初始化 ──────────────────────────────────────────

    def __init__(self, **props: Any) -> None:
        """初始化 Box 组件。

        所有属性通过 kwargs 传入（React props 风格）。
        children 可从 props 中提取或通过 add_child() 链式添加。
        """
        children = props.pop('children', None)
        if children is not None:
            if isinstance(children, TuiComponent):
                children = [children]
            else:
                children = list(children)
        super().__init__(children=children)

        # 边框样式
        self.border_style: str | BoxBorderStyle = props.get('border_style', 'single')

        # 统一边框颜色
        self.border_color: str | None = props.get('border_color', None)

        # 各边独立颜色
        self.border_top_color: str | None = props.get('border_top_color', None)
        self.border_right_color: str | None = props.get('border_right_color', None)
        self.border_bottom_color: str | None = props.get('border_bottom_color', None)
        self.border_left_color: str | None = props.get('border_left_color', None)

        # 暗色边框
        self.border_dim_color: bool = props.get('border_dim_color', False)

        # 统一边框背景色
        self.border_background_color: str | None = props.get(
            'border_background_color', None)

        # 各边独立背景色
        self.border_top_bg_color: str | None = props.get(
            'border_top_bg_color', None)
        self.border_right_bg_color: str | None = props.get(
            'border_right_bg_color', None)
        self.border_bottom_bg_color: str | None = props.get(
            'border_bottom_bg_color', None)
        self.border_left_bg_color: str | None = props.get(
            'border_left_bg_color', None)

        # 内容区背景色
        self.background_color: str | None = props.get('background_color', None)

        # 各边可见性
        self.show_top: bool = props.get('show_top', True)
        self.show_bottom: bool = props.get('show_bottom', True)
        self.show_left: bool = props.get('show_left', True)
        self.show_right: bool = props.get('show_right', True)

        # 尺寸
        self.padding_x: int = props.get('padding_x', 0)
        self.padding_y: int = props.get('padding_y', 0)
        self.margin_x: int = props.get('margin_x', 0)
        self.margin_y: int = props.get('margin_y', 0)
        self.width: int | str | None = props.get('width', None)
        self.height: int | str | None = props.get('height', None)

    # ── 边框字符解析 ────────────────────────────────────

    def _resolve_border_chars(self) -> dict[str, str]:
        """解析边框字符集。

        支持两种格式：
        - 字符串：从 _BORDER_STYLES 预设中查找，找不到回退 'single'。
        - dict：自定义样式，需包含 tl/tr/bl/br/h/v 键（缺省回退 'single' 对应字符）。

        Returns:
            字符映射 dict，包含 tl, tr, bl, br, h, v 六个键。
        """
        if isinstance(self.border_style, dict):
            # 自定义样式 — 以 single 为 base，逐个覆盖
            base = _BORDER_STYLES["single"].copy()
            mapping = {
                "topLeft": "tl", "top": "h", "topRight": "tr",
                "right": "v", "bottomRight": "br",
                "bottom": "h", "bottomLeft": "bl", "left": "v",
            }
            for user_key, char_key in mapping.items():
                if user_key in self.border_style:
                    base[char_key] = self.border_style[user_key]
            # 也支持直接用内部键名
            for k in ("tl", "tr", "bl", "br", "h", "v"):
                if k in self.border_style:
                    base[k] = self.border_style[k]
            return base
        else:
            return _BORDER_STYLES.get(
                str(self.border_style), _BORDER_STYLES["single"])

    # ── 颜色解析辅助 ────────────────────────────────────

    def _border_fg(self, side: str) -> str | None:
        """获取指定边的最终前景色。

        优先级：独立颜色 > 统一颜色 > None。
        """
        attr_map = {
            "top": self.border_top_color,
            "right": self.border_right_color,
            "bottom": self.border_bottom_color,
            "left": self.border_left_color,
        }
        return attr_map.get(side, None) or self.border_color

    def _border_bg(self, side: str) -> str | None:
        """获取指定边的最终背景色。

        优先级：独立背景色 > 统一背景色 > None。
        """
        attr_map = {
            "top": self.border_top_bg_color,
            "right": self.border_right_bg_color,
            "bottom": self.border_bottom_bg_color,
            "left": self.border_left_bg_color,
        }
        return attr_map.get(side, None) or self.border_background_color

    # ── 渲染 ────────────────────────────────────────────

    def render(self) -> str:
        """渲染 Box 边框及子内容。

        流程:
            1. 渲染子组件获取内容文本
            2. 计算内容尺寸
            3. 应用 width/height 约束计算内部宽高
            4. 解析边框字符集
            5. 逐行构建输出（margin → top 边 → padding → content → padding → bottom 边 → margin）
            6. 拼接为 ANSI 字符串返回
        """
        # ── 1. 渲染子组件 ────────────────────────────────
        raw_content = self.render_children()
        content_str: str
        if isinstance(raw_content, str):
            content_str = raw_content
        else:
            content_str = str(raw_content)

        content_lines = content_str.split('\n') if content_str else [""]

        # ── 2. 计算内容尺寸 ──────────────────────────────
        content_width = max(
            (_visual_width(line) for line in content_lines), default=0)
        content_height = len(content_lines)

        # ── 3. 计算内部宽度 ──────────────────────────────
        inner_width = content_width + 2 * self.padding_x

        # 处理 width 属性
        if self.width is not None:
            if isinstance(self.width, int) and self.width > 0:
                # 减去边框占用（左右各 1 列），保留 padding 在内
                target_inner = max(self.width, 0)
                inner_width = max(inner_width, target_inner)
            elif isinstance(self.width, str):
                # 百分比
                pct_str = self.width.rstrip('%')
                try:
                    pct = float(pct_str) / 100.0
                    term_w = shutil.get_terminal_size().columns
                    target_inner = max(int(term_w * pct), 0)
                    inner_width = max(inner_width, target_inner)
                except (ValueError, OSError):
                    pass

        # 处理 height 属性
        if self.height is not None:
            if isinstance(self.height, int) and self.height > 0:
                content_height = max(content_height, self.height)
            elif isinstance(self.height, str):
                pct_str = self.height.rstrip('%')
                try:
                    pct = float(pct_str) / 100.0
                    term_h = shutil.get_terminal_size().lines
                    target_h = max(int(term_h * pct), 0)
                    content_height = max(content_height, target_h)
                except (ValueError, OSError):
                    pass

        # ── 4. 解析边框字符 ──────────────────────────────
        chars = self._resolve_border_chars()

        # ── 5. 逐行构建输出 ──────────────────────────────
        lines: list[str] = []

        # margin top
        for _ in range(self.margin_y):
            lines.append("")

        # top 边框行
        if self.show_top:
            top_fg = self._border_fg("top")
            top_bg = self._border_bg("top")
            if inner_width > 0:
                top_line = (
                    _styled(chars["tl"], fg=top_fg, bg=top_bg,
                            dim=self.border_dim_color)
                    + _styled(chars["h"] * inner_width, fg=top_fg, bg=top_bg,
                              dim=self.border_dim_color)
                    + _styled(chars["tr"], fg=top_fg, bg=top_bg,
                              dim=self.border_dim_color)
                )
            else:
                # 极小宽度：仅渲染两个角
                top_line = (
                    _styled(chars["tl"], fg=top_fg, bg=top_bg,
                            dim=self.border_dim_color)
                    + _styled(chars["tr"], fg=top_fg, bg=top_bg,
                              dim=self.border_dim_color)
                )
            lines.append(top_line)

        # padding 顶行（含左右边框）
        for _ in range(self.padding_y):
            lines.append(self._build_side_line(inner_width, chars))

        # 内容行（含左右边框）
        # 补齐 content_lines 到 content_height
        padded_lines = list(content_lines)
        while len(padded_lines) < content_height:
            padded_lines.append("")

        for line in padded_lines:
            lines.append(self._build_content_line(
                line, inner_width, chars))

        # padding 底行（含左右边框）
        for _ in range(self.padding_y):
            lines.append(self._build_side_line(inner_width, chars))

        # bottom 边框行
        if self.show_bottom:
            bottom_fg = self._border_fg("bottom")
            bottom_bg = self._border_bg("bottom")
            if inner_width > 0:
                bottom_line = (
                    _styled(chars["bl"], fg=bottom_fg, bg=bottom_bg,
                            dim=self.border_dim_color)
                    + _styled(chars["h"] * inner_width, fg=bottom_fg,
                              bg=bottom_bg, dim=self.border_dim_color)
                    + _styled(chars["br"], fg=bottom_fg, bg=bottom_bg,
                              dim=self.border_dim_color)
                )
            else:
                bottom_line = (
                    _styled(chars["bl"], fg=bottom_fg, bg=bottom_bg,
                            dim=self.border_dim_color)
                    + _styled(chars["br"], fg=bottom_fg, bg=bottom_bg,
                              dim=self.border_dim_color)
                )
            lines.append(bottom_line)

        # margin bottom
        for _ in range(self.margin_y):
            lines.append("")

        return "\n".join(lines)

    # ── 行构建辅助 ──────────────────────────────────────

    def _build_side_line(self, inner_width: int,
                         chars: dict[str, str]) -> str:
        """构建仅含左右边框 + 空白的行（padding 行）。

        Args:
            inner_width: 内部宽度（不含左右边框）。
            chars: 边框字符映射。

        Returns:
            带 ANSI 样式的行字符串。
        """
        left_fg = self._border_fg("left")
        left_bg = self._border_bg("left")
        right_fg = self._border_fg("right")
        right_bg = self._border_bg("right")

        parts: list[str] = []

        # 左边框
        if self.show_left:
            parts.append(_styled(
                chars["v"], fg=left_fg, bg=left_bg,
                dim=self.border_dim_color))

        # 空白填充区（可选背景色）
        if self.background_color:
            parts.append(_styled(
                " " * inner_width, bg=self.background_color))
        else:
            parts.append(" " * inner_width)

        # 右边框
        if self.show_right:
            parts.append(_styled(
                chars["v"], fg=right_fg, bg=right_bg,
                dim=self.border_dim_color))

        return "".join(parts)

    def _build_content_line(self, content: str, inner_width: int,
                            chars: dict[str, str]) -> str:
        """构建含左右边框 + 内容文本的行。

        Args:
            content: 原始内容文本（可能含 ANSI 序列）。
            inner_width: 内部宽度（不含左右边框）。
            chars: 边框字符映射。

        Returns:
            带 ANSI 样式的行字符串。
        """
        left_fg = self._border_fg("left")
        left_bg = self._border_bg("left")
        right_fg = self._border_fg("right")
        right_bg = self._border_bg("right")

        parts: list[str] = []

        # 左边框
        if self.show_left:
            parts.append(_styled(
                chars["v"], fg=left_fg, bg=left_bg,
                dim=self.border_dim_color))

        # 内容文本（已含原始 ANSI 样式）
        content_visual_w = _visual_width(content)

        # 背景色前缀（包裹整个内容区域：左 padding + 内容 + 右 padding）
        if self.background_color:
            parts.append(_make_ansi_prefix(bg=self.background_color))

        # 左 padding
        if self.padding_x > 0:
            parts.append(" " * self.padding_x)

        # 内容（content 可能含自己的 ANSI 序列，包括 \033[0m 会清除背景色；
        # 此为预期行为 — 内容自身的样式优先）
        parts.append(content)

        # 右 padding
        right_pad_w = inner_width - self.padding_x - content_visual_w
        if right_pad_w > 0:
            parts.append(" " * right_pad_w)

        # 关闭背景色（防止泄漏到右边框）
        if self.background_color:
            parts.append(ANSI_RESET)

        # 右边框
        if self.show_right:
            parts.append(_styled(
                chars["v"], fg=right_fg, bg=right_bg,
                dim=self.border_dim_color))

        return "".join(parts)
