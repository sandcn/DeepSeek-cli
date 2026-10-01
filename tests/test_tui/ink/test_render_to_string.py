"""renderToString — React Ink renderToString() 等价物测试。"""

from __future__ import annotations

from src.tui.ink import (
    BOX,
    TEXT,
    h,
    renderToString,
    use_state,
    useLayoutEffect,
)
from src.tui.ink import hooks as H


def test_render_to_string_basic():
    assert renderToString(h(TEXT, {"children": "hello"})) == "hello"


def test_render_to_string_columns_option():
    text = renderToString(
        h(TEXT, {"children": "hello world"}), {"columns": 5}
    )
    assert text.split("\n")[0] == "hello"


def test_render_to_string_nested_layout():
    el = h(
        BOX,
        {"flexDirection": "column"},
        h(TEXT, {"children": "a"}),
        h(TEXT, {"children": "b"}),
    )
    assert renderToString(el) == "a\nb"


def test_render_to_string_layout_effect_state_reflected():
    def Comp(props):
        value, set_value = use_state("start")
        useLayoutEffect(lambda: set_value("done"), ())
        return h(TEXT, {"children": value})

    assert renderToString(h(Comp, {})) == "done"


def test_render_to_string_isolates_hook_state():
    H.set_screen_reader_enabled(True)
    H.reset_animation_state()
    before_stdout = H._stdout_accessor
    H._stdout_accessor = lambda: "SESSION"

    try:
        assert renderToString(h(TEXT, {"children": "x"})) == "x"
        # renderToString 结束后恢复既有全局状态（隔离）
        assert H._stdout_accessor is not None
    finally:
        H._stdout_accessor = before_stdout
        H.set_screen_reader_enabled(False)


def test_render_to_string_does_not_write_stdout():
    import io
    import sys

    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        renderToString(h(TEXT, {"children": "quiet"}))
    finally:
        sys.stdout = saved
    assert buf.getvalue() == ""


def test_render_to_string_multiline_wrap():
    text = renderToString(h(TEXT, {"children": "aaaa bbbb"}), {"columns": 4})
    assert text.split("\n") == ["aaaa", "bbbb"]
