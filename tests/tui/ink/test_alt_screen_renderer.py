"""AlternateScreenRenderer 回归测试（B3 备用屏渲染基线）。

覆盖：
  1. 视口裁剪（文档高于屏幕时显示最后 N 行）；
  2. 全量帧重绘序列（HOME / 清行 / 文本 / 清下方）；
  3. 光标定位钳制到视口；
  4. InkSession(alternate_screen=True) 使用备用屏渲染器并输出组件文本。
"""

from __future__ import annotations

import io

import pytest

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink import TEXT, h
from src.tui.ink._alt_screen_renderer import AlternateScreenRenderer
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.output import Frame, Line, StyledRun
from src.tui.ink.session import InkSession

try:
    import pyte
except ImportError:  # pragma: no cover
    pyte = None


def _frame(*texts: str) -> Frame:
    return Frame([Line([StyledRun(t, None)]) for t in texts])


def test_alt_renderer_clips_to_viewport_height():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=2)
    r.render(_frame("a", "b", "c"))
    text = out.getvalue()
    assert "b" in text and "c" in text
    assert "\x1b[H" in text
    assert "\x1b[2K" in text
    # 视口贴底：首行 a 被裁掉
    assert "\x1b[2K" + "a" not in text


def test_alt_renderer_place_cursor_clamps():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=3)
    r.set_width(20)
    r.place_cursor(99, 999)
    assert "\x1b[3;20H" in out.getvalue()


def test_alt_renderer_scroll_offset_shows_earlier_lines():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=2)
    r.render(_frame("a", "b", "c", "d", "e"))
    assert "d" in out.getvalue() and "e" in out.getvalue()

    out2 = io.StringIO()
    r = AlternateScreenRenderer(stream=out2, height=2)
    r.render(_frame("a", "b", "c", "d", "e"))
    r.scroll_by(1)                 # 向上滚 1 行
    out2.seek(0)
    out2.truncate(0)
    r.render(_frame("a", "b", "c", "d", "e"))
    text = out2.getvalue()
    assert "c" in text and "d" in text
    assert "\x1b[2K" + "e" not in text

    r.scroll_to_bottom()
    out2.seek(0)
    out2.truncate(0)
    r.render(_frame("a", "b", "c", "d", "e"))
    assert "e" in out2.getvalue()


def test_alt_renderer_cursor_maps_to_window():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=2)
    r.set_width(20)
    r.render(_frame("a", "b", "c", "d", "e"))
    r.scroll_by(1)                 # 窗口 = c,d（start=2）
    r.render(_frame("a", "b", "c", "d", "e"))
    r.place_cursor(4, 3)           # 文档第 4 行 d → 窗口第 2 行
    assert "\x1b[2;3H" in out.getvalue()


def test_alt_renderer_incremental_rewrites_only_changed_rows():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=3)
    r.render(_frame("a", "b", "c"))
    out.seek(0)
    out.truncate(0)

    r.render(_frame("a", "B", "c"))
    text = out.getvalue()
    assert "\x1b[2;1H" in text      # 只定位到第 2 行
    assert "B" in text
    assert "\x1b[H" not in text     # 未全量重绘
    assert "a" not in text          # 未重写未变化行


def test_alt_renderer_scroll_triggers_full_redraw():
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=2)
    r.render(_frame("a", "b", "c", "d", "e"))
    r.scroll_by(1)
    out.seek(0)
    out.truncate(0)
    r.render(_frame("a", "b", "c", "d", "e"))
    assert "\x1b[H" in out.getvalue()   # 窗口变化 → 全量重绘


@pytest.mark.skipif(pyte is None, reason="pyte 未安装，跳过终端快照回归")
def test_alt_renderer_pyte_snapshot():
    screen = pyte.Screen(20, 3)
    stream = pyte.Stream(screen)
    out = io.StringIO()
    r = AlternateScreenRenderer(stream=out, height=3)
    r.render(_frame("alpha", "beta", "gamma"))
    stream.feed(out.getvalue())
    assert [line.strip() for line in screen.display] == ["alpha", "beta", "gamma"]


def test_session_scroll_api_only_for_alt_screen():
    def _build_tree(model, width):
        return h(TEXT, {"children": "scroll"})

    alt = InkSession(
        model=_SimpleModel(),
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
        alternate_screen=True,
    )
    assert alt.scroll_by(1) is True
    assert alt.scroll_to_bottom() is True

    flow = InkSession(
        model=_SimpleModel(),
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )
    assert flow.scroll_by(1) is False
    assert flow.scroll_to_bottom() is False


def test_alternate_screen_config_default_off():
    from src.config import TUI_ALTERNATE_SCREEN
    from src.tui._config import TuiConfig

    assert TUI_ALTERNATE_SCREEN is False
    assert TuiConfig.defaults().alternate_screen is False


def test_session_alternate_screen_uses_alt_renderer():
    def _build_tree(model, width):
        return h(TEXT, {"children": "alt-screen"})

    out = io.StringIO()
    session = InkSession(
        model=_SimpleModel(),
        build_tree=_build_tree,
        stream=out,
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
        alternate_screen=True,
    )
    assert isinstance(session._ink_renderer, AlternateScreenRenderer)
    session._render_frame()
    assert "alt-screen" in out.getvalue()
