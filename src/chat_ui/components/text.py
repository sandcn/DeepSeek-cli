"""Text 组件 — React Ink 文本输出。

提供 <Text> 组件，支持：
  - ANSI 样式：color / backgroundColor / bold / italic / underline / strikethrough / dim / inverse / hidden
  - 文本换行模式：wrap / truncate / truncate-middle / truncate-end
  - 子组件渲染：字符串或 TuiComponent 列表作为 children

继承自 TuiComponent，复用 StyledText 和 ANSI 基础设施。

使用示例：
    Text("Hello", color="red", bold=True)
    Text("Long text...", wrap="truncate", width=10)
    Text(children=Text("nested"), color="blue")
"""

from __future__ import annotations

import shutil
import unicodedata
from typing import TYPE_CHECKING

from ..components.base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（CJK 字符计为 2 列，其余 1 列）。

    不处理 ANSI 转义序列——调用方应先剥离 ANSI。
    """
    w = 0
    for ch in text:
        w += 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
    return w


def _truncate_end(text: str, max_width: int) -> str:
    """按视觉宽度从末尾截断，追加 "…"（U+2026，宽度 1）。

    Args:
        text: 纯文本（无 ANSI）。
        max_width: 最大视觉宽度。

    Returns:
        截断后的文本（含 "…"），未超出时原样返回。
    """
    if max_width <= 0:
        return ""
    ellipsis = "\u2026"  # … — 单字符，视觉宽度 1
    ellipsis_w = 1
    if _visual_width(text) <= max_width:
        return text
    # 逐字符累加宽度，预留 "…" 空间
    w = 0
    for i, ch in enumerate(text):
        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if w + cw + ellipsis_w > max_width:
            return text[:i] + ellipsis
        w += cw
    return text + ellipsis


def _truncate_middle(text: str, max_width: int) -> str:
    """从中间截断，用 "…" 替换中间部分。

    Args:
        text: 纯文本（无 ANSI）。
        max_width: 最大视觉宽度。

    Returns:
        截断后的文本（如 "long…ext"），未超出时原样返回。
    """
    if max_width <= 0:
        return ""
    ellipsis = "\u2026"
    ellipsis_w = 1
    total_w = _visual_width(text)
    if total_w <= max_width:
        return text
    # 前后各分配一半宽度（减去省略号）
    half = (max_width - ellipsis_w) / 2
    prefix_w = int(half)
    suffix_w = max_width - ellipsis_w - prefix_w

    # 从前向后取 prefix_w 宽度的字符
    w = 0
    prefix = ""
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if w + cw > prefix_w:
            break
        prefix += ch
        w += cw

    # 从后向前取 suffix_w 宽度的字符
    w = 0
    suffix = ""
    for ch in reversed(text):
        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if w + cw > suffix_w:
            break
        suffix = ch + suffix
        w += cw

    return prefix + ellipsis + suffix


class Text(TuiComponent):
    """React Ink Text 组件。

    渲染带 ANSI 样式的文本内容，支持截断和子组件。

    Props:
        color: 文本颜色（ANSI 颜色名或 '#RRGGBB'）。
        backgroundColor: 背景色（ANSI 颜色名或 '#RRGGBB'）。
        bold: 粗体。
        italic: 斜体。
        underline: 下划线。
        strikethrough: 删除线。
        dim: 暗色。
        inverse: 反色。
        hidden: 隐藏文本（ANSI 代码 8）。
        wrap: 换行模式 — "wrap" | "truncate" | "truncate-middle" | "truncate-end"。
              None 时不截断，由布局引擎处理换行。
        width: 截断宽度（列数），wrap 非 None 时生效。
               未指定时使用终端宽度。
        children: 文本内容（str）或子组件列表。
    """

    def __init__(
        self,
        children: str | list[TuiComponent] | None = None,
        *,
        color: str | None = None,
        backgroundColor: str | None = None,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        strikethrough: bool = False,
        dim: bool = False,
        inverse: bool = False,
        hidden: bool = False,
        wrap: str | None = None,
        width: int | None = None,
    ):
        """初始化 Text 组件。

        Args:
            children: 文本内容或子组件列表。默认空字符串。
            color: 前景色名（如 'red', '#FF0000'）。
            backgroundColor: 背景色名。
            bold/italic/underline/strikethrough/dim/inverse/hidden: 样式标志。
            wrap: 换行模式，默认 None（不截断）。
            width: 截断宽度，默认 None（自动取终端宽度）。
        """
        # 处理 children：统一转为内部格式
        if children is None:
            children = ""
        if isinstance(children, str):
            super().__init__(children=[])  # 纯文本模式，无子组件
        else:
            super().__init__(children=list(children))  # 子组件列表

        self._text_content: str = children if isinstance(children, str) else ""
        self._props: dict = {
            "color": color,
            "backgroundColor": backgroundColor,
            "bold": bold,
            "italic": italic,
            "underline": underline,
            "strikethrough": strikethrough,
            "dim": dim,
            "inverse": inverse,
            "hidden": hidden,
            "wrap": wrap,
            "width": width,
        }

    @property
    def key(self) -> str:
        """稳定标识符 — 用于 VNode Diff 的 key 匹配。"""
        return "text"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。

        Args:
            props: 新的属性字典。

        Returns:
            True 如果任何属性发生变化。
        """
        changed = False
        for k in self._props:
            if k in props and props[k] != self._props[k]:
                self._props[k] = props[k]
                changed = True
        # 若 children 变化且为字符串，更新 _text_content
        if "children" in props:
            new_children = props["children"]
            if isinstance(new_children, str):
                if new_children != self._text_content:
                    self._text_content = new_children
                    changed = True
            elif isinstance(new_children, list):
                # 子组件列表：更新实例的 children
                self._ensure_children().clear()
                self._ensure_children().extend(new_children)
                changed = True
        return changed

    def _resolve_width(self) -> int | None:
        """解析截断宽度。

        若 props 中指定了 width，直接使用；
        否则从终端尺寸获取（wrap 非 None 时）。

        Returns:
            截断宽度（列数），无法获取时返回 None。
        """
        w = self._props.get("width")
        if w is not None:
            return w
        wrap = self._props.get("wrap")
        if wrap is None or wrap == "wrap":
            return None
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return None

    def _apply_wrap(self, text: str) -> str:
        """对纯文本应用 wrap 截断。

        Args:
            text: 纯文本内容（已剥离 ANSI）。

        Returns:
            截断后的纯文本（含 "…"），无需截断时原样返回。
        """
        wrap = self._props.get("wrap")
        if wrap is None or wrap == "wrap":
            return text
        width = self._resolve_width()
        if width is None or width <= 0:
            return text
        if _visual_width(text) <= width:
            return text
        if wrap == "truncate-middle":
            return _truncate_middle(text, width)
        else:
            # "truncate" 或 "truncate-end"
            return _truncate_end(text, width)

    def render(self) -> str | StyledText:
        """渲染 Text 组件为样式化文本。

        - 若有子组件（TuiComponent 列表）：渲染子组件，提取纯文本后应用样式
        - 若为纯文本 children：直接应用样式
        - 根据 wrap 模式截断文本
        """
        children_list = self._ensure_children()
        # 提取文本内容
        if children_list:
            # 子组件模式：渲染每个子组件并拼接
            outputs: list[str] = []
            for child in children_list:
                result = child.render()
                if isinstance(result, StyledText):
                    outputs.append(result.plain)
                elif isinstance(result, str):
                    outputs.append(result)
            text = "".join(outputs)
        else:
            text = self._text_content

        # 应用 wrap 截断（在应用样式前，对纯文本操作）
        text = self._apply_wrap(text)

        if not text:
            return ""

        # 构建 StyledText
        p = self._props
        # 将 inverse 映射为 reverse（StyledText 使用 reverse）
        # hidden 在 StyledText 中使用 strikethrough 风格的独立字段传递
        return StyledText(
            text,
            fg=p.get("color"),
            bg=p.get("backgroundColor"),
            bold=bool(p.get("bold")),
            dim=bool(p.get("dim")),
            italic=bool(p.get("italic")),
            underline=bool(p.get("underline")),
            reverse=bool(p.get("inverse")),
            strikethrough=bool(p.get("strikethrough")),
        )

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。

        Returns:
            VNode(type="text", key="text", props=...)
        """
        from ..vdom.vnode import VNode
        rendered = self.render()
        vnode_props: dict = {
            "text": str(rendered) if rendered else "",
        }
        # 传递样式属性供 Diff 使用
        for k in ("color", "backgroundColor", "bold", "italic", "underline",
                  "strikethrough", "dim", "inverse", "hidden", "wrap", "width"):
            v = self._props.get(k)
            if v is not None and v is not False:
                vnode_props[k] = v
        return VNode(
            type="text",
            key=self.key,
            props=vnode_props,
        )
