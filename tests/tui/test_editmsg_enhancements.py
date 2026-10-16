"""editmsg 增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 消息行元信息增强（序号·轮次·字符/行数·时间）；
  2. 消息全文预览（``_msg_preview_text`` + 弹窗预览区渲染）；
  3. 弹窗内搜索过滤（``/`` 进入、字符累积、Esc 清除、Enter 保留、
     过滤后列表只显示匹配项、选中换算回原始索引）；
  4. 导航增强（PgUp/PgDn/Home/End + 标题 (n/total)）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.app.editmsg_select import (
    EditMsgSelectPopup,
    _filter_indices,
)
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, InputHook
from src.tui.pipeline.message_editor import (
    _build_selection_items,
    _msg_preview_text,
    _user_msg_summary,
)

# ═══════════════════════════════════════════════════════════
# 通用辅助
# ═══════════════════════════════════════════════════════════


def _render_popup(model, width=80):
    """手动 fiber 上下文渲染 EditMsgSelectPopup，返回 (fiber, element)。"""
    fiber = Fiber(TAG_FUNCTION, EditMsgSelectPopup, {"model": model, "width": width})
    hooks._push_current(fiber)
    try:
        el = EditMsgSelectPopup({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _input_handler(fiber):
    """fiber 上第一个活跃 use_input handler。"""
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(
        kind=kind, char=char, modifier=0, keycode=0, raw=b"",
        kitty_bits=-1, event_type="",
    )


def _texts(el) -> list:
    return [str(c.props.get("children", "")) for c in el.children]


def _select_control(el):
    """弹窗中的 SelectInput 元素。"""
    for c in el.children:
        if getattr(c, "type", "") and "select" in str(c.type).lower():
            return c
    # 回退：第二个子元素（标题之后）
    return el.children[1]


# ═══════════════════════════════════════════════════════════
# 1. 消息行元信息增强
# ═══════════════════════════════════════════════════════════


class TestMsgSummaryMeta:

    def test_index_round_chars_lines(self):
        line = _user_msg_summary({"role": "user", "content": "a\nb\nc"}, 0)
        assert line.startswith("1. t1 \u25cf \u2502")
        assert "3\u884c" in line

    def test_round_number_follows_index(self):
        line = _user_msg_summary({"role": "user", "content": "x"}, 4)
        assert line.startswith("5. t5 \u25cf \u2502")

    def test_time_field_shown(self):
        import time as _time
        ts = _time.time() - 60
        line = _user_msg_summary({"role": "user", "content": "x", "timestamp": ts}, 0)
        expected = _time.strftime("%H:%M:%S", _time.localtime(ts))
        assert expected in line

    def test_time_string_field(self):
        line = _user_msg_summary(
            {"role": "user", "content": "x", "time": "12:34:56"}, 0,
        )
        assert "12:34:56" in line

    def test_single_line(self):
        line = _user_msg_summary({"role": "user", "content": "a\nb"}, 0)
        assert "\n" not in line


# ═══════════════════════════════════════════════════════════
# 2. 消息全文预览
# ═══════════════════════════════════════════════════════════


class TestMsgPreview:

    def test_preview_keeps_newlines(self):
        text = _msg_preview_text({"role": "user", "content": "a\nb"})
        assert text == "a\nb"

    def test_preview_empty_placeholder(self):
        assert _msg_preview_text({"role": "user", "content": "  "}) == "(\u7a7a\u6d88\u606f)"

    def test_preview_truncates_long(self):
        from src.tui.pipeline.message_editor import _PREVIEW_MAX_CHARS
        long_text = "x" * (_PREVIEW_MAX_CHARS + 500)
        out = _msg_preview_text({"role": "user", "content": long_text})
        assert out.startswith("x" * 100)
        assert "\u5185\u5bb9\u8fc7\u957f" in out

    def test_build_selection_items_align(self):
        msgs = [(0, {"role": "user", "content": "hi"}),
                (2, {"role": "user", "content": "yo\nho"})]
        display, previews = _build_selection_items(msgs)
        assert len(display) == len(previews) == 2
        assert display[0].startswith("1. t1")
        assert previews[1] == "yo\nho"

    def test_popup_renders_preview_area(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["1. A", "2. B"],
            previews=["full-A", "full-B\nline2"], selected=1,
        )
        _fiber, el = _render_popup(model)
        texts = "\n".join(_texts(el))
        assert "\u2500 \u9884\u89c8" in texts
        assert "full-B" in texts
        assert "line2" in texts

    def test_popup_preview_absent_without_data(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["1. A"], selected=0,
        )
        _fiber, el = _render_popup(model)
        texts = "\n".join(_texts(el))
        assert "\u2500 \u9884\u89c8" not in texts


# ═══════════════════════════════════════════════════════════
# 3. 弹窗内搜索过滤
# ═══════════════════════════════════════════════════════════


class TestPopupFilter:

    def test_filter_indices_case_insensitive(self):
        options = ["Hello", "world", "HELLO again"]
        assert _filter_indices(options, "") == [0, 1, 2]
        assert _filter_indices(options, "hello") == [0, 2]
        assert _filter_indices(options, "zzz") == []

    def test_filter_narrows_items(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择",
            options=["alpha", "beta", "gamma"], selected=0,
        )
        model.editmsg_select.filter = "a"
        _fiber, el = _render_popup(model)
        control = _select_control(el)
        # alpha / beta / gamma 均含 "a" → 全保留
        assert len(control.props["items"]) == 3
        model.editmsg_select.filter = "et"
        _fiber, el = _render_popup(model)
        control = _select_control(el)
        assert [it["value"] for it in control.props["items"]] == [1]

    def test_filter_selected_outside_view_clamps(self):
        model = AppModel()
        es = EditMsgSelectState(
            visible=True, seq=1, title="选择",
            options=["alpha", "beta", "gamma"], selected=2,
        )
        es.filter = "et"  # 只剩 beta（原索引 1）
        model.editmsg_select = es
        _fiber, el = _render_popup(model)
        control = _select_control(el)
        assert control.props["index"] == 0
        assert es.selected == 1  # 选中同步回首项原始索引

    def test_search_mode_key_enters(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["a", "b"], selected=0,
        )
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler is not None
        assert handler(_ev("char", "/")) is True
        assert model.editmsg_select.search_mode is True

    def test_search_input_accumulates_and_esc_clears(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["a", "b"], selected=0,
        )
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        handler(_ev("char", "/"))
        assert handler(_ev("char", "a")) is True
        assert handler(_ev("char", "b")) is True
        assert model.editmsg_select.filter == "ab"
        assert handler(_ev("backspace")) is True
        assert model.editmsg_select.filter == "a"
        assert handler(_ev("escape")) is True
        assert model.editmsg_select.search_mode is False
        assert model.editmsg_select.filter == ""

    def test_search_enter_keeps_filter(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["a", "b"], selected=0,
        )
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        handler(_ev("char", "/"))
        handler(_ev("char", "a"))
        assert handler(_ev("enter")) is True
        assert model.editmsg_select.search_mode is False
        assert model.editmsg_select.filter == "a"

    def test_search_mode_disables_list_focus(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择", options=["a", "b"], selected=0,
        )
        model.editmsg_select.search_mode = True
        _fiber, el = _render_popup(model)
        control = _select_control(el)
        assert control.props["focus"] is False

    def test_on_select_maps_original_index(self):
        model = AppModel()
        es = EditMsgSelectState(
            visible=True, seq=1, title="选择",
            options=["alpha", "beta", "gamma"], selected=0,
        )
        es.filter = "et"
        model.editmsg_select = es
        _fiber, el = _render_popup(model)
        control = _select_control(el)
        on_select = control.props["onSelect"]
        on_select({"label": "beta", "value": 1})
        assert es.done is True and es.action == "confirmed"
        assert es.result == ["beta"]
        assert es.selected == 1


# ═══════════════════════════════════════════════════════════
# 4. 导航增强
# ═══════════════════════════════════════════════════════════


class TestPopupNavigation:

    def _model(self, count=10):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择",
            options=[f"m{i}" for i in range(count)], selected=0,
        )
        return model

    def test_home_end(self):
        model = self._model()
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("end")) is True
        assert model.editmsg_select.selected == 9
        assert handler(_ev("home")) is True
        assert model.editmsg_select.selected == 0

    def test_page_down_up(self):
        model = self._model(30)
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("page_down")) is True
        first = model.editmsg_select.selected
        assert first > 0
        assert handler(_ev("page_up")) is True
        assert model.editmsg_select.selected == 0

    def test_title_shows_position(self):
        model = self._model(5)
        model.editmsg_select.selected = 2
        _fiber, el = _render_popup(model)
        assert "(3/5)" in _texts(el)[0]

    def test_title_shows_filter_counts(self):
        model = self._model(5)
        model.editmsg_select.filter = "m1"
        _fiber, el = _render_popup(model)
        assert "\u8fc7\u6ee4" in _texts(el)[0]
