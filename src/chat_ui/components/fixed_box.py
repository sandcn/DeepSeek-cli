"""FixedSizeBox 组件 — React Ink 风格固定尺寸容器。

固定 width/height 的容器，内容超出时截断，不足时填充空白。
支持边框、标题、内边距。

使用示例:
    box = FixedSizeBox(width=20, height=5, border_style="single", title="日志")
    box.add_child(Text("内容"))
    print(box.render())
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .base import TuiComponent
from .box import _BORDER_STYLES, _visual_width, _strip_ansi, _styled, _make_ansi_prefix

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


_ANSI_SGR_RE = re.compile(r'\033\[[\d;]*m')


def _ensure_border_chars(border_style: str | dict | None) -> dict[str, str] | None:
    """解析边框字符集，返回 None 表示无边框。"""
    if border_style is None:
        return None
    if isinstance(border_style, dict):
        base = _BORDER_STYLES["single"].copy()
        for k in ("tl", "tr", "bl", "br", "h", "v"):
            if k in border_style:
                base[k] = border_style[k]
        return base
    return dict(_BORDER_STYLES.get(str(border_style), _BORDER_STYLES["single"]))


class FixedSizeBox(TuiComponent):
    """React Ink FixedSizeBox 组件 — 固定尺寸容器。

    内容区宽度 = width，高度 = height。
    边框额外占据左右各 1 列、上下各 1 行。

    Props:
        width: int — 内容区固定宽度（必填，>=1）
        height: int — 内容区固定高度（必填，>=1）
        border_style: str | None — 边框样式预设名或自定义 dict，None 为无边框
        border_color: str | None — 边框前景色
        border_dim: bool — 边框暗色模式（默认 False）
        title: str | None — 边框标题
        title_color: str | None — 标题颜色
        padding_x: int — 水平内边距（默认 0）
        padding_y: int — 垂直内边距（默认 0）
        truncate_indicator: str — 截断指示符（默认 "…"）
        children: list[TuiComponent] | None
    """

    def __init__(
        self,
        width: int = 20,
        height: int = 5,
        border_style: str | None = "single",
        border_color: str | None = None,
        border_dim: bool = False,
        title: str | None = None,
        title_color: str | None = None,
        padding_x: int = 0,
        padding_y: int = 0,
        truncate_indicator: str = "\u2026",
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._width = max(1, width)
        self._height = max(1, height)
        self._border_style = border_style
        self._border_color = border_color
        self._border_dim = border_dim
        self._title = title
        self._title_color = title_color
        self._padding_x = max(0, padding_x)
        self._padding_y = max(0, padding_y)
        self._truncate_indicator = truncate_indicator

    @property
    def key(self) -> str:
        return "fixed_box"

    def update(self, props: dict) -> bool:
        changed = False
        if "width" in props:
            new_w = max(1, props["width"])
            if new_w != self._width:
                self._width = new_w
                changed = True
        if "height" in props:
            new_h = max(1, props["height"])
            if new_h != self._height:
                self._height = new_h
                changed = True
        if "border_style" in props and props["border_style"] != self._border_style:
            self._border_style = props["border_style"]
            changed = True
        if "border_color" in props and props["border_color"] != self._border_color:
            self._border_color = props["border_color"]
            changed = True
        if "border_dim" in props and props["border_dim"] != self._border_dim:
            self._border_dim = props["border_dim"]
            changed = True
        if "title" in props and props["title"] != self._title:
            self._title = props["title"]
            changed = True
        if "title_color" in props and props["title_color"] != self._title_color:
            self._title_color = props["title_color"]
            changed = True
        if "padding_x" in props:
            new_px = max(0, props["padding_x"])
            if new_px != self._padding_x:
                self._padding_x = new_px
                changed = True
        if "padding_y" in props:
            new_py = max(0, props["padding_y"])
            if new_py != self._padding_y:
                self._padding_y = new_py
                changed = True
        if "truncate_indicator" in props and props["truncate_indicator"] != self._truncate_indicator:
            self._truncate_indicator = props["truncate_indicator"]
            changed = True
        return changed

    def _truncate_line(self, line: str, max_width: int) -> str:
        """截断单行到指定视觉宽度，超长时追加 truncate_indicator。"""
        clean = _strip_ansi(line)
        vw = _visual_width(clean)
        if vw <= max_width:
            return line  # 不超宽，原样返回（含 ANSI）

        # 需要截断：逐字符截取到 max_width - indicator_width
        ind_w = _visual_width(self._truncate_indicator)
        avail = max_width - ind_w
        if avail <= 0:
            return self._truncate_indicator

        chars = []
        cur_w = 0
        for ch in clean:
            cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
            if cur_w + cw > avail:
                break
            chars.append(ch)
            cur_w += cw
        return "".join(chars) + self._truncate_indicator

    def _render_content_lines(self) -> list[str]:
        """渲染子组件内容，返回纯文本行列表。"""
        raw = self.render_children()
        content = str(raw) if raw else ""
        return content.split("\n") if content else [""]

    def _build_content_block(self, content_lines: list[str]) -> list[str]:
        """构建固定尺寸的内容块（含 padding，不含边框）。"""
        inner_w = self._width
        inner_h = self._height

        # 1. 每行宽度截断
        truncated = [self._truncate_line(line, inner_w) for line in content_lines]

        # 2. 水平 padding
        if self._padding_x > 0:
            pad_left = " " * self._padding_x
            truncated = [
                f"{pad_left}{line}" if i < len(truncated) else ""
                for i, line in enumerate(truncated)
            ]
            # 重新做宽度截断（padding 后可能超宽）
            truncated = [self._truncate_line(line, inner_w) for line in truncated]

        # 3. 垂直 padding（在内容行前后加空白行）
        blank_line = " " * inner_w
        top_pad = [blank_line] * self._padding_y if self._padding_y > 0 else []
        bottom_pad = [blank_line] * self._padding_y if self._padding_y > 0 else []
        padded = top_pad + truncated + bottom_pad

        # 4. 高度截断
        if len(padded) > inner_h:
            keep = max(inner_h - 1, 0)
            padded = padded[:keep]
            truncated_indicator = "... (truncated)"
            if _visual_width(truncated_indicator) > inner_w:
                truncated_indicator = "..."
            padded.append(truncated_indicator)

        # 5. 空白填充（确保每行宽度为 inner_w）
        while len(padded) < inner_h:
            padded.append(blank_line)

        # 6. 确保每行宽度正好为 inner_w（截断 + 右填充空格）
        result: list[str] = []
        for line in padded:
            line = self._truncate_line(line, inner_w)
            line_vw = _visual_width(_strip_ansi(line))
            if line_vw < inner_w:
                line += " " * (inner_w - line_vw)
            result.append(line)
        padded = result

        return padded

    def _build_border_top(self, chars: dict[str, str], inner_w: int) -> str:
        """构建上边框行。"""
        fg = self._border_color
        dim = self._border_dim
        if self._title:
            title_str = f" {self._title} "
            title_w = _visual_width(title_str)
            if title_w < inner_w:
                left_h_cnt = (inner_w - title_w) // 2
                right_h_cnt = inner_w - title_w - left_h_cnt
                left_h = chars["h"] * left_h_cnt
                right_h = chars["h"] * right_h_cnt
                if self._title_color:
                    return (
                        _styled(chars["tl"], fg=fg, dim=dim)
                        + _styled(left_h, fg=fg, dim=dim)
                        + _styled(title_str, fg=self._title_color)
                        + _styled(right_h, fg=fg, dim=dim)
                        + _styled(chars["tr"], fg=fg, dim=dim)
                    )
                else:
                    return (
                        _styled(chars["tl"], fg=fg, dim=dim)
                        + _styled(left_h + title_str + right_h, fg=fg, dim=dim)
                        + _styled(chars["tr"], fg=fg, dim=dim)
                    )
        # 无标题
        return (
            _styled(chars["tl"], fg=fg, dim=dim)
            + _styled(chars["h"] * inner_w, fg=fg, dim=dim)
            + _styled(chars["tr"], fg=fg, dim=dim)
        )

    def _build_border_bottom(self, chars: dict[str, str], inner_w: int) -> str:
        """构建下边框行。"""
        fg = self._border_color
        dim = self._border_dim
        return (
            _styled(chars["bl"], fg=fg, dim=dim)
            + _styled(chars["h"] * inner_w, fg=fg, dim=dim)
            + _styled(chars["br"], fg=fg, dim=dim)
        )

    def _build_border_side(self, chars: dict[str, str], inner_w: int, content: str = "") -> str:
        """构建侧边框行。"""
        fg = self._border_color
        dim = self._border_dim
        # 对齐内容到 inner_w
        clean = _strip_ansi(content)
        vw = _visual_width(clean)
        if vw < inner_w:
            content = content + " " * (inner_w - vw)
        return (
            _styled(chars["v"], fg=fg, dim=dim)
            + content
            + _styled(chars["v"], fg=fg, dim=dim)
        )

    def render(self) -> str:
        chars = _ensure_border_chars(self._border_style)
        inner_w = self._width
        inner_h = self._height

        # 获取内容块
        content_lines = self._render_content_lines()
        block = self._build_content_block(content_lines)

        if chars is None:
            # 无边框模式：直接输出内容块
            return "\n".join(block)

        # 有边框模式
        lines: list[str] = []

        # 上边框
        lines.append(self._build_border_top(chars, inner_w))

        # 内容行 + 侧边框
        for line in block:
            lines.append(self._build_border_side(chars, inner_w, line))

        # 下边框
        lines.append(self._build_border_bottom(chars, inner_w))

        return "\n".join(lines)

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="fixed_box",
            key=self.key,
            props={
                "text": rendered,
                "width": self._width,
                "height": self._height,
            },
        )
