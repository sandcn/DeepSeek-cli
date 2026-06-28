"""BottomBarContent — React Ink 底部栏内容渲染组件。

产出完整底部栏 ANSI 字符串（含分隔线、状态行、输入区、补全弹窗、SubAgent 槽位）。
纯渲染组件，render() 返回 ANSI 字符串，不做终端 I/O。

从 _bottom_bar.py 的 _draw_all_locked() + _draw_input_lines_locked() 职责迁移，
替代旧的命令式 ANSI 直接写入。
"""

from __future__ import annotations

from wcwidth import wcswidth

from .base import TuiComponent
from ..bottom_bar._theme import (
    _COLOR_ACCENT, _COLOR_DIM, _COLOR_RESET, _COLOR_SEP,
    _COLOR_SELECT_BG, _COLOR_SELECT_FG, _COLOR_COMPLETE_TITLE,
    _PLACEHOLDER_TEXT, _PLACEHOLDER_COMPACT, _PLACEHOLDER_STREAMING,
    _COLOR_TIME,
    CLAUDE_PROMPT_COLOR,
)
from ..bottom_bar._cursor import _truncate_by_width, _visual_len, _wrap_by_width

# ── 补全弹窗常量 ──────────────────────────────────────────
_COMPLETION_MAX_ITEMS = 10       # 单屏最多显示选项数
_COMPLETION_MAX_WIDTH = 50       # 弹窗最大宽度


class BottomBarContent(TuiComponent):
    """底部栏内容渲染组件 — 产出完整底部栏 ANSI 字符串。

    Props 字典包含：
        term_width: int          — 终端宽度
        status_text: str         — 状态行文本（已渲染的 ANSI 字符串）
        input_text: str          — 输入文本
        input_cursor_pos: int    — 光标位置
        is_streaming: bool       — 是否流式输出中
        completion_items: tuple  — 补全项列表
        completion_selected: int — 补全选中索引
        completion_visible: bool — 补全弹窗是否可见
        completion_title: str    — 补全弹窗标题（默认 "补全"）
        completion_is_selection: bool — 是否选择模式
        subagent_slots: dict     — SubAgent 槽位数据
        claude_style: bool       — 是否 Claude Code 风格
    """

    def __init__(self, **props):
        super().__init__(children=None)
        self._props = props

    # ── Props 访问器 ─────────────────────────────────────

    @property
    def term_width(self) -> int:
        return self._props.get("term_width", 80)

    @property
    def status_text(self) -> str:
        return self._props.get("status_text", "")

    @property
    def input_text(self) -> str:
        return self._props.get("input_text", "")

    @property
    def input_cursor_pos(self) -> int:
        return self._props.get("input_cursor_pos", 0)

    @property
    def is_streaming(self) -> bool:
        return self._props.get("is_streaming", False)

    @property
    def completion_items(self) -> tuple:
        return self._props.get("completion_items", ())

    @property
    def completion_selected(self) -> int:
        return self._props.get("completion_selected", 0)

    @property
    def completion_visible(self) -> bool:
        return self._props.get("completion_visible", False)

    @property
    def completion_title(self) -> str:
        return self._props.get("completion_title", "补全")

    @property
    def completion_is_selection(self) -> bool:
        return self._props.get("completion_is_selection", False)

    @property
    def subagent_slots(self) -> dict:
        return self._props.get("subagent_slots", {})

    @property
    def claude_style(self) -> bool:
        return self._props.get("claude_style", False)

    # ── Render ───────────────────────────────────────────

    def render(self) -> str:
        """产出完整底部栏 ANSI 字符串。

        输出结构（按行拼接，用 \\n 分隔）：
        1. SubAgent 槽位行（若非空）
        2. 分隔线
        3. 状态行
        4. 输入区（❯ 提示符 + 文本/占位符）
        5. 补全弹窗（若可见）
        """
        lines: list[str] = []

        # 1. SubAgent 槽位
        subagent_lines = self._render_subagent_slots()
        if subagent_lines:
            lines.extend(subagent_lines)

        # 2. 分隔线
        lines.append(self._render_separator())

        # 3. 状态行
        if self.status_text:
            lines.append(self.status_text)

        # 4. 输入区
        lines.extend(self._render_input_area())

        # 5. 补全弹窗
        if self.completion_visible and self.completion_items:
            lines.extend(self._render_completion_popup())

        return "\n".join(lines)

    # ── 分隔线渲染 ───────────────────────────────────────

    def _render_separator(self) -> str:
        """渲染分隔线：━ 字符 × min(term_width - 2, 40)。"""
        sep_width = min(self.term_width - 2, 40)
        sep_width = max(sep_width, 1)
        sep_chars = "━" * sep_width
        return f"{_COLOR_SEP}{sep_chars}{_COLOR_RESET}"

    # ── SubAgent 槽位渲染 ────────────────────────────────

    def _render_subagent_slots(self) -> list[str]:
        """渲染 SubAgent 槽位行，使用 Tree 树控件渲染。

        Returns:
            渲染后的行列表（可能为空）。
        """
        slots = self.subagent_slots
        if not slots:
            return []
        try:
            from .subagent_tree import subagent_slots_to_tree
            from .tree import Tree

            tree_root = subagent_slots_to_tree(slots)
            if tree_root is None:
                return []

            tree = Tree(root=tree_root, indent=2)
            rendered = tree.render()
            if not rendered:
                return []

            return rendered.split('\n')
        except Exception:
            return []

    # ── 输入区渲染 ────────────────────────────────────────

    def _render_input_area(self) -> list[str]:
        """渲染输入区：❯ 提示符 + 输入文本（或占位符）。

        拆行处理超长输入，续行使用 · 前缀。
        """
        text = self.input_text
        tw = self.term_width

        prompt_color = CLAUDE_PROMPT_COLOR if self.claude_style else _COLOR_ACCENT
        lines: list[str] = []

        if not text:
            # 空输入 → 显示占位符
            placeholder = self._get_placeholder()
            prompt = f"{prompt_color}❯{_COLOR_RESET} "
            lines.append(f"{prompt}{_COLOR_DIM}{placeholder}{_COLOR_RESET}")
            return lines

        # 有输入文本 → 按宽度拆行
        # 展开制表符
        from ..bottom_bar._cursor import _expand_tabs
        expanded = _expand_tabs(text)

        # 按终端宽度拆行
        # 第一行前缀宽度：❯ 占 1 列视觉宽度（但它是 CJK 兼容的）
        # 使用 wcswidth 计算 ❯ 的宽度：通常是 2（CJK ambiguous width）
        prompt_char = "❯"
        prompt_visual_w = wcswidth(prompt_char) if wcswidth(prompt_char) >= 0 else 1
        first_line_prefix = "  "  # 2 空格缩进
        first_line_avail = tw - len(first_line_prefix) - prompt_visual_w - 1  # -1 for space after ❯

        if first_line_avail <= 0:
            first_line_avail = tw - 2

        cont_line_prefix = "   · "
        cont_line_avail = tw - len(cont_line_prefix)

        # 拆行
        wrapped = _wrap_by_width(expanded, first_line_avail)

        if not wrapped:
            wrapped = [""]

        # 第一行：  ❯ first_segment
        first_segment = wrapped[0]
        prompt = f"{prompt_color}❯{_COLOR_RESET} "
        lines.append(f"{first_line_prefix}{prompt}{first_segment}")

        # 续行：   · rest
        remaining = expanded[len(wrapped[0]):]
        while remaining:
            # 重新按 cont_line_avail 拆行
            cont_wrapped = _wrap_by_width(remaining, cont_line_avail)
            if not cont_wrapped:
                break
            seg = cont_wrapped[0]
            lines.append(f"{cont_line_prefix}{_COLOR_DIM}{seg}{_COLOR_RESET}")
            remaining = remaining[len(seg):]

        return lines

    def _get_placeholder(self) -> str:
        """根据当前状态返回合适的占位符文本。"""
        if self.is_streaming:
            return _PLACEHOLDER_STREAMING
        if self.completion_visible:
            return _PLACEHOLDER_COMPACT
        return _PLACEHOLDER_TEXT

    # ── 补全弹窗渲染 ─────────────────────────────────────

    def _render_completion_popup(self) -> list[str]:
        """渲染补全弹窗（内联实现，不创建额外文件）。

        输出结构：
        - 标题行：{title} (N项)
        - 选项行：▶ 选中项（高亮）+ 普通项
        - 快捷键提示行
        """
        items = list(self.completion_items)
        if not items:
            return []

        tw = self.term_width
        popup_w = min(tw - 2, _COMPLETION_MAX_WIDTH)
        cell_w = popup_w - 3  # 前缀 " ▶ " 或 "   " 占 3 列
        selected = max(0, min(self.completion_selected, len(items) - 1))
        total = len(items)

        # 截断到最大显示项数
        display_items = items[:_COMPLETION_MAX_ITEMS]
        n = len(display_items)
        truncated = total > n

        lines: list[str] = []

        # ── 标题行 ──
        header = (
            f" {_COLOR_COMPLETE_TITLE}{self.completion_title}{_COLOR_RESET}"
            f" {_COLOR_DIM}({total}项){_COLOR_RESET}"
        )
        lines.append(header)

        # ── 选项行 ──
        for i, item in enumerate(display_items):
            display = _truncate_by_width(item, cell_w)
            vis_len = _visual_len(display)
            pad = " " * max(0, cell_w - vis_len)
            if i == selected:
                # 选中项：▶ 前缀 + 高亮背景
                lines.append(
                    f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                    f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}"
                )
            else:
                # 普通项：缩进对齐
                lines.append(f"  {display}{pad}")

        # ── 快捷键提示行 ──
        is_selection = self.completion_is_selection
        if is_selection:
            hint_prefix = "\u2191\u2193 Enter Esc"
        else:
            hint_prefix = "Tab \u2191\u2193 Esc"

        if truncated:
            hint = (
                f" {_COLOR_TIME}{selected + 1}/{n}{_COLOR_RESET}"
                f" {_COLOR_DIM}(\u524d{n}/{total}){_COLOR_RESET}"
                f"  {hint_prefix} "
            )
        else:
            hint = f" {hint_prefix} "

        lines.append(f"{_COLOR_DIM}{hint}{_COLOR_RESET}")

        return lines
