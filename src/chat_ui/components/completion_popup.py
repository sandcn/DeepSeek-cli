"""React Ink 补全弹窗组件 — 声明式补全弹窗渲染。"""
from __future__ import annotations
from .base import TuiComponent


class CompletionPopupComponent(TuiComponent):
    """补全弹窗 — 无边框扁平样式，render() 产出 ANSI 字符串。
    
    Props:
        items: list[str] - 显示文本列表
        texts: list[str] - 替换文本列表
        selected: int - 当前选中索引
        title: str - 弹窗标题
        is_selection: bool - 是否为选择模式
        term_width: int - 终端宽度
    """
    
    def __init__(self, **props):
        super().__init__(children=None)
        self._props = props
    
    def render(self) -> str:
        items = self._props.get("items", [])
        if not items or not self._props.get("visible", False):
            return ""
        
        from ..bottom_bar._theme import (
            _COLOR_COMPLETE_TITLE, _COLOR_DIM, _COLOR_RESET,
            _COLOR_SELECT_BG, _COLOR_SELECT_FG, _COLOR_TIME,
        )
        from ..bottom_bar._cursor import _truncate_by_width, _visual_len
        
        title = self._props.get("title", "补全")
        texts = self._props.get("texts", items)
        selected = self._props.get("selected", 0)
        is_selection = self._props.get("is_selection", False)
        term_width = self._props.get("term_width", 80)
        
        total_items = len(texts)
        n = len(items)
        popup_w = min(term_width - 2, 50)
        cell_w = popup_w - 3
        
        lines = []
        
        # 标题行
        header = f" {_COLOR_COMPLETE_TITLE}{title}{_COLOR_RESET} {_COLOR_DIM}({total_items}项){_COLOR_RESET}"
        lines.append(header)
        
        # 选项行
        for i, item in enumerate(items):
            display = _truncate_by_width(item, cell_w)
            pad = " " * max(0, cell_w - _visual_len(display))
            if i == selected:
                lines.append(
                    f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                    f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}"
                )
            else:
                lines.append(f"  {display}{pad}")
        
        # 快捷键提示行
        truncated = total_items > n
        hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
        if truncated:
            hint = f" {_COLOR_TIME}{selected + 1}/{n}{_COLOR_RESET} {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} "
        else:
            hint = f" {hint_prefix} "
        lines.append(f"{_COLOR_DIM}{hint}{_COLOR_RESET}")
        
        return "\n".join(lines)
