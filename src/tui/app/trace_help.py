"""轨迹视图帮助面板（``?`` 开关）——快捷键速查内容构建。

2026-10-07（用户需求：轨迹 Trace 操作 / 更多功能）新增：视图内帮助面板
展示轨迹视图全部快捷键（分组 + 键列对齐 + 描述）。

数据源 = 表现层数据注册表 ``presentation_data.trace_keymap``（「一切皆
插件」——分组/键位/说明可经 Patch/Overlay 整表替换或禁用）；渲染委托
通用实现 ``_keymap_pane.keymap_panel_rows``（与插件/配置视图共享，样式
取本视图 ``trace_styles``）。
"""

from __future__ import annotations

from ._keymap_pane import keymap_panel_rows
from .trace_styles import (
    _S_HELP_DESC,
    _S_HELP_GROUP,
    _S_HELP_KEY,
    _S_SEP_ROW,
)

__all__ = ["help_panel_rows"]


def help_panel_rows(width: int) -> list:
    """帮助面板全量内容行（滚动窗口数据源）。

    按 ``trace_keymap`` 注册顺序分组输出：每组一个小节标题 + 若干
    ``键位  说明`` 行（键列按最长键位对齐）。

    Args:
        width: 右栏宽（键列预算/行宽预算）。

    Returns:
        ``list[list[StyledRun]]``；数据表缺席/为空 → 单条空态提示行。
    """
    from src.presentation_data import trace_keymap

    return keymap_panel_rows(
        trace_keymap(), width,
        key_style=_S_HELP_KEY,
        group_style=_S_HELP_GROUP,
        desc_style=_S_HELP_DESC,
        sep_style=_S_SEP_ROW,
        empty_text="(\u5feb\u6377\u952e\u901f\u67e5\u8868\u672a\u6ce8\u518c)",
    )
