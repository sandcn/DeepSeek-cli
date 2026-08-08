"""测试 Bug A 修复 — 工具输出事件链路 tool_id 贯穿（步骤 1）。

覆盖：
  1. ToolOutputChunkEvent 新增 tool_id 字段（frozen dataclass 兼容默认值）
  2. EventDispatcher._on_tool_output 使用 event.tool_id or event.label 路由
  3. tools/base.print_to_terminal 从 contextvar 解析工具归属（未设置回退 "assistant"）
  4. SharedCapture.write 按 contextvar 定向分发（未命中广播兜底）
  5. ToolCallbackChain._run_tool_method 设置/重置 contextvar（含取消路径）
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import MagicMock

import pytest

from src.core.internal.agent import _tool_context
from src.core.internal.agent._tool_context import (
    get_current_tool_id,
    reset_current_tool_id,
    set_current_tool_id,
)


@pytest.fixture(autouse=True)
def _clean_tool_context():
    """测试前后清理 contextvar，防止泄漏污染后续测试。"""
    yield
    # 兜底：若测试中途异常导致 contextvar 残留，重置为默认值
    _tool_context._CURRENT_TOOL_ID.set("")
    _tool_context._CURRENT_TOOL_ID.reset(
        _tool_context._CURRENT_TOOL_ID.set("")
    )


@pytest.fixture(autouse=True)
def _reset_bus():
    """每个测试前后重置 DisplayEventBus 进程级单例，保证订阅计数可预测。"""
    from src.tui.events.event_bus import DisplayEventBus

    DisplayEventBus.reset_default()
    yield
    DisplayEventBus.reset_default()


class TestToolOutputEventField:
    """子步骤1 — ToolOutputChunkEvent 新增 tool_id 字段。"""

    def test_tool_output_event_has_tool_id_field(self):
        from src.tui.events.event_types import ToolOutputChunkEvent

        ev = ToolOutputChunkEvent(tool_id="call_1")
        assert ev.tool_id == "call_1"

    def test_tool_output_event_tool_id_default_empty(self):
        """既有发布方不传 tool_id 仍可构造（默认值兼容）。"""
        from src.tui.events.event_types import ToolOutputChunkEvent

        ev = ToolOutputChunkEvent(label="call_1", text="x")
        assert ev.tool_id == ""
        assert ev.label == "call_1"
        assert dataclasses.is_dataclass(ev)
        assert ev.__dataclass_params__.frozen  # frozen 不变式保持


class TestDispatcherRouting:
    """子步骤6 — _on_tool_output 使用 event.tool_id or event.label。"""

    def test_dispatcher_routes_by_tool_id(self):
        from src.tui._const import ToolOutputCmd
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolOutputChunkEvent

        pushed = []
        d = EventDispatcher(push_cmd=pushed.append)

        # 仅 label（兼容路径）→ tool_id 回退 label
        d._on_tool_output(ToolOutputChunkEvent(
            label="call_1", text="hello", source="agent",
        ))
        assert len(pushed) == 1
        assert isinstance(pushed[0], ToolOutputCmd)
        assert pushed[0].tool_id == "call_1"
        assert pushed[0].text == "hello"

        # tool_id 优先于 label
        pushed.clear()
        d._on_tool_output(ToolOutputChunkEvent(
            label="call_1", tool_id="call_2", text="world", source="agent",
        ))
        assert len(pushed) == 1
        assert pushed[0].tool_id == "call_2"
        assert pushed[0].text == "world"

    def test_dispatcher_routes_subagent_source_skipped(self):
        """非 agent 来源输出不进主内容 box（行为保持）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolOutputChunkEvent

        pushed = []
        d = EventDispatcher(push_cmd=pushed.append)
        d._on_tool_output(ToolOutputChunkEvent(
            label="agent-1", tool_id="agent-1", text="sub", source="agent-1",
        ))
        assert pushed == []

    def test_dispatcher_filters_subagent_label_with_agent_source(self):
        """BUG-63：subagent 工具输出（source=agent 但 label=agent-N）不进主内容 box。

        subagent 工具执行期间 ``_publish_tool_text``/``SharedCapture.write``
        统一发布 ``ToolOutputChunkEvent(source="agent")``，但 subagent 上下文
        ``get_current_tool_id()`` 为 ``self.label``（``agent-N``）。修复前这些
        事件被误判为主 agent 输出 → ``append_tool_output`` 兜底创建永不关闭的
        工具 box，subagent 每次写文件主聊天区累积大 diff 开放 box（文档爆炸 +
        全量刷新闪烁）。
        """
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolOutputChunkEvent

        pushed = []
        d = EventDispatcher(push_cmd=pushed.append)
        # label=agent-N（subagent 工具输出）→ 丢弃，不进主聊天区
        d._on_tool_output(ToolOutputChunkEvent(
            label="agent-1", tool_id="agent-1", text="sub diff\n", source="agent",
        ))
        assert pushed == []

        # tool_id=agent-N（仅 tool_id 是 subagent label）→ 同样丢弃
        d._on_tool_output(ToolOutputChunkEvent(
            label="agent-1", tool_id="agent-1", text="sub diff\n", source="agent",
        ))
        assert pushed == []

        # 主 agent 工具输出（tool_id=call_xxx）→ 正常进入主聊天区
        d._on_tool_output(ToolOutputChunkEvent(
            label="call_main", tool_id="call_main", text="main diff\n", source="agent",
        ))
        assert len(pushed) == 1
        from src.tui._const import ToolOutputCmd
        assert isinstance(pushed[0], ToolOutputCmd)
        assert pushed[0].tool_id == "call_main"


class TestPrintToTerminalContext:
    """子步骤4 — print_to_terminal 从 contextvar 解析工具归属。"""

    @pytest.mark.asyncio
    async def test_print_to_terminal_resolves_tool_id_from_context(self):
        from src.tools.base import print_to_terminal
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events.event_types import ToolOutputChunkEvent

        bus = DisplayEventBus.get_default()
        captured = []
        handler = captured.append
        bus.subscribe(handler, ToolOutputChunkEvent)
        try:
            # 未设置 contextvar → 回退 "assistant"（兼容旧行为）
            await print_to_terminal("hello")
            assert len(captured) == 1
            assert captured[0].label == "assistant"
            assert captured[0].tool_id == "assistant"
            assert captured[0].text == "hello"

            # 设置 contextvar → 解析为 context 值
            captured.clear()
            token = set_current_tool_id("call_x")
            try:
                await print_to_terminal("world")
            finally:
                reset_current_tool_id(token)
            assert len(captured) == 1
            assert captured[0].label == "call_x"
            assert captured[0].tool_id == "call_x"

            # 显式 tool_id 参数优先于 contextvar
            captured.clear()
            token2 = set_current_tool_id("call_ctx")
            try:
                await print_to_terminal("explicit", tool_id="call_explicit")
            finally:
                reset_current_tool_id(token2)
            assert len(captured) == 1
            assert captured[0].label == "call_explicit"
            assert captured[0].tool_id == "call_explicit"
        finally:
            bus.unsubscribe(handler, ToolOutputChunkEvent)


class TestSharedCaptureDirectedDispatch:
    """子步骤5 — SharedCapture.write 按 label 定向分发。"""

    def test_shared_capture_directed_dispatch(self):
        from src.core.internal.agent._capture_manager import SharedCapture

        event_port = MagicMock()
        capture = SharedCapture(
            tool_labels=["call_A", "call_B"],
            real_stdout=MagicMock(),
            bus=event_port,
        )

        # contextvar 命中 → 仅定向发布给 call_A（O(1) 事件）
        token = set_current_tool_id("call_A")
        try:
            capture.write("hello\n")
        finally:
            reset_current_tool_id(token)
        assert event_port.publish_event.call_count == 1
        event = event_port.publish_event.call_args[0][0]
        assert event.label == "call_A"
        assert event.tool_id == "call_A"

        # 未设置 contextvar → 广播两 label（兜底保留旧行为）
        event_port.publish_event.reset_mock()
        capture.write("hello\n")
        assert event_port.publish_event.call_count == 2
        labels = [c.args[0].label for c in event_port.publish_event.call_args_list]
        assert set(labels) == {"call_A", "call_B"}
        assert all(
            c.args[0].tool_id == c.args[0].label
            for c in event_port.publish_event.call_args_list
        )

    def test_shared_capture_blank_write_skipped(self):
        """空白写入不发布事件（行为保持）。"""
        from src.core.internal.agent._capture_manager import SharedCapture

        event_port = MagicMock()
        capture = SharedCapture(
            tool_labels=["call_A"],
            real_stdout=MagicMock(),
            bus=event_port,
        )
        capture.write("   \n")
        event_port.publish_event.assert_not_called()


class TestRunToolMethodContext:
    """子步骤3 — _run_tool_method 设置/重置 contextvar。"""

    @pytest.mark.asyncio
    async def test_run_tool_method_sets_context(self):
        from src.core.internal.agent._tool_callbacks import ToolCallbackChain

        seen = []
        func = MagicMock()
        func.tool_label = None

        async def fake_display():
            seen.append(get_current_tool_id())
            return "ok"

        func.display = fake_display

        agent = MagicMock()
        agent._display_port = MagicMock()
        agent._display_port.is_web = False
        agent._capture_mgr = MagicMock()

        chain = ToolCallbackChain(agent)
        tc = {"id": "call_1", "name": "bash"}
        result = await chain._run_tool_method(func, tc)
        assert result == "ok"
        assert seen == ["call_1"]  # 执行期间 contextvar 为 tc.id
        assert get_current_tool_id() == ""  # 执行后重置
        assert func.tool_label == "call_1"

    @pytest.mark.asyncio
    async def test_run_tool_method_resets_on_cancelled_error(self):
        """取消路径 contextvar 重置（finally 覆盖所有路径）。"""
        from src.core.internal.agent._tool_callbacks import ToolCallbackChain

        func = MagicMock()

        async def fake_display():
            raise asyncio.CancelledError()

        func.display = fake_display

        agent = MagicMock()
        agent._display_port = MagicMock()
        agent._display_port.is_web = False
        agent._capture_mgr = MagicMock()

        chain = ToolCallbackChain(agent)
        tc = {"id": "call_2", "name": "bash"}
        with pytest.raises(asyncio.CancelledError):
            await chain._run_tool_method(func, tc)
        assert get_current_tool_id() == ""  # 取消路径已重置


class TestToolContextModule:
    """子步骤2 — _tool_context contextvar 模块。"""

    def test_contextvar_default_empty(self):
        assert get_current_tool_id() == ""

    def test_set_get_reset_roundtrip(self):
        token = set_current_tool_id("call_9")
        assert get_current_tool_id() == "call_9"
        reset_current_tool_id(token)
        assert get_current_tool_id() == ""

    @pytest.mark.asyncio
    async def test_run_with_tool_context_isolated(self):
        """run_with_tool_context 在协程内设置并在结束后重置。"""
        from src.core.internal.agent._tool_context import run_with_tool_context

        async def _probe():
            return get_current_tool_id()

        result = await run_with_tool_context("call_10", _probe())
        assert result == "call_10"
        assert get_current_tool_id() == ""  # 结束后重置


class TestEmptyToolIdBoxLifecycle:
    """方向1 B8 — 空 tool_id 工具 box 生命周期（open/close 不泄漏）。

    覆盖：open("") 记录原始空 id 标记；close("") 按 _box_key == "" 匹配匿名
    box 关闭（无泄漏）；多空 id box 取最近者（倒序语义）；无匿名 box 时
    close("") no-op（不误关其他 box）。
    """

    def test_open_empty_tool_id_records_box_key(self):
        """open('') → 实际存储于生成 key，_box_key 记录原始空 id。"""
        from src.tui.app.model import AppModel
        m = AppModel()
        block = m.open_tool_box("", "x")
        assert block.extra["_box_key"] == ""
        assert len(m.tool_boxes) == 1
        stored_key = next(iter(m.tool_boxes))
        assert stored_key.startswith("tool-")  # 实际存储 key 为生成 id
        assert m.tool_boxes[stored_key] is block

    def test_close_empty_tool_id_closes_box_no_leak(self):
        """open('') + close('') → tool_boxes 空、块已关闭、无泄漏。"""
        from src.tui.app.model import AppModel
        m = AppModel()
        m.open_tool_box("", "x")
        m.close_tool_box("", True)
        assert m.tool_boxes == {}
        block = m.blocks[-1]
        assert block.closed is True
        assert block.extra["tool_status"] == "done"

    def test_close_empty_tool_id_latest_anonymous_box(self):
        """多个空 tool_id box → close('') 关闭最近打开者（倒序语义）。"""
        from src.tui.app.model import AppModel
        m = AppModel()
        m.open_tool_box("", "first")
        m.open_tool_box("", "second")
        m.close_tool_box("", True)
        assert len(m.tool_boxes) == 1
        # 最近的 "second" 已关闭，最早的 "first" 保留
        assert m.blocks[0].extra.get("tool_name") == "first"
        assert m.blocks[0].closed is False
        assert m.blocks[1].extra.get("tool_name") == "second"
        assert m.blocks[1].closed is True

    def test_close_empty_tool_id_no_anonymous_box_noop(self):
        """无空 tool_id box 时 close('') → no-op（不误关其他 box）。"""
        from src.tui.app.model import AppModel
        m = AppModel()
        m.open_tool_box("t1", "read_file")
        m.close_tool_box("", True)  # 不抛、不误关
        assert len(m.tool_boxes) == 1
        assert m.tool_boxes["t1"].closed is False

    def test_close_named_id_still_direct_pop(self):
        """非空 tool_id close 仍直接 pop（不走 _box_key 匹配路径）。"""
        from src.tui.app.model import AppModel
        m = AppModel()
        m.open_tool_box("t1", "read_file")
        m.close_tool_box("t1", True)
        assert m.tool_boxes == {}
        assert m.blocks[-1].closed is True
