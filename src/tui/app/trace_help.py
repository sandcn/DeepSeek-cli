"""轨迹视图帮助面板（``?`` 开关）——快捷键速查内容构建。

2026-10-07（用户需求：轨迹 Trace 操作 / 更多功能）新增：视图内帮助面板
展示轨迹视图全部快捷键（分组 + 键列对齐 + 描述）。

数据源 = 表现层数据注册表 ``presentation_data.trace_keymap``（「一切皆
插件」——分组/键位/说明可经 Patch/Overlay 整表替换或禁用）；本模块只负责
渲染成 ``list[StyledRun]`` 行（与检查器内容行同模型，供 TraceView 渲染）。
"""

from __future__ import annotations

from src.tui._screen import wcswidth_simple
from src.tui.ink import StyledRun

from .trace_styles import (
    _S_HELP_DESC,
    _S_HELP_GROUP,
    _S_HELP_KEY,
    _S_HINT,
    _S_SEP_ROW,
)

__all__ = ["help_panel_rows"]

#: 分组标题前缀（与统计面板/检查器小节前缀一致）。
_SECTION_PREFIX = "\u25b8 "  # ▸
#: 键列最小宽度（键位短时仍保持对齐）。
_KEY_COL_MIN = 10


def _disp_len(text: str) -> int:
    """文本显示宽度（东亚宽/全角按 2 列；异常回退字符数）。"""
    try:
        return wcswidth_simple(text)
    except Exception:
        return len(text)


def _group_header(label: str, width: int) -> list:
    """分组标题行：``▸ 分组名 `` + ``─`` 填充至满宽。"""
    text = f"{_SECTION_PREFIX}{label} "
    pad = max(0, width - _disp_len(text))
    runs = [StyledRun(text, _S_HELP_GROUP)]
    if pad:
        runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))
    return runs


def help_panel_rows(width: int) -> list:
    """帮助面板全量内容行（滚动窗口数据源）。

    按 ``trace_keymap`` 注册顺序分组输出：每组一个小节标题 + 若干
    ``键位  说明`` 行（键列按最长键位对齐）。

    Args:
        width: 右栏宽（键列预算/行宽预算）。

    Returns:
        ``list[list[StyledRun]]``；数据表缺席/为空 → 单条空态提示行。
    """
    width = max(1, int(width))
    from src.presentation_data import trace_keymap

    entries = trace_keymap()
    if not entries:
        return [[StyledRun("(快捷键速查表未注册)", _S_HINT)]]
    key_col = _KEY_COL_MIN
    for item in entries:
        if isinstance(item, dict):
            key_col = max(key_col, _disp_len(str(item.get("keys", ""))))
    key_col = min(key_col, max(_KEY_COL_MIN, width // 2))
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
            rows.append(_group_header(group, width))
            current_group = group
        pad = max(1, key_col - _disp_len(keys))
        runs = [
            StyledRun("  ", None),
            StyledRun(keys, _S_HELP_KEY),
            StyledRun(" " * pad, None),
            StyledRun(desc, _S_HELP_DESC),
        ]
        rows.append(runs)
    # 行宽截断 + 空 run 清理（行级 diff 宽度不变量）
    from src.tui.ink.helpers import truncate_runs
    out: list = []
    for row in rows:
        cleaned = [r for r in row if r.text]
        if not cleaned:
            cleaned = [StyledRun(" ", None)]
        out.append(truncate_runs(cleaned, width))
    return out
