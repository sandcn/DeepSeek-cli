"""BottomBarContent — React Ink 底部栏内容渲染组件。

产出完整底部栏 ANSI 字符串（含分隔线、状态行、输入区、补全弹窗、SubAgent 槽位）。
使用 Box / Text / Tree 标准控件构建 VNode 组件树，替代旧的手动 ANSI 字符串拼接。

从 _bottom_bar.py 的 _draw_all_locked() + _draw_input_lines_locked() 职责迁移，
替代旧的命令式 ANSI 直接写入。
"""

from __future__ import annotations

from wcwidth import wcswidth

from .base import TuiComponent
from .box import Box
from .fixed_box import FixedSizeBox
from .text import Text
from .tree import Tree
from ..infrastructure.bottom_theme import (
    _COLOR_ACCENT, _COLOR_DIM, _COLOR_RESET,
    _COLOR_SELECT_BG, _COLOR_SELECT_FG, _COLOR_COMPLETE_TITLE,
    _PLACEHOLDER_TEXT, _PLACEHOLDER_COMPACT, _PLACEHOLDER_STREAMING,
    _COLOR_TIME,
    CLAUDE_PROMPT_COLOR,
)
from ..infrastructure.text_visual import _truncate_by_width, _visual_len, _wrap_by_width

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

        使用 Box / Text / Tree 标准控件构建组件树：
        Box (border_style="single", border_dim_color=True)
        ├── Tree (subagent_slots)        — 仅在 slots 非空时
        ├── Text (分隔线, dim=True)
        ├── Text (状态行)
        ├── Text × N (输入区)
        └── Box (补全弹窗)               — 仅在 visible 时
        """
        children: list[TuiComponent] = []

        # 1. SubAgent 槽位 → Tree
        subagent_tree = self._build_subagent_tree()
        if subagent_tree is not None:
            children.append(subagent_tree)

        # 2. 分隔线 → Text
        sep_width = min(self.term_width - 2, 40)
        sep_width = max(sep_width, 1)
        children.append(Text("━" * sep_width, dim=True))

        # 3. 状态行 → Text（status_text 已是 ANSI 字符串，透传即可）
        if self.status_text:
            children.append(Text(self.status_text))

        # 4. 输入区 → Text（可能多行）
        input_texts = self._render_input_area_texts()
        children.extend(input_texts)

        # 5. 补全弹窗 → Box
        if self.completion_visible and self.completion_items:
            popup_box = self._build_completion_popup_box()
            children.append(popup_box)

        # 组装顶层 FixedSizeBox 容器并渲染
        # 固定宽度 = 终端宽度 - 2（左右边框），确保内容不水平溢出
        inner_w = max(1, self.term_width - 2)

        # 预计算内容行数作为 FixedSizeBox height
        total_lines = 0
        for child in children:
            r = child.render()
            text = str(r) if r else ""
            if text:
                total_lines += text.count('\n') + 1

        box = FixedSizeBox(
            width=inner_w,
            height=max(1, total_lines),
            border_style="single",
            border_dim=True,
            padding_x=0,
            padding_y=0,
            children=children,
        )
        return box.render()

    # ── SubAgent 树构建 ──────────────────────────────────

    def _build_subagent_tree(self) -> Tree | None:
        """将 subagent_slots 转换为 Tree 组件。

        Returns:
            Tree 组件实例，或 None（slots 为空/转换失败时）。
        """
        slots = self.subagent_slots
        if not slots:
            return None
        try:
            from .subagent_tree import subagent_slots_to_tree

            tree_root = subagent_slots_to_tree(slots)
            if tree_root is None:
                return None

            return Tree(root=tree_root, indent=2)
        except Exception:
            return None

    # ── 输入区渲染 ────────────────────────────────────────

    def _render_input_area_texts(self) -> list[Text]:
        """渲染输入区为 Text 组件列表。

        拆行处理超长输入，续行使用 · 前缀。
        """
        text = self.input_text
        tw = self.term_width

        prompt_color = CLAUDE_PROMPT_COLOR if self.claude_style else _COLOR_ACCENT

        if not text:
            # 空输入 → 显示占位符
            placeholder = self._get_placeholder()
            line = (
                f"{prompt_color}❯{_COLOR_RESET} "
                f"{_COLOR_DIM}{placeholder}{_COLOR_RESET}"
            )
            return [Text(line)]

        # 有输入文本 → 按宽度拆行
        from ..infrastructure.text_visual import _expand_tabs
        expanded = _expand_tabs(text)

        # 第一行前缀宽度：❯ 的视觉宽度（CJK ambiguous，通常为 2）
        prompt_char = "❯"
        prompt_visual_w = wcswidth(prompt_char) if wcswidth(prompt_char) >= 0 else 1
        first_line_prefix = "  "
        first_line_avail = tw - len(first_line_prefix) - prompt_visual_w - 1  # -1 for space

        if first_line_avail <= 0:
            first_line_avail = tw - 2

        cont_line_prefix = "   · "
        cont_line_avail = tw - len(cont_line_prefix)

        wrapped = _wrap_by_width(expanded, first_line_avail)
        if not wrapped:
            wrapped = [""]

        texts: list[Text] = []

        # 第一行：  ❯ first_segment
        first_segment = wrapped[0]
        line = f"{first_line_prefix}{prompt_color}❯{_COLOR_RESET} {first_segment}"
        texts.append(Text(line))

        # 续行：   · rest（dim 色）
        remaining = expanded[len(wrapped[0]):]
        while remaining:
            cont_wrapped = _wrap_by_width(remaining, cont_line_avail)
            if not cont_wrapped:
                break
            seg = cont_wrapped[0]
            line = f"{cont_line_prefix}{_COLOR_DIM}{seg}{_COLOR_RESET}"
            texts.append(Text(line))
            remaining = remaining[len(seg):]

        return texts

    def _get_placeholder(self) -> str:
        """根据当前状态返回合适的占位符文本。"""
        if self.is_streaming:
            return _PLACEHOLDER_STREAMING
        if self.completion_visible:
            return _PLACEHOLDER_COMPACT
        return _PLACEHOLDER_TEXT

    # ── 补全弹窗渲染 ─────────────────────────────────────

    def _build_completion_popup_box(self) -> Box:
        """构建补全弹窗 Box 组件。

        输出结构：
        - 标题行：{title} (N项)
        - 选项行：▶ 选中项（高亮）+ 普通项
        - 快捷键提示行
        """
        items = list(self.completion_items)
        if not items:
            return Box(children=[])

        tw = self.term_width
        popup_w = min(tw - 2, _COMPLETION_MAX_WIDTH)
        cell_w = popup_w - 3  # 前缀 " ▶ " 或 "   " 占 3 列
        selected = max(0, min(self.completion_selected, len(items) - 1))
        total = len(items)

        # 截断到最大显示项数
        display_items = items[:_COMPLETION_MAX_ITEMS]
        n = len(display_items)
        truncated = total > n

        popup_children: list[TuiComponent] = []

        # ── 标题行 ──
        header = (
            f" {_COLOR_COMPLETE_TITLE}{self.completion_title}{_COLOR_RESET}"
            f" {_COLOR_DIM}({total}项){_COLOR_RESET}"
        )
        popup_children.append(Text(header))

        # ── 选项行 ──
        for i, item in enumerate(display_items):
            display = _truncate_by_width(item, cell_w)
            vis_len = _visual_len(display)
            pad = " " * max(0, cell_w - vis_len)
            if i == selected:
                # 选中项：▶ 前缀 + 高亮背景
                line = (
                    f" {_COLOR_SELECT_BG}{_COLOR_SELECT_FG}▶{_COLOR_RESET}"
                    f"{_COLOR_SELECT_BG}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}"
                )
            else:
                # 普通项：缩进对齐
                line = f"  {display}{pad}"
            popup_children.append(Text(line))

        # ── 快捷键提示行 ──
        is_selection = self.completion_is_selection
        if is_selection:
            hint_prefix = "↑↓ Enter Esc"
        else:
            hint_prefix = "Tab ↑↓ Esc"

        if truncated:
            hint = (
                f" {_COLOR_TIME}{selected + 1}/{n}{_COLOR_RESET}"
                f" {_COLOR_DIM}(前{n}/{total}){_COLOR_RESET}"
                f"  {hint_prefix} "
            )
        else:
            hint = f" {hint_prefix} "

        popup_children.append(Text(f"{_COLOR_DIM}{hint}{_COLOR_RESET}"))

        return Box(border_style="single", children=popup_children)
