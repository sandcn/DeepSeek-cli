"""终端能力测试 — 备用屏/括号粘贴/鼠标/raw 模式/标题/光标形状/OSC 8 超链接。

覆盖方向：React Ink v7 API 对齐（``alternateScreen``/``interactive``/
``usePaste`` 真实粘贴通道/``setRawMode``/返回值身份稳定/kitty auto 查询）+
框架扩展（SGR 鼠标输入、OSC 8 可点击链接、窗口标题、光标形状）。
"""

from __future__ import annotations

import io
import time

from src.tui._input_parser import InputParser, KeyEvent, decode_sgr_mouse
from src.tui.ink import (
    BOX,
    TEXT,
    attach_links,
    h,
    hyperlink,
    render,
    renderToString,
    useMouseInput,
    usePaste,
    useStdin,
    useStdout,
    useStderr,
    use_state,
)
from src.tui.ink._paint_canvas import _canvas_row_to_line, _line_as_dict
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.terminal import (
    CURSOR_SHAPES,
    RawModeController,
    TerminalModeManager,
    cursor_shape_sequence,
    linkify_runs,
    linkify_text,
    strip_sequences,
    window_title_sequence,
)
from tests.test_tui.ink._harness import Harness


class _TTYBuffer(io.StringIO):
    """isatty() 为 True 的缓冲（模拟真实终端，用于终端能力协商断言）。"""

    def isatty(self) -> bool:
        return True


class _FakeIO:
    """InputParser 的假 I/O：按需返回预设字节（read_with_timeout/read_bulk）。"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_with_timeout(self, timeout, fd=None):
        if self.pos >= len(self.data):
            return None
        chunk = self.data[self.pos:self.pos + 1]
        self.pos += 1
        return chunk

    def read_bulk(self, fd, max_bytes, timeout):
        if self.pos >= len(self.data):
            return None
        chunk = self.data[self.pos:self.pos + max_bytes]
        self.pos += len(chunk)
        return chunk

    def prepend_pending(self, data: bytes) -> None:
        self.data = data + self.data[self.pos:]
        self.pos = 0


# ═══════════════════════════════════════════════════════════
# 终端模式管理（TerminalModeManager）
# ═══════════════════════════════════════════════════════════

def test_mode_manager_enable_disable_idempotent():
    buf = _TTYBuffer()
    mgr = TerminalModeManager(buf)
    assert mgr.enable("bracketedPaste") is True
    assert mgr.enable("bracketedPaste") is False      # 已启用不重复写
    assert mgr.enable("unknownMode") is False         # 未知模式
    assert mgr.disable("bracketedPaste") is True
    assert mgr.disable("bracketedPaste") is False     # 已禁用
    out = buf.getvalue()
    assert out.count("\x1b[?2004h") == 1
    assert out.count("\x1b[?2004l") == 1


def test_mode_manager_non_tty_writes_nothing():
    buf = io.StringIO()          # isatty() False
    mgr = TerminalModeManager(buf)
    assert mgr.enable("alternateScreen") is False
    assert mgr.enable("mouse") is False
    assert buf.getvalue() == ""


def test_mode_manager_disable_all():
    buf = _TTYBuffer()
    mgr = TerminalModeManager(buf)
    mgr.enable("alternateScreen")
    mgr.enable("mouse")
    mgr.disable_all()
    out = buf.getvalue()
    assert "\x1b[?1049l" in out
    assert "\x1b[?1006l" in out
    assert not mgr.is_enabled("alternateScreen")


def test_raw_mode_controller_unsupported_without_fd():
    ctrl = RawModeController(None)
    assert ctrl.supported is False
    assert ctrl.enable() is False
    assert ctrl.disable() is False
    assert ctrl.active is False


def test_window_title_sequence_strips_controls():
    seq = window_title_sequence("a\x07b\x1bc")
    assert seq == "\x1b]0;abc\x07"


def test_cursor_shape_sequence_known_and_fallback():
    assert cursor_shape_sequence("bar") == f"\x1b[{CURSOR_SHAPES['bar']} q"
    assert cursor_shape_sequence("nope") == "\x1b[0 q"
    assert cursor_shape_sequence(999) == "\x1b[0 q"
    assert cursor_shape_sequence("underline_blink") == "\x1b[4 q"


# ═══════════════════════════════════════════════════════════
# render() 选项：alternateScreen / interactive / 标题 / 光标形状
# ═══════════════════════════════════════════════════════════

def test_render_alternate_screen_enter_and_exit():
    buf = _TTYBuffer()
    ctrl = render(
        h(TEXT, {"children": "a"}), stdout=buf, width=20,
        alternateScreen=True, interactive=False,
    )
    time.sleep(0.05)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b[?1049h" in out       # 进入备用屏
    assert "\x1b[?1049l" in out       # 退出还原


def test_render_non_interactive_writes_no_bracketed_paste():
    buf = io.StringIO()
    ctrl = render(h(TEXT, {"children": "b"}), stdout=buf, width=20)
    time.sleep(0.05)
    ctrl["unmount"]()
    assert "\x1b[?2004h" not in buf.getvalue()


def test_render_interactive_enables_bracketed_paste():
    buf = _TTYBuffer()
    ctrl = render(
        h(TEXT, {"children": "c"}), stdout=buf, width=20, interactive=True,
    )
    time.sleep(0.05)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b[?2004h" in out
    assert "\x1b[?2004l" in out


def test_render_window_title_and_cursor_shape():
    buf = _TTYBuffer()
    ctrl = render(
        h(TEXT, {"children": "d"}), stdout=buf, width=20,
        windowTitle="My Title", cursorShape="bar", interactive=False,
    )
    time.sleep(0.05)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b]0;My Title\x07" in out
    assert f"\x1b[{CURSOR_SHAPES['bar']} q" in out
    assert "\x1b[0 q" in out          # unmount 还原默认形状


def test_render_mouse_option_enables_and_disables():
    buf = _TTYBuffer()
    ctrl = render(
        h(TEXT, {"children": "e"}), stdout=buf, width=20,
        mouse=True, interactive=False,
    )
    time.sleep(0.05)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b[?1006h" in out
    assert "\x1b[?1006l" in out


# ═══════════════════════════════════════════════════════════
# 括号粘贴：解析 → 事件 → usePaste / 输入缓冲兜底
# ═══════════════════════════════════════════════════════════

def test_parser_bracketed_paste_event():
    parser = InputParser(io=_FakeIO(b"200~hello\nworld\x1b[201~"))
    event = parser._read_csi_sequence(0)
    assert event.kind == "paste"
    assert event.char == "hello\nworld"


def test_parser_bracketed_paste_trailing_returned_to_pending():
    fake = _FakeIO(b"200~hi\x1b[201~rest")
    parser = InputParser(io=fake)
    assert parser._read_csi_sequence(0).char == "hi"
    # 结束标记之后的字节回写 pending，后续解析正常消费
    assert fake.read_with_timeout(0.0) == b"r"


def test_parser_bracketed_paste_unterminated_returns_partial():
    parser = InputParser(io=_FakeIO(b"200~partial"))
    event = parser._read_csi_sequence(0)
    assert event.kind == "paste"
    assert event.char == "partial"


def test_use_paste_receives_bracketed_paste_event():
    seen = {"paste": [], "input": []}

    def Comp(props):
        usePaste(lambda text: seen["paste"].append(text))
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="paste", char="multi\nline")) is True
    assert seen["paste"] == ["multi\nline"]


def test_dispatcher_inserts_bracketed_paste_without_use_paste():
    from src.tui._input_dispatcher import InputDispatcher

    class _Stub:
        _dispatch_key_event = InputDispatcher._dispatch_key_event
        _router_consume = InputDispatcher._router_consume
        _mouse_fallback_callback = None
        _input_hook_router = None
        _drop_path_normalize = False

        def __init__(self):
            self.inserted = []

        def _insert_pasted_text(self, text):
            self.inserted.append(text)

    stub = _Stub()
    stub._dispatch_key_event(KeyEvent(kind="paste", char="pasted"))
    assert stub.inserted == ["pasted"]


# ═══════════════════════════════════════════════════════════
# 鼠标：SGR 解析 → useMouseInput → 兜底回调
# ═══════════════════════════════════════════════════════════

def test_decode_sgr_mouse_press_release_move_wheel():
    press = decode_sgr_mouse(b"\x1b[<0;10;5M")
    assert (press.kind, press.mouse_button, press.mouse_action) == ("mouse", "left", "press")
    assert (press.mouse_x, press.mouse_y) == (10, 5)

    release = decode_sgr_mouse(b"\x1b[<2;3;4m")
    assert (release.mouse_button, release.mouse_action) == ("right", "release")

    wheel_up = decode_sgr_mouse(b"\x1b[<64;1;1M")
    assert wheel_up.mouse_action == "wheel" and wheel_up.mouse_wheel == -1
    wheel_down = decode_sgr_mouse(b"\x1b[<65;1;1M")
    assert wheel_down.mouse_wheel == 1

    move = decode_sgr_mouse(b"\x1b[<32;7;8M")
    assert move.mouse_action == "move" and move.mouse_x == 7

    assert decode_sgr_mouse(b"nonsense") is None
    # Ctrl 修饰（+16）
    assert decode_sgr_mouse(b"\x1b[<16;1;1M").mouse_modifiers == 16


def test_parser_dispatches_sgr_mouse_sequence():
    parser = InputParser(io=_FakeIO(b"<0;4;2M"))
    event = parser._read_csi_sequence(0)
    assert event.kind == "mouse"
    assert event.mouse_button == "left"
    assert (event.mouse_x, event.mouse_y) == (4, 2)


def test_use_mouse_input_consumes_event():
    seen = []

    def Comp(props):
        useMouseInput(lambda ev: seen.append((ev.mouse_action, ev.mouse_wheel)) or True)
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(decode_sgr_mouse(b"\x1b[<64;1;1M")) is True
    assert seen == [("wheel", -1)]


def test_use_mouse_input_unconsumed_passes_through():
    def Comp(props):
        useMouseInput(lambda ev: False)
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(decode_sgr_mouse(b"\x1b[<64;1;1M")) is False


def test_mouse_event_without_hooks_not_routed():
    harness = Harness(20)
    harness.render(h(TEXT, {"children": "x"}))
    assert harness.reconciler._input_router_cache is None


def test_dispatcher_mouse_fallback_called():
    from src.tui._input_dispatcher import InputDispatcher

    class _Stub:
        _dispatch_key_event = InputDispatcher._dispatch_key_event
        _router_consume = InputDispatcher._router_consume
        _input_hook_router = None

        def __init__(self):
            self.events = []
            self._mouse_fallback_callback = self.events.append

    stub = _Stub()
    event = decode_sgr_mouse(b"\x1b[<64;1;1M")
    stub._dispatch_key_event(event)
    assert stub.events == [event]


# ═══════════════════════════════════════════════════════════
# useStdin/useStdout/useStderr：身份稳定 + raw 模式能力
# ═══════════════════════════════════════════════════════════

def test_use_stdin_identity_stable_and_raw_mode():
    captured = []
    calls = []
    results = []

    def Comp(props):
        stdin = useStdin()
        captured.append(stdin)
        results.append(stdin["setRawMode"](False))
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    ctx = harness.hook_context
    ctx.raw_mode_supported = lambda: True
    ctx.raw_mode_callback = lambda value: calls.append(value) or True
    harness.render(h(Comp, {}))
    harness.render(h(Comp, {}))
    assert len(captured) == 2
    assert captured[0] is captured[1]            # 身份跨渲染稳定
    assert captured[0]["isRawModeSupported"] is True
    assert results == [True, True]               # 转发到会话注入的 raw 模式回调
    assert calls == [False, False]


def test_use_stdout_stderr_identity_stable():
    captured = []

    def Comp(props):
        captured.append((useStdout(), useStderr()))
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    harness.render(h(Comp, {}))
    (out1, err1), (out2, err2) = captured
    assert out1 is out2 and err1 is err2
    assert "write" in out1 and "write" in err1


def test_session_set_raw_mode_without_support():
    from src.tui.ink.session import InkSession
    from src.tui.ink._render_api import _SimpleModel

    session = InkSession(model=_SimpleModel())
    session.set_interactive(False)
    assert session.raw_mode_supported is False
    assert session.set_raw_mode(True) is False
    session.stop()


# ═══════════════════════════════════════════════════════════
# kitty 键盘协议：auto 模式实机查询
# ═══════════════════════════════════════════════════════════

def test_resolve_kitty_options_auto_with_querier():
    from src.tui.ink.kitty import decode_query_reply, resolve_kitty_options

    assert resolve_kitty_options({"mode": "auto"}, lambda: None) == -1
    assert resolve_kitty_options({"mode": "auto"}, lambda: 3) == 3
    # 显式 flags 时与终端应答取交集
    assert resolve_kitty_options(
        {"mode": "auto", "flags": ["reportEventTypes", "reportAllKeysAsEscapeCodes"]},
        lambda: 2,
    ) == 2
    # 查询异常 → 不启用
    assert resolve_kitty_options({"mode": "auto"}, lambda: (_ for _ in ()).throw(RuntimeError())) == -1
    # enabled 模式不查询
    assert resolve_kitty_options({"mode": "enabled", "flags": ["reportEventTypes"]}) == 2
    assert resolve_kitty_options({"mode": "disabled"}) == -1


def test_decode_query_reply():
    from src.tui.ink.kitty import decode_query_reply

    assert decode_query_reply(b"\x1b[?3u") == 3
    assert decode_query_reply(b"garbage") is None


# ═══════════════════════════════════════════════════════════
# OSC 8 超链接
# ═══════════════════════════════════════════════════════════

def test_styled_run_render_with_link():
    run = StyledRun("click", None, "https://example.com")
    rendered = run.render()
    assert "\x1b]8;;https://example.com\x1b\\click\x1b]8;;\x1b\\" == rendered
    assert run.width == 5                        # 链接不影响显示宽度


def test_hyperlink_strips_control_chars_and_empty_url():
    assert hyperlink("", "text") == "text"
    assert "\x07" not in hyperlink("http://a\x07b", "x")


def test_line_render_emits_osc8():
    line = Line()
    line.append("see ", None)
    line.append("here", None, "https://x.dev")
    assert "https://x.dev" in line.render()
    assert line.plain == "see here"
    assert line.width == 8


def test_canvas_roundtrip_preserves_link():
    line = Line()
    line.append("go", None, "https://a.b")
    row = _line_as_dict(line)
    back = _canvas_row_to_line(row)
    assert [r.link for r in back.runs] == ["https://a.b"]
    assert back.plain == "go"


def test_attach_links_fast_path_returns_same_object():
    runs = [StyledRun("no url here", None)]
    assert attach_links(runs) is runs


def test_attach_links_splits_url_run():
    runs = [StyledRun("visit https://a.b now", None)]
    linked = attach_links(runs)
    assert [r.text for r in linked] == ["visit ", "https://a.b", " now"]
    assert linked[1].link == "https://a.b"
    assert "".join(r.text for r in linked) == "visit https://a.b now"


def test_linkify_runs_www_prefix_normalized():
    runs = linkify_runs("goto www.example.com end")
    url_runs = [r for r in runs if r.link]
    assert url_runs and url_runs[0].link == "http://www.example.com"


def test_linkify_text_and_strip_sequences():
    text = linkify_text("see https://a.b, ok")
    assert "https://a.b" in text
    assert strip_sequences(text) == "see https://a.b, ok"


def test_render_to_string_emits_osc8_for_styled_link():
    styled = [StyledRun("docs", None, "https://docs.example")]
    out = renderToString(h(TEXT, {"styled": styled, "width": 10}), {"columns": 20})
    assert "https://docs.example" in out
    assert "\x1b]8;;" in out
