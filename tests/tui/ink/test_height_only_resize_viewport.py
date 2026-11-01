"""终端行数单独变化（宽度不变）时只重绘可见视口，不整篇全量刷新。

需求（2026-11-02）：终端**行数变化、宽度不变**时，TUI 不做整篇文档的全量
重写——只清屏并写入文档末尾 ``height`` 行（可见视口），历史行保留在终端
scrollback 不重复写入；宽度变化仍整篇全量重写（终端按新宽度重排旧行，必须
逐行覆盖）。

验证口径（不依赖 resize 前的屏幕内容）：行数变化那一帧的输出喂给**新建的**
pyte 屏幕（新尺寸）后，屏幕内容必须与「文档末 height 行底部对齐」完全一致
——视口重绘必须自足，不得依赖旧屏幕残留。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink import Box, Text, h
from src.tui.ink.session import InkSession


def _tree(count: int):
    lines = [h(Text, None, f"L{i:02d}") for i in range(count)]
    return h(Box, {"flexDirection": "column"}, lines)


def _expected_screen(texts: list[str], height: int) -> list[str]:
    """底部对齐：文档末 height 行，上方补空行。"""
    out = [""] * height
    tail = texts[-height:]
    out[height - len(tail):] = tail
    return out


class _Harness:
    """InkSession + 可改尺寸的宽度缓存 + 独立 pyte 屏幕。"""

    def __init__(self, *, doc_lines: int, width: int = 40, height: int = 20) -> None:
        pyte = pytest.importorskip("pyte")
        self._pyte = pyte
        self.doc_lines = doc_lines
        self.width = width
        self.height = height
        self.cache = TerminalWidthCache(ttl=60)
        self.cache.set_dimensions(width, height)
        self.stream = io.StringIO()
        self.session = InkSession(
            model=SimpleNamespace(
                width=width, reflow_committed=lambda _w: None,
            ),
            build_tree=lambda _m, _w: _tree(doc_lines),
            width_cache=self.cache,
            config=TuiConfig.defaults(),
            stream=self.stream,
        )
        self.screen = self._screen(width, height)
        self._render()  # 首帧（不清屏，建立基准）

    def _screen(self, width: int, height: int):
        return self._pyte.Screen(width, height)

    def _render(self) -> str:
        self.session._dirty = True
        self.session._idle_ok = False
        self.session._render_frame()
        data = self.stream.getvalue()
        self.stream.seek(0)
        self.stream.truncate(0)
        if data:
            self._pyte.Stream(self.screen).feed(data)
        return data

    def resize(self, *, width: int | None = None,
               height: int | None = None) -> tuple[str, list[str]]:
        """改尺寸并渲染一帧；返回 ``(该帧输出, 全新屏幕上的可见内容)``。"""
        if width is not None:
            self.width = width
        if height is not None:
            self.height = height
        self.cache.set_dimensions(self.width, self.height)
        data = self._render()
        if not data:
            # 尺寸未变（无输出）：屏幕内容保持——不能换成空白屏。
            return data, self.lines()
        # 自足性检查：只看本帧输出，新屏幕（新尺寸）重建可见内容。
        fresh = self._screen(self.width, self.height)
        self._pyte.Stream(fresh).feed(data)
        self.screen = fresh
        return data, [line.rstrip() for line in fresh.display]

    def step(self) -> str:
        """渲染一帧（输出喂给**既有**屏幕——增量帧验证用）。"""
        return self._render()

    def set_doc(self, count: int) -> str:
        """换文档行数（模拟流式增长/缩短）并渲染一帧。"""
        self.doc_lines = count
        self.session._build_tree = lambda _m, _w: _tree(count)
        return self._render()

    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]


def _doc(count: int) -> list[str]:
    return [f"L{i:02d}" for i in range(count)]


# ── 1. 行数减少：只重绘可见视口 ──────────────────────────


def test_height_shrink_repaints_viewport_only() -> None:
    h_ = _Harness(doc_lines=40, width=40, height=40)
    assert h_.lines() == _doc(40)

    data, visible = h_.resize(height=20)

    # 只写文档末 20 行（不是整篇 40 行）——写入行数 = 新视口高度。
    assert "\x1b[2J" in data, "视口重绘仍需清屏，避免旧视口残留"
    assert data.count("\n") == 19, f"应只写 20 行，实际 {data.count(chr(10)) + 1} 行"
    assert "L00" not in data and "L19" not in data, "视口外的历史行不得重写"
    assert visible == _doc(40)[-20:]


# ── 2. 行数增加：同样只重绘可见视口 ──────────────────────


def test_height_grow_repaints_viewport_only() -> None:
    h_ = _Harness(doc_lines=40, width=40, height=20)
    assert h_.lines() == _doc(40)[-20:]

    data, visible = h_.resize(height=30)

    assert data.count("\n") == 29, f"应只写 30 行，实际 {data.count(chr(10)) + 1} 行"
    assert "L00" not in data and "L09" not in data
    assert visible == _doc(40)[-30:]


# ── 3. 文档矮于视口：全部行写到底部锚定位置 ──────────────


def test_short_document_height_resize_keeps_bottom_anchor() -> None:
    h_ = _Harness(doc_lines=5, width=40, height=20)

    data, visible = h_.resize(height=24)

    assert data.count("\n") == 4, "矮文档只写 5 行（不补空白行）"
    assert visible == _expected_screen(_doc(5), 24), "文档底部须贴新视口底部"


# ── 4. 宽度变化仍整篇全量重写（对照） ────────────────────


def test_width_change_still_rewrites_whole_document() -> None:
    h_ = _Harness(doc_lines=30, width=40, height=20)

    data, visible = h_.resize(width=20)

    assert "\x1b[2J" in data
    # 整篇 30 行都写（宽度变化后终端可能重排旧行，必须整篇覆盖）。
    assert data.count("\n") == 29, f"宽度变化应写整篇 30 行，实际 {data.count(chr(10)) + 1}"
    assert "L00" in data
    assert visible == _expected_screen(_doc(30), 20)


# ── 5. 视口重绘后的增量帧仍按可见区更新 ─────────────────


def test_incremental_frame_after_viewport_repaint() -> None:
    h_ = _Harness(doc_lines=40, width=40, height=40)
    h_.resize(height=20)  # 先做一次行数单独变化（视口重绘）

    # 文档末尾新增一行（模型/帧重新构建）：走增量路径，只写变化的可见行。
    h_.doc_lines = 41
    h_.session._build_tree = lambda _m, _w: _tree(41)
    data = h_.step()

    assert data.count("\n") <= 3, f"增量帧写行数过多：{data.count(chr(10))}"
    assert "L00" not in data
    assert h_.lines() == _doc(41)[-20:]


# ── 6. 视口重绘后的增长/缩短仍正确（物理缓冲 = 一屏） ────


def test_content_growth_and_shrink_after_viewport_repaint() -> None:
    h_ = _Harness(doc_lines=40, width=40, height=40)
    h_.resize(height=20)

    # 视口重绘后 ``_buf_h == height``（无历史漂移）——后续增量增长/缩短
    # 走滚动/IL 快路径，屏幕必须始终等于文档末 20 行。
    for count in (43, 38, 60, 25):
        h_.set_doc(count)
        assert h_.lines() == _doc(count)[-20:], f"doc={count}"


# ── 7. 渲染器状态：视口重绘后记账与新视口一致 ────────────


def test_renderer_state_matches_new_viewport() -> None:
    h_ = _Harness(doc_lines=40, width=40, height=40)
    h_.resize(height=20)

    renderer = h_.session._ink_renderer
    assert renderer._height == 20
    assert renderer._buf_h == 20, "物理缓冲应等于新视口高度（无历史漂移）"
    assert renderer._top_aligned is False
    assert renderer.cursor_row == 20
    assert renderer.prev_frame is not None
    assert renderer.prev_frame.height == 40, "prev 仍是完整文档帧（增量 diff 基准不变）"
