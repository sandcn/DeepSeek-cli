"""检查器面板通用逻辑测试（P0-1）。

覆盖 ``src/tui/app/_inspector_pane.py``：vim 视口滚动、渲染期归一化、
状态规约（getter/setter 注入）、通用导航键分派。三视图
（trace_view / trace_tools_view / plugin_view）共享本模块，此前的三份
重复实现已删除。
"""

from __future__ import annotations

from src.tui.app._inspector_pane import (
    PaneState,
    handle_nav,
    resolve,
    scroll_for_cursor,
)


class _Event:
    """最小事件桩（只读 kind / char）。"""

    def __init__(self, kind: str, char: str = "") -> None:
        self.kind = kind
        self.char = char


# ═══════════════════════════════════════════════════════════
# scroll_for_cursor
# ═══════════════════════════════════════════════════════════


def test_scroll_for_cursor_content_fits_returns_zero():
    """内容不足一屏 → 滚动恒 0（无论光标/滚动值）。"""
    assert scroll_for_cursor(0, 0, 5, 10) == 0
    assert scroll_for_cursor(4, 3, 5, 10) == 0
    assert scroll_for_cursor(0, 0, 0, 10) == 0


def test_scroll_for_cursor_keeps_window_when_cursor_inside():
    """光标在窗口内 → 不滚动（scroll 保持）。"""
    assert scroll_for_cursor(5, 3, 100, 10) == 3
    assert scroll_for_cursor(12, 3, 100, 10) == 3  # 12 == scroll+viewport-1


def test_scroll_for_cursor_follows_upper_boundary():
    """光标越过窗口上边界 → scroll = cursor。"""
    assert scroll_for_cursor(1, 5, 100, 10) == 1
    assert scroll_for_cursor(0, 5, 100, 10) == 0


def test_scroll_for_cursor_follows_lower_boundary():
    """光标越过窗口下边界 → scroll = cursor - viewport + 1。"""
    assert scroll_for_cursor(13, 3, 100, 10) == 4
    assert scroll_for_cursor(99, 3, 100, 10) == 90  # cursor 钳到 99 → 99-10+1


def test_scroll_for_cursor_clamps_inputs():
    """scroll 越界钳制；viewport<=0 视为 1；负 cursor 钳 0。"""
    assert scroll_for_cursor(0, 999, 100, 10) == 0
    assert scroll_for_cursor(0, -5, 100, 10) == 0
    assert scroll_for_cursor(0, 0, 3, 0) == 0  # viewport<=0 → 1，内容 3>1
    assert scroll_for_cursor(-1, 5, 100, 10) == 0


# ═══════════════════════════════════════════════════════════
# resolve
# ═══════════════════════════════════════════════════════════


def test_resolve_empty_content():
    """空内容 → (0, 0)。"""
    assert resolve(5, 5, 0, 10) == (0, 0)
    assert resolve(-3, 7, 0, 10) == (0, 0)


def test_resolve_clamps_cursor_and_scroll():
    """cursor/scroll 越界钳制（光标在窗口内时 scroll 钳到上界）。"""
    assert resolve(999, 0, 50, 10) == (49, 40)
    assert resolve(45, 999, 50, 10) == (45, 40)
    # 光标在 scroll 之前 → 光标可见跟随回 0（scroll 随之归 0）
    assert resolve(0, 999, 50, 10) == (0, 0)
    assert resolve(-1, -1, 50, 10) == (0, 0)


def test_resolve_content_fits_scroll_zero_but_cursor_clamped():
    """内容不足一屏：scroll=0，cursor 仍钳到 [0, total-1]。"""
    assert resolve(9, 3, 5, 10) == (4, 0)
    assert resolve(0, 0, 5, 10) == (0, 0)


def test_resolve_follows_cursor():
    """光标越界 → scroll 跟随保持可见。"""
    assert resolve(1, 5, 100, 10) == (1, 1)
    assert resolve(20, 0, 100, 10) == (20, 11)


# ═══════════════════════════════════════════════════════════
# PaneState
# ═══════════════════════════════════════════════════════════


class _Host:
    """状态宿主桩（模拟 model / 子状态对象字段）。"""

    def __init__(self, cursor: int = 0, scroll: int = 0) -> None:
        self.cursor = cursor
        self.scroll = scroll


def _make_state(host: _Host) -> PaneState:
    return PaneState(
        lambda: host.cursor,
        lambda v: setattr(host, "cursor", v),
        lambda: host.scroll,
        lambda v: setattr(host, "scroll", v),
    )


def test_pane_state_reads_injected_fields():
    """PaneState 经 getter 读取宿主字段。"""
    host = _Host(3, 7)
    state = _make_state(host)
    assert state.cursor() == 3
    assert state.scroll() == 7


def test_pane_state_defends_non_numeric():
    """非数值字段回退 0（防御）。"""
    host = _Host(cursor="bad", scroll=None)  # type: ignore[arg-type]
    state = _make_state(host)
    assert state.cursor() == 0
    assert state.scroll() == 0


def test_pane_state_move_cursor_writes_back_and_follows():
    """move_cursor 写回 cursor + scroll 跟随。"""
    host = _Host(0, 0)
    state = _make_state(host)
    state.move_cursor(20, total=100, viewport=10)
    assert host.cursor == 20
    assert host.scroll == 11


def test_pane_state_move_cursor_clamps_and_empty():
    """move_cursor 越界钳制；空内容 → cursor=0、scroll=0。"""
    host = _Host(5, 5)
    state = _make_state(host)
    state.move_cursor(999, total=100, viewport=10)
    assert host.cursor == 99
    state.move_cursor(3, total=0, viewport=10)
    assert host.cursor == 0
    assert host.scroll == 0


# ═══════════════════════════════════════════════════════════
# handle_nav
# ═══════════════════════════════════════════════════════════


def test_handle_nav_moves_down_up():
    """↓/j/J 下移、↑/k/K 上移（消费）。"""
    for kind, char in (("arrow_down", ""), ("char", "j"), ("char", "J")):
        host = _Host(5, 0)
        state = _make_state(host)
        assert handle_nav(_Event(kind, char), state, 100, 10) is True
        assert host.cursor == 6
    for kind, char in (("arrow_up", ""), ("char", "k"), ("char", "K")):
        host = _Host(5, 0)
        state = _make_state(host)
        assert handle_nav(_Event(kind, char), state, 100, 10) is True
        assert host.cursor == 4


def test_handle_nav_page_up_down():
    """PgDn/PgUp 按 page（缺省 viewport）步进。"""
    host = _Host(20, 15)
    state = _make_state(host)
    assert handle_nav(_Event("page_down"), state, 100, 10) is True
    assert host.cursor == 30
    assert handle_nav(_Event("page_up"), state, 100, 10) is True
    assert host.cursor == 20


def test_handle_nav_page_custom_step():
    """page 参数显式覆盖步长。"""
    host = _Host(0, 0)
    state = _make_state(host)
    handle_nav(_Event("page_down"), state, 100, 10, page=3)
    assert host.cursor == 3


def test_handle_nav_home_end_and_g_G():
    """Home/g → 0；End/G → total（钳到 total-1）。"""
    host = _Host(50, 40)
    state = _make_state(host)
    assert handle_nav(_Event("home"), state, 100, 10) is True
    assert host.cursor == 0
    assert handle_nav(_Event("char", "G"), state, 100, 10) is True
    assert host.cursor == 99
    assert handle_nav(_Event("char", "g"), state, 100, 10) is True
    assert host.cursor == 0


def test_handle_nav_ignores_non_navigation():
    """非导航键返回 False（不消费、不改状态）。"""
    host = _Host(5, 2)
    state = _make_state(host)
    for ev in (_Event("char", "x"), _Event("escape"), _Event("enter"),
               _Event("char", "/"), _Event("char", "h"), _Event("backspace")):
        assert handle_nav(ev, state, 100, 10) is False
    assert host.cursor == 5
    assert host.scroll == 2


def test_handle_nav_empty_content_safe():
    """空内容下导航安全（cursor/scroll 保持 0）。"""
    host = _Host(0, 0)
    state = _make_state(host)
    assert handle_nav(_Event("arrow_down"), state, 0, 10) is True
    assert host.cursor == 0
    assert host.scroll == 0
