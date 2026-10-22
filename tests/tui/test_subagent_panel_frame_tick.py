"""subagent 面板推送归并到 render 帧（单次调用完成所有工作）。

事件处理器只置脏/待推送（不再在事件线程即时渲染面板），由 ``_panel_refresh``
（render 帧每拍一次）统一渲染推送；无帧驱动（``_cb_registered=False``）时
保留即时推送降级路径。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui._subagent_panel import SubAgentPanelController


def _event(**kwargs) -> SimpleNamespace:
    base = dict(
        label="agent-1", description="d", status="running",
        agent_type="execute", dispatch_label="", text="x",
        tool_name="t", arguments="{}", tool_id="tid",
        tool_names="t", tokens=1, elapsed=0.1, success=True,
        usage={}, replace=False, live_input_tokens=0, live_output_tokens=0,
        output_tokens=0, speed=0.0, phase="content", info="",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_event_only_marks_pending_when_frame_driven() -> None:
    pushed: list[list] = []
    ctrl = SubAgentPanelController(push_cmd=pushed.append)
    ctrl._cb_registered = True
    try:
        ctrl._on_agent_added(_event())
        assert pushed == []
        assert ctrl._pending_emit is True
        assert ctrl._dirty is True

        ctrl._panel_refresh()
        assert len(pushed) == 1
        assert ctrl._pending_emit is False
    finally:
        ctrl.stop(clear_panel=False)


def test_event_emits_immediately_without_frame_driver() -> None:
    pushed: list[list] = []
    ctrl = SubAgentPanelController(push_cmd=pushed.append)
    ctrl._cb_registered = False
    try:
        ctrl._on_agent_added(_event())
        assert len(pushed) == 1
    finally:
        ctrl.stop(clear_panel=False)
