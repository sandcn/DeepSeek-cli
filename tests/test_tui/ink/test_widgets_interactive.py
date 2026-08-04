"""测试 ink/widgets/interactive.py — SelectInput / TextInput / MultiSelect / ConfirmInput。

覆盖：
  - 渲染输出（选中高亮/勾选/光标/占位符）；
  - 按键交互（移动/输入/删除/提交/确认）与回调触发；
  - 同批连续按键（渲染批次间无重渲染）状态累积正确性；
  - focus=False 不参与输入路由（事件放行）；
  - limit 滚动窗口 / initialIndex / initialValues / mask / placeholder。
"""

from __future__ import annotations

from src.tui.ink import h
from src.tui.ink.widgets import SelectInput, TextInput, MultiSelect, ConfirmInput
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.hooks import set_input_router_callback
from src.tui._input_parser import KeyEvent


def _render(element, width=80, height=24):
    """渲染元素树，返回 (Frame, Reconciler, root)。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, element, width, height)
    frame = render_frame(root, width)
    return frame, r, root


def _rerender(r, root, element, width=80, height=24):
    """再次调和 + 渲染（应用 state queue）。"""
    r.render(root, element, width, height)
    return render_frame(root, width)


class _Router:
    """捕获 reconciler 发布的 input router。"""

    def __init__(self):
        self.router = None
        set_input_router_callback(lambda router: setattr(self, "router", router))

    def cleanup(self):
        set_input_router_callback(None)

    def key(self, kind, char=""):
        return self.router(KeyEvent(kind=kind, char=char))


def _key(kind, char=""):
    return KeyEvent(kind=kind, char=char)


# ═══════════════════════════════════════════════════════════
# SelectInput
# ═══════════════════════════════════════════════════════════


class TestSelectInput:
    def test_render_first_selected(self):
        frame, _, _ = _render(h(SelectInput, {"items": ["A", "B", "C"]}))
        assert [ln.plain for ln in frame.lines] == ["> A", "  B", "  C"]

    def test_render_initial_index(self):
        frame, _, _ = _render(h(SelectInput, {"items": ["A", "B", "C"], "initialIndex": 2}))
        assert [ln.plain for ln in frame.lines] == ["  A", "  B", "> C"]

    def test_render_dict_items(self):
        items = [{"label": "One", "value": 1}, {"label": "Two", "value": 2}]
        frame, _, _ = _render(h(SelectInput, {"items": items}))
        assert [ln.plain for ln in frame.lines] == ["> One", "  Two"]

    def test_arrow_move_and_enter_callback(self):
        calls = []
        cap = _Router()
        try:
            el = h(SelectInput, {"items": ["A", "B", "C"], "onSelect": lambda it: calls.append(it["value"])})
            _, r, root = _render(el)
            assert cap.key("arrow_down") is True
            assert cap.key("arrow_down") is True
            assert cap.key("enter") is True
            assert calls == ["C"]  # down x2 → index 2
        finally:
            cap.cleanup()

    def test_arrow_up_clamps_at_top(self):
        calls = []
        cap = _Router()
        try:
            el = h(SelectInput, {"items": ["A", "B"], "onSelect": lambda it: calls.append(it["value"])})
            _, r, root = _render(el)
            cap.key("arrow_up")  # 已在 0，不移动
            cap.key("enter")
            assert calls == ["A"]
        finally:
            cap.cleanup()

    def test_same_batch_consecutive_keys_accumulate(self):
        """同一渲染批次内连续按键状态累积（闭包陈旧修复）。"""
        calls = []
        cap = _Router()
        try:
            el = h(SelectInput, {"items": ["A", "B", "C", "D"], "onSelect": lambda it: calls.append(it["value"])})
            _, r, root = _render(el)
            cap.key("arrow_down")
            cap.key("arrow_down")
            cap.key("arrow_down")
            cap.key("enter")
            assert calls == ["D"]  # 未渲染也累积到 index 3
        finally:
            cap.cleanup()

    def test_limit_scroll_window(self):
        """limit=2 时可见窗口跟随选中滚动。"""
        cap = _Router()
        try:
            el = h(SelectInput, {"items": ["A", "B", "C", "D"], "limit": 2})
            frame, r, root = _render(el)
            assert [ln.plain for ln in frame.lines] == ["> A", "  B"]
            cap.key("arrow_down")
            cap.key("arrow_down")
            frame2 = _rerender(r, root, el)
            assert [ln.plain for ln in frame2.lines] == ["> C", "  D"]
        finally:
            cap.cleanup()

    def test_focus_false_not_consumed(self):
        calls = []
        cap = _Router()
        try:
            el = h(SelectInput, {"items": ["A", "B"], "focus": False, "onSelect": lambda it: calls.append(it)})
            _render(el)
            assert cap.router is None or cap.key("enter") is False
            assert calls == []
        finally:
            cap.cleanup()

    def test_empty_items_no_crash(self):
        frame, _, _ = _render(h(SelectInput, {"items": []}))
        assert all(not ln.plain for ln in frame.lines)

    def test_highlight_style(self):
        from src.tui.core.style import Style
        el = h(SelectInput, {"items": ["A"], "highlightStyle": Style(fg=9, bold=True)})
        frame, _, _ = _render(el)
        assert frame.lines[0].runs[0].style == Style(fg=9, bold=True)


# ═══════════════════════════════════════════════════════════
# TextInput
# ═══════════════════════════════════════════════════════════


class TestTextInput:
    def test_placeholder_when_empty(self):
        frame, _, _ = _render(h(TextInput, {"value": "", "placeholder": "type here"}))
        assert frame.lines[0].plain == "type here"

    def test_render_value_with_cursor(self):
        frame, _, _ = _render(h(TextInput, {"value": "hello"}))
        assert frame.lines[0].plain == "hello "

    def test_same_batch_typing_accumulates(self):
        """同一渲染批次连续字符输入完整累积（闭包陈旧修复）。"""
        got = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "", "onChange": got.append})
            _, r, root = _render(el)
            for ch in "hello":
                assert cap.key("char", ch) is True
            assert got == ["h", "he", "hel", "hell", "hello"]  # 逐步累积
            # 重渲染后显示完整文本
            frame = _rerender(r, root, el)
            assert frame.lines[0].plain == "hello "
        finally:
            cap.cleanup()

    def test_backspace_delete(self):
        got = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "abc", "onChange": got.append})
            _, r, root = _render(el)
            # 初始光标在末尾
            cap.key("backspace")
            assert got[-1] == "ab"
            # delete 删除光标后字符（光标在末尾 → 无效果）
            cap.key("delete")
            assert got[-1] == "ab"
            # 光标移到开头，delete 删除首字符
            cap.key("home")
            cap.key("delete")
            assert got[-1] == "b"
        finally:
            cap.cleanup()

    def test_cursor_movement_and_insert(self):
        got = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "ab", "onChange": got.append})
            _, r, root = _render(el)
            cap.key("arrow_left")
            cap.key("char", "X")
            assert got[-1] == "aXb"
            cap.key("end")
            cap.key("char", "Y")
            assert got[-1] == "aXbY"
        finally:
            cap.cleanup()

    def test_enter_submit(self):
        submitted = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "query", "onSubmit": submitted.append})
            _, r, root = _render(el)
            assert cap.key("enter") is True
            assert submitted == ["query"]
        finally:
            cap.cleanup()

    def test_mask_hides_text(self):
        cap = _Router()
        try:
            el = h(TextInput, {"value": "secret", "mask": "*"})
            frame, _, _ = _render(el)
            plain = "".join(ln.plain for ln in frame.lines)
            assert plain == "****** "  # 掩码 + 光标
        finally:
            cap.cleanup()

    def test_focus_false_not_consumed(self):
        got = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "", "focus": False, "onChange": got.append})
            _render(el)
            assert cap.router is None or cap.key("char", "x") is False
            assert got == []
        finally:
            cap.cleanup()

    def test_external_value_sync(self):
        """外部受控 value 变化同步覆盖内部缓冲。"""
        got = []
        cap = _Router()
        try:
            el = h(TextInput, {"value": "external", "onChange": got.append})
            _, r, root = _render(el)
            # 渲染一次后外部值变化（模拟父组件受控更新）
            el2 = h(TextInput, {"value": "new", "onChange": got.append})
            _rerender(r, root, el2)  # 本帧 effect 提交 set_text
            frame2 = _rerender(r, root, el2)  # 下一帧应用 state queue
            assert frame2.lines[0].plain == "new "
        finally:
            cap.cleanup()


# ═══════════════════════════════════════════════════════════
# MultiSelect
# ═══════════════════════════════════════════════════════════


class TestMultiSelect:
    def test_render_checked_state(self):
        el = h(MultiSelect, {"items": ["A", "B", "C"], "initialValues": ["B"]})
        frame, _, _ = _render(el)
        assert [ln.plain for ln in frame.lines] == ["○ A", "● B", "○ C"]

    def test_space_toggle_and_submit(self):
        submitted = []
        cap = _Router()
        try:
            el = h(MultiSelect, {"items": ["A", "B", "C"], "onSubmit": submitted.append})
            _, r, root = _render(el)
            cap.key("char", " ")  # 选中 A
            cap.key("arrow_down")  # 光标 → B
            cap.key("char", " ")  # 选中 B
            cap.key("char", " ")  # 取消 B
            cap.key("enter")
            assert submitted == [["A"]]
        finally:
            cap.cleanup()

    def test_same_batch_consecutive_toggles(self):
        submitted = []
        cap = _Router()
        try:
            el = h(MultiSelect, {"items": ["A", "B", "C"], "onSubmit": submitted.append})
            _, r, root = _render(el)
            cap.key("char", " ")
            cap.key("char", " ")
            cap.key("char", " ")
            cap.key("enter")
            assert submitted == [["A"]]  # 三次 toggle → 选中 A（奇数）
        finally:
            cap.cleanup()

    def test_dict_items_value_order(self):
        items = [{"label": "One", "value": 1}, {"label": "Two", "value": 2}]
        submitted = []
        cap = _Router()
        try:
            el = h(MultiSelect, {"items": items, "onSubmit": submitted.append})
            _, r, root = _render(el)
            cap.key("char", " ")
            cap.key("arrow_down")
            cap.key("char", " ")
            cap.key("enter")
            assert submitted == [[1, 2]]  # 保持 items 顺序
        finally:
            cap.cleanup()

    def test_focus_false_not_consumed(self):
        submitted = []
        cap = _Router()
        try:
            el = h(MultiSelect, {"items": ["A"], "focus": False, "onSubmit": submitted.append})
            _render(el)
            assert cap.router is None or cap.key("enter") is False
            assert submitted == []
        finally:
            cap.cleanup()


# ═══════════════════════════════════════════════════════════
# ConfirmInput
# ═══════════════════════════════════════════════════════════


class TestConfirmInput:
    def test_render_label(self):
        frame, _, _ = _render(h(ConfirmInput, {}))
        assert frame.lines[0].plain == "(y/n)"

    def test_custom_label(self):
        frame, _, _ = _render(h(ConfirmInput, {"label": "Continue? (y/n)"}))
        assert frame.lines[0].plain == "Continue? (y/n)"

    def test_yes_no_enter_escape(self):
        got = []
        cap = _Router()
        try:
            el = h(ConfirmInput, {"onConfirm": got.append})
            _, r, root = _render(el)
            assert cap.key("char", "y") is True
            assert cap.key("char", "N") is True
            assert cap.key("enter") is True
            assert cap.key("escape") is True
            assert got == [True, False, True, False]
        finally:
            cap.cleanup()

    def test_other_keys_released(self):
        got = []
        cap = _Router()
        try:
            el = h(ConfirmInput, {"onConfirm": got.append})
            _, r, root = _render(el)
            assert cap.key("char", "x") is False
            assert got == []
        finally:
            cap.cleanup()

    def test_custom_keys(self):
        got = []
        cap = _Router()
        try:
            el = h(ConfirmInput, {"onConfirm": got.append, "yesKeys": ("ok",), "noKeys": ("no",)})
            _, r, root = _render(el)
            assert cap.key("char", "ok") is True
            assert cap.key("char", "no") is True
            assert got == [True, False]
        finally:
            cap.cleanup()

    def test_focus_false_not_consumed(self):
        got = []
        cap = _Router()
        try:
            el = h(ConfirmInput, {"focus": False, "onConfirm": got.append})
            _render(el)
            assert cap.router is None or cap.key("char", "y") is False
            assert got == []
        finally:
            cap.cleanup()
