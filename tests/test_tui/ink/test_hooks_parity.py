"""usePaste / useCursor / useBoxMetrics / useWindowSize 官方语义测试。"""

from __future__ import annotations

from src.tui.ink import (
    BOX,
    TEXT,
    h,
    usePaste,
    use_input,
    useCursor,
    useBoxMetrics,
    useWindowSize,
    use_ref,
)
from src.tui._input_parser import KeyEvent
from tests.test_tui.ink._harness import Harness


def test_use_paste_consumes_paste_and_blocks_input():
    seen = {"paste": [], "input": []}

    def Comp(props):
        def _on_paste(text):
            seen["paste"].append(text)  # 返回 None（官方 void 签名）

        def _on_input(event):
            seen["input"].append(getattr(event, "char", ""))
            return True

        usePaste(_on_paste)
        use_input(_on_input)
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="char", char="pasted text")) is True
    assert seen["paste"] == ["pasted text"]
    assert seen["input"] == []


def test_use_paste_single_char_not_paste():
    seen = []

    def Comp(props):
        usePaste(seen.append)
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="char", char="a")) is False
    assert seen == []


def test_use_paste_is_active_false():
    seen = []

    def Comp(props):
        usePaste(seen.append, {"isActive": False})
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    router = harness.reconciler._input_router_cache
    if router is not None:
        assert router[1](KeyEvent(kind="char", char="pasted")) is False
    assert seen == []


def test_use_cursor_returns_setter():
    captured = {}

    def Comp(props):
        captured["cursor"] = useCursor()
        return h(TEXT, {"children": "x"})

    harness = Harness(20)
    harness.render(h(Comp, {}))
    assert callable(captured["cursor"]["setCursorPosition"])
    # 未注入回调 → no-op 不抛异常
    captured["cursor"]["setCursorPosition"]({"x": 1, "y": 1})
    captured["cursor"]["setCursorPosition"](None)


def test_use_box_metrics_shape():
    captured = {}

    def Comp(props):
        ref = use_ref(None)
        captured["m"] = useBoxMetrics(ref)
        captured["ref"] = ref
        return h(BOX, {"ref": ref, "width": 4, "height": 2}, h(TEXT, {"children": "ab"}))

    harness = Harness(20)
    harness.render(h(Comp, {}))
    harness.render(h(Comp, {}))
    metrics = captured["m"]
    assert set(metrics) == {"width", "height", "left", "top", "hasMeasured"}


def test_use_window_size_shape():
    captured = {}

    def Comp(props):
        captured["w"] = useWindowSize()
        return h(TEXT, {"children": "x"})

    Harness(20).render(h(Comp, {}))
    assert set(captured["w"]) == {"columns", "rows"}
    assert captured["w"]["columns"] > 0


def test_memo_wraps_normal_single_arg_component():
    """memo(Comp) 对常规单参组件不得 TypeError（P0 回归）。"""
    from src.tui.ink import memo

    calls = []

    def Comp(props):
        calls.append(props.get("v"))
        return h(TEXT, {"children": str(props.get("v"))})

    Memo = memo(Comp)
    harness = Harness(20)
    assert harness.render(h(Memo, {"v": 1})) == "1"
    # props 未变 → memo 短路（组件函数不再调用）
    assert harness.render(h(Memo, {"v": 1})) == "1"
    assert calls == [1]


def test_memo_are_equal_custom():
    from src.tui.ink import memo

    calls = []

    def Comp(props):
        calls.append(1)
        return h(TEXT, {"children": str(props.get("v"))})

    Memo = memo(Comp, are_equal=lambda a, b: True)
    harness = Harness(20)
    harness.render(h(Memo, {"v": 1}))
    harness.render(h(Memo, {"v": 2}))
    assert calls == [1]


def test_memo_forward_ref_transfers_marker():
    from src.tui.ink import memo, forwardRef

    def Inner(props, ref):
        return h(TEXT, {"children": "fr"})

    Forwarded = forwardRef(Inner)
    Memo = memo(Forwarded)
    assert getattr(Memo, "_is_forward_ref", False) is True
    assert getattr(Memo, "_forward_ref_fn", None) is not None


def test_event_key_ctrl_from_kitty_bits():
    from src.tui._input_parser import InputParser
    from src.tui.ink._hooks_input import _event_key

    class _IO:
        def __init__(self, data):
            self.d = data
            self.i = 0

        def read_with_timeout(self, fd, timeout):
            if self.i >= len(self.d):
                return None
            b = self.d[self.i:self.i + 1]
            self.i += 1
            return b

    # \x1b[97;5u → Ctrl+A → 映射为 home（modifier 被置 0），kitty ctrl 位=4
    ev = InputParser(io=_IO(b"97;5u"))._read_csi_sequence(None)
    key = _event_key(ev)
    assert key["ctrl"] is True
    assert key["home"] is True
