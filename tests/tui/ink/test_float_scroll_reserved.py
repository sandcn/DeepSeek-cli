"""满屏流式输出时浮动窗口闪烁回归测试（2026-10-23 用户报障）。

用户报障：「流式输出时，满屏的时候，浮动窗口闪」。

根因：底部锚定的浮动窗口（``/loop``、``/usage``、``/pet``）位于文档末尾、
状态栏/输入区上方。增长时渲染器只把**底栏**排除出 DECSTBM 滚动区域
（``_scroll_grow_reserved``），浮窗仍在内容区内：终端先把浮窗随内容一起
滚上去、再由后续写入重画——mintty 等逐字节渲染的终端会显示这个中间态
（浮窗跳一下/闪一下）。缩短路径（``_insert_shrink``）的整屏 IL 同样会把
浮窗与底栏一起下移一帧。

修复：
  - 会话侧 ``_fixed_bottom_rows`` 把「可见的底部锚定浮窗」并入文档末尾
    固定区，经 ``set_bottom_reserved`` 注入渲染器；
  - 渲染器增长（``_scroll_grow_reserved``）滚动区域因此只覆盖浮窗上方内容；
  - 新增 ``_insert_shrink_reserved``：缩短时用 DECSTBM 把 IL 限制在内容区，
    固定区原地不动。

覆盖：
  1. 满屏 + ``/loop`` 浮窗：逐帧增长时窗口行位置不变、DECSTBM 区域含浮窗；
  2. 多窗口（``/loop`` + 第二浮窗）：固定区取最上沿；
  3. 非底部锚定浮窗与固定区相交：保守回退为仅固定底栏（屏幕仍正确）；
  4. 浮窗高度变化：固定区行数变化 → 回退整屏滚动（屏幕仍正确）；
  5. 缩短路径：流式结束释放补白走固定区局部 IL（不整屏下移），屏幕正确；
  6. 渲染器级：``_insert_shrink_reserved`` 的 IL 限制与逐行正确性。
"""

from __future__ import annotations

import io

import pytest

from src.tui.app.apply import apply_cmd
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _frame(texts: list[str]) -> Frame:
    return Frame([_line(t) for t in texts])


def _line_text(line: Line) -> str:
    return "".join(run.text for run in line.runs)


# ── 渲染器级：固定区缩短（IL 限制在内容区） ──────────────────


class _Harness:
    """渲染器 + pyte 终端（首帧与增量帧都喂给模拟器）。"""

    def __init__(self, height: int = 30, width: int = 40) -> None:
        pyte = pytest.importorskip("pyte")
        self.height = height
        self.stream = io.StringIO()
        self.screen = pyte.Screen(width, height)
        self.parser = pyte.Stream(self.screen)
        self.renderer = InkRenderer(stream=self.stream, height=height)

    def _drain(self) -> str:
        data = self.stream.getvalue()
        self.stream.seek(0)
        self.stream.truncate(0)
        if data:
            self.parser.feed(data)
        return data

    def render(self, texts: list[str], bottom: int) -> str:
        self.renderer.set_bottom_reserved(bottom)
        self.renderer.render(_frame(texts))
        return self._drain()

    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    def assert_screen(self, texts: list[str]) -> None:
        expected = [""] * self.height
        tail = texts[-self.height:]
        expected[self.height - len(tail):] = tail
        assert self.lines() == expected


def test_reserved_shrink_uses_region_il_and_keeps_fixed_rows() -> None:
    """缩短：IL 只在内容区插入（固定区行原地不动），屏幕与新帧一致。"""
    h = _Harness(height=30, width=40)
    bottom = 6
    doc = [f"L{i:02d}" for i in range(24)] + [
        "win1", "win2", "status", "input", "mode", "hint",
    ]
    h.render(doc, bottom)
    fixed_before = h.lines()[-bottom:]

    # 文档缩短 2 行（内容整体下移）：固定区 6 行必须原地不动。
    shrunk = [f"L{i:02d}" for i in range(2, 24)] + doc[-6:]
    data = h.render(shrunk, bottom)
    # 滚动区域 = 内容区 1..height-bottom；IL 只在区域内插入 2 行。
    assert f"\033[1;{h.height - bottom}r" in data
    assert "\033[2L" in data
    assert h.lines()[-bottom:] == fixed_before
    h.assert_screen(shrunk)
    assert h.renderer.cursor_row == h.height


def test_reserved_shrink_middle_removal_screen_still_correct() -> None:
    """内容中部删除（映射整体位移）：屏幕必须与底部对齐的新帧完全一致。"""
    h = _Harness(height=30, width=40)
    bottom = 6
    doc = [f"L{i:02d}" for i in range(24)] + [
        "win1", "win2", "status", "input", "mode", "hint",
    ]
    h.render(doc, bottom)

    shrunk = doc[:10] + doc[12:]  # 删除 L10、L11
    h.render(shrunk, bottom)
    h.assert_screen(shrunk)


def test_reserved_short_document_growth_and_shrink_keep_fixed_rows() -> None:
    """矮文档（内容不足一屏）+ 固定区：增长/缩短后固定区与屏幕仍正确。"""
    h = _Harness(height=30, width=40)
    bottom = 6
    doc = [f"L{i:02d}" for i in range(10)] + [
        "win1", "win2", "status", "input", "mode", "hint",
    ]
    h.render(doc, bottom)
    fixed_before = h.lines()[-bottom:]

    grown = doc[:-6] + ["new"] + doc[-6:]
    h.render(grown, bottom)
    h.assert_screen(grown)
    assert h.lines()[-bottom:] == fixed_before

    h.render(doc, bottom)
    h.assert_screen(doc)
    assert h.lines()[-bottom:] == fixed_before


def test_reserved_growth_geq_content_height_rewrites_whole_region() -> None:
    """增长量 >= 内容区高度：旧内容被整段滚出后必须整段重写（不留空白）。"""
    h = _Harness(height=30, width=40)
    bottom = 16  # 固定区占大半屏：内容区只剩 8 行
    content_h = h.height - bottom
    footer = [f"f{i}" for i in range(bottom)]
    doc = ["same"] * 24 + footer
    h.render(doc, bottom)

    # 一次增长 9 行（> content_h=8）：滚动量被区域高度截断，旧内容全部滚出。
    grown = doc[:24] + ["same"] * 9 + footer
    h.render(grown, bottom)
    h.assert_screen(grown)
    # 内容区 8 行必须是重写后的 "same"，而不是滚动残留的空白。
    assert h.lines()[:content_h] == ["same"] * content_h


def test_reserved_shrink_falls_back_when_bottom_count_changes() -> None:
    """固定区行数变化（浮窗高度变化）：回退整屏 IL，屏幕仍必须正确。"""
    h = _Harness(height=30, width=40)
    doc = [f"L{i:02d}" for i in range(24)] + [
        "win1", "win2", "status", "input", "mode", "hint",
    ]
    h.render(doc, 6)
    shrunk = doc[:10] + doc[12:] + ["extra"]
    data = h.render(shrunk, 7)
    assert f"\033[1;{h.height - 7}r" not in data  # 未使用局部 IL
    h.assert_screen(shrunk)


# ── 会话级：满屏流式 + 浮窗 ─────────────────────────────


class _Recorder(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def write(self, s: str) -> int:
        self.chunks.append(s)
        return super().write(s)

    def take(self) -> str:
        data = "".join(self.chunks)
        self.chunks.clear()
        self.seek(0)
        self.truncate(0)
        return data


def _make_session(windows=None, *, width: int = 80, height: int = 24,
                  active: bool = True):
    pyte = pytest.importorskip("pyte")
    from src.tui._config import TuiConfig
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.app import build_app_element
    from src.tui.app.apply import apply_cmd
    from src.tui.app.model import AppModel
    from src.tui.ink.session import InkSession

    cache = TerminalWidthCache(ttl=60)
    cache.set_dimensions(width, height)
    model = AppModel()
    model.status.status_active = active
    model.floating_windows = list(windows or [])
    stream = _Recorder()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        width_cache=cache,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    screen = pyte.Screen(width, height)
    parser = pyte.Stream(screen)
    return model, session, stream, screen, parser, width, height


def _step(model, session, stream, screen, parser, *cmds) -> str:
    for cmd in cmds:
        apply_cmd(model, cmd)
    session._dirty = True
    session._idle_ok = False
    session._render_frame()
    data = stream.take()
    if data:
        parser.feed(data)
    return data


def _assert_screen_matches_frame(session, screen, height: int) -> None:
    frame = session._last_frame
    got = [line.rstrip() for line in screen.display]
    want = [_line_text(ln).rstrip() for ln in frame.lines[-height:]]
    assert got == want


def _window_rows(display, needle: str) -> list[int]:
    return [index for index, line in enumerate(display) if needle in line]


def _messages(count: int = 14) -> list[dict]:
    return [
        {"role": "user", "content": f"消息 {index} " + "内容" * 30}
        for index in range(count)
    ]


def _loop_model(model) -> None:
    st = model.loop_status
    st.active = True
    st.total = 3
    st.round = 1
    st.run = 1
    st.prompt = "测试提词"


def _fill_and_grow(windows, *, frames: int = 6):
    from src.tui._const import ContentCmd, DisplayMsgsCmd

    model, session, stream, screen, parser, width, height = _make_session(
        windows,
    )
    _loop_model(model)
    _step(model, session, stream, screen, parser,
          DisplayMsgsCmd(messages=_messages()))
    text = ""
    growth: list[tuple[str, int]] = []
    for n in range(1, frames + 1):
        text += f"流式第 {n} 行内容 " + "abcdefghij" * 5 + "\n"
        data = _step(model, session, stream, screen, parser, ContentCmd(text=text))
        growth.append((data, session._last_frame.height))
    return model, session, stream, screen, parser, height, growth


def test_full_screen_growth_keeps_loop_window_fixed() -> None:
    """满屏增长：浮窗行位置逐帧不变，DECSTBM 区域包含浮窗（不只底栏）。"""
    from src.tui.app.floating import loop_window_spec

    model, session, stream, screen, parser, height, growth = _fill_and_grow(
        [loop_window_spec()],
    )
    display = [line.rstrip() for line in screen.display]
    top_rows = _window_rows(display, "╭")
    title_rows = _window_rows(display, "⟳ /loop")
    bottom_rows = _window_rows(display, "╰")
    assert top_rows and title_rows and bottom_rows

    window_h = session._root_fiber._float_frame_windows[0].layout_box.h
    footer_rows = session._last_frame.height - session._find_bottom_area_fiber().layout_box.y
    fixed = int(window_h) + int(footer_rows)
    content_h = height - fixed
    assert session._ink_renderer._bottom_reserved == fixed
    assert content_h > 0

    prev_doc_h = None
    for data, doc_h in growth:
        if prev_doc_h is not None and doc_h > prev_doc_h and data:
            assert f"\033[1;{content_h}r" in data, (
                f"增长滚动区域未包含浮窗：期望 1..{content_h}，"
                f"实际输出 {data[:120]!r}"
            )
        prev_doc_h = doc_h
        _assert_screen_matches_frame(session, screen, height)
        now = [line.rstrip() for line in screen.display]
        assert _window_rows(now, "╭") == top_rows
        assert _window_rows(now, "⟳ /loop") == title_rows
        assert _window_rows(now, "╰") == bottom_rows


def test_multiple_bottom_windows_use_topmost_fixed_top() -> None:
    """多个底部锚定浮窗：固定区取最上沿（较高的窗口），屏幕逐帧正确。"""
    from src.tui.app.floating import FloatingWindowSpec, loop_window_spec
    from src.tui.ink import TEXT, h

    small = FloatingWindowSpec(
        id="small-window",
        render=lambda props: h(TEXT, {"children": "小窗"}),
        priority=90,
        width=24,
        x="left",
        bottom="bottom_area",
        border=True,
        border_style="round",
    )
    model, session, stream, screen, parser, height, growth = _fill_and_grow(
        [loop_window_spec(), small],
    )
    window_h = max(
        int(fiber.layout_box.h)
        for fiber in session._root_fiber._float_frame_windows
        if not getattr(fiber, "_float_hidden", False)
    )
    footer_rows = session._last_frame.height - session._find_bottom_area_fiber().layout_box.y
    fixed = int(window_h) + int(footer_rows)
    assert session._ink_renderer._bottom_reserved == fixed
    for _data, _doc_h in growth:
        _assert_screen_matches_frame(session, screen, height)


def test_non_fixed_window_intersecting_fixed_area_falls_back() -> None:
    """文档锚定浮窗与固定区相交：只固定底栏（保守，屏幕仍正确）。"""
    from src.tui.app.floating import FloatingWindowSpec, loop_window_spec
    from src.tui.ink import TEXT, h

    doc_window = FloatingWindowSpec(
        id="doc-window",
        render=lambda props: h(TEXT, {"children": "文档锚定"}),
        priority=110,
        width=20,
        x="left",
        bottom="doc_bottom",
        border=True,
        allow_cover_bottom=True,
    )
    model, session, stream, screen, parser, height, growth = _fill_and_grow(
        [loop_window_spec(), doc_window],
    )
    footer_rows = session._last_frame.height - session._find_bottom_area_fiber().layout_box.y
    for _data, _doc_h in growth:
        assert session._ink_renderer._bottom_reserved == footer_rows
        _assert_screen_matches_frame(session, screen, height)


def test_window_height_change_falls_back_to_full_scroll() -> None:
    """浮窗高度变化 → 固定区行数变化 → 回退整屏滚动（屏幕仍正确）。"""
    from src.tui.app.floating import FloatingWindowSpec
    from src.tui.ink import TEXT, h

    state = {"lines": 1}

    def _render(props):
        children = [
            h(TEXT, {"key": f"dyn-{index}", "children": f"动态行 {index}"})
            for index in range(state["lines"])
        ]
        return h("fragment", {"key": "dyn-body"}, children)

    spec = FloatingWindowSpec(
        id="dyn-window", render=_render, priority=110, width=20,
        x="right", bottom="bottom_area",
    )
    model, session, stream, screen, parser, height, growth = _fill_and_grow(
        [spec], frames=4,
    )
    before = session._ink_renderer._bottom_reserved
    assert before > 0

    from src.tui._const import ContentCmd

    state["lines"] = 4
    data = _step(
        model, session, stream, screen, parser,
        ContentCmd(text="高度变化\n"),
    )
    after = session._ink_renderer._bottom_reserved
    assert after != before
    assert f"\033[1;{height - after}r" not in data  # 未使用局部滚动
    _assert_screen_matches_frame(session, screen, height)


def test_stream_end_shrink_uses_reserved_il(monkeypatch) -> None:
    """流式结束释放补白：缩短走固定区局部 IL，浮窗行位置不变。"""
    from src.tui._const import DisplayMsgsCmd, ParseInfoCmd
    from src.tui.app.floating import loop_window_spec

    model, session, stream, screen, parser, width, height = _make_session(
        [loop_window_spec()],
    )
    _loop_model(model)
    _step(model, session, stream, screen, parser,
          DisplayMsgsCmd(messages=_messages()))
    _step(model, session, stream, screen, parser,
          ParseInfoCmd(tool_names="Edit", tokens=100, elapsed=1.0))
    _step(model, session, stream, screen, parser,
          ParseInfoCmd(tool_names="", tokens=-1, elapsed=0.0))
    assert session._frame_bottom_pad == 1

    display = [line.rstrip() for line in screen.display]
    top_rows = _window_rows(display, "╭")
    title_rows = _window_rows(display, "⟳ /loop")
    bottom_rows = _window_rows(display, "╰")
    assert top_rows and title_rows and bottom_rows

    renderer = session._ink_renderer
    calls: list[str] = []
    original = renderer._insert_shrink_reserved

    def _record(frame, prev_h, new_h, bottom):
        calls.append("reserved")
        return original(frame, prev_h, new_h, bottom)

    monkeypatch.setattr(renderer, "_insert_shrink_reserved", _record)
    model.status.status_active = False
    data = _step(model, session, stream, screen, parser)

    assert calls == ["reserved"], "固定区存在时缩短未走局部 IL 路径"
    assert "\033[1L" in data
    _assert_screen_matches_frame(session, screen, height)
    now = [line.rstrip() for line in screen.display]
    # 浮窗行位置不变（整屏 IL 会把浮窗与底栏一起下移一帧）。
    assert _window_rows(now, "╭") == top_rows
    assert _window_rows(now, "⟳ /loop") == title_rows
    assert _window_rows(now, "╰") == bottom_rows
