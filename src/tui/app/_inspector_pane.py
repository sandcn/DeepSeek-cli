"""检查器（右栏）面板通用逻辑 — 光标 / 滚动 / vim 导航（P0-1）。

背景：``trace_view``（轨迹检查器）、``trace_tools_view``（工具列表详情）、
``plugin_view``（插件详情）三个双栏视图的右栏共享同一套「vim 视口」语义
——光标位置 + 滚动跟随（光标越界才滚动）+ ↑↓/j/k/PgUp/PgDn/Home/End/g/G
导航。此前三处各自复刻了 ``_scroll_for_cursor`` / ``_move_cursor`` / 键分派
（逻辑逐行等价、字段名不同），本模块收敛为 **纯函数 + 状态读写规约**。

各视图状态字段/宿主对象不同（``model.trace_inspector_cursor`` /
``model.trace_tools_cursor`` / ``plugin_view.cursor``），故经 ``PaneState``
的 getter/setter 注入解耦——模块不感知具体宿主。

职责边界：本模块只处理**通用的光标/滚动/导航**；视图特有逻辑（面板切换
h/l、树折叠空格、搜索 n/N/p、关闭键等）仍由各视图保留。
"""

from __future__ import annotations

from typing import Callable

__all__ = ["scroll_for_cursor", "resolve", "PaneState", "handle_nav"]


def scroll_for_cursor(cursor: int, scroll: int, total: int, viewport: int) -> int:
    """计算视口滚动位置：钳制 + 跟随光标保持可见（vim 视口语义）。

    光标已在窗口 ``[scroll, scroll+viewport)`` 内 → 不滚动；光标越过上/下
    边界 → 滚动窗口使光标回到边缘可见；内容不足一屏 → 0。

    Args:
        cursor: 光标行（0-based；内部钳制到 ``[0, total-1]``）。
        scroll: 当前滚动偏移（0-based）。
        total: 内容总行数。
        viewport: 可见行数（<=0 视为 1）。

    Returns:
        新的滚动偏移（0-based）。
    """
    viewport = max(1, int(viewport))
    if total <= viewport:
        return 0
    scroll = max(0, min(int(scroll), total - viewport))
    cursor = max(0, min(int(cursor), total - 1))
    if cursor < scroll:
        return cursor
    if cursor >= scroll + viewport:
        return cursor - viewport + 1
    return scroll


def resolve(cursor: int, scroll: int, total: int, viewport: int) -> tuple[int, int]:
    """渲染期归一化 ``(cursor, scroll)``：越界钳制 + 光标可见跟随。

    与 ``scroll_for_cursor`` 的差别：本函数**同时钳制 cursor**（用于渲染期
    把越界的残留状态收敛回合法范围），返回归一化后的二元组供调用方写回。

    Args:
        cursor: 原始光标行。
        scroll: 原始滚动偏移。
        total: 内容总行数（<=0 返回 ``(0, 0)``）。
        viewport: 可见行数。

    Returns:
        ``(cursor, scroll)``（均为钳制后的合法值）。
    """
    if total <= 0:
        return 0, 0
    viewport = max(1, int(viewport))
    cursor = max(0, min(int(cursor), total - 1))
    if total <= viewport:
        return cursor, 0
    scroll = max(0, min(int(scroll), total - viewport))
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + viewport:
        scroll = cursor - viewport + 1
    return cursor, scroll


class PaneState:
    """检查器面板状态读写规约（getter/setter 注入，解耦具体宿主字段）。

    各视图状态位置不同（model 属性 / 子状态对象属性），构造时注入四个访问
    器即可复用通用逻辑。所有取值经 ``int()`` 防御（非数值回退 0）。
    """

    __slots__ = ("_get_cursor", "_set_cursor", "_get_scroll", "_set_scroll")

    def __init__(
        self,
        get_cursor: Callable[[], int],
        set_cursor: Callable[[int], None],
        get_scroll: Callable[[], int],
        set_scroll: Callable[[int], None],
    ) -> None:
        self._get_cursor = get_cursor
        self._set_cursor = set_cursor
        self._get_scroll = get_scroll
        self._set_scroll = set_scroll

    def cursor(self) -> int:
        """当前光标行（0-based；非数值回退 0）。"""
        try:
            return int(self._get_cursor())
        except (TypeError, ValueError):
            return 0

    def scroll(self) -> int:
        """当前滚动偏移（0-based；非数值回退 0）。"""
        try:
            return int(self._get_scroll())
        except (TypeError, ValueError):
            return 0

    def move_cursor(self, new_cursor: int, total: int, viewport: int) -> None:
        """移动光标：写回 cursor + scroll 跟随（保持光标可见）。"""
        if total > 0:
            new_cursor = max(0, min(int(new_cursor), total - 1))
        else:
            new_cursor = 0
        self._set_cursor(new_cursor)
        self._set_scroll(
            scroll_for_cursor(new_cursor, self.scroll(), total, viewport)
        )


def handle_nav(
    event,
    pane: PaneState,
    total: int,
    viewport: int,
    *,
    page: int | None = None,
) -> bool:
    """通用 vim 导航键处理（检查器焦点）。

    键映射（三视图一致）：``↓/j/J`` 下移、``↑/k/K`` 上移、``page_down/up``
    翻页、``home/g`` 首行、``end/G`` 末行。

    Args:
        event: 输入事件（读 ``kind`` / ``char``）。
        pane: 检查器面板状态规约。
        total: 内容总行数。
        viewport: 可见行数。
        page: 翻页步长（缺省 = ``viewport``；调用方可传精确视口预算）。

    Returns:
        True — 事件已消费；False — 非导航键（调用方继续处理）。
    """
    step = max(1, int(page)) if page else max(1, int(viewport))
    kind = getattr(event, "kind", "")
    ch = getattr(event, "char", "") or ""
    is_char = kind == "char"
    cursor = pane.cursor()
    if kind == "arrow_down" or (is_char and ch in ("j", "J")):
        pane.move_cursor(cursor + 1, total, viewport)
        return True
    if kind == "arrow_up" or (is_char and ch in ("k", "K")):
        pane.move_cursor(cursor - 1, total, viewport)
        return True
    if kind == "page_down":
        pane.move_cursor(cursor + step, total, viewport)
        return True
    if kind == "page_up":
        pane.move_cursor(cursor - step, total, viewport)
        return True
    if kind == "home" or (is_char and ch == "g"):
        pane.move_cursor(0, total, viewport)
        return True
    if kind == "end" or (is_char and ch == "G"):
        pane.move_cursor(total, total, viewport)
        return True
    return False
