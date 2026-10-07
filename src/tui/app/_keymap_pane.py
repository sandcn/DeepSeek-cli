"""键位速查面板渲染（通用）——轨迹 / 插件 / 配置视图共享。

把「表现层数据注册表里的键位表 → 对齐的 ``list[list[StyledRun]]`` 行」
这段纯渲染逻辑收敛为单一实现（各视图样式不同，经参数注入解耦）：

  - ``keymap_panel_rows(entries, width, ...)``：分组标题（``▸ 分组 ───``）+
    ``键位  说明`` 行（键列按最长键位对齐）+ 行宽截断。

视图侧（trace_help / plugin_view / config_view）只负责取各自数据表
（``presentation_data.trace_keymap`` / ``plugin_keymap`` / ``config_keymap``）
并传入样式常量。
"""

from __future__ import annotations

from src.tui._screen import wcswidth_simple
from src.tui.ink import StyledRun
from src.tui.ink.helpers import truncate_runs

__all__ = ["keymap_panel_rows", "disp_width", "group_header_runs"]

#: 分组标题前缀（与统计/检查器小节前缀一致）。
SECTION_PREFIX = "\u25b8 "  # ▸
#: 键列最小宽度（键位短时仍保持对齐）。
KEY_COL_MIN = 10


def disp_width(text: str) -> int:
    """文本显示宽度（东亚宽/全角按 2 列；异常回退字符数）。"""
    try:
        return wcswidth_simple(text)
    except Exception:
        return len(text)


def group_header_runs(label: str, width: int, group_style, sep_style) -> list:
    """分组标题行：``▸ 分组名 `` + ``─`` 填充至满宽。"""
    text = f"{SECTION_PREFIX}{label} "
    pad = max(0, int(width) - disp_width(text))
    runs = [StyledRun(text, group_style)]
    if pad:
        runs.append(StyledRun("\u2500" * pad, sep_style))
    return runs


def keymap_panel_rows(
    entries: list, width: int, *,
    key_style, group_style, desc_style, sep_style,
    empty_text: str = "(快捷键速查表未注册)",
) -> list:
    """键位表 → 面板内容行（滚动窗口数据源）。

    按注册顺序分组输出：每组一个小节标题 + 若干 ``键位  说明`` 行（键列按
    最长键位对齐）；行宽截断到 ``width``，空 run 清理（行级 diff 宽度
    不变量）。

    Args:
        entries: 键位表（``[{"group","keys","desc"}, ...]``）。
        width: 面板宽（键列预算/行宽预算）。
        key_style: 键位样式。
        group_style: 分组标题样式。
        desc_style: 说明样式。
        sep_style: 分组标题填充线样式。
        empty_text: 空态提示文本。

    Returns:
        ``list[list[StyledRun]]``。
    """
    width = max(1, int(width))
    entries = list(entries or [])
    if not entries:
        return [[StyledRun(empty_text, desc_style)]]
    key_col = KEY_COL_MIN
    for item in entries:
        if isinstance(item, dict):
            key_col = max(key_col, disp_width(str(item.get("keys", ""))))
    key_col = min(key_col, max(KEY_COL_MIN, width // 2))
    rows: list = []
    current_group = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        group = str(item.get("group", "") or "")
        keys = str(item.get("keys", "") or "")
        desc = str(item.get("desc", "") or "")
        if group and group != current_group:
            if rows:
                rows.append([StyledRun("", None)])
            rows.append(group_header_runs(group, width, group_style, sep_style))
            current_group = group
        pad = max(1, key_col - disp_width(keys))
        rows.append([
            StyledRun("  ", None),
            StyledRun(keys, key_style),
            StyledRun(" " * pad, None),
            StyledRun(desc, desc_style),
        ])
    out: list = []
    for row in rows:
        cleaned = [r for r in row if r.text]
        if not cleaned:
            cleaned = [StyledRun(" ", None)]
        out.append(truncate_runs(cleaned, width))
    return out
