"""视图组件拆分后的模块级逻辑测试（P1-1）。

背景：``ConfigView`` / ``UserSelectPopup`` / ``TraceView`` 三个巨型组件函数
（681 / 514 / 642 行）的「事件处理 / 协议回调 / 搜索辅助 / 子 JSON 编辑」
已提取为模块级函数（职责分离、可独立测试）。既有组件级测试（
``test_config_view.py`` 等）覆盖端到端行为；本文件直接对被提取的模块级
函数做单元测试，固化其行为契约。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app.config_view import (
    _cancel_edit,
    _json_container,
    _json_delete_selected,
    _json_entry_seg,
    _json_entry_value,
    _json_path_text,
    _persist_value,
)
from src.tui.app.trace_view import _clear_search
from src.tui.app.user_select import (
    _advance_to_next_pending,
    _handle_tab_event,
    _submit_all,
)


# ═══════════════════════════════════════════════════════════
# ConfigView 提取函数
# ═══════════════════════════════════════════════════════════


def _cv(**kwargs) -> SimpleNamespace:
    base = {
        "edit_error": "",
        "message": "",
        "editing": False,
        "edit_mode": "input",
        "edit_json_data": None,
        "edit_json_path": [],
        "edit_json_selected": 0,
        "edit_json_keys": [],
        "edit_json_action": "edit",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_persist_value_type_error_sets_edit_error():
    """类型校验失败：写 edit_error 且不持久化（返回 False）。"""
    cv = _cv()
    entry = {"type": int, "key": "temperature", "sensitive": False}
    assert _persist_value(cv, entry, "not-an-int") is False
    assert cv.edit_error


def test_persist_value_sensitive_empty_rejected():
    """敏感项空输入被拒（数据丢失防御）。"""
    cv = _cv()
    entry = {"type": str, "key": "api_key", "sensitive": True}
    assert _persist_value(cv, entry, "") is False
    assert "敏感" in cv.edit_error


def test_json_container_root_and_nested_navigation():
    """``_json_container`` 支持顶层与递归路径下钻；非法路径返回 None。"""
    data = {"a": {"b": [1, 2]}}
    cv = _cv(edit_json_data=data, edit_json_path=[])
    assert _json_container(cv) is data
    cv.edit_json_path = ["a"]
    assert _json_container(cv) == {"b": [1, 2]}
    cv.edit_json_path = ["a", "b"]
    assert _json_container(cv) == [1, 2]
    cv.edit_json_path = ["a", "b", "9"]
    assert _json_container(cv) is None  # 越界/非容器
    cv.edit_json_path = ["missing"]
    assert _json_container(cv) is None


def test_json_entry_value_and_seg():
    """条目值/路径段提取（dict 键名 / list 索引）。"""
    assert _json_entry_value({"k": 1}, 0) == 1
    assert _json_entry_value([10, 20], 1) == 20
    assert _json_entry_value([10], 5) is None
    assert _json_entry_seg({"k": 1}, 0) == "k"
    assert _json_entry_seg([10], 0) == "0"


def test_json_path_text_breadcrumb():
    """路径显示（空路径=顶层；非空=点分 breadcrumb）。"""
    assert _json_path_text(_cv()) == ""
    assert _json_path_text(_cv(edit_json_path=["a", "b", 2])) == "a.b.2"


def test_json_delete_selected_list_and_dict():
    """删除当前容器选中条目（list 元素 / dict 键），并钳制选中索引。"""
    cv = _cv(edit_json_data=[1, 2, 3], edit_json_selected=2)
    _json_delete_selected(cv)
    assert cv.edit_json_data == [1, 2]
    assert cv.edit_json_selected == 1

    cv = _cv(edit_json_data={"a": 1, "b": 2}, edit_json_selected=0,
             edit_json_keys=["a", "b"])
    _json_delete_selected(cv)
    assert cv.edit_json_data == {"b": 2}
    assert cv.edit_json_keys == ["b"]
    assert cv.edit_json_selected == 0


def test_cancel_edit_delegates_reset():
    """``_cancel_edit`` 委托 ``ConfigViewState.reset_edit_state``。"""
    called = []

    class _State:
        def reset_edit_state(self):
            called.append(True)

    _cancel_edit(_State())
    assert called == [True]


# ═══════════════════════════════════════════════════════════
# UserSelectPopup 提取函数
# ═══════════════════════════════════════════════════════════


class _SelState:
    def __init__(self, *, answered=False, done=False, action="", result=None,
                 default_options=None, title="Q"):
        self.answered = answered
        self.done = done
        self.action = action
        self.result = result or []
        self.default_options = default_options or []
        self.title = title
        self.final = None

    def try_set_final(self, action, result):
        if self.done:
            return False
        self.done = True
        self.action = action
        self.result = list(result)
        self.final = (action, list(result))
        return True


def test_submit_all_answers_and_defaults():
    """Submit：已回答按其 action/result，未回答取 default_options。"""
    answered = _SelState(answered=True, action="confirmed", result=["A"])
    pending = _SelState(default_options=["B"])
    _submit_all([answered, pending])
    assert answered.final == ("confirmed", ["A"])
    assert pending.final == ("confirmed", ["B"])


def test_submit_all_skips_done():
    """已 done 的问题在 Submit 时跳过（first-write-wins）。"""
    s = _SelState(done=True, action="cancel", result=["X"])
    _submit_all([s])
    assert s.action == "cancel"
    assert s.final is None


class _ActiveRef:
    def __init__(self, value=0):
        self.current = value


def test_handle_tab_event_submit_page_enter_submits():
    """Submit 页 Enter → 统一提交（消费）。"""
    states = [_SelState(default_options=["B"])]
    ref = _ActiveRef(1)
    set_active = []
    assert _handle_tab_event(
        SimpleNamespace(kind="enter", char=""), visible=True, is_submit=True,
        multi_mode=True, tab_count=2, active_ref=ref, set_active=set_active.append,
        states=states, model=SimpleNamespace(user_selects=states),
    ) is True
    assert states[0].done is True


def test_handle_tab_event_cycles_tabs():
    """多问题 Tab/←/→ 切换（环绕）。"""
    states = [_SelState(), _SelState()]
    ref = _ActiveRef(0)
    set_active = []
    ok = _handle_tab_event(
        SimpleNamespace(kind="tab", char="", modifier=0), visible=True,
        is_submit=False, multi_mode=True, tab_count=3, active_ref=ref,
        set_active=set_active.append, states=states,
        model=SimpleNamespace(user_selects=states),
    )
    assert ok is True and ref.current == 1 and set_active == [1]
    # 左移环绕：0 → 2（tab_count=3 时 (0-1)%3=2）
    ref.current = 0
    _handle_tab_event(
        SimpleNamespace(kind="arrow_left", char=""), visible=True,
        is_submit=False, multi_mode=True, tab_count=3, active_ref=ref,
        set_active=set_active.append, states=states,
        model=SimpleNamespace(user_selects=states),
    )
    assert ref.current == 2


def test_handle_tab_event_non_multi_and_invisible():
    """非多问题 / 不可见时不消费 tab 键。"""
    states = [_SelState()]
    ref = _ActiveRef(0)
    assert _handle_tab_event(
        SimpleNamespace(kind="tab", char="", modifier=0), visible=True,
        is_submit=False, multi_mode=False, tab_count=1, active_ref=ref,
        set_active=lambda i: None, states=states,
        model=SimpleNamespace(user_selects=states),
    ) is False
    assert _handle_tab_event(
        SimpleNamespace(kind="tab", char="", modifier=0), visible=False,
        is_submit=False, multi_mode=True, tab_count=2, active_ref=ref,
        set_active=lambda i: None, states=states,
        model=SimpleNamespace(user_selects=states),
    ) is False


def test_advance_to_next_pending_skips_answered():
    """自动推进到下一个未回答；全部已回答 → Submit tab（索引 n）。"""
    s0 = _SelState(answered=True, done=True)
    s1 = _SelState()
    model = SimpleNamespace(user_selects=[s0, s1])
    ref = _ActiveRef(0)
    set_active = []
    _advance_to_next_pending(model, ref, set_active.append)
    assert ref.current == 1 and set_active == [1]
    # 全部已回答 → Submit tab（索引 == n）
    s1.answered = True
    ref.current = 0
    set_active.clear()
    _advance_to_next_pending(model, ref, set_active.append)
    assert ref.current == 2 and set_active == [2]


# ═══════════════════════════════════════════════════════════
# TraceView 提取函数
# ═══════════════════════════════════════════════════════════


def test_clear_search_resets_state():
    """``_clear_search`` 复位搜索四字段。"""
    model = SimpleNamespace(
        trace_search_pattern="abc", trace_search_side="ledger",
        trace_search_matches=[1, 2], trace_search_idx=1,
    )
    _clear_search(model)
    assert model.trace_search_pattern == ""
    assert model.trace_search_side == ""
    assert model.trace_search_matches == []
    assert model.trace_search_idx == -1
