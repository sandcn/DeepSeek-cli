"""光标显隐测试 — 轨迹 Trace 全屏模式隐藏终端光标（2026-08-19 用户需求）。

背景：``model.trace_open`` 时 App 整屏渲染 TraceView（消息区/状态栏/输入区
全部不渲染）——``_position_cursor`` 找不到 input fiber，光标停留在残留位置
闪烁。修复：无 input fiber → 隐藏光标（DECTCEM ``\\033[?25l``）；正常模式
（找到 input fiber）→ 显示光标（``\\033[?25h``）并定位。显隐经
``InkRenderer.set_cursor_visible`` 状态跟踪（仅变化时写转义序列，不变帧零
输出——每帧调用开销可忽略）。

覆盖：
  1. ``_screen.cursor_hide/cursor_show`` 返回正确 DECTCEM 序列；
  2. ``InkRenderer.set_cursor_visible`` 状态跟踪（首次输出、重复同值零输出、
     切换输出）；
  3. ``_position_cursor`` 无 input fiber → 隐藏光标；
  4. ``_position_cursor`` 有 input fiber → 显示光标并定位；
  5. 端到端：trace_open 切换（渲染循环）→ 隐藏/显示状态正确。
"""

from __future__ import annotations

import io

from src.tui._screen import cursor_hide, cursor_show
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


# ── 1. DECTCEM 序列 ───────────────────────────────────

def test_cursor_hide_sequence():
    """cursor_hide 返回 DECTCEM 隐藏序列（\\033[?25l）。"""
    assert cursor_hide() == "\033[?25l"


def test_cursor_show_sequence():
    """cursor_show 返回 DECTCEM 显示序列（\\033[?25h）。"""
    assert cursor_show() == "\033[?25h"


# ── 2. InkRenderer.set_cursor_visible 状态跟踪 ─────────

def test_set_cursor_visible_initial_hide_writes_once():
    """初始（未知态）首次 set(False) 输出隐藏序列；重复同值零输出。"""
    stream = io.StringIO()
    r = InkRenderer(stream=stream)
    assert r._cursor_visible is None
    r.set_cursor_visible(False)
    assert stream.getvalue() == "\033[?25l"
    assert r._cursor_visible is False
    # 重复同值：零输出（状态跟踪）
    r.set_cursor_visible(False)
    assert stream.getvalue() == "\033[?25l"


def test_set_cursor_visible_switch_writes_show():
    """隐藏后切换显示 → 输出显示序列；重复同值零输出。"""
    stream = io.StringIO()
    r = InkRenderer(stream=stream)
    r.set_cursor_visible(False)
    r.set_cursor_visible(True)
    assert stream.getvalue() == "\033[?25l\033[?25h"
    assert r._cursor_visible is True
    r.set_cursor_visible(True)
    assert stream.getvalue() == "\033[?25l\033[?25h"


def test_set_cursor_visible_does_not_affect_frame_render():
    """set_cursor_visible 不影响帧渲染输出（光标显隐独立于内容 diff）。"""
    stream = io.StringIO()
    r = InkRenderer(stream=stream)
    r.render(Frame([Line.of("l0"), Line.of("l1")]))
    stream.seek(0)
    stream.truncate(0)
    r.set_cursor_visible(True)
    # 帧内容输出不受显隐调用影响（仅追加显隐序列）
    assert "\033[?25h" in stream.getvalue()


# ── 3/4. _position_cursor 显隐决策 ────────────────────

def _make_session(model=None, build_tree=None):
    """构造最小 InkSession（test_trace_view._make_session 同风格）。"""
    from src.tui._config import TuiConfig
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.apply import apply_cmd
    from src.tui.app.model import AppModel
    from src.tui.ink.session import InkSession
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    if model is None:
        model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_tree,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    session.set_line_tracker(None)
    return session, stream


def test_position_cursor_hides_when_no_input_fiber():
    """无 input fiber（全屏 TraceView/无输入区树）→ 隐藏终端光标。"""
    from src.tui.app.model import AppModel
    from src.tui.ink.element import TEXT, h
    from src.tui.ink.reconciler import Reconciler
    session, stream = _make_session(
        model=AppModel(),
        build_tree=lambda m, w: h(TEXT, {"children": "x"}),
    )
    session._reconciler.render(session._root_fiber, h(TEXT, {"children": "x"}), 80, 24)
    session._position_cursor()
    assert "\033[?25l" in stream.getvalue(), "无 input fiber 应隐藏光标"
    assert session._ink_renderer._cursor_visible is False


def test_position_cursor_shows_and_positions_when_input_fiber():
    """有 input fiber（正常聊天模式）→ 显示光标并定位到输入区。"""
    from src.tui.app.app import build_app_element
    from src.tui.app.model import AppModel
    session, stream = _make_session(
        model=AppModel(),
        build_tree=build_app_element,
    )
    # 完整渲染（正常模式：含 InputArea dataInputArea）→ _input_fiber 缓存
    session._render_frame()
    assert session._input_fiber is not None, "正常模式应找到 input fiber"
    # 首帧渲染即已显示光标（_render_frame → _position_cursor → set_cursor_visible）
    assert session._ink_renderer._cursor_visible is True
    assert "\033[?25h" in stream.getvalue(), "有 input fiber 应显示光标"
    # 再次定位：状态已显示 → 零显隐输出（仅光标移动序列）——状态跟踪防每帧重复写
    before = len(stream.getvalue())
    session._position_cursor()
    out = stream.getvalue()[before:]
    assert "\033[?25h" not in out, "光标已显示时不应重复输出显隐序列"


def test_position_cursor_hides_after_trace_open_renders():
    """端到端：trace_open 后渲染一帧 → 光标隐藏（_position_cursor 决策）。"""
    from src.tui.app.app import build_app_element
    from src.tui.app.model import AppModel
    from src.renderer.ansi.helpers import AnsiLine

    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> hi")])
    session, stream = _make_session(model=m, build_tree=build_app_element)
    # 正常模式首帧：光标显示
    session._render_frame()
    assert session._ink_renderer._cursor_visible is True
    # 打开轨迹视图：整屏 TraceView（无 input-area）→ 渲染后光标隐藏
    m.trace_open = True
    session._render_frame()
    assert session._ink_renderer._cursor_visible is False, "trace_open 应隐藏光标"
    # 关闭轨迹视图：恢复聊天界面 → 光标显示
    m.trace_open = False
    session._render_frame()
    assert session._ink_renderer._cursor_visible is True, "关闭 trace 应恢复光标显示"
