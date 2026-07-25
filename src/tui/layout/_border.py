from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Border — 边框布局
# ═══════════════════════════════════════════════════════════


class Border(Widget):
    """边框控件。

    在子控件周围绘制边框。
    复用 ``components/_box.py`` 的 ``BoxStyle`` 枚举和 ``_BOX_CHARS`` 字符集。
    窄屏时自动降级为无边框。

    Args:
        child: 子控件。
        style: 边框样式（BoxStyle 枚举值或字符串），默认 "rounded"。
        title: 标题文本（可选），默认 ""。
        title_align: 标题对齐方式，"left" / "center" / "right"，默认 "left"。
        border_color: 边框色号（256 色），默认 None（使用默认色）。
    """

    def __init__(
        self,
        child: Widget,
        style: str = "rounded",
        title: str = "",
        title_align: str = "left",
        border_color: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(props={
            "style": style,
            "title": title,
            "title_align": title_align,
            "border_color": border_color,
        }, key=key)
        self._children_source: list[Widget] = [child]
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """在子控件周围绘制边框。

        边框占用 1 行上、1 行下、1 列左、1 列右。
        窄屏时自动降级为无边框（直接渲染子控件）。
        """
        if buffer.is_empty():
            return
        style = self._props.get("style", "rounded")
        title = self._props.get("title", "")
        title_align = self._props.get("title_align", "left")
        bc = self._props.get("border_color")

        # 窄屏降级：无边框，直接渲染子控件
        try:
            from ..terminal.terminal import is_narrow as _is_narrow
            if _is_narrow():
                child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
                if child is not None:
                    try:
                        child.render(buffer)
                    except Exception as e:
                        _logger.debug("Border narrow: child.render failed: %s", e)
                return
        except ImportError:
            pass

        # 获取边框字符集
        chars = self._get_box_chars(style)
        if chars is None:
            child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
            if child is not None:
                try:
                    child.render(buffer)
                except Exception as e:
                    _logger.debug("Border chars None: child.render failed: %s", e)
            return

        # 边框占用 1 行上、1 行下、1 列左、1 列右
        inner_w = max(0, buffer.width - 2)
        inner_h = max(0, buffer.height - 2)
        if inner_w <= 0 or inner_h <= 0:
            return

        # 构建 ANSI 颜色前缀/后缀
        pre = f"\033[38;5;{bc}m" if bc is not None else ""
        suf = "\033[0m" if bc is not None else ""

        tl, tr, bl, br = chars["tl"], chars["tr"], chars["bl"], chars["br"]
        h, v = chars["h"], chars["v"]

        # 顶部边框（含标题）
        top_line = self._build_top_border(
            tl, tr, h, buffer.width, title, title_align, pre, suf,
        )
        buffer.write(0, 0, top_line)

        # 底部边框
        bottom = f"{pre}{bl}{h * (buffer.width - 2)}{br}{suf}"
        buffer.write(0, buffer.height - 1, bottom)

        # 左右边框
        for row in range(inner_h):
            buf_row = row + 1
            if buf_row < buffer.height:
                buffer.write(0, buf_row, f"{pre}{v}{suf}")
                buffer.write(buffer.width - 1, buf_row, f"{pre}{v}{suf}")

        # 子控件渲染到内部区域
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is not None:
            # 渲染到临时缓冲区再合并回父缓冲区
            tmp = RenderBuffer(inner_w, inner_h)
            try:
                child.render(tmp)
            except Exception as e:
                _logger.debug("Border: child.render failed: %s", e)
            buffer.merge(tmp, 1, 1)

    def _build_top_border(
        self, tl: str, tr: str, h: str,
        box_w: int, title: str, align: str,
        pre: str, suf: str,
    ) -> str:
        """构建顶部边框行。

        Args:
            tl: 左上角字符。
            tr: 右上角字符。
            h: 水平边框字符。
            box_w: 边框总宽度。
            title: 标题文本。
            align: 标题对齐方式。
            pre: ANSI 颜色前缀。
            suf: ANSI 颜色后缀。

        Returns:
            格式化后的顶部边框字符串。
        """
        fill_w = box_w - 2  # 减去 tl + tr
        if not title:
            return f"{pre}{tl}{h * fill_w}{tr}{suf}"

        # 标题装饰: "[ title ]"（不使用 \033[0m 重置，保持与外层 border_color 连贯）
        title_deco = f"[ {title} ]"
        # 实际视觉宽度 = len(title) + 4 (括号+空格)
        title_vw = len(title) + 4

        if title_vw > fill_w:
            max_t = fill_w - 4
            if max_t < 1:
                return f"{pre}{tl}{h * fill_w}{tr}{suf}"
            title_disp = title[:max_t]
            title_deco = f"[ {title_disp} ]"
            title_vw = len(title_disp) + 4

        remaining = fill_w - title_vw
        if align == "left":
            left_h, right_h = 0, remaining
        elif align == "right":
            left_h, right_h = remaining, 0
        else:
            left_h = remaining // 2
            right_h = remaining - left_h

        return (
            f"{pre}{tl}{h * left_h}"
            f"{title_deco}"
            f"{pre}{h * right_h}{tr}{suf}"
        )

    @staticmethod
    def _get_box_chars(style_name: str) -> dict | None:
        """获取边框字符集。

        Args:
            style_name: 边框样式名。

        Returns:
            边框字符字典，包含 tl/tr/bl/br/h/v 键。获取失败时返回 None。
        """
        try:
            from ..components._box import BoxStyle as _BS, _BOX_CHARS as _BC
            # 将字符串转为 BoxStyle 枚举
            for bs in _BS:
                if bs.value == style_name:
                    return _BC.get(bs)
            # 兜底使用 ROUNDED
            return _BC.get(_BS.ROUNDED)
        except (ImportError, AttributeError):
            # 降级：基本 ASCII 边框
            return {
                "tl": "+", "tr": "+", "bl": "+", "br": "+",
                "h": "-", "v": "|",
            }

    def __repr__(self) -> str:
        return f"Border(style={self._props.get('style')})"
