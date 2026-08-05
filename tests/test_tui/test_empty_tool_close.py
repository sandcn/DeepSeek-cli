"""空工具卡自动闭合测试（┌─ ● ⚙ 工具）。

背景：后台 bash 任务完成后的提示（print_to_terminal 在工具上下文外回退
label/tool_id="assistant"）经工具输出路径会触发 ``append_tool_output``
兜底创建一个**只有顶边框、永不闭合**的空「工具」box（┌─ ● ⚙ 工具）。

修复（三层防御 + 轮次清理）：
1. ``_dispatcher._on_tool_output`` — 过滤 assistant 标签（无归属输出不进工具卡）
2. ``_tool_output_mixin.append_tool_output`` — assistant tool_id 直接丢弃（模型层双保险）
3. ``_tool_output_mixin.close_empty_tool_boxes`` — 每轮对话结束自动闭合遗留空 box
4. ``_session_setup._on_round_end`` — 调用 close_empty_tool_boxes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tui._const import RenderCommand, ToolOutputCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app.model import AppModel
from src.tui.events.event_types import ToolOutputChunkEvent


# ═══════════════════════════════════════════════════════════
# Dispatcher 过滤
# ═══════════════════════════════════════════════════════════

class TestDispatcherFiltersAssistantOutput:
    """无归属输出（label/tool_id=assistant）不作为工具输出推送。"""

    def test_assistant_output_filtered(self) -> None:
        """label 和 tool_id 都为 assistant → 不 push ToolOutputCmd。"""
        pushed = []
        dispatcher = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        dispatcher._on_tool_output(ToolOutputChunkEvent(
            label="assistant", tool_id="assistant",
            text="[后台任务 bg-x 已完成]", source="agent",
        ))
        assert pushed == []

    def test_real_tool_output_not_filtered(self) -> None:
        """真实工具输出（call_xxx）正常推送。"""
        pushed = []
        dispatcher = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        dispatcher._on_tool_output(ToolOutputChunkEvent(
            label="call_1", tool_id="call_1", text="hello", source="agent",
        ))
        assert len(pushed) == 1
        assert isinstance(pushed[0], ToolOutputCmd)
        assert pushed[0].tool_id == "call_1"

    def test_subagent_output_still_filtered(self) -> None:
        """subagent 输出（agent-N）仍被过滤（既有行为不变）。"""
        pushed = []
        dispatcher = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        dispatcher._on_tool_output(ToolOutputChunkEvent(
            label="agent-1", tool_id="agent-1", text="diff", source="agent",
        ))
        assert pushed == []


# ═══════════════════════════════════════════════════════════
# append_tool_output 模型层防御
# ═══════════════════════════════════════════════════════════

class TestAppendToolOutputDefense:
    """append_tool_output 对 assistant 输出直接丢弃（不创建空 box）。"""

    def test_assistant_output_dropped(self) -> None:
        """tool_id=assistant 不创建 box。"""
        model = AppModel()
        model.append_tool_output("assistant", "[后台任务 bg-x 已完成]")
        assert "assistant" not in model.tool_boxes
        assert not model.tool_boxes

    def test_unknown_id_creates_box(self) -> None:
        """非 assistant 的未知 tool_id 仍兜底创建 box（输出先于 start 时序兼容）。"""
        model = AppModel()
        model.append_tool_output("call_9", "late output")
        assert "call_9" in model.tool_boxes


# ═══════════════════════════════════════════════════════════
# close_empty_tool_boxes 自动闭合
# ═══════════════════════════════════════════════════════════

class TestCloseEmptyToolBoxes:
    """自动闭合开放但无主体的空工具 box。"""

    def test_closes_empty_box(self) -> None:
        """只有标题行的空 box 被闭合。"""
        model = AppModel()
        model.open_tool_box("orphan_1", "")  # 只有标题行
        assert "orphan_1" in model.tool_boxes
        closed = model.close_empty_tool_boxes()
        assert closed == 1
        assert "orphan_1" not in model.tool_boxes

    def test_keeps_box_with_content(self) -> None:
        """有主体内容的 box 不误闭合。"""
        model = AppModel()
        model.open_tool_box("real_1", "bash", "echo hi")
        model.append_tool_output("real_1", "real output")
        closed = model.close_empty_tool_boxes()
        assert closed == 0
        assert "real_1" in model.tool_boxes

    def test_closed_box_untouched(self) -> None:
        """已关闭的 box 不受影响。"""
        model = AppModel()
        model.open_tool_box("done_1", "bash", "echo hi")
        model.close_tool_box("done_1", True)
        closed = model.close_empty_tool_boxes()
        assert closed == 0

    def test_no_boxes_noop(self) -> None:
        """无 box 时返回 0。"""
        model = AppModel()
        assert model.close_empty_tool_boxes() == 0


# ═══════════════════════════════════════════════════════════
# _on_round_end 调用 close_empty_tool_boxes
# ═══════════════════════════════════════════════════════════

class TestRoundEndClosesEmptyTools:
    """round_end 回调自动闭合空工具 box。"""

    def _make_round_end(self):
        """构造 _on_round_end 回调（chat_ui 为 mock，_loop_mode 跳过状态栏逻辑）。"""
        from src.app_loop._session_setup import _make_round_callbacks
        session = MagicMock()
        session.pending_messages = []
        monitor = MagicMock()
        # _loop_mode=True：跳过底部栏冻结/桌面通知逻辑，聚焦空工具闭合
        loop_state = {"_loop_mode": True}
        chat_ui = MagicMock()
        # mock input.drain_all / drain_captured 返回空
        chat_ui._components.input.drain_all.return_value = (None, "")
        chat_ui._components.input.drain_captured.return_value = ""
        callbacks = _make_round_callbacks(session, monitor, loop_state, chat_ui)
        return callbacks, chat_ui

    def test_round_end_calls_close_empty_tool_boxes(self) -> None:
        """_on_round_end 调用模型 close_empty_tool_boxes。"""
        callbacks, chat_ui = self._make_round_end()
        # mock model：记录 close_empty_tool_boxes 调用
        model = MagicMock()
        model.close_empty_tool_boxes.return_value = 1
        chat_ui.get_model.return_value = model

        callbacks["on_end"](interrupted=False, delta={"input": 1, "output": 1})

        model.close_empty_tool_boxes.assert_called_once()

    def test_round_end_model_none_no_error(self) -> None:
        """chat_ui.get_model() 返回 None 不抛异常。"""
        callbacks, chat_ui = self._make_round_end()
        chat_ui.get_model.return_value = None

        # 不抛异常
        callbacks["on_end"](interrupted=False, delta={"input": 1, "output": 1})

    def test_round_end_no_model_method_no_error(self) -> None:
        """模型无 close_empty_tool_boxes 方法不抛异常（防御）。"""
        callbacks, chat_ui = self._make_round_end()
        chat_ui.get_model.return_value = MagicMock(spec=["blocks"])  # 无该方法

        callbacks["on_end"](interrupted=False, delta={"input": 1, "output": 1})
