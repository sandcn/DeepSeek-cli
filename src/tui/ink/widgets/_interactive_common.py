"""交互控件公共辅助 — 回调安全调用 / 颜色解析 / items 规范化 / 窗口钳制。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——交互
控件（SelectInput/TextInput/MultiSelect/ConfirmInput/Toggle）共享的纯辅助
独立成模块，控件实现分别拆至 ``_select_input`` / ``_text_input`` /
``_multi_select`` / ``_confirm_toggle``，``interactive.py`` 门面 re-export。

依赖方向：本模块 → element/output/core.style/_width/_screen；不反向依赖
控件模块。
"""

from __future__ import annotations

import logging

# ★ 公共纯辅助收敛（2026-08-05 架构优化）：``_clamp_index`` / ``_color`` /
#   ``_call`` 与多个控件模块重复的实现收敛至 ``_widget_common``（单一真源）；
#   本模块 re-export 保持 interactive 门面/测试 patch 路径兼容。
from ._widget_common import _clamp_index, _color, _call

_logger = logging.getLogger(__name__)


def _normalize_items(items) -> list[dict]:
    """将 items 规范化为 ``{"label": str, "value": Any}`` 列表。

    支持两种输入形态：
      - list of str（label == value）；
      - list of dict（含 "label" 键；缺省回退 "value"）。

    ★ 健壮性（渲染错误防御）：items 不可迭代（None/标量/对象）时回退空列表
      ——修复前 ``for item in items or []`` 对不可迭代的 items（如 bool/float）
      抛 TypeError，SelectInput/MultiSelect 渲染崩溃。
    """
    if items is None:
        return []
    if not hasattr(items, "__iter__"):
        # 不可迭代：回退空列表（渲染安全）
        return []
    if isinstance(items, (str, bytes)):
        # ★ P3（review）：str/bytes 是 Iterable（逐字符）——作为 items 会被
        #   逐字符拆列（意外语义，与 _table/_normalize_tree 守卫一致）：回退
        #   空列表（渲染安全）。
        return []
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label", item.get("value", "")))
            out.append({"label": label, "value": item.get("value", label)})
        else:
            out.append({"label": str(item), "value": item})
    return out


def _visible_window(selected: int, total: int, limit: int | None,
                    current_offset: int = 0) -> tuple[int, int]:
    """计算可见窗口 ``(offset, count)``——跟随光标滚动语义（limit 无/超界全量）。

    ★ 跟随光标滚动（2026-08-19，补全弹窗候选多时按 ↑↓ 高亮可移动到
    未显示行）：光标在当前窗口**内部**移动时窗口不动（高亮逐行移动），
    仅越过窗口边界时滚动——

      - ``selected < offset``（越过上边界，如 ↑ 回到窗口之上）→ 窗口贴顶
        （``offset = selected``，高亮在首行）；
      - ``selected >= offset + limit``（越过下边界，如 ↓ 到窗口之下）→
        窗口贴底（``offset = selected - limit + 1``，高亮在末行）；
      - 其余（窗口内）→ 窗口保持 ``current_offset`` 不变；
      - ``current_offset`` 先钳制到 ``[0, total - limit]``（items 动态缩小
        后旧 offset 越界防护）。

    修复前为无状态贴顶语义（``offset = min(selected, total - limit)``）——
    每按一次 ↑/↓ 窗口立即滚动一行、高亮钉在窗口首行（用户看到高亮
    不动、列表乱滚，无法感知「按上下移动到未显示的行」）。与 ListView
    的跟随光标滚动语义对齐。

    Args:
        selected: 光标/选中下标（调用方已钳制到 [0, total-1]）。
        total: 项总数。
        limit: 可见行数（None 时全量显示，无滚动）。
        current_offset: 当前窗口起始偏移（上一帧渲染窗口；默认 0 顶部）。

    Returns:
        ``(offset, count)``：窗口起始偏移与可见行数。
    """
    if limit is None or limit <= 0 or total <= limit:
        return 0, total
    max_offset = total - limit
    offset = max(0, min(current_offset, max_offset))
    if selected < offset:
        return max(0, min(selected, max_offset)), limit
    if selected >= offset + limit:
        return max(0, min(selected - limit + 1, max_offset)), limit
    return offset, limit


def _hashable(value):
    """将 value 归一化为可哈希对象（不可哈希值兜底为稳定字符串键，E9）。

    ``hash(value)`` 成功返回原值（int/str/tuple 等）；不可哈希（dict/list 等）
    回退 ``f"<unhashable:{value!r}>"`` 带前缀字符串键——避免与可哈希的同
    repr 字符串（如 ``"[1, 2]"`` 字面量）碰撞，保证 MultiSelect 选中集合
    成员判断不崩溃且不互相污染。

    ⚠️ 已知限制：自定义 ``__hash__`` 抛非 TypeError 异常（ValueError 等）
    时仍会传播（Python 约定 hash 只抛 TypeError 表示不可哈希；极少数自定义
    对象违反该约定时按崩溃传播处理）。onSubmit 的 ordered 输出仍按 items
    **原始 value** 收集（不归一化），本函数仅用于选中状态的**集合成员判断**。
    """
    try:
        hash(value)
        return value
    except TypeError:
        return f"<unhashable:{value!r}>"


__all__ = [
    "_call",
    "_color",
    "_normalize_items",
    "_visible_window",
    "_clamp_index",
    "_hashable",
]
