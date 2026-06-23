"""布局组件 — React Ink-like Box/Static/Text 组件。

提供声明式组件树布局能力：
- FlexDirection: 弹性方向枚举（COLUMN/ROW）
- Box: 布局容器，支持 flex_direction/padding/margin/border_style/width/height
- Static: 不可变区域组件，首次渲染后缓存结果
- Text: 叶子文本组件，支持 Rich Style
"""

from __future__ import annotations

import logging
from enum import StrEnum

from rich.style import Style
from rich.text import Text as RichText

from ._components import TuiComponent
from ._measure import _display_width, _truncate_by_width

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# FlexDirection 枚举
# ═══════════════════════════════════════════════════════════

class FlexDirection(StrEnum):
    """弹性布局方向。

    COLUMN: 垂直排列，子组件间换行
    ROW: 水平排列，子组件间空格分隔
    """
    COLUMN = "column"
    ROW = "row"


# ═══════════════════════════════════════════════════════════
# Box 布局容器
# ═══════════════════════════════════════════════════════════

class Box(TuiComponent):
    """React Ink-like Box 布局容器。

    将子组件按指定方向排列，支持内边距、外边距、边框和固定尺寸。

    Attributes:
        flex_direction: 排列方向，COLUMN（默认）或 ROW
        padding: 内边距 — int 或 (top, right, bottom, left) 元组
        margin: 外边距 — int 或 (top, right, bottom, left) 元组
        border_style: Rich Style 边框样式，None 表示无边框
        width: 固定宽度（字符数），None=自适应
        height: 固定高度（行数），None=自适应
    """

    def __init__(
        self,
        children: list[TuiComponent] | None = None,
        flex_direction: FlexDirection = FlexDirection.COLUMN,
        padding: int | tuple[int, int, int, int] = 0,
        margin: int | tuple[int, int, int, int] = 0,
        border_style: Style | None = None,
        width: int | None = None,
        height: int | None = None,
    ):
        """初始化 Box 布局容器。

        Args:
            children: 子组件列表
            flex_direction: FlexDirection.COLUMN 或 FlexDirection.ROW
            padding: 内边距（CSS 顺序：上 右 下 左）
            margin: 外边距（CSS 顺序：上 右 下 左）
            border_style: 边框的 Rich Style，None 为无边框
            width: 固定宽度（None=自适应）
            height: 固定高度（None=自适应）
        """
        super().__init__(children=children)
        self.flex_direction = flex_direction
        self.padding = padding
        self.margin = margin
        self.border_style = border_style
        self.width = width
        self.height = height

    @staticmethod
    def _normalize_spacing(value: int | tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """将 int 或 4 元组统一规范化为 (top, right, bottom, left) 元组。

        Args:
            value: int → (value, value, value, value); 4 元组 → 原样返回

        Returns:
            (top, right, bottom, left) — CSS 顺序
        """
        if isinstance(value, int):
            return (value, value, value, value)
        if isinstance(value, tuple) and len(value) == 4:
            return value
        raise ValueError(f"间距值须为 int 或 (top,right,bottom,left) 4 元组, 收到: {value!r}")

    def render(self) -> str | RichText:
        """按 flex_direction 排列子组件，应用 padding/margin/border/尺寸约束。

        保留子组件的 RichText 样式：不再对 render() 返回值调用 str()，
        使用 RichText.assemble() 拼接；边框样式仅应用于边框字符。
        """
        ch = self._ensure_children()

        # ── 1. 渲染子组件（保留 RichText 样式） ──
        rendered: list[str | RichText] = []
        for child in ch:
            result = child.render()
            if result is not None:
                rendered.append(result)

        if not rendered:
            return ""

        # 判断是否所有子组件输出均为纯文本（无样式、无边框时最终返回 str）
        has_styled = (
            any(isinstance(r, RichText) for r in rendered)
            or self.border_style is not None
        )

        # ── 2. 按方向组装子组件 ──
        if self.flex_direction == FlexDirection.ROW:
            parts: list[str | RichText] = []
            for i, r in enumerate(rendered):
                if i > 0:
                    parts.append(" ")
                parts.append(r)
            content = RichText.assemble(*parts)
        else:
            # COLUMN: 子组件间空行分隔
            parts: list[str | RichText] = []
            for i, r in enumerate(rendered):
                if i > 0:
                    parts.append("\n\n")
                parts.append(r)
            content = RichText.assemble(*parts)

        # ── 3. 应用 padding（内边距） ──
        pad_t, pad_r, pad_b, pad_l = self._normalize_spacing(self.padding)
        if pad_t or pad_r or pad_b or pad_l:
            content_lines = content.split("\n") if content.plain else [RichText("")]
            pad_parts: list[str | RichText] = []
            for _ in range(pad_t):
                pad_parts.append("\n")
            for i, line_rt in enumerate(content_lines):
                if i > 0:
                    pad_parts.append("\n")
                if pad_l:
                    pad_parts.append(" " * pad_l)
                pad_parts.append(line_rt)
                if pad_r:
                    pad_parts.append(" " * pad_r)
            for _ in range(pad_b):
                pad_parts.append("\n")
            content = RichText.assemble(*pad_parts) if pad_parts else RichText("")

        # ── 4. 应用固定宽高约束 ──
        if self.width is not None or self.height is not None:
            content_lines = content.split("\n") if content.plain else [RichText("")]

            # 宽度约束
            if self.width is not None:
                w_parts: list[str | RichText] = []
                for i, line_rt in enumerate(content_lines):
                    if i > 0:
                        w_parts.append("\n")
                    line_plain = line_rt.plain
                    dw = _display_width(line_plain)
                    if dw > self.width:
                        truncated = _truncate_by_width(line_plain, self.width)
                        w_parts.append(truncated)
                    else:
                        w_parts.append(line_rt)
                        if dw < self.width:
                            w_parts.append(" " * (self.width - dw))
                content = RichText.assemble(*w_parts) if w_parts else RichText("")
                content_lines = content.split("\n") if content.plain else [RichText("")]

            # 高度约束
            if self.height is not None:
                if len(content_lines) > self.height:
                    content_lines = content_lines[: self.height]
                elif len(content_lines) < self.height:
                    fill_width = self.width if self.width is not None else 0
                    content_lines = list(content_lines)
                    content_lines.extend(
                        [RichText(" " * fill_width if fill_width else "")]
                        for _ in range(self.height - len(content_lines))
                    )
                h_parts: list[str | RichText] = []
                for i, line_rt in enumerate(content_lines):
                    if i > 0:
                        h_parts.append("\n")
                    h_parts.append(line_rt)
                content = RichText.assemble(*h_parts) if h_parts else RichText("")

        # ── 5. 应用边框（样式仅应用于边框字符） ──
        if self.border_style is not None:
            content_lines = content.split("\n") if content.plain else [RichText("")]
            max_width = max((_display_width(line.plain) for line in content_lines), default=0)

            border_parts: list[str | RichText | tuple[str, Style]] = []
            # 上边框（带样式）
            border_parts.append(("┌" + "─" * max_width + "┐", self.border_style))
            border_parts.append("\n")
            # 内容行 + 侧边框（边框带样式，内容保留原样式）
            for i, line_rt in enumerate(content_lines):
                if i > 0:
                    border_parts.append("\n")
                border_parts.append(("│", self.border_style))
                border_parts.append(line_rt)
                line_width = _display_width(line_rt.plain)
                if line_width < max_width:
                    border_parts.append(" " * (max_width - line_width))
                border_parts.append(("│", self.border_style))
            border_parts.append("\n")
            # 下边框（带样式）
            border_parts.append(("└" + "─" * max_width + "┘", self.border_style))

            content = RichText.assemble(*border_parts)

        # ── 6. 应用 margin（外边距） ──
        mar_t, mar_r, mar_b, mar_l = self._normalize_spacing(self.margin)
        if mar_t or mar_r or mar_b or mar_l:
            content_lines = content.split("\n") if content.plain else [RichText("")]
            # 收集含上下 margin 空行的全部逻辑行
            all_lines: list[RichText] = []
            for _ in range(mar_t):
                all_lines.append(RichText(""))
            all_lines.extend(content_lines)
            for _ in range(mar_b):
                all_lines.append(RichText(""))
            # 左右 margin 应用于所有行（含空行）
            margin_parts: list[str | RichText] = []
            for i, line_rt in enumerate(all_lines):
                if i > 0:
                    margin_parts.append("\n")
                if mar_l:
                    margin_parts.append(" " * mar_l)
                margin_parts.append(line_rt)
                if mar_r:
                    margin_parts.append(" " * mar_r)
            content = RichText.assemble(*margin_parts) if margin_parts else RichText("")

        # ── 7. 返回：无样式时返回纯文本 str，有样式时返回 RichText ──
        if not has_styled:
            return content.plain
        return content


# ═══════════════════════════════════════════════════════════
# Static 不可变区域组件
# ═══════════════════════════════════════════════════════════

class Static(TuiComponent):
    """React Ink-like Static 组件 — 内容一次性渲染后缓存，后续不变。

    首次 render() 时调用子组件渲染并缓存结果；后续 render() 直接返回缓存。
    适用于内容不变的固定区域（如工具栏、标题栏）。

    调用 invalidate_cache() 可强制下次 render() 重新渲染。
    """

    def __init__(self, children: list[TuiComponent] | None = None):
        """初始化 Static 组件。

        Args:
            children: 子组件列表
        """
        super().__init__(children=children)
        self._cached: str | RichText | None = None

    def render(self) -> str | RichText:
        """返回子组件渲染结果。

        首次调用时渲染并缓存，后续调用直接返回缓存。
        """
        if self._cached is not None:
            return self._cached

        _logger.debug("Static 首次渲染, 缓存结果")
        self._cached = self.render_children()
        return self._cached

    def invalidate_cache(self) -> None:
        """清空缓存，下次 render() 将重新渲染子组件。"""
        _logger.debug("Static 缓存已失效")
        self._cached = None


# ═══════════════════════════════════════════════════════════
# Text 叶子组件
# ═══════════════════════════════════════════════════════════

class Text(TuiComponent):
    """React Ink-like Text 叶子组件。

    渲染纯文本或带 Rich Style 的文本。作为组件树的叶子节点存在，
    通常不包含子组件。

    Attributes:
        content: 文本内容
        style: Rich Style 样式，None 表示纯文本
    """

    def __init__(self, content: str, style: Style | None = None):
        """初始化 Text 叶子组件。

        Args:
            content: 文本内容
            style: Rich Style 样式（可选）
        """
        super().__init__(children=None)
        self.content = content
        self.style = style

    def render(self) -> str | RichText:
        """渲染文本。

        Returns:
            style 为 None 时返回纯 str content，否则返回 Rich Text(content, style=style)
        """
        if self.style is None:
            return self.content
        return RichText(self.content, style=self.style)


