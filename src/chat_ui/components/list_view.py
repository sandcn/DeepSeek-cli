"""ListView 组件 — React Ink 风格通用列表组件。

支持选中项高亮、分组标题、分割线、编号、自定义前缀。

使用示例:
    lv = ListView(
        items=[
            {"label": "苹果", "selected": True},
            {"label": "香蕉"},
            {"label": "橘子"},
        ],
        show_numbers=True,
    )
    print(lv.render())
"""

from __future__ import annotations

import shutil
import unicodedata
from typing import TYPE_CHECKING, Any

from .base import TuiComponent
from ..infrastructure.styled import StyledText
from ..infrastructure.ansi import ANSI_RESET

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（CJK 字符计为 2 列）。"""
    w = 0
    for ch in text:
        w += 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
    return w


class ListView(TuiComponent):
    """React Ink ListView 组件 — 通用列表。

    支持选中项高亮、分组、分割线、编号/自定义前缀。

    Props:
        items: list[dict] — 列表项，每项可含
            label (str): 显示文本
            selected (bool): 是否选中
            group (str): 分组名
            prefix (str): 自定义前缀
        selected: str | None — 选中项标识（匹配 label），
            当 item 中无显式 selected 时生效
        show_numbers: bool — 显示编号（默认 False）
        show_divider: bool — 项间显示分割线（默认 False）
        group_header_style: str — 分组标题样式 ("dim"/"bold"/color_name，默认 "dim")
        children: list[TuiComponent] | None
    """

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        selected: str | None = None,
        show_numbers: bool = False,
        show_divider: bool = False,
        group_header_style: str = "dim",
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._items = list(items) if items else []
        self._selected = selected
        self._show_numbers = show_numbers
        self._show_divider = show_divider
        self._group_header_style = group_header_style

    @property
    def key(self) -> str:
        return "list_view"

    def update(self, props: dict) -> bool:
        changed = False
        if "items" in props and props["items"] != self._items:
            self._items = list(props["items"]) if props["items"] else []
            changed = True
        if "selected" in props and props["selected"] != self._selected:
            self._selected = props["selected"]
            changed = True
        if "show_numbers" in props and props["show_numbers"] != self._show_numbers:
            self._show_numbers = props["show_numbers"]
            changed = True
        if "show_divider" in props and props["show_divider"] != self._show_divider:
            self._show_divider = props["show_divider"]
            changed = True
        if "group_header_style" in props and props["group_header_style"] != self._group_header_style:
            self._group_header_style = props["group_header_style"]
            changed = True
        return changed

    def _is_item_selected(self, item: dict[str, Any], index: int) -> bool:
        """判断列表项是否选中。

        优先级：item dict 中的 selected 字段 > selected prop 匹配 label。
        """
        if "selected" in item:
            return bool(item["selected"])
        label = item.get("label", "")
        return bool(self._selected) and label == self._selected

    def _render_item(
        self,
        item: dict[str, Any],
        index: int,
        number: int,
        is_selected: bool,
    ) -> StyledText:
        """渲染单个列表项。"""
        label = item.get("label", "")
        prefix = item.get("prefix", "")

        # 构建前缀部分
        parts: list[str | StyledText] = []

        if prefix:
            # 自定义前缀
            parts.append(StyledText(prefix))
        elif self._show_numbers:
            parts.append(StyledText(f"{number}. "))

        # 标签部分
        if is_selected:
            parts.append(StyledText(label, fg="blue", bold=True))
        else:
            parts.append(StyledText(label))

        if not parts:
            return StyledText("")

        return StyledText.assemble(*parts) if len(parts) > 1 else parts[0]

    def _render_group_header(self, group_name: str) -> StyledText:
        """渲染分组标题。

        样式由 group_header_style 控制：
        - "dim" → dim 样式
        - "bold" → 加粗
        - 其他颜色名 → 使用该前景色
        """
        style = self._group_header_style
        if style == "dim":
            return StyledText(f"── {group_name} ──", dim=True)
        elif style == "bold":
            return StyledText(f"── {group_name} ──", bold=True)
        else:
            # 当作颜色名处理
            return StyledText(f"── {group_name} ──", fg=style)

    def _render_divider(self) -> StyledText:
        """渲染分割线。"""
        try:
            term_w = shutil.get_terminal_size().columns
        except Exception:
            term_w = 80
        line = "\u2500" * min(term_w, 80)
        return StyledText(line, dim=True)

    def render(self) -> str | StyledText:
        if not self._items:
            return ""

        parts: list[str | StyledText] = []

        # 按 group 分组
        groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        nongroup_items: list[tuple[int, dict[str, Any]]] = []

        for i, item in enumerate(self._items):
            group = item.get("group", "")
            if group:
                groups.setdefault(group, []).append((i, item))
            else:
                nongroup_items.append((i, item))

        global_number = 1

        # 渲染非分组项
        if nongroup_items:
            for idx, (i, item) in enumerate(nongroup_items):
                is_selected = self._is_item_selected(item, i)
                parts.append(self._render_item(item, i, global_number, is_selected))
                global_number += 1

                if self._show_divider and idx < len(nongroup_items) - 1:
                    parts.append(self._render_divider())

        # 渲染分组项
        for group_name, group_items in groups.items():
            # 分组标题
            parts.append(self._render_group_header(group_name))

            for idx, (i, item) in enumerate(group_items):
                is_selected = self._is_item_selected(item, i)
                parts.append(self._render_item(item, i, global_number, is_selected))
                global_number += 1

                if self._show_divider and idx < len(group_items) - 1:
                    parts.append(self._render_divider())

        children_output = self.render_children()
        if children_output:
            if isinstance(children_output, str):
                parts.append(StyledText(children_output))
            else:
                parts.append(children_output)

        if not parts:
            return ""

        return StyledText.assemble(*parts) if len(parts) > 1 else parts[0]

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="list_view",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "item_count": len(self._items),
                "show_numbers": self._show_numbers,
            },
        )
