"""渲染期光标显隐回归测试（2026-10-22 用户需求）。

需求：TUI **渲染时先隐藏光标**；渲染完成后**有输入界面**才把光标移到输入
位置再显示，**没有输入界面不显示**。

覆盖：
  1. ``InkRenderer`` 写帧前隐藏光标（首帧全量写入 / 增量 diff 写入 /
     Ctrl+L 清屏 ``full_clear``），且渲染器自身从不显示光标；
  2. 帧内容完全一致（diff 为空、不写终端）时不写任何 DECTCEM 序列
     （避免空闲帧 60Hz 显隐抖动）；
  3. DECTCEM 状态跟踪：已隐藏时重复隐藏零输出；
  4. ``_cursor.position_cursor`` 返回值表达「是否真正放置」；
  5. ``_SessionFrameMixin._position_cursor``：有输入区 → 先放置再显示；
     无输入区 / 无布局盒 / 定位异常 → 不显示（保持隐藏）；
  6. ``InkSession.stop`` / ``suspend`` 交还终端前恢复光标可见。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from src.tui import _input_cursor as _cursor
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer
from src.tui.ink.session import InkSession

_HIDE = "\033[?25l"
_SHOW = "\033[?25h"
_CLEAR = "\033[2J"


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _frame(*texts: str) -> Frame:
    return Frame([_line(t) for t in texts])


# ── 1. 渲染器：写前隐藏 ──────────────────────────────


def test_render_hides_cursor_before_first_frame_write() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=0)
    renderer.render(_frame("hello", "world"))
    out = stream.getvalue()
    assert _HIDE in out
    # 隐藏序列必须出现在帧内容之前（先隐藏再写帧）
    assert out.index(_HIDE) < out.index("hello")
    # 渲染器自身从不显示光标（显示由会话在定位后决定）
    assert _SHOW not in out


def test_render_hides_cursor_before_incremental_write() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=0)
    renderer.render(_frame("a", "b"))
    # 模拟会话在上帧末尾定位并显示光标
    renderer.set_cursor_visible(True)
    mark = len(stream.getvalue())
    renderer.render(_frame("a", "changed"))
    tail = stream.getvalue()[mark:]
    assert _HIDE in tail
    assert tail.index(_HIDE) < tail.index("changed")


def test_identical_frame_writes_no_cursor_sequences() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=0)
    frame = _frame("same")
    renderer.render(frame)
    renderer.set_cursor_visible(True)
    before = stream.getvalue()
    # 同一帧对象（diff 为空）→ 不写任何字节（含显隐序列）
    renderer.render(frame)
    assert stream.getvalue() == before


def test_cursor_visibility_state_tracking_no_duplicate_writes() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=0)
    renderer.set_cursor_visible(False)
    renderer.set_cursor_visible(False)
    assert stream.getvalue().count(_HIDE) == 1
    renderer.set_cursor_visible(True)
    renderer.set_cursor_visible(True)
    assert stream.getvalue().count(_SHOW) == 1


def test_full_clear_hides_cursor_before_clearing() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=0)
    renderer.render(_frame("x"))
    renderer.set_cursor_visible(True)
    stream.seek(0)
    stream.truncate(0)
    renderer.full_clear()
    out = stream.getvalue()
    assert _HIDE in out
    assert _CLEAR in out
    # 先隐藏，再清屏（清屏后光标不在 (1,1) 闪烁）
    assert out.index(_HIDE) < out.index(_CLEAR)


# ── 2. position_cursor 返回值 ────────────────────────


class _RecRenderer:
    """记录光标调用的渲染器替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def place_cursor(self, row: int, col: int) -> None:
        self.calls.append(("place", row, col))

    def set_cursor_visible(self, visible: bool) -> None:
        self.calls.append(("visible", visible))


def _input_fiber(*, layout: bool = True, props: dict | None = None):
    return SimpleNamespace(
        layout_box=SimpleNamespace(x=0, y=0, w=20, h=1) if layout else None,
        props=(
            props
            if props is not None
            else {"text": "ab", "cursor_pos": 2, "prompt": "> "}
        ),
        deleted=False,
        is_host=True,
        type="input-area",
    )


def test_position_cursor_returns_false_without_layout_box() -> None:
    renderer = _RecRenderer()
    assert _cursor.position_cursor(renderer, 80, _input_fiber(layout=False)) is False
    assert renderer.calls == []


def test_position_cursor_returns_true_after_placing() -> None:
    renderer = _RecRenderer()
    assert _cursor.position_cursor(renderer, 80, _input_fiber()) is True
    assert renderer.calls and renderer.calls[0][0] == "place"


# ── 3. 会话收口：有输入区先定位再显示 ─────────────────


class _Session(_SessionFrameMixin):
    """最小会话替身：只提供 ``_position_cursor`` 所需字段。"""

    def __init__(self, fiber, renderer: _RecRenderer, width: int = 80) -> None:
        self._model = SimpleNamespace()
        self._input_fiber = fiber
        self._root_fiber = SimpleNamespace(
            deleted=False, is_host=False, props={}, sibling=None, child=None,
        )
        self._ink_renderer = renderer
        self._width_cache = SimpleNamespace(get_width=lambda: width)
        # 2026-10 架构演进：光标定位策略由装配注入（框架不再 import 应用输入层）。
        self._cursor_provider = _cursor.position_cursor


def test_position_cursor_places_then_shows() -> None:
    renderer = _RecRenderer()
    _Session(_input_fiber(), renderer)._position_cursor()
    # 必须先放置、后显示（顺序即需求）
    assert [c[0] for c in renderer.calls] == ["place", "visible"]
    assert renderer.calls[1] == ("visible", True)


def test_position_cursor_hidden_without_input_area() -> None:
    renderer = _RecRenderer()
    _Session(None, renderer)._position_cursor()
    assert renderer.calls == [("visible", False)]


def test_position_cursor_stays_hidden_when_not_placed() -> None:
    renderer = _RecRenderer()
    _Session(_input_fiber(layout=False), renderer)._position_cursor()
    # 无布局盒 → 不放置也不显示（不把光标留在旧位置）
    assert renderer.calls == []


def test_position_cursor_stays_hidden_on_placement_error() -> None:
    renderer = _RecRenderer()
    # props 非法（无 ``get`` 的对象）→ position_cursor 抛异常 → 保持隐藏
    _Session(_input_fiber(props="bad"), renderer)._position_cursor()
    assert ("visible", True) not in renderer.calls


# ── 4. 交还终端时恢复光标可见 ────────────────────────


class _FakeWidthCache:
    def get_width(self) -> int:
        return 80

    def get_height(self) -> int:
        return 24

    def get_dimensions(self) -> tuple[int, int]:
        return (80, 24)

    def poll(self, ttl: float = 60.0) -> None:  # pragma: no cover - 本测试不用
        pass


def _make_session() -> tuple[InkSession, io.StringIO]:
    stream = io.StringIO()
    session = InkSession(
        model=SimpleNamespace(), width_cache=_FakeWidthCache(), stream=stream,
    )
    return session, stream


def test_stop_restores_cursor_visible(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.tui._window_title.restore_terminal_title", lambda: None,
    )
    session, stream = _make_session()
    session._ink_renderer.set_cursor_visible(False)
    stream.seek(0)
    stream.truncate(0)
    session.stop()
    assert _SHOW in stream.getvalue()


def test_suspend_restores_cursor_visible(monkeypatch) -> None:
    session, stream = _make_session()
    session._width_cache = _FakeWidthCache()
    session._ink_renderer.set_cursor_visible(False)
    stream.seek(0)
    stream.truncate(0)
    session.suspend()
    assert _SHOW in stream.getvalue()
