"""FileToolBase 输出发布路由测试 — subagent 上下文中 write_file/update_file 的 diff 显示在消息区。

需求（2026-08-05）：subagent 调用 write_file/update_file 时，在消息区显示 diff。

覆盖：
  1. ``_is_subagent_context()``：主 agent（tool_id=call_xxx）→ False；
     subagent（tool_id=agent-N）→ True；无上下文 → False
  2. subagent 上下文 ``display()`` → 发布 ``OutputEvent``（→ EventDispatcher._on_output
     → WriteLineCmd → 主消息区 committed 文本行）
  3. 主 agent 上下文 ``display()`` → 发布 ``ToolOutputChunkEvent``（既有行为不变，
     走工具卡片路径）
  4. update_file 同样生效
  5. 端到端：OutputEvent 经 EventDispatcher 转为 WriteLineCmd 进入消息区
     （不创建工具 box，避免 BUG-63 的永不关闭 box 问题）
"""

from __future__ import annotations

import pytest
from typing import List

from src.core.internal.agent._tool_context import (
    get_current_tool_id,
    run_with_tool_context,
    set_current_tool_id,
    reset_current_tool_id,
)


class _Capture:
    """订阅事件总线的收集器（可 unsub 清理）。"""

    def __init__(self) -> None:
        self.events: List[object] = []

    def __call__(self, event) -> None:
        self.events.append(event)


@pytest.fixture(autouse=True)
def _reset_bus():
    """每个测试前后重置 DisplayEventBus 进程级单例，保证订阅计数可预测。"""
    from src.tui.events.event_bus import DisplayEventBus

    DisplayEventBus.reset_default()
    yield
    DisplayEventBus.reset_default()


@pytest.fixture(autouse=True)
def _clean_tool_context():
    """测试前后清理 contextvar，防止泄漏污染后续测试。"""
    yield
    from src.core.internal.agent import _tool_context

    _tool_context._CURRENT_TOOL_ID.set("")
    _tool_context._CURRENT_TOOL_ID.reset(_tool_context._CURRENT_TOOL_ID.set(""))


def _subscribe(types):
    """订阅指定事件类型，返回 (_Capture, unsub 回调)。"""
    from src.tui.events.event_bus import DisplayEventBus

    bus = DisplayEventBus.get_default()
    cap = _Capture()
    for t in types:
        bus.subscribe(cap, event_type=t)
    return cap, lambda: bus.unsubscribe(cap, event_type=types[0])


def _strip(text: str) -> str:
    """剥离 ANSI 转义序列（diff 含语法高亮/行内高亮码，断言前清理）。"""
    from src.tui.ink.helpers import strip_ansi

    return strip_ansi(text)


# ── 上下文判定 ─────────────────────────────────────────

class TestIsSubagentContext:
    def test_no_context_false(self):
        from src.tools.write_file import WriteFileFunc

        func = WriteFileFunc("/tmp/x.py", "x")
        assert get_current_tool_id() == ""
        assert func._is_subagent_context() is False

    def test_main_agent_tool_id_false(self):
        """主 agent 工具执行上下文（tool_call_id，无 agent- 前缀）→ False。"""
        from src.tools.write_file import WriteFileFunc

        func = WriteFileFunc("/tmp/x.py", "x")
        token = set_current_tool_id("call_abc123")
        try:
            assert func._is_subagent_context() is False
        finally:
            reset_current_tool_id(token)

    def test_subagent_tool_id_true(self):
        """subagent 工具执行上下文（agent-N 前缀）→ True。"""
        from src.tools.write_file import WriteFileFunc

        func = WriteFileFunc("/tmp/x.py", "x")
        token = set_current_tool_id("agent-2")
        try:
            assert func._is_subagent_context() is True
        finally:
            reset_current_tool_id(token)


# ── write_file display() 发布路由 ───────────────────────

class TestWriteFileDisplayRouting:
    @pytest.mark.asyncio
    async def test_subagent_display_publishes_output_event(self, tmp_path):
        """subagent 上下文 display() → 发布 OutputEvent，不发布 ToolOutputChunkEvent。"""
        from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent
        from src.tools.write_file import WriteFileFunc

        cap, unsub = _subscribe([OutputEvent, ToolOutputChunkEvent])
        try:
            path = tmp_path / "sub.py"
            func = WriteFileFunc(str(path), "print('hello')\n")

            result = await run_with_tool_context("agent-1", func.display())

            # 执行成功：文件已写入，返回结果字符串
            assert result.startswith("写入成功")
            assert path.read_text() == "print('hello')\n"
            # diff 以 OutputEvent 发布（消息区路径）
            out_events = [e for e in cap.events if isinstance(e, OutputEvent)]
            assert out_events, "subagent 上下文应发布 OutputEvent"
            joined = _strip("".join(e.text for e in out_events))
            assert "print('hello')" in joined  # diff 内容包含新文件内容
            assert "覆盖写入整个文件" in joined
            # 不发布 ToolOutputChunkEvent（避免创建永不关闭的工具 box）
            assert not any(isinstance(e, ToolOutputChunkEvent) for e in cap.events)
        finally:
            unsub()

    @pytest.mark.asyncio
    async def test_main_display_publishes_tool_output_chunk(self, tmp_path):
        """主 agent 上下文 display() → 发布 ToolOutputChunkEvent（既有行为不变）。"""
        from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent
        from src.tools.write_file import WriteFileFunc

        cap, unsub = _subscribe([OutputEvent, ToolOutputChunkEvent])
        try:
            path = tmp_path / "main.py"
            func = WriteFileFunc(str(path), "print('main')\n")

            result = await run_with_tool_context("call_main_1", func.display())

            assert result.startswith("写入成功")
            assert path.read_text() == "print('main')\n"
            # 主 agent 走工具卡片路径：发布 ToolOutputChunkEvent
            chunk_events = [e for e in cap.events if isinstance(e, ToolOutputChunkEvent)]
            assert chunk_events, "主 agent 上下文应发布 ToolOutputChunkEvent"
            joined = _strip("".join(e.text for e in chunk_events))
            assert "print('main')" in joined
            # 主 agent 不应改走 OutputEvent（行为不变）
            assert not any(isinstance(e, OutputEvent) for e in cap.events)
        finally:
            unsub()

    @pytest.mark.asyncio
    async def test_subagent_error_publishes_output_event(self, tmp_path):
        """subagent 上下文 display() 错误路径（内容生成失败）→ 发布 OutputEvent。"""
        from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent
        from src.tools.update_file import UpdateFileFunc

        cap, unsub = _subscribe([OutputEvent, ToolOutputChunkEvent])
        try:
            path = tmp_path / "target.py"
            path.write_text("old\n", encoding="utf-8")
            # old_string 不存在 → StringNotFoundError
            func = UpdateFileFunc(str(path), "不存在的锚点", "new")

            result = await run_with_tool_context("agent-3", func.display())

            assert result.startswith("(更新失败")
            out_events = [e for e in cap.events if isinstance(e, OutputEvent)]
            assert out_events, "subagent 错误路径应发布 OutputEvent"
            assert "更新失败" in "".join(e.text for e in out_events)
            assert not any(isinstance(e, ToolOutputChunkEvent) for e in cap.events)
        finally:
            unsub()


# ── update_file display() 发布路由 ───────────────────────

class TestUpdateFileDisplayRouting:
    @pytest.mark.asyncio
    async def test_subagent_update_publishes_output_event(self, tmp_path):
        """subagent 上下文 update_file → 发布 OutputEvent（含修改 diff）。"""
        from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent
        from src.tools.update_file import UpdateFileFunc

        cap, unsub = _subscribe([OutputEvent, ToolOutputChunkEvent])
        try:
            path = tmp_path / "mod.py"
            path.write_text("a = 1\nb = 2\n", encoding="utf-8")
            func = UpdateFileFunc(str(path), "a = 1", "a = 100")

            result = await run_with_tool_context("agent-2", func.display())

            assert result.startswith("更新成功")
            assert path.read_text() == "a = 100\nb = 2\n"
            out_events = [e for e in cap.events if isinstance(e, OutputEvent)]
            assert out_events
            joined = _strip("".join(e.text for e in out_events))
            assert "a = 1" in joined  # diff 含删除行（旧值）
            assert "a = 100" in joined  # diff 含新增行（新值）
            assert not any(isinstance(e, ToolOutputChunkEvent) for e in cap.events)
        finally:
            unsub()


# ── 端到端：OutputEvent → EventDispatcher → WriteLineCmd ──

class TestDispatcherRouting:
    def test_output_event_routed_to_write_line_cmd(self):
        """OutputEvent（subagent 文件操作发布）经 EventDispatcher → WriteLineCmd 进消息区。"""
        from src.tui._const import WriteLineCmd
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import OutputEvent

        pushed = []
        d = EventDispatcher(push_cmd=pushed.append)

        d._on_output(OutputEvent(text="  ┌─ a/sub.py\n  └─ b/sub.py", level="raw"))

        assert len(pushed) == 1
        assert isinstance(pushed[0], WriteLineCmd)
        assert "└─ b/sub.py" in pushed[0].text

    def test_subagent_tool_output_chunk_still_dropped(self):
        """BUG-63 行为保持：subagent 的 ToolOutputChunkEvent 仍不进主内容 box。

        subagent 文件操作已改走 OutputEvent 路径，剩余的 ToolOutputChunkEvent
        （read_file/bash 等其他 subagent 工具）依然被丢弃，避免开放 box。
        """
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolOutputChunkEvent

        pushed = []
        d = EventDispatcher(push_cmd=pushed.append)
        d._on_tool_output(ToolOutputChunkEvent(
            label="agent-1", tool_id="agent-1", text="sub diff\n", source="agent",
        ))
        assert pushed == []
