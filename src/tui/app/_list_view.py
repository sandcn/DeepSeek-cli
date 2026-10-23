"""列表型视图的共享上下文 — 收敛 16 个视图重复的「状态推导 + 分栏 + 视口」样板。

背景（2026-10 架构评审）：17 个全屏视图各自实现了同一套样板：

  1. ``visible = state is not None and state.visible and not state.done``
  2. 读 ``entries`` / 搜索模式 / 过滤 / 匹配集，算 ``index_map`` 与 ``items``
  3. 把 ``selected`` 夹紧回写（``sel != state.selected`` → 写回）
  4. ``split_panes`` + ``viewport_rows`` + 「搜索行/状态行占高」预算
  5. ``use_memo(detail_rows, 键)`` → ``resolve(cursor, scroll, total, vh)`` 回写
  6. ``PaneState``（cursor/scroll 读写器）与 ``_row_of`` / ``_select_row``

本模块把这些**纯推导**抽成 ``use_list_view``（唯一 use_memo 调用点，hook 顺序
恒定）。视图只保留「右栏内容长什么样」「键位特有动作」「左栏怎么画」。

迁移约定（重要）：
  - 本 hook 内部**不消费按键**、不写业务状态（除 selected/cursor/scroll 的
    夹紧回写，与历史行为一致）；
  - ``search_text`` / ``detail_rows`` 必须是纯函数（每次渲染可能调用一次）；
  - 未迁移的视图继续用原实现，两者并存不冲突（无全局状态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.tui.ink.hooks import use_memo

from ._inspector_pane import PaneState, resolve
from ._view_common import clamp_index, split_panes, viewport_rows

__all__ = ["ListViewContext", "use_list_view"]


@dataclass
class ListViewContext:
    """列表视图的一次渲染快照（每次渲染重建，无跨帧状态）。"""

    state: Any
    visible: bool
    entries: list
    items: list
    index_map: list
    total: int
    selected: int
    entry: Any
    pane: str
    help_open: bool
    search_mode: bool
    pattern: str
    filter_on: bool
    status_message: str
    left_w: int
    right_w: int
    viewport: int
    content_rows: list
    cursor: int
    scroll: int

    def pane_state(self) -> PaneState:
        """构造本视图的 cursor/scroll 读写规约（``handle_nav`` 用）。"""
        state = self.state
        return PaneState(
            lambda: getattr(state, "cursor", 0) or 0,
            lambda value: setattr(state, "cursor", value),
            lambda: getattr(state, "scroll", 0) or 0,
            lambda value: setattr(state, "scroll", value),
        )

    def row_of(self, original: int) -> int:
        """原始 entries 索引 → 过滤后行号（未命中返回 0）。"""
        try:
            return self.index_map.index(original)
        except ValueError:
            return 0

    def select_row(self, row: int) -> None:
        """选中过滤后的第 ``row`` 行并复位详情光标。"""
        if self.state is None:
            return
        self.state.selected = row
        self.state.cursor = 0
        self.state.scroll = 0


def use_list_view(
    model: Any,
    state_attr: str,
    *,
    width: int,
    search_text: Callable[[Any], str],
    detail_rows: Callable[[Any, int], list],
    help_rows: Callable[[int], list],
    fallback: tuple[int, int] = (36, 46),
    extra_rows: int = 0,
) -> ListViewContext:
    """推导列表视图上下文（状态 / 分栏 / 视口 / 右栏内容与滚动定位）。

    Args:
        model: AppModel（只读 ``state_attr``）。
        state_attr: 状态属性名（如 ``"notify_view"``）。
        width: 组件 props 的终端宽度（分栏基准）。
        search_text: ``entry -> 可搜索文本``（``/`` 搜索匹配用）。
        detail_rows: ``(entry, right_w) -> list[Line]``（右栏内容；帮助打开时
            不调用）。
        help_rows: ``right_w -> list[Line]``（帮助面板内容）。
        fallback: 左右栏兜底宽度（窄屏/极窄屏时使用）。
        extra_rows: 除搜索行/状态行外的额外占用行（如 tab 栏）。

    Returns:
        :class:`ListViewContext`。
    """
    state = getattr(model, state_attr, None)
    visible = bool(state is not None and state.visible and not state.done)
    entries = list(getattr(state, "entries", None) or []) if state is not None else []
    help_open = bool(getattr(state, "help_open", False)) if state is not None else False
    search_mode = bool(getattr(state, "search_mode", False)) if state is not None else False
    pattern = (getattr(state, "search_pattern", "") or "") if state is not None else ""
    filter_on = bool(getattr(state, "search_filter", False)) if state is not None else False
    matches = list(getattr(state, "search_matches", None) or []) if state is not None else []
    status_message = (getattr(state, "status_message", "") or "") if state is not None else ""

    allowed = set(matches) if (filter_on and pattern and matches) else None
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)

    selected = 0
    if total:
        selected = clamp_index(getattr(state, "selected", 0), total)
        if state is not None and selected != getattr(state, "selected", None):
            state.selected = selected
    entry = items[selected] if total else None

    pane = getattr(state, "pane", "list") if state is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(max(0, int(width)), fallback=fallback)
    reserved = (1 if search_mode else 0) + (1 if status_message else 0) + extra_rows
    viewport = max(4, viewport_rows() - reserved)

    def _content_rows() -> list:
        if help_open:
            return help_rows(right_w)
        return detail_rows(entry, right_w)

    content_rows = use_memo(
        _content_rows,
        (help_open, right_w, _entry_key(entry),
         getattr(state, "refresh_seq", 0) or 0,
         getattr(state, "applied_seq", 0) or 0),
    )
    cursor, scroll = resolve(
        getattr(state, "cursor", 0) or 0,
        getattr(state, "scroll", 0) or 0,
        len(content_rows),
        max(1, viewport),
    )
    if state is not None:
        state.cursor = cursor
        state.scroll = scroll

    return ListViewContext(
        state=state,
        visible=visible,
        entries=entries,
        items=items,
        index_map=index_map,
        total=total,
        selected=selected,
        entry=entry,
        pane=pane,
        help_open=help_open,
        search_mode=search_mode,
        pattern=pattern,
        filter_on=filter_on,
        status_message=status_message,
        left_w=left_w,
        right_w=right_w,
        viewport=viewport,
        content_rows=content_rows,
        cursor=cursor,
        scroll=scroll,
    )


def _entry_key(entry: Any) -> Any:
    """右栏 memo 键：优先用条目身份（dict/list 不可哈希时退回 id）。"""
    if entry is None:
        return None
    try:
        hash(entry)
    except TypeError:
        return id(entry)
    return entry
