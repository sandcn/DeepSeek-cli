"""src/tui/_ink_bridge + _ink_bridge_compat — InkBridge 桥接层单元测试。

覆盖（_ink_bridge.py）：
  - 状态域：set_model_name/enable/disable_status/reset_tool_count/
    increment_tool/decrement_tool/increment_tool_fail/set_main_phase/
    get_status_elapsed
  - 补全域：show/hide/cycle/get_selected_completion/get_selected_completion_index
  - 输入域 / subagent 域 / _request_redraw 容错

覆盖（_ink_bridge_compat.py）：
  - _BottomBarCompatMixin 兼容字段（_last_text/_bottom_lines/_completion_idx/
    _completion 代理/force_redraw）与生命周期 no-op
  - _completion_idx setter 双向钳制（负值/越界）
"""

from __future__ import annotations

import pytest

import src.tui._ink_bridge as ib
from src.tui._ink_bridge import InkBridge
from src.tui._ink_bridge_compat import _BottomBarCompatMixin, _CompletionProxy
from src.tui.app._state_types import CompletionState, StatusState


class _FakeModel:
    """InkBridge 依赖的最小模型桩（status/completion/input/subagent）。"""

    def __init__(self):
        self.status = StatusState()
        self.completion = CompletionState()
        self.input_text = ""
        self.input_cursor = 0
        self.subagent_lines = []


class _FakeSession:
    def __init__(self):
        self.redraws = 0

    def request_bottom_redraw(self):
        self.redraws += 1


@pytest.fixture
def bridge():
    model = _FakeModel()
    session = _FakeSession()
    return InkBridge(model, session), model, session


# ── 状态域 ───────────────────────────────────────────────

def test_set_model_name(bridge):
    b, m, _ = bridge
    b.set_model_name("deepseek-v4")
    assert m.status.model_name == "deepseek-v4"


def test_enable_disable_status(bridge):
    b, m, _ = bridge
    b.enable_status()
    assert m.status.status_active is True
    b.disable_status()
    assert m.status.status_active is False


def test_reset_tool_count(bridge):
    b, m, _ = bridge
    m.status.tool_count = 3
    m.status.tool_fail = 2
    m.status.tool_total = 5
    m.status.tool_phase_start = 9.0
    b.reset_tool_count()
    assert m.status.tool_count == 0
    assert m.status.tool_fail == 0
    assert m.status.tool_total == 0
    assert m.status.tool_phase_start == 0.0


def test_increment_decrement_tool(bridge):
    b, m, _ = bridge
    b.increment_tool()
    assert m.status.tool_count == 1
    b.increment_tool()
    assert m.status.tool_count == 2
    b.decrement_tool()
    assert m.status.tool_count == 1


def test_increment_tool_fail(bridge):
    b, m, _ = bridge
    b.increment_tool_fail()
    assert m.status.tool_fail == 1


def test_set_main_phase_changes_start(bridge, monkeypatch):
    b, m, _ = bridge
    monkeypatch.setattr(ib.time, "monotonic", lambda: 123.0)
    b.set_main_phase("working")
    assert m.status.main_phase == "working"
    assert m.status.main_phase_start == 123.0
    # 相同 phase 不重置 start
    monkeypatch.setattr(ib.time, "monotonic", lambda: 999.0)
    b.set_main_phase("working")
    assert m.status.main_phase_start == 123.0


def test_get_status_elapsed_via_snapshot(bridge, monkeypatch):
    b, _, _ = bridge

    def fake_snapshot():
        return {"elapsed_seconds": 42.5}

    # get_status_elapsed 在函数体内 ``from src.tui._snapshot import _get_snapshot``
    import src.tui._snapshot as snap_mod

    monkeypatch.setattr(snap_mod, "_get_snapshot", lambda: fake_snapshot)
    assert b.get_status_elapsed() == 42.5


def test_get_status_elapsed_no_snapshot(bridge, monkeypatch):
    b, _, _ = bridge
    import src.tui._snapshot as snap_mod

    monkeypatch.setattr(snap_mod, "_get_snapshot", lambda: None)
    assert b.get_status_elapsed() == 0.0


def test_get_status_elapsed_exception_silent(bridge, monkeypatch):
    b, _, _ = bridge
    import src.tui._snapshot as snap_mod

    monkeypatch.setattr(
        snap_mod, "_get_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("x")),
    )
    assert b.get_status_elapsed() == 0.0


# ── 补全域 ───────────────────────────────────────────────

def test_show_completions_populates(bridge):
    b, m, _ = bridge
    b.show_completions(
        ["item1", "item2"], selected_idx=1, texts=["t1", "t2"],
        start_pos=3, orig_prefix="/", title="命令", types=["cmd"],
        match_prefix="co", descriptions=["desc1", "desc2"], split_desc=True,
    )
    c = m.completion
    assert c.visible is True
    assert c.items == ["item1", "item2"]
    assert c.texts == ["t1", "t2"]
    assert c.selected == 1
    assert c.start_pos == 3
    assert c.orig_prefix == "/"
    assert c.title == "命令"
    assert c.types == ["cmd"]
    assert c.match_prefix == "co"
    assert c.descriptions == ["desc1", "desc2"]
    assert c.split_desc is True


def test_show_completions_empty_noop(bridge):
    b, m, _ = bridge
    b.show_completions([], 0)
    assert m.completion.visible is False


def test_show_completions_selected_clamped(bridge):
    b, m, _ = bridge
    b.show_completions(["a", "b", "c"], selected_idx=-5)
    assert m.completion.selected == 0
    b.show_completions(["a", "b", "c"], selected_idx=99)
    assert m.completion.selected == 2


def test_hide_completions_resets(bridge):
    b, m, _ = bridge
    b.show_completions(["a", "b"], selected_idx=1)
    b.hide_completions()
    c = m.completion
    assert c.visible is False
    assert c.items == []
    assert b._last_completion_idx == 1  # 隐藏前索引保留


def test_hide_completions_when_hidden_noop(bridge):
    b, m, _ = bridge
    b.hide_completions()  # 已隐藏 → 无副作用
    # _last_completion_idx 尚未创建（getattr 兜底 0）
    assert getattr(b, "_last_completion_idx", 0) == 0


def test_cycle_completion(bridge):
    b, m, _ = bridge
    b.show_completions(["a", "b", "c"], selected_idx=0)
    assert b.cycle_completion(1) == 1
    assert b.cycle_completion(1) == 2
    assert b.cycle_completion(1) == 0  # 回绕
    assert b.cycle_completion(-1) == 2


def test_cycle_completion_hidden_returns_0(bridge):
    b, _, _ = bridge
    assert b.cycle_completion(1) == 0


def test_get_selected_completion(bridge):
    b, _, _ = bridge
    b.show_completions(["a", "b"], selected_idx=0, texts=["TA", "TB"], start_pos=2, orig_prefix="/x")
    assert b.get_selected_completion() == ("TA", 2, "/x")


def test_get_selected_completion_hidden_default(bridge):
    b, _, _ = bridge
    assert b.get_selected_completion() == ("", 0, "")


def test_get_selected_completion_index_visible(bridge):
    b, _, _ = bridge
    b.show_completions(["a", "b", "c"], selected_idx=2)
    assert b.get_selected_completion_index() == 2


def test_get_selected_completion_index_hidden_uses_last(bridge):
    b, _, _ = bridge
    b.show_completions(["a", "b", "c"], selected_idx=1)
    b.hide_completions()
    assert b.get_selected_completion_index() == 1


# ── 输入 / subagent / 重绘 ───────────────────────────────

def test_set_input_state(bridge):
    b, m, s = bridge
    b.set_input_state("hello", 3)
    assert m.input_text == "hello"
    assert m.input_cursor == 3
    assert s.redraws == 1


def test_set_subagent_frame(bridge):
    b, m, _ = bridge
    b.set_subagent_frame(["line1", "line2"])
    assert m.subagent_lines == ["line1", "line2"]


def test_request_redraw_exception_silent(bridge, monkeypatch):
    b, _, s = bridge

    def boom():
        raise RuntimeError("session died")

    s.request_bottom_redraw = boom  # type: ignore[method-assign]
    b._request_redraw()  # 不抛异常


# ── _BottomBarCompatMixin ────────────────────────────────

def test_compat_lifecycle_noops(bridge):
    b, _, _ = bridge
    assert b.is_active is True
    b.set_active(False)  # no-op
    b.setup()
    b.teardown()
    b.ensure_cursor_in_upper()
    b.ensure_cursor_in_lower()
    assert b._MIN_HEIGHT == 12
    assert b._bottom_lines == 5
    assert b._last_bottom_lines == 5


def test_compat_last_text_roundtrip(bridge):
    b, m, s = bridge
    b._last_text = "abc"
    assert m.input_text == "abc"
    assert b._last_text == "abc"
    assert s.redraws >= 1


def test_compat_completion_idx_clamped(bridge):
    b, m, _ = bridge
    m.completion.items = ["a", "b", "c"]
    b._completion_idx = -3
    assert m.completion.selected == 0
    b._completion_idx = 99
    assert m.completion.selected == 2
    m.completion.items = []
    b._completion_idx = 5
    assert m.completion.selected == 0


def test_compat_completion_proxy(bridge):
    b, m, _ = bridge
    proxy = b._completion
    assert isinstance(proxy, _CompletionProxy)
    proxy._visible = True
    assert m.completion.visible is True
    proxy._items = ["x", "y"]
    assert m.completion.items == ["x", "y"]
    proxy._texts = ["tx", "ty"]
    assert m.completion.texts == ["tx", "ty"]
    proxy._popup_height = 7
    assert m.completion.popup_height == 7
    proxy._split_desc = True
    assert m.completion.split_desc is True


def test_compat_force_redraw(bridge):
    b, _, s = bridge
    b.force_redraw()
    assert s.redraws == 1


def test_compat_is_completion_visible_property(bridge):
    b, m, _ = bridge
    m.completion.visible = True
    assert b.is_completion_visible is True
    m.completion.visible = False
    assert b.is_completion_visible is False
