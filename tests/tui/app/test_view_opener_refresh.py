"""全屏视图非输入刷新回归测试（2026-11-03）。

覆盖两类「与 /models 同根因」的不刷新路径：

1. ``open_fullscreen_view`` 的 ``on_tick`` 在命令线程直接改视图状态
   （刷新/重连/改键/导出结果/沙盒动作等）——不经 ``use_state``，空闲复用
   会一直显示旧内容。修复：tick 前后状态指纹变化即请求重绘。
2. logs / changes / sandbox 视图在渲染期经 ``use_data_refresher`` 调用
   ``model.<x>_refresher`` 写状态——刷新器报告重建时递增 state 计数，
   触发下一次调和（同为 idle_static 视图，否则旧数据停留到偶发脏事件）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.commands._view_opener import _state_fingerprint, open_fullscreen_view
from src.tui.app._view_common import use_data_refresher
from src.tui.ink import TEXT, h
from src.tui.ink._render_api import renderToString


class _State:
    """最小视图状态桩（visible/seq/deadline/done/action + 任意状态字段）。"""

    def __init__(self, visible=True, seq=0, deadline=0.0, **kwargs) -> None:
        self.visible = visible
        self.seq = seq
        self.deadline = deadline
        self.done = False
        self.action = ""
        for key, value in kwargs.items():
            setattr(self, key, value)

    def try_set_final(self, action: str) -> bool:
        if self.done:
            return False
        self.done = True
        self.action = action
        return True


def _open(monkeypatch, *, on_tick, timeout=10.0):
    from src.core.adapters import ui_runtime

    calls: list[int] = []
    model = SimpleNamespace(view_state=_State(), fullscreen="")
    chat_ui = SimpleNamespace(
        get_model=lambda: model,
        request_bottom_redraw=lambda: calls.append(1),
        flush_input_router=lambda timeout: None,
    )
    monkeypatch.setattr(ui_runtime, "get_active_chat_ui", lambda: chat_ui)
    assert open_fullscreen_view(
        None,
        view_id="demo",
        state_attr="view_state",
        state_cls=_State,
        on_tick=on_tick,
        timeout=timeout,
    ) is True
    return calls, model


def test_tick_state_mutation_requests_redraw(monkeypatch) -> None:
    """tick 改状态（未返回关闭）→ 额外请求一次重绘。"""

    def tick(state) -> bool:
        state.status_message = "已刷新"
        state.entries = [{"name": "a"}]
        state.try_set_final("done")
        return False

    calls, _model = _open(monkeypatch, on_tick=tick)
    # 初始 1 次 + tick 变更 1 次 + 清理 1 次
    assert len(calls) == 3


def test_noop_tick_does_not_request_extra_redraw(monkeypatch) -> None:
    """tick 无状态变化 → 不产生额外重绘（空闲复用不被破坏）。"""

    def tick(state) -> bool:
        return False

    calls, _model = _open(monkeypatch, on_tick=tick, timeout=0.0)
    # 初始 1 次 + 清理 1 次；tick 无变化不得请求重绘。
    assert len(calls) == 2


def test_state_fingerprint_detects_scalar_and_list_reassignment() -> None:
    state = _State(status_message="old", entries=[1, 2])
    before = _state_fingerprint(state)
    assert before is not None
    state.status_message = "new"
    assert _state_fingerprint(state) != before
    before = _state_fingerprint(state)
    state.entries = [1, 2, 3]
    assert _state_fingerprint(state) != before
    # 稳定对象（锁/未知对象 id）不误报。
    assert _state_fingerprint(state) == _state_fingerprint(state)


def test_export_view_state_has_applied_protocol_fields() -> None:
    """/export 的跨线程协议字段必须存在（缺失时 _apply 抛异常 → 导出无反应）。"""
    from src.tui.app._state_types import ExportViewState

    state = ExportViewState()
    assert state.applied_seq == 0
    assert state.applied is None
    state.applied = {"action": "export"}
    state.applied_seq += 1
    assert state.applied_seq == 1


def test_data_refresher_rerenders_external_state_in_same_render() -> None:
    """刷新器写外部状态并返回 True → 同帧后续 pass 渲染出新值。"""
    store = {"value": "old"}

    def refresher() -> bool:
        if store["value"] == "old":
            store["value"] = "new"
            return True
        return False

    def _view(_props):
        use_data_refresher(refresher, True)
        return h(TEXT, {"children": store["value"]})

    text = renderToString(h(_view, None), {"columns": 20})
    assert "new" in text
