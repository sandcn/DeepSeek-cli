"""轨迹 Trace：subagent 完成后不再留下独立记录（2026-08-23）。

需求（用户）：subagent 运行完之后会在「轨迹 Trace」中留下
`🤖 ✔ sa-xxx · desc` 独立记录（``kind="subagent"``），该残留不被需要，
应删除——subagent 完成后主轨迹不再显示独立 subagent 记录；仅运行中的
subagent 显示 ● running；完成/失败/错误态跳过。

固化项：
  1. 完成的独立 subagent（无 tool 关联）在主轨迹中不再生成
     kind=subagent 记录；
  2. 运行中的独立 subagent 仍生成 kind=subagent（● running）；
  3. fail/error 终态同样被跳过；
  4. 带 dispatch_label 且消息源存在匹配 tool 记录的完成 subagent 仍
     走合并路径（并入 tool 记录，不降级为独立记录）。
"""

from __future__ import annotations

from src.tui._subagent_panel import SubAgentPanelController
from src.tui.app.trace import build_trace_records


class _FakeModel:
    """消息源模式主 Agent 模型（不含 subagent 调用的普通会话）。"""

    blocks = []
    status = "idle"
    fullscreen = "trace"

    def __init__(self, msgs=None):
        self._msgs = msgs or [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    @property
    def message_source(self):
        return lambda: self._msgs


def _make_ctrl():
    ctrl = SubAgentPanelController.get_default()
    if ctrl._active:
        ctrl.stop(clear_panel=False)
    ctrl._store.clear()
    ctrl.clear_trace_archive()
    return ctrl


def _cleanup(ctrl):
    if ctrl._active:
        ctrl.stop(clear_panel=False)
    ctrl._store.clear()
    ctrl.clear_trace_archive()


def _subagent_kinds(records):
    return [r for r in records if getattr(r, "kind", "") == "subagent"]


def test_finished_independent_subagent_not_in_main_trace():
    """完成的独立 subagent（status=done，无 tool 关联）不再生成 kind=subagent 记录。"""
    ctrl = _make_ctrl()
    try:
        ctrl.ensure_active()
        ctrl._store.add_agent(
            label="sa-done", description="完成后任务", status="done",
            agent_type="execute", dispatch_label="",
        )
        records, rows = build_trace_records(_FakeModel())
        sa_recs = _subagent_kinds(records)
        assert sa_recs == [], "完成的独立 subagent 不应留在主轨迹"
    finally:
        _cleanup(ctrl)


def test_running_independent_subagent_still_in_main_trace():
    """运行中的独立 subagent 仍生成 kind=subagent 记录（● running）。"""
    ctrl = _make_ctrl()
    try:
        ctrl.ensure_active()
        ctrl._store.add_agent(
            label="sa-run", description="运行中任务", status="running",
            agent_type="execute", dispatch_label="",
        )
        records, rows = build_trace_records(_FakeModel())
        sa_recs = _subagent_kinds(records)
        assert len(sa_recs) == 1
        assert getattr(sa_recs[0], "status", "") == "running"
        assert "sa-run" in (sa_recs[0].summary or "")
    finally:
        _cleanup(ctrl)


def test_fail_error_terminal_states_skipped():
    """fail/error 终态同样跳过（不再留下独立记录）。"""
    ctrl = _make_ctrl()
    try:
        ctrl.ensure_active()
        ctrl._store.add_agent(
            label="sa-fail", description="失败任务", status="fail",
            agent_type="execute", dispatch_label="",
        )
        ctrl._store.add_agent(
            label="sa-err", description="错误任务", status="error",
            agent_type="execute", dispatch_label="",
        )
        records, rows = build_trace_records(_FakeModel())
        assert _subagent_kinds(records) == []
    finally:
        _cleanup(ctrl)


def test_finished_subagent_with_dispatch_still_merges():
    """带 dispatch_label 且消息源存在匹配 tool 记录的完成 subagent 走合并路径。

    合并后 tool 记录带 subagent_label（可下钻），不降级为独立记录；
    合并路径不受「完成后跳过独立记录」影响。
    """
    ctrl = _make_ctrl()
    try:
        ctrl.ensure_active()
        msgs = [
            {"role": "user", "content": "派发一个任务"},
            {"role": "assistant", "content": "好的",
             "tool_calls": [{"id": "call-x", "type": "function",
                             "function": {"name": "subagent",
                                          "arguments": '{"description": "合并任务"}'}}]},
            {"role": "tool", "tool_call_id": "call-x",
             "content": '{"task_id": "sa-x", "status": "running"}'},
        ]
        ctrl._store.add_agent(
            label="sa-x", description="合并任务", status="done",
            agent_type="execute", dispatch_label="call-x",
        )
        records, rows = build_trace_records(_FakeModel(msgs))
        sa_recs = _subagent_kinds(records)
        assert sa_recs == [], "带 dispatch_label 的完成 subagent 不应生成独立记录"
        tools = [r for r in records if getattr(r, "kind", "") == "tool"]
        assert any(getattr(r, "subagent_label", "") == "sa-x" for r in tools), \
            "合并路径仍应保留（tool 记录带 subagent_label，可下钻）"
    finally:
        _cleanup(ctrl)
