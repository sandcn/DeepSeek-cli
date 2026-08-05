"""后台 bash 数量状态栏统计测试（主 Agent + SubAgent 聚合）。

覆盖需求：
- 在右侧最下面（状态栏右下角）显示当前有多少个后台 bash
- SubAgent 的后台 bash 也要统计（EventDispatcher 按 label 聚合）

数据流：
  core：BaseAgent._register_background_task / _complete_background_task /
        _collect_done_background_messages → 发布 BackgroundTaskChangedEvent
        （主 Agent label="main"，SubAgent label="agent-N"）
  TUI：EventDispatcher._on_bg_bash_changed 按 label 聚合 → 推送 BgBashCountCmd
       → apply._do_bg_bash_count 更新 AppModel.status.bg_bash_count
       → StatusBar 右下角渲染「↻ N」
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.core.agent import Agent
from src.core.ports.model import ModelResult
from src.core.subagent import SubAgent
from src.tui._const import RenderCommand, BgBashCountCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.app.status_bar import _build_status_runs, _BG_BASH_ICON
from src.tui.events.event_types import BackgroundTaskChangedEvent


# ═══════════════════════════════════════════════════════════
# 事件 / 命令 / apply 基础
# ═══════════════════════════════════════════════════════════

class TestBgBashEventAndCommand:
    """事件与命令定义。"""

    def test_event_type_exists(self) -> None:
        """BackgroundTaskChangedEvent 有 label 和 count 字段。"""
        ev = BackgroundTaskChangedEvent(label="main", count=2, source="agent")
        assert ev.label == "main"
        assert ev.count == 2

    def test_cmd_cid(self) -> None:
        """BgBashCountCmd 使用 BG_BASH_COUNT 命令码。"""
        cmd = BgBashCountCmd(count=3)
        assert cmd.cid == RenderCommand.BG_BASH_COUNT
        assert cmd.count == 3

    def test_apply_updates_status(self) -> None:
        """apply 处理 BgBashCountCmd → 更新 status.bg_bash_count。"""
        model = AppModel()
        apply_cmd(model, BgBashCountCmd(count=5))
        assert model.status.bg_bash_count == 5

    def test_apply_ignores_negative(self) -> None:
        """apply 对负数钳制为 0。"""
        model = AppModel()
        apply_cmd(model, BgBashCountCmd(count=-3))
        assert model.status.bg_bash_count == 0


# ═══════════════════════════════════════════════════════════
# EventDispatcher 聚合
# ═══════════════════════════════════════════════════════════

class TestDispatcherAggregation:
    """EventDispatcher 按 label 聚合主 Agent + SubAgent 的后台 bash 数量。"""

    def _make_dispatcher(self):
        pushed = []
        dispatcher = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        return dispatcher, pushed

    def test_aggregates_main_and_subagent(self) -> None:
        """主 Agent（main）+ SubAgent（agent-N）的计数聚合。"""
        dispatcher, pushed = self._make_dispatcher()
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="main", count=2))
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="agent-1", count=3))
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="agent-2", count=1))
        assert pushed[-1].count == 6  # 2 + 3 + 1

    def test_zero_removes_label(self) -> None:
        """某 agent 后台任务清零后从聚合中移除（总数回落）。"""
        dispatcher, pushed = self._make_dispatcher()
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="main", count=2))
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="agent-1", count=3))
        assert pushed[-1].count == 5
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="agent-1", count=0))
        assert pushed[-1].count == 2

    def test_all_cleared(self) -> None:
        """全部后台任务清零 → 总数 0。"""
        dispatcher, pushed = self._make_dispatcher()
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="main", count=1))
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="main", count=0))
        assert pushed[-1].count == 0

    def test_list_handlers_includes_event(self) -> None:
        """list_handlers 包含 BackgroundTaskChangedEvent 处理器。"""
        dispatcher, _ = self._make_dispatcher()
        handlers = dispatcher.list_handlers()
        assert BackgroundTaskChangedEvent in handlers
        # 绑定方法每次访问都是新对象，比较底层函数对象
        assert handlers[BackgroundTaskChangedEvent].__func__ is EventDispatcher._on_bg_bash_changed

    def test_end_to_end_apply(self) -> None:
        """事件 → 聚合 → 推送命令 → apply → status 更新。"""
        dispatcher, pushed = self._make_dispatcher()
        model = AppModel()
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="main", count=1))
        dispatcher._on_bg_bash_changed(BackgroundTaskChangedEvent(label="agent-1", count=2))
        for cmd in pushed:
            apply_cmd(model, cmd)
        assert model.status.bg_bash_count == 3


# ═══════════════════════════════════════════════════════════
# StatusBar 渲染
# ═══════════════════════════════════════════════════════════

class TestStatusBarRendersBgBash:
    """状态栏右下角显示后台 bash 数量。"""

    def _runs_text(self, model) -> str:
        return "".join(r.text for r in _build_status_runs(model, 0.0, "\u00b7"))

    def test_idle_shows_count(self) -> None:
        """空闲（非活跃）时也显示后台 bash 数量。"""
        model = AppModel()
        model.status.model_name = "deepseek-chat"
        model.status.bg_bash_count = 3
        text = self._runs_text(model)
        assert _BG_BASH_ICON in text
        assert "3" in text

    def test_active_shows_count_at_right(self) -> None:
        """活跃时后台数量显示在最右（与统计同侧）。"""
        model = AppModel()
        model.status.model_name = "m"
        model.status.status_active = True
        model.status.tool_total = 5
        model.status.tool_count = 2
        model.status.bg_bash_count = 4
        text = self._runs_text(model)
        assert _BG_BASH_ICON in text
        assert "4" in text
        # 后台数量在工具计数之后（右侧）
        assert text.rindex(_BG_BASH_ICON) > text.rindex("2") if "2" in text else True

    def test_no_count_hides_icon(self) -> None:
        """无后台任务时不显示图标。"""
        model = AppModel()
        model.status.model_name = "m"
        text = self._runs_text(model)
        assert _BG_BASH_ICON not in text

    def test_zero_hides_icon(self) -> None:
        """bg_bash_count 为 0 时不显示图标。"""
        model = AppModel()
        model.status.model_name = "m"
        model.status.bg_bash_count = 0
        text = self._runs_text(model)
        assert _BG_BASH_ICON not in text


# ═══════════════════════════════════════════════════════════
# BaseAgent 发布事件（core 侧）
# ═══════════════════════════════════════════════════════════

class _FakeTask:
    """假 asyncio.Task（仅用于注册记录，不真实执行）。"""

    def __init__(self, done: bool = False):
        self._done = done

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self._done = True


class TestBaseAgentPublishesEvent:
    """后台任务注册/完成/移除时发布 BackgroundTaskChangedEvent。"""

    def _capture_events(self, agent):
        """把 agent 的 event_port 替换为记录器，返回事件列表。"""
        events = []

        class _Recorder:
            def publish_event(self, event, source="core"):
                events.append(event)

        agent._event_port = _Recorder()
        return events

    def test_register_publishes(self) -> None:
        """注册后台任务发布 count=1。"""
        agent = Agent(model="fake-model")
        events = self._capture_events(agent)
        agent._register_background_task("bg-1", {"task": _FakeTask(), "done": False})
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, BackgroundTaskChangedEvent)
        assert ev.label == "main"  # 主 Agent 无 label → "main"
        assert ev.count == 1

    def test_complete_publishes_zero(self) -> None:
        """后台任务完成发布 count=0。"""
        agent = Agent(model="fake-model")
        events = self._capture_events(agent)
        agent._register_background_task("bg-1", {"task": _FakeTask(), "done": False})
        agent._complete_background_task("bg-1", "out")
        assert events[-1].count == 0

    def test_collect_removes_publishes(self) -> None:
        """收集已完成任务（移除 tasklist）后发布最新计数。"""
        agent = Agent(model="fake-model")
        events = self._capture_events(agent)
        agent._register_background_task("bg-1", {"task": _FakeTask(done=True), "done": True, "result": "x"})
        msgs = agent._collect_done_background_messages()
        assert len(msgs) == 1
        assert events[-1].count == 0
        assert "bg-1" not in agent._background_tasks

    def test_subagent_label_used(self) -> None:
        """SubAgent 发布事件时使用自身 label（agent-N）。"""
        parent = Agent(model="fake-model")
        sub = SubAgent(
            label="agent-1", description="t", prompt="p",
            parent_agent=parent, model="fake-model",
        )
        events = []

        class _Recorder:
            def publish_event(self, event, source="core"):
                events.append(event)

        sub._event_port = _Recorder()
        sub._register_background_task("bg-s1", {"task": _FakeTask(), "done": False})
        assert events[0].label == "agent-1"
        assert events[0].count == 1


# ═══════════════════════════════════════════════════════════
# 集成：SubAgent 后台 bash 真实注册 → 事件发布
# ═══════════════════════════════════════════════════════════

class _FakeParentPort:
    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False):
        return ModelResult(content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])

    async def call_sync(self, messages, model=None, tools=None, display=None,
                        label=None):
        return ModelResult(content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])


class TestSubAgentBgBashIntegration:
    """SubAgent 内真实后台 bash 触发事件，且能聚合到状态栏。"""

    @pytest.mark.asyncio
    async def test_subagent_background_bash_publishes_event(self) -> None:
        """SubAgent 执行后台 bash → 发布 count=1 事件（label=agent-N）。"""
        parent = Agent(model="fake-model", async_model_port=_FakeParentPort())
        events = []

        class _Recorder:
            def publish_event(self, event, source="core"):
                if isinstance(event, BackgroundTaskChangedEvent):
                    events.append(event)

        # SubAgent 继承父 event_port；用记录器替换以捕获事件
        sub = SubAgent(
            label="agent-1", description="t", prompt="p",
            parent_agent=parent, model="fake-model",
        )
        sub._event_port = _Recorder()

        # 在 SubAgent 上下文中执行后台 bash（模拟工具调用路径）
        func = parent.get_tool_registry().dispatch(
            "bash",
            {"command": "sleep 0.3 && echo sub-bg", "background": True},
            agent=sub,
        )
        ret = await func.execute()
        data = json.loads(ret)
        assert data["status"] == "running"

        # 注册事件已发布（count=1，label=agent-1）
        assert any(ev.count == 1 and ev.label == "agent-1" for ev in events)
        assert data["task_id"] in sub._background_tasks

        # 等待后台完成 → 完成事件（count=0）
        rec = sub._background_tasks[data["task_id"]]
        await asyncio.wait_for(rec["task"], timeout=15)
        assert any(ev.count == 0 and ev.label == "agent-1" for ev in events)
