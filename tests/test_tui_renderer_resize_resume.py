"""TUI 渲染缺陷回归测试（2026-10-20）。

覆盖本轮修复：
  1. resize 全量重写前清屏（``InkRenderer.reset(full=True)`` → ``_write_full``）；
  2. 首帧不清屏（不得清掉用户终端里已有的历史输出）；
  3. ``suspend``/``resume`` 保持帧与光标记账，不再整篇重复写入；
  4. ``goto_bottom`` 同步 ``_cursor_row``（suspend 后物理光标位置一致）；
  5. resize 帧禁用空闲帧复用（布局必须跟随窗口尺寸重建）；
  6. Windows 光标行查询（Win32 实现不抛异常、返回值合法）。
"""

from __future__ import annotations

import io
import threading
import time
from types import SimpleNamespace

from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


class _RecordingStream(io.StringIO):
    """记录写入内容的输出流（InkRenderer 只依赖 write/flush）。"""

    def flush(self) -> None:  # StringIO.flush 已是 no-op，显式保留语义
        super().flush()


def _frame(tag: str, n: int) -> Frame:
    return Frame([Line.of(f"{tag}-{i:02d}") for i in range(n)])


# ═══════════════════════════════════════════════════════════
# 1/2. resize 清屏 + 首帧不清屏
# ═══════════════════════════════════════════════════════════

class TestResizeClear:
    def test_first_frame_does_not_clear_screen(self):
        """首帧不得清屏——否则会抹掉用户终端里已有的历史输出。"""
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=24)
        r.set_width(80)
        r.render(_frame("A", 20))
        assert "\x1b[2J" not in stream.getvalue()

    def test_resize_full_rewrite_clears_screen(self):
        """resize（``reset(full=True)``）后的全量写入必须先清屏。

        修复前只从新文档起始行逐行覆盖：旧帧（旧宽度，终端 reflow 后占更多
        物理行）在屏幕上方残留，resize 后屏幕出现两份 UI。
        """
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=24)
        r.set_width(80)
        r.render(_frame("A", 20))
        stream.seek(0)
        stream.truncate(0)

        r.set_width(120)
        r.set_height(30)
        r.reset(full=True)
        r.render(_frame("B", 20))

        out = stream.getvalue()
        assert out.startswith("\x1b[2J\x1b[H"), (
            "resize 全量重写应以 clear_screen（ED 2 + 归位）开头，实际: "
            f"{out[:40]!r}"
        )
        # 清屏后仍必须定位到新文档起始行（120x30 屏、20 行文档 → 第 11 行）
        assert "\x1b[11;1H" in out

    def test_plain_reset_does_not_clear_screen(self):
        """软重置（``reset(full=False)``，Ctrl+L 等）沿用 full_clear 语义，不清屏。"""
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=24)
        r.set_width(80)
        r.render(_frame("A", 10))
        stream.seek(0)
        stream.truncate(0)
        r.reset(full=False)
        r.render(_frame("A", 10))
        assert "\x1b[2J" not in stream.getvalue()


# ═══════════════════════════════════════════════════════════
# 3/4. suspend / resume 保持布局记账
# ═══════════════════════════════════════════════════════════

class TestSuspendResume:
    def test_goto_bottom_updates_cursor_row(self):
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=34)
        r.set_width(80)
        r.render(_frame("A", 20))
        r.goto_bottom(34)
        assert r.cursor_row == 34, "goto_bottom 后光标记账必须跟随物理位置"

    def test_suspend_keeps_previous_frame(self):
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=34)
        r.set_width(80)
        first = _frame("A", 20)
        r.render(first)
        before_buf = r._buf_h

        r.goto_bottom(34)
        r.suspend()

        assert r.prev_frame is first, "suspend 不得丢弃上一帧（否则 resume 整篇重写）"
        assert r.cursor_row == 34, "suspend 不得把光标记账重置为 1"
        assert r._buf_h == before_buf, "suspend 不得重置物理缓冲记账"

    def test_resume_same_frame_is_noop(self):
        """suspend → 同帧 render：不得产生任何输出（不重复写入整篇文档）。"""
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=34)
        r.set_width(80)
        frame = _frame("A", 20)
        r.render(frame)

        r.goto_bottom(34)
        r.suspend()
        stream.seek(0)
        stream.truncate(0)

        r.render(frame)
        assert stream.getvalue() == "", (
            "resume 后同一帧应零输出，实际: " + repr(stream.getvalue()[:120])
        )

    def test_resume_changed_line_rewrites_only_that_line(self):
        """resume 后仅一行变化：只重写该行，不写 \\n（不滚动/不追加）。"""
        stream = _RecordingStream()
        r = InkRenderer(stream=stream, height=34)
        r.set_width(80)
        docs = [f"UI-{i:02d}" for i in range(20)]
        r.render(Frame([Line.of(t) for t in docs]))

        r.goto_bottom(34)
        r.suspend()
        stream.seek(0)
        stream.truncate(0)

        docs[10] = "CHANGED"
        r.render(Frame([Line.of(t) for t in docs]))
        out = stream.getvalue()
        assert "CHANGED" in out
        assert "\n" not in out, "增量重写不得写换行（会触发滚动 → 旧帧残留）"
        assert out.count("\r") >= 1


# ═══════════════════════════════════════════════════════════
# 5. resize 帧禁用空闲帧复用
# ═══════════════════════════════════════════════════════════

class _StubRenderer:
    def __init__(self) -> None:
        self.reset_calls: list[bool] = []
        self.rendered: list[Frame] = []
        self.width = 0
        self.height = 0

    def reset(self, full: bool = False) -> None:
        self.reset_calls.append(bool(full))

    def set_width(self, width: int) -> None:
        self.width = int(width)

    def set_height(self, height: int) -> None:
        self.height = int(height)

    def set_content_line_count(self, count: int, *, start=None, resync: bool = False) -> None:
        pass

    def render(self, frame: Frame) -> None:
        self.rendered.append(frame)

    def set_cursor_visible(self, visible: bool) -> None:
        pass

    def place_cursor(self, row: int, col: int) -> None:
        pass


class _StubCache:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def poll(self, max_age: float) -> bool:
        return False


class _StubReconciler:
    def render(self, *args, **kwargs) -> None:
        pass


def _make_session(width: int, height: int, monkeypatch, idle: bool = True):
    """构造最小可用的 _SessionFrameMixin 实例（无真实组件树）。"""
    from src.tui.ink import _session_frame_mixin as mixin_mod
    from src.tui.ink import components as components_mod

    built: list[int] = []
    new_frame = Frame([Line.of("new")])
    old_frame = Frame([Line.of("old")])

    monkeypatch.setattr(mixin_mod, "advance_animation", lambda ctx=None: None)
    monkeypatch.setattr(mixin_mod, "has_active_animations", lambda ctx=None: False)
    monkeypatch.setattr(
        mixin_mod._hooks, "_notify_window_size", lambda ctx=None: None,
    )
    monkeypatch.setattr(
        components_mod, "render_frame", lambda root, w: new_frame,
    )
    monkeypatch.setattr(
        components_mod, "_find_committed_chat", lambda root: None,
    )

    session = _SessionFrameMixin()
    session._build_tree = lambda model, w: built.append(int(w)) or SimpleNamespace()
    session._model = SimpleNamespace(width=width)
    session._apply_fn = None
    session._reconciler = _StubReconciler()
    session._root_fiber = None
    session._ink_renderer = _StubRenderer()
    session._width_cache = _StubCache(width, height)
    session._config = SimpleNamespace()
    session._input_fiber = SimpleNamespace(
        deleted=False, type="input-area", props={}, layout_box=None,
    )
    session._last_render_width = width
    session._last_render_height = height
    session._last_size_poll = time.monotonic()
    session._resize_pending = False
    session._dirty = False
    session._idle_ok = idle
    session._system_monitor = None
    session._last_sys_stats_time = time.monotonic()
    session._sys_stats_interval = 1000.0
    session._hook_ctx = None
    session._on_render_callback = None
    session._cmd_queue = None
    session._last_frame = old_frame
    session._last_frame_lines = 1
    session._idle_frame_predicate = lambda: True
    session._bottom_redraw_requested = threading.Event()
    session._render_lock = None
    session._frame_active = False
    return session, built, old_frame, new_frame


class TestResizeIdleReuse:
    def test_resize_frame_rebuilds_tree(self, monkeypatch):
        """resize 帧即便处于空闲态也必须重建（不得复用旧尺寸帧）。"""
        session, built, old_frame, new_frame = _make_session(80, 24, monkeypatch)
        # 模拟终端尺寸变化：缓存返回新尺寸
        session._width_cache.width = 120
        session._width_cache.height = 30

        session._render_frame_impl()

        assert built == [120], "resize 帧必须按新宽度重建组件树"
        assert session._ink_renderer.reset_calls == [True]
        assert session._ink_renderer.rendered[-1] is new_frame

    def test_static_frame_reuses_previous_frame(self, monkeypatch):
        """对照：无 resize 的空闲帧仍复用上一帧（性能优化不被破坏）。"""
        session, built, old_frame, new_frame = _make_session(80, 24, monkeypatch)

        session._render_frame_impl()

        assert built == [], "空闲帧应复用上一帧（不重建）"
        assert session._ink_renderer.rendered[-1] is old_frame


# ═══════════════════════════════════════════════════════════
# 6. Windows 光标行查询
# ═══════════════════════════════════════════════════════════

class TestWindowsCursorRow:
    def test_query_does_not_raise_and_returns_valid_row(self):
        from src.tui._screen import _query_cursor_row_windows

        row = _query_cursor_row_windows()
        assert row is None or (isinstance(row, int) and row >= 1)
