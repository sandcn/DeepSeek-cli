"""插件依赖关系面板（plugin_relation）——``r`` 键依赖/被依赖交叉引用。

2026-10-07 第三批（用户需求：插件依赖关系视图 + 服务交叉引用）新增：
  - ``relation_rows``：选中插件 + 全局条目表 → 关系面板全量内容行
    （``list[list[StyledRun]]``，与详情/帮助面板同一滚动数据模型）+ 与行
    对齐的**跳转目标**列表（可跳转项 = 依赖/被依赖/服务提供方中已加载的
    插件名；Enter 跳到该插件）。

纯函数（样式取自本模块常量，与 plugin_view 视觉同源）；数据源 =
``plugins.view_model`` 条目（``depends`` / ``dependents`` / ``provides``）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import StyledRun
from src.tui.ink.helpers import truncate_runs

__all__ = ["relation_rows"]

#: 小节前缀（对齐插件/配置视图）
_SECTION_PREFIX = "\u25b8 "  # ▸
#: 关系项箭头
_ARROW = "\u2192 "  # →

_S_SECTION = Style(fg=110, bold=True)
_S_TEXT = Style(fg=252)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_ARROW = Style(fg=45)


def relation_rows(entry: dict, by_name: dict, width: int) -> tuple:
    """选中插件 → (关系面板行, 跳转目标列表)。

    小节：依赖（inject）→ 被依赖（谁依赖本插件）→ 提供服务。每项一行；
    已加载的目标插件在 Enter 时跳转（``targets`` 对齐非 None），未加载项
    标注 ``(未加载)``。

    Args:
        entry: 选中插件条目（None → 空态占位）。
        by_name: {插件名: 条目}（判断目标是否已加载——可跳转）。
        width: 面板宽（截断预算）。

    Returns:
        ``(rows, targets)``：rows 为 ``list[list[StyledRun]]``；targets 与
        rows 等长（可跳转项目标插件名 / None）。
    """
    width = max(1, int(width))
    if entry is None:
        return [[StyledRun("(\u65e0\u5df2\u52a0\u8f7d\u63d2\u4ef6)", _S_HINT)]], [None]
    name = str(entry.get("name", ""))
    rows: list = []
    targets: list = []

    def _push(runs, target):
        rows.append(truncate_runs(runs, width) if runs else [StyledRun(" ", None)])
        targets.append(target)

    _push([StyledRun(f"{_SECTION_PREFIX}{name} \u5173\u7cfb", _S_SECTION)], None)
    for title, items in (
        ("\u4f9d\u8d56\uff08inject\uff09", list(entry.get("depends") or [])),
        ("\u88ab\u4f9d\u8d56\uff08\u8c01\u4f9d\u8d56\u672c\u63d2\u4ef6\uff09",
         list(entry.get("dependents") or [])),
        ("\u63d0\u4f9b\u670d\u52a1", list(entry.get("provides") or [])),
    ):
        _push([StyledRun("\u2500" * max(1, width - 1), _S_SEP)], None)
        _push([StyledRun(f"{_SECTION_PREFIX}{title}\uff08{len(items)}\uff09", _S_SECTION)], None)
        if not items:
            _push([StyledRun("  (\u65e0)", _S_HINT)], None)
            continue
        for it in items:
            key = str(it)
            tgt = key if key in (by_name or {}) else None
            runs = [
                StyledRun("  " + _ARROW, _S_ARROW),
                StyledRun(key, _S_TEXT),
            ]
            if tgt is None:
                runs.append(StyledRun("  (\u672a\u52a0\u8f7d)", _S_HINT))
            _push(runs, tgt)
    return rows, targets
